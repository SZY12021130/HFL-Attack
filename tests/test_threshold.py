"""
门限 CKKS 解密模块测试（纯 CPU，秒级）

覆盖（对应 doc/threshold_ckks.md）：
- sharing：任意长度秘密的分块 Shamir 分片/恢复（§3.2，复用 ppbr/sa.py）
- dkg：Pedersen DKG 的承诺-验证-聚合与门限恢复（§5）
- toy_ckks：教科书 CKKS 真·门限部分解密（§1/§3/§9.1）
- ttp 端到端：项目真实 CKKSContext 密文 → 分片 → 重构 → 解密（§4）
- ThresholdConfig：配置校验（§10.2）
"""

import random

import numpy as np
import pytest
import torch

from threshold import ThresholdConfig, split_secret, recover_secret
from threshold.dkg import (
    DKGParticipant, run_dkg, recover_from_shares, G, H, P,
)
from threshold.toy_ckks import (
    N, toy_keygen, toy_encrypt, toy_decrypt, share_key,
    partial_decrypt, combine, poly_mul,
)


# =====================================================================
# ThresholdConfig 校验
# =====================================================================
class TestThresholdConfig:
    def test_defaults_disabled(self):
        cfg = ThresholdConfig.from_config(None)
        assert cfg.enabled is False and cfg.mode == "ttp"
        assert cfg.t == 5 and cfg.combiner == "client" and cfg.smudging is True

    def test_unknown_keys_ignored(self):
        cfg = ThresholdConfig.from_config({'enabled': True, 't': 3, 'nope': 1})
        assert cfg.enabled is True and cfg.t == 3

    def test_invalid(self):
        with pytest.raises(ValueError):
            ThresholdConfig.from_config({'t': 1})          # t 必须 ≥2
        with pytest.raises(ValueError):
            ThresholdConfig.from_config({'mode': 'x'})     # 非法模式
        with pytest.raises(ValueError):
            ThresholdConfig.from_config({'combiner': 'x'})  # 非法组合器


# =====================================================================
# sharing：分块 Shamir 分片/恢复
# =====================================================================
class TestSharing:
    def test_roundtrip_any_t_of_n(self):
        secret = bytes(range(256)) * 4          # 1024 字节（16 块）
        assert len(secret) == 1024
        blobs = split_secret(secret, n=10, t=4)
        assert len(blobs) == 10
        for combo in [(0, 1, 2, 3), (0, 4, 6, 9), (2, 5, 7, 8), (1, 2, 3, 4)]:
            assert recover_secret([blobs[i] for i in combo]) == secret
        # 超过 t 份同样可以恢复
        assert recover_secret(blobs) == secret

    def test_below_threshold_fails(self):
        secret = b"threshold-sharing-secret"
        blobs = split_secret(secret, n=10, t=4)
        with pytest.raises(ValueError):
            recover_secret([blobs[0], blobs[1], blobs[2]])
        with pytest.raises(ValueError):
            recover_secret([])

    def test_blob_format_and_small_secret(self):
        # 13 字节秘密 = 1 块；blob = 14 + 69 字节头+记录
        blobs = split_secret(b"A" * 13, n=5, t=3)
        assert len(blobs[0]) == 14 + 69
        assert recover_secret(blobs[:3]) == b"A" * 13


# =====================================================================
# dkg：Pedersen DKG（真实数学，教学参数）
# =====================================================================
class TestDKG:
    def _exchange(self, n=6, t=3):
        """手工完成一轮份额交换，返回 (parts, commits, shares)"""
        parts = [DKGParticipant(i + 1, n, t) for i in range(n)]
        commits = {p.pid: p.generate_contribution(random.Random(1000 + i))
                   for i, p in enumerate(parts)}
        shares = {pj.pid: sum(pi.share_for(pj.pid)[0] for pi in parts)
                  for pj in parts}
        return parts, commits, shares

    def test_all_shares_verify(self):
        n, t = 6, 3
        parts, commits, _ = self._exchange(n, t)
        for pj in parts:                    # 每个接收方验证全部发送方份额
            for pi in parts:
                share = pi.share_for(pj.pid)
                assert pj.verify_share(pi.pid, pj.pid, share,
                                       commits[pi.pid])

    def test_tampered_share_fails(self):
        n, t = 6, 3
        parts, commits, _ = self._exchange(n, t)
        s, r = parts[0].share_for(2)
        assert not parts[1].verify_share(1, 2, (s + 1, r), commits[1])
        assert not parts[1].verify_share(1, 2, (s, r + 1), commits[1])

    def test_recover_and_public_key(self):
        n, t = 6, 3
        parts, commits, shares = self._exchange(n, t)
        secret = sum(p._f_coeffs[0] for p in parts)      # Σ_i a_i0（测试断言）
        # 集体公钥 = ∏ g^{a_i0}（各参与方只公布 g^{a_i0}）
        public = 1
        for p in parts:
            pk_i = p.commits[0] * pow(pow(H, p._b_coeffs[0], P), P - 2, P) % P
            public = public * pk_i % P
        for subset in [(1, 2, 3), (2, 4, 6), (1, 3, 5)]:
            rec = recover_from_shares({k: shares[k] for k in subset}, t)
            assert rec == secret                      # 任意 t 份恢复同一 secret
            assert pow(G, rec, P) == public           # g^secret == 集体公钥

    def test_below_threshold_fails(self):
        _, _, shares = self._exchange()
        with pytest.raises(ValueError):
            recover_from_shares({1: shares[1], 2: shares[2]}, 3)

    def test_run_dkg_interface(self):
        res = run_dkg(6, 3, seed=42)
        assert res.participant_ids == [1, 2, 3, 4, 5, 6]
        assert len(res.shares) == 6 and len(res.commits) == 6
        s1 = recover_from_shares({k: res.shares[k] for k in (1, 2, 3)}, 3)
        s2 = recover_from_shares({k: res.shares[k] for k in (4, 5, 6)}, 3)
        assert s1 == s2
        assert pow(G, s1, P) == res.public


# =====================================================================
# toy_ckks：教科书 CKKS + 真·门限部分解密
# =====================================================================
class TestToyCKKS:
    def test_poly_mul_negacyclic(self):
        """与朴素 negacyclic 卷积对拍（小环）"""
        rng = np.random.RandomState(0)
        n_s, q_s = 64, 2 ** 59 - 41
        a = rng.randint(0, q_s, n_s, dtype=np.int64)
        b = rng.randint(0, q_s, n_s, dtype=np.int64)
        got = poly_mul(a, b, q_s, n_s)
        conv = np.convolve(a.astype(object), b.astype(object))
        want = conv[:n_s].copy()
        want[:n_s - 1] -= conv[n_s:2 * n_s - 1]
        assert np.array_equal(got, np.asarray(want % q_s, dtype=np.int64))

    def test_full_decryption(self):
        rng = np.random.RandomState(7)
        sk, pk = toy_keygen(rng=rng)
        m = rng.uniform(-1.0, 1.0, size=N)      # 明文幅值 1.0
        ct = toy_encrypt(pk, m, rng=rng)
        d = toy_decrypt(sk, ct)
        assert np.max(np.abs(d - m)) < 5e-3

    def test_threshold_decryption(self):
        rng = np.random.RandomState(7)
        sk, pk = toy_keygen(rng=rng)
        m = rng.uniform(-1.0, 1.0, size=N)
        ct = toy_encrypt(pk, m, rng=rng)
        n_p, t = 5, 3
        shares = share_key(sk, n_p, t, rng=np.random.RandomState(1))
        xs = [x for x, _ in shares[:t]]

        # smudging=True：t 份部分解密组合仍精确
        parts = [partial_decrypt(ct, s, rng=np.random.RandomState(i),
                                 smudging=True)
                 for i, (_, s) in enumerate(shares[:t])]
        rec = combine(ct[0], parts, xs)
        assert np.max(np.abs(rec - m)) < 2e-2

        # smudging=False：真值路径误差更小
        parts_ns = [partial_decrypt(ct, s, rng=np.random.RandomState(i),
                                    smudging=False)
                    for i, (_, s) in enumerate(shares[:t])]
        rec_ns = combine(ct[0], parts_ns, xs)
        assert np.max(np.abs(rec_ns - m)) < 5e-3

    def test_below_threshold_explodes(self):
        """t−1 份组合 = 伪随机噪声（Shamir 信息论安全）"""
        rng = np.random.RandomState(7)
        sk, pk = toy_keygen(rng=rng)
        m = rng.uniform(-1.0, 1.0, size=N)
        ct = toy_encrypt(pk, m, rng=rng)
        shares = share_key(sk, 5, 3, rng=np.random.RandomState(1))
        parts = [partial_decrypt(ct, s, rng=np.random.RandomState(i))
                 for i, (_, s) in enumerate(shares[:2])]
        rec = combine(ct[0], parts, [x for x, _ in shares[:2]])
        assert np.max(np.abs(rec - m)) > 1.0


# =====================================================================
# ttp 端到端：项目真实 CKKSContext
# =====================================================================
class TestTTPEndToEnd:
    def test_split_recover_decrypt(self):
        from ckks.crypto import CKKSContext, CKKSManager

        ckks_cfg = {'poly_modulus_degree': 4096,
                    'coeff_mod_bit_sizes': [40, 20, 40],
                    'global_scale': 2 ** 20,
                    'slot_packing': True}
        context = CKKSContext(
            poly_modulus_degree=4096,
            coeff_mod_bit_sizes=[40, 20, 40],
            global_scale=2 ** 20)
        manager = CKKSManager(ckks_cfg, context=context)

        # 加密一个随机参数向量（__sparse__ 格式，模拟服务器全局模型）
        params = {'layer.weight': torch.randn(32, 16),
                  'layer.bias': torch.randn(32)}
        enc = manager.encrypt_model_update(params, skip_sparsification=True)

        # TTP：分片 → 任取 t=3 份重构一次性上下文 → 解密
        full_bytes = context.get_full_context_bytes()
        blobs = split_secret(full_bytes, n=5, t=3)
        recovered = recover_secret([blobs[0], blobs[2], blobs[4]])
        ctx2 = CKKSContext.from_full_bytes(recovered,
                                           poly_modulus_degree=4096)
        manager2 = CKKSManager(ckks_cfg, context=ctx2)
        dec = manager2.decrypt_model_update(enc, None)

        for name in params:
            err = (dec[name] - params[name]).abs().max().item()
            scale = params[name].abs().max().item()
            assert err / max(scale, 1e-6) < 1e-2, \
                f"{name}: 相对误差 {err / scale:.2e} 超界"

    def test_ceremony_ttp_stats(self):
        """ThresholdDecryptor（ttp）端到端：解密形状正确 + 统计字段齐全"""
        from ckks.crypto import CKKSContext, CKKSManager
        from threshold import ThresholdDecryptor

        ckks_cfg = {'poly_modulus_degree': 4096,
                    'coeff_mod_bit_sizes': [40, 20, 40],
                    'global_scale': 2 ** 20,
                    'slot_packing': True}
        context = CKKSContext(4096, [40, 20, 40], 2 ** 20)
        manager = CKKSManager(ckks_cfg, context=context)
        params = {'w': torch.randn(64)}
        enc = manager.encrypt_model_update(params, skip_sparsification=True)

        for combiner in ('client', 'server'):
            cfg = ThresholdConfig(enabled=True, mode='ttp', t=3,
                                  combiner=combiner)
            dec = ThresholdDecryptor(cfg, num_clients=5,
                                     full_context_bytes=context.get_full_context_bytes(),
                                     poly_modulus_degree=4096)
            plain, stats = dec.decrypt_global_params(enc, [0, 2, 4])
            assert plain['w'].shape == params['w'].shape
            assert (plain['w'] - params['w']).abs().max().item() < 1e-2
            assert stats['mode'] == 'ttp' and stats['combiner'] == combiner
            assert stats['t'] == 3 and stats['num_participants'] == 3
            assert stats['ideal'] is False
            assert stats['dist_bytes_per_client'] > 0
            assert stats['ceremony_bytes_per_round'] >= \
                3 * stats['dist_bytes_per_client']
            assert stats['ceremony_time_sec'] >= 0.0

    def test_ceremony_dkg_ideal(self):
        """ThresholdDecryptor（dkg）：理想解密 + 统计标 ideal=True"""
        from ckks.crypto import CKKSContext, CKKSManager
        from threshold import ThresholdDecryptor

        ckks_cfg = {'poly_modulus_degree': 4096,
                    'coeff_mod_bit_sizes': [40, 20, 40],
                    'global_scale': 2 ** 20,
                    'slot_packing': True}
        context = CKKSContext(4096, [40, 20, 40], 2 ** 20)
        manager = CKKSManager(ckks_cfg, context=context)
        params = {'w': torch.randn(64)}
        enc = manager.encrypt_model_update(params, skip_sparsification=True)

        cfg = ThresholdConfig(enabled=True, mode='dkg', t=3)
        dec = ThresholdDecryptor(cfg, num_clients=5,
                                 full_context_bytes=context.get_full_context_bytes(),
                                 poly_modulus_degree=4096)
        plain, stats = dec.decrypt_global_params(enc, [1, 3])
        assert (plain['w'] - params['w']).abs().max().item() < 1e-2
        assert stats['ideal'] is True and stats['num_participants'] == 2
        assert stats['dist_bytes_per_client'] == 4 * (66 + 66) + 3 * 66
