"""
Pedersen 分布式密钥生成（DKG，Gennaro et al.）
==============================================

模拟 n 个参与方在素数值域上集体生成一个 (t,n) 门限密钥：

- 每个参与方 P_i 独立选取 t−1 次随机多项式 f_i（常数项 a_i0 即其对
  集体 secret 的贡献）与盲化多项式 b_i；
- Pedersen 承诺广播：C_ik = g^{a_ik} · h^{b_ik} mod P
  （g=4；h = SHA256("threshold-h") 整数 mod P，全局确定性值）；
- P_i 向 P_j 私下发送份额 (f_i(j), b_i(j))，P_j 用承诺验证
      g^{f_i(j)} · h^{b_i(j)} == ∏_k C_ik^{j^k}   (mod P)
- 各自聚合 s_j = Σ_i f_i(j)。集体公钥 public = g^{Σ_i a_i0} mod P。

⚠️ 教学参数（doc/threshold_ckks.md §5 的"简单起见"约定）：
1. 承诺的值域是 Z_P^*（P = 2^521−1，复用 ppbr/sa.py 的 _SHAMIR_P），
   其元素阶整除 P−1（合数），并非素数阶子群；真实实现应使用 safe
   prime 的 q 阶子群或椭圆曲线群；
2. 为使验证等式两边指数严格一致，共享多项式系数取 [0, P) 的整数、
   份额不做模约化（精确整数运算），秘密恢复用精确有理数拉格朗日
   （fractions.Fraction）——验证的代数正确性与真实实现完全同构。

安全性要点：任何 <t 个参与方合谋无法推出 secret（Shamir 门限性质 +
承诺的绑定性）；任何参与方提交不一致份额会被验证发现（f 与 b 不同构
即被捕获）。

⚠️ 真实 DKG 协议中 secret 永不重构；recover_from_shares 仅供测试断言。
"""

import hashlib
import random
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Dict, List, Sequence, Tuple

from ppbr.sa import _SHAMIR_P

P = _SHAMIR_P   # 承诺值域模数
G = 4           # 生成元（教学参数，见模块 docstring）


def _derive_h() -> int:
    """第二生成元：h = int(SHA256(b"threshold-h"), 16) mod P（全局确定性）"""
    h = int(hashlib.sha256(b"threshold-h").hexdigest(), 16) % P
    return h if h != 0 else 1


H = _derive_h()


def _eval_poly_exact(coeffs: Sequence[int], x: int) -> int:
    """Horner 法在 x 处求多项式值（精确整数，不约化）"""
    y = 0
    for c in reversed(coeffs):
        y = y * x + c
    return y


class DKGParticipant:
    """一个 DKG 参与方：生成贡献、发份额、验证他人份额（真实数学）"""

    def __init__(self, pid: int, n: int, t: int, g: int = G, p: int = P):
        self.pid = pid
        self.n = n
        self.t = t
        self.g = g % p
        self.p = p
        # 本参与方只持有自己的 f_i / b_i 系数——从不持有集体 secret
        self._f_coeffs: List[int] = []
        self._b_coeffs: List[int] = []
        self.commits: List[int] = []  # Pedersen 承诺 C_i0..C_i,t-1（公开）

    def generate_contribution(self, rng: random.Random = None) -> List[int]:
        """生成 t−1 次随机多项式 f_i、b_i（系数 ∈ [0, P)）并广播承诺

        Args:
            rng: 随机源（仿真传入 random.Random 保证确定性；缺省用全局随机）
        """
        rng = rng or random.Random()
        self._f_coeffs = [rng.randrange(self.p) for _ in range(self.t)]
        self._b_coeffs = [rng.randrange(self.p) for _ in range(self.t)]
        self.commits = [
            pow(self.g, a, self.p) * pow(H, b, self.p) % self.p
            for a, b in zip(self._f_coeffs, self._b_coeffs)
        ]
        return self.commits

    def share_for(self, j: int) -> Tuple[int, int]:
        """发给 P_j 的私下份额 (f_i(j), b_i(j))（精确整数，加密通道传输）"""
        assert self.commits, "generate_contribution 尚未调用"
        return (_eval_poly_exact(self._f_coeffs, j),
                _eval_poly_exact(self._b_coeffs, j))

    def verify_share(self, sid: int, j: int, share: Tuple[int, int],
                     commits: Sequence[int]) -> bool:
        """验证 P_{sid} 发给自己的份额（j 必须是本参与方 pid）：
            g^{s} · h^{r} == ∏_k C_k^{j^k}  (mod P)

        Args:
            sid:     发送方参与方编号
            j:       接收方求值点（= 本参与方 pid）
            share:   (s, r) = (f_i(j), b_i(j))
            commits: 发送方广播的承诺列表
        """
        assert j == self.pid, "只能验证发给自己的份额"
        s, r = share
        lhs = pow(self.g, s, self.p) * pow(H, r, self.p) % self.p
        rhs = 1
        xk = 1
        for c in commits:
            rhs = rhs * pow(c % self.p, xk, self.p) % self.p
            xk *= j
        return lhs == rhs


@dataclass
class DKGResult:
    """一轮 Pedersen DKG 的产物（run_dkg 返回）"""
    public: int                    # 集体公钥 g^{secret} mod P
    shares: Dict[int, int]         # {pid: s_j}，s_j = Σ_i f_i(j)（聚合份额）
    participant_ids: List[int]     # 全体参与方编号（1..n）
    commits: Dict[int, List[int]] = field(default_factory=dict)  # {pid: 承诺列表}


def run_dkg(n: int, t: int, seed: int = 42) -> DKGResult:
    """
    模拟 n 个参与方完成一轮 Pedersen DKG（内部循环模拟两两份额交换，
    每方验证全部收到的份额，任一分额不一致即抛异常模拟协议中止）。

    Args:
        n:    参与方数
        t:    门限
        seed: 随机种子（仿真确定性）

    Returns:
        DKGResult(public=g^{Σ a_i0} mod P, shares={pid: s_j},
                  participant_ids=[1..n], commits={pid: [...]})

    Example:
        >>> res = run_dkg(6, 3, seed=42)
        >>> len(res.shares)
        6
    """
    assert n >= t >= 1, f"非法 DKG 参数 n={n}, t={t}"
    parts = [DKGParticipant(i + 1, n, t) for i in range(n)]
    rngs = [random.Random(seed * 100003 + i) for i in range(n)]
    # 阶段 1：各 broadcast 承诺
    all_commits = {p.pid: p.generate_contribution(rngs[i])
                   for i, p in enumerate(parts)}
    # 阶段 2：私下份额交换 + 验证 + 聚合（不一致份额 → 中止模拟）
    shares: Dict[int, int] = {}
    for pj in parts:
        s_j = 0
        for pi in parts:
            sh = pi.share_for(pj.pid)
            if not pj.verify_share(pi.pid, pj.pid, sh, all_commits[pi.pid]):
                raise ValueError(f"P_{pi.pid}→P_{pj.pid} 的份额未通过验证"
                                 f"（DKG 应中止，此处抛异常模拟）")
            s_j += sh[0]
        shares[pj.pid] = s_j
    # 集体公钥 = ∏_i pk_i，pk_i = C_i0 · h^{−b_i0} = g^{a_i0} 由各参与方
    # 本地计算并公布（只暴露 g^{a_i0}，不暴露 a_i0 本身）
    public = 1
    for p in parts:
        pk_i = p.commits[0] * pow(
            pow(H, p._b_coeffs[0], p.p), p.p - 2, p.p) % p.p
        public = public * pk_i % p.p
    return DKGResult(public=public, shares=shares,
                     participant_ids=[p.pid for p in parts],
                     commits=all_commits)


def recover_from_shares(shares: Dict[int, int], t: int) -> int:
    """
    ⚠️ 仅用于测试断言——真实 DKG 协议中 secret 永不重构（doc §5）。

    精确有理数拉格朗日插值在 x=0 处求值，恢复集体 secret = Σ_i a_i0
    （结果为整数：分母整除，Fraction 自动精确约分）。

    Args:
        shares: {pid: s_j}，至少 t 项
        t:      门限（少于 t 项抛 ValueError）
    """
    if len(shares) < t:
        raise ValueError(f"份额不足：需要 ≥{t} 份，实得 {len(shares)} 份")
    items = list(shares.items())
    acc = Fraction(0, 1)
    for i, (xi, yi) in enumerate(items):
        lam = Fraction(1, 1)
        for j, (xj, _) in enumerate(items):
            if i == j:
                continue
            lam *= Fraction(-xj, xi - xj)
        acc += lam * yi
    assert acc.denominator == 1, "插值结果非整数——份额被篡改或不足 t 份"
    return acc.numerator


# 兼容旧接口名
recover_secret_from_shares = recover_from_shares

__all__ = ['P', 'G', 'H', 'DKGParticipant', 'DKGResult', 'run_dkg',
           'recover_from_shares', 'recover_secret_from_shares']
