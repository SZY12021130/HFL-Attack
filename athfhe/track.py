"""
athfhe/track.py：AThFHE 两方案轨道（USENIX Sec'25 任意门限 FHE 扩展）
=====================================================================

- HierAthfheTrack：方案一 分簇层次架构（athfhe/hier.py，内层升环 Q'）；
- AtassesTrack：论文 ATASSES 基线（athfhe/hier.py 令 num_clusters=1 的
  忠实复现：单级 (T,N)-Shamir + BFV 内层两回合，数学上与方案一退化同构）；
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

import math
import time
from typing import Optional

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

# 逐流程通信键（每轮，cost dict / metrics 同名）
TWIN_FLOW_KEYS = (
    'athfhe_comm_p1_upload', 'athfhe_comm_p1_broadcast_c1',
    'athfhe_comm_p2_mask', 'athfhe_comm_p2_seed',
    'athfhe_comm_p3_list', 'athfhe_comm_p3_dk',
)
HIER_FLOW_KEYS = (
    'athfhe_comm_p1_upload', 'athfhe_comm_p1_broadcast_c1',
    'athfhe_comm_p2_ct', 'athfhe_comm_p2_ek',
    'athfhe_comm_p3_dkshare', 'athfhe_comm_p3_bc',
)

# report.md 逐流程表行定义：(阶段, 发送方→接收方, 传输内容, 指标键)
TWIN_FLOW_ROWS = (
    ('阶段1 Outer-Upload', 'P_i → S_A', '外层 CKKS 密文 CT_i（2 多项式/块/客户端）',
     'athfhe_comm_p1_upload'),
    ('阶段1 Broadcast', 'S_A → 全体 P_i', '广播 c_1（按发送一次计）',
     'athfhe_comm_p1_broadcast_c1'),
    ('阶段2 OneShot', 'P_i → S_A', '掩码密文 (c\'_b, c\'_n)（2 多项式/块/客户端）',
     'athfhe_comm_p2_mask'),
    ('阶段2 OneShot', 'P_i → S_H', 'PRG 种子 (seed1, seed2)，TLS 记录 32B',
     'athfhe_comm_p2_seed'),
    ('阶段3 Coop', 'S_A → S_H', '在线名单 T（|T| 个 ID × 2B）',
     'athfhe_comm_p3_list'),
    ('阶段3 Coop', 'S_H → S_A', '联合解密密钥 dk（1 多项式/块）',
     'athfhe_comm_p3_dk'),
    ('阶段0 Setup（一次性）', 'S → 各 P_i', '(T,N)-Shamir 份额（每客户端 1 多项式）',
     'athfhe_setup_bytes'),
)
HIER_FLOW_ROWS = (
    ('阶段1 Outer-Agg', 'P_{c,i} → CH_c/CS', '外层 CKKS 密文（2 多项式/块/客户端）',
     'athfhe_comm_p1_upload'),
    ('阶段1 Broadcast', 'CS → 全体 P_i', '广播 c_1（按发送一次计）',
     'athfhe_comm_p1_broadcast_c1'),
    ('阶段2 Cluster-R1', 'P_{c,i} → CH_c', '内层双密文首项 (CTb, CTn)',
     'athfhe_comm_p2_ct'),
    ('阶段2 Cluster-R1', 'P_{c,i} → CH_c', 'ek1/ek2 簇内 Shamir 份额',
     'athfhe_comm_p2_ek'),
    ('阶段3 Cluster-R2', '节点 j → CH_c', 'dkShare_{c,j}（1 多项式/在线节点/块）',
     'athfhe_comm_p3_dkshare'),
    ('阶段3 Cluster-R2', 'CH_c → CS', '簇级份额 b\'_c（1 多项式/簇/块）',
     'athfhe_comm_p3_bc'),
    ('阶段0 Setup（一次性）', 'S → 各 P_{c,i}', '簇间加法份额 + 簇内 Shamir 份额',
     'athfhe_setup_bytes'),
)


def _real_ckks_factor(exp, m) -> float:
    """等效真实 CKKS 折算系数 = ceil(real_coeff_bits/8) / ceil(Q_bits/8)。

    通信/浮点运算量与 M 无关，折算只动每系数字节数（Q 位宽 60→218 bit，
    系数 ≈ 28/8 = 3.5）；real_coeff_bits=0 时返回 0（关闭折算）。"""
    rcb = int(exp.athfhe_cfg.get('real_coeff_bits', 0))
    if rcb <= 0:
        return 0.0
    q_bits_list = m.get('athfhe_Q_bits') or []
    q_bits = q_bits_list[-1] if q_bits_list else 61
    return math.ceil(rcb / 8) / math.ceil(q_bits / 8)


def _athfhe_flow_report(exp, lines, m, kind: str) -> None:
    """report.md 逐流程通信表（论文口径）：仿真量 / 等效真实 CKKS / @98Mbps"""
    rows = TWIN_FLOW_ROWS if kind == 'twin' else HIER_FLOW_ROWS
    factor = _real_ckks_factor(exp, m)
    bw_bps = 98e6 / 8          # ATASSES 论文同口径：98 Mbps

    def _fmt(n):
        return f"{n:,.0f} B ({n / (1024 * 1024):.2f} MB)"

    lines.append(f"### 3.1 AThFHE-{'Twin' if kind == 'twin' else 'Hier'} "
                 f"Per-Flow Communication / 逐流程通信统计（论文口径）")
    lines.append(f"")
    lines.append(f"| 阶段 Phase | 发送方→接收方 | 传输内容 | "
                 f"通信量(仿真) Simulated | 通信量(等效真实CKKS) Real-CKKS | "
                 f"通信时间@98Mbps |")
    lines.append(f"|---|---|---|---|---|---|")
    total_sim = 0
    for phase, path, content, key in rows:
        sim = sum(m.get(key, []))
        total_sim += sim
        real = f"{_fmt(sim * factor)}" if factor > 0 else "-（折算关闭）"
        t = sim / bw_bps
        lines.append(f"| {phase} | {path} | {content} | {_fmt(sim)} | {real} "
                     f"| {t:.2f} s |")
    real_total = (f"{_fmt(total_sim * factor)}" if factor > 0
                  else "-（折算关闭）")
    lines.append(f"| **合计 Total** |  |  | **{_fmt(total_sim)}** | "
                 f"**{real_total}** | **{total_sim / bw_bps:.2f} s** |")
    lines.append(f"")
    if factor > 0:
        lines.append(f"> 折算系数 = ceil({int(exp.athfhe_cfg.get('real_coeff_bits'))}"
                     f"/8) / ceil(Q_bits/8) ≈ **{factor:.2f}**"
                     f"（仿真 Q≈2^{int(exp.athfhe_cfg.get('real_coeff_bits'))} 系数位宽"
                     f" → 真实 CKKS 218-bit 系数；通信量与浮点运算数与 M 无关，"
                     f"折算只动每系数字节数）。")
    lines.append(f"> 口径：广播（c_1）按发送一次计；Setup 为一次性成本（仅首轮）；"
                 f"仿真基于 numpy 大整数多项式记账（poly_bytes = n × "
                 f"ceil(bitlen(q)/8)），≠ 真实 CKKS 实现通信。")
    lines.append(f"")


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
    local_train_times = []
    for client in selected_clients:
        update, loss, acc = client.local_train(
            global_params=global_params, round_num=round_num)
        client_updates.append(update)
        train_losses.append(loss)
        train_accs.append(acc)
        local_train_times.append(getattr(client, 'local_train_time', 0.0))
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

    # ==== 论文口径指标：等效真实 CKKS 折算 + straggler 轮时 ==============
    flow_keys = TWIN_FLOW_KEYS if kind == 'twin' else HIER_FLOW_KEYS
    sim_comm = sum(cost.get(k, 0) for k in flow_keys)
    rcb = int(exp.athfhe_cfg.get('real_coeff_bits', 0))
    if rcb > 0:
        # 折算系数 = ceil(rcb/8) / ceil(仿真Q_bits/8)；通信/浮点与 M 无关，
        # 折算只动每系数字节数
        factor = math.ceil(rcb / 8) / math.ceil(
            cost.get('athfhe_Q_bits', 61) / 8)
        exp.metrics.record('athfhe_comm_real_bytes', sim_comm * factor)
        exp.metrics.record('athfhe_real_coeff_bits', rcb)

    # straggler 轮时：crypto_time + max_选中客户端(local_train + dl + ul)。
    # dl = 广播 c_1（全员同一份，按发送一次计）；ul 按在线客户端均摊上传
    dl_bits = cost.get('athfhe_comm_p1_broadcast_c1', 0) * 8
    ul_bits = (cost.get('athfhe_comm_p1_upload', 0)
               + cost.get('athfhe_comm_p2_mask',
                          cost.get('athfhe_comm_p2_ct', 0))
               + cost.get('athfhe_comm_p2_seed', 0)) * 8 / max(num_online, 1)
    crypto_time = cost.get('athfhe_crypto_time', 0.0)
    simulated_download_time = 0.0
    simulated_upload_time = 0.0
    simulated_round_duration = 0.0
    for idx, client in enumerate(selected_clients):
        if exp.bandwidth_simulator is not None:
            client_dl = exp.bandwidth_simulator.estimate_download_time(
                client.client_id, dl_bits)
            client_ul = exp.bandwidth_simulator.estimate_upload_time(
                client.client_id, ul_bits)
        else:
            # fallback 固定带宽：下行 100 Mbps / 上行 50 Mbps
            client_dl = dl_bits / 100e6
            client_ul = ul_bits / 50e6
        client_total = local_train_times[idx] + client_dl + client_ul
        simulated_round_duration = max(simulated_round_duration, client_total)
        simulated_download_time = max(simulated_download_time, client_dl)
        simulated_upload_time = max(simulated_upload_time, client_ul)
    total_round_time_simulated = crypto_time + simulated_round_duration
    exp.metrics.record('total_round_time_simulated',
                       total_round_time_simulated)
    exp.metrics.record('upload_time', simulated_upload_time)
    exp.metrics.record('download_time', simulated_download_time)

    for c in selected_clients:
        exp.client_participation[c.client_id] += 1
        exp.client_total_ciphertexts[c.client_id] += \
            2 * cost.get('athfhe_num_blocks', 0)

    round_time = time.time() - round_start
    exp.metrics.record('total_round_time', round_time)

    tag_info(LogTag.ROUND,
             f"#{round_num + 1} done  |  acc={test_acc:.2f}%  |  "
             f"real_time={round_time:.2f}s  |  sim_dur={total_round_time_simulated:.2f}s  |  "
             f"mode=AThFHE-{kind}  |  online={num_online}/{len(selected_clients)}")

    return {
        'round': round_num + 1,
        'test_acc': test_acc,
        'test_loss': test_loss,
        'round_time': round_time,
        'simulated_round_duration': total_round_time_simulated,
    }


def _setup_hier(exp, cfg_key: str, c_override: Optional[int] = None) -> dict:
    """hier / atasses 共用的 HierATHFHE 初始化。

    Args:
        exp: 实验实例
        cfg_key: 配置段名（'athfhe_hier' / 'athfhe_atasses'）
        c_override: 强制簇数（ATASSES 基线 = 1，即论文单级 (T,N)-Shamir
                    + BFV 内层两回合协议的退化形态）

    Returns:
        该轨道的配置段（同时存入 exp.athfhe_cfg / exp.athfhe）
    """
    cfg = exp.config[cfg_key]
    exp.athfhe_cfg = cfg
    t_c = int(cfg.get('cluster_threshold', 2))
    num_clients = exp.config['system']['num_clients']
    C = int(c_override if c_override is not None
            else cfg.get('num_clusters', 2))
    # 均分客户端到 C 簇（余数摊到前几个簇），每簇必须 ≥ t_c
    base, rem = divmod(num_clients, C)
    cluster_sizes = [base + (1 if c < rem else 0) for c in range(C)]
    if any(n < t_c for n in cluster_sizes):
        raise ValueError(
            f"{cfg_key}：每簇客户端数必须 ≥ t_c={t_c}（当前分簇 {cluster_sizes}），"
            f"请调大 num_clients 或调小 num_clusters/cluster_threshold")
    exp.athfhe = HierATHFHE(
        num_clusters=C, cluster_threshold=t_c,
        ring_degree=int(cfg.get('ring_degree', 256)),
        outer_modulus_bits=int(cfg.get('outer_modulus_bits', 60)),
        scale_bits=int(cfg.get('scale_bits', 40)),
        inner_noise_bits=int(cfg.get('inner_noise_bits', 20)),
        smudge_bits=int(cfg.get('smudge_bits', 10)),
        cluster_sizes=cluster_sizes,
        seed=exp.config['system'].get('random_seed', 42),
        parallel=bool(cfg.get('parallel', True)),
        parallel_workers=int(cfg.get('parallel_workers', 0)),
        parallel_progress=bool(cfg.get('parallel_progress', False)))
    return cfg


class HierAthfheTrack(Track):
    """方案一轨道：分簇层次 CKKS-ATASSES（内层升环 Q' > 2·n_c·Q²·B'）"""

    name = 'athfhe_hier'
    conflicts = ('athfhe_atasses', 'athfhe_twin', 'ppbr', 'rfl', 'ckks')
    supports_client_attacks = True
    needs_checkpoint = False

    def enabled(self, config: dict) -> bool:
        return bool((config.get('athfhe_hier') or {}).get('enabled', False))

    def wants_crypto(self, config: dict) -> bool:
        return False    # 自带外层 CKKS 仿真（numpy 大整数），不经 TenSEAL

    def init_protocol(self, exp) -> None:
        _check_conflicts(exp.config, 'athfhe_hier', 'athfhe_twin')
        _check_conflicts(exp.config, 'athfhe_hier', 'athfhe_atasses')
        _force_full_sampling(exp)
        cfg = _setup_hier(exp, 'athfhe_hier')
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

    def report_stats_section(self, exp, lines, m, num_rounds, is_encrypted) -> None:
        super().report_stats_section(exp, lines, m, num_rounds, is_encrypted)
        _athfhe_flow_report(exp, lines, m, 'hier')


class AtassesTrack(HierAthfheTrack):
    """ATASSES 基线轨道：论文原版（USENIX Sec'25）的忠实复现。

    数学上与 HierATHFHE 令 num_clusters=1 完全同构（簇间加法拆分退化为
    平凡：S^(1)=S），即单级 (T,N)-Shamir + BFV 内层加密（升环
    Q' > 2·N·Q²·B'）+ 两回合解密。单独成轨道的目的：与方案一/方案二在
    完全相同参数下做逐流程通信量对比（见 scripts/benchmark_athfhe.py
    与 doc/athfhe.md §与 ATASSES 的复杂度对比）。
    """

    name = 'athfhe_atasses'
    conflicts = ('athfhe_hier', 'athfhe_twin', 'ppbr', 'rfl', 'ckks')

    def enabled(self, config: dict) -> bool:
        return bool((config.get('athfhe_atasses') or {}).get('enabled', False))

    def init_protocol(self, exp) -> None:
        _check_conflicts(exp.config, 'athfhe_atasses', 'athfhe_hier')
        _check_conflicts(exp.config, 'athfhe_atasses', 'athfhe_twin')
        _force_full_sampling(exp)
        _setup_hier(exp, 'athfhe_atasses', c_override=1)
        p = exp.athfhe
        tree([
            ("🛡️", f"ATASSES-baseline: M={p.M}  |  Q={p.ctx_outer.q.bit_length()}bit"
                   f"  |  Δ=2^{p.delta.bit_length() - 1}  |  C=1（单簇退化，"
                   f"= 论文 ATASSES 忠实复现）T={p.t_c} N={p.N}"),
            ("🔐", f"内层升环: Q'={p.ctx_inner.q.bit_length()}bit "
                   f"(Q'>2·N·Q²·B'，单簇时 n_c=N，N 大时 Q' 位宽膨胀最快)  |  "
                   f"Δ'=2^{p.delta_prime.bit_length() - 1}  |  "
                   f"B_sm=2^{p.B_sm.bit_length() - 1} B'=2^{p.B_prime.bit_length() - 1}"),
        ], indent=1)
        for c in exp.clients:
            c.track = "athfhe_atasses"

    def record_key_distribution(self, exp) -> None:
        tag_info(LogTag.INIT,
                 "ATASSES-baseline: (T,N)-Shamir 份额随 Setup 分发"
                 "（每客户端 1 个外层环多项式）")

    def output_prefix(self, exp) -> str:
        return 'athfhe_atasses'

    def mode_label(self, exp) -> str:
        return "ATASSES-baseline（论文单级 (T,N)-Shamir + BFV 内层两回合）"

    def upload_label(self, exp) -> str:
        return "ATASSES 协议消息（外层 CKKS 密文 + 内层升环份额密文）"

    def run_round(self, exp, round_num: int) -> dict:
        return _run_athfhe_round(exp, round_num, exp.athfhe, 'atasses')


class TwinAthfheTrack(Track):
    """方案二轨道：非共谋双服务器 CKKS-ATASSES（PRG 种子掩码、不升环）"""

    name = 'athfhe_twin'
    conflicts = ('athfhe_hier', 'athfhe_atasses', 'ppbr', 'rfl', 'ckks')
    supports_client_attacks = True
    needs_checkpoint = False

    def enabled(self, config: dict) -> bool:
        return bool((config.get('athfhe_twin') or {}).get('enabled', False))

    def wants_crypto(self, config: dict) -> bool:
        return False    # 自带外层 CKKS 仿真（numpy 大整数），不经 TenSEAL

    def init_protocol(self, exp) -> None:
        _check_conflicts(exp.config, 'athfhe_twin', 'athfhe_hier')
        _check_conflicts(exp.config, 'athfhe_twin', 'athfhe_atasses')
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
            seed=exp.config['system'].get('random_seed', 42),
            parallel=bool(cfg.get('parallel', True)),
            parallel_workers=int(cfg.get('parallel_workers', 0)),
            parallel_progress=bool(cfg.get('parallel_progress', False)))
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

    def report_stats_section(self, exp, lines, m, num_rounds, is_encrypted) -> None:
        super().report_stats_section(exp, lines, m, num_rounds, is_encrypted)
        _athfhe_flow_report(exp, lines, m, 'twin')


register_track(HierAthfheTrack())
register_track(AtassesTrack())
register_track(TwinAthfheTrack())
