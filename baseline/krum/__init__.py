"""Krum 聚合 (Blanchard et al. NDSS'17)

说明：本包实现单 Krum；多选变体 Multi-Krum 见 baseline/multi_krum/
（同一论文，选择 m 个最可信更新做 FedAvg，更稳定）。
"""

from .rule import aggregate
