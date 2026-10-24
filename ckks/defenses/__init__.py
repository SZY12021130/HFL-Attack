"""
联邦学习防御模块（CKKS 轨道）
提供多种鲁棒聚合规则（实现见 baseline/ 注册表，本包为兼容 re-export）。
异常检测已上移至 base.anomaly（通用层，供各方案复用）。
"""

from .aggregation_rules import (
    fedavg_aggregate,
    median_aggregate,
    trimmed_mean_aggregate,
    krum_aggregate,
    multi_krum_aggregate,
    geometric_median_aggregate,
    dnc_aggregate,
    centered_clipping_aggregate,
)


__all__ = [
    # 聚合规则
    "fedavg_aggregate",
    "median_aggregate",
    "trimmed_mean_aggregate",
    "krum_aggregate",
    "multi_krum_aggregate",
    "geometric_median_aggregate",
    "dnc_aggregate",
    "centered_clipping_aggregate",
]
