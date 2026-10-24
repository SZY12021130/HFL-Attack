"""
rfl/track.py：RFL 轨道（鲁棒 + 可验证联邦学习，Zhou et al. IEEE TDSC 2026）

收编原 main.py 中 RFL 轨道专属的实验编排：RFLProtocol 初始化
（自动开启层次架构、模型切分校验）、RFL 协议单轮执行
（split learning + 签名/同态哈希可验证聚合）、hfl_attacks 不适用的警告。

RFL 不经单密钥 CKKS：加密体系为数字签名 + 同态哈希，常与
encryption.enabled=false（明文模式）搭配，此时密钥分发按明文轨道语义
记录为 0。RFL 支持云 CS 恶意行为演示（cs_tamper / simulate_ifgia），
不支持 hfl_attacks 的客户端攻击注入。

导入纪律：静态导入仅限 base、rfl 包与第三方；rfl 协议类在
init_protocol 内惰性导入（与原 main.py 的导入时机一致）。
"""

import time

import torch
import torch.nn as nn
import numpy as np
import psutil

from base.experiment import Track, register_track
from base.data import evaluate
from base.log_utils import (
    tree,
    LogTag, tag_info, tag_warn,
    log_round_header,
)


class RflTrack(Track):
    """RFL 协议轨道：三层 云CS/边缘ES/客户端EC，split learning + 可验证聚合"""

    name = 'rfl'
    conflicts = ('ppbr', 'ckks')
    supports_client_attacks = False   # split learning 消息语义不同于完整模型更新 Δ
    needs_checkpoint = False

    def enabled(self, config: dict) -> bool:
        return bool((config.get('rfl') or {}).get('enabled', False))

    def init_protocol(self, exp) -> None:
        # RFL（Zhou et al. IEEE TDSC 2026，doc/rfl.md）：鲁棒 + 可验证联邦学习。
        # 开启后自动启用层次架构（云CS-边缘ES-客户端EC 是 RFL 的前提）。
        # 信任模型与 CKKS/PPBR 轨道不同：云 CS 可半诚实或恶意（伪造虚拟
        # 客户端 IFGIA、篡改/丢弃聚合结果），ES/EC 诚实；防御 = 式(9)
        # 签名身份验证（伪造身份被拒）+ 式(17) 同态哈希聚合校验（篡改被
        # 检出，全体 ES 中止本轮）。加密体系为签名 + 同态哈希，不经 CKKS。
        rfl_cfg = exp.config.get('rfl') or {}
        if not exp.config.get('hierarchy', {}).get('enabled', False):
            exp.config['hierarchy']['enabled'] = True
            tag_warn(LogTag.ATTACK,
                     "rfl.enabled=true → 自动开启 hierarchy（云-边-端三层架构）")
        from rfl import RFLProtocol, build_split
        try:
            build_split(exp.global_model,
                        int(rfl_cfg.get('num_shallow_layers', 1)))
        except ValueError as e:
            raise ValueError(f"RFL 模型切分失败：{e}") from e
        exp.rfl = RFLProtocol(
            exp.config,
            seed=exp.config['system'].get('random_seed', 42))
        rc = exp.rfl.cfg
        tree([
            ("🛡️", f"RFL: ε={rc.epsilon} δ={rc.delta_dp} C={rc.clip_threshold}"
                   f"  |  Ls={rc.num_shallow_layers}  |  HH={rc.hash_bits}-bit"
                   f"  |  tamper={rc.cs_tamper}  ifgia={rc.simulate_ifgia}"),
        ], indent=1)
        for c in exp.clients:
            c.track = "rfl"

    def on_attacks_ready(self, exp) -> None:
        if exp.rfl is not None:
            tag_warn(LogTag.ATTACK,
                     "RFL 轨道：hfl_attacks 的客户端攻击注入不适用"
                     "（split learning 消息语义不同于完整模型更新 Δ），"
                     "已跳过；可验证性/IFGIA 演示请用 rfl 配置段的 "
                     "cs_tamper / simulate_ifgia")

    def record_key_distribution(self, exp) -> None:
        # RFL 通常与 encryption.enabled=false 搭配（明文模式分支语义）：
        # 无统一密钥分发环节（签名密钥为协议内自注册，不经 KGC 分发）
        exp.metrics.record('key_distribution_cost', 0.0)
        tag_info(LogTag.INIT, "Plaintext mode: 无 CKKS 密钥分发")

    def mode_label(self, exp) -> str:
        return "RFL"

    def upload_label(self, exp) -> str:
        return "RFL 协议消息（加噪中间表示 + 签名 + 同态哈希）"

    def run_round(self, exp, round_num: int) -> dict:
        return run_round_rfl(exp, round_num)


register_track(RflTrack())


def run_round_rfl(exp, round_num: int) -> dict:
    """
    运行一轮 RFL 协议（Zhou et al. IEEE TDSC 2026，鲁棒+可验证，RFL 轨道）

    流程：CS 切分全局模型（浅层→EC，深层→ES）→ 新客户端集成（EC→ES
          直连注册 pk）→ split learning（式 6-12：EC 浅层前向 + 式 7 DP
          加噪 + 式 8 签名上传 → ES 式 9 验签 + 式 10 深层前向 → EC 式 11
          输出梯度 → ES 式 12 深层反向得 g_v）→ ES 式 13/14 同态哈希+签名
          上传 CS → CS 式 15 聚合广播 → 各 ES 式 16/17 签名+哈希校验
          （失败全体中止本轮）→ 云更新 w ← w − ζ·g → 明文评估

    信任模型（doc/rfl.md）：CS 可半诚实/恶意（伪造客户端 IFGIA、篡改
    聚合），ES/EC 诚实；防御 = 式(9) 签名身份验证 + 式(17) 同态哈希。
    不走全局掩码投票 / TopK / 单密钥 CKKS 聚合；不支持 hfl_attacks
    注入（split learning 消息语义不同，见初始化警告）。
    """
    log_round_header(round_num + 1, exp.config['system']['num_rounds'],
                     mode="RFL")
    round_start = time.time()

    selected_clients = exp._select_clients()
    loaders = {c.client_id: c.train_loader for c in selected_clients}
    groups = exp.hierarchy.group_clients(
        [c.client_id for c in selected_clients], round_num)

    # ==== RFL 完整协议（算法 2，见 rfl/protocol.py 时序注释）====
    grad, aborted = exp.rfl.run_round(
        round_num, loaders, groups, exp.server.model)
    st = exp.rfl.last_stats
    wire = exp.rfl.last_wire
    exp.metrics.record('rfl_client_wire',
                       wire.get('client_smashed', 0)
                       + wire.get('client_sig', 0)
                       + wire.get('client_outgrad', 0)
                       + wire.get('client_gradback', 0)
                       + wire.get('client_shallow', 0))
    exp.metrics.record('rfl_edge_wire', wire.get('es_to_cloud', 0))
    exp.metrics.record('rfl_cloud_wire', wire.get('cloud_to_es', 0))
    for c in selected_clients:
        exp.client_participation[c.client_id] += 1
    tag_info(LogTag.ATTACK,
             f"round {round_num + 1}: RFL 校验 "
             f"{'通过' if st.get('verified') else '失败/中止'} "
             f"edges={st.get('num_edges', 0)} "
             f"拒绝伪造={st.get('rejected_fabricated', 0)}"
             + (f" 恶意CS篡改: {st.get('tampered')}" if st.get('tampered') else ""))

    # 云更新全局模型：w ← w − ζ·g（g 为聚合梯度方向）
    zeta = exp.rfl.cfg.global_zeta
    with torch.no_grad():
        for name, p in exp.server.model.named_parameters():
            if name in grad:
                p.add_(grad[name].to(p.device), alpha=-zeta)

    # 评估：全局模型对测试集明文评估
    criterion = nn.CrossEntropyLoss()
    test_loss, test_acc = evaluate(
        exp.server.model, exp.test_loader, criterion, exp.device)
    exp.server.record_evaluation(test_loss, test_acc)

    exp.metrics.record('train_loss',
                       float(st.get('mean_loss', 0.0)))
    exp.metrics.record('train_acc', 0.0)
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
             f"real_time={round_time:.2f}s  |  mode=RFL"
             + ("  |  ABORTED(验证失败)" if aborted else ""))

    return {
        'round': round_num + 1,
        'test_acc': test_acc,
        'test_loss': test_loss,
        'round_time': round_time,
        'simulated_round_duration': 0.0,
    }
