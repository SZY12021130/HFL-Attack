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
import multiprocessing as mp
import os
import time
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from athfhe.ckks_toy import (
    RingContext, next_prime, outer_keygen, outer_encrypt, outer_add,
    shamir_share, lagrange_coeffs,
)

SEED_BYTES = 16          # 128-bit 种子
TLS_RECORD_BYTES = 32    # P_i→S_H 两条种子的安全信道记录字节数

# 块并行的确定性随机源：每块独立 RandomState(seed, blk)，与串行逐位一致
_PARALLEL_MIN_BLOCKS = 8     # 块数低于此值退回串行
_PARALLEL_MAX_WORKERS = 8    # Windows spawn 下每 worker 重导入 __main__（torch 等），
                             # 自动模式封顶，避免内存爆


def block_rng(seed: int, blk: int) -> np.random.RandomState:
    """块级确定性随机流：SeedSequence([seed, blk]) 派生（跨进程稳定）"""
    st = np.random.SeedSequence([int(seed) & 0xFFFFFFFF,
                                 int(blk) & 0xFFFFFFFF]).generate_state(1)
    return np.random.RandomState(int(st[0]))


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
                 seed: int = 42, parallel: bool = True,
                 parallel_workers: int = 0, parallel_progress: bool = False):
        """
        Args:
            threshold: T（门限，任意 ≥T 个在线客户端可完成解密）
            ring_degree: M（环次数，2 的幂）
            outer_modulus_bits: 外层模数位宽（Q 取 > 2^bits 的最小素数）
            scale_bits: Δ_ckks = 2^scale_bits
            smudge_bits: B_sm = 2^smudge_bits（掩码噪声界）
            num_clients: N（客户端总数）
            seed: 随机种子
            parallel: 块间多进程并行（True 时 workers>1 且块数 ≥8 才生效；
                      每块随机流由 block_rng(seed, blk) 派生，并行结果与
                      串行逐位一致）
            parallel_workers: 并行进程数（0 = 自动 min(cpu_count, n_blocks)）
            parallel_progress: 并行时每 50 块打印一次进度（默认静默）
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
        self.parallel = bool(parallel)
        self.parallel_workers = int(parallel_workers)
        self.parallel_progress = bool(parallel_progress)
        self._pool = None          # 跨轮复用的进程池（惰性创建）
        self._pool_workers = 0

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
    def outer_encrypt_block(self, block: Sequence[float],
                            rng: Optional[np.random.RandomState] = None):
        return outer_encrypt(self.ctx, self.PK, block, self.delta,
                             self.rng if rng is None else rng)

    def new_crs(self, rng: Optional[np.random.RandomState] = None
                ) -> Tuple[np.ndarray, np.ndarray]:
        """CRS 每轮生成公共基底 a'·u' ∈ R_{M,Q}"""
        rng = self.rng if rng is None else rng
        a_ = self.ctx.uniform_poly(rng)
        u_ = self.ctx.uniform_poly(rng)
        return a_, u_

    # ---- 阶段2 Client-OneShot-Upload --------------------------------------
    def client_one_shot(self, c1, s_i: np.ndarray, a_, u_,
                        rng: Optional[np.random.RandomState] = None):
        """单轮发完即走：返回 ((c'_{b,0}, c'_{n,0}), (seed1, seed2), n_i, b_i)"""
        ctx = self.ctx
        rng = self.rng if rng is None else rng
        b_i = ctx.poly_mul(c1, s_i)                       # 外层份额 b_i=c1·s_i
        n_i = ctx.small_uniform(rng, self.B_sm)           # 掩码噪声
        # 两个 128-bit 短种子（P_i→S_H 的 TLS 信道只传这 32 字节）
        seed1 = bytes(rng.randint(0, 256, size=SEED_BYTES,
                                  dtype=np.int64).astype(np.uint8))
        seed2 = bytes(rng.randint(0, 256, size=SEED_BYTES,
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

    def _close_pool(self) -> None:
        """关闭跨轮复用的进程池（实验结束或 worker 数变化时调用）"""
        if self._pool is not None:
            self._pool.terminate()
            self._pool.join()
            self._pool = None
            self._pool_workers = 0

    def __del__(self):
        try:
            self._close_pool()
        except Exception:
            pass

    # ---- 单块四阶段（并行 worker 与串行共用，rng 决定随机流）--------------
    def run_block(self, blk: int, blocks: Dict[int, Sequence[float]],
                  online: Sequence[int],
                  rng: Optional[np.random.RandomState] = None
                  ) -> Tuple[np.ndarray, Dict[int, tuple]]:
        """单个块的四阶段协议。blocks: {全局编号 g: 该块浮点向量}。

        返回 (聚合和向量（M/2 维）, {g: (seed1, seed2)} S_H 种子登记)。
        串行路径传 block_rng(self.seed, blk) 时与并行逐位一致。"""
        rng = self.rng if rng is None else rng
        ctx = self.ctx
        # ---- 阶段1：外层加密 + 同态聚合 + 广播 c1 ----
        ct = None
        for g in online:
            ct_i = outer_encrypt(ctx, self.PK, blocks[g], self.delta, rng)
            ct = ct_i if ct is None else outer_add(ctx, ct, ct_i)
        c0, c1 = ct
        # ---- 阶段2：Client-OneShot-Upload（单轮发完即走）----
        a_, u_ = self.new_crs(rng)
        neg_au = ctx.poly_neg(ctx.poly_mul(a_, u_))
        ct0s: Dict[int, tuple] = {}
        seeds_map: Dict[int, tuple] = {}
        self.sh_view['seeds'] = [None] * self.N      # 本轮种子登记（S_H 视图）
        for g in online:
            (c_b0, c_n0), seeds, _n_i, _b_i = self.client_one_shot(
                c1, self.client_shares[g], a_, u_, rng=rng)
            ct0s[g] = (c_b0, c_n0)
            seeds_map[g] = seeds
            self.sh_view['seeds'][g] = seeds
        # ---- 阶段3：Twin-Server-Coop ----
        dk, rec = self.twin_server_coop(online, ct0s)
        # ---- 阶段4：Final-CKKS-Decode（全程无取整，精确）----
        dec = self.final_decode(c0, rec['c_all0'], neg_au, dk)
        return dec[:self.M // 2].copy(), seeds_map

    # ---- 端到端 ----------------------------------------------------------
    def run_protocol(self, client_chunks: Sequence[Sequence[Sequence[float]]],
                     online: Optional[Sequence[int]] = None,
                     ) -> Tuple[List[np.ndarray], dict]:
        """按块跑完整四阶段协议（单轮解密、发完即走）。

        Args:
            client_chunks: [客户端][块] → 浮点向量（每块 ≤ M/2 维）
            online: 在线客户端全局编号（None → 全部在线；|T|≥T 校验）

        Returns:
            (聚合和向量列表（每块 M/2 维），成本统计 dict)

        块间可选多进程并行（self.parallel）：每块随机流为
        block_rng(self.seed, blk)，并行 agg 结果与串行逐位一致。
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
        n_on = len(online)
        pb = self.ctx.poly_bytes()

        agg_blocks: List[Optional[np.ndarray]] = [None] * n_blocks
        if self.parallel:
            cpu = os.cpu_count() or 1
            workers = self.parallel_workers if self.parallel_workers > 0 \
                else min(cpu, _PARALLEL_MAX_WORKERS)
            workers = min(workers, cpu, n_blocks)
        else:
            workers = 1

        if workers > 1 and n_blocks >= _PARALLEL_MIN_BLOCKS:
            tasks = [(blk, online,
                      {g: client_chunks[g][blk] for g in online})
                     for blk in range(n_blocks)]
            # 跨轮复用进程池：避免每轮重新 spawn（Windows 下每个 worker
            # 重导入 __main__/torch 既慢又占内存）
            if self._pool is None or self._pool_workers != workers:
                self._close_pool()
                self._pool = mp.Pool(workers, initializer=_twin_pool_init,
                                     initargs=(self,))
                self._pool_workers = workers
            pool = self._pool
            done = 0
            for blk, dec, seeds_map in pool.imap(_twin_pool_worker, tasks):
                agg_blocks[blk] = dec
                # 主进程同步 S_H 视图（串行语义：每块重置 seeds、追加名单）
                self.sh_view['seeds'] = [None] * N
                for g, sd in seeds_map.items():
                    self.sh_view['seeds'][g] = sd
                self.sh_view['rosters'].append(list(online))
                done += 1
                if self.parallel_progress and done % 50 == 0:
                    from base.log_utils import tag_info as _ti, LogTag as _LT
                    _ti(_LT.ROUND,
                        f"AThFHE-Twin 块并行进度 {done}/{n_blocks}")
        else:
            for blk in range(n_blocks):
                blocks = {g: client_chunks[g][blk] for g in online}
                dec, _seeds = self.run_block(
                    blk, blocks, online, rng=block_rng(self.seed, blk))
                agg_blocks[blk] = dec

        crypto_time = time.time() - t_start
        cost = dict(
            athfhe_setup_bytes=setup_bytes,
            # 聚合键（兼容旧口径，由细分键推导）
            athfhe_upload_bytes=2 * pb * n_on * n_blocks,
            athfhe_inner_bytes=2 * pb * n_on * n_blocks,
            athfhe_seed_bytes=TLS_RECORD_BYTES * n_on * n_blocks,
            # 逐流程细分键（每轮；广播/名单/dk 按发送一次计）
            athfhe_comm_p1_upload=2 * pb * n_on * n_blocks,       # P_i→S_A 外层密文
            athfhe_comm_p1_broadcast_c1=pb * n_blocks,            # S_A→全体P_i 广播 c1
            athfhe_comm_p2_mask=2 * pb * n_on * n_blocks,         # P_i→S_A 掩码密文
            athfhe_comm_p2_seed=TLS_RECORD_BYTES * n_on * n_blocks,  # P_i→S_H 种子
            athfhe_comm_p3_list=2 * n_on,                         # S_A→S_H 名单 |T|×2B
            athfhe_comm_p3_dk=pb * n_blocks,                      # S_H→S_A 联合密钥 dk
            athfhe_comm_total_bytes=(
                2 * pb * n_on * n_blocks          # p1_upload
                + pb * n_blocks                   # p1_broadcast_c1
                + 2 * pb * n_on * n_blocks        # p2_mask
                + TLS_RECORD_BYTES * n_on * n_blocks  # p2_seed
                + 2 * n_on                        # p3_list
                + pb * n_blocks),                 # p3_dk
            athfhe_crypto_time=crypto_time,
            athfhe_num_online=n_on,
            athfhe_num_blocks=n_blocks,
            athfhe_M=self.M,
            athfhe_Q_bits=self.ctx.q.bit_length(),
            athfhe_delta_bits=self.delta.bit_length() - 1,
        )
        return list(agg_blocks), cost


# =====================================================================
# 块并行 worker（Windows spawn 兼容：模块级函数 + initializer 缓存协议）
# =====================================================================
_TWIN_POOL_PROTO = None


def _twin_pool_init(proto: TwinATHFHE) -> None:
    """Pool initializer：把协议实例缓存为 worker 全局（仅初始化时 pickle 一次）"""
    global _TWIN_POOL_PROTO
    _TWIN_POOL_PROTO = proto


def _twin_pool_worker(args):
    """单块任务：(blk, online, blocks) → (blk, dec, seeds_map)"""
    blk, online, blocks = args
    proto = _TWIN_POOL_PROTO
    dec, seeds_map = proto.run_block(
        blk, blocks, online, rng=block_rng(proto.seed, blk))
    return blk, dec, seeds_map


__all__ = ['TwinATHFHE', 'prg_expand_poly', 'block_rng',
           'SEED_BYTES', 'TLS_RECORD_BYTES']
