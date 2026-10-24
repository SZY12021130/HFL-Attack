"""
RFL 密码原语测试（论文 §V-A：数字签名式 8/9/14/16 + 同态哈希式 13/17）
====================================================================

运行方式：
    python -m pytest tests/test_rfl_crypto.py -v

覆盖：
  1. DigitalSignature：Ed25519 向量签名/验签、篡改拒绝、跨密钥拒绝、整数签名；
  2. HomomorphicHash：乘性同态 H(x+y)=H(x)H(y)、聚合验证式(17) 成立/篡改失败、
     定点量化往返；
  3. dp_sigma：式(7) 噪声尺度 σ=c·d·S2/ε 与灵敏度 S2=2C/|D_u|。
"""

import numpy as np
import pytest


# =====================================================================
# 1. 数字签名（式 8/9/14/16）
# =====================================================================

def test_signature_sign_verify_vector():
    from rfl.crypto import DigitalSignature
    ds = DigitalSignature()
    vec = np.array([0.5, -1.25, 3.0], dtype=np.float64)
    sig = ds.sign_vector(vec, meta=b"r0|e0|c1")
    assert DigitalSignature.verify_vector(
        ds.public_key_bytes, vec, sig, meta=b"r0|e0|c1")


def test_signature_rejects_tampered_vector():
    from rfl.crypto import DigitalSignature
    ds = DigitalSignature()
    vec = np.array([0.5, -1.25, 3.0], dtype=np.float64)
    sig = ds.sign_vector(vec, meta=b"r0")
    # 向量被篡改（CS 替换中间表示）→ 验签失败
    assert not DigitalSignature.verify_vector(
        ds.public_key_bytes, vec * 1.01, sig, meta=b"r0")
    # 元数据被篡改（换轮次/换实体）→ 验签失败
    assert not DigitalSignature.verify_vector(
        ds.public_key_bytes, vec, sig, meta=b"r1")


def test_signature_rejects_wrong_key():
    from rfl.crypto import DigitalSignature
    ds_a, ds_b = DigitalSignature(), DigitalSignature()
    vec = np.array([1.0, 2.0])
    sig = ds_a.sign_vector(vec)
    # 用其他实体的公钥验证 → 失败（身份伪造检测）
    assert not DigitalSignature.verify_vector(ds_b.public_key_bytes, vec, sig)


def test_signature_deterministic_seed():
    from rfl.crypto import DigitalSignature
    a = DigitalSignature(seed=b"rfl-client-42-7")
    b = DigitalSignature(seed=b"rfl-client-42-7")
    assert a.public_key_bytes == b.public_key_bytes


def test_signature_int_hash():
    """式(14)：ES 对同态哈希值 h_v 签名（整数消息）。"""
    from rfl.crypto import DigitalSignature
    ds = DigitalSignature()
    h = 12345678901234567890
    sig = ds.sign_int(h, meta=b"r0|e2")
    assert DigitalSignature.verify_int(ds.public_key_bytes, h, sig, meta=b"r0|e2")
    assert not DigitalSignature.verify_int(ds.public_key_bytes, h + 1, sig,
                                           meta=b"r0|e2")


# =====================================================================
# 2. 同态哈希（式 13/17）
# =====================================================================

@pytest.fixture(scope="module")
def hh():
    from rfl.crypto import HomomorphicHash
    return HomomorphicHash(bits=128, scale=1000, seed=b"rfl-test")


def test_hh_homomorphism(hh):
    """H(qx)⊗H(qy) = H(qx+qy)（乘性同态，式 17 的验证基础）。

    同态性定义在定点量化整数域（协议中式 17 即整数校验：云对量化
    向量 q_v 求和 Σq_v，校验 Π h_v = H(Σq_v)，无浮点误差）。
    """
    rng = np.random.RandomState(0)
    x = rng.uniform(-5, 5, size=37)
    y = rng.uniform(-5, 5, size=37)
    qx, qy = hh.quantize(x), hh.quantize(y)
    lhs = hh.eval([hh.hash_ints(qx), hh.hash_ints(qy)])
    rhs = hh.hash_ints(qx + qy)
    assert lhs == rhs


def test_hh_verify_aggregate_pass_and_fail(hh):
    """式(17)：Π_v H(q_v) ≡ H(Σ_v q_v)；篡改任一边缘贡献即失败。"""
    rng = np.random.RandomState(1)
    qs = [np.rint(rng.uniform(-3, 3, size=50) * hh.scale).astype(np.int64)
          for _ in range(3)]
    hashes = [hh.hash_ints(q) for q in qs]
    S = np.sum(qs, axis=0)
    assert hh.verify_aggregate(hashes, S)
    # 云丢弃/缩放某边缘更新 → 校验失败
    S_tampered = S - qs[0]
    assert not hh.verify_aggregate(hashes, S_tampered)
    S_scaled = S - qs[1] + np.rint(qs[1] * 1.5).astype(np.int64)
    assert not hh.verify_aggregate(hashes, S_scaled)


def test_hh_quantize_roundtrip(hh):
    vec = np.array([0.1234, -5.6789, 0.0])
    ints = hh.quantize(vec)
    back = hh.dequantize(ints)
    assert np.allclose(back, vec, atol=1.0 / hh.scale)


def test_hh_negative_values(hh):
    """负坐标经模 q 指数归约后，整数域同态性保持。"""
    rng = np.random.RandomState(2)
    x = rng.uniform(-10, 10, size=20)
    y = rng.uniform(-10, 10, size=20)
    qx, qy = hh.quantize(x), hh.quantize(y)
    assert hh.eval([hh.hash_ints(qx), hh.hash_ints(qy)]) == hh.hash_ints(qx + qy)


def test_hh_group_is_safe_prime():
    """群参数为安全素数 p=2q+1（仿真参数，测试 128 位快速路径）。"""
    from rfl.crypto import _group_params
    p, q = _group_params(128, b"rfl-check")
    assert q is not None, "128 位安全素数搜索不应触发退化路径"
    assert p == 2 * q + 1


# =====================================================================
# 3. DP 噪声尺度（式 7）
# =====================================================================

def test_dp_sigma_formula():
    """σ_u = c·d·S2/ε，c=√(2ln(1.25/δ))，S2=2C/|D_u|（论文式 7）。"""
    from rfl.splitnn import dp_sigma
    eps, delta, C, n, d = 5.0, 1e-5, 1.0, 250, 64
    c = float(np.sqrt(2.0 * np.log(1.25 / delta)))
    expected = c * d * (2.0 * C / n) / eps
    assert dp_sigma(d, n, eps, delta, C) == pytest.approx(expected)
    # 单调性：数据量越大灵敏度越小 → 噪声越小；ε 越小噪声越大
    assert dp_sigma(d, 2 * n, eps, delta, C) < expected
    assert dp_sigma(d, n, eps / 2, delta, C) > expected
