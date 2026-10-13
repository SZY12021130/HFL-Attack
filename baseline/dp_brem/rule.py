"""
DP-BREM：服务器动量 + 高斯 DP 噪声的鲁棒聚合

出处: E. M. El Mhamdi, R. Guerraoui, S. Rouault, "The Hidden Vulnerability
      of Distributed Learning in Byzantium", ICML 2018.
      （BR moment / 动量聚合 + 差分隐私噪声释放的思路）
"""

import torch
from typing import Dict, List, Optional


def _l2_norm(update: Dict[str, torch.Tensor]) -> float:
    return float(sum(v.pow(2).sum().item() for v in update.values()) ** 0.5)


def _clip_update(update: Dict[str, torch.Tensor],
                 center: Dict[str, torch.Tensor],
                 tau: float) -> Dict[str, torch.Tensor]:
    """clip_τ(u − v)：相对中心裁剪到 L2 阈值 τ 内"""
    diff = {k: u - center[k] for k, u in update.items()}
    dnorm = _l2_norm(diff)
    c = min(1.0, tau / max(dnorm, 1e-12))
    return {k: c * d for k, d in diff.items()}


def _br_mean(updates: List[Dict[str, torch.Tensor]],
             tau: float,
             num_iter: int = 3) -> Dict[str, torch.Tensor]:
    """BR moment（El Mhamdi et al. 2018）：迭代 v ← v + (1/n)Σ_i clip_τ(u_i − v)

    对单轮客户端更新做拜占庭鲁棒的裁剪均值，作为动量的输入 g_agg。
    """
    n = len(updates)
    v = {k: torch.zeros_like(u) for k, u in updates[0].items()}
    for _ in range(max(1, num_iter)):
        delta = {k: torch.zeros_like(x) for k, x in v.items()}
        for u in updates:
            clipped = _clip_update(u, v, tau)
            for k in v.keys():
                delta[k] += clipped[k]
        for k in v.keys():
            v[k] += delta[k] / n
    return v


def aggregate(updates: List[Dict[str, torch.Tensor]],
              weights: Optional[List[float]] = None,
              state: Optional[dict] = None,
              phi: float = 0.9,
              sigma: float = 0.01,
              clip_C: Optional[float] = None,
              num_iter: int = 3,
              seed: Optional[int] = None,
              **params) -> Dict[str, torch.Tensor]:
    """
    DP-BREM：服务器端动量 + 高斯 DP 噪声

    每轮流程：
      1) g_agg = BR moment 裁剪均值（迭代 v ← v + (1/n)Σ_i clip_C(u_i − v)，
         即 El Mhamdi et al. 2018 的 Byzantine-Robust moment；clip_C=None 时
         取客户端范数的中位数作为裁剪阈值）；
      2) 服务器动量更新：m ← φ·m + (1 − φ)·g_agg（m 为跨轮状态，
         存于 state["momentum"]，首次调用从零向量初始化）；
      3) 裁剪：m ← m · min(1, C/‖m‖)，C = clip_C；
      4) 加噪释放：m̃ = m + N(0, σ²C²·I)（相邻轮级 DP 保护），返回 m̃。

    ⚠️ 与 baseline/ 中纯规则型基线的差异：本规则是**有状态**的
       （动量 m 跨轮累积），必须通过 state 参数传入同一个 dict
       （服务器为每条规则维护一份，见 AggregationServer._rule_state）；
       state=None 时退化为无状态的单轮动量（仍可用，但跨轮语义丢失）。

    Args:
        updates: 客户端更新列表
        weights: 客户端权重（本规则不使用，BR moment 等权）
        state: 跨轮状态字典（{"momentum": Dict[str, Tensor]}），会被原地更新
        phi: 动量系数 φ ∈ [0, 1)
        sigma: 高斯噪声乘子 σ（噪声标准差 = σ·C）
        clip_C: L2 裁剪阈值 C（None = 取客户端范数的中位数）
        num_iter: BR moment 迭代轮数
        seed: 噪声随机种子（None = 不可复现）
    """
    n = len(updates)
    if n == 0:
        raise ValueError("DP-BREM: 没有可聚合的更新")

    # 裁剪阈值：默认取客户端范数的中位数（对 <50% 恶意稳健）
    if clip_C is None:
        norms = sorted(_l2_norm(u) for u in updates)
        clip_C = max(norms[len(norms) // 2], 1e-12)

    # 1) BR moment 裁剪均值
    g_agg = _br_mean(updates, tau=clip_C, num_iter=num_iter)

    keys = list(g_agg.keys())
    if state is None:
        state = {}
    if "momentum" not in state:
        state["momentum"] = {k: torch.zeros_like(v) for k, v in g_agg.items()}
    m = state["momentum"]

    # 2) 动量更新：m ← φ·m + (1−φ)·g_agg
    with torch.no_grad():
        for k in keys:
            m[k].mul_(phi).add_(g_agg[k], alpha=1.0 - phi)

    # 3) 裁剪：m ← m · min(1, C/‖m‖)
    m_norm = _l2_norm(m)
    scale = min(1.0, clip_C / max(m_norm, 1e-12))
    if scale < 1.0:
        for k in keys:
            m[k].mul_(scale)

    # 4) 加噪释放：m̃ = m + N(0, σ²C²·I)
    gen = torch.Generator(device=g_agg[keys[0]].device)
    if seed is not None:
        gen.manual_seed(seed)
    released = {}
    for k in keys:
        noise = torch.randn(m[k].shape, generator=gen, device=m[k].device,
                            dtype=m[k].dtype) * (sigma * clip_C)
        released[k] = m[k] + noise

    return released
