"""
标准 FedAvg 加权平均聚合（无防御基线）

出处: McMahan et al., "Communication-Efficient Learning of Deep Networks
      from Decentralized Data", AISTATS 2017.
"""

import torch
from typing import Dict, List, Optional


def aggregate(updates: List[Dict[str, torch.Tensor]],
              weights: Optional[List[float]] = None,
              **params) -> Dict[str, torch.Tensor]:
    """
    标准 FedAvg 聚合（基线）

    Args:
        updates: 客户端更新列表
        weights: 客户端权重（默认等权）
    """
    if weights is None:
        weights = [1.0] * len(updates)
    total_weight = sum(weights)
    normalized = [w / total_weight for w in weights]

    aggregated = {}
    for name in updates[0].keys():
        weighted_sum = None
        for i, update in enumerate(updates):
            weighted = update[name] * normalized[i]
            if weighted_sum is None:
                weighted_sum = weighted
            else:
                weighted_sum += weighted
        aggregated[name] = weighted_sum
    return aggregated
