"""
ppbr/track.py：PPBR 轨道（可插拔拜占庭鲁棒，He 等 IEEE TMC 2026）

收编原 main.py 中 PPBR 轨道专属的实验编排：PPBRProtocol 初始化
（自动开启层次架构、恶意客户端集合回填、跨轮检查点动量/rng）、
PPBR 协议单轮执行、线上传输审计的最终横幅与报告分节。

导入纪律：静态导入仅限 base、ppbr 包与第三方；ppbr 协议类在
init_protocol 内惰性导入（与原 main.py 的导入时机一致）。
"""

import time

import torch
import torch.nn as nn
import numpy as np
import psutil

from base.experiment import Track, register_track
from base.data import evaluate, get_model_params
from base.log_utils import (
    banner, blank_line, kv, tree,
    LogTag, tag_info, tag_warn,
    log_round_header,
)


class PpbrTrack(Track):
    """PPBR 协议轨道：云-边-端完整密文路线（双轨架构的 PPBR 轨）"""

    name = 'ppbr'
    conflicts = ('rfl',)
    supports_client_attacks = True
    needs_checkpoint = True     # 动量与 rng 是跨轮状态，需检查点续跑

    def enabled(self, config: dict) -> bool:
        return bool((config.get('ppbr') or {}).get('enabled', False))

    def init_protocol(self, exp) -> None:
        # PPBR（可插拔拜占庭鲁棒，He 等 IEEE TMC 2026）：开启后自动启用层次架构
        # （云-边-端是 PPBR 的前提），无需手动设置 hierarchy.enabled。
        # 双轨架构：开启后整条加解密路线走 PPBR 协议（云持 sk_A、边缘各自持
        # 私钥、SA 掩码聚合、CLDP、范数盲化裁剪），不走全局投票/TopK/单密钥 CKKS。
        if not exp.config.get('hierarchy', {}).get('enabled', False):
            exp.config['hierarchy']['enabled'] = True
            tag_warn(LogTag.ATTACK,
                     "ppbr.enabled=true → 自动开启 hierarchy（云-边-端架构）")
        from ppbr import PPBRProtocol
        exp.ppbr = PPBRProtocol(
            exp.config,
            num_edges=int(exp.config['hierarchy'].get('num_edges', 3)),
            seed=exp.config['system'].get('random_seed', 42))
        # 供协议日志区分良性/恶意（攻击实验的过滤诊断）；
        # exp.malicious_ids 在攻击初始化才填充，先置空、后回填
        exp.ppbr._malicious_ids = set()
        c = exp.ppbr.cfg
        tree([
            ("🛡️", f"PPBR: ε_χ={c.epsilon_chi}  |  k={c.k} b={c.b}  |  "
                   f"φ={c.momentum_phi} ζ={c.global_zeta}  |  "
                   f"β={'已知' if c.beta_known else '未知'}"),
        ], indent=1)
        # 双轨标记：客户端打印与 PTopk 残差逻辑按 PPBR 语义走
        for c in exp.clients:
            c.track = "ppbr"

    def on_attacks_ready(self, exp) -> None:
        if exp.ppbr is not None:
            exp.ppbr._malicious_ids = set(exp.malicious_ids)

    def record_key_distribution(self, exp) -> None:
        # PPBR 轨道：云公钥 pk_A 随 w_a 广播，边缘公钥注册到云，
        # SA 密钥每轮临时生成——无统一密钥分发环节
        exp.metrics.record('key_distribution_cost', 0.0)
        tag_info(LogTag.INIT,
                 "PPBR: pk_A 随全局模型广播，边缘公钥注册到云，"
                 "SA 密钥每轮临时生成（无统一密钥分发）")

    def save_checkpoint(self, exp, ckpt: dict) -> None:
        ckpt['ppbr_momenta'] = exp.ppbr.controller.momenta
        ckpt['ppbr_rng_state'] = exp.ppbr.rng.get_state()

    def load_checkpoint(self, exp, ckpt: dict) -> None:
        exp.ppbr.controller.momenta = {
            cid: {k: v.to(exp.device) for k, v in m.items()}
            for cid, m in ckpt['ppbr_momenta'].items()}
        exp.ppbr.rng.set_state(ckpt['ppbr_rng_state'])

    def mode_label(self, exp) -> str:
        return "PPBR"

    def upload_label(self, exp) -> str:
        return "PPBR 协议消息（[x]_A + SAz）"

    def print_final_summary(self, exp) -> None:
        # 终端打印：PPBR 轨道打线上传输审计
        mm = exp.metrics.get_metrics()
        blank_line()
        banner("PPBR Wire Statistics", width=72)

        def _sum(key: str) -> int:
            return int(sum(mm.get(key, [])))

        kv("客户端→边缘 [x_{i,j}]_A 密文块", _sum('ppbr_client_x_blocks'), indent=1)
        kv("边缘→云 [x_i]_A 密文块", _sum('ppbr_edge_xsum_blocks'), indent=1)
        kv("边缘→云 [2m'_i]_{Ei} 密文块", _sum('ppbr_edge_2mp_blocks'), indent=1)
        kv("云→边缘盲化乘积密文块", _sum('ppbr_norm_products'), indent=1)
        kv("SA early 掉线（输入不计入）", _sum('ppbr_sa_early_dropped'), indent=1)
        kv("SA late 掉线（输入仍计入）", _sum('ppbr_sa_late_dropped'), indent=1)

    def report_formulas(self, exp) -> list:
        return [
            "客户端→边缘上传 / Client→Edge | `[x_{{i,j}}]_A`（云公钥分块密文）+ `z`（SA 掩码）",
            "边缘→云上传 / Edge→Cloud | `[x_i]_A = Σ_j [x_{{i,j}}]_A`、`[2m'_i]_{{Ei}}`（边缘公钥，云解不开）",
            "云→边缘下发 / Cloud→Edge | `[v]_{{Ei}} = [2m'_i]_{{Ei}} ∘ (r_i·x_i)` 盲化乘积密文",
            "SA 掉线恢复 / Dropout recovery | Shamir (t,n−1) 份额重建 DH 私钥与自负载种子（early 不计入 / late 计入）",
            "全局更新 / Global update | `w_a ← w_a + ζ·m_a`，`m_a` 经去噪 `Σ x''_i(m'_i+x_i)/l'`",
        ]

    def report_stats_section(self, exp, lines, m, num_rounds, is_encrypted) -> None:
        lines.append(f"## 3. PPBR Wire Statistics / PPBR 线上传输审计（密文块数）")
        lines.append(f"")
        lines.append(f"| Round / 轮次 | 客户端→边缘 [x]_A 块 | 边缘→云 [x_i]_A 块 | "
                     f"边缘→云 [2m'_i] 块 | 云→边缘盲化乘积块 | SA early 掉线 | SA late 掉线 |")
        lines.append(f"|---|---|---|---|---|---|---|")
        for r in range(num_rounds):
            def _at(k):
                v = m.get(k, [])
                return v[r] if r < len(v) else 0
            lines.append(
                f"| {r+1} | {_at('ppbr_client_x_blocks')} | {_at('ppbr_edge_xsum_blocks')} | "
                f"{_at('ppbr_edge_2mp_blocks')} | {_at('ppbr_norm_products')} | "
                f"{_at('ppbr_sa_early_dropped')} | {_at('ppbr_sa_late_dropped')} |")
        lines.append(f"")

    def run_round(self, exp, round_num: int) -> dict:
        return run_round_ppbr(exp, round_num)


register_track(PpbrTrack())


def run_round_ppbr(exp, round_num: int) -> dict:
    """
    运行一轮 PPBR 协议（He 等 IEEE TMC 2026 完整密文路线，双轨架构的 PPBR 轨道）

    流程：云明文广播 w_a（论文设定：全局模型公开）→ 客户端本地训练
          → 攻击注入（与 CKKS 轨道同一威胁模型）→ 算法 2 符号过滤
          → 客户端噪声掩码 + [x]_A + SA 掩码上传 → 边缘 SA 聚合 + [2m']_{Ei}
          → 云两轮点积范数盲化 → 范数中位数筛选 + 盲化裁剪聚合 → denoise 得 m_a
          → 云更新 w_a ← w_a + ζ·m_a（论文 w ← w − ζ·m，符号见 doc/ppbr.md）
          → 明文评估

    信任模型与 CKKS 轨道不同（见 doc/ppbr.md）：云持 sk_A（论文原样），
    每个边缘自持 sk_{Ei}，客户端不持任何服务器密钥；不走全局掩码投票、
    TopK 稀疏化与单密钥 CKKS 密文聚合。
    """
    log_round_header(round_num + 1, exp.config['system']['num_rounds'],
                     mode="PPBR")
    round_start = time.time()

    selected_clients = exp._select_clients()

    # 云明文广播全局模型（论文设定：w_a 公开，任何客户端可得）
    global_params = get_model_params(exp.server.model)
    client_updates, train_losses, train_accs = [], [], []
    for client in selected_clients:
        update, loss, acc = client.local_train(
            global_params=global_params, round_num=round_num)
        client_updates.append(update)
        train_losses.append(loss)
        train_accs.append(acc)
        exp.metrics.record('local_train_time', client.local_train_time)

    # 攻击注入（与 CKKS 轨道相同语义：客户端本地、加密上传之前）
    client_weights = [1.0] * len(selected_clients)
    selected_clients, client_updates, _, train_losses, train_accs = \
        exp._apply_attacks_to_updates(
            round_num, selected_clients, client_updates,
            client_weights, train_losses, train_accs)

    # ==== PPBR 完整协议（算法 2/3/4/5，见 ppbr/protocol.py 时序注释）====
    updates_by_cid = {c.client_id: u for c, u in
                      zip(selected_clients, client_updates)}
    m_a = {k: torch.zeros_like(v)
           for k, v in get_model_params(exp.server.model).items()}
    if updates_by_cid:
        groups = exp.hierarchy.group_clients(
            [c.client_id for c in selected_clients], round_num)
        m_a = exp.ppbr.run_round(round_num, updates_by_cid, groups,
                                 num_edges=exp.hierarchy.num_edges)
        st = exp.ppbr.last_stats.get('edges', {})
        # PPBR 线上传输审计（块数）：累积进 metrics，供最终汇总打印
        wire = exp.ppbr.last_wire
        exp.metrics.record('ppbr_client_x_blocks',
                           wire.get('client_x_blocks', 0))
        exp.metrics.record('ppbr_edge_xsum_blocks',
                           wire.get('edge_xsum_blocks', 0))
        exp.metrics.record('ppbr_edge_2mp_blocks',
                           wire.get('edge_2mp_blocks', 0))
        exp.metrics.record('ppbr_norm_products',
                           wire.get('norm_products', 0))
        exp.metrics.record('ppbr_sa_early_dropped',
                           wire.get('sa_early_dropped', 0))
        exp.metrics.record('ppbr_sa_late_dropped',
                           wire.get('sa_late_dropped', 0))
        tag_info(LogTag.ATTACK,
                 f"round {round_num + 1}: PPBR 云筛选 "
                 f"edges={sorted(st.get('trusted', []))} "
                 f"M={st.get('M', 0):.4f} "
                 f"clip={ {e: round(x, 3) for e, x in st.get('clip', {}).items()} }")

    # 云更新全局模型：w_a ← w_a + ζ·m_a（对应算法 5 的 w ← w − ζ·m——
    # 论文 m 为梯度方向，本项目 Δ 为参数变化量即下降方向，故符号为正）
    zeta = exp.ppbr.cfg.global_zeta
    with torch.no_grad():
        for name, p in exp.server.model.named_parameters():
            p.add_(m_a[name].to(p.device), alpha=+zeta)

    # 评估：论文设定 w_a 公开，直接对明文全局模型评估
    criterion = nn.CrossEntropyLoss()
    test_loss, test_acc = evaluate(
        exp.server.model, exp.test_loader, criterion, exp.device)
    exp.server.record_evaluation(test_loss, test_acc)

    exp.metrics.record('train_loss',
                       float(np.mean(train_losses)) if train_losses else 0.0)
    exp.metrics.record('train_acc',
                       float(np.mean(train_accs)) if train_accs else 0.0)
    exp.metrics.record('test_loss', test_loss)
    exp.metrics.record('test_acc', test_acc)
    exp.metrics.record('communication_rounds', 1)
    exp.metrics.record('cpu_utilization', psutil.cpu_percent())
    exp.metrics.record('memory_usage', psutil.virtual_memory().percent)

    round_time = time.time() - round_start
    exp.metrics.record('total_round_time', round_time)
    exp.metrics.record('total_round_time_simulated', 0.0)

    tag_info(LogTag.ROUND,
             f"#{round_num + 1} done  |  acc={test_acc:.2f}%  |  "
             f"real_time={round_time:.2f}s  |  mode=PPBR")

    return {
        'round': round_num + 1,
        'test_acc': test_acc,
        'test_loss': test_loss,
        'round_time': round_time,
        'simulated_round_duration': 0.0,
    }
