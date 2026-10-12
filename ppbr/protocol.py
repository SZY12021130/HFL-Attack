"""
PPBRProtocol —— 完整协议编排（双轨架构的 PPBR 轨道）
=====================================================

开启 ``ppbr.enabled`` 后，**整条加解密路线切换为 PPBR 协议**（He 等,
IEEE TMC 2026），不走全局掩码投票 / TopK 稀疏化 / 单密钥 CKKS 聚合。
与 CKKS 轨道共享：数据加载划分、模型、客户端本地训练、攻击注入
（hfl_attacks）、边缘-客户端分组（HierarchyController）、指标采集。

一轮的完整时序（对应 paperlist/ppbr_notes.md §6 的 15 步消息流）：

  0. 云明文广播 w_a（论文设定：全局模型公开，任何客户端可得）
  1. 客户端本地训练得 Δ，攻击注入在 Δ 上完成（本项目威胁模型：
     恶意客户端/被攻破客户端，攻击发生在加密上传之前）
  2. 算法 2 (CC)：客户端对广播坐标子集 G 统计符号元组（正/负/零号
     比例）→ CLDP 扰动（式 2）→ 边缘只做 trimmed k-means 过滤
     （边缘唯一可见信息：3 维扰动符号元组）
  3. 可信客户端更新动量 m ← φ·m + (1−φ)·Δ（算法 3 第 1 行）
  4. 客户端：采相对噪声 x；m' = m − x；``[x]_A`` 用云公钥加密发往边缘；
     z = m' + 成对 SA 掩码 + 自负载掩码 PRG(b_j) 发往边缘
     （SA：真实 X25519+HKDF 密钥协商 + Shamir 掉线恢复，见 ppbr/sa.py；
       sa_key_exchange="sim" 时可退回哈希种子模拟）
  5. 边缘（算法 3）：SA 求和（掩码相消，掉线经 Shamir 份额恢复，云/边缘
     只见聚合均值）得 m'_i；同态求和 ``[x_i]_A = Σ[x_{i,j}]_A``；
     加密 ``[2m'_i]_{Ei}``（边缘私钥自持）发往云
  6. 云范数盲化（算法 4，两轮点积协议——TenSEAL 无 rotate，无法直接
     密文内求和，改为云/边缘协作两轮）：
       云解密 ``[x_i]_A`` 得 x_i；取随机标量 r_i，计算
       ``[v]_{Ei} = [2m'_i]_{Ei} ∘ (r_i·x_i)`` 发回边缘；
       边缘解密得 v = 2r_i·m'_i∘x_i，自算 S_i = Σv = 2r_i(m'_i·x_i)，
       连同边缘自算的 ‖m'_i‖² 发云；
       云算 ‖m_i‖² = ‖m'_i‖² + ‖x_i‖² + S_i/r_i
     泄露分析：云只知范数与盲化点积（与论文泄露 ‖m_i‖ 等价）；
     边缘只见随机向量 r_i∘x_i，反推不出 x_i 或 m'_i
  7. 云筛选（算法 4/5）：范数中位数 M，[M−θ, M+θ] 带 + 数量 l' 约束
     筛出可信边缘 A^(T)，算裁剪系数 x''_i = min(1, M/‖m_i‖)
  8. 盲化裁剪聚合（算法 5）：云取全局随机标量 R，发 t_i = R·x''_i
     （边缘看 t_i 是随机数）；边缘算 u_i = t_i·m'_i，叠加边缘级 SA
     掩码后发云（掩码在 Σ 上相消）；云得 R·Σx''_i m'_i，除回 R，
     再用第 6 步已得的 x_i 去噪：
       m_a = (Σ_{i∈A} x''_i m'_i + Σ_{i∈A} x''_i x_i) / l'
  9. 云更新 w_a ← w_a + ζ·m_a（对应论文 w ← w − ζ·m；论文 m 为梯度方向、
     本项目 m 为参数变化量方向，符号相反），回到第 0 步

SA 层（ppbr/sa.py）为 Bonawitz'17 真实实现：X25519+HKDF 成对种子、
自负载掩码、私钥与 b 的 Shamir (t,n−1) 份额、early/late 两阶段掉线恢复；
sa_key_exchange="sim" 退回哈希种子模拟（不抗合谋）。掉线仿真由
sa_simulate_dropout_rate 控制（仅实验用途）。
"""

import logging
from typing import Dict, List, Optional, Set, Tuple

import numpy as np
import torch

from ppbr.config import PPBRConfig
from ppbr.controller import PPBRController
from ppbr.crypto import (PPBRKeyContext, blocks_of, ct_mul_plain, ct_sum,
                         pair_seed, prg_vector)

logger = logging.getLogger(__name__)


class PPBRProtocol:
    """
    PPBR 协议编排器。持有云/边缘密钥上下文与 PPBRController
    （动量、CLDP、聚类、筛选的数学机制，与明文/密文无关，两轨复用）。

    属性访问委托给内部 controller：``momenta`` / ``last_stats`` /
    ``cfg`` 等直接可用（测试与 main.py 依赖此约定）。
    """

    def __init__(self, config: dict, num_edges: int, seed: int = 42):
        object.__setattr__(self, 'controller',
                           PPBRController(config, num_edges, seed))
        self.cfg: PPBRConfig = self.controller.cfg
        self.seed = seed
        self.rng = np.random.RandomState(seed)
        # 密钥上下文（懒初始化，第一轮确定向量维度后创建）。
        # 双轨解耦：PPBR 轨道使用自己的密码参数（默认高精度，见 crypto.py），
        # 仅当配置 ckks 段满足范数盲化协议的精度门槛时才复用。
        from ppbr.crypto import (DEFAULT_COEFF_MOD_BIT_SIZES,
                                 DEFAULT_GLOBAL_SCALE,
                                 DEFAULT_POLY_MODULUS_DEGREE,
                                 MIN_GLOBAL_SCALE)
        ckks = (config or {}).get('ckks', {}) if isinstance(config, dict) else {}
        coeff = ckks.get('coeff_mod_bit_sizes') or []
        scale = int(ckks.get('global_scale', 0))
        if len(coeff) < 3 or scale < MIN_GLOBAL_SCALE:
            logger.warning(
                "配置 ckks 参数不满足 PPBR 范数盲化协议精度门槛 "
                f"(scale={scale}, coeff={coeff})，PPBR 轨道回退到默认参数 "
                f"(poly={DEFAULT_POLY_MODULUS_DEGREE}, "
                f"coeff={DEFAULT_COEFF_MOD_BIT_SIZES}, "
                f"scale=2^{DEFAULT_GLOBAL_SCALE.bit_length()-1})")
            self._crypto_params = dict(
                poly_modulus_degree=DEFAULT_POLY_MODULUS_DEGREE,
                coeff_mod_bit_sizes=list(DEFAULT_COEFF_MOD_BIT_SIZES),
                global_scale=DEFAULT_GLOBAL_SCALE)
        else:
            self._crypto_params = dict(
                poly_modulus_degree=int(ckks.get('poly_modulus_degree', 8192)),
                coeff_mod_bit_sizes=list(coeff),
                global_scale=scale)
        self.cloud_ctx: Optional[PPBRKeyContext] = None
        self.edge_ctxs: Dict[int, PPBRKeyContext] = {}
        # 线上传输审计（块数）：client→edge / edge→cloud / cloud→edge
        self.last_wire: Dict[str, int] = {}

    def __getattr__(self, name):
        # 仅在常规查找失败时触发；__init__ 期间 controller 未就绪需防御
        try:
            controller = object.__getattribute__(self, 'controller')
        except AttributeError:
            raise AttributeError(name) from None
        return getattr(controller, name)

    # -----------------------------------------------------------------
    # 密钥初始化
    # -----------------------------------------------------------------
    def _ensure_contexts(self, num_edges: int) -> None:
        if self.cloud_ctx is None:
            self.cloud_ctx = PPBRKeyContext(**self._crypto_params)
        for eid in range(num_edges):
            if eid not in self.edge_ctxs:
                # 每个边缘生成自己的密钥对，私钥边缘自持（论文 §IV-B）；
                # 公钥字节注册到云（仿真直接共享对象）
                self.edge_ctxs[eid] = PPBRKeyContext(**self._crypto_params)

    # -----------------------------------------------------------------
    # 扁平向量工具
    # -----------------------------------------------------------------
    @staticmethod
    def _flatten(update: Dict[str, torch.Tensor]) -> np.ndarray:
        return np.concatenate([
            v.detach().cpu().double().numpy().ravel()
            for k, v in sorted(update.items())])

    @staticmethod
    def _unflatten(vec: np.ndarray,
                   template: Dict[str, torch.Tensor]) -> Dict[str, torch.Tensor]:
        out: Dict[str, torch.Tensor] = {}
        offset = 0
        for k in sorted(template):
            n = template[k].numel()
            out[k] = torch.from_numpy(
                np.asarray(vec[offset:offset + n], dtype=np.float32)
                .reshape(template[k].shape)).clone()
            offset += n
        return out

    # -----------------------------------------------------------------
    # 一轮完整协议
    # -----------------------------------------------------------------
    def run_round(self, round_num: int,
                  updates: Dict[int, Dict[str, torch.Tensor]],
                  groups: Dict[int, List[int]],
                  num_edges: int = None) -> Dict[str, torch.Tensor]:
        """
        执行一轮 PPBR 协议。

        Args:
            updates: {client_id: 本地更新 Δ}（攻击注入已完成）
            groups:  {edge_id: [client_id, ...]} 本轮边缘-客户端分组
        Returns:
            m_a: 全局更新（调用方做 w_a ← w_a + ζ·m_a；对应论文 w ← w − ζ·m，
            符号差异因本项目 Δ 为参数变化量、论文 m 为梯度方向）
        """
        num_edges = num_edges if num_edges is not None else \
            (max(groups) + 1 if groups else 1)
        self._ensure_contexts(num_edges)

        template = updates[next(iter(updates))]
        dim = sum(v.numel() for v in template.values())

        # ---- 算法 2 (CC)：符号统计 + CLDP + 边缘 trimmed k-means 过滤 ----
        coord_idx = self.controller.select_coordinates(dim, round_num)
        tuples = {cid: self.controller.perturb(
                      self.controller.client_sign_tuple(u, coord_idx))
                  for cid, u in updates.items()}
        trusted: Set[int] = self.controller.filter_clients(tuples, groups)
        # 过滤统计：良性/恶意各存活多少（攻击实验的关键诊断信息）
        mal = getattr(self, '_malicious_ids', None) or set()
        sub_mal = len([c for c in tuples if c in mal])
        tru_mal = len([c for c in trusted if c in mal])
        logger.info(
            f"[PPBR] round {round_num + 1}: 边缘过滤 提交={len(tuples)} "
            f"可信={len(trusted)} | 恶意: 提交={sub_mal} 存活={tru_mal}")
        updates = {cid: u for cid, u in updates.items() if cid in trusted}
        if not updates:
            tag = "PPBR 过滤后无可信客户端，本轮不更新"
            logger.warning(tag)
            return {k: torch.zeros_like(v) for k, v in template.items()}

        # ---- 算法 3 第 1 行：客户端动量 m ← φ·m + (1−φ)·Δ ----
        cids = sorted(updates)
        self.controller.update_momenta(cids, [updates[c] for c in cids])
        m = {c: self._flatten(self.controller.momentum_of(c)) for c in cids}

        # 客户端-边缘归属
        edge_of = {c: e for e, cs in groups.items() for c in cs}

        # ---- 客户端阶段：噪声掩码 + 云公钥加密 [x]_A + SA 掩码 z ----
        # x = noise_rel·‖m‖·u/‖u‖：与动量同尺度的相对噪声（见 config.py 注释），
        # 避免范数展开式的灾难性抵消
        use_dh = (self.cfg.sa_key_exchange == "dh")
        x: Dict[int, np.ndarray] = {}
        z: Dict[int, np.ndarray] = {}
        ct_x: Dict[int, list] = {}
        sa_members: Dict[int, 'SAMember'] = {}
        sa_buckets: Dict[int, 'SABucket'] = {}
        sa_early: Dict[int, Set[int]] = {}
        sa_late: Dict[int, Set[int]] = {}
        shares_out: Dict[int, Dict[int, Tuple[int, int]]] = {}
        sk_shares_out: Dict[int, Dict[int, Tuple[int, int]]] = {}

        if use_dh:
            # SA 阶段 1：真实 DH 密钥交换（公钥经边缘广播）
            from ppbr.sa import SABucket, SAMember, sa_threshold
            for eid, cs in groups.items():
                present = [c for c in cs if c in m]
                if len(present) < 2:
                    continue  # 单客户端组无 SA 对偶方，退化为无掩码直传
                tag = f"e{eid}"
                bucket = SABucket(tag, round_num, sa_threshold(len(present)))
                for c in present:
                    mem = SAMember(c, tag, round_num, bucket.threshold)
                    bucket.add_member(c, mem.public_key())
                    sa_members[c] = mem
                for c in present:
                    sa_members[c].register_peers(bucket.pubkeys())
                # Bonawitz 私钥备份：DH 私钥以 Shamir (t,n−1) 分片给同组其余方，
                # 掉线方的全部成对掩码可由 ≥t 份份额重建私钥后重算扣除
                for c in present:
                    sk_shares_out[c] = sa_members[c].share_private_key()
                sa_buckets[eid] = bucket
            # 掉线仿真：半数"提交前掉线"（不发 z）、半数"提交后掉线"
            # （发 z 但不揭示，走 Shamir 份额恢复）
            rate = float(self.cfg.sa_simulate_dropout_rate)
            if rate > 0.0:
                for eid, bucket in sa_buckets.items():
                    present = bucket.member_ids
                    if len(present) < 4:
                        continue  # 组太小不做掉线仿真
                    cand = [c for c in present if self.rng.rand() < rate]
                    max_drop = len(present) - max(2, bucket.threshold)
                    dropped = cand[:max(0, max_drop)]
                    n_late = len(dropped) // 2
                    sa_late[eid] = set(dropped[:n_late])
                    early = set(dropped[n_late:])
                    sa_early[eid] = early
                    for c in early:
                        bucket.mark_early_dropout(c)

        for c in cids:
            eid = edge_of[c]
            is_early = use_dh and c in sa_early.get(eid, set())
            if is_early:
                continue  # 提交前掉线：不上传 z 与 [x]_A
            u = self.rng.normal(0.0, 1.0, dim)
            x[c] = self.cfg.noise_rel * float(np.linalg.norm(m[c])) * u / np.linalg.norm(u)
            mprime = m[c] - x[c]
            if use_dh and c in sa_members:
                # SA 阶段 2：成对掩码 + 自负载掩码 + b 的 Shamir 分片
                z[c], shares_out[c] = sa_members[c].masked_input(mprime)
            else:
                masked = mprime.copy()
                for c2 in groups.get(eid, []):
                    if c2 == c or c2 not in m:
                        continue
                    r = prg_vector(pair_seed("dh", c, c2, round_num), dim)
                    masked += r if c < c2 else -r
                z[c] = masked
            ct_x[c] = self.cloud_ctx.encrypt_blocks(x[c])

        # ---- 边缘阶段（算法 3）：SA 聚合 + 同态求和 + [2m'_i]_{Ei} ----
        edge_data: Dict[int, dict] = {}
        wire = {'client_x_blocks': 0, 'edge_xsum_blocks': 0,
                'edge_2mp_blocks': 0, 'norm_products': 0,
                'sa_early_dropped': 0, 'sa_late_dropped': 0}
        for eid, cs in groups.items():
            present = [c for c in cs if c in z]
            if not present:
                continue
            if use_dh and eid in sa_buckets:
                # SA 聚合（含掉线修正）：总和 = Σ幸存输入（late 掉线方计入）
                bucket = sa_buckets[eid]
                late = sa_late.get(eid, set()) & set(present)
                for c in present:
                    if c in late:
                        bucket.mark_late_dropout(c, z[c])
                    else:
                        bucket.submit(c, z[c])
                survivors = [c for c in present if c not in late]
                reveals = {c: sa_members[c].reveal_self(dim) for c in survivors}
                # 掉线方（early∪late）的 DH 私钥由其分发、幸存方代持的 ≥t 份
                # 份额重建；late 掉线方的自负载种子 b 同理由 ≥t 份份额重建
                shares_sk = {d: [sk_shares_out[d][s] for s in survivors
                                 if s in sk_shares_out.get(d, {})]
                             for d in bucket.early_dropped | late}
                shares_b = {d: [shares_out[d][s] for s in survivors
                                if s in shares_out.get(d, {})] for d in late}
                total, n_i = bucket.aggregate(reveals, shares_sk, shares_b, dim)
                wire['sa_early_dropped'] += len(bucket.early_dropped)
                wire['sa_late_dropped'] += len(late)
                mprime_i = total / n_i
            else:
                n_i = len(present)
                mprime_i = np.sum([z[c] for c in present], axis=0) / n_i
            ct_x_i = ct_x[present[0]]
            wire['client_x_blocks'] += sum(len(v) for c in present
                                           for v in [ct_x[c]])
            for c in present[1:]:
                ct_x_i = ct_sum(ct_x_i, ct_x[c])
            ctx_e = self.edge_ctxs[eid]
            ct_2mp = ctx_e.encrypt_blocks(2.0 * mprime_i)
            wire['edge_xsum_blocks'] += len(ct_x_i)
            wire['edge_2mp_blocks'] += len(ct_2mp)
            edge_data[eid] = dict(
                mprime=mprime_i, ct_x=ct_x_i, ct_2mp=ct_2mp, n=n_i,
                norm2_mp=float(mprime_i @ mprime_i), xi=None)

        # ---- 云范数盲化（算法 4，两轮点积协议）----
        # x_i = (1/n'_i)Σ_j x_{i,j}：密文侧做同态 Σ，云解密后除以 n'_i
        # （除法公开，与 [x_i]_A = Σ[x_{i,j}]_A / n'_i 等价），
        # 与边缘侧均值 m'_i 尺度一致，denoise 才能精确抵消。
        norms: Dict[int, float] = {}
        for eid, d in edge_data.items():
            xi = self.cloud_ctx.decrypt_blocks(d['ct_x'], dim) / d['n']
            d['xi'] = xi
            r_i = float(self.rng.uniform(0.5, 1.5))
            prod_plain = [b * r_i for b in blocks_of(self.cloud_ctx, xi)]
            ct_prod = ct_mul_plain(d['ct_2mp'], prod_plain)
            wire['norm_products'] += len(ct_prod)
            v = self.edge_ctxs[eid].decrypt_blocks(ct_prod, dim)
            S_i = float(v.sum())
            arg = d['norm2_mp'] + float(xi @ xi) + S_i / r_i
            norms[eid] = float(np.sqrt(max(arg, 0.0)))

        # ---- 云筛选（算法 4/5）：中位数 + l' 约束 + 裁剪系数 ----
        trusted_edges, clip, M = self.controller.cloud_filter_norms(norms)
        if not trusted_edges:
            logger.warning("PPBR 云筛选空集，本轮不更新")
            self.last_wire = wire
            return {k: torch.zeros_like(v) for k, v in template.items()}
        eids_t = sorted(trusted_edges)

        # ---- 盲化裁剪聚合（算法 5）----
        R = float(self.rng.uniform(1.0, 2.0))
        if use_dh and len(eids_t) >= 2:
            # 边缘级同样走真实 SA（成员=可信边缘，聚合方=云）
            from ppbr.sa import SABucket as _CB, SAMember as _CM, sa_threshold
            cbucket = _CB("cloud", round_num, sa_threshold(len(eids_t)))
            cmember = {}
            for eid in eids_t:
                mem = _CM(eid, "cloud", round_num, cbucket.threshold)
                cbucket.add_member(eid, mem.public_key())
                cmember[eid] = mem
            for eid in eids_t:
                cmember[eid].register_peers(cbucket.pubkeys())
            for eid in eids_t:
                u, _sh = cmember[eid].masked_input(
                    (R * clip[eid]) * edge_data[eid]['mprime'])
                cbucket.submit(eid, u)
            # 边缘级无掉线：全员揭示自负载掩码，无份额恢复
            total_e, _n_e = cbucket.aggregate(
                {e: cmember[e].reveal_self(dim) for e in eids_t}, {}, {}, dim)
            sum_mp = total_e / R            # Σ x''_i m'_i（SA 掩码相消）
        else:
            agg = np.zeros(dim)
            for eid in eids_t:
                u = (R * clip[eid]) * edge_data[eid]['mprime']
                for e2 in eids_t:
                    if e2 == eid:
                        continue
                    p = prg_vector(pair_seed("edge-dh", eid, e2, round_num), dim)
                    u = u + (p if eid < e2 else -p)
                agg += u
            sum_mp = agg / R                # Σ x''_i m'_i（掩码相消）
        sum_x = np.sum([clip[e] * edge_data[e]['xi'] for e in eids_t], axis=0)
        l_prime = len(eids_t)
        m_a = (sum_mp + sum_x) / l_prime

        self.last_wire = wire
        logger.info(
            f"PPBR wire: x_blocks={wire['client_x_blocks']} "
            f"2mp_blocks={wire['edge_2mp_blocks']} products={wire['norm_products']}")
        return self._unflatten(m_a, template)
