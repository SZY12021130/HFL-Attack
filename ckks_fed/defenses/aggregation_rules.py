"""
鲁棒聚合规则 (Robust Aggregation Rules)
提供多种替代 FedAvg 的聚合方法，用于抵御拜占庭攻击
"""

import torch
from typing import Dict, List, Tuple
import copy


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
