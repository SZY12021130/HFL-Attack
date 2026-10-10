"""
联邦学习防御模块
提供多种鲁棒聚合规则和异常检测机制
"""

from .aggregation_rules import (
    fedavg_aggregate,
    median_aggregate,
    trimmed_mean_aggregate,
    krum_aggregate,
    multi_krum_aggregate,
)

from .anomaly_detection import (
    norm_clip_updates,
    cosine_similarity_filter,
    median_absolute_deviation_filter,
)

__all__ = [
    # 聚合规则
    "fedavg_aggregate",
    "median_aggregate",
    "trimmed_mean_aggregate",
    "krum_aggregate",
    "multi_krum_aggregate",
    # 异常检测
    "norm_clip_updates",
    "cosine_similarity_filter",
    "median_absolute_deviation_filter",
]
