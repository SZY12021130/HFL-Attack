"""
RFLProtocol —— 完整协议编排（论文 §V 算法 2）
================================================

开启 ``rfl.enabled`` 后，整条训练路线切换为 RFL 协议（Zhou et al., IEEE
TDSC 2026），不走全局掩码投票 / TopK 稀疏化 / 单密钥 CKKS 聚合。与 CKKS
轨道共享：数据加载划分、模型创建、边缘-客户端分组（HierarchyController）、
指标采集。信任模型与 CKKS/PPBR 轨道不同：云 CS 可半诚实或恶意（会伪造
客户端身份、篡改聚合结果），ES 与 EC 诚实（论文 §IV-B）。

一轮的完整时序（对应论文算法 2）：

  0. CS 初始化/切分全局模型：浅层 p̲ 广播给全部 EC，深层 p̄ 交给各 ES；
     HH 的公开参数 pp 分发给各 ES（仿真：协议内共享 HomomorphicHash 实例）
  1. 新客户端集成：EC u 本地生成密钥对 (sk_u, pk_u)，**直接**向所属 ES v
     注册 pk_u（CS 无法代注册——这是 IFGIA 伪造客户端被识别的根基）；
     各 ES v 交换 pk_v
  2. 每个 EC u 用浅层 SL 本地前向（式 6）：p_u = SL(p̲, D_u)
  3. u 对中间表示加高斯噪声（式 7）：p̂_u = p_u + δ_u（轻量化 DP）
  4. u 签名（式 8）：z_u = DS.Sign(sk_u, p̂_u)，发送 (p̂_u, z_u) 至最近 ES v
  5. v 验签（式 9）：DS.Verify(pk_u, p̂_u, z_u)，失败即拒绝（伪造身份被拒）
  6. v 深层前向（式 10）：p_v = DL(p̂_u, p̄)，回传 p_v 给 u
  7. u 计算输出梯度（式 11）：g_u = ∇Loss(p_v, D_u)，回传 g_u 给 v
  8. v 深层反向（式 12）：g_v = Update(g_u)，并把 ∂Loss/∂p̂_u 回传 u 更新浅层；
     v 聚合组内客户端的浅层更新与 g_v 为本边缘更新 u_v
  9. v 计算同态哈希（式 13）：h_v = HH.Hash(u_v)，签名（式 14）：
     z_v = DS.Sign(sk_v, h_v)，发送 u_v 至 CS，广播 (h_v, z_v) 给其他 ES
 10. CS 聚合（式 15）：g_p = p + (1/M)Σ_v u_v，广播 g_p 与全部 (h_v, z_v)
 11. 各 ES 互验签名（式 16）并校验（式 17）：
     Π_v h_v ≡ HH.Hash(M·(g_p − p))；不成立则全体 ES 退出系统

实现说明：
- 仿真为单进程实体对象，"线上传输"以字节计数审计（self.last_wire）；
- 式(17) 在定点整数域校验：各 ES 上传量化整数向量 q_v = ⌊u_v·s⌉，
  CS 对整数求和 S = Σ q_v，校验 Π h_v = H(S)（无浮点误差，严格成立），
  全局更新 g = dequantize(S)/M（论文式 15 只写深层梯度，实现将 ES 更新
  取 浅层∥深层 拼接向量统一聚合与校验，使全模型一致更新，见 doc/rfl.md）；
- 更新语义为梯度：调用方做 w ← w − ζ·g。
"""

import copy
import logging
from typing import Dict, List, Optional, Tuple

import numpy as np
import torch
import torch.nn as nn

from rfl.config import RFLConfig
from rfl.crypto import DigitalSignature, HomomorphicHash
from rfl.splitnn import build_split, dp_sigma, flatten_module

logger = logging.getLogger(__name__)


class RFLProtocol:
    """
    RFL 协议编排器。持有全部 EC/ES 的签名密钥上下文与 ES 身份注册表，
    单入口 ``run_round`` 执行一轮完整协议（论文算法 2）。
    """

    PK_SIZE = 32  # Ed25519 公钥字节数

    def __init__(self, config: dict, seed: int = 42):
        self.cfg = RFLConfig.from_dict(
            (config or {}).get('rfl') if isinstance(config, dict) else config)
        self.seed = seed
        self.rng = np.random.RandomState(seed)
        self.hh = HomomorphicHash(bits=self.cfg.hash_bits,
                                  scale=self.cfg.hash_scale)
        # 密钥上下文（持久，跨轮复用——身份一旦注册长期有效）
        self._client_keys: Dict[int, DigitalSignature] = {}
        self._edge_keys: Dict[int, DigitalSignature] = {}
        # ES 身份注册表：{edge_id: {pk_bytes: client_id}}。
        # 注册通道是 EC → ES 直连（论文算法 2 第 1 行），CS 不在通道上，
        # 因此 CS 伪造的客户端没有合法注册身份，式(9) 验签必然失败。
        self.registry: Dict[int, Dict[bytes, int]] = {}
        # 线上传输审计（字节）
        self.last_wire: Dict[str, int] = {}
        # 本轮统计
        self.last_stats: Dict = {}

    # -----------------------------------------------------------------
    # 密钥与注册
    # -----------------------------------------------------------------
    def _client_key(self, cid: int) -> DigitalSignature:
        if cid not in self._client_keys:
            self._client_keys[cid] = DigitalSignature(
                seed=f"rfl-client-{self.seed}-{cid}".encode())
        return self._client_keys[cid]

    def _edge_key(self, eid: int) -> DigitalSignature:
        if eid not in self._edge_keys:
            self._edge_keys[eid] = DigitalSignature(
                seed=f"rfl-edge-{self.seed}-{eid}".encode())
            self.registry.setdefault(eid, {})
        return self._edge_keys[eid]

    def registered_clients(self, eid: int) -> List[int]:
        return list(self.registry.get(eid, {}).values())

    # -----------------------------------------------------------------
    # 一轮完整协议（论文算法 2）
    # -----------------------------------------------------------------
    def run_round(self, round_num: int,
                  loaders: Dict[int, torch.utils.data.DataLoader],
                  groups: Dict[int, List[int]],
                  model: nn.Module
                  ) -> Tuple[Dict[str, torch.Tensor], bool]:
        """
        执行一轮 RFL 协议。

        Args:
            loaders: {client_id: 本地训练加载器}
            groups:  {edge_id: [client_id, ...]} 本轮边缘-客户端分组
            model:   全局模型（本轮起点，浅/深层由其切出）

        Returns:
            (grad_update, aborted): grad_update 为命名参数梯度字典
            （aborted=True 时为零向量），调用方做 w ← w − ζ·g。
        """
        device = next(model.parameters()).device
        cfg = self.cfg

        # 切分模板（参数名/形状），向量长度 = 浅层∥深层
        _, _, shallow_names, deep_names = build_split(model,
                                                      cfg.num_shallow_layers)
        template = {k: v.detach().cpu() for k, v in model.named_parameters()}
        names = shallow_names + deep_names
        d_total = sum(template[k].numel() for k in names)
        d_shallow = sum(template[k].numel() for k in shallow_names)

        wire = {k: 0 for k in (
            'reg_pk',            # EC→ES 身份注册（新客户端集成）
            'client_smashed',    # EC→ES 加噪中间表示 p̂_u（式 7 上传）
            'client_sig',        # EC→ES 签名 z_u（式 8）
            'es_activation',     # ES→EC 深层输出 p_v（式 10 回传）
            'client_outgrad',    # EC→ES 输出梯度 g_u（式 11）
            'client_gradback',   # ES→EC ∂Loss/∂p̂_u（浅层反向）
            'client_shallow',    # EC→ES 浅层梯度更新
            'es_to_cloud',       # ES→CS 更新 + 哈希 + 签名（式 13/14）
            'cloud_to_es',       # CS→ES 聚合结果 + 校验广播（式 15/16/17）
        )}

        # ---- 步骤 1：新客户端集成（EC 直连 ES 注册 pk，CS 不在通道上）----
        for eid, cids in groups.items():
            reg = self.registry.setdefault(eid, {})
            for cid in cids:
                key = self._client_key(cid)
                if key.public_key_bytes not in reg:
                    reg[key.public_key_bytes] = cid
                    wire['reg_pk'] += self.PK_SIZE

        # ---- IFGIA 仿真（论文 §III）：CS 伪造虚拟客户端注入分组 ----
        # 伪造客户端由 CS 生成密钥，其 pk 从未经 EC→ES 直连通道注册，
        # 式(9) 验签时因"未知身份"被拒——这是 RFL 抵御 IFGIA 的机制演示。
        fabricated: Dict[int, List[int]] = {}
        if cfg.simulate_ifgia:
            for eid, cids in groups.items():
                if not cids:
                    continue
                fabricated[eid] = []
                for _ in range(cfg.ifgia_per_edge):
                    fid = -(10_000 * (round_num + 1) + 100 * eid
                            + len(fabricated[eid]))
                    self._client_keys[fid] = DigitalSignature()  # CS 自签身份
                    fabricated[eid].append(fid)

        # ---- 步骤 2-8：分组内 split learning（式 6-12）----
        edge_contrib: Dict[int, dict] = {}   # 提交到 CS 的边缘贡献
        n_rejected = 0
        loss_sum, loss_count = 0.0, 0
        criterion = nn.CrossEntropyLoss()

        for eid, cids in groups.items():
            if not cids:
                continue
            shallow_t, deep_t, _, _ = build_split(model,
                                                  cfg.num_shallow_layers)
            deep = deep_t.to(device)
            deep.train()
            edge_key = self._edge_key(eid)
            reg = self.registry[eid]

            es_shallow_grads: List[np.ndarray] = []
            n_batches_total = 0

            for cid in cids + fabricated.get(eid, []):
                loader = loaders.get(cid if cid >= 0 else cids[0])
                if loader is None:
                    continue
                key = self._client_key(cid)
                # 每个 EC 独立持有自己的浅层副本（物理设备分离，梯度不共享）
                shallow = copy.deepcopy(shallow_t).to(device)
                shallow.train()
                n_client_batches = 0

                for _ in range(max(1, cfg.local_epochs)):
                    for x, y in loader:
                        x = x.to(device)
                        y = y.to(device)
                        # 式(6)：EC 浅层前向
                        h = shallow(x)
                        # 式(7)：高斯 DP 扰动
                        if cfg.client_dp_noise:
                            sigma = dp_sigma(h.numel() // h.size(0),
                                             len(loader.dataset),
                                             cfg.epsilon, cfg.delta_dp,
                                             cfg.clip_threshold)
                            h = h + torch.randn_like(h) * sigma
                        h_hat = h.detach().requires_grad_(True)

                        # 式(8)：签名并上传 (p̂_u, z_u)
                        meta = f"r{round_num}|e{eid}|c{cid}".encode()
                        vec = h_hat.detach().cpu().numpy().ravel()
                        sig = key.sign_vector(vec, meta)
                        wire['client_smashed'] += vec.nbytes
                        wire['client_sig'] += DigitalSignature.SIG_SIZE

                        # 式(9)：ES 验签（注册身份 + 签名，双重校验）
                        registered = reg.get(key.public_key_bytes)
                        if registered != cid or not DigitalSignature.verify_vector(
                                key.public_key_bytes, vec, sig, meta):
                            n_rejected += 1
                            tag = "未知身份（IFGIA 伪造客户端）" if registered is None \
                                else "签名无效"
                            logger.warning(
                                f"[RFL] round {round_num + 1}: ES-{eid} 拒绝 "
                                f"客户端 {cid}（{tag}）——式(9) 验签失败")
                            break  # 该伪造客户端被剔除

                        # 式(10)：ES 深层前向并回传
                        logits = deep(h_hat)
                        wire['es_activation'] += logits.numel() * 4

                        # 式(11)：EC 计算输出梯度并回传
                        loss = criterion(logits, y)
                        loss_sum += float(loss.item())
                        loss_count += 1
                        g_u = torch.autograd.grad(loss, logits,
                                                  retain_graph=True)[0]
                        wire['client_outgrad'] += g_u.numel() * 4

                        # 式(12)：ES 深层反向（g_v 累积 + 回传浅层梯度）
                        torch.autograd.backward(logits, grad_tensors=g_u)
                        grad_back = h_hat.grad.detach()
                        wire['client_gradback'] += grad_back.numel() * 4
                        h.backward(gradient=grad_back)  # EC 浅层反向

                        n_client_batches += 1
                        n_batches_total += 1
                    else:
                        continue
                    break  # 伪造客户端被拒后跳过 local_epochs 剩余轮

                if n_client_batches == 0:
                    continue
                # EC 浅层更新上传（签名保护完整性）
                g_shallow = flatten_module(shallow, grad=True) / n_client_batches
                sig_g = key.sign_vector(g_shallow, meta + b"|grad")
                wire['client_shallow'] += g_shallow.nbytes
                wire['client_sig'] += DigitalSignature.SIG_SIZE
                if not DigitalSignature.verify_vector(
                        key.public_key_bytes, g_shallow, sig_g, meta + b"|grad"):
                    n_rejected += 1
                    continue
                es_shallow_grads.append(g_shallow)

            if not es_shallow_grads:
                logger.warning(
                    f"[RFL] round {round_num + 1}: ES-{eid} 无有效客户端贡献，跳过")
                continue

            # 边缘更新 u_v = 浅层均值 ∥ 深层均值（式 12 聚合）
            g_deep = flatten_module(deep, grad=True) / max(1, n_batches_total)
            u_v = np.concatenate([np.mean(es_shallow_grads, axis=0), g_deep])
            assert u_v.shape[0] == d_total

            # 式(13)(14)：同态哈希 + 签名，上传 CS 并广播给其他 ES
            q_v = self.hh.quantize(u_v)
            h_v = self.hh.hash_ints(q_v)
            z_v = edge_key.sign_int(h_v, f"r{round_num}|e{eid}".encode())
            wire['es_to_cloud'] += (q_v.nbytes + self.hh.hash_bytes
                                    + DigitalSignature.SIG_SIZE)
            edge_contrib[eid] = dict(q_v=q_v, h_v=h_v, z_v=z_v, n=len(es_shallow_grads))

        zero = {k: torch.zeros_like(v) for k, v in template.items()}
        if not edge_contrib:
            logger.warning(f"[RFL] round {round_num + 1}: 无任何边缘贡献，本轮不更新")
            self.last_wire = wire
            self.last_stats = dict(
                aborted=False, num_edges=0, rejected_fabricated=n_rejected,
                verified=False, mean_loss=(loss_sum / loss_count) if loss_count else 0.0)
            return zero, False

        # ---- 步骤 10：CS 聚合（式 15），广播 g_p 与 (h_v, z_v) ----
        M = len(edge_contrib)
        S = np.sum([d['q_v'] for d in edge_contrib.values()], axis=0)
        tampered = None
        if cfg.cs_tamper != "none":
            victim = sorted(edge_contrib)[0]
            if cfg.cs_tamper == "drop_edge":
                # 云丢弃某边缘更新：聚合和缺少该边缘，式(17) 校验失败
                S = S - edge_contrib[victim]['q_v']
                tampered = f"丢弃 ES-{victim} 的更新"
            elif cfg.cs_tamper == "scale_edge":
                # 云缩放某边缘更新：整数和与被篡改边缘的哈希不一致
                scaled = np.rint(edge_contrib[victim]['q_v']
                                 * cfg.cs_tamper_scale).astype(np.int64)
                S = S - edge_contrib[victim]['q_v'] + scaled
                tampered = (f"将 ES-{victim} 的更新缩放 ×{cfg.cs_tamper_scale}")
            else:
                raise ValueError(f"未知 cs_tamper 模式: {cfg.cs_tamper}")
            logger.warning(
                f"[RFL] round {round_num + 1}: 恶意 CS 篡改聚合（{tampered}）")

        wire['cloud_to_es'] += (S.nbytes + M * (self.hh.hash_bytes
                                                + DigitalSignature.SIG_SIZE))
        # ES 公钥交换（步骤 1，一次性开销，均摊进本轮广播）
        wire['cloud_to_es'] += M * self.PK_SIZE

        # ---- 步骤 11：各 ES 验证（式 16 签名 + 式 17 同态哈希）----
        verified = True
        for eid in edge_contrib:
            key_i = self._edge_key(eid)
            for e2, d2 in edge_contrib.items():
                if e2 == eid:
                    continue
                if not DigitalSignature.verify_int(
                        self._edge_key(e2).public_key_bytes, d2['h_v'], d2['z_v'],
                        f"r{round_num}|e{e2}".encode()):
                    verified = False
                    logger.warning(
                        f"[RFL] round {round_num + 1}: ES-{eid} 校验 ES-{e2} "
                        f"哈希签名失败（式 16）")
            # 式(17)：Π_v h_v ≡ H(S)（定点整数域，严格成立）
            if not self.hh.verify_aggregate([d['h_v'] for d in edge_contrib.values()], S):
                verified = False
                logger.warning(
                    f"[RFL] round {round_num + 1}: ES-{eid} 聚合校验失败"
                    f"（式 17：Π h_v ≢ H(Σq_v)）——退出系统")

        aborted = not verified
        if aborted:
            logger.warning(
                f"[RFL] round {round_num + 1}: 验证失败，全体 ES 中止本轮（不更新）")
            update_vec = np.zeros(d_total)
        else:
            update_vec = self.hh.dequantize(S) / M

        self.last_wire = wire
        self.last_stats = dict(
            aborted=aborted, num_edges=M, rejected_fabricated=n_rejected,
            verified=verified, tampered=tampered,
            mean_loss=(loss_sum / loss_count) if loss_count else 0.0,
            dims=(d_shallow, d_total - d_shallow))
        logger.info(
            f"[RFL] round {round_num + 1}: edges={M} 校验={'通过' if verified else '失败'}"
            f" 拒绝伪造={n_rejected} wire={wire}")
        return self._to_named(update_vec, names, template), aborted

    # -----------------------------------------------------------------
    # 向量 -> 命名参数（按浅层∥深层 names 顺序切片）
    # -----------------------------------------------------------------
    @staticmethod
    def _to_named(vec: np.ndarray, names: List[str],
                  template: Dict[str, torch.Tensor]
                  ) -> Dict[str, torch.Tensor]:
        out: Dict[str, torch.Tensor] = {}
        offset = 0
        for k in names:
            n = template[k].numel()
            out[k] = torch.from_numpy(
                np.asarray(vec[offset:offset + n], dtype=np.float32)
                .reshape(template[k].shape)).clone()
            offset += n
        return out
