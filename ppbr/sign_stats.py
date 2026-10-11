"""
符号统计元组 —— PPBR 算法 2 第 a 步
====================================
云每轮选出 ι 个随机坐标 G 广播；客户端只在这 ι 个坐标上统计本地更新梯度的
符号比例，生成 3 维符号统计元组（引用 Xu ICDCS'22 的符号统计思想）：

    s = (正号比例, 负号比例, 零号比例) ∈ R³

良性客户端梯度方向相似 → 元组相近；恶意客户端方向发散 → 元组偏离，供边缘聚类。
"""

from typing import Dict

import numpy as np
import torch


def choose_coordinates(total_params: int, num_coords: int,
                       seed: int, round_num: int) -> np.ndarray:
    """云广播的坐标子集 G（按轮次变化，种子可复现）"""
    rng = np.random.RandomState(seed + 7919 * round_num)
    size = min(num_coords, total_params)
    return rng.choice(total_params, size=size, replace=False)


def sign_tuple(update: Dict[str, torch.Tensor], coord_idx: np.ndarray) -> np.ndarray:
    """
    客户端本地计算符号统计元组 s ∈ R³（正/负/零号比例）。

    Args:
        update: 明文模型更新 {参数名: 张量}（加密上传之前，客户端本地可见）
        coord_idx: 云广播的坐标子集 G（全局扁平坐标索引）
    """
    vec = torch.cat([update[k].detach().reshape(-1)
                     for k in sorted(update)]).cpu().numpy()
    vals = vec[coord_idx]
    pos = float((vals > 0).mean())
    neg = float((vals < 0).mean())
    return np.array([pos, neg, 1.0 - pos - neg], dtype=np.float64)
