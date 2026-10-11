"""
PPBR 传输层加密原语与协议一致性测试
====================================

运行方式：
    python -m pytest tests/test_ppbr_crypto.py -v

覆盖：
  1. PLHE 分块加解密：多块往返（dim > slots）精度在 CKKS 误差内；
  2. SA 成对掩码：双方由同一 pair_seed 导出同一掩码，求和时严格相消；
  3. 两轮范数点积协议：云由盲化点积恢复 ‖m‖²，与明文真值一致；
  4. 协议整体一致性：PPBRProtocol.run_round 的产物 m_a 与明文参考实现
     （同一动量/过滤/筛选/裁剪语义）在 CKKS 误差内相等——噪声 x、盲化
     标量 r/R 在 denoise 后精确抵消。
"""

import numpy as np
import pytest
import torch

_CRYPTO = {'poly_modulus_degree': 4096,
           'coeff_mod_bit_sizes': [40, 20, 40],
           'global_scale': 2 ** 20}


# =====================================================================
# 1. PLHE 分块加解密
# =====================================================================

def test_key_context_multiblock_roundtrip():
    from ppbr.crypto import PPBRKeyContext
    ctx = PPBRKeyContext(**_CRYPTO)
    rng = np.random.RandomState(0)
    vec = rng.randn(5000)  # 5000 > 2048 slots → 多块
    blocks = ctx.encrypt_blocks(vec)
    assert len(blocks) == (len(vec) + ctx.slots - 1) // ctx.slots
    rec = ctx.decrypt_blocks(blocks, len(vec))
    # CKKS 近似算术：4096 度 + [40,20,40] 链的绝对误差 ~1e-3 量级
    assert np.allclose(rec, vec, rtol=1e-2, atol=5e-3)


def test_public_bytes_hide_secret_key():
    """公钥字节序列化后反序列化，不能解密（云只拿边缘公钥的场景）"""
    import tenseal as ts
    from ppbr.crypto import PPBRKeyContext
    ctx = PPBRKeyContext(**_CRYPTO)
    pub = ts.context_from(ctx.public_bytes())
    ct = ts.ckks_vector(pub, [1.0, 2.0, 3.0])
    with pytest.raises(Exception):
        ct.decrypt()


# =====================================================================
# 2. SA 成对掩码求和相消
# =====================================================================

def test_sa_pairwise_masks_cancel():
    from ppbr.crypto import pair_seed, prg_vector
    dim, round_num = 1000, 3
    cids = [3, 7, 12, 20]
    rng = np.random.RandomState(1)
    mprime = {c: rng.randn(dim) for c in cids}
    z = dict(mprime)
    for c in cids:
        for c2 in cids:
            if c2 == c:
                continue
            r = prg_vector(pair_seed("dh", c, c2, round_num), dim)
            z[c] = z[c] + (r if c < c2 else -r)
    total = np.sum([z[c] for c in cids], axis=0)
    assert np.allclose(total, np.sum([mprime[c] for c in cids], axis=0),
                       atol=1e-8)


def test_pair_seed_is_symmetric():
    """通信双方 (a,b) 与 (b,a) 导出同一种子（模拟 DH 协商的对称性）"""
    from ppbr.crypto import pair_seed
    assert pair_seed("dh", 3, 7, 5) == pair_seed("dh", 7, 3, 5)
    assert pair_seed("dh", 3, 7, 5) != pair_seed("dh", 3, 7, 6)


# =====================================================================
# 3. 两轮范数点积协议（算法 4 的盲化实现）
# =====================================================================

def test_two_round_norm_protocol_recovers_squared_norm():
    """
    云持有 [x]_A 与 [2m']_{Ei}（分别可解密/不可解密），通过
    ct = [2m']∘(r·x) → 边缘解密求和 S = 2r(m'·x)，云算
    ‖m‖² = ‖m'‖² + ‖x‖² + S/r，应与明文真值一致。
    """
    from ppbr.crypto import PPBRKeyContext, blocks_of, ct_mul_plain
    cloud = PPBRKeyContext(**_CRYPTO)
    edge = PPBRKeyContext(**_CRYPTO)
    rng = np.random.RandomState(2)
    dim = 1500
    mprime = rng.randn(dim)
    x = rng.randn(dim) * 0.1

    # 客户端→边缘：[x]_A（云公钥）
    ct_x = cloud.encrypt_blocks(x)
    x_at_cloud = cloud.decrypt_blocks(ct_x, dim)
    assert np.allclose(x_at_cloud, x, rtol=1e-2, atol=5e-3)

    # 边缘→云：[2m']_{Ei}（边缘公钥，云不可解密）
    ct_2mp = edge.encrypt_blocks(2.0 * mprime)

    # 云盲化乘法 + 边缘解密求和
    r = 0.73
    ct_prod = ct_mul_plain(ct_2mp, [b * r for b in blocks_of(cloud, x_at_cloud)])
    v = edge.decrypt_blocks(ct_prod, dim)
    S = float(v.sum())
    norm2_rec = float(mprime @ mprime + x @ x + S / r)
    norm2_true = float((mprime + x) @ (mprime + x))
    assert norm2_rec == pytest.approx(norm2_true, rel=0.02)


# =====================================================================
# 4. 协议整体一致性：m_a 与明文参考实现一致
# =====================================================================

_PPBR_CFG = {'enabled': True, 'beta_known': True, 'beta': 0.0,
             'num_coords': 50, 'k': 1, 'b': 1, 'trim_ratio': 0.05,
             'momentum_phi': 0.9, 'noise_rel': 0.5}


def _synthetic_round():
    g = torch.Generator().manual_seed(7)
    updates = {cid: {"w": 0.1 * torch.randn(200, generator=g)}
               for cid in range(6)}
    groups = {0: [0, 1, 2], 1: [3, 4, 5]}
    return updates, groups


def test_protocol_matches_plaintext_reference():
    """
    PPBR 完整协议（掩码 + 密文 + 盲化范数 + 盲化裁剪）的产物 m_a
    与明文参考实现（相同过滤/动量/筛选/裁剪语义）一致。
    噪声 x 与盲化标量 r/R 在 denoise 与除回后精确抵消，
    只剩 CKKS 范数估计的 ~1% 误差（容忍 5%）。
    """
    from ppbr import PPBRController, PPBRProtocol
    updates, groups = _synthetic_round()
    full = {'ppbr': dict(_PPBR_CFG), 'ckks': dict(_CRYPTO)}

    proto = PPBRProtocol(full, num_edges=2, seed=42)
    m_a = proto.run_round(0, updates, groups)

    # 明文参考：与 protocol 内部完全相同的调用序列（CLDP rng 消费一致）
    ref = PPBRController({'ppbr': dict(_PPBR_CFG)}, num_edges=2, seed=42)
    dim = 200
    coord = ref.select_coordinates(dim, 0)
    tuples = {cid: ref.perturb(ref.client_sign_tuple(u, coord))
              for cid, u in updates.items()}
    trusted = ref.filter_clients(tuples, groups)
    cids = sorted(trusted)
    ref.update_momenta(cids, [updates[c] for c in cids])
    means = ref.edge_means(groups, set(cids))
    trusted_edges, clip, M = ref.cloud_filter_edges(means)
    m_ref = torch.zeros(dim)
    for e in trusted_edges:
        m_ref += clip[e] * means[e]["w"]
    m_ref /= max(1, len(trusted_edges))

    assert sorted(m_a) == ["w"]
    assert torch.allclose(m_a["w"], m_ref, rtol=5e-2, atol=1e-4), \
        f"协议产物与明文参考不一致: max diff = {(m_a['w'] - m_ref).abs().max()}"


def test_protocol_leaves_audit_trail():
    """协议执行后留痕：线上密文块统计 + 云筛选诊断"""
    from ppbr import PPBRProtocol
    updates, groups = _synthetic_round()
    full = {'ppbr': dict(_PPBR_CFG), 'ckks': dict(_CRYPTO)}
    proto = PPBRProtocol(full, num_edges=2, seed=42)
    proto.run_round(0, updates, groups)
    assert proto.last_wire['client_x_blocks'] > 0
    assert proto.last_wire['edge_2mp_blocks'] > 0
    assert 'M' in proto.last_stats['edges']
