"""
IPM 攻击与 PPBR 对比基线（DnC / CenteredClipping）测试
========================================================

运行方式：
    python -m pytest tests/test_ppbr_baselines.py -v
"""

import numpy as np
import pytest
import torch


def _mk_updates(benign_mean: float, n_benign: int, malicious: list,
                seed: int = 0, dim: int = 20):
    g = torch.Generator().manual_seed(seed)
    updates = [{"w": benign_mean + 0.1 * torch.randn(dim, generator=g)}
               for _ in range(n_benign)]
    updates += [{"w": m} for m in malicious]
    return updates


# =====================================================================
# IPM 攻击
# =====================================================================

def test_ipm_zero_sum_property():
    """ξ=(n−m)/m 时恶意更新抵消良性总和：Σ_all = (1 − mξ/(n−m))·Σ_benign = 0"""
    from hfl_attacks import ATTACK_REGISTRY
    from hfl_attacks.base import AttackContext

    atk = ATTACK_REGISTRY["model:ipm"](xi=3.0)  # (8−2)/2 = 3
    n, m = 8, 2
    updates = [{"w": torch.ones(10)} for _ in range(n)]
    malicious = list(range(n - m, n))
    for i in malicious:
        ctx = AttackContext(all_updates=updates, self_index=i,
                            malicious_indices=malicious)
        updates[i] = atk.attack(updates[i], ctx)
    total = torch.stack([u["w"] for u in updates]).sum(0)
    assert total.abs().max() < 1e-5


def test_ipm_registered():
    from hfl_attacks import ATTACK_REGISTRY
    assert "model:ipm" in ATTACK_REGISTRY


# =====================================================================
# DnC（Detect and Clean）
# =====================================================================

def test_dnc_filters_gaussian_outlier():
    """高斯离群更新应被 DnC 剔除，聚合结果接近良性均值"""
    from ckks.defenses import dnc_aggregate
    updates = _mk_updates(1.0, 8, [10.0 * torch.ones(20), -10.0 * torch.ones(20)])
    agg = dnc_aggregate(updates, num_byzantine=2, seed=1)
    assert torch.allclose(agg["w"], torch.ones(20), atol=0.2), \
        f"DnC 聚合应接近良性均值，得到 {agg['w'].mean():.3f}"


def test_dnc_clean_case_matches_mean():
    """无攻击时 DnC 不应误伤良性更新"""
    from ckks.defenses import dnc_aggregate
    updates = _mk_updates(1.0, 8, [], seed=2)
    agg = dnc_aggregate(updates, num_byzantine=2, seed=1)
    assert torch.allclose(agg["w"], torch.ones(20), atol=0.15)


# =====================================================================
# Centered Clipping
# =====================================================================

def test_centered_clipping_resists_ipm():
    """CenteredClipping 对 IPM 类方向反转攻击应收敛到良性均值附近"""
    from ckks.defenses import centered_clipping_aggregate
    n, m = 8, 2
    benign = [torch.full((20,), 0.5) for _ in range(n - m)]
    # IPM: 恶意更新 = −1/(n−m)·Σ_benign = −0.5
    malicious = [torch.full((20,), -0.5) for _ in range(m)]
    updates = [{"w": u} for u in benign + malicious]
    agg = centered_clipping_aggregate(updates, num_iter=5)
    assert agg["w"].mean().item() > 0.2, \
        f"CenteredClipping 应抵抗 IPM，得到 {agg['w'].mean():.3f}"


def test_centered_clipping_clean_case():
    from ckks.defenses import centered_clipping_aggregate
    updates = _mk_updates(1.0, 8, [], seed=3)
    agg = centered_clipping_aggregate(updates, num_iter=5)
    assert torch.allclose(agg["w"], torch.ones(20), atol=0.1)
