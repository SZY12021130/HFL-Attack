"""
CLDP（Condensed Local Differential Privacy）扰动 —— 论文式 (1)(2)
====================================================================
定义（式 1）：随机算法 M 满足 ε_χ-CLDP，若对任意输入 s, s' 与输出 y：
    f(y|s) ≤ e^{ε_χ·d(s,s')} · f(y|s')，  PPBR 取 d(s,s') = ‖s−s'‖₂

扰动分布（式 2），d=3 维符号元组：
    f(y|s) = λ·e^(−ε_χ·‖y−s‖₂)   当 ‖y−s‖₂ ≤ L            （球 B_L 内）
           = λ·e^(−ε_χ·L)         当 L < ‖y−s‖₂ 且 ‖y−s‖_∞ ≤ L'（立方壳层）
           = 0                      其他（超出超立方支撑 S_{L'}）

精确采样（组合分布法，O(1) 期望时间，替代低效的纯拒绝采样——ε=3,L=6 时
全立方提议的接受率仅 ~1e-7）：
  1) 以概率 m₁/(m₁+m₂) 落入球 B_L：
       m₁ = ∫_{B_L} e^{−ε‖y−s‖}dy = 4π ∫₀^L r²e^{−εr}dr （d=3 旋转对称）
       采样 = 球面均匀方向 × 径向密度 ∝ r²e^{−εr}（解析 CDF + 二分反演）
  2) 以概率 m₂/(m₁+m₂) 落入壳层 S_{L'}\B_L：
       m₂ = e^(−εL)·(vol(S_{L'}) − vol(B_L))
       采样 = 立方内均匀提议，拒绝落球（接受率 ≈ 1 − vol(B_L)/vol(S_{L'})，高）
"""

import math

import numpy as np


def _ball_volume(d: int, r: float) -> float:
    """d 维球体积 π^{d/2}/Γ(d/2+1)·r^d"""
    return np.pi ** (d / 2) / math.gamma(d / 2 + 1) * r ** d


def _ball_mass(epsilon: float, L: float) -> float:
    """∫_{B_L} e^{−ε‖y‖} dy（d=3）：4π ∫₀^L r²e^{−εr} dr 的闭式解"""
    a = epsilon
    integral = 2.0 / a ** 3 - np.exp(-a * L) * (L ** 2 / a + 2 * L / a ** 2 + 2.0 / a ** 3)
    return 4.0 * np.pi * integral


def _sample_radius(epsilon: float, L: float, rng: np.random.RandomState) -> float:
    """径向密度 p(r) ∝ r²e^{−εr}，r ∈ [0, L]：解析 CDF F(r)=u 二分反演"""
    a = epsilon
    g = lambda r: -np.exp(-a * r) * (r ** 2 / a + 2 * r / a ** 2 + 2.0 / a ** 3)
    total = g(L) - g(0.0)
    u = rng.random() * total
    lo, hi = 0.0, L
    for _ in range(60):
        mid = (lo + hi) / 2
        if g(mid) - g(0.0) < u:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def sample_cldp(s: np.ndarray, epsilon: float, L: float, L_prime: float,
                rng: np.random.RandomState = None) -> np.ndarray:
    """
    从式 (2) 分布精确采样扰动后的 y（边缘只应看到这个结果）。

    Args:
        s: 原始符号统计元组（d 维，d=3）
        epsilon: 隐私预算 ε_χ
        L: 球半径阈值（B_L 内密度随距离指数衰减，壳层内密度恒为 e^{−εL}）
        L_prime: 超立方支撑半径（‖y−s‖_∞ ≤ L'）
        rng: 随机源
    Returns:
        扰动后的元组 y，满足 ‖y−s‖_∞ ≤ L'
    """
    rng = rng or np.random.RandomState(0)
    s = np.asarray(s, dtype=np.float64)
    d = s.shape[0]
    if not (0 < L <= L_prime):
        raise ValueError(f"需要 0 < L ≤ L'，得到 L={L}, L'={L_prime}")

    cube_vol = (2.0 * L_prime) ** d
    ball_vol = _ball_volume(d, L)
    if d != 3:
        raise NotImplementedError("CLDP 采样器按论文 d=3（符号三元组）实现")
    m1 = _ball_mass(epsilon, L)
    m2 = np.exp(-epsilon * L) * (cube_vol - ball_vol)

    if rng.random() < m1 / (m1 + m2):
        # 球内：方向球面均匀，半径 ∝ r²e^{−εr}
        direction = rng.normal(size=d)
        direction /= np.linalg.norm(direction)
        return s + _sample_radius(epsilon, L, rng) * direction

    # 壳层：立方均匀提议，拒绝落球（接受率高，期望迭代 < 2）
    while True:
        y = s + rng.uniform(-L_prime, L_prime, size=d)
        if np.linalg.norm(y - s) > L:
            return y


def perturb_sign_tuple(s: np.ndarray, epsilon: float, L: float, L_prime: float,
                       rng: np.random.RandomState = None) -> np.ndarray:
    """PPBR 算法 2 第 b 步：客户端对符号元组施加 ε_χ-CLDP 扰动"""
    return sample_cldp(s, epsilon, L, L_prime, rng)
