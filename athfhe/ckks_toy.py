"""
athfhe/ckks_toy.py：公共外层——分圆环运算 + 教科书 CKKS + Shamir 分片
=====================================================================

技术方案改写自 threshold/toy_ckks.py（保持 athfhe 包隔离，不 import
threshold），关键扩展：

1. limb 数参数化：toy_ckks.py 的 _NLIMB=4 硬编码适配 q<2^60；本模块的
   negacyclic 乘法按实际模数位宽自动选择 limb 数（方案一内层模数
   Q' ~ 2·n_c·Q²·B' 可达 140+ bit），见 RingContext.nlimb；
2. 支持任意 2 的幂环次数 M 与任意素数模数 Q（大模数走 object dtype +
   Python 大整数）；
3. 外层玩具 CKKS：全局私钥 S（三元小系数）、PK=(p0,p1)=(−a·S+e_pk, a)、
   系数编码（浮点块 g → round(Δ·g) 作为多项式系数，高 M/2 位置 0）、
   加密 c0 = p0·u + m + e0 / c1 = p1·u + e1、解密核心 c0 + c1·S；
4. 按系数的 Shamir (t,n) 分片（公开评估点小整数 1..n，模数取素数保证
   Lagrange 分母可逆）、x=0 处 Lagrange 系数、部分解密/重构。

威胁模型与参数界见 doc/athfhe.md。
"""

from fractions import Fraction
from math import gcd
from typing import List, Optional, Sequence, Tuple

import numpy as np

_LIMB_BITS = 15           # 15-bit limb：limb 卷积部分积和 ≤ M·2^30，M≤2^23 时
                          # < 2^53，float64 FFT 精确表示
_LIMB = 1 << _LIMB_BITS
_INT64_SAFE = 1 << 62     # 模数 < 2^62 时全程 int64；更大走 object 大整数

# Miller-Rabin 确定基集：对 n < 3,317,044,064,679,887,385,961,981 (~3.3e24)
# 只需以下前 13 个素数基即可确定判定（Farrott 界，覆盖 2^141 以内）
_MR_BASES = (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41)


def _is_probable_prime(n: int) -> bool:
    if n < 2:
        return False
    for p in _MR_BASES[:6]:
        if n % p == 0:
            return n == p
    d, r = n - 1, 0
    while d % 2 == 0:
        d //= 2
        r += 1
    for a in _MR_BASES:
        if a % n == 0:
            continue
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = x * x % n
            if x == n - 1:
                break
        else:
            return False
    return True


def next_prime(bound: int) -> int:
    """严格大于 bound 的最小素数（升序奇数筛 + Miller-Rabin）"""
    n = bound + 1
    if n <= 2:
        return 2
    if n % 2 == 0:
        n += 1
    while not _is_probable_prime(n):
        n += 2
    return n


class RingContext:
    """分圆环 R_{M,Q} = Z_Q[X]/(X^M+1) 上下文（M 为 2 的幂，Q 素数）。

    多项式一律表示为长度 M 的 numpy 数组：Q < 2^62 时 int64，否则
    object dtype（逐元素 Python 大整数）。模乘按 Q 的位宽自动参数化
    15-bit limb 数（toy_ckks._NLIMB=4 的泛化）。
    """

    def __init__(self, modulus: int, degree: int, limb_bits: int = _LIMB_BITS):
        if degree < 2 or (degree & (degree - 1)) != 0:
            raise ValueError(f"环次数 M 必须为 2 的幂，得到 {degree}")
        if not _is_probable_prime(modulus):
            raise ValueError(f"模数 Q 必须为素数（Lagrange 分母可逆），得到 {modulus}")
        if modulus <= degree:
            raise ValueError(f"模数 Q={modulus} 必须 > 环次数 M={degree}")
        self.q = int(modulus)
        self.n = int(degree)
        self.limb_bits = int(limb_bits)
        self.limb = 1 << limb_bits
        # limb 数：ceil(bits(Q)/limb_bits)；卷积列数 2*nlimb-1
        self.nlimb = max(1, (self.q.bit_length() + limb_bits - 1) // limb_bits)
        self.dtype = np.int64 if self.q < _INT64_SAFE else object

    # ---- 表示 ----------------------------------------------------------
    def reduce(self, a: np.ndarray) -> np.ndarray:
        """约化到 [0, Q)，按本环 dtype 返回"""
        r = np.asarray(a, dtype=object) % self.q
        return r.astype(self.dtype) if self.dtype is np.int64 else r

    def center_lift(self, a: np.ndarray) -> np.ndarray:
        """约化到 (−Q/2, Q/2] 的整数代表（保留符号的解密视角）"""
        r = np.asarray(a, dtype=object) % self.q
        r = r - self.q * (r > self.q // 2)
        return r.astype(np.int64) if (self.dtype is np.int64) else r

    # ---- 采样 ----------------------------------------------------------
    def uniform_poly(self, rng: np.random.RandomState) -> np.ndarray:
        """R_Q 上均匀随机多项式（大模数时按 30-bit  limbs 拼装）"""
        if self.q < _INT64_SAFE:
            return rng.randint(0, self.q, size=self.n, dtype=np.int64)
        vals = np.zeros(self.n, dtype=object)
        nchunk = (self.q.bit_length() + 29) // 30
        for k in range(nchunk):
            vals = (vals + (rng.randint(0, 1 << 30, size=self.n).astype(object)
                            << (30 * k))) % self.q
        return vals

    def gauss_poly(self, rng: np.random.RandomState, std: float = 3.2) -> np.ndarray:
        """离散高斯近似（四舍五入；玩具噪声，非精确采样器）"""
        return np.rint(rng.normal(0.0, std, size=self.n)).astype(np.int64)

    def ternary_sparse(self, rng: np.random.RandomState) -> np.ndarray:
        """{0,±1} 稀疏采样（1/3 密度），加密随机源 u / 三元私钥 S"""
        return rng.choice(np.array([-1, 0, 1], dtype=np.int64), size=self.n,
                          p=[1 / 3, 1 / 3, 1 / 3])

    def small_uniform(self, rng: np.random.RandomState, bound: int) -> np.ndarray:
        """[−bound, bound]^M 整数均匀（掩码噪声 n）"""
        return rng.randint(-bound, bound + 1, size=self.n).astype(np.int64)

    # ---- 环运算 --------------------------------------------------------
    def _fft_conv(self, a: np.ndarray, b: np.ndarray) -> np.ndarray:
        """朴素卷积（线性卷积，长度 2M−1），float64 FFT 精确（limb 15-bit）"""
        size = 1 << int(np.ceil(np.log2(2 * len(a) - 1)))
        fa = np.fft.rfft(a, size)
        fb = np.fft.rfft(b, size)
        return np.rint(np.fft.irfft(fa * fb, size)[:2 * len(a) - 1]).astype(np.int64)

    def poly_mul(self, a: np.ndarray, b: np.ndarray) -> np.ndarray:
        """negacyclic 乘法 mod (Q, X^M+1)：15-bit limb 分裂 + FFT 卷积 +
        进位合并 + 折叠 + 大整数 Horner 约化（limb 数 = self.nlimb，参数化）"""
        q, n = self.q, self.n
        aa = np.asarray(a, dtype=np.int64) % q if q < _INT64_SAFE \
            else np.asarray(a, dtype=object) % q
        bb = np.asarray(b, dtype=np.int64) % q if q < _INT64_SAFE \
            else np.asarray(b, dtype=object) % q
        lb, nl = self.limb_bits, self.nlimb
        a_limbs = [(aa >> (lb * k)) % self.limb for k in range(nl)]
        b_limbs = [(bb >> (lb * k)) % self.limb for k in range(nl)]
        # 列 k = Σ_{i+j=k} a_i·b_j（权重 limb^k），长度 2n−1
        cols: List[Optional[np.ndarray]] = [None] * (2 * nl - 1)
        for i in range(nl):
            for j in range(nl):
                k = i + j
                conv = self._fft_conv(np.asarray(a_limbs[i], dtype=np.int64),
                                      np.asarray(b_limbs[j], dtype=np.int64))
                cols[k] = conv if cols[k] is None else cols[k] + conv
        # 进位传播（任意 a*b < M·Q²；列以 int64 起步，溢出风险列自动转 object）
        k = 0
        while k < len(cols):
            col = cols[k]
            if col.dtype != object and (col.max(initial=0) >= (1 << 62)):
                col = col.astype(object)
            carry = col // self.limb
            cols[k] = (col % self.limb).astype(object)
            if k + 1 < len(cols):
                cols[k + 1] = (cols[k + 1].astype(object) + carry)
            elif np.any(carry):
                cols.append(carry)
            k += 1
        # negacyclic 折叠（x^{n+k} ≡ −x^k；x^{2n−1} 项系数恒为 0）
        folded = []
        for col in cols:
            c = np.asarray(col, dtype=object)
            f = c[:n].copy()
            f[:n - 1] -= c[n:2 * n - 1]
            folded.append(f)
        # 大整数 Horner 合并并 mod q
        vals = np.zeros(n, dtype=object)
        for col in reversed(folded):
            vals = (vals * self.limb + col) % q
        return vals.astype(self.dtype) if self.dtype is np.int64 else vals

    def poly_add(self, a: np.ndarray, b: np.ndarray) -> np.ndarray:
        return self.reduce(np.asarray(a) + np.asarray(b))

    def poly_neg(self, a: np.ndarray) -> np.ndarray:
        return self.reduce(-np.asarray(a))

    def poly_scalar_mul(self, s, a: np.ndarray) -> np.ndarray:
        """标量（Python int）× 多项式"""
        return self.reduce(int(s) * np.asarray(a, dtype=object))

    def zero(self) -> np.ndarray:
        return np.zeros(self.n, dtype=self.dtype)

    def poly_bytes(self) -> int:
        """单个多项式的序列化字节数（通信成本统计）"""
        return self.n * ((self.q.bit_length() + 7) // 8)


# =====================================================================
# 外层玩具 CKKS（系数编码简化，同 toy_ckks）
# =====================================================================
def outer_keygen(ctx: RingContext, rng: np.random.RandomState
                 ) -> Tuple[np.ndarray, Tuple[np.ndarray, np.ndarray]]:
    """全局外层私钥 S（三元小系数），公钥 PK=(p0,p1)=(−a·S+e_pk, a)"""
    s = ctx.ternary_sparse(rng)
    a = ctx.uniform_poly(rng)
    e_pk = ctx.gauss_poly(rng)
    p0 = ctx.poly_add(ctx.poly_neg(ctx.poly_mul(a, s)), e_pk)
    return s, (p0, a)


def outer_encrypt(ctx: RingContext, pk: Tuple[np.ndarray, np.ndarray],
                  block: Sequence[float], delta: int,
                  rng: np.random.RandomState,
                  ) -> Tuple[np.ndarray, np.ndarray]:
    """系数编码：浮点块 g∈R^{M/2} → m=round(Δ·g)（高 M/2 位置 0）；
    c0 = p0·u + m + e0，c1 = p1·u + e1（u 三元稀疏，e 高斯）"""
    p0, p1 = pk
    m = np.zeros(ctx.n, dtype=np.int64)
    blk = np.rint(np.asarray(block, dtype=np.float64) * delta).astype(object)
    m[:len(blk)] = np.asarray(blk, dtype=object)
    m = ctx.reduce(m)
    u = ctx.ternary_sparse(rng)
    e0 = ctx.gauss_poly(rng)
    e1 = ctx.gauss_poly(rng)
    c0 = ctx.poly_add(ctx.poly_add(ctx.poly_mul(p0, u), m), e0)
    c1 = ctx.poly_add(ctx.poly_mul(p1, u), e1)
    return c0, c1


def outer_add(ctx: RingContext, ct1, ct2):
    """同态加法聚合（分量分别相加）"""
    return (ctx.poly_add(ct1[0], ct2[0]), ctx.poly_add(ct1[1], ct2[1]))


def outer_decrypt_core(ctx: RingContext, s: np.ndarray, ct) -> np.ndarray:
    """解密核心：c0 + c1·S = M_agg + e_CT (mod Q)，返回 center_lift 后的
    整数多项式（调用方除以 Δ 解码）"""
    c0, c1 = ct
    return ctx.center_lift(ctx.poly_add(c0, ctx.poly_mul(c1, s)))


# =====================================================================
# 按系数的 Shamir 分片 / Lagrange（模素数，分母可逆）
# =====================================================================
def shamir_share(ctx: RingContext, secret: np.ndarray, n: int, t: int,
                 rng: np.random.RandomState
                 ) -> List[Tuple[int, np.ndarray]]:
    """secret（长度 M 多项式）逐系数做 (t,n)-Shamir：f_j(x)=s_j+a_1 x+…+
    a_{t-1} x^{t-1}（系数 mod Q），评估点 x=1..n。返回 [(x_i, share_i)]，
    任意 ≥t 个子集满足 Σ λ_i·share_i ≡ secret (mod Q)。"""
    if not (1 <= t <= n):
        raise ValueError(f"Shamir 参数非法：t={t}, n={n}")
    if n >= ctx.q:
        raise ValueError(f"评估点数 n={n} 必须 < 模数 Q={ctx.q}（分母可逆）")
    sec = np.asarray(secret, dtype=object) % ctx.q
    rand_coeffs = np.stack([ctx.uniform_poly(rng).astype(object)
                            for _ in range(t - 1)]) if t > 1 else None
    shares = []
    for x in range(1, n + 1):
        sx = sec.copy()
        xp = x
        for k in range(t - 1):
            sx = (sx + rand_coeffs[k] * xp) % ctx.q
            xp *= x
        shares.append((x, sx.astype(ctx.dtype)))
    return shares


def lagrange_coeffs(ctx: RingContext, indices: Sequence[int]) -> List[int]:
    """x=0 处 Lagrange 系数（Fraction 精确整数恒等式，模 Q 求逆）"""
    lambdas: List[int] = []
    for i in indices:
        lam = Fraction(1, 1)
        for j in indices:
            if i == j:
                continue
            lam *= Fraction(-j, i - j)
        assert lam.denominator == 1 or gcd(lam.denominator, ctx.q) == 1
        lambdas.append(lam.numerator % ctx.q *
                       pow(lam.denominator % ctx.q, ctx.q - 2, ctx.q) % ctx.q)
    return lambdas


def shamir_recover(ctx: RingContext,
                   shares: Sequence[Tuple[int, np.ndarray]]) -> np.ndarray:
    """x=0 处 Shamir 插值重构 secret（同 threshold/toy_ckks.combine 的份额侧）"""
    xs = [x for x, _ in shares]
    lambdas = lagrange_coeffs(ctx, xs)
    acc = np.zeros(ctx.n, dtype=object)
    for lam, (_, sh) in zip(lambdas, shares):
        acc = (acc + lam * np.asarray(sh, dtype=object)) % ctx.q
    return acc.astype(ctx.dtype)


def shamir_recover_scalar_secret(ctx: RingContext,
                                 points: Sequence[int],
                                 values: Sequence[int]) -> int:
    """标量秘密在 x=0 处的 Shamir 插值（方案一阶段 3 的 dk 重构路径演示）"""
    lambdas = lagrange_coeffs(ctx, list(points))
    acc = 0
    for lam, v in zip(lambdas, values):
        acc = (acc + lam * int(v)) % ctx.q
    return acc


__all__ = ['RingContext', 'next_prime',
           'outer_keygen', 'outer_encrypt', 'outer_add', 'outer_decrypt_core',
           'shamir_share', 'shamir_recover', 'shamir_recover_scalar_secret',
           'lagrange_coeffs']
