"""
athfhe/twin.py：方案二——面向 CKKS 的非共谋双服务器架构
（Twin-Server Non-Colluding CKKS-ATASSES）

实体：N 个客户端 P_i、聚合服务器 S_A、辅助密钥服务器 S_H（双服务器非共谋
假设，均半诚实）。与方案一不同，内层掩码私钥 ek 由 128-bit 种子经 PRG
（hashlib.shake_256 字节流展开）在 R_{M,Q} 上展开为**均匀随机**多项式，
(a'u')·ek 即完美一次性掩码（PRF/OTP），因此：

- 不需要内层 RLWE 底噪 e'，也没有取整去噪步骤（全程模 Q 恒等，精确）；
- 不需要升环——内层直接复用外层环 R_{M,Q}，通信开销更小（doc §方案二）。

四阶段：
- 阶段0 Twin-CKKS-Setup：S、PK；每客户端持 (T,N)-Shamir 份额 s_i；
  CRS 每轮生成公共 a'·u' ∈ R_{M,Q}。
- 阶段1：外层加密上传 S_A，同态加得 CT=(c0,c1)，广播 c1。
- 阶段2 Client-OneShot-Upload（在线集 T，|T|≥T，单轮发完即走、零 P2P）：
  b_i=c1·s_i、n_i←[−B_sm,B_sm]^M；两个 128-bit 种子经 PRG 展开
  ek_{i,1},ek_{i,2}；c'_{b,i,0}=(a'·ek1)·u'+b_i、c'_{n,i,0}=(a'·ek2)·u'+n_i、
  c'_1=−a'·u'（均 mod Q）；P_i→S_A 发 (c'_{b,i,0},c'_{n,i,0})，P_i→S_H 发
  两种子（模拟 TLS 安全信道，记录 32 字节）。
- 阶段3 Twin-Server-Coop：S_A 算 L_i^(T)、C'_all,0=Σ(L_i·c'_{b,i,0}+c'_{n,i,0})
  并把在线名单 T 发 S_H；S_H 校验 |T|≥T（防 S_A 伪造名单套密钥），展开种子
  得 S1=Σ L_i·ek_{i,1}、S2=Σ ek_{i,2}，dk=S1+S2 发 S_A（S_A 无种子，无法
  从 dk 分离 S1 或任何 ek_{i,1}）。
- 阶段4 Final-CKKS-Decode：b'=C'_all,0+C'_all,1·dk=c1·S+Σn_i (mod Q)；
  m'=c0+b'；CKKS 解码。全程无取整，模 Q 恒等。
"""

import hashlib
import time
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from athfhe.ckks_toy import (
    RingContext, next_prime, outer_keygen, outer_encrypt, outer_add,
    shamir_share, lagrange_coeffs,
)

SEED_BYTES = 16          # 128-bit 种子
TLS_RECORD_BYTES = 32    # P_i→S_H 两条种子的安全信道记录字节数


def prg_expand_poly(seed: bytes, ctx: RingContext) -> np.ndarray:
    """PRG：hashlib.shake_256 字节流展开为 R_{M,Q} 上均匀随机多项式。

    每系数取 ceil(bits(Q)/8) 字节，拒绝采样模 Q（Q 距 2 的幂极近时拒绝率
    可忽略；为确定性直接用 mod 约化——密码学 demo 足够）。"""
    nbytes = (ctx.q.bit_length() + 7) // 8
    buf = hashlib.shake_256(seed).digest(nbytes * ctx.n)
    coeffs = np.empty(ctx.n, dtype=object)
    for i in range(ctx.n):
        coeffs[i] = int.from_bytes(buf[i * nbytes:(i + 1) * nbytes], 'little') % ctx.q
    return coeffs.astype(ctx.dtype)


class TwinATHFHE:
    """非共谋双服务器 CKKS-ATASSES 协议（普通 Python 类模拟实体，无网络）。"""

    def __init__(self, threshold: int = 3, ring_degree: int = 256,
                 outer_modulus_bits: int = 60, scale_bits: int = 40,
                 smudge_bits: int = 10, num_clients: int = 4,
                 seed: int = 42):
        """
        Args:
            threshold: T（门限，任意 ≥T 个在线客户端可完成解密）
            ring_degree: M（环次数，2 的幂）
            outer_modulus_bits: 外层模数位宽（Q 取 > 2^bits 的最小素数）
            scale_bits: Δ_ckks = 2^scale_bits
            smudge_bits: B_sm = 2^smudge_bits（掩码噪声界）
            num_clients: N（客户端总数）
            seed: 随机种子
        """
        if num_clients < 1:
            raise ValueError("客户端数 N 必须 ≥ 1")
        self.T = int(threshold)
        self.N = int(num_clients)
        if not (1 <= self.T <= self.N):
            raise ValueError(f"门限 T={self.T} 必须在 [1,{self.N}] 内")
        self.M = int(ring_degree)
        self.delta = 1 << int(scale_bits)
        self.B_sm = 1 << int(smudge_bits)
        self.rng = np.random.RandomState(seed)
        self.seed = seed

        # 外层环（内层复用同一环——不升环）
        self.ctx = RingContext(next_prime(1 << outer_modulus_bits), self.M)
        if self.N >= self.ctx.q:
            raise ValueError("客户端总数 N 必须 < 模数 Q（Lagrange 分母可逆）")

        # ---- 阶段0 Twin-CKKS-Setup ----
        self.S, self.PK = outer_keygen(self.ctx, self.rng)
        # 每客户端 (T,N)-Shamir 份额 s_i（评估点 x=i+1）
        sh = shamir_share(self.ctx, self.S, self.N, self.T, self.rng)
        self.client_shares = [s for _, s in sh]

        # S_H 视图（测试安全属性用）：只含种子与名单，无任何密文/份额
        self.sh_view: Dict[str, list] = {'seeds': [None] * self.N, 'rosters': []}

    # ---- 阶段1 -----------------------------------------------------------
    def outer_encrypt_block(self, block: Sequence[float]):
        return outer_encrypt(self.ctx, self.PK, block, self.delta, self.rng)

    def new_crs(self) -> Tuple[np.ndarray, np.ndarray]:
        """CRS 每轮生成公共基底 a'·u' ∈ R_{M,Q}"""
        a_ = self.ctx.uniform_poly(self.rng)
        u_ = self.ctx.uniform_poly(self.rng)
        return a_, u_

    # ---- 阶段2 Client-OneShot-Upload --------------------------------------
    def client_one_shot(self, c1, s_i: np.ndarray, a_, u_):
        """单轮发完即走：返回 ((c'_{b,0}, c'_{n,0}), (seed1, seed2), n_i, b_i)"""
        ctx = self.ctx
        b_i = ctx.poly_mul(c1, s_i)                       # 外层份额 b_i=c1·s_i
        n_i = ctx.small_uniform(self.rng, self.B_sm)      # 掩码噪声
        # 两个 128-bit 短种子（P_i→S_H 的 TLS 信道只传这 32 字节）
        seed1 = bytes(self.rng.randint(0, 256, size=SEED_BYTES,
                                       dtype=np.int64).astype(np.uint8))
        seed2 = bytes(self.rng.randint(0, 256, size=SEED_BYTES,
                                       dtype=np.int64).astype(np.uint8))
        ek1 = prg_expand_poly(seed1, ctx)                 # PRG 展开均匀随机掩码私钥
        ek2 = prg_expand_poly(seed2, ctx)
        c_b0 = ctx.poly_add(ctx.poly_mul(ctx.poly_mul(a_, ek1), u_), b_i)
        c_n0 = ctx.poly_add(ctx.poly_mul(ctx.poly_mul(a_, ek2), u_), n_i)
        return (c_b0, c_n0), (seed1, seed2), n_i, b_i

    # ---- 阶段3 Twin-Server-Coop -------------------------------------------
    def twin_server_coop(self, roster: Sequence[int],
                         ct0s: Dict[int, tuple],
                         verify_threshold: bool = True) -> Tuple[np.ndarray, dict]:
        """S_A 合成 C'_all,0 并把名单发 S_H；S_H 校验 |T|≥T、展开种子合成
        dk=S1+S2 发 S_A。返回 (dk, S_H 视图记录)。verify_threshold=False
        用于演示 S_H 不校验时被伪造名单攻击（测试不启用，仅文档说明）。"""
        ctx = self.ctx
        roster = sorted(roster)
        if verify_threshold and len(roster) < self.T:
            raise ValueError(
                f"S_H 校验失败：在线名单 |T|={len(roster)} < 门限 T={self.T}"
                f"（拒绝合成 dk，防 S_A 伪造名单套取个体密钥）")
        lam = lagrange_coeffs(ctx, [i + 1 for i in roster])
        lam_map = dict(zip(roster, lam))
        # S_A：非对称线性同态组合（通道 1 乘 L_i，通道 2 直接相加）
        c_all0 = np.zeros(ctx.n, dtype=object)
        for i in roster:
            c_b0, c_n0 = ct0s[i]
            c_all0 = (c_all0 + lam_map[i] * np.asarray(c_b0, dtype=object)
                      + np.asarray(c_n0, dtype=object)) % ctx.q
        c_all0 = c_all0.astype(ctx.dtype)
        c_all1 = None  # C'_all,1 = −a'u'，由调用方持有 CRS

        # S_H：视图只含种子与名单；展开种子合成 dk = S1 + S2
        seeds = [self.sh_view['seeds'][i] for i in roster]
        s1 = np.zeros(ctx.n, dtype=object)
        s2 = np.zeros(ctx.n, dtype=object)
        for i, (sd1, sd2) in zip(roster, seeds):
            ek1 = prg_expand_poly(sd1, ctx)
            ek2 = prg_expand_poly(sd2, ctx)
            s1 = (s1 + lam_map[i] * np.asarray(ek1, dtype=object)) % ctx.q
            s2 = (s2 + np.asarray(ek2, dtype=object)) % ctx.q
        dk = ((s1 + s2) % ctx.q).astype(ctx.dtype)
        record = dict(roster=list(roster), dk=dk, c_all0=c_all0, c_all1=c_all1)
        self.sh_view['rosters'].append(list(roster))
        return dk, record

    # ---- 阶段4 Final-CKKS-Decode ------------------------------------------
    def final_decode(self, c0, c_all0, neg_au, dk) -> np.ndarray:
        """b' = C'_all,0 + (−a'u')·dk = c1·S + Σn_i (mod Q)，精确（无取整）；
        m' = c0 + b'；返回 center_lift(m')/Δ"""
        ctx = self.ctx
        b = ctx.poly_add(c_all0, ctx.poly_mul(neg_au, dk))
        m = (np.asarray(c0, dtype=object) + np.asarray(b, dtype=object)) % ctx.q
        lifted = ctx.center_lift(m.astype(ctx.dtype))
        return np.asarray(lifted, dtype=np.float64) / self.delta

    # ---- 端到端 ----------------------------------------------------------
    def run_protocol(self, client_chunks: Sequence[Sequence[Sequence[float]]],
                     online: Optional[Sequence[int]] = None,
                     ) -> Tuple[List[np.ndarray], dict]:
        """按块并行跑完整四阶段协议（单轮解密、发完即走）。

        Args:
            client_chunks: [客户端][块] → 浮点向量（每块 ≤ M/2 维）
            online: 在线客户端全局编号（None → 全部在线；|T|≥T 校验）

        Returns:
            (聚合和向量列表（每块 M/2 维），成本统计 dict)
        """
        t_start = time.time()
        N = len(client_chunks)
        if N != self.N:
            raise ValueError(f"客户端数 {N} 与协议初始化 N={self.N} 不符")
        n_blocks = len(client_chunks[0])
        if any(len(cc) != n_blocks for cc in client_chunks):
            raise ValueError("所有客户端的块数必须一致")

        online = list(range(N)) if online is None else sorted(online)
        if len(online) < self.T:
            raise ValueError(
                f"在线客户端数 |T|={len(online)} < 门限 T={self.T}，无法解密")

        setup_bytes = self.N * self.ctx.poly_bytes()     # Shamir 份额分发
        upload_bytes = 0    # 阶段1 外层密文：每在线客户端每块 2 个多项式
        seed_bytes = 0      # 阶段2 种子信道：每在线客户端 32 字节（TLS 记录）
        inner_bytes = 0     # 阶段2 掩码密文：每在线客户端每块 2 个多项式（无 Q' 膨胀）

        agg_blocks: List[np.ndarray] = []
        for blk in range(n_blocks):
            # ---- 阶段1：外层加密 + 同态聚合 + 广播 c1 ----
            ct = None
            for g in online:
                ct_i = self.outer_encrypt_block(client_chunks[g][blk])
                upload_bytes += 2 * self.ctx.poly_bytes()
                ct = ct_i if ct is None else outer_add(self.ctx, ct, ct_i)
            c0, c1 = ct
            # ---- 阶段2：Client-OneShot-Upload（单轮发完即走）----
            a_, u_ = self.new_crs()
            neg_au = self.ctx.poly_neg(self.ctx.poly_mul(a_, u_))
            ct0s: Dict[int, tuple] = {}
            self.sh_view['seeds'] = [None] * N             # 本轮种子登记（S_H 视图）
            for g in online:
                (c_b0, c_n0), seeds, _n_i, _b_i = self.client_one_shot(
                    c1, self.client_shares[g], a_, u_)
                ct0s[g] = (c_b0, c_n0)
                self.sh_view['seeds'][g] = seeds
                seed_bytes += TLS_RECORD_BYTES
                inner_bytes += 2 * self.ctx.poly_bytes()
            # ---- 阶段3：Twin-Server-Coop ----
            dk, _rec = self.twin_server_coop(online, ct0s)
            # ---- 阶段4：Final-CKKS-Decode（全程无取整，精确）----
            dec = self.final_decode(c0, _rec['c_all0'], neg_au, dk)
            agg_blocks.append(dec[:self.M // 2].copy())

        crypto_time = time.time() - t_start
        cost = dict(
            athfhe_setup_bytes=setup_bytes,
            athfhe_upload_bytes=upload_bytes,
            athfhe_inner_bytes=inner_bytes,
            athfhe_seed_bytes=seed_bytes,
            athfhe_crypto_time=crypto_time,
            athfhe_num_online=len(online),
            athfhe_num_blocks=n_blocks,
            athfhe_M=self.M,
            athfhe_Q_bits=self.ctx.q.bit_length(),
            athfhe_delta_bits=self.delta.bit_length() - 1,
        )
        return agg_blocks, cost


__all__ = ['TwinATHFHE', 'prg_expand_poly', 'SEED_BYTES', 'TLS_RECORD_BYTES']
