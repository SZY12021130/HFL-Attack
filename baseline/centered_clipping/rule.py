"""
Centered Clipping 聚合

出处: S. P. Karimireddy, L. He, M. Jaggi, "Learning from History for
      Byzantine Robust Optimization", ICML 2021.
"""

import numpy as np
import torch
from typing import Dict, List, Optional


def aggregate(updates: List[Dict[str, torch.Tensor]],
              weights: Optional[List[float]] = None,
              num_iter: int = 3,
              tau_factor: float = 1.3,
              **params) -> Dict[str, torch.Tensor]:
    """
    Centered Clipping (Karimireddy et al., ICML 2021)

    迭代围绕动量中心裁剪：
        v ← v + (1/n) Σ_i clip_τ(u_i − v)，  clip_τ(x) = x·min(1, τ/‖x‖)
        τ = tau_factor · max_i ‖u_i‖
    对合谋攻击（ALIE/IPM）的鲁棒性显著优于朴素 clipping。

    Args:
        updates: 客户端更新列表
        weights: 客户端权重（本规则不使用，等权迭代）
        num_iter: 围绕中心的迭代轮数
        tau_factor: 裁剪阈值相对最大范数的倍数
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
