"""
IPM（Inner Product Manipulation）合谋模型投毒攻击
================================================

论文出处：
- Xie et al., "Fall of Empires: Breaking Byzantine-tolerant SGD by Inner Product
  Manipulation", USENIX Security 2021
- PPBR (He 等, IEEE TMC 2026) 评测的 6 种攻击之一：β_in 个合谋拜占庭客户端
  知道良性更新均值时，设
      ∇ℓ_1 = … = ∇ℓ_{β_in} = −ξ/(n_i−β_in) · Σ_{j=β_in+1}^{n_i} ∇ℓ_j
  ξ 为调节恶意更新幅度的正因子。

原理：恶意更新与良性均值的内积为负（方向反转），且若 ξ=1，全部更新的
代数和为零——梯度下降被完全抵消。因其不依赖大幅值，可绕过基于范数的
异常过滤，需靠 PPBR 的符号统计聚类（方向性特征）检测。

威胁等级：合谋攻击——需要 all_updates（可见本轮全部明文更新）。
"""

import os
import sys

import torch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from hfl_attacks.base import ClientUpdateAttack, register_attack


@register_attack
class IPMAttack(ClientUpdateAttack):
    """IPM 合谋攻击：恶意客户端集体提交 −ξ/(n−m)·Σ_benign Δ_j"""

    name = "model:ipm"

    def __init__(self, xi: float = 1.0):
        """
        Args:
            xi: 幅度因子 ξ（论文默认正因子，ξ=1 时全体更新代数和为零）
        """
        self.xi = xi

    def attack(self, update, context):
        all_updates = context.all_updates
        malicious = set(context.malicious_indices or [])
        if not all_updates or not malicious:
            # 无合谋上下文（如单攻击者离线评估）：退化为零更新
            return {k: torch.zeros_like(v) for k, v in update.items()}

        benign = [u for i, u in enumerate(all_updates) if i not in malicious]
        if not benign:
            return {k: torch.zeros_like(v) for k, v in update.items()}

        scale = -self.xi / len(benign)
        out = {}
        for k in update:
            out[k] = scale * torch.stack([u[k] for u in benign]).sum(0)
        return out


if __name__ == "__main__":
    from hfl_attacks.base import AttackContext

    torch.manual_seed(0)
    updates = [{"w": torch.randn(4)} for _ in range(8)]
    mal = [6, 7]
    # 零和条件：ξ = (n−m)/m = 3（此时 Σ_all = (1 − mξ/(n−m))·Σ_benign = 0）
    for i in mal:
        ctx = AttackContext(all_updates=updates, self_index=i,
                            malicious_indices=mal)
        updates[i] = IPMAttack(xi=3.0).attack(updates[i], ctx)
    total = torch.stack([u["w"] for u in updates]).sum(0)
    assert total.abs().max() < 1e-5, "ξ=(n−m)/m 时全体更新代数和应为零"
    print("IPM self-test passed: Σupdates ≈ 0")
