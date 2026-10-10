"""
异常检测与过滤机制 (Anomaly Detection)
在聚合前检测并剔除异常客户端更新
"""

import torch
from typing import Dict, List, Tuple
import copy


def _flatten_update(update: Dict[str, torch.Tensor]) -> torch.Tensor:
    """将模型更新展平为一维向量"""
    return torch.cat([v.flatten() for v in update.values()])


def _compute_norm(update: Dict[str, torch.Tensor]) -> float:
    """计算模型更新的L2范数"""
    flat = _flatten_update(update)
    return torch.norm(flat, p=2).item()


def _compute_cosine_similarity(u1: Dict[str, torch.Tensor],
                                u2: Dict[str, torch.Tensor]) -> float:
    """计算两个模型更新之间的余弦相似度"""
    v1 = _flatten_update(u1)
    v2 = _flatten_update(u2)
    dot = torch.dot(v1, v2).item()
    norm1 = torch.norm(v1, p=2).item()
    norm2 = torch.norm(v2, p=2).item()
    if norm1 < 1e-10 or norm2 < 1e-10:
        return 0.0
    return dot / (norm1 * norm2)


def norm_clip_updates(updates: List[Dict[str, torch.Tensor]],
                      weights: List[float],
                      global_update: Dict[str, torch.Tensor] = None,
                      max_norm_ratio: float = 3.0) -> Tuple[List[Dict], List[float], List[int]]:
    """
    基于范数裁剪的过滤 (Norm-based Clipping)
    
    如果某个客户端更新的范数超过中位数范数的 max_norm_ratio 倍，
    则将其裁剪到该阈值或直接剔除。
    
    Args:
        updates: 客户端更新列表
        weights: 客户端权重列表
        global_update: 上一轮全局更新（可选，用于参考）
        max_norm_ratio: 范数倍数阈值
    
    Returns:
        filtered_updates, filtered_weights, kept_indices
    """
    norms = [_compute_norm(u) for u in updates]
    median_norm = float(torch.median(torch.tensor(norms)))
    threshold = median_norm * max_norm_ratio
    
    filtered_updates = []
    filtered_weights = []
    kept_indices = []
    
    for i, (update, weight, norm) in enumerate(zip(updates, weights, norms)):
        if norm <= threshold:
            filtered_updates.append(update)
            filtered_weights.append(weight)
            kept_indices.append(i)
    
    if len(filtered_updates) == 0:
        # 如果全部都被剔除了，回退到中位数对应的更新
        median_idx = int(torch.argmin(torch.abs(torch.tensor(norms) - median_norm)))
        filtered_updates = [updates[median_idx]]
        filtered_weights = [weights[median_idx]]
        kept_indices = [median_idx]
    
    return filtered_updates, filtered_weights, kept_indices


def cosine_similarity_filter(updates: List[Dict[str, torch.Tensor]],
                             weights: List[float],
                             reference: Dict[str, torch.Tensor] = None,
                             threshold: float = 0.5) -> Tuple[List[Dict], List[float], List[int]]:
    """
    余弦相似度过滤 (Cosine Similarity Filtering)
    
    计算每个客户端更新与参考更新（如上一轮全局更新或所有更新的中位数）
    之间的余弦相似度，剔除相似度低于阈值的异常更新。
    
    Args:
        updates: 客户端更新列表
        weights: 客户端权重列表
        reference: 参考更新（默认使用所有更新的中位数）
        threshold: 余弦相似度阈值
    
    Returns:
        filtered_updates, filtered_weights, kept_indices
    """
    if reference is None:
        # 使用中位数更新作为参考
        from .aggregation_rules import median_aggregate
        reference = median_aggregate(updates)
    
    similarities = [_compute_cosine_similarity(u, reference) for u in updates]
    
    filtered_updates = []
    filtered_weights = []
    kept_indices = []
    
    for i, (update, weight, sim) in enumerate(zip(updates, weights, similarities)):
        if sim >= threshold:
            filtered_updates.append(update)
            filtered_weights.append(weight)
            kept_indices.append(i)
    
    if len(filtered_updates) == 0:
        # 全部剔除则保留相似度最高的一个
        best_idx = int(torch.argmax(torch.tensor(similarities)))
        filtered_updates = [updates[best_idx]]
        filtered_weights = [weights[best_idx]]
        kept_indices = [best_idx]
    
    return filtered_updates, filtered_weights, kept_indices


def median_absolute_deviation_filter(updates: List[Dict[str, torch.Tensor]],
                                     weights: List[float],
                                     threshold: float = 3.0) -> Tuple[List[Dict], List[float], List[int]]:
    """
    基于中位数绝对偏差 (MAD) 的异常检测
    
    计算每个更新与所有更新中位数的偏离程度，
    使用 MAD 作为稳健的标准差估计，剔除偏离过大的更新。
    
    Args:
        updates: 客户端更新列表
        weights: 客户端权重列表
        threshold: MAD倍数阈值（通常取 2.5 或 3.0）
    """
    # 计算每个更新与中位数更新的逐元素距离
    from .aggregation_rules import median_aggregate
    median_update = median_aggregate(updates)
    
    distances = []
    for u in updates:
        dist = 0.0
        for name in u.keys():
            dist += torch.sum((u[name] - median_update[name]) ** 2).item()
        distances.append(dist ** 0.5)
    
    distances_tensor = torch.tensor(distances)
    median_dist = torch.median(distances_tensor).item()
    
    # 计算 MAD
    abs_deviations = torch.abs(distances_tensor - median_dist)
    mad = torch.median(abs_deviations).item()
    
    # 如果 mad 为0，说明有一半更新完全重合，放宽条件
    if mad < 1e-10:
        mad = 1e-10
    
    # Z-score based on MAD
    modified_z_scores = 0.6745 * (distances_tensor - median_dist) / mad
    
    filtered_updates = []
    filtered_weights = []
    kept_indices = []
    
    for i, (update, weight, z) in enumerate(zip(updates, weights, modified_z_scores)):
        if z.item() <= threshold:
            filtered_updates.append(update)
            filtered_weights.append(weight)
            kept_indices.append(i)
    
    if len(filtered_updates) == 0:
        best_idx = int(torch.argmin(modified_z_scores))
        filtered_updates = [updates[best_idx]]
        filtered_weights = [weights[best_idx]]
        kept_indices = [best_idx]
    
    return filtered_updates, filtered_weights, kept_indices


def multi_criteria_filter(updates: List[Dict[str, torch.Tensor]],
                          weights: List[float],
                          norm_ratio: float = 5.0,
                          cos_threshold: float = -0.1) -> Tuple[List[Dict], List[float], List[int]]:
    """
    多标准联合过滤
    
    同时应用范数检测和余弦相似度检测，只有同时通过两个检测的更新才会被保留。
    这是一个更保守的策略，能有效过滤明显异常的更新。
    """
    # 第一步：范数裁剪
    fu, fw, ki = norm_clip_updates(updates, weights, max_norm_ratio=norm_ratio)
    
    # 第二步：余弦相似度过滤（在第一步结果上）
    if len(fu) > 1:
        from .aggregation_rules import median_aggregate
        ref = median_aggregate(fu)
        fu, fw, ki2 = cosine_similarity_filter(fu, fw, reference=ref, threshold=cos_threshold)
        ki = [ki[i] for i in ki2]
    
    return fu, fw, ki
