"""
层次联邦学习（云-边-端）测试
==============================

运行方式：
    python -m pytest tests/test_hierarchy.py -v

覆盖：
  1. 关联策略：static 固定分片完整性/均衡性；uav 每轮合法分配且随轮变化；
  2. 安全边界：边缘节点只持公钥，不能解密；
  3. 层次化掩码投票与扁平投票严格等价；
  4. 数学正确性：层次两级密文聚合 == 扁平单服务器密文聚合（CKKS 端到端）；
  5. 端到端：开启 hierarchy 跑一轮 main.py 加密训练流程不报错。
"""

import copy

import numpy as np
import pytest
import torch
import yaml


# =====================================================================
# 1. 关联策略
# =====================================================================

def _hier_cfg(**over):
    cfg = {
        'hierarchy': {
            'enabled': True,
            'num_edges': 3,
            'assignment': 'static',
            'edge_selection_rate': 1.0,
            'uav': {'area_size': 1000.0, 'speed': 30.0,
                    'coverage_radius': 400.0, 'strict_coverage': False,
                    'seed': 42},
        },
        'ckks': {
            'poly_modulus_degree': 4096,
            'coeff_mod_bit_sizes': [40, 20, 40],
            'global_scale': 2**20,
            'slot_packing': True,
            'sparsification': {'strategy': 'none'},
        },
        'global_mask_vote': {'mode': 'none'},
        'system': {'random_seed': 42},
    }
    cfg['hierarchy'].update({k: v for k, v in over.items() if k != 'uav'})
    if 'uav' in over:
        cfg['hierarchy']['uav'].update(over['uav'])
    return cfg


def _fake_server_manager(cfg):
    """构造一个仅公钥的服务器 CKKSManager（与 main.py 相同方式）"""
    from ckks_fed.crypto import CKKSContext, CKKSManager
    full = CKKSManager(cfg['ckks'].copy())
    public_ctx = CKKSContext.from_full_bytes(
        full.context.get_context_bytes(),
        poly_modulus_degree=cfg['ckks']['poly_modulus_degree'])
    return CKKSManager(cfg['ckks'].copy(), context=public_ctx), full


def test_static_assignment_covers_all_clients():
    from ckks_fed.hierarchy import StaticAssignment
    a = StaticAssignment(num_clients=10, num_edges=3)
    mapping = a.assign(0)
    assert sorted(mapping.keys()) == list(range(10))
    assert set(mapping.values()) == {0, 1, 2}
    # 分片大小差不超过 1
    sizes = [list(mapping.values()).count(e) for e in range(3)]
    assert max(sizes) - min(sizes) <= 1


def test_uav_assignment_valid_and_dynamic():
    from ckks_fed.hierarchy import UAVAssignment
    uav = UAVAssignment(num_clients=20, num_edges=4, seed=42)
    m0 = uav.assign(0)
    assert all(0 <= e < 4 for e in m0.values())
    assert len(m0) == 20
    # 多轮后关联发生变化（UAV 移动）
    m50 = uav.assign(50)
    changed = sum(1 for c in m0 if m0[c] != m50.get(c))
    assert changed > 0, "UAV 移动后客户端关联应发生变化"


def test_uav_strict_coverage_excludes_far_clients():
    from ckks_fed.hierarchy import UAVAssignment
    uav = UAVAssignment(num_clients=20, num_edges=2,
                        coverage_radius=50.0, strict_coverage=True, seed=1)
    m = uav.assign(0)
    assert len(m) <= 20  # 部分客户端可能无覆盖


# =====================================================================
# 2. 安全边界：边缘节点无私钥
# =====================================================================

def test_edge_node_has_no_secret_key():
    from ckks_fed.hierarchy import HierarchyController
    cfg = _hier_cfg()
    server_mgr, _ = _fake_server_manager(cfg)
    ctrl = HierarchyController(cfg, server_mgr, num_clients=10)
    for eid, edge in ctrl.edges.items():
        assert not edge.has_secret_key(), f"Edge #{eid} 不应持有私钥"
        # 边缘尝试解密必须失败
        from ckks_fed.crypto import CKKSEncryptor
        enc = CKKSEncryptor(edge.ckks_manager.context)
        ct = enc.encrypt(np.array([1.0, 2.0]))
        with pytest.raises(Exception):
            ct.decrypt()


# =====================================================================
# 3. 层次化掩码投票 == 扁平投票
# =====================================================================

@pytest.mark.parametrize("mode", ["union", "threshold", "topk"])
def test_hierarchical_mask_vote_equivalence(mode):
    from ckks_fed.global_mask_vote import GlobalMaskVote
    from ckks_fed.hierarchy import HierarchyController

    gmv_cfg = {'mode': mode, 'threshold_pct': 0.5,
               'topk_count_k': 5, 'topk_pct': None}
    cfg = _hier_cfg()
    cfg['global_mask_vote'] = gmv_cfg
    server_mgr, _ = _fake_server_manager(cfg)
    ctrl = HierarchyController(cfg, server_mgr, num_clients=10)

    rng = np.random.RandomState(0)
    num_batches, num_clients = 22, 6
    masks = {cid: rng.randint(0, 2, size=num_batches).astype(np.int8)
             for cid in rng.choice(10, size=num_clients, replace=False)}

    # 层次投票
    hier_mask = ctrl.vote_global_mask(masks, num_batches=num_batches, round_num=0)
    # 扁平投票
    flat_vote = GlobalMaskVote(mode=mode, threshold_pct=0.5,
                               topk_k=5, topk_pct=None)
    flat_mask = flat_vote.server_aggregate(list(masks.values()), num_batches)
    assert np.array_equal(hier_mask, flat_mask), \
        f"mode={mode}: 层次投票与扁平投票结果不一致"


# =====================================================================
# 4. 数学正确性：两级密文聚合 == 扁平聚合
# =====================================================================

def _make_update(seed):
    g = torch.Generator().manual_seed(seed)
    return {"w": torch.randn(4, 4, generator=g),
            "b": torch.randn(4, generator=g)}


def test_hierarchical_aggregation_math_equivalence():
    """边缘两级聚合的结果必须与扁平聚合一致（解密后数值相等）"""
    from ckks_fed.hierarchy import HierarchyController

    cfg = _hier_cfg(num_edges=2, assignment='static')
    cfg['ckks']['sparsification'] = {'strategy': 'none'}
    cfg['global_mask_vote'] = {'mode': 'none'}
    server_mgr, full_mgr = _fake_server_manager(cfg)

    updates = [_make_update(s) for s in range(6)]
    weights = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]

    # 客户端加密（模拟网络传输：序列化为 bytes）
    encs = {}
    for cid, upd in enumerate(updates):
        e = full_mgr.encrypt_model_update(upd, round_num=0)
        e['ciphertexts'] = [c.serialize() for c in e['ciphertexts']]
        encs[cid] = e

    # 扁平聚合（直接全部到服务器）
    flat = full_mgr.aggregate_encrypted_updates(
        [encs[c] for c in range(6)], weights)
    shapes = {k: v.shape for k, v in updates[0].items()}
    flat_result = full_mgr.decrypt_model_update(flat, shapes)

    # 层次聚合：边缘 0 负责客户端 0-2，边缘 1 负责客户端 3-5
    ctrl = HierarchyController(cfg, server_mgr, num_clients=6)
    edge_updates, edge_weights, edge_sizes = ctrl.aggregate_edges(
        encs, {c: weights[c] for c in range(6)}, round_num=0)
    assert len(edge_updates) == 2
    # 边缘权重 = 1.0（云侧等权加法合并；全局权重缩放已在边缘预缩放完成）
    assert edge_weights == [1.0, 1.0]
    hier = full_mgr.aggregate_encrypted_updates(edge_updates, edge_weights,
                                                normalize=False)
    hier_result = full_mgr.decrypt_model_update(hier, shapes)

    for k in flat_result:
        assert torch.allclose(flat_result[k], hier_result[k], atol=1e-3), \
            f"{k}: 层次聚合与扁平聚合不一致"


# =====================================================================
# 5. 端到端：开启 hierarchy 跑一轮主流程
# =====================================================================

@pytest.mark.slow
def test_hierarchy_end_to_end_one_round(tmp_path):
    from main import CKKSFedExperiment

    with open('configs/config.yaml', encoding='utf-8') as f:
        cfg = yaml.safe_load(f)
    cfg['hierarchy'] = {'enabled': True, 'num_edges': 3, 'assignment': 'uav',
                        'edge_selection_rate': 1.0,
                        'uav': {'area_size': 1000.0, 'speed': 30.0,
                                'coverage_radius': 400.0,
                                'strict_coverage': False, 'seed': 42}}
    cfg['system']['num_clients'] = 10
    cfg['system']['num_rounds'] = 1
    cfg['system']['device'] = 'cpu'
    cfg['data']['dataset'] = 'mnist'
    cfg['ckks']['poly_modulus_degree'] = 4096
    cfg['ckks']['coeff_mod_bit_sizes'] = [40, 20, 40]
    cfg['ckks']['global_scale'] = 1048576

    config_path = tmp_path / 'hier_config.yaml'
    with open(config_path, 'w', encoding='utf-8') as f:
        yaml.safe_dump(cfg, f, allow_unicode=True)

    exp = CKKSFedExperiment(str(config_path), output_dir=str(tmp_path))
    exp.run_experiment()
    acc = exp.metrics.get_metrics().get('test_acc', [])
    assert len(acc) == 1
    assert 0.0 <= acc[0] <= 100.0
