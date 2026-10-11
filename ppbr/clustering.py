"""
Trimmed k-means 聚类 —— PPBR 算法 2 第 c 步
============================================
边缘对收到的 CLDP 带噪符号元组执行 trimmed k-means [Dorabiala 2021]，
取尺寸最大的前 b 个簇中的客户端为可信集 U_i^(T)（论文默认 k=2, b=1）。

trimmed k-means = Lloyd k-means + 每轮剔除距质心最远的 trim_ratio 比例点，
使聚类对离群（恶意）元组鲁棒。
"""

from typing import Tuple

import numpy as np


def trimmed_kmeans(points: np.ndarray, k: int = 2, trim_ratio: float = 0.1,
                   rng: np.random.RandomState = None,
                   max_iter: int = 50) -> Tuple[np.ndarray, np.ndarray]:
    """
    Returns:
        labels:  每个点的簇标签 (n,)
        keep:    未被剔除的布尔掩码 (n,)（剔除的是距质心最远的 trim_ratio 比例点）
    """
    rng = rng or np.random.RandomState(0)
    points = np.asarray(points, dtype=np.float64)
    n = len(points)
    if n <= k:
        return np.zeros(n, dtype=int), np.ones(n, dtype=bool)

    n_keep = n - int(round(n * trim_ratio))
    n_keep = max(n_keep, k)
    n_keep = min(n_keep, n)

    # k-means++ 初始化
    centroids = [points[rng.randint(n)]]
    for _ in range(k - 1):
        d = np.min(((points[:, None, :] - np.array(centroids)[None]) ** 2).sum(-1), axis=1)
        total = d.sum()
        if total <= 0:  # 所有点重合
            centroids.append(points[rng.randint(n)])
        else:
            centroids.append(points[rng.choice(n, p=d / total)])
    centroids = np.array(centroids)

    labels = np.zeros(n, dtype=int)
    keep = np.ones(n, dtype=bool)
    for _ in range(max_iter):
        dist = ((points[:, None, :] - centroids[None]) ** 2).sum(-1)
        labels = dist.argmin(axis=1)
        dmin = dist.min(axis=1)
        keep = np.zeros(n, dtype=bool)
        keep[np.argsort(dmin)[:n_keep]] = True

        new_centroids = centroids.copy()
        for j in range(k):
            sel = keep & (labels == j)
            if sel.any():
                new_centroids[j] = points[sel].mean(axis=0)
        if np.allclose(new_centroids, centroids, atol=1e-8):
            break
        centroids = new_centroids

    # 最终一轮：用收敛质心重新分配
    dist = ((points[:, None, :] - centroids[None]) ** 2).sum(-1)
    labels = dist.argmin(axis=1)
    dmin = dist.min(axis=1)
    keep = np.zeros(n, dtype=bool)
    keep[np.argsort(dmin)[:n_keep]] = True
    return labels, keep


def select_trusted_clients(points: np.ndarray, client_ids, k: int = 2, b: int = 1,
                           trim_ratio: float = 0.1,
                           rng: np.random.RandomState = None) -> set:
    """
    算法 2 第 c 步：trimmed k-means → 取尺寸最大的前 b 个簇 → 可信客户端集。

    Args:
        points:     (n, 3) CLDP 带噪符号元组
        client_ids: 与 points 行对齐的客户端编号
        k, b:       簇数 / 取前 b 个大簇（论文默认 2 / 1）
        trim_ratio: trimmed k-means 剔除比例
    """
    labels, keep = trimmed_kmeans(points, k=k, trim_ratio=trim_ratio, rng=rng)
    sizes = np.bincount(labels[keep], minlength=k)
    top_clusters = set(np.argsort(sizes)[::-1][:b].tolist())
    trusted = set()
    for idx, cid in enumerate(client_ids):
        if keep[idx] and labels[idx] in top_clusters:
            trusted.add(cid)
    return trusted
