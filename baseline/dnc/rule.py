"""
DnC (Detect and Clean, 数据与计算筛选) 聚合

出处: V. Shejwalkar, A. Houmansadr, "Manipulating the Byzantine:
      Optimizing Model Poisoning Attacks and Defenses for Federated
      Learning", USENIX Security 2021.
      （亦见 Su & Xu 2019 的 Data&Computation Sieving 思路）
"""

import numpy as np
import torch
from typing import Dict, List, Optional

from baseline.fedavg import aggregate as _fedavg_aggregate
from baseline.trimmed_mean import aggregate as _trimmed_mean_aggregate


def aggregate(updates: List[Dict[str, torch.Tensor]],
              weights: Optional[List[float]] = None,
              num_byzantine: Optional[int] = None,
              sub_dim: Optional[int] = None,
              num_iter: int = 1,
              trim_ratio: float = 0.1,
              seed: int = 42,
              **params) -> Dict[str, torch.Tensor]:
    """
    DnC (Detect and Clean)

    流程（单轮迭代）：
      1) 随机投影：把更新随机采样到 sub_dim 个坐标；
      2) 谱检测：中心化的投影矩阵的 top 奇异向量，剔除投影得分最高的
         num_byzantine 个离群更新；
      3) 聚类：对幸存更新做 k-means (k=2)，取直径较小的簇；
      4) 对选中簇做 trimmed mean 聚合。

    Args:
        updates: 客户端更新列表
        weights: 客户端权重（本规则不使用）
        num_byzantine: 估计的恶意客户端数量（None 时按 trim_ratio 推算）
        sub_dim: 随机投影维度（None=不降维）
        num_iter: 谱检测迭代轮数
        trim_ratio: 聚类后 trimmed mean 的截断比例
        seed: 随机投影/聚类种子
    """
    n = len(updates)
    if n == 0:
        raise ValueError("DnC: 没有可聚合的更新")
    if n <= 2:
        return _fedavg_aggregate(updates, [1.0] * n)

    rng = np.random.RandomState(seed)
    keys = sorted(updates[0].keys())
    flat = [torch.cat([u[k].reshape(-1) for k in keys]) for u in updates]

    survivors = list(range(n))
    for _ in range(max(1, num_iter)):
        dim = flat[0].numel()
        d = min(sub_dim or dim, dim)
        cols = rng.choice(dim, size=d, replace=False)
        P = torch.stack([f[cols] for f in flat])          # (n, d)

        # 谱检测：中心化 + 幂迭代求 top 右奇异向量
        mu = P.mean(0)
        C = P - mu
        v = rng.normal(size=d).astype(np.float32)
        v = torch.tensor(v / np.linalg.norm(v))
        for _ in range(20):
            v = C.T @ (C @ v)
            nv = torch.norm(v)
            if nv < 1e-12:
                break
            v = v / nv
        scores = (C @ v) ** 2                             # 离群得分
        order = torch.argsort(scores, descending=True)

        b = num_byzantine if num_byzantine is not None else max(1, int(round(trim_ratio * n)))
        removed = set(order[:min(b, len(survivors) - 1)].tolist())
        survivors = [i for i in survivors if i not in removed]

    # 聚类：k-means (k=2) 取直径较小的簇
    S = torch.stack([flat[i] for i in survivors])
    if len(survivors) <= 2:
        chosen = survivors
    else:
        c0 = S[rng.randint(len(S))]
        d2 = ((S - c0) ** 2).sum(1)
        c1 = S[int(torch.argmax(d2))]
        for _ in range(20):
            dist = torch.stack([((S - c0) ** 2).sum(1), ((S - c1) ** 2).sum(1)])
            lab = dist.argmin(0)
            new_c0 = S[lab == 0].mean(0) if (lab == 0).any() else c0
            new_c1 = S[lab == 1].mean(0) if (lab == 1).any() else c1
            if torch.allclose(new_c0, c0, atol=1e-7) and torch.allclose(new_c1, c1, atol=1e-7):
                break
            c0, c1 = new_c0, new_c1
        members = {0: S[lab == 0], 1: S[lab == 1]}
        def diameter(m):
            return float(((m[:, None, :] - m[None, :, :]) ** 2).sum(-1).max()) if len(m) > 1 else 0.0
        chosen_idx = 0 if diameter(members[0]) <= diameter(members[1]) else 1
        chosen = [survivors[i] for i in torch.nonzero(lab == chosen_idx).flatten().tolist()]

    # 选中簇 trimmed mean
    t = max(0, min(int(round(trim_ratio * len(chosen))), len(chosen) // 2))
    ratio = t / len(chosen) if chosen else 0.0
    agg = _trimmed_mean_aggregate([updates[i] for i in chosen], trim_ratio=ratio)
    return agg
