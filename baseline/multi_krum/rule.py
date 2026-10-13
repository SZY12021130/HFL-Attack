"""
Multi-Krum 聚合

出处: P. Blanchard, E. M. El Mhamdi, R. Guerraoui, J. Stainer,
      "Machine Learning with Adversaries: Byzantine Tolerant Gradient
      Descent", NDSS 2017.（Krum 的多选变体）
"""

import torch
from typing import Dict, List, Optional

from baseline.fedavg import aggregate as _fedavg_aggregate
from baseline.krum.rule import _pairwise_distance


def aggregate(updates: List[Dict[str, torch.Tensor]],
              weights: Optional[List[float]] = None,
              f: int = None,
              m: int = None,
              **params) -> Dict[str, torch.Tensor]:
    """
    Multi-Krum 聚合

    选择 m 个最可信的更新，然后用 FedAvg 聚合它们。
    比 Krum 更稳定，保留了更多客户端的信息。

    Args:
        updates: 客户端更新列表
        weights: 客户端权重（本规则不使用，选中客户端等权 FedAvg）
        f: 估计的恶意客户端数量
        m: 选择的可信客户端数量（默认 n − f）

    威胁模型前提: 需要 n > 2f + 2，且 m 大于实际恶意数才有剔除效果。
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
    return _fedavg_aggregate(selected_updates, selected_weights)
