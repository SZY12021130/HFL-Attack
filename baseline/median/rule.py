"""
坐标中值聚合 (Coordinate-wise Median)

出处: Y. Chen, L. Su, J. Xu, "Distributed Statistical Machine Learning
      in Adversarial Settings: Byzantine Gradient Descent", POMACS 2017.
"""

import torch
from typing import Dict, List, Optional


def aggregate(updates: List[Dict[str, torch.Tensor]],
              weights: Optional[List[float]] = None,
              **params) -> Dict[str, torch.Tensor]:
    """
    坐标中值聚合

    对每个参数坐标的所有客户端取值取中值，
    不依赖客户端权重，对异常值有很强的鲁棒性。
    """
    aggregated = {}
    for name in updates[0].keys():
        # stack all tensors: [num_clients, *param_shape]
        stacked = torch.stack([u[name] for u in updates], dim=0)
        aggregated[name] = torch.median(stacked, dim=0).values
    return aggregated
