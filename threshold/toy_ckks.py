"""
教科书单模数 CKKS + 门限部分解密演示（numpy，仅 CPU）
========================================================

玩具参数（教学用，无安全等级，见 doc/threshold_ckks.md §1/§9）：
- 素数模数 q = 2^60 − 93（已验证素性；q 为素数时 Lagrange 分母天然可逆。
  若用复合模数，需保证其素因子均 > n，否则 x_i − x_j 可能不可逆）；
- 分圆多项式 x^N + 1，N = 4096（negacyclic 环 R_q）；
- 缩放 Δ = 2^30；加密/密钥噪声 σ ≈ 3.2（取整高斯近似）；
- 明文直接编码为系数（跳过 canonical embedding 的 N/2 复数 slot 视角，
  解密代数完全一致：c0 + c1·s ≡ Δ·m + e  (mod q, x^N+1)）。

方案（仅加性同态，够演示解密代数的线性性）：
    sk = s（小系数多项式）
    pk = (b, a)，b = −a·s + e mod (q, x^N+1)，a 均匀随机
    Enc(pk, m):  u ← {0,±1} 稀疏、 e1,e2 小噪声
                 c0 = b·u + e1 + round(Δ·m)
                 c1 = a·u + e2
    Dec(sk, ct): m ≈ center_lift(c0 + c1·s) / Δ

门限解密（Shamir (t,n) 分片私钥，doc §3.2）：
    share_key:       s 的每个系数做 mod-q Shamir (t,n)，参与方 P_i 持份额
                     多项式 s_i，满足 Σ λ_i·s_i ≡ s (mod q)
    partial_decrypt: d_i = c1·s_i + e'_i（smudging 噪声 e'_i 不可省，doc §9.1）
    combine:         m ≈ center_lift(c0 + Σ λ_i·d_i) / Δ，λ_i 为 x=0 处
                     Lagrange 系数（要求素数 q > n，分母可逆）

⚠️ smudging 噪声尺度 σ' 的取舍：解码噪声预算是 Δ/2 ≈ 2^29（系数单位），
教科书约束要求 t 份 smudging 噪声组合后仍 ≪ 预算。此处取 σ' = 2^20：
t=5 时组合标准差 ≈ √5·2^20 ≈ 2^22.2 ≈ 预算的 1/180，明文域误差 ~2e-3，
既满足"组合后仍远低于预算"，又使单个 d_i 的 smudging 噪声统计上主导。
（若机械地取预算的 1/4 = 2^27，t 份组合后 ≈ √t·2^27 将吃掉整个解码
余量，密文直接解码失败。）
"""

from math import gcd
from typing import List, Optional, Sequence, Tuple

import numpy as np

# ---- 玩具参数 ----
Q = 1152921504606846883   # 2^60 − 93（素数，已用 sympy.isprime 验证；≥ 2^59）
N = 4096                  # 分圆多项式次数（mod x^N+1）
DELTA = 2 ** 30           # 明文缩放
SIGMA = 3.2               # 加密/密钥噪声标准差（取整高斯近似）
SMUDGE_SIGMA = 2.0 ** 20  # smudging 噪声标准差（σ'，取舍见模块 docstring）

_LIMB_BITS = 15          # 15-bit limb：limb 卷积部分积和 ≤ 4096·2^30 = 2^42 < 2^53，
                         # float64 FFT 精确表示
_LIMB = 1 << _LIMB_BITS
_NLIMB = 4               # q < 2^60 → 4 个 15-bit limb 覆盖


# =====================================================================
# 环 Z_q[x]/(x^N+1) 上的多项式运算
# =====================================================================
def _fft_conv(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """朴素卷积（线性卷积，长度 2N−1），FFT 加速"""
    size = 1 << int(np.ceil(np.log2(2 * len(a) - 1)))
    fa = np.fft.rfft(a, size)
    fb = np.fft.rfft(b, size)
    return np.rint(np.fft.irfft(fa * fb, size)[:2 * len(a) - 1]).astype(np.int64)


def poly_mul(a: np.ndarray, b: np.ndarray,
             q: int = Q, n: int = N) -> np.ndarray:
    """
    negacyclic 乘法 mod (q, x^n+1)。
    系数按 15-bit limb 分裂（q < 2^60 → 4 limb），各部分积卷积用 float64
    FFT（精确），进位合并后先做 negacyclic 折叠，再用 Python 大整数 Horner
    约化模 q。（naive O(N^2) 分块卷积同样可接受，测试向量短。）
    """
    a = np.asarray(a, dtype=np.int64) % q
    b = np.asarray(b, dtype=np.int64) % q
    a_limbs = [(a >> (_LIMB_BITS * k)) % _LIMB for k in range(_NLIMB)]
    b_limbs = [(b >> (_LIMB_BITS * k)) % _LIMB for k in range(_NLIMB)]
    # 列 k = Σ_{i+j=k} a_i·b_j（权重 _LIMB^k），长度 2n−1
    cols: List[Optional[np.ndarray]] = [None] * (2 * _NLIMB - 1)
    for i in range(_NLIMB):
        for j in range(_NLIMB):
            k = i + j
            conv = _fft_conv(a_limbs[i], b_limbs[j])
            cols[k] = conv if cols[k] is None else cols[k] + conv
    # 进位传播：任意 a*b < n·q^2 < 2^134，≤ 10 列
    k = 0
    while k < len(cols):
        carry = cols[k] // _LIMB
        cols[k] = cols[k] % _LIMB
        if k + 1 < len(cols):
            cols[k + 1] = cols[k + 1] + carry
        elif np.any(carry):
            cols.append(carry)
        k += 1
    # 每列先做 negacyclic 折叠（x^{n+k} ≡ −x^k；x^{2n−1} 项系数恒为 0），
    # 把长度收到 n——合并与约化都是线性运算，先折叠不改变结果
    folded = []
    for col in cols:
        f = col[:n].copy()
        f[:n - 1] -= col[n:2 * n - 1]
        folded.append(f)
    # 大整数 Horner 合并并 mod q（dtype=object 逐元素，n=4096 时毫秒级）
    vals = np.zeros(n, dtype=object)
    for col in reversed(folded):
        vals = (vals * _LIMB + col.astype(object)) % q
    return np.asarray(vals, dtype=np.int64)


def poly_add(a: np.ndarray, b: np.ndarray, q: int = Q) -> np.ndarray:
    return (a + b) % q


def _sample_gauss_poly(rng: np.random.RandomState, std: float,
                       n: int = N) -> np.ndarray:
    """离散高斯近似（四舍五入；玩具噪声，非精确采样器）"""
    return np.rint(rng.normal(0.0, std, size=n)).astype(np.int64)


def _sample_ternary_sparse(rng: np.random.RandomState,
                           n: int = N) -> np.ndarray:
    """{0,±1} 稀疏采样（1/3 密度），即加密随机源 u"""
    return rng.choice(np.array([-1, 0, 1], dtype=np.int64), size=n,
                      p=[1 / 3, 1 / 3, 1 / 3])


def _center_lift(a: np.ndarray, q: int = Q) -> np.ndarray:
    """约化到 (−q/2, q/2] 的整数代表"""
    a = np.asarray(a, dtype=np.int64) % q
    return a - q * (a > q // 2)


# =====================================================================
# 玩具 CKKS：密钥生成 / 加密 / 解密
# =====================================================================
def toy_keygen(q: int = Q, n: int = N, sigma: float = SIGMA,
               rng: Optional[np.random.RandomState] = None
               ) -> Tuple[np.ndarray, Tuple[np.ndarray, np.ndarray]]:
    """sk = s（小系数），pk = (b, a)，b = −a·s + e mod (q, x^N+1)"""
    rng = rng or np.random.RandomState(0)
    s = _sample_gauss_poly(rng, sigma, n)
    a = rng.randint(0, q, size=n, dtype=np.int64)
    e = _sample_gauss_poly(rng, sigma, n)
    b = (-poly_mul(a, s, q, n) + e) % q
    return s, (b, a)


def toy_encrypt(pk: Tuple[np.ndarray, np.ndarray], m: np.ndarray,
                q: int = Q, n: int = N, delta: int = DELTA,
                sigma: float = SIGMA,
                rng: Optional[np.random.RandomState] = None
                ) -> Tuple[np.ndarray, np.ndarray]:
    """m 为长度 n 的实向量（明文多项式系数视角）；编码 round(m·Δ)

    c0 = b·u + e1 + round(Δ·m)，c1 = a·u + e2
    """
    rng = rng or np.random.RandomState(0)
    b, a = pk
    pt = np.rint(np.asarray(m, dtype=np.float64) * delta).astype(object)
    pt = np.asarray(pt % q, dtype=np.int64)
    u = _sample_ternary_sparse(rng, n)
    e1 = _sample_gauss_poly(rng, sigma, n)
    e2 = _sample_gauss_poly(rng, sigma, n)
    c0 = (poly_mul(b, u, q, n) + pt + e1) % q
    c1 = (poly_mul(a, u, q, n) + e2) % q
    return c0, c1


def toy_decrypt(sk: np.ndarray, ct: Tuple[np.ndarray, np.ndarray],
                q: int = Q, n: int = N, delta: int = DELTA) -> np.ndarray:
    """m' = center_lift(c0 + c1·s) / Δ

    返回长度 n 的实向量（明文多项式的系数视角）；对单个标量明文可看
    第 0 个系数。误差 |m' − m| 由总噪声 e_tot = e1 + e2·s − e·u 决定。
    """
    c0, c1 = ct
    noisy = _center_lift(poly_add(c0, poly_mul(c1, sk, q, n), q), q)
    return noisy.astype(np.float64) / delta


# =====================================================================
# 门限部分解密
# =====================================================================
def share_key(sk: np.ndarray, n: int, t: int,
              q: int = Q, n_coeffs: int = N,
              rng: Optional[np.random.RandomState] = None
              ) -> List[Tuple[int, np.ndarray]]:
    """
    私钥 s 的每个系数做 Shamir (t, n) 分片（模 q 上的多项式份额：
    f_j(x) = s_j + a_1 x + ... + a_{t-1} x^{t-1}，系数 mod q，求值点
    x = 1..n），返回 [(x_i, s_i_poly)]，满足 Σ λ_i·s_i ≡ s (mod q)
    （λ_i 为 x=0 处 Lagrange 系数）。q 为素数且 q > n 保证分母可逆。
    """
    rng = rng or np.random.RandomState(0)
    sk_red = np.asarray(np.asarray(sk, dtype=object) % q)  # 丢符号位：份额 ≡ sk (mod q)
    if sk_red.shape != (n_coeffs,):
        raise ValueError(f"私钥长度不符：期望 {n_coeffs} 个系数")
    # 每个系数一条随机 t−1 次多项式（常数项 = 该系数）
    rand_coeffs = rng.randint(0, q, size=(n_coeffs, t - 1),
                              dtype=np.int64).astype(object)
    shares = []
    for x in range(1, n + 1):
        sx = sk_red.copy()
        xp = x
        for k in range(t - 1):
            sx = (sx + rand_coeffs[:, k] * xp) % q
            xp *= x
        shares.append((x, np.asarray(sx, dtype=np.int64)))
    return shares


def partial_decrypt(ct: Tuple[np.ndarray, np.ndarray],
                    share_i: np.ndarray,
                    rng: Optional[np.random.RandomState] = None,
                    smudging: bool = True,
                    q: int = Q, n: int = N) -> np.ndarray:
    """参与方 P_i 的部分解密：d_i = c1·s_i mod (q, x^N+1)，smudging 时加 e'_i

    d_i 本身是 c1·s 的 Shamir 份额（线性映射保持份额结构，doc §3.2），
    裸发 c1·s_i 可能在选择密文敌手面前泄露 s_i——smudging 噪声 e'_i
    不可省（doc §9.1）。σ' 取值见模块 docstring 的取舍说明。
    """
    c0, c1 = ct
    d_i = poly_mul(c1, share_i, q, n)
    if smudging:
        rng = rng or np.random.RandomState(0)
        d_i = (d_i + _sample_gauss_poly(rng, SMUDGE_SIGMA, n)) % q
    return d_i


def _lagrange_lambdas(indices: Sequence[int], q: int = Q) -> List[int]:
    """x=0 处拉格朗日系数：精确整数恒等式 L·s = Σ s_i·N_i，再模 q 求逆。

    用 Fraction 保持精确：λ_i = N_i / D_i ∈ Q，且 Σ λ_i·s_i = s（整数恒
    等式，s_i 为整数份额），故模 q 约化合法（要求分母与 q 互素，素数 q>n
    保证）。"""
    from fractions import Fraction
    lambdas: List[int] = []
    for i in indices:
        lam = Fraction(1, 1)
        for j in indices:
            if i == j:
                continue
            lam *= Fraction(-j, i - j)
        assert lam.denominator == 1 or gcd(lam.denominator, q) == 1
        lambdas.append(lam.numerator % q *
                       pow(lam.denominator % q, q - 2, q) % q)
    return lambdas


def combine(c0: np.ndarray, partials: Sequence[np.ndarray],
            xs: Sequence[int],
            q: int = Q, n: int = N, delta: int = DELTA) -> np.ndarray:
    """组合器：m ≈ center_lift(c0 + Σ λ_i·d_i) / Δ，λ_i 为 x=0 处
    Lagrange 系数（参与方求值点 xs）。少于 t 个份额时结果是伪随机
    噪声（Shamir 信息论安全），解码误差爆炸。"""
    if len(partials) != len(xs):
        raise ValueError("部分解密份额与求值点数量不一致")
    lambdas = _lagrange_lambdas(list(xs), q)
    acc = np.asarray(c0, dtype=np.int64) % q
    for lam, d_i in zip(lambdas, partials):
        acc = (acc + lam * np.asarray(d_i, dtype=object)) % q
    noisy = _center_lift(np.asarray(acc, dtype=np.int64), q)
    return noisy.astype(np.float64) / delta


__all__ = ['Q', 'N', 'DELTA', 'SIGMA', 'SMUDGE_SIGMA',
           'poly_mul', 'poly_add', 'toy_keygen', 'toy_encrypt', 'toy_decrypt',
           'share_key', 'partial_decrypt', 'combine']
