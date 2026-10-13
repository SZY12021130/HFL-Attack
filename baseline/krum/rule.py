"""
Krum 聚合

出处: P. Blanchard, E. M. El Mhamdi, R. Guerraoui, J. Stainer,
      "Machine Learning with Adversaries: Byzantine Tolerant Gradient
      Descent", NDSS 2017.
"""

import copy
import torch
from typing import Dict, List, Optional


def _pairwise_distance(updates: List[Dict[str, torch.Tensor]]) -> torch.Tensor:
    """计算所有客户端更新两两之间的欧氏距离平方"""
    n = len(updates)
    # 将每个更新展平为向量
    flattened = []
    for u in updates:
        vec = torch.cat([v.flatten() for v in u.values()])
        flattened.append(vec)

    # 计算两两距离矩阵
    dists = torch.zeros(n, n)
    for i in range(n):
        for j in range(i + 1, n):
            d = torch.sum((flattened[i] - flattened[j]) ** 2).item()
            dists[i, j] = d
            dists[j, i] = d
    return dists


def aggregate(updates: List[Dict[str, torch.Tensor]],
              weights: Optional[List[float]] = None,
              f: int = None,
              **params) -> Dict[str, torch.Tensor]:
    """
    Krum 聚合

    选择与其他更新最相似的那个更新作为聚合结果。
    假设有 f 个恶意客户端，选择与其 n − f − 2 个最近邻距离之和最小的更新。

    Args:
        updates: 客户端更新列表
        weights: 客户端权重（本规则不使用）
        f: 估计的恶意客户端数量（默认 n // 2 − 1 方向取整）

    威胁模型前提: 需要 n > 2f + 2，即恶意客户端不超过约半数。
    """
    n = len(updates)
    if f is None:
        f = max(0, (n - 3) // 2)

    assert n > 2 * f + 2, f"Krum requires n > 2f + 2, got n={n}, f={f}"

    dists = _pairwise_distance(updates)
    scores = []
    for i in range(n):
        # 排序距离，取最近的 n − f − 2 个（不包括自己）
        sorted_dists = torch.sort(dists[i])[0]
        # sorted_dists[0] 是自己到自己的距离 0
        score = torch.sum(sorted_dists[1:n - f]).item()
        scores.append(score)

    best_idx = int(torch.argmin(torch.tensor(scores)))
    return copy.deepcopy(updates[best_idx])
