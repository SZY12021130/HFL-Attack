"""
距离规避优化攻击：Min-Max / Min-Sum
====================================

论文出处：
- Shejwalkar & Houmansadr, "Manipulating the Byzantine: Optimizing Model
  Poisoning Attacks and Defenses for Federated Learning", USENIX Security 2021

算法：
1. 计算恶意方向 w_mal = (1/|M|)·Σ恶意更新 − (1/|H|)·Σ诚实更新，
   使聚合结果远离诚实更新方向。
2. 二分搜索缩放系数 γ，使恶意更新满足距离规避约束：
   - Min-Max: max_{h∈H} d(γ·w_mal, u_h) ≤ max_{h,h'∈H} d(u_h, u_h')
   - Min-Sum:  Σ_{h∈H} d(γ·w_mal, u_h) ≤ max_{h∈H} Σ_{h'≠h} d(u_h', u_h)
   其中 d 为 L2 范数（在展平向量上计算）。
3. 提交 诚实均值 + γ·w_mal（所有合谋者提交相同向量）。

参考开源：
- 论文作者方向实现 (https://github.com/vrt1shjwlkr)
- FLPoison 基准 (https://github.com/vio1etus/FLPoison)
- federated-learning-attack-code (https://github.com/anoxia-1/federated-learning-attack-code)

降级策略：本攻击需要合谋上下文。若 context.all_updates 为 None，
则抛 ValueError，提示调用方提供合谋上下文。
"""

import os
import sys
from typing import Dict, List

import torch

# 允许直接运行本文件（python hfl_attacks/model_poisoning/optimization_attacks.py）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from hfl_attacks.base import AttackContext, ClientUpdateAttack, register_attack


def _flatten(update: Dict[str, torch.Tensor]) -> torch.Tensor:
    """把参数字典展平为一维向量"""
    return torch.cat([v.detach().float().reshape(-1) for v in update.values()])


def _unflatten_like(vec: torch.Tensor,
                    reference: Dict[str, torch.Tensor]) -> Dict[str, torch.Tensor]:
    """把一维向量按 reference 的形状拆回参数字典"""
    out = {}
    offset = 0
    for k, v in reference.items():
        n = v.numel()
        out[k] = vec[offset:offset + n].reshape(v.shape).to(v.dtype)
        offset += n
    return out


class _BaseDistanceEvasion(ClientUpdateAttack):
    """Min-Max / Min-Sum 共用逻辑：恶意方向 + 二分搜索缩放系数"""

    def __init__(self, scale_search_loops: int = 5):
        self.scale_search_loops = scale_search_loops

    # ---- 子类实现两种约束 ----
    def _constraint(self, w_mal: torch.Tensor,
                    honest_flat: List[torch.Tensor]) -> float:
        """返回 γ 需满足的右端上界（与 γ 无关的基准值）"""
        raise NotImplementedError

    def _left_side(self, gamma: float, w_mal: torch.Tensor,
                   honest_flat: List[torch.Tensor]) -> float:
        """返回给定 γ 时左端（攻击向量到诚实更新的距离度量）"""
        raise NotImplementedError

    def _search_gamma(self, w_mal: torch.Tensor,
                      honest_flat: List[torch.Tensor]) -> float:
        """二分搜索满足约束的最大 γ（γ 越大攻击越强）"""
        bound = self._constraint(w_mal, honest_flat)
        lo, hi = 1e-6, 1e6
        gamma = 1.0
        for _ in range(self.scale_search_loops * 10):
            gamma = (lo + hi) / 2.0
            if self._left_side(gamma, w_mal, honest_flat) <= bound:
                lo = gamma  # 满足约束，尝试更大的 γ
            else:
                hi = gamma
        return gamma

    def attack(self, update: Dict[str, torch.Tensor],
               context: AttackContext) -> Dict[str, torch.Tensor]:
        if context.all_updates is None:
            raise ValueError(
                f"{self.__class__.__name__} 需要合谋上下文：请提供 "
                "context.all_updates（本轮全部客户端更新）与 "
                "context.malicious_indices。该攻击无法仅凭 "
                "global_params/self_update 执行。"
            )

        mal_set = set(context.malicious_indices)
        honest = [u for i, u in enumerate(context.all_updates) if i not in mal_set]
        malicious = [u for i, u in enumerate(context.all_updates) if i in mal_set]
        if not honest or not malicious:
            raise ValueError(
                f"{self.__class__.__name__} 需要至少一个诚实和一个恶意客户端更新。"
            )

        honest_flat = [_flatten(u) for u in honest]
        mal_flat = [_flatten(u) for u in malicious]

        # 恶意方向：恶意均值 − 诚实均值
        w_mal = torch.stack(mal_flat).mean(dim=0) \
            - torch.stack(honest_flat).mean(dim=0)

        gamma = self._search_gamma(w_mal, honest_flat)
        honest_mean = torch.stack(honest_flat).mean(dim=0)
        poisoned_vec = honest_mean + gamma * w_mal
        return _unflatten_like(poisoned_vec, honest[0])


@register_attack
class MinMaxAttack(_BaseDistanceEvasion):
    """
    Min-Max 攻击：选择 γ 使
        max_{h∈H} d(γ·w_mal, u_h) ≤ max_{h,h'∈H} d(u_h, u_h')
    即攻击向量到任意诚实更新的最大距离，不超过诚实更新两两之间的
    最大距离（模拟 Krum 等基于距离的防御视角）。
    """

    name = "model:min_max"

    def _constraint(self, w_mal, honest_flat):
        max_dist = 0.0
        for i in range(len(honest_flat)):
            for j in range(i + 1, len(honest_flat)):
                d = torch.norm(honest_flat[i] - honest_flat[j]).item()
                max_dist = max(max_dist, d)
        return max_dist

    def _left_side(self, gamma, w_mal, honest_flat):
        vec = gamma * w_mal
        return max(torch.norm(vec - h).item() for h in honest_flat)


@register_attack
class MinSumAttack(_BaseDistanceEvasion):
    """
    Min-Sum 攻击：选择 γ 使
        Σ_{h∈H} d(γ·w_mal, u_h) ≤ max_{h∈H} Σ_{h'≠h} d(u_h', u_h)
    即攻击向量到全体诚实更新的距离之和，不超过任一诚实更新到其余
    诚实更新的距离和的最大值。
    """

    name = "model:min_sum"

    def _constraint(self, w_mal, honest_flat):
        max_sum = 0.0
        for i in range(len(honest_flat)):
            s = sum(
                torch.norm(honest_flat[i] - honest_flat[j]).item()
                for j in range(len(honest_flat)) if j != i
            )
            max_sum = max(max_sum, s)
        return max_sum

    def _left_side(self, gamma, w_mal, honest_flat):
        vec = gamma * w_mal
        return sum(torch.norm(vec - h).item() for h in honest_flat)


if __name__ == "__main__":
    torch.manual_seed(0)
    # 3 个诚实客户端 + 2 个恶意客户端（下标 3、4）
    keys = ["w1", "w2"]
    honest = [{k: torch.randn(3, 4) for k in keys} for _ in range(3)]
    malicious = [{k: torch.randn(3, 4) + 0.5 for k in keys} for _ in range(2)]
    all_updates = honest + malicious
    ctx = AttackContext(
        global_params={k: torch.zeros(3, 4) for k in keys},
        self_update=all_updates[3],
        all_updates=all_updates,
        self_index=3,
        malicious_indices=[3, 4],
    )

    for atk in (MinMaxAttack(), MinSumAttack()):
        for i in (3, 4):
            out = atk.attack(all_updates[i], ctx)
            for k in keys:
                print(f"{atk.name} client {i} {k}: "
                      f"||poisoned|| = {torch.norm(out[k]).item():.4f}"
                      f"  ||clean|| = {torch.norm(all_updates[i][k]).item():.4f}")
    print("optimization_attacks self-test passed")
