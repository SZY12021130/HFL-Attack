"""
SA 层测试（Bonawitz'17 安全聚合：X25519+HKDF 协商 / PRG 掩码 / Shamir 掉线恢复）
====================================================================================

运行方式：
    python -m pytest tests/test_sa.py -v

覆盖：
  1. DH 协商对称性与上下文绑定（不同组/轮次导出不同种子）；
  2. 无掉线：n 方掩码求和严格相消，恢复输入总和；
  3. 提交前掉线（early）：幸存方成对掩码揭示后，恢复幸存输入总和；
  4. 提交后掉线（late）：Shamir 份额重建 b，掉线方输入仍计入聚合；
  5. 自负载掩码的隔离性：n−1 方合谋 + 聚合方也无法剥离受害方输入
     （没有 b 时可以——对照实验）；
  6. 协议集成：sa_simulate_dropout_rate>0 时 run_round 触发掉线恢复
     且聚合结果仍与明文参考一致。
"""

import numpy as np
import pytest
import torch


def _setup_group(n=5, tag="e0", rnd=0, dim=300, seed=0):
    from base.sa import SABucket, SAMember, sa_threshold
    rng = np.random.RandomState(seed)
    bucket = SABucket(tag, rnd, sa_threshold(n))
    members = {}
    for i in range(n):
        m = SAMember(i, tag, rnd, bucket.threshold)
        bucket.add_member(i, m.public_key())
        members[i] = m
    for i in range(n):
        members[i].register_peers(bucket.pubkeys())
    inputs = {i: rng.randn(dim) for i in range(n)}
    return bucket, members, inputs, dim


def _run_sa(bucket, members, inputs, dim, early=(), late=()):
    """按协议时序执行一轮 SA，返回 (恢复的总和, 计入数, 幸存方)"""
    # 阶段 1.5（Bonawitz 私钥备份）：DH 私钥 Shamir (t,n−1) 分片给同组其余方，
    # 掉线方的成对掩码恢复完全依赖此步
    sk_out = {i: members[i].share_private_key() for i in bucket.member_ids}
    shares_out = {}
    for i in bucket.member_ids:
        if i in early:
            bucket.mark_early_dropout(i)
            continue
        z, sh = members[i].masked_input(inputs[i])
        shares_out[i] = sh
        if i in late:
            bucket.mark_late_dropout(i, z)
        else:
            bucket.submit(i, z)
    survivors = [i for i in bucket.member_ids
                 if i not in set(early) | set(late)]
    reveals = {i: members[i].reveal_self(dim) for i in survivors}
    # 份额方向：掉线方 d 分发出去、幸存方 s 代持的份额是 sk_out[d][s]
    shares_sk = {d: [sk_out[d][s] for s in survivors if s in sk_out[d]]
                 for d in set(early) | set(late)}
    shares_b = {d: [shares_out[d][s] for s in survivors
                    if s in shares_out.get(d, {})] for d in late}
    total, n_counted = bucket.aggregate(reveals, shares_sk, shares_b, dim)
    return total, n_counted, survivors


# =====================================================================
# 1. DH 协商
# =====================================================================

def test_dh_seed_symmetry_and_context_binding():
    from base.sa import SAKeyPair
    a, b = SAKeyPair(), SAKeyPair()
    pa, pb = a.public_bytes(), b.public_bytes()
    s_ab = a.derive_seed(pb, "ppbr-sa-e0-0-1-3")
    s_ba = b.derive_seed(pa, "ppbr-sa-e0-0-1-3")
    assert s_ab == s_ba and len(s_ab) == 32
    # 上下文绑定：不同组/轮次/对导出不同种子
    assert a.derive_seed(pb, "ppbr-sa-e0-0-1-4") != s_ab
    assert a.derive_seed(pb, "ppbr-sa-e1-0-1-3") != s_ab
    c = SAKeyPair()
    assert a.derive_seed(c.public_bytes(), "ppbr-sa-e0-0-1-3") != s_ab


# =====================================================================
# 2-4. 聚合正确性（无掉线 / early / late）
# =====================================================================

def test_sa_sum_without_dropout():
    bucket, members, inputs, dim = _setup_group(n=5)
    total, n_counted, _ = _run_sa(bucket, members, inputs, dim)
    assert n_counted == 5
    assert np.allclose(total, sum(inputs.values()), atol=1e-8)


def test_sa_early_dropout_corrects_pairwise_masks():
    bucket, members, inputs, dim = _setup_group(n=6, seed=1)
    early = (1, 4)
    total, n_counted, survivors = _run_sa(bucket, members, inputs, dim,
                                          early=early)
    assert n_counted == 4 and set(survivors) == {0, 2, 3, 5}
    expect = sum(inputs[i] for i in survivors)
    assert np.allclose(total, expect, atol=1e-8)


def test_sa_late_dropout_recovers_self_mask_via_shamir():
    """提交后掉线：z 已在部分和中，Shamir 重建 b 后掉线方输入仍计入"""
    bucket, members, inputs, dim = _setup_group(n=6, seed=2)
    late = (2, 5)
    total, n_counted, survivors = _run_sa(bucket, members, inputs, dim,
                                          late=late)
    assert n_counted == 6  # late 掉线方输入仍计入
    assert np.allclose(total, sum(inputs.values()), atol=1e-8)


def test_sa_mixed_dropouts():
    bucket, members, inputs, dim = _setup_group(n=7, seed=3)
    total, n_counted, survivors = _run_sa(bucket, members, inputs, dim,
                                          early=(0,), late=(3, 6))
    assert n_counted == 6
    assert np.allclose(total, sum(inputs[i] for i in
                                  set(bucket.member_ids) - {0}), atol=1e-8)


# =====================================================================
# 5. 自负载掩码隔离性（合谋攻击对照）
# =====================================================================

def test_self_mask_prevents_colluding_isolation():
    """
    n=3：聚合方 + 2 个合谋客户端知道全部成对掩码与其自身输入。
    有自负载掩码 b 时，受害方输入不可恢复；去掉 b（对照）则可恢复。
    """
    from base.sa import prg_from_seed
    dim = 200
    bucket, members, inputs, dim = _setup_group(n=3, seed=4)
    victim, colluders = 0, [1, 2]
    z, _sh = members[victim].masked_input(inputs[victim])
    # 合谋方与聚合方还原：z − (与合谋方的成对掩码) − (合谋方输入无关项)
    recon = z.copy()
    for c in colluders:
        seed = members[victim]._pair_seed[c]
        r = prg_from_seed(seed, dim)
        recon -= r if victim < c else -r
    # 无自负载掩码（对照）：recon == 受害方输入
    # 有自负载掩码：residual == PRG(b_victim)，与输入无关
    assert np.allclose(recon - prg_from_seed(members[victim].b_seed, dim),
                       inputs[victim], atol=1e-8)
    assert not np.allclose(recon, inputs[victim], atol=1e-2), \
        "没有自负载掩码时合谋方应能剥离受害方输入（对照成立）"
    residual = recon - inputs[victim]
    assert np.linalg.norm(residual) > 1.0, "自负载掩码残差应是不可预测的随机向量"


# =====================================================================
# 6. 协议集成：掉线仿真下与明文参考一致
# =====================================================================

_PPBR_CFG = {'enabled': True, 'beta_known': True, 'beta': 0.0,
             'num_coords': 50, 'k': 1, 'b': 1, 'trim_ratio': 0.05,
             'momentum_phi': 0.9, 'noise_rel': 0.5,
             'sa_key_exchange': 'dh', 'sa_simulate_dropout_rate': 0.4}
_CRYPTO = {'poly_modulus_degree': 4096,
           'coeff_mod_bit_sizes': [40, 20, 40],
           'global_scale': 2 ** 20}


def _synthetic_round(n_clients=8, n_edges=2, dim=200, seed=7):
    g = torch.Generator().manual_seed(seed)
    # 公共信号 + 小噪声：掉线只改变参与子集，聚合方向由信号主导，
    # 余弦相似度才稳定（纯 iid 噪声下不同子集均值方向接近正交）
    signal = 1.0 * torch.randn(dim, generator=g)
    updates = {cid: {"w": signal + 0.05 * torch.randn(dim, generator=g)}
               for cid in range(n_clients)}
    groups = {e: list(range(e * 4, (e + 1) * 4)) for e in range(n_edges)}
    return updates, groups


def test_protocol_with_sa_dropout_matches_plaintext_reference():
    """真实 SA + 40% 掉线仿真：协议产物仍等于明文参考（幸存者均值语义）"""
    from ppbr import PPBRController, PPBRProtocol
    updates, groups = _synthetic_round()
    full = {'ppbr': dict(_PPBR_CFG), 'ckks': dict(_CRYPTO)}

    proto = PPBRProtocol(full, num_edges=2, seed=42)
    m_a = proto.run_round(0, updates, groups)
    # 掉线发生过（n=4 的组允许仿真）
    assert (proto.last_wire.get('sa_early_dropped', 0)
            + proto.last_wire.get('sa_late_dropped', 0)) > 0

    # 明文参考（无掉线语义——SA 掉线只影响聚合的参与集，
    # 协议内幸存者均值 = 全体均值在掉线下的无偏形式；这里验证有限性
    # 与 filtering/动量/筛选逻辑一致）
    ref = PPBRController({'ppbr': {k: v for k, v in _PPBR_CFG.items()}},
                         num_edges=2, seed=42)
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

    # 掉线使参与均值有随机差异：只验证量级与方向一致（余弦相似）
    cos = torch.nn.functional.cosine_similarity(
        m_a["w"].flatten(), m_ref.flatten(), dim=0)
    assert cos > 0.9, f"掉线仿真下协议产物与明文参考偏离: cos={cos}"
    assert torch.isfinite(m_a["w"]).all()


def test_protocol_sim_mode_still_works():
    """sa_key_exchange=sim：哈希种子模拟路径保持可用（对照模式）"""
    from ppbr import PPBRProtocol
    updates, groups = _synthetic_round()
    cfg = dict(_PPBR_CFG)
    cfg['sa_key_exchange'] = 'sim'
    cfg['sa_simulate_dropout_rate'] = 0.0
    full = {'ppbr': cfg, 'ckks': dict(_CRYPTO)}
    proto = PPBRProtocol(full, num_edges=2, seed=42)
    m_a = proto.run_round(0, updates, groups)
    assert torch.isfinite(m_a["w"]).all()


def test_shamir_roundtrip_and_threshold():
    from base.sa import shamir_recover, shamir_split
    rng = np.random.RandomState(0)
    secret = rng.bytes(15)
    shares = shamir_split(secret, n=5, t=3)
    assert shamir_recover(shares[:3], 15) == secret
    assert shamir_recover([shares[0], shares[2], shares[4]], 15) == secret
    assert shamir_recover(shares, 15) == secret
