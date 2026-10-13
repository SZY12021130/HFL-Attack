"""
截断均值聚合 (Trimmed Mean / TrMean)

出处: Y. Chen, L. Su, J. Xu, "Distributed Statistical Machine Learning
      in Adversarial Settings: Byzantine Gradient Descent", POMACS 2017.
"""

import torch
from typing import Dict, List, Optional


def aggregate(updates: List[Dict[str, torch.Tensor]],
              weights: Optional[List[float]] = None,
              trim_ratio: float = 0.1,
              **params) -> Dict[str, torch.Tensor]:
    """
    截断均值聚合

    对每个坐标，去掉最高和最低的 trim_ratio 比例的值，然后取平均。
    需要恶意客户端比例小于 trim_ratio。

    Args:
        updates: 客户端更新列表
        weights: 客户端权重（本规则不使用）
        trim_ratio: 每端截断比例 (0.0 ~ 0.5)
    """
    n = len(updates)
    n_trim = int(n * trim_ratio)

    aggregated = {}
    for name in updates[0].keys():
        stacked = torch.stack([u[name] for u in updates], dim=0)
        # 按元素逐个坐标排序后截断
        original_shape = stacked.shape[1:]
        flat = stacked.view(n, -1)  # [n, num_elements]
        sorted_vals, _ = torch.sort(flat, dim=0)
        trimmed = sorted_vals[n_trim:n - n_trim]
        mean_trimmed = trimmed.mean(dim=0)
        aggregated[name] = mean_trimmed.view(original_shape)
    return aggregated
