"""
AThFHE 测试（USENIX Sec'25 ATASSES 两扩展方案：分簇层次 / 双服务器）
====================================================================

运行方式：
    python -m pytest tests/test_athfhe.py -v          # 单元测试（快）
    python -m pytest tests/test_athfhe.py -v -m slow  # 轨道端到端

覆盖：
  1. 环运算：poly_add/poly_mul/center_lift mod (Q, X^M+1) 与朴素多项式乘法对拍；
  2. 外层玩具 CKKS：加解密、加法同态、精度 |ĝ−g|∞ < 1e-6（Δ=2^40）；
  3. Shamir：(t,n) 分片/重构（任意 t 子集）+ 簇间加法拆分 × 簇内 Shamir 两级重构；
  4. 方案一端到端：误差 <1e-6、T1≠T2 异步掉线、内层精确性（底噪 100% 消除）、
     Q' 参数界校验（不够大必须 raise）、CH 视图掩码界 |T1|·B_sm；
  5. 方案二端到端：误差 <1e-6、掉线 |T|≥T、精确掩码模 Q 恒等、S_A 无种子解密
     失败（错误 dk 解出垃圾）、S_H 视图只含种子与名单；
  6. 轨道集成（slow）：tiny config 跑 run_experiment，test_acc / athfhe 成本
     指标 / 模型参数更新。
"""

import numpy as np
import pytest
import torch

from athfhe.ckks_toy import shamir_recover

# ---- 小编码参数（快而精确：M=8、61-bit 素数 Q、Δ=2^30）----
M_SMALL = 8
Q_BITS = 61
DELTA_SMALL = 2 ** 30


def _naive_poly_mul(a, b, q, n):
    """朴素 negacyclic 乘法（教科书定义，对拍用）"""
    a = [int(x) % q for x in a]
    b = [int(x) % q for x in b]
    res = [0] * n
    for i in range(n):
        for j in range(n):
            v = a[i] * b[j]
            k = i + j
            if k >= n:
                res[k - n] -= v      # x^n ≡ −1
            else:
                res[k] += v
    return np.array([r % q for r in res], dtype=object)


# =====================================================================
# 1. 环运算
# =====================================================================
def test_ring_poly_mul_matches_naive():
    from athfhe.ckks_toy import RingContext, next_prime
    q = next_prime(1 << Q_BITS)
    ctx = RingContext(q, M_SMALL)
    rng = np.random.RandomState(0)
    for _ in range(5):
        a = ctx.uniform_poly(rng)
        b = ctx.uniform_poly(rng)
        got = np.asarray(ctx.poly_mul(a, b), dtype=object)
        want = _naive_poly_mul(a, b, q, M_SMALL)
        assert np.array_equal(got % q, want % q), "poly_mul 与朴素乘法不一致"


def test_ring_poly_add_and_center_lift():
    from athfhe.ckks_toy import RingContext, next_prime
    q = next_prime(1 << 20)          # 小模数验证 object/int64 双路径
    ctx = RingContext(q, M_SMALL)
    rng = np.random.RandomState(1)
    a = ctx.uniform_poly(rng)
    b = ctx.uniform_poly(rng)
    s = ctx.poly_add(a, b)
    assert np.array_equal(np.asarray(s, dtype=object) % q,
                          (np.asarray(a, dtype=object)
                           + np.asarray(b, dtype=object)) % q)
    # center_lift：约化到 (−Q/2, Q/2]
    lifted = ctx.center_lift(s)
    vals = np.asarray(lifted, dtype=object)
    assert np.all(vals <= q // 2) and np.all(vals > -q // 2)
    assert np.array_equal(vals % q, np.asarray(s, dtype=object) % q)


def test_ring_poly_mul_big_modulus_object_dtype():
    """大模数（>2^62）走 object dtype 大整数路径，仍与朴素乘法一致"""
    from athfhe.ckks_toy import RingContext, next_prime
    q = next_prime(1 << 140)         # 方案一内层模数量级
    ctx = RingContext(q, M_SMALL)
    assert ctx.dtype is object
    rng = np.random.RandomState(2)
    a = ctx.uniform_poly(rng)
    b = ctx.uniform_poly(rng)
    got = np.asarray(ctx.poly_mul(a, b), dtype=object)
    want = _naive_poly_mul(a, b, q, M_SMALL)
    assert np.array_equal(got % q, want % q)


# =====================================================================
# 2. 外层玩具 CKKS
# =====================================================================
def test_outer_ckks_encrypt_decrypt_and_precision():
    from athfhe.ckks_toy import (RingContext, next_prime, outer_keygen,
                                 outer_encrypt, outer_decrypt_core)
    ctx = RingContext(next_prime(1 << Q_BITS), M_SMALL)
    rng = np.random.RandomState(3)
    S, PK = outer_keygen(ctx, rng)
    g = rng.uniform(-1, 1, size=M_SMALL // 2)
    ct = outer_encrypt(ctx, PK, g, DELTA_SMALL, rng)
    dec = outer_decrypt_core(ctx, S, ct)[:M_SMALL // 2] / DELTA_SMALL
    assert np.max(np.abs(dec - g)) < 1e-6, f"解密误差过大: {np.max(np.abs(dec - g))}"


def test_outer_ckks_additive_homomorphism():
    from athfhe.ckks_toy import (RingContext, next_prime, outer_keygen,
                                 outer_encrypt, outer_add, outer_decrypt_core)
    ctx = RingContext(next_prime(1 << Q_BITS), M_SMALL)
    rng = np.random.RandomState(4)
    S, PK = outer_keygen(ctx, rng)
    g1 = rng.uniform(-0.5, 0.5, size=M_SMALL // 2)
    g2 = rng.uniform(-0.5, 0.5, size=M_SMALL // 2)
    ct = outer_add(ctx, outer_encrypt(ctx, PK, g1, DELTA_SMALL, rng),
                   outer_encrypt(ctx, PK, g2, DELTA_SMALL, rng))
    dec = outer_decrypt_core(ctx, S, ct)[:M_SMALL // 2] / DELTA_SMALL
    assert np.max(np.abs(dec - (g1 + g2))) < 1e-6, "加法同态误差过大"


def test_outer_ckks_precision_delta_40():
    """Δ=2^40 时精度 |ĝ−g|∞ < 1e-6（规格要求）"""
    from athfhe.ckks_toy import (RingContext, next_prime, outer_keygen,
                                 outer_encrypt, outer_decrypt_core)
    ctx = RingContext(next_prime(1 << Q_BITS), M_SMALL)
    rng = np.random.RandomState(5)
    S, PK = outer_keygen(ctx, rng)
    g = rng.uniform(-1, 1, size=M_SMALL // 2)
    ct = outer_encrypt(ctx, PK, g, 2 ** 40, rng)
    dec = outer_decrypt_core(ctx, S, ct)[:M_SMALL // 2] / (2 ** 40)
    assert np.max(np.abs(dec - g)) < 1e-6


# =====================================================================
# 3. Shamir：分片/重构 + 簇间加法拆分两级重构
# =====================================================================
def test_shamir_share_recover_arbitrary_subsets():
    from athfhe.ckks_toy import (RingContext, next_prime, shamir_share,
                                 shamir_recover)
    ctx = RingContext(next_prime(1 << Q_BITS), M_SMALL)
    rng = np.random.RandomState(6)
    secret = ctx.uniform_poly(rng)
    n, t = 5, 3
    shares = shamir_share(ctx, secret, n, t, rng)
    # 任意 ≥t 子集均可重构
    for subset in ([0, 1, 2], [0, 2, 4], [1, 2, 3, 4], [0, 1, 2, 3, 4]):
        rec = shamir_recover(ctx, [shares[i] for i in subset])
        assert np.array_equal(np.asarray(rec, dtype=object) % ctx.q,
                              np.asarray(secret, dtype=object) % ctx.q), \
            f"子集 {subset} 重构失败"


def test_two_level_additive_plus_shamir_recovers_S():
    """簇间加法拆分 + 簇内 Shamir 的两级重构：Σ_c Σ_i L·s_{c,i} = S"""
    from athfhe.hier import HierATHFHE
    proto = HierATHFHE(num_clusters=2, cluster_threshold=2,
                       ring_degree=M_SMALL, outer_modulus_bits=Q_BITS,
                       scale_bits=30, inner_noise_bits=8, smudge_bits=4,
                       cluster_sizes=[3, 3], seed=7)
    acc = np.zeros(M_SMALL, dtype=object)
    for c in range(proto.C):
        # 每簇任取 ≥t_c 子集，用簇内 Lagrange 重构 S^(c)
        shares = [(i + 1, proto.shares[c][i]) for i in (0, 2)]
        rec = np.asarray(shamir_recover(proto.ctx_outer, shares), dtype=object)
        acc = (acc + rec) % proto.ctx_outer.q
    assert np.array_equal(acc % proto.ctx_outer.q,
                          np.asarray(proto.S, dtype=object) % proto.ctx_outer.q), \
        "两级重构 Σ_c Σ_i L·s_{c,i} ≠ S"


# =====================================================================
# 4. 方案一（分簇层次）端到端
# =====================================================================
def _hier_proto(seed=11, **kw):
    from athfhe.hier import HierATHFHE
    args = dict(num_clusters=2, cluster_threshold=2, ring_degree=M_SMALL,
                outer_modulus_bits=Q_BITS, scale_bits=30, inner_noise_bits=8,
                smudge_bits=4, cluster_sizes=[3, 3], seed=seed)
    args.update(kw)
    return HierATHFHE(**args)


def _grad_chunks(n_clients, n_blocks, seed=0):
    rng = np.random.RandomState(seed)
    return [[rng.uniform(-0.5, 0.5, size=M_SMALL // 2)
             for _ in range(n_blocks)] for _ in range(n_clients)]


def test_hier_end_to_end_error_below_1e6():
    proto = _hier_proto()
    chunks = _grad_chunks(6, 2, seed=1)
    agg, cost = proto.run_protocol(chunks)
    assert len(agg) == 2
    expected = [sum(chunks[c][b] for c in range(6)) for b in range(2)]
    for b in range(2):
        err = np.max(np.abs(agg[b] - expected[b]))
        assert err < 1e-6, f"块 {b} 聚合误差 {err}"
    for key in ('athfhe_setup_bytes', 'athfhe_upload_bytes',
                'athfhe_inner_bytes', 'athfhe_crypto_time'):
        assert key in cost and cost[key] >= 0


def test_hier_async_dropout_T1_neq_T2():
    """T1≠T2 异步掉线：两回合在线集不同，每簇 ≥t_c 即可正确解密"""
    proto = _hier_proto(seed=12)
    chunks = _grad_chunks(6, 1, seed=2)
    online1 = [0, 1, 3, 4]
    online2 = [1, 2, 4, 5]
    agg, _ = proto.run_protocol(chunks, online1=online1, online2=online2)
    expected = sum(chunks[c][0] for c in online1)
    assert np.max(np.abs(agg[0] - expected)) < 1e-6


def test_hier_inner_decryption_exact_noise_eliminated():
    """内层精确性：b'_c − (c1·S^(c) + Σn) = 0 mod Q（底噪 100% 消除）"""
    proto = _hier_proto(seed=13)
    chunks = _grad_chunks(6, 1, seed=3)
    ct = None
    from athfhe.ckks_toy import outer_add
    for g in range(6):
        ct_i = proto.outer_encrypt_block(chunks[g][0])
        ct = ct_i if ct is None else outer_add(proto.ctx_outer, ct, ct_i)
    _, c1 = ct
    for c in range(proto.C):
        online1 = [0, 1, 2]   # 簇本地索引（两簇规模相同）
        view = proto.cluster_r1(c, c1, online1)
        b_c, _ = proto.cluster_r2(c, view, online1)
        # 期望：c1·S^(c) + Σ_{i∈T1} n_{c,i} (mod Q)
        want = proto.ctx_outer.poly_mul(c1, proto.S_clusters[c])
        acc = np.asarray(want, dtype=object)
        for i in online1:
            acc = (acc + np.asarray(view['clients'][i]['n'], dtype=object)) \
                % proto.ctx_outer.q
        assert np.array_equal(np.asarray(b_c, dtype=object) % proto.ctx_outer.q,
                              acc % proto.ctx_outer.q), \
            f"簇 {c} 内层解密不精确（底噪未 100% 消除）"


def test_hier_inner_modulus_bound_enforced():
    """Q' 约束校验：故意给不够大的 Q' 必须 raise ValueError"""
    from athfhe.hier import HierATHFHE
    with pytest.raises(ValueError, match="参数界"):
        HierATHFHE(num_clusters=2, cluster_threshold=2, ring_degree=M_SMALL,
                   outer_modulus_bits=Q_BITS, scale_bits=30, inner_noise_bits=8,
                   smudge_bits=4, cluster_sizes=[2, 2], seed=14,
                   q_prime_bits=64)   # 2^64 ≪ 2·n_c·Q²·B'


def test_hier_cluster_head_view_smudge_bounded():
    """安全冒烟：CH 视图 b'_c 与 c1·S^(c) 之差 = Σn 且界 |T1|·B_sm"""
    proto = _hier_proto(seed=15)
    chunks = _grad_chunks(6, 1, seed=5)
    ct = None
    from athfhe.ckks_toy import outer_add
    for g in range(6):
        ct_i = proto.outer_encrypt_block(chunks[g][0])
        ct = ct_i if ct is None else outer_add(proto.ctx_outer, ct, ct_i)
    _, c1 = ct
    for c in range(proto.C):
        online1 = [0, 1, 2]   # 簇本地索引（两簇规模相同）
        view = proto.cluster_r1(c, c1, online1)
        b_c, _ = proto.cluster_r2(c, view, list(online1))
        diff = proto.ctx_outer.center_lift(
            (np.asarray(b_c, dtype=object)
             - np.asarray(proto.ctx_outer.poly_mul(c1, proto.S_clusters[c]),
                          dtype=object)) % proto.ctx_outer.q)
        diff = np.asarray(diff, dtype=object).astype(np.int64)
        # 差值恰为掩码噪声之和，无穷范数 ≤ |T1|·B_sm
        assert np.max(np.abs(diff)) <= len(online1) * proto.B_sm, \
            f"簇 {c} CH 视图掩码越界: {np.max(np.abs(diff))}"
        n_sum = np.zeros(M_SMALL, dtype=np.int64)
        for i in online1:
            n_sum += view['clients'][i]['n']
        assert np.array_equal(diff, n_sum), "CH 视图差值应精确等于 Σn"


# =====================================================================
# 5. 方案二（双服务器）端到端
# =====================================================================
def _twin_proto(seed=21, **kw):
    from athfhe.twin import TwinATHFHE
    args = dict(threshold=3, ring_degree=M_SMALL, outer_modulus_bits=Q_BITS,
                scale_bits=30, smudge_bits=4, num_clients=4, seed=seed)
    args.update(kw)
    return TwinATHFHE(**args)


def test_twin_end_to_end_error_below_1e6():
    proto = _twin_proto()
    chunks = _grad_chunks(4, 2, seed=6)
    agg, cost = proto.run_protocol(chunks)
    expected = [sum(chunks[c][b] for c in range(4)) for b in range(2)]
    for b in range(2):
        assert np.max(np.abs(agg[b] - expected[b])) < 1e-6
    assert cost['athfhe_seed_bytes'] == 2 * 4 * 32, "种子信道应记录 32B/客户端/块"


def test_twin_dropout_resilience():
    proto = _twin_proto(seed=22)
    chunks = _grad_chunks(4, 1, seed=7)
    online = [0, 2, 3]
    agg, _ = proto.run_protocol(chunks, online=online)
    expected = sum(chunks[c][0] for c in online)
    assert np.max(np.abs(agg[0] - expected)) < 1e-6
    with pytest.raises(ValueError, match="门限"):
        proto.run_protocol(chunks, online=[0, 2])


def test_twin_exact_mask_identity_mod_Q():
    """精确掩码验证：b' = C'_all,0 + (−a'u')·dk ≡ c1·S + Σn (mod Q) 恒等"""
    from athfhe.ckks_toy import outer_add
    proto = _twin_proto(seed=23)
    chunks = _grad_chunks(4, 1, seed=8)
    online = [0, 1, 3]
    ct = None
    for g in online:
        ct_i = proto.outer_encrypt_block(chunks[g][0])
        ct = ct_i if ct is None else outer_add(proto.ctx, ct, ct_i)
    c0, c1 = ct
    a_, u_ = proto.new_crs()
    neg_au = proto.ctx.poly_neg(proto.ctx.poly_mul(a_, u_))
    ct0s, noise_sum = {}, None
    for g in online:
        (c_b0, c_n0), seeds, n_i, _b = proto.client_one_shot(
            c1, proto.client_shares[g], a_, u_)
        ct0s[g] = (c_b0, c_n0)
        proto.sh_view['seeds'][g] = seeds
        noise_sum = n_i if noise_sum is None else proto.ctx.poly_add(noise_sum, n_i)
    dk, rec = proto.twin_server_coop(online, ct0s)
    b = proto.ctx.poly_add(rec['c_all0'], proto.ctx.poly_mul(neg_au, dk))
    want = proto.ctx.poly_add(proto.ctx.poly_mul(c1, proto.S), noise_sum)
    assert np.array_equal(np.asarray(b, dtype=object) % proto.ctx.q,
                          np.asarray(want, dtype=object) % proto.ctx.q), \
        "b' 与 c1·S+Σn 模 Q 不恒等（掩码未精确消元）"


def test_twin_wrong_dk_decrypts_garbage():
    """S_A 无种子：用错误 dk 解出垃圾，不等于真值"""
    from athfhe.ckks_toy import outer_add
    proto = _twin_proto(seed=24)
    chunks = _grad_chunks(4, 1, seed=9)
    online = [0, 1, 2, 3]
    ct = None
    for g in online:
        ct_i = proto.outer_encrypt_block(chunks[g][0])
        ct = ct_i if ct is None else outer_add(proto.ctx, ct, ct_i)
    c0, c1 = ct
    a_, u_ = proto.new_crs()
    neg_au = proto.ctx.poly_neg(proto.ctx.poly_mul(a_, u_))
    ct0s = {}
    for g in online:
        (c_b0, c_n0), seeds, _n, _b = proto.client_one_shot(
            c1, proto.client_shares[g], a_, u_)
        ct0s[g] = (c_b0, c_n0)
        proto.sh_view['seeds'][g] = seeds
    _dk, rec = proto.twin_server_coop(online, ct0s)
    expected = sum(chunks[g][0] for g in online)
    # 攻击者视角：S_A 只有密文、没有种子——随机猜一个 dk
    rng = np.random.RandomState(99)
    wrong_dk = proto.ctx.uniform_poly(rng)
    dec_bad = proto.final_decode(c0, rec['c_all0'], neg_au, wrong_dk)
    assert not np.allclose(dec_bad[:M_SMALL // 2], expected, atol=1e-3), \
        "错误 dk 竟解出真值——掩码失效"
    # 对照：正确 dk（S_H 视角）精确还原
    dk_good, rec2 = proto.twin_server_coop(online, ct0s)
    dec_good = proto.final_decode(c0, rec2['c_all0'], neg_au, dk_good)
    assert np.max(np.abs(dec_good[:M_SMALL // 2] - expected)) < 1e-6


def test_twin_helper_view_only_seeds_and_roster():
    """S_H 视图只含种子与名单（无任何密文/份额/掩码）"""
    proto = _twin_proto(seed=25)
    chunks = _grad_chunks(4, 1, seed=10)
    proto.run_protocol(chunks, online=[0, 1, 2])
    assert set(proto.sh_view.keys()) <= {'seeds', 'rosters'}
    for sd1, sd2 in (s for s in proto.sh_view['seeds'] if s is not None):
        assert len(sd1) == 16 and len(sd2) == 16
    assert proto.sh_view['rosters'] and len(proto.sh_view['rosters'][0]) == 3


# =====================================================================
# 6. 轨道集成（slow）
# =====================================================================
def _write_cfg(tmp_path, track='athfhe_twin', extra=None):
    import yaml
    with open('configs/config.yaml', 'r', encoding='utf-8') as f:
        cfg = yaml.safe_load(f)
    cfg['system']['num_clients'] = 4
    cfg['system']['num_rounds'] = 1
    cfg['system']['device'] = 'cpu'
    cfg['system']['num_epochs'] = 1
    cfg['system']['batch_size'] = 32
    cfg['data']['dataset'] = 'mnist'
    cfg['data']['iid'] = True
    cfg['model']['name'] = 'mlp'
    cfg['model']['hidden_dims'] = [16]        # 小模型：切块数 = 参数量/32 可控
    cfg['attack']['enabled'] = False
    if track == 'athfhe_twin':
        cfg['athfhe_twin'] = {
            'enabled': True, 'threshold': 3, 'ring_degree': 64,
            'outer_modulus_bits': 48, 'scale_bits': 30, 'smudge_bits': 6,
            'dropout': 0}
    else:
        cfg['athfhe_hier'] = {
            'enabled': True, 'num_clusters': 2, 'cluster_threshold': 2,
            'ring_degree': 64, 'outer_modulus_bits': 48, 'scale_bits': 30,
            'inner_noise_bits': 10, 'smudge_bits': 6, 'dropout': 0}
    if extra:
        extra(cfg)
    path = tmp_path / f'{track}_config.yaml'
    with open(path, 'w', encoding='utf-8') as f:
        yaml.safe_dump(cfg, f, allow_unicode=True)
    return str(path)


@pytest.mark.slow
def test_track_twin_end_to_end(tmp_path):
    """athfhe_twin 轨道：tiny config 跑 run_experiment，指标留痕、参数更新"""
    from main import CKKSFedExperiment
    from base.data import get_model_params
    exp = CKKSFedExperiment(_write_cfg(tmp_path, 'athfhe_twin'),
                            output_dir=str(tmp_path))
    before = get_model_params(exp.server.model)
    exp.run_experiment()
    m = exp.metrics.get_metrics()
    assert m.get('test_acc'), "test_acc 指标未记录"
    for key in ('athfhe_setup_bytes', 'athfhe_upload_bytes',
                'athfhe_inner_bytes', 'athfhe_seed_bytes', 'athfhe_crypto_time'):
        assert m.get(key), f"athfhe 成本指标 {key} 未记录"
    after = get_model_params(exp.server.model)
    changed = any(not torch.allclose(before[k], after[k]) for k in before)
    assert changed, "全局模型参数未更新"


@pytest.mark.slow
def test_track_hier_end_to_end(tmp_path):
    """athfhe_hier 轨道：tiny config 跑 run_experiment，指标留痕、参数更新"""
    from main import CKKSFedExperiment
    from base.data import get_model_params
    exp = CKKSFedExperiment(_write_cfg(tmp_path, 'athfhe_hier'),
                            output_dir=str(tmp_path))
    before = get_model_params(exp.server.model)
    exp.run_experiment()
    m = exp.metrics.get_metrics()
    assert m.get('test_acc'), "test_acc 指标未记录"
    for key in ('athfhe_setup_bytes', 'athfhe_upload_bytes',
                'athfhe_inner_bytes', 'athfhe_crypto_time'):
        assert m.get(key), f"athfhe 成本指标 {key} 未记录"
    after = get_model_params(exp.server.model)
    changed = any(not torch.allclose(before[k], after[k]) for k in before)
    assert changed, "全局模型参数未更新"


@pytest.mark.slow
def test_track_conflict_raises(tmp_path):
    """轨道互斥：athfhe_twin + athfhe_hier 同开必须拒绝启动"""
    import yaml
    path = _write_cfg(tmp_path, 'athfhe_twin',
                      extra=lambda c: c['athfhe_hier'].update(
                          {'enabled': True, 'num_clusters': 2,
                           'cluster_threshold': 2, 'ring_degree': 64}))
    from main import CKKSFedExperiment
    with pytest.raises(ValueError, match="冲突"):
        CKKSFedExperiment(path, output_dir=str(tmp_path))


# =====================================================================
# 7. 块并行确定性（multiprocessing，Windows spawn 兼容）
# =====================================================================
def test_parallel_matches_serial_twin():
    """twin：parallel(workers=2) 与串行 agg_blocks 逐位一致、cost 各键相等"""
    n_blocks = 10
    chunks = _grad_chunks(4, n_blocks, seed=42)
    serial = _twin_proto(seed=31, parallel=False)
    agg_s, cost_s = serial.run_protocol(chunks)
    par = _twin_proto(seed=31, parallel=True, parallel_workers=2)
    agg_p, cost_p = par.run_protocol(chunks)
    assert len(agg_s) == n_blocks
    for b in range(n_blocks):
        assert np.array_equal(agg_s[b], agg_p[b]), f"块 {b} 并行/串行不一致"
    for k in cost_s:
        if k == 'athfhe_crypto_time':
            continue
        assert cost_s[k] == cost_p[k], f"cost 键 {k} 不一致"


def test_parallel_matches_serial_hier():
    """hier：parallel(workers=2) 与串行 agg_blocks 逐位一致、cost 各键相等"""
    n_blocks = 10
    chunks = _grad_chunks(6, n_blocks, seed=43)
    serial = _hier_proto(seed=32, parallel=False)
    agg_s, cost_s = serial.run_protocol(chunks)
    par = _hier_proto(seed=32, parallel=True, parallel_workers=2)
    agg_p, cost_p = par.run_protocol(chunks)
    for b in range(n_blocks):
        assert np.array_equal(agg_s[b], agg_p[b]), f"块 {b} 并行/串行不一致"
    for k in cost_s:
        if k == 'athfhe_crypto_time':
            continue
        assert cost_s[k] == cost_p[k], f"cost 键 {k} 不一致"


# =====================================================================
# 8. 逐流程通信键完整性
# =====================================================================
def test_twin_per_flow_comm_keys():
    """twin：全部逐流程键存在，且六键之和 == athfhe_comm_total_bytes == 该轮总通信"""
    proto = _twin_proto(seed=33)
    chunks = _grad_chunks(4, 3, seed=44)
    _agg, cost = proto.run_protocol(chunks)
    keys = ('athfhe_comm_p1_upload', 'athfhe_comm_p2_mask',
            'athfhe_comm_p2_seed', 'athfhe_comm_p3_dk',
            'athfhe_comm_p3_list', 'athfhe_comm_p1_broadcast_c1')
    for k in keys:
        assert k in cost and cost[k] > 0, f"逐流程键 {k} 缺失"
    assert sum(cost[k] for k in keys) == cost['athfhe_comm_total_bytes']
    # 兼容聚合键可由细分键推导
    assert cost['athfhe_upload_bytes'] == cost['athfhe_comm_p1_upload']
    assert cost['athfhe_inner_bytes'] == cost['athfhe_comm_p2_mask']
    assert cost['athfhe_seed_bytes'] == cost['athfhe_comm_p2_seed']


def test_hier_per_flow_comm_keys():
    """hier：逐流程键存在且之和 == athfhe_comm_total_bytes"""
    proto = _hier_proto(seed=34)
    chunks = _grad_chunks(6, 3, seed=45)
    _agg, cost = proto.run_protocol(chunks)
    keys = ('athfhe_comm_p1_upload', 'athfhe_comm_p1_broadcast_c1',
            'athfhe_comm_p2_ct', 'athfhe_comm_p2_ek',
            'athfhe_comm_p3_dkshare', 'athfhe_comm_p3_bc')
    for k in keys:
        assert k in cost and cost[k] > 0, f"逐流程键 {k} 缺失"
    assert sum(cost[k] for k in keys) == cost['athfhe_comm_total_bytes']


# =====================================================================
# 9. 论文口径指标（轨道级，slow）
# =====================================================================
@pytest.mark.slow
def test_track_real_ckks_conversion(tmp_path):
    """real_coeff_bits=218：athfhe_comm_real_bytes ≈ 总通信 × 折算系数（1% 容差）"""
    path = _write_cfg(tmp_path, 'athfhe_twin',
                      extra=lambda c: c['athfhe_twin'].update(
                          {'real_coeff_bits': 218}))
    from main import CKKSFedExperiment
    exp = CKKSFedExperiment(path, output_dir=str(tmp_path))
    exp.run_experiment()
    m = exp.metrics.get_metrics()
    total = sum(sum(m.get(k, [])) for k in
                ('athfhe_comm_p1_upload', 'athfhe_comm_p2_mask',
                 'athfhe_comm_p2_seed', 'athfhe_comm_p3_dk',
                 'athfhe_comm_p3_list', 'athfhe_comm_p1_broadcast_c1'))
    real = sum(m['athfhe_comm_real_bytes'])
    # tiny 配置 outer_modulus_bits=48 → Q=49bit：系数 = ceil(218/8)/ceil(49/8)
    # = 28/7 = 4.0（默认 60bit 配置下为 28/8 = 3.5）
    factor = 28 / 7
    assert abs(real - total * factor) / (total * factor) < 0.01
    assert m['athfhe_real_coeff_bits'] == [218]


@pytest.mark.slow
def test_track_straggler_round_time(tmp_path):
    """straggler 指标：total_round_time_simulated > 0 且 ≥ max local_train_time；
    upload_time/download_time ≥ 0"""
    path = _write_cfg(tmp_path, 'athfhe_twin')
    from main import CKKSFedExperiment
    exp = CKKSFedExperiment(path, output_dir=str(tmp_path))
    exp.run_experiment()
    m = exp.metrics.get_metrics()
    sim = m['total_round_time_simulated']
    train = m['local_train_time']
    assert all(t > 0 for t in sim)
    assert all(s >= t for s, t in zip(sim, train))
    assert all(t >= 0 for t in m['upload_time'])
    assert all(t >= 0 for t in m['download_time'])
