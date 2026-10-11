"""
鲁棒聚合规则 (Robust Aggregation Rules)
提供多种替代 FedAvg 的聚合方法，用于抵御拜占庭攻击
"""

import torch
from typing import Dict, List, Optional, Tuple
import copy

import numpy as np


def fedavg_aggregate(updates: List[Dict[str, torch.Tensor]],
                     weights: List[float]) -> Dict[str, torch.Tensor]:
    """
    标准 FedAvg 聚合（基线）
    """
    total_weight = sum(weights)
    normalized = [w / total_weight for w in weights]
    
    aggregated = {}
    for name in updates[0].keys():
        weighted_sum = None
        for i, update in enumerate(updates):
            weighted = update[name] * normalized[i]
            if weighted_sum is None:
                weighted_sum = weighted
            else:
                weighted_sum += weighted
        aggregated[name] = weighted_sum
    return aggregated


def median_aggregate(updates: List[Dict[str, torch.Tensor]]) -> Dict[str, torch.Tensor]:
    """
    坐标中值聚合 (Coordinate-wise Median)
    
    对每个参数坐标的所有客户端取值取中值，
    不依赖客户端权重，对异常值有很强的鲁棒性。
    
    参考: Y. Chen et al., "Distributed statistical machine learning",
          in adversarial settings: Byzantine gradient descent. POMACS 2017.
    """
    aggregated = {}
    for name in updates[0].keys():
        # stack all tensors: [num_clients, *param_shape]
        stacked = torch.stack([u[name] for u in updates], dim=0)
        aggregated[name] = torch.median(stacked, dim=0).values
    return aggregated


def trimmed_mean_aggregate(updates: List[Dict[str, torch.Tensor]],
                           trim_ratio: float = 0.1) -> Dict[str, torch.Tensor]:
    """
    截断均值聚合 (Trimmed Mean / TrMean)
    
    对每个坐标，去掉最高和最低的 trim_ratio 比例的值，然后取平均。
    需要恶意客户端比例小于 trim_ratio。
    
    Args:
        updates: 客户端更新列表
        trim_ratio: 每端截断比例 (0.0 ~ 0.5)
    """
    n = len(updates)
    n_trim = int(n * trim_ratio)
    
    aggregated = {}
    for name in updates[0].keys():
        stacked = torch.stack([u[name] for u in updates], dim=0)
        # 按最后一个维度排序（这里需要按元素逐个坐标排序）
        # 使用 flatten 后处理再 reshape
        original_shape = stacked.shape[1:]
        flat = stacked.view(n, -1)  # [n, num_elements]
        sorted_vals, _ = torch.sort(flat, dim=0)
        trimmed = sorted_vals[n_trim:n - n_trim]
        mean_trimmed = trimmed.mean(dim=0)
        aggregated[name] = mean_trimmed.view(original_shape)
    return aggregated


def _pairwise_distance(updates: List[Dict[str, torch.Tensor]]) -> torch.Tensor:
    """计算所有客户端更新两两之间的欧氏距离平方"""
    n = len(updates)
    # 将每个更新展平为向量
    flattened = []
    for u in updates:
        vec = torch.cat([v.flatten() for v in u.values()])
        flattened.append(vec)
    
    # 计算两两距离矩阵
    dists = torch.zeros(n, n)
    for i in range(n):
        for j in range(i + 1, n):
            d = torch.sum((flattened[i] - flattened[j]) ** 2).item()
            dists[i, j] = d
            dists[j, i] = d
    return dists


def krum_aggregate(updates: List[Dict[str, torch.Tensor]],
                   f: int = None) -> Dict[str, torch.Tensor]:
    """
    Krum 聚合
    
    选择与其他更新最相似的那个更新作为聚合结果。
    假设有 f 个恶意客户端，选择与其 n - f - 2 个最近邻距离之和最小的更新。
    
    Args:
        updates: 客户端更新列表
        f: 估计的恶意客户端数量（默认 n // 2 - 1）
    
    参考: P. Blanchard et al., "Machine learning with adversaries: 
          Byzantine tolerant gradient descent", NeurIPS 2017.
    """
    n = len(updates)
    if f is None:
        f = max(0, (n - 3) // 2)
    
    assert n > 2 * f + 2, f"Krum requires n > 2f + 2, got n={n}, f={f}"
    
    dists = _pairwise_distance(updates)
    scores = []
    for i in range(n):
        # 排序距离，取最近的 n - f - 2 个（不包括自己）
        sorted_dists = torch.sort(dists[i])[0]
        # sorted_dists[0] 是自己到自己的距离0
        score = torch.sum(sorted_dists[1:n - f]).item()
        scores.append(score)
    
    best_idx = int(torch.argmin(torch.tensor(scores)))
    return copy.deepcopy(updates[best_idx])


def multi_krum_aggregate(updates: List[Dict[str, torch.Tensor]],
                         f: int = None,
                         m: int = None) -> Dict[str, torch.Tensor]:
    """
    Multi-Krum 聚合
    
    选择 m 个最可信的更新，然后用 FedAvg 聚合它们。
    比 Krum 更稳定，保留了更多客户端的信息。
    
    Args:
        updates: 客户端更新列表
        f: 估计的恶意客户端数量
        m: 选择的可信客户端数量（默认 n - f）
    """
    n = len(updates)
    if f is None:
        f = max(0, (n - 3) // 2)
    if m is None:
        m = max(1, n - f)
    
    assert n > 2 * f + 2, f"Multi-Krum requires n > 2f + 2, got n={n}, f={f}"
    
    dists = _pairwise_distance(updates)
    scores = []
    for i in range(n):
        sorted_dists = torch.sort(dists[i])[0]
        score = torch.sum(sorted_dists[1:n - f]).item()
        scores.append(score)
    
    # 选择得分最低的 m 个
    scores_tensor = torch.tensor(scores)
    _, selected_indices = torch.topk(scores_tensor, m, largest=False)
    selected_indices = selected_indices.tolist()
    
    selected_updates = [updates[i] for i in selected_indices]
    selected_weights = [1.0] * m
    return fedavg_aggregate(selected_updates, selected_weights)


def geometric_median_aggregate(updates: List[Dict[str, torch.Tensor]],
                                max_iter: int = 10,
                                tol: float = 1e-5) -> Dict[str, torch.Tensor]:
    """
    几何中位数聚合 (Geometric Median)
    
    寻找使得到所有更新点距离之和最小的那个点。
    使用 Weiszfeld 算法迭代求解。
    """
    # 初始化为中位数
    current = median_aggregate(updates)
    
    for _ in range(max_iter):
        prev = {k: v.clone() for k, v in current.items()}
        
        numerator = {k: torch.zeros_like(v) for k, v in current.items()}
        denominator = 0.0
        
        for u in updates:
            dist = 0.0
            for name in current.keys():
                dist += torch.sum((current[name] - u[name]) ** 2).item()
            dist = max(dist ** 0.5, 1e-10)
            
            w = 1.0 / dist
            for name in current.keys():
                numerator[name] += w * u[name]
            denominator += w
        
        for name in current.keys():
            current[name] = numerator[name] / denominator
        
        # 检查收敛
        diff = 0.0
        for name in current.keys():
            diff += torch.sum((current[name] - prev[name]) ** 2).item()
        if diff ** 0.5 < tol:
            break

    return current


# =====================================================================
# PPBR 论文对比基线（He 等, IEEE TMC 2026）：Median、TrimmedMean、GeoMed、
# Multi-Krum、DnC、CenteredClipping —— 已全部实现于 ckks_fed/defenses/，
# 但只能在明文聚合路径执行；CKKS 密文聚合路径下不生效（见 README 已知限制）。
# =====================================================================

def dnc_aggregate(updates: List[Dict[str, torch.Tensor]],
                  num_byzantine: Optional[int] = None,
                  sub_dim: Optional[int] = None,
                  num_iter: int = 1,
                  trim_ratio: float = 0.1,
                  seed: int = 42) -> Dict[str, torch.Tensor]:
    """
    DnC (Detect and Clean, Shejwalkar & Houmansadr, USENIX Security 2021)
    —— PPBR 论文对比基线之一。

    流程（单轮迭代）：
      1) 随机投影：把更新随机采样到 sub_dim 个坐标；
      2) 谱检测：中心化的投影矩阵的 top 奇异向量，剔除投影得分最高的
         num_byzantine 个离群更新；
      3) 聚类：对幸存更新做 k-means (k=2)，取直径较小的簇；
      4) 对选中簇做 trimmed mean 聚合。
    """
    n = len(updates)
    if n == 0:
        raise ValueError("DnC: 没有可聚合的更新")
    if n <= 2:
        return fedavg_aggregate(updates, [1.0] * n)

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
    agg = trimmed_mean_aggregate([updates[i] for i in chosen], trim_ratio=ratio)
    return agg


def centered_clipping_aggregate(updates: List[Dict[str, torch.Tensor]],
                                num_iter: int = 3,
                                tau_factor: float = 1.3) -> Dict[str, torch.Tensor]:
    """
    Centered Clipping (Karimireddy et al., ICML 2021)
    —— PPBR 论文对比基线之一。

    迭代围绕动量中心裁剪：
        v ← v + (1/n) Σ_i clip_τ(u_i − v)，  clip_τ(x) = x·min(1, τ/‖x‖)
        τ = tau_factor · max_i ‖u_i‖
    对合谋攻击（ALIE/IPM）的鲁棒性显著优于朴素 clipping。
    """
    n = len(updates)
    if n == 0:
        raise ValueError("CenteredClipping: 没有可聚合的更新")

    keys = sorted(updates[0].keys())
    norms = [float(np.sqrt(sum(u[k].double().pow(2).sum().item() for k in keys)))
             for u in updates]
    tau = tau_factor * max(norms)

    v = {k: torch.zeros_like(updates[0][k], dtype=torch.float64) for k in keys}
    for _ in range(max(1, num_iter)):
        delta = {k: torch.zeros_like(v[k]) for k in keys}
        for u in updates:
            r = {k: u[k].double() - v[k] for k in keys}
            rnorm = float(np.sqrt(sum(x.pow(2).sum().item() for x in r.values())))
            c = min(1.0, tau / max(rnorm, 1e-12))
            for k in keys:
                delta[k] += c * r[k]
        for k in keys:
            v[k] += delta[k] / n

    return {k: v[k].to(updates[0][k].dtype) for k in keys}
