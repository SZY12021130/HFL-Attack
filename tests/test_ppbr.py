"""
PPBR 测试（He 等 IEEE TMC 2026 的算法 2/3/4/5）
================================================

运行方式：
    python -m pytest tests/test_ppbr.py -v

覆盖：
  1. CLDP 扰动：支撑有界（‖y−s‖_∞ ≤ L'）且近似无偏（Theorem 2）；
  2. trimmed k-means：良性元组聚成大簇、恶意离群元组被剔除出可信集；
  3. 云范数中位数筛选：中位数 M、按 l' 剔除偏离边缘、裁剪系数 x''=min(1,M/‖m‖)；
  4. 端到端：ppbr.enabled 自动开启 hierarchy，2 轮加密训练跑通且指标正常；
  5. 端到端：PPBR + 攻击组合跑通，过滤统计留痕。
"""

import numpy as np
import pytest
import torch
import yaml


# =====================================================================
# 1. CLDP（式 2）
# =====================================================================

def test_cldp_support_bounded_and_unbiased():
    from ppbr.cldp import perturb_sign_tuple
    rng = np.random.RandomState(0)
    s = np.array([0.45, 0.50, 0.05])
    samples = np.stack([perturb_sign_tuple(s, 3.0, 6.0, 7.0, rng)
                        for _ in range(20000)])
    # 支撑有界：‖y−s‖_∞ ≤ L'（拒绝采样保证）
    assert np.all(np.abs(samples - s) <= 7.0 + 1e-9)
    # Theorem 2：扰动质心无偏 —— 大样本均值应接近 s
    assert np.allclose(samples.mean(axis=0), s, atol=0.05), \
        f"CLDP 扰动有偏: mean={samples.mean(axis=0)} vs s={s}"


def test_cldp_epsilon_zero_is_roughly_uniform_in_ball():
    """ε→0 时密度在 B_L 内趋近均匀，样本应散布在球内而非紧贴 s"""
    from ppbr.cldp import perturb_sign_tuple
    rng = np.random.RandomState(1)
    s = np.array([0.5, 0.5, 0.0])
    samples = np.stack([perturb_sign_tuple(s, 0.01, 6.0, 7.0, rng)
                        for _ in range(2000)])
    r = np.linalg.norm(samples - s, axis=1)
    assert np.median(r) > 0.5, "ε→0 时扰动应充分随机（中位半径应较大）"


# =====================================================================
# 2. trimmed k-means 聚类过滤（算法 2 c）
# =====================================================================

def test_clustering_separates_malicious_outliers():
    from ppbr.clustering import select_trusted_clients
    rng = np.random.RandomState(42)
    # 8 个良性客户端：符号元组相近（梯度方向相似）
    benign = np.array([[0.5, 0.45, 0.05], [0.48, 0.47, 0.05], [0.52, 0.43, 0.05],
                       [0.49, 0.46, 0.05], [0.51, 0.44, 0.05], [0.47, 0.48, 0.05],
                       [0.50, 0.45, 0.05], [0.53, 0.42, 0.05]])
    benign += rng.normal(0, 0.01, benign.shape)
    # 3 个恶意客户端：符号分布完全反转（远离良性簇）
    malicious = np.array([[0.05, 0.90, 0.05], [0.08, 0.87, 0.05],
                          [0.10, 0.85, 0.05]])
    points = np.vstack([benign, malicious])
    ids = list(range(len(points)))
    trusted = select_trusted_clients(points, ids, k=2, b=1,
                                     trim_ratio=0.3, rng=rng)
    assert trusted.isdisjoint({8, 9, 10}), \
        f"恶意客户端应被过滤，可信集={trusted}"
    assert len(trusted) >= 5, f"良性客户端大多应保留，可信集={trusted}"


# =====================================================================
# 3. 云范数中位数筛选 + 裁剪（算法 4 EF / 算法 5 GA）
# =====================================================================

def _means(norm: float, seed: int):
    g = torch.Generator().manual_seed(seed)
    v = torch.randn(8, generator=g)
    v = v / v.norm() * norm
    return {"w": v}


def test_cloud_filter_median_and_clip():
    from ppbr import PPBRController
    ctrl = PPBRController({'ppbr': {'enabled': True, 'beta_known': False,
                                    'num_edges': 4}},
                          num_edges=4, seed=0)
    edge_means = {0: _means(1.0, 0), 1: _means(1.1, 1),
                  2: _means(5.0, 2), 3: _means(0.9, 3)}
    trusted, clip, M = ctrl.cloud_filter_edges(edge_means)
    assert M == pytest.approx((1.0 + 1.1) / 2, abs=1e-6)  # median of 4 = 中间两数均值
    # β 未知 → l' = 0.5·4 = 2：先剔除偏离中位数最远的边缘 2（范数 5.0），
    # 再剔除次远的边缘 3（范数 0.9）
    assert trusted == {0, 1}
    # 裁剪系数 x'' = min(1, M/‖m‖)
    assert clip[0] == pytest.approx(1.0)                 # 范数低于 M 不裁剪
    assert clip[1] == pytest.approx(1.05 / 1.1)          # min(1, M/1.1)


def test_cloud_filter_beta_known():
    from ppbr import PPBRController
    ctrl = PPBRController({'ppbr': {'enabled': True, 'beta_known': True,
                                    'beta': 0.25, 'num_edges': 4}},
                          num_edges=4, seed=0)
    edge_means = {0: _means(1.0, 0), 1: _means(1.05, 1),
                  2: _means(1.1, 2), 3: _means(9.0, 3)}
    trusted, clip, M = ctrl.cloud_filter_edges(edge_means)
    # β 已知 → l' = (1−0.25)·4 = 3，剔除范数 9.0 的边缘
    assert 3 not in trusted and len(trusted) == 3


def test_client_momentum_update():
    from ppbr import PPBRController
    ctrl = PPBRController({'ppbr': {'enabled': True, 'momentum_phi': 0.9}},
                          num_edges=2, seed=0)
    upd = {"w": torch.ones(4)}
    ctrl.update_momenta([0], [upd])
    m1 = ctrl.momentum_of(0)["w"]
    assert torch.allclose(m1, torch.full((4,), 0.1))  # (1−φ)·Δ
    ctrl.update_momenta([0], [upd])
    m2 = ctrl.momentum_of(0)["w"]
    assert torch.allclose(m2, torch.full((4,), 0.19))  # φ·0.1 + 0.1


# =====================================================================
# 4/5. 端到端
# =====================================================================

def _write_cfg(tmp_path, ppbr=True, attack=None):
    with open('configs/config.yaml', encoding='utf-8') as f:
        cfg = yaml.safe_load(f)
    cfg['ppbr'] = {'enabled': ppbr}
    cfg['hierarchy'] = {'enabled': False, 'num_edges': 3, 'assignment': 'static'}
    if attack:
        cfg['attack'] = {'enabled': True, 'name': attack, 'malicious_count': 2}
    cfg['system']['num_clients'] = 10
    cfg['system']['num_rounds'] = 2
    cfg['system']['device'] = 'cpu'
    cfg['data']['dataset'] = 'mnist'
    cfg['ckks']['poly_modulus_degree'] = 4096
    cfg['ckks']['coeff_mod_bit_sizes'] = [40, 20, 40]
    cfg['ckks']['global_scale'] = 1048576
    path = tmp_path / 'ppbr_config.yaml'
    with open(path, 'w', encoding='utf-8') as f:
        yaml.safe_dump(cfg, f, allow_unicode=True)
    return str(path)


@pytest.mark.slow
def test_ppbr_auto_enables_hierarchy_end_to_end(tmp_path):
    """ppbr.enabled=true 且 hierarchy.enabled=false → 自动开启层次架构并跑通"""
    from main import CKKSFedExperiment
    exp = CKKSFedExperiment(_write_cfg(tmp_path), output_dir=str(tmp_path))
    assert exp.ppbr is not None
    assert exp.hierarchy is not None, "PPBR 应自动开启 hierarchy"
    exp.run_experiment()
    acc = exp.metrics.get_metrics().get('test_acc', [])
    assert len(acc) == 2 and all(0.0 <= a <= 100.0 for a in acc)
    # PPBR 过滤统计留痕
    assert 'clients' in exp.ppbr.last_stats and 'edges' in exp.ppbr.last_stats


@pytest.mark.slow
def test_ppbr_with_attack_end_to_end(tmp_path):
    """PPBR + 模型投毒组合：训练跑通且动量/筛选状态正常"""
    from main import CKKSFedExperiment
    exp = CKKSFedExperiment(_write_cfg(tmp_path, attack='model:sign_flipping'),
                            output_dir=str(tmp_path))
    assert exp.attack_inst is not None and exp.ppbr is not None
    exp.run_experiment()
    acc = exp.metrics.get_metrics().get('test_acc', [])
    assert len(acc) == 2 and all(0.0 <= a <= 100.0 for a in acc)
    # 动量已按算法 3 维护，云筛选结果已记录
    assert len(exp.ppbr.momenta) > 0
    assert 'M' in exp.ppbr.last_stats['edges']
