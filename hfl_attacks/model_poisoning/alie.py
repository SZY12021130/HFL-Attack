"""
ALIE 合谋攻击 (A Little Is Enough)
==================================

论文出处：
- Baruch et al., "A Little Is Enough: Circumventing Defenses For Distributed
  Learning", NeurIPS 2019

算法：合谋的攻击者观察合谋外诚实客户端的更新，对全部更新逐参数（逐坐标）
求均值 μ 和标准差 σ，然后每个合谋者提交相同的向量 μ + z·σ —— 即比诚实
更新的均值"多一点点"，恰好落在 Krum、trimmed-mean 等统计防御的容忍范围
之外，使聚合结果被持续拉偏。

参考开源：
- FLPoison 基准 (https://github.com/vio1etus/FLPoison)
- federated-learning-attack-code (https://github.com/anoxia-1/federated-learning-attack-code)

降级策略：本攻击需要合谋上下文（context.all_updates + malicious_indices）。
若 context.all_updates 为 None，则抛 ValueError，提示调用方提供合谋上下文。
"""

import os
import sys
from typing import Dict, List

import torch

# 允许直接运行本文件（python hfl_attacks/model_poisoning/alie.py）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from hfl_attacks.base import AttackContext, ClientUpdateAttack, register_attack


@register_attack
class ALIEAttack(ClientUpdateAttack):
    """
    ALIE 合谋攻击：所有合谋者提交 逐坐标 (μ + z·σ)，μ/σ 为全部更新
    （含合谋者自身上一轮提交，模拟攻击者可见的诚实更新集合）的逐坐标
    均值与标准差。

    注：标准实现中 σ 通常只统计诚实客户端更新；这里按 Baruch et al.
    论文 Algorithm 1，用全部可见更新的逐坐标统计量（合谋客户端占比小
    时二者几乎相同）。

    参数 z 控制偏移强度：z=1.5 是论文对 trimmed-mean 防御的推荐值。
    """

    name = "model:alie"

    def __init__(self, z: float = 1.5):
        self.z = z

    def attack(self, update: Dict[str, torch.Tensor],
               context: AttackContext) -> Dict[str, torch.Tensor]:
        if context.all_updates is None:
            raise ValueError(
                "ALIEAttack 需要合谋上下文：请提供 context.all_updates "
                "（本轮全部客户端更新）以便计算逐坐标均值 μ 与标准差 σ。"
                "该攻击无法仅凭 global_params/self_update 执行。"
            )

        honest_updates = [
            u for i, u in enumerate(context.all_updates)
            if i not in set(context.malicious_indices)
        ]
        if not honest_updates:
            raise ValueError(
                "ALIEAttack 需要至少一个合谋外（诚实）客户端的更新。"
            )

        stacked = {
            k: torch.stack([u[k].float() for u in honest_updates])
            for k in honest_updates[0]
        }
        poisoned = {}
        for k, tensors in stacked.items():
            mu = tensors.mean(dim=0)
            sigma = tensors.std(dim=0, unbiased=False)
            poisoned[k] = mu + self.z * sigma
        return poisoned


if __name__ == "__main__":
    torch.manual_seed(0)
    # 3 个诚实客户端 + 2 个恶意客户端（下标 3、4）
    keys = ["w1", "w2"]
    honest = [{k: torch.randn(3, 4) for k in keys} for _ in range(3)]
    malicious = [{k: torch.randn(3, 4) for k in keys} for _ in range(2)]
    all_updates = honest + malicious
    ctx = AttackContext(
        global_params={k: torch.zeros(3, 4) for k in keys},
        self_update=all_updates[3],
        all_updates=all_updates,
        self_index=3,
        malicious_indices=[3, 4],
    )

    atk = ALIEAttack(z=1.5)
    for i in (3, 4):
        out = atk.attack(all_updates[i], ctx)
        for k in keys:
            print(f"client {i} {k}: ||poisoned|| = {torch.norm(out[k]).item():.4f}"
                  f"  ||clean|| = {torch.norm(all_updates[i][k]).item():.4f}")
    print("alie self-test passed")
