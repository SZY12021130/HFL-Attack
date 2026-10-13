"""
鲁棒聚合规则 (Robust Aggregation Rules)
提供多种替代 FedAvg 的聚合方法，用于抵御拜占庭攻击

⚠️ 实现已迁移至 baseline/ 包（明文鲁棒聚合基线），本模块仅做 re-export
以保持向后兼容（ckks_fed.defenses.*_aggregate 的导入路径与行为不变），
不要在此新增实现。
"""

import torch  # noqa: F401  (保持原模块导出面对 torch 的隐式可用性)
from typing import Dict, List, Optional, Tuple  # noqa: F401
import copy  # noqa: F401
import numpy as np  # noqa: F401

from baseline.fedavg import aggregate as fedavg_aggregate
from baseline.median import aggregate as median_aggregate
from baseline.trimmed_mean import aggregate as trimmed_mean_aggregate
from baseline.krum import aggregate as krum_aggregate
from baseline.krum.rule import _pairwise_distance
from baseline.multi_krum import aggregate as multi_krum_aggregate
from baseline.geometric_median import aggregate as geometric_median_aggregate
from baseline.dnc import aggregate as dnc_aggregate
from baseline.centered_clipping import aggregate as centered_clipping_aggregate

__all__ = [
    "fedavg_aggregate",
    "median_aggregate",
    "trimmed_mean_aggregate",
    "krum_aggregate",
    "_pairwise_distance",
    "multi_krum_aggregate",
    "geometric_median_aggregate",
    "dnc_aggregate",
    "centered_clipping_aggregate",
]
