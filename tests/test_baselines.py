"""
baseline/ 明文鲁棒聚合基线测试
==============================

运行方式：
    python -m pytest tests/test_baselines.py -v
"""

import copy

import torch

from baseline import AGGREGATION_REGISTRY


def _mk_updates(benign_mean: float = 1.0, n_benign: int = 10,
                n_malicious: int = 3, seed: int = 0, dim: int = 20):
    """构造合成更新：n_benign 个良性（基准向量 + 小噪声）+
    n_malicious 个恶意（符号翻转 + 大偏移）"""
    g = torch.Generator().manual_seed(seed)
    benign = [benign_mean + 0.05 * torch.randn(dim, generator=g)
              for _ in range(n_benign)]
    malicious = [-benign_mean - 10.0 - 0.05 * torch.randn(dim, generator=g)
                 for _ in range(n_malicious)]
    updates = [{"w": u} for u in benign + malicious]
    weights = [1.0] * len(updates)
    return updates, weights, benign


def _flatten(update: dict) -> torch.Tensor:
    return torch.cat([v.flatten() for v in update.values()])


def _cosine(a: torch.Tensor, b: torch.Tensor) -> float:
    return torch.dot(a, b).item() / (a.norm().item() * b.norm().item() + 1e-12)


def _assert_closer_to_benign(agg: dict, benign: list, updates: list,
                             n_malicious: int):
    """聚合结果与良性均值方向的余弦相似度应显著大于与恶意方向的余弦相似度"""
    agg_vec = _flatten(agg)
    benign_centroid = torch.stack(benign).mean(0)
    malicious_centroid = torch.stack(
        [_flatten(u) for u in updates[-n_malicious:]]).mean(0)
    cos_benign = _cosine(agg_vec, benign_centroid)
    cos_malicious = _cosine(agg_vec, malicious_centroid)
    assert cos_benign > 0.0, f"聚合结果应指向良性方向, cos={cos_benign:.3f}"
    assert cos_benign > cos_malicious, \
        f"应更接近良性方向: cos_benign={cos_benign:.3f} <= cos_malicious={cos_malicious:.3f}"


# =====================================================================
# 注册表完整性
# =====================================================================

_EXPECTED_PACKAGES = {
    "fedavg", "krum", "median", "trimmed_mean", "multi_krum",
    "geometric_median", "centered_clipping", "dnc", "dp_brem", "rflba",
}


def test_registry_completeness():
    """每个 baseline/ 子包都应在注册表中，且四元信息齐全、可调用"""
    assert set(AGGREGATION_REGISTRY.keys()) == _EXPECTED_PACKAGES
    for name, spec in AGGREGATION_REGISTRY.items():
        assert callable(spec.fn), f"{name}: fn 不可调用"
        assert isinstance(spec.default_params, dict), f"{name}: 默认params缺失"
        assert spec.citation, f"{name}: 论文出处缺失"
        assert spec.description, f"{name}: 描述缺失"


def test_registry_all_importable_and_callable():
    """逐一 import 并调用每个规则（良性小数据，验证接口与输出形状）"""
    updates, weights, _ = _mk_updates(n_malicious=0)
    ref_keys = set(updates[0].keys())
    for name, spec in AGGREGATION_REGISTRY.items():
        state = {} if spec.stateful else None
        out = spec.fn(copy.deepcopy(updates), weights,
                      **({**spec.default_params, **({"state": state} if state is not None else {})}))
        assert set(out.keys()) == ref_keys, f"{name}: 输出键不匹配"
        for k, v in out.items():
            assert v.shape == updates[0][k].shape, f"{name}: {k} 形状不匹配"
            assert torch.isfinite(v).all(), f"{name}: {k} 含非有限值"


# =====================================================================
# 鲁棒性：各规则在符号翻转攻击下应输出指向良性方向的聚合结果
# =====================================================================

def test_median_robust():
    updates, weights, benign = _mk_updates()
    _assert_closer_to_benign(AGGREGATION_REGISTRY["median"].fn(updates), benign, updates, 3)


def test_trimmed_mean_robust():
    updates, weights, benign = _mk_updates()
    agg = AGGREGATION_REGISTRY["trimmed_mean"].fn(updates, trim_ratio=0.25)
    _assert_closer_to_benign(agg, benign, updates, 3)


def test_krum_robust():
    updates, weights, benign = _mk_updates()
    agg = AGGREGATION_REGISTRY["krum"].fn(updates, f=3)
    _assert_closer_to_benign(agg, benign, updates, 3)


def test_multi_krum_robust():
    updates, weights, benign = _mk_updates()
    agg = AGGREGATION_REGISTRY["multi_krum"].fn(updates, f=3, m=10)
    _assert_closer_to_benign(agg, benign, updates, 3)


def test_geometric_median_robust():
    updates, weights, benign = _mk_updates()
    agg = AGGREGATION_REGISTRY["geometric_median"].fn(updates)
    _assert_closer_to_benign(agg, benign, updates, 3)


def test_centered_clipping_robust():
    updates, weights, benign = _mk_updates()
    # tau_factor=1.3 时 τ 随恶意大范数缩放、裁剪不生效；
    # 收紧 tau_factor 验证裁剪语义（聚合方向仍指向良性）
    agg = AGGREGATION_REGISTRY["centered_clipping"].fn(
        updates, num_iter=5, tau_factor=0.3)
    _assert_closer_to_benign(agg, benign, updates, 3)


def test_dnc_robust():
    updates, weights, benign = _mk_updates()
    agg = AGGREGATION_REGISTRY["dnc"].fn(updates, num_byzantine=3, seed=1)
    _assert_closer_to_benign(agg, benign, updates, 3)


def test_rflba_robust():
    updates, weights, benign = _mk_updates()
    agg = AGGREGATION_REGISTRY["rflba"].fn(updates, weights, threshold=0.0)
    _assert_closer_to_benign(agg, benign, updates, 3)


def test_dp_brem_robust():
    """dp_brem 跨轮状态：动量应朝良性方向累积，加噪后仍更接近良性"""
    updates, weights, benign = _mk_updates()
    fn = AGGREGATION_REGISTRY["dp_brem"].fn
    state = {}
    agg = None
    for _ in range(3):
        agg = fn(copy.deepcopy(updates), weights, state=state,
                 phi=0.9, sigma=0.001, seed=42)
    _assert_closer_to_benign(agg, benign, updates, 3)


def test_dp_brem_stateful_across_rounds():
    """dp_brem 的 state 应跨轮保持动量（同一 state 复用 vs 新 state 结果不同，
    且长期应趋向良性均值）"""
    updates, weights, benign = _mk_updates(n_malicious=0)
    fn = AGGREGATION_REGISTRY["dp_brem"].fn
    state = {}
    for _ in range(3):
        agg_shared = fn(copy.deepcopy(updates), weights, state=state,
                        phi=0.9, sigma=0.0, seed=42)
    agg_fresh = fn(copy.deepcopy(updates), weights, state={},
                   phi=0.9, sigma=0.0, seed=42)
    # 无恶意时，共享动量多轮后应显著接近良性均值（动量加速效果）
    benign_mean = torch.stack(benign).mean(0)
    assert (agg_shared["w"] - benign_mean).norm() < (agg_fresh["w"] - benign_mean).norm()


def test_fedavg_no_defense():
    """fedavg 不做防御：含恶意更新时聚合结果被显著拉向恶意方向（对比基线）"""
    updates, weights, benign = _mk_updates()
    agg = AGGREGATION_REGISTRY["fedavg"].fn(updates, weights)
    agg_vec = _flatten(agg)
    benign_centroid = torch.stack(benign).mean(0)
    # 符号翻转 + 大偏移：fedavg 均值应明显偏离良性均值
    assert (agg_vec - benign_centroid).norm() > 1.0, \
        "fedavg 应被恶意更新显著带偏（无防御基线语义）"


# =====================================================================
# 服务器注册表分派（含向后兼容与未知规则回退）
# =====================================================================

def test_server_registry_dispatch():
    """AggregationServer 按 defense.aggregation_rule 从注册表分派（含旧名字）"""
    import torch.nn as nn
    from ckks_fed.server import AggregationServer

    model = nn.Linear(4, 2)
    for rule in ["fedavg", "median", "trimmed_mean", "krum", "multi_krum",
                 "geometric_median", "centered_clipping", "dnc",
                 "dp_brem", "rflba"]:
        config = {"defense": {"aggregation_rule": rule,
                              "anomaly_filter": None,
                              "params": {"f": 1, "seed": 42, "sigma": 0.001}},
                  "global_mask_vote": {"mode": "none"},
                  "system": {"upload_difference": True}}
        server = AggregationServer(model, config, device="cpu", ckks_manager=None)
        updates, weights, _ = _mk_updates(n_benign=6, n_malicious=2, dim=4)
        agg = server.aggregate_plain_updates(updates, weights)
        assert set(agg.keys()) == set(updates[0].keys())
        # dp_brem 有状态：服务器应自动维护跨轮 state
        if rule == "dp_brem":
            assert "dp_brem" in server._rule_state
            assert "momentum" in server._rule_state["dp_brem"]


def test_server_unknown_rule_falls_back_to_fedavg():
    """未知聚合规则应回退 fedavg 并告警，不应抛异常"""
    import torch.nn as nn
    from ckks_fed.server import AggregationServer

    model = nn.Linear(4, 2)
    config = {"defense": {"aggregation_rule": "no_such_rule",
                          "anomaly_filter": None, "params": {}},
              "global_mask_vote": {"mode": "none"},
              "system": {"upload_difference": True}}
    server = AggregationServer(model, config, device="cpu", ckks_manager=None)
    updates, weights, benign = _mk_updates(n_benign=6, n_malicious=0, dim=4)
    agg = server.aggregate_plain_updates(updates, weights)
    # 回退 fedavg = 等权均值
    assert torch.allclose(_flatten(agg),
                          torch.stack([_flatten(u) for u in updates]).mean(0),
                          atol=1e-6)


def test_legacy_import_path_backward_compatible():
    """旧导入路径 ckks_fed.defenses.*_aggregate 仍可用（re-export）"""
    from ckks_fed.defenses import (
        fedavg_aggregate, median_aggregate, trimmed_mean_aggregate,
        krum_aggregate, multi_krum_aggregate, geometric_median_aggregate,
        dnc_aggregate, centered_clipping_aggregate,
    )
    updates, weights, _ = _mk_updates(n_benign=6, n_malicious=0, dim=4)
    for fn in (fedavg_aggregate, median_aggregate, trimmed_mean_aggregate,
               krum_aggregate, multi_krum_aggregate, geometric_median_aggregate,
               dnc_aggregate, centered_clipping_aggregate):
        out = fn(copy.deepcopy(updates), *(() if fn is not fedavg_aggregate else (weights,)))
        assert set(out.keys()) == set(updates[0].keys())
