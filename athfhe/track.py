"""
athfhe/track.py：AThFHE 两方案轨道（USENIX Sec'25 任意门限 FHE 扩展）
=====================================================================

- HierAthfheTrack：方案一 分簇层次架构（athfhe/hier.py，内层升环 Q'）；
- TwinAthfheTrack：方案二 非共谋双服务器架构（athfhe/twin.py，PRG 种子
  掩码、不升环、单轮发完即走）。

两条轨道均为自带加密的完整协议轨道（外层玩具 CKKS × 内层 ATASSES 联合
解密密钥消元），与 ppbr / rfl / threshold 互斥，且要求 encryption.enabled
=true（不走 TenSEAL：wants_crypto=False，协议对象是 numpy 大整数仿真）。

单轮流程（参照 ppbr/track.py 的 run_round_ppbr）：选客户端 → 明文下发
global_params 本地训练 → 攻击注入 → update dict 展平/切块 → 跑协议
（注入配置指定的掉线数）→ 聚合和 ÷ 在线数 = 平均更新 → reshape 回 dict
→ 加到 exp.server.model → 明文评估 → 记录标准指标 + athfhe_* 协议成本。
"""

import time

import numpy as np
import torch
import torch.nn as nn
import psutil

from base.experiment import Track, register_track
from base.data import evaluate, get_model_params
from base.log_utils import (
    tree,
    LogTag, tag_info, tag_warn,
    log_round_header,
)

from athfhe.hier import HierATHFHE
from athfhe.twin import TwinATHFHE


def _check_conflicts(config: dict, self_key: str, other_key: str) -> None:
    """athfhe 轨道冲突预检：与另一条 athfhe 轨道、ppbr/rfl/threshold 互斥，
    且要求 encryption.enabled=true（自带加密的完整协议轨道）。"""
    if (config.get(other_key) or {}).get('enabled', False):
        raise ValueError(
            f"{self_key}.enabled=true 与 {other_key}.enabled=true 冲突"
            f"（两条 AThFHE 方案轨道，请只开启其一）")
    for track_key, track_name in (('ppbr', 'ppbr'), ('rfl', 'rfl')):
        if (config.get(track_key) or {}).get('enabled', False):
            raise ValueError(
                f"{self_key}.enabled=true 与 {track_key}.enabled=true 冲突"
                f"（均为完整协议轨道，请只开启其一）")
    if (config.get('threshold') or {}).get('enabled', False):
        raise ValueError(
            f"{self_key}.enabled=true 与 threshold.enabled=true 冲突"
            f"（threshold 仅 CKKS 轨道，AThFHE 不经单密钥 CKKS 门限）")
    if not config.get('encryption', {}).get('enabled', True):
        raise ValueError(
            f"{self_key}.enabled=true 要求 encryption.enabled=true"
            f"（AThFHE 是自带外层 CKKS 加密的完整协议轨道，明文模式无法执行）")


def _flatten_update(update: dict):
    """update dict → (float 向量, shapes)。shapes 记录各张量形状供还原。"""
    shapes = [(k, tuple(v.shape)) for k, v in update.items()]
    vec = torch.cat([v.detach().reshape(-1).float() for v in update.values()]) \
        if update else torch.zeros(0)
    # .cpu() 不可省：device=cuda 时 vec 在 GPU 上，.numpy() 直接报
    # "can't convert cuda:0 device type tensor to numpy"
    return vec.cpu().numpy().astype(np.float64), shapes


def _reshape_average(avg_vec: np.ndarray, shapes, ref_params: dict) -> dict:
    """平均更新向量按 shapes 还原回 dict（键序与展平时一致）"""
    out = {}
    pos = 0
    for k, shp in shapes:
        n = int(np.prod(shp)) if shp else 1
        out[k] = torch.from_numpy(avg_vec[pos:pos + n].reshape(shp))
        pos += n
    return out


def _chunk_blocks(vec: np.ndarray, slots: int) -> list:
    """向量按 M/2 切块（末块 0 填充），每块一个协议实例并行跑"""
    if len(vec) == 0:
        return [np.zeros(slots)]
    n_blocks = (len(vec) + slots - 1) // slots
    blocks = []
    for b in range(n_blocks):
        blk = np.zeros(slots)
        seg = vec[b * slots:(b + 1) * slots]
        blk[:len(seg)] = seg
        blocks.append(blk)
    return blocks


def _force_full_sampling(exp) -> None:
    """AThFHE 协议按全体客户端初始化密钥结构（Shamir 评估点 1..N），
    与 RFL 轨道同惯例要求全采样"""
    if exp.config['system'].get('client_sampling_rate', 1.0) != 1.0:
        exp.config['system']['client_sampling_rate'] = 1.0
        tag_warn(LogTag.INIT,
                 "AThFHE 协议要求全体客户端在线（Shamir 评估点按 N 初始化）"
                 "→ 强制 client_sampling_rate=1.0")


def _run_athfhe_round(exp, round_num: int, proto, kind: str) -> dict:
    """athfhe 两轨道共用的单轮执行（kind='hier'/'twin'）"""
    log_round_header(round_num + 1, exp.config['system']['num_rounds'],
                     mode=f"AThFHE-{kind}")
    round_start = time.time()

    selected_clients = exp._select_clients()

    # 明文下发全局模型（同 PPBR 轨道：global_params 明文本地训练）
    global_params = get_model_params(exp.server.model)
    client_updates, train_losses, train_accs = [], [], []
    for client in selected_clients:
        update, loss, acc = client.local_train(
            global_params=global_params, round_num=round_num)
        client_updates.append(update)
        train_losses.append(loss)
        train_accs.append(acc)
        exp.metrics.record('local_train_time', client.local_train_time)

    # 攻击注入（与 CKKS/PPBR 轨道同一威胁模型：客户端本地、加密上传之前）
    client_weights = [1.0] * len(selected_clients)
    selected_clients, client_updates, _, train_losses, train_accs = \
        exp._apply_attacks_to_updates(
            round_num, selected_clients, client_updates,
            client_weights, train_losses, train_accs)

    # ==== AThFHE 协议：展平 → 切块 → 四阶段 → 平均 → reshape 回 dict ====
    slots = proto.M // 2
    client_chunks = []
    shapes_ref = None
    for update in client_updates:
        vec, shapes = _flatten_update(update)
        if shapes_ref is None:
            shapes_ref = shapes
        client_chunks.append(_chunk_blocks(vec, slots))

    rng = np.random.RandomState(
        exp.config['system'].get('random_seed', 42) + round_num)
    dropout = int(exp.athfhe_cfg.get('dropout', 0))
    selected_ids = [c.client_id for c in selected_clients]
    if dropout > 0:
        if dropout >= len(selected_ids):
            raise ValueError(
                f"athfhe dropout={dropout} 必须 < 本轮选中客户端数 {len(selected_ids)}")
        if kind == 'twin':
            offline = rng.choice(selected_ids, size=dropout, replace=False).tolist()
            online = sorted(set(selected_ids) - set(offline))
            agg_chunks, cost = proto.run_protocol(client_chunks, online=online)
        else:
            # 方案一：T1/T2 两个在线集独立抽样掉线（允许异步、每簇 ≥t_c）
            offline1 = rng.choice(selected_ids, size=dropout, replace=False).tolist()
            offline2 = rng.choice(selected_ids, size=dropout, replace=False).tolist()
            online1 = sorted(set(selected_ids) - set(offline1))
            online2 = sorted(set(selected_ids) - set(offline2))
            agg_chunks, cost = proto.run_protocol(
                client_chunks, online1=online1, online2=online2)
    else:
        if kind == 'twin':
            agg_chunks, cost = proto.run_protocol(client_chunks)
        else:
            agg_chunks, cost = proto.run_protocol(client_chunks)

    # 聚合和 ÷ 在线客户端数 = 平均更新
    num_online = cost.get('athfhe_num_online1',
                          cost.get('athfhe_num_online', len(selected_ids)))
    avg_vec = np.concatenate(agg_chunks) / float(num_online)

    avg_update = _reshape_average(avg_vec, shapes_ref,
                                  get_model_params(exp.server.model))
    with torch.no_grad():
        for name, p in exp.server.model.named_parameters():
            if name in avg_update:
                p.add_(avg_update[name].to(p.device))

    # 评估：明文全局模型（论文设定：全局模型公开，同 PPBR 轨道语义）
    criterion = nn.CrossEntropyLoss()
    test_loss, test_acc = evaluate(
        exp.server.model, exp.test_loader, criterion, exp.device)
    exp.server.record_evaluation(test_loss, test_acc)

    # 标准指标 + athfhe 协议成本指标
    exp.metrics.record('train_loss',
                       float(np.mean(train_losses)) if train_losses else 0.0)
    exp.metrics.record('train_acc',
                       float(np.mean(train_accs)) if train_accs else 0.0)
    exp.metrics.record('test_loss', test_loss)
    exp.metrics.record('test_acc', test_acc)
    exp.metrics.record('communication_rounds', 1)
    exp.metrics.record('cpu_utilization', psutil.cpu_percent())
    exp.metrics.record('memory_usage', psutil.virtual_memory().percent)

    plaintext_size = sum(p.numel() * 4 for p in exp.server.model.parameters())
    upload_bytes = cost.get('athfhe_upload_bytes', 0)
    inner_bytes = cost.get('athfhe_inner_bytes', 0)
    seed_bytes = cost.get('athfhe_seed_bytes', 0)
    ciphertext_size = upload_bytes + inner_bytes + seed_bytes
    exp.metrics.record('plaintext_size', plaintext_size)
    exp.metrics.record('ciphertext_size', ciphertext_size)
    exp.metrics.record('num_ciphertexts',
                       2 * cost.get('athfhe_num_blocks', 0) * num_online)
    exp.metrics.record('aggregated_ciphertexts',
                       2 * cost.get('athfhe_num_blocks', 0))
    exp.metrics.record('expansion_ratio',
                       ciphertext_size / plaintext_size if plaintext_size else 0.0)
    exp.metrics.record('encryption_time', 0.0)
    exp.metrics.record('aggregation_time', cost.get('athfhe_crypto_time', 0.0))
    download_cost = plaintext_size * len(selected_clients)
    exp.metrics.record('upload_cost', ciphertext_size)
    exp.metrics.record('download_cost', download_cost)
    exp.metrics.record('total_communication_cost', ciphertext_size + download_cost)
    exp.metrics.record('key_distribution_cost',
                       cost.get('athfhe_setup_bytes', 0) if round_num == 0 else 0)
    for k, v in cost.items():
        exp.metrics.record(k, v)
    for c in selected_clients:
        exp.client_participation[c.client_id] += 1
        exp.client_total_ciphertexts[c.client_id] += \
            2 * cost.get('athfhe_num_blocks', 0)

    round_time = time.time() - round_start
    exp.metrics.record('total_round_time', round_time)
    exp.metrics.record('total_round_time_simulated', 0.0)

    tag_info(LogTag.ROUND,
             f"#{round_num + 1} done  |  acc={test_acc:.2f}%  |  "
             f"real_time={round_time:.2f}s  |  mode=AThFHE-{kind}  |  "
             f"online={num_online}/{len(selected_clients)}")

    return {
        'round': round_num + 1,
        'test_acc': test_acc,
        'test_loss': test_loss,
        'round_time': round_time,
        'simulated_round_duration': 0.0,
    }


class HierAthfheTrack(Track):
    """方案一轨道：分簇层次 CKKS-ATASSES（内层升环 Q' > 2·n_c·Q²·B'）"""

    name = 'athfhe_hier'
    conflicts = ('athfhe_twin', 'ppbr', 'rfl', 'ckks')
    supports_client_attacks = True
    needs_checkpoint = False

    def enabled(self, config: dict) -> bool:
        return bool((config.get('athfhe_hier') or {}).get('enabled', False))

    def wants_crypto(self, config: dict) -> bool:
        return False    # 自带外层 CKKS 仿真（numpy 大整数），不经 TenSEAL

    def init_protocol(self, exp) -> None:
        _check_conflicts(exp.config, 'athfhe_hier', 'athfhe_twin')
        _force_full_sampling(exp)
        cfg = exp.config['athfhe_hier']
        exp.athfhe_cfg = cfg
        C = int(cfg.get('num_clusters', 2))
        t_c = int(cfg.get('cluster_threshold', 2))
        num_clients = exp.config['system']['num_clients']
        # 均分客户端到 C 簇（余数摊到前几个簇），每簇必须 ≥ t_c
        base, rem = divmod(num_clients, C)
        cluster_sizes = [base + (1 if c < rem else 0) for c in range(C)]
        if any(n < t_c for n in cluster_sizes):
            raise ValueError(
                f"athfhe_hier：每簇客户端数必须 ≥ t_c={t_c}（当前分簇 {cluster_sizes}），"
                f"请调大 num_clients 或调小 num_clusters/cluster_threshold")
        exp.athfhe = HierATHFHE(
            num_clusters=C, cluster_threshold=t_c,
            ring_degree=int(cfg.get('ring_degree', 256)),
            outer_modulus_bits=int(cfg.get('outer_modulus_bits', 60)),
            scale_bits=int(cfg.get('scale_bits', 40)),
            inner_noise_bits=int(cfg.get('inner_noise_bits', 20)),
            smudge_bits=int(cfg.get('smudge_bits', 10)),
            cluster_sizes=cluster_sizes,
            seed=exp.config['system'].get('random_seed', 42))
        p = exp.athfhe
        tree([
            ("🛡️", f"AThFHE-Hier: M={p.M}  |  Q={p.ctx_outer.q.bit_length()}bit"
                   f"  |  Δ=2^{p.delta.bit_length() - 1}  |  C={p.C} t_c={p.t_c} "
                   f"n_c={p.cluster_sizes}"),
            ("🔐", f"内层升环: Q'={p.ctx_inner.q.bit_length()}bit "
                   f"(Q'>2·n_c·Q²·B')  |  Δ'=2^{p.delta_prime.bit_length() - 1}  |  "
                   f"B_sm=2^{p.B_sm.bit_length() - 1} B'=2^{p.B_prime.bit_length() - 1}"),
        ], indent=1)
        for c in exp.clients:
            c.track = "athfhe_hier"

    def record_key_distribution(self, exp) -> None:
        # Setup 阶段密钥分发：簇间加法份额 + 簇内 Shamir 份额（每客户端 1 个
        # R_{M,Q} 多项式）。首轮 run_round 记 key_distribution_cost，此处留痕
        tag_info(LogTag.INIT,
                 "AThFHE-Hier: S 簇间加法拆分 + 簇内 Shamir 份额随 Setup 分发"
                 "（每客户端 1 个外层环多项式）")

    def output_prefix(self, exp) -> str:
        return 'athfhe_hier'

    def mode_label(self, exp) -> str:
        return "AThFHE-Hier（分簇层次 CKKS-ATASSES）"

    def upload_label(self, exp) -> str:
        return "AThFHE 协议消息（外层 CKKS 密文 + 内层升环份额密文）"

    def run_round(self, exp, round_num: int) -> dict:
        return _run_athfhe_round(exp, round_num, exp.athfhe, 'hier')


class TwinAthfheTrack(Track):
    """方案二轨道：非共谋双服务器 CKKS-ATASSES（PRG 种子掩码、不升环）"""

    name = 'athfhe_twin'
    conflicts = ('athfhe_hier', 'ppbr', 'rfl', 'ckks')
    supports_client_attacks = True
    needs_checkpoint = False

    def enabled(self, config: dict) -> bool:
        return bool((config.get('athfhe_twin') or {}).get('enabled', False))

    def wants_crypto(self, config: dict) -> bool:
        return False    # 自带外层 CKKS 仿真（numpy 大整数），不经 TenSEAL

    def init_protocol(self, exp) -> None:
        _check_conflicts(exp.config, 'athfhe_twin', 'athfhe_hier')
        _force_full_sampling(exp)
        cfg = exp.config['athfhe_twin']
        exp.athfhe_cfg = cfg
        T = int(cfg.get('threshold', 3))
        num_clients = exp.config['system']['num_clients']
        if T > num_clients:
            raise ValueError(
                f"athfhe_twin：门限 T={T} 不能超过客户端总数 {num_clients}")
        exp.athfhe = TwinATHFHE(
            threshold=T,
            ring_degree=int(cfg.get('ring_degree', 256)),
            outer_modulus_bits=int(cfg.get('outer_modulus_bits', 60)),
            scale_bits=int(cfg.get('scale_bits', 40)),
            smudge_bits=int(cfg.get('smudge_bits', 10)),
            num_clients=num_clients,
            seed=exp.config['system'].get('random_seed', 42))
        p = exp.athfhe
        tree([
            ("🛡️", f"AThFHE-Twin: M={p.M}  |  Q={p.ctx.q.bit_length()}bit"
                   f"  |  Δ=2^{p.delta.bit_length() - 1}  |  T={p.T} N={p.N}"),
            ("🔐", f"PRG 种子掩码: 不升环（内层复用 R_{{M,Q}}）  |  "
                   f"seed={2 * 16}B/客户端  |  B_sm=2^{p.B_sm.bit_length() - 1}"),
        ], indent=1)
        for c in exp.clients:
            c.track = "athfhe_twin"

    def record_key_distribution(self, exp) -> None:
        tag_info(LogTag.INIT,
                 "AThFHE-Twin: (T,N)-Shamir 份额随 Setup 分发"
                 "（每客户端 1 个外层环多项式）")

    def output_prefix(self, exp) -> str:
        return 'athfhe_twin'

    def mode_label(self, exp) -> str:
        return "AThFHE-Twin（双服务器非共谋 CKKS-ATASSES）"

    def upload_label(self, exp) -> str:
        return "AThFHE 协议消息（外层 CKKS 密文 + 同环掩码密文 + 32B 种子）"

    def run_round(self, exp, round_num: int) -> dict:
        return _run_athfhe_round(exp, round_num, exp.athfhe, 'twin')


register_track(HierAthfheTrack())
register_track(TwinAthfheTrack())
