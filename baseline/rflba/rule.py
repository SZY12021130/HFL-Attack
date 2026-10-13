"""
RFLBA：余弦相似度过滤 + 质心估计

出处: X. Cao, M. Fang, J. Liu, N. Z. Gong, "FLTrust: Byzantine-robust
      Federated Learning via Trust Bootstrapping", NDSS 2021.
      （余弦相似度剔除低相似更新后对幸存者取均值/质心的思路）
"""

import torch
from typing import Dict, List, Optional

from ckks_fed.defenses.anomaly_detection import cosine_similarity_filter
from baseline.fedavg import aggregate as _fedavg_aggregate


def aggregate(updates: List[Dict[str, torch.Tensor]],
              weights: Optional[List[float]] = None,
              threshold: float = 0.5,
              **params) -> Dict[str, torch.Tensor]:
    """
    RFLBA：余弦相似度过滤 + 质心估计

    1) 以全体更新的坐标中值为参考方向，剔除与参考方向余弦相似度
       低于 threshold 的更新（复用 anomaly_detection.cosine_similarity_filter）；
    2) 对幸存更新取（加权）均值作为质心估计输出。

    Args:
        updates: 客户端更新列表
        weights: 客户端权重（过滤后用于质心加权平均）
        threshold: 余弦相似度阈值
    """
    n = len(updates)
    if weights is None:
        weights = [1.0] * n
    if n == 0:
        raise ValueError("RFLBA: 没有可聚合的更新")
    if n == 1:
        return dict(updates[0])

    filtered_updates, filtered_weights, _ = cosine_similarity_filter(
        updates, weights, reference=None, threshold=threshold)
    return _fedavg_aggregate(filtered_updates, filtered_weights)
