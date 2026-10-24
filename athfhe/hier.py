"""
athfhe/hier.py：方案一——面向 CKKS 的分簇层次架构
（Edge-Assisted Hierarchical CKKS-ATASSES）

实体：云服务器 CS、C 个簇头 CH_c、N 个客户端 P_{c,i}（第 c 簇 n_c 个，
Σn_c=N）。两圈加密：

- 外层（CKKS，全程）：保护梯度数据。客户端加密梯度块 → CH_c 簇内密文
  相加 → CS 全量相加得 CT=(c0,c1)，解密核心 c0 + c1·S。
- 内层（ATASSES BFV 式，仅解密阶段两回合）：保护部分解密份额 b_{c,i} =
  c1·s_{c,i}——若明文上传簇头可用多个份额反推私钥，故在更大环 R_{M,Q'}
  上用 Δ' 编码 + 取整把内层底噪 100% 归零（论文"选取略大的密文空间"）。

四阶段（doc/athfhe.md §方案一）：
- 阶段0 Hier-CKKS-Setup：S 簇间加法拆分为 S^(c)（前 C−1 随机、第 C 个
  补齐），簇内 (t_c,n_c)-Shamir 分片（评估点 α_{c,i}=1..n_c，模素数 Q）。
- 阶段1 Outer-CKKS-Agg：外层加密/两级密文聚合，CS 广播 c1 求 b=c1·S。
- 阶段2 Cluster-R1（在线集 T1，|T1|≥t_c）：b_{c,i}=c1·s_{c,i}；两把内层
  私钥 ek1/ek2 各自簇内 Shamir 分片；掩码噪声 n_{c,i}←[−B_sm,B_sm]^M 与
  内层底噪 e'（界 B'）；CRS 公共基底 −a'_c·u'_c；双密文
  CTb/CTn 以 Δ' 编码 b / n 上传 CH_c；CH_c 广播 Lagrange 系数。
- 阶段3 Cluster-R2（在线集 T2，允许 ≠T1）：节点 j 合成
  dkShare_{c,j}=Σ_{i∈T1} L_i·ekShare_{i,j,1}+Σ ekShare_{i,j,2}；CH_c
  Shamir 插值得 dk_c=Σ(L_i·ek1+ek2)，合成 C'_all,c,0，取整去噪
  b'_c=round((C'_all,c,0+(−a'u')·dk_c)/Δ') mod Q = c1·S^(c)+Σn（底噪
  100% 消除，要求 Q' > 2·n_c·Q²·B'，初始化校验，不满足 raise）。
- 阶段4 Cloud-CKKS-Decode：m'=c0+Σ_c b'_c，G=center_lift(m')/Δ_ckks。

异步掉线：T1（第一回合密文上传）与 T2（第二回合密钥份额上传）可不同，
每簇均只需 ≥t_c 在线。

通信/计算成本按字节数与 wall-clock 时间累计进 cost dict（见 run_protocol）。
"""

import multiprocessing as mp
import os
import time
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from athfhe.ckks_toy import (
    RingContext, next_prime, outer_keygen, outer_encrypt, outer_add,
    shamir_share, lagrange_coeffs,
)
from athfhe.twin import block_rng, _PARALLEL_MIN_BLOCKS, _PARALLEL_MAX_WORKERS


class HierATHFHE:
    """分簇层次 CKKS-ATASSES 协议（普通 Python 类模拟实体，无网络）。"""

    def __init__(self, num_clusters: int = 2, cluster_threshold: int = 2,
                 ring_degree: int = 256, outer_modulus_bits: int = 60,
                 scale_bits: int = 40, inner_noise_bits: int = 20,
                 smudge_bits: int = 10,
                 cluster_sizes: Optional[Sequence[int]] = None,
                 q_prime_bits: Optional[int] = None,
                 seed: int = 42, parallel: bool = True,
                 parallel_workers: int = 0, parallel_progress: bool = False):
        """
        Args:
            num_clusters: C（簇数）
            cluster_threshold: t_c（每簇门限，假设各簇相同）
            ring_degree: M（环次数，2 的幂）
            outer_modulus_bits: 外层模数位宽（Q 取 > 2^bits 的最小素数）
            scale_bits: Δ_ckks = 2^scale_bits
            inner_noise_bits: B' = 2^inner_noise_bits（内层底噪界）
            smudge_bits: B_sm = 2^smudge_bits（掩码噪声界）
            cluster_sizes: 每簇客户端数 [n_0..n_{C-1}]（None → 均分）
            q_prime_bits: 显式指定内层模数位宽（默认按论文界取最小素数；
                          显式给定时若违反 Q' > 2·n_c·Q²·B' 则 raise——测试用）
            seed: 随机种子
            parallel: 块间多进程并行（每块随机流 block_rng(seed, blk)，
                      并行结果与串行逐位一致）
            parallel_workers: 并行进程数（0 = 自动）
            parallel_progress: 并行时每 50 块打印进度（默认静默）
        """
        if num_clusters < 1:
            raise ValueError("簇数 C 必须 ≥ 1")
        self.C = int(num_clusters)
        self.t_c = int(cluster_threshold)
        self.M = int(ring_degree)
        self.delta = 1 << int(scale_bits)
        self.B_sm = 1 << int(smudge_bits)
        self.B_prime = 1 << int(inner_noise_bits)
        self.rng = np.random.RandomState(seed)
        self.seed = seed
        self.parallel = bool(parallel)
        self.parallel_workers = int(parallel_workers)
        self.parallel_progress = bool(parallel_progress)
        self._pool = None          # 跨轮复用的进程池（惰性创建）
        self._pool_workers = 0

        # 外层环：Q 取 > 2^outer_modulus_bits 的最小素数
        self.ctx_outer = RingContext(next_prime(1 << outer_modulus_bits), self.M)

        # 分簇规模
        if cluster_sizes is None:
            base = 2  # 默认每簇 2 个（与配置默认 t_c=2 匹配）
            cluster_sizes = [base] * self.C
        if len(cluster_sizes) != self.C:
            raise ValueError("cluster_sizes 长度必须等于簇数 C")
        if any(n < self.t_c for n in cluster_sizes):
            raise ValueError("每簇客户端数 n_c 必须 ≥ t_c")
        self.cluster_sizes = [int(n) for n in cluster_sizes]
        self.N = sum(self.cluster_sizes)
        self.n_max = max(self.cluster_sizes)
        if self.N >= self.ctx_outer.q:
            raise ValueError("客户端总数 N 必须 < 外层模数 Q（Lagrange 分母可逆）")

        # 内层环：Q' > 2·n_c·Q²·B'（论文 ATASSES 参数界，取最小素数）
        bound = 2 * self.n_max * self.ctx_outer.q ** 2 * self.B_prime
        if q_prime_bits is not None:
            q_prime = next_prime(1 << q_prime_bits)
            if q_prime <= bound:
                raise ValueError(
                    f"内层模数不满足论文参数界：Q'={q_prime} ≈ 2^{q_prime_bits} "
                    f"≤ 2·n_c·Q²·B' = {bound}（需 Q' 更大，见 doc/athfhe.md §参数界）")
        else:
            q_prime = next_prime(bound)
        self.ctx_inner = RingContext(q_prime, self.M)
        self.delta_prime = self.ctx_inner.q // self.ctx_outer.q

        # ---- 阶段0 Hier-CKKS-Setup ----
        self.S, self.PK = outer_keygen(self.ctx_outer, self.rng)
        # S 簇间加法拆分：前 C−1 个随机，第 C 个补齐（S = Σ S^(c) mod Q）
        self.S_clusters: List[np.ndarray] = []
        acc = self.ctx_outer.zero().astype(object)
        for _ in range(self.C - 1):
            sc = self.ctx_outer.uniform_poly(self.rng)
            self.S_clusters.append(sc)
            acc = (acc + np.asarray(sc, dtype=object)) % self.ctx_outer.q
        last = (np.asarray(self.S, dtype=object) - acc) % self.ctx_outer.q
        self.S_clusters.append(last.astype(self.ctx_outer.dtype))
        # 簇内 (t_c,n_c)-Shamir：客户端 (c,i) 持 s_{c,i}（评估点 x=i+1）
        self.shares: List[List[np.ndarray]] = []   # shares[c] = [s_{c,0},...,s_{c,n_c-1}]
        for c in range(self.C):
            sh = shamir_share(self.ctx_outer, self.S_clusters[c],
                              self.cluster_sizes[c], self.t_c, self.rng)
            self.shares.append([s for _, s in sh])

        self.last_cluster_views: Dict[int, dict] = {}

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

    # ---- 成本统计 --------------------------------------------------------
    def _poly_bytes_outer(self) -> int:
        return self.ctx_outer.poly_bytes()

    def _poly_bytes_inner(self) -> int:
        return self.ctx_inner.poly_bytes()

    # ---- 阶段1 Outer-CKKS-Agg -------------------------------------------
    def outer_encrypt_block(self, block: Sequence[float],
                            rng: Optional[np.random.RandomState] = None):
        return outer_encrypt(self.ctx_outer, self.PK, block, self.delta,
                             self.rng if rng is None else rng)

    # ---- 阶段2 Cluster-R1 -------------------------------------------------
    def cluster_r1(self, c: int, c1, online1: Sequence[int],
                   rng: Optional[np.random.RandomState] = None) -> dict:
        """第 c 簇第一回合（在线集 T1，|T1|≥t_c）：客户端内层加密份额与噪声，
        CH_c 广播 Lagrange 系数。返回 per-client 记录（测试/安全冒烟用）。"""
        ctx_o, ctx_i = self.ctx_outer, self.ctx_inner
        rng = self.rng if rng is None else rng
        if len(online1) < self.t_c:
            raise ValueError(f"簇 {c} 第一回合在线数 {len(online1)} < t_c={self.t_c}")
        # CRS 生成本轮公共基底 −a'_c·u'_c
        a_c = ctx_i.uniform_poly(rng)
        u_c = ctx_i.uniform_poly(rng)
        neg_au = ctx_i.poly_neg(ctx_i.poly_mul(a_c, u_c))
        lam = lagrange_coeffs(ctx_o, [i + 1 for i in online1])

        recs = {}
        for i in online1:
            s_ci = self.shares[c][i]
            b_ci = ctx_o.poly_mul(c1, s_ci)              # 外层份额 b_{c,i}=c1·s_{c,i}
            # 两把内层私钥 + 簇内 Shamir 分片（评估点 x=j+1）
            ek1 = ctx_i.gauss_poly(rng)                  # 小系数私钥
            ek2 = ctx_i.gauss_poly(rng)
            sh1 = shamir_share(ctx_i, ek1, self.cluster_sizes[c], self.t_c, rng)
            sh2 = shamir_share(ctx_i, ek2, self.cluster_sizes[c], self.t_c, rng)
            n_ci = ctx_i.small_uniform(rng, self.B_sm)      # 掩码噪声
            e1 = ctx_i.small_uniform(rng, self.B_prime)     # 内层底噪（界 B'）
            e2 = ctx_i.small_uniform(rng, self.B_prime)
            au1 = ctx_i.poly_mul(ctx_i.poly_mul(a_c, ek1), u_c)
            au2 = ctx_i.poly_mul(ctx_i.poly_mul(a_c, ek2), u_c)
            # Δ' 编码：Δ'·b 与 Δ'·n 视为 [0,Q) 代表直接嵌入内层环
            db = (np.asarray(b_ci, dtype=object) * self.delta_prime) % ctx_i.q
            dn = (np.asarray(n_ci, dtype=object) * self.delta_prime) % ctx_i.q
            ct_b0 = (au1 + db + np.asarray(e1, dtype=object)) % ctx_i.q
            ct_n0 = (au2 + dn + np.asarray(e2, dtype=object)) % ctx_i.q
            ct_b0 = ct_b0.astype(ctx_i.dtype)
            ct_n0 = ct_n0.astype(ctx_i.dtype)
            recs[i] = dict(b=b_ci, ek_shares_1=sh1, ek_shares_2=sh2,
                           n=n_ci, ct_b=(ct_b0, neg_au), ct_n=(ct_n0, neg_au))
        view = dict(crs_neg_au=neg_au, lambdas=lam, clients=recs)
        self.last_cluster_views[c] = view
        return view

    # ---- 阶段3 Cluster-R2 -------------------------------------------------
    def cluster_r2(self, c: int, view: dict, online2: Sequence[int]):
        """第 c 簇第二回合（在线集 T2，允许 ≠T1）：节点 j 合成 dkShare 发给
        CH_c；CH_c 插值得 dk_c，合成 C'_all,c,0，取整去噪得簇级份额 b'_c。"""
        ctx_o, ctx_i = self.ctx_outer, self.ctx_inner
        if len(online2) < self.t_c:
            raise ValueError(f"簇 {c} 第二回合在线数 {len(online2)} < t_c={self.t_c}")
        online1 = sorted(view['clients'].keys())
        lam = view['lambdas']
        lam_map = dict(zip(online1, lam))

        # 节点 j∈T2 本地合成联合解密密钥的份额（内层环 mod Q'）
        dk_shares = []
        for j in online2:
            acc = np.zeros(ctx_i.n, dtype=object)
            for i in online1:
                L_i = lam_map[i]
                acc = (acc + L_i * np.asarray(view['clients'][i]['ek_shares_1'][j][1],
                                              dtype=object)) % ctx_i.q
            for i in online1:
                acc = (acc + np.asarray(view['clients'][i]['ek_shares_2'][j][1],
                                        dtype=object)) % ctx_i.q
            dk_shares.append((j + 1, acc.astype(ctx_i.dtype)))
        # CH_c：x=0 处插值恢复 dk_c = Σ_{i∈T1}(L_i·ek1 + ek2)（逐系数）
        xs = [x for x, _ in dk_shares]
        lambdas_r2 = lagrange_coeffs(ctx_i, xs)
        dk = np.zeros(ctx_i.n, dtype=object)
        for lam_j, (_, sh) in zip(lambdas_r2, dk_shares):
            dk = (dk + lam_j * np.asarray(sh, dtype=object)) % ctx_i.q
        dk = dk.astype(ctx_i.dtype)

        # CH_c 合成簇内总密文首项（通道 1 乘 L_i，通道 2 直接相加）
        c_all0 = np.zeros(ctx_i.n, dtype=object)
        for i in online1:
            L_i = lam_map[i]
            ct_b0 = view['clients'][i]['ct_b'][0]
            ct_n0 = view['clients'][i]['ct_n'][0]
            c_all0 = (c_all0 + L_i * np.asarray(ct_b0, dtype=object)
                      + np.asarray(ct_n0, dtype=object)) % ctx_i.q
        c_all0 = c_all0.astype(ctx_i.dtype)
        # 取整去噪：b'_c = round((C'_all,0 + (−a'u')·dk_c) / Δ') mod Q。
        # 注意此处不能用 center_lift：Δ'·b 可达 Δ'(Q−1) ≈ Q'（BFV 式高位
        # 编码），必须取 [0,Q') 非负代表，round 除法才还原 b（内层底噪
        # E'_total 满足 |E'| < Δ'/2——Q' > 2·n_c·Q²·B' 保证——被 100% 消除）
        masked = ctx_i.poly_add(c_all0, ctx_i.poly_mul(view['crs_neg_au'], dk))
        v = np.asarray(masked, dtype=object).ravel()     # 已是 [0,Q') 代表
        quot = np.asarray([(2 * int(x) + self.delta_prime) // (2 * self.delta_prime)
                           for x in v], dtype=object).reshape(ctx_i.n)
        b_c = np.asarray(quot % ctx_o.q, dtype=ctx_o.dtype)
        return b_c, dict(dk=dk, c_all0=c_all0, online1=online1, online2=list(online2))

    # ---- 阶段4 Cloud-CKKS-Decode -----------------------------------------
    def cloud_decode(self, c0, cluster_shares: Sequence[np.ndarray]) -> np.ndarray:
        """m' = c0 + Σ_c b'_c (mod Q)；返回 center_lift(m')/Δ（长度 M float）"""
        m = np.asarray(c0, dtype=object) % self.ctx_outer.q
        for b_c in cluster_shares:
            m = (m + np.asarray(b_c, dtype=object)) % self.ctx_outer.q
        lifted = self.ctx_outer.center_lift(m.astype(self.ctx_outer.dtype))
        return np.asarray(lifted, dtype=np.float64) / self.delta

    # ---- 单块四阶段（并行 worker 与串行共用）----------------------------
    def run_block(self, blk: int, blocks: Dict[int, Sequence[float]],
                  online1: Sequence[int], per_cluster1: Dict[int, List[int]],
                  per_cluster2: Dict[int, List[int]],
                  rng: Optional[np.random.RandomState] = None) -> np.ndarray:
        """单个块的四阶段协议。blocks: {全局编号 g: 该块浮点向量}（在线集
        online1 的梯度参与外层聚合）。返回聚合和向量（M/2 维）。"""
        rng = self.rng if rng is None else rng
        ct = None
        for g in online1:
            ct_i = outer_encrypt(self.ctx_outer, self.PK, blocks[g],
                                 self.delta, rng)
            ct = ct_i if ct is None else outer_add(self.ctx_outer, ct, ct_i)
        c0, c1 = ct
        cluster_b = []
        for c in range(self.C):
            view = self.cluster_r1(c, c1, per_cluster1[c], rng=rng)
            b_c, _ = self.cluster_r2(c, view, per_cluster2[c])
            cluster_b.append(b_c)
        dec = self.cloud_decode(c0, cluster_b)
        return dec[:self.M // 2].copy()

    # ---- 端到端 ----------------------------------------------------------
    def run_protocol(self, client_chunks: Sequence[Sequence[Sequence[float]]],
                     online1: Optional[Sequence[int]] = None,
                     online2: Optional[Sequence[int]] = None,
                     ) -> Tuple[List[np.ndarray], dict]:
        """按块跑完整四阶段协议。

        Args:
            client_chunks: [客户端][块] → 浮点向量（每块 ≤ M/2 维）
            online1: 第一回合在线客户端全局编号（None → 全部在线）
            online2: 第二回合在线客户端全局编号（None = online1；允许 ≠online1，
                     只要每簇两回合在线数均 ≥ t_c）

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

        # 客户端 → (簇, 簇内编号)
        assignment = []
        for c, n_c in enumerate(self.cluster_sizes):
            assignment.extend([(c, i) for i in range(n_c)])

        online1 = list(range(N)) if online1 is None else sorted(online1)
        online2 = (list(online1) if online2 is None else sorted(online2))
        per_cluster1: Dict[int, List[int]] = {c: [] for c in range(self.C)}
        per_cluster2: Dict[int, List[int]] = {c: [] for c in range(self.C)}
        for g in online1:
            per_cluster1[assignment[g][0]].append(assignment[g][1])
        for g in online2:
            per_cluster2[assignment[g][0]].append(assignment[g][1])

        setup_bytes = 0   # 阶段0 密钥分发：每客户端外层私钥份额多项式
        for c in range(self.C):
            setup_bytes += self.cluster_sizes[c] * self._poly_bytes_outer()

        n1, n2 = len(online1), len(online2)
        pb_o = self._poly_bytes_outer()
        pb_i = self._poly_bytes_inner()
        per_block = dict(
            athfhe_comm_p1_upload=2 * pb_o * n1,            # P→CH/CS 外层密文
            athfhe_comm_p1_broadcast_c1=pb_o,               # CS→全体P 广播 c1
            athfhe_comm_p2_ct=2 * pb_i * n1,                # P→CH 内层双密文首项
            athfhe_comm_p2_ek=2 * pb_i * n1,                # P→CH ek1/ek2 簇内份额
            athfhe_comm_p3_dkshare=pb_i * n2,               # 节点 j→CH dkShare
            athfhe_comm_p3_bc=pb_o * self.C,                # CH_c→CS 簇级份额 b'_c
        )

        agg_blocks: List[Optional[np.ndarray]] = [None] * n_blocks
        if self.parallel:
            cpu = os.cpu_count() or 1
            workers = self.parallel_workers if self.parallel_workers > 0 \
                else min(cpu, _PARALLEL_MAX_WORKERS)
            workers = min(workers, cpu, n_blocks)
        else:
            workers = 1

        if workers > 1 and n_blocks >= _PARALLEL_MIN_BLOCKS:
            tasks = [(blk, {g: client_chunks[g][blk] for g in online1},
                      online1, per_cluster1, per_cluster2)
                     for blk in range(n_blocks)]
            # 跨轮复用进程池：避免每轮重新 spawn（Windows 下每个 worker
            # 重导入 __main__/torch 既慢又占内存）
            if self._pool is None or self._pool_workers != workers:
                self._close_pool()
                self._pool = mp.Pool(workers, initializer=_hier_pool_init,
                                     initargs=(self,))
                self._pool_workers = workers
            pool = self._pool
            done = 0
            for blk, dec in pool.imap(_hier_pool_worker, tasks):
                agg_blocks[blk] = dec
                done += 1
                if self.parallel_progress and done % 50 == 0:
                    from base.log_utils import tag_info as _ti, LogTag as _LT
                    _ti(_LT.ROUND,
                        f"AThFHE-Hier 块并行进度 {done}/{n_blocks}")
        else:
            for blk in range(n_blocks):
                blocks = {g: client_chunks[g][blk] for g in online1}
                agg_blocks[blk] = self.run_block(
                    blk, blocks, online1, per_cluster1, per_cluster2,
                    rng=block_rng(self.seed, blk))

        crypto_time = time.time() - t_start
        flow_total = sum(per_block.values()) * n_blocks
        cost = dict(
            athfhe_setup_bytes=setup_bytes,
            # 聚合键（兼容旧口径，由细分键推导）
            athfhe_upload_bytes=per_block['athfhe_comm_p1_upload'] * n_blocks,
            athfhe_inner_bytes=(per_block['athfhe_comm_p2_ct']
                                + per_block['athfhe_comm_p2_ek']
                                + per_block['athfhe_comm_p3_dkshare']
                                + per_block['athfhe_comm_p3_bc']) * n_blocks,
            # 逐流程细分键（每轮；广播/dkShare/b'_c 按发送一次计）
            **{k: v * n_blocks for k, v in per_block.items()},
            athfhe_comm_total_bytes=flow_total,
            athfhe_crypto_time=crypto_time,
            athfhe_num_online1=len(online1),
            athfhe_num_online2=len(online2),
            athfhe_num_blocks=n_blocks,
            athfhe_M=self.M,
            athfhe_Q_bits=self.ctx_outer.q.bit_length(),
            athfhe_Qprime_bits=self.ctx_inner.q.bit_length(),
            athfhe_delta_bits=self.delta.bit_length() - 1,
            athfhe_delta_prime_bits=self.delta_prime.bit_length() - 1,
        )
        return list(agg_blocks), cost


# =====================================================================
# 块并行 worker（Windows spawn 兼容：模块级函数 + initializer 缓存协议）
# =====================================================================
_HIER_POOL_PROTO = None


def _hier_pool_init(proto: HierATHFHE) -> None:
    """Pool initializer：把协议实例缓存为 worker 全局（仅初始化时 pickle 一次）"""
    global _HIER_POOL_PROTO
    _HIER_POOL_PROTO = proto


def _hier_pool_worker(args):
    """单块任务：(blk, blocks, online1, per_cluster1, per_cluster2)
    → (blk, dec)"""
    blk, blocks, online1, per_cluster1, per_cluster2 = args
    proto = _HIER_POOL_PROTO
    dec = proto.run_block(blk, blocks, online1, per_cluster1, per_cluster2,
                          rng=block_rng(proto.seed, blk))
    return blk, dec


__all__ = ['HierATHFHE']
