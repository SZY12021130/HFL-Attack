"""
几何中位数聚合 (Geometric Median)

出处: 经典 Weiszfeld 算法；作为拜占庭鲁棒聚合规则见
      Chen et al. POMACS'17 与 Blanchard et al. NDSS'17 的对比讨论。
"""

import torch
from typing import Dict, List, Optional

from baseline.median import aggregate as _median_aggregate


def aggregate(updates: List[Dict[str, torch.Tensor]],
              weights: Optional[List[float]] = None,
              max_iter: int = 10,
              tol: float = 1e-5,
              **params) -> Dict[str, torch.Tensor]:
    """
    几何中位数聚合 (Geometric Median)

    寻找使得到所有更新点距离之和最小的那个点。
    使用 Weiszfeld 算法迭代求解，初始值为坐标中值。

    Args:
        updates: 客户端更新列表
        weights: 客户端权重（本规则不使用）
        max_iter: Weiszfeld 最大迭代轮数
        tol: 收敛阈值（欧氏距离）
    """
    # 初始化为中位数
    current = _median_aggregate(updates)

    for _ in range(max_iter):
        prev = {k: v.clone() for k, v in current.items()}

        numerator = {k: torch.zeros_like(v) for k, v in current.items()}
        denominator = 0.0

        for u in updates:
            dist = 0.0
            for name in current.keys():
                dist += torch.sum((current[name] - u[name]) ** 2).item()
            dist = max(dist ** 0.5, 1e-10)

            w = 1.0 / dist
            for name in current.keys():
                numerator[name] += w * u[name]
            denominator += w

        for name in current.keys():
            current[name] = numerator[name] / denominator

        # 检查收敛
        diff = 0.0
        for name in current.keys():
            diff += torch.sum((current[name] - prev[name]) ** 2).item()
        if diff ** 0.5 < tol:
            break

    return current
