"""
层次联邦学习（云-边-端）模块
==============================

在标准"单服务器-多客户端"联邦学习之上增加边缘节点层：

    客户端密文 ──→ 边缘节点密文聚合 ──→ 边缘密文 ──→ 云服务器密文聚合
                                                          │
                              密文全局模型 云 ──→ 边 ──→ 端 逐级下发

设计要点（参考 paperlist/ 下单密钥 HE 云边端论文与 HierFL 两级聚合调度）：
1. **边缘节点只持有公钥上下文**：只做同态加法与密文×标量（加权平均），
   与云服务器一样不能解密任何密文；
2. **两级权重衔接**：边缘内部先归一化（加权平均），云聚合边缘密文时
   边缘权重 = 该边缘下辖客户端权重之和，保证结果与扁平 FedAvg 严格一致；
3. **层次化掩码投票**：客户端本地 TopK mask → 边缘汇总部分计数 →
   云合并计数生成全局 mask → 沿云→边→端下发。数学上与扁平投票等价；
4. **可插拔客户端-边缘关联**：
   - `static`：固定分片（边缘 e 负责连续一段客户端）；
   - `uav`：UAV 随机路点移动，客户端每轮关联到最近的 UAV
     （模拟 UAV-assisted FL 中客户端关联随轮次变化）。

配置（configs/config.yaml 的 hierarchy 段）：
    hierarchy:
      enabled: false
      num_edges: 3
      assignment: "static"        # static | uav
      edge_selection_rate: 1.0    # 每轮参与训练的边缘节点比例
      uav:
        area_size: 1000.0
        speed: 30.0
        coverage_radius: 400.0
        strict_coverage: false
        seed: 42
"""

import logging
import math
from typing import Dict, List, Optional, Tuple

import numpy as np

from base.grouping import (
    BaseAssignment, StaticAssignment, UAVAssignment, create_assignment,
)
from ckks.crypto import CKKSContext, CKKSManager
from ckks.global_mask_vote import GlobalMaskVote

logger = logging.getLogger(__name__)


# =====================================================================
# 边缘节点
# =====================================================================

class EdgeNode:
    """
    边缘节点（UAV / 边缘服务器）：负责一部分客户端的密文聚合。

    安全边界：仅持有公钥上下文（从服务器公钥字节反序列化），
    可执行密文加法 / 密文×标量，不能解密。
    """

    def __init__(self, edge_id: int, client_ids: List[int],
                 public_context_bytes: bytes, ckks_config: dict,
                 poly_modulus_degree: int):
        self.edge_id = edge_id
        self.client_ids: List[int] = list(client_ids)

        public_context = CKKSContext.from_full_bytes(
            public_context_bytes, poly_modulus_degree=poly_modulus_degree)
        self.ckks_manager = CKKSManager(ckks_config.copy(), context=public_context)
        self.served_rounds = 0

    @property
    def num_clients(self) -> int:
        return len(self.client_ids)

    def has_secret_key(self) -> bool:
        """自检：边缘节点上下文不得包含私钥"""
        try:
            self.ckks_manager.context.context.secret_key()
            return True
        except Exception:
            return False

    # ---- 阶段一：掩码部分聚合（边缘把下辖客户端的 mask 计数合并）----
    def aggregate_mask_counts(self, client_masks: List[np.ndarray]) -> Optional[np.ndarray]:
        """
        边缘侧掩码聚合：把下辖客户端的 0/1 mask 求和为部分计数向量。

        Args:
            client_masks: 下辖客户端的 mask 列表，每个 shape=(num_batches,)

        Returns:
            np.ndarray 部分计数向量；无输入时返回 None
        """
        valid = [m for m in client_masks if m is not None]
        if not valid:
            return None
        return np.sum(np.stack(valid, axis=0), axis=0)

    # ---- 阶段二：密文部分聚合 ----
    def aggregate_updates(self, encrypted_updates: List[dict],
                          client_weights: List[float],
                          normalize: bool = True) -> dict:
        """
        边缘侧密文聚合。

        Args:
            encrypted_updates: 下辖客户端的稀疏加密更新
            client_weights: 客户端权重。
                normalize=True  : 边缘内归一化（独立边缘聚合语义）；
                normalize=False : 按传入权重做加权和不归一化（层次聚合：
                    权重已由编排器按全局总权重预缩放 α_i = w_i / Σw，
                    服务器端将纯加法合并，避免二次密文乘法耗尽乘法深度）。
        """
        if not encrypted_updates:
            raise ValueError(f"Edge #{self.edge_id} 没有可聚合的客户端更新")
        self.served_rounds += 1
        return self.ckks_manager.aggregate_encrypted_updates(
            encrypted_updates, client_weights, normalize=normalize)


# =====================================================================
# 层次编排器
# =====================================================================

class HierarchyController:
    """
    层次联邦学习编排器：管理边缘节点、关联策略与两级聚合流程。

    一轮的调用顺序（在 run_round_encrypted 内）：
      1. select_edges()             选择本轮参与的边缘节点
      2. group_clients(...)         把选中客户端按归属边缘分组
      3. vote_global_mask(...)      层次化掩码投票（边缘部分计数→云合并）
      4. aggregate_edges(...)       边缘密文聚合→返回边缘密文/权重/大小
    """

    def __init__(self, config: dict, server_ckks_manager: CKKSManager,
                 num_clients: int):
        hcfg = config.get('hierarchy', {})
        self.enabled = hcfg.get('enabled', False)
        self.num_edges = int(hcfg.get('num_edges', 3))
        self.edge_selection_rate = float(hcfg.get('edge_selection_rate', 1.0))
        self.config = config

        if self.num_edges < 1:
            raise ValueError("hierarchy.num_edges 必须 >= 1")
        if self.num_edges > num_clients:
            raise ValueError(
                f"hierarchy.num_edges({self.num_edges}) 不能超过客户端数({num_clients})")

        # 关联策略（实现位于 base/grouping.py，各轨道共享）
        assignment_name = hcfg.get('assignment', 'static')
        self.assigner = create_assignment(config, num_clients, self.num_edges)

        # 边缘节点：只发公钥上下文。
        # server_ckks_manager=None 的轨道（如 RFL：split learning 实体在协议内，
        # 不经 CKKS 密文聚合）仅使用客户端-边缘关联分组，不创建密文边缘节点。
        poly_degree = config['ckks'].get('poly_modulus_degree', 8192)
        self.edges: Dict[int, EdgeNode] = {}
        mapping = self.assigner.edge_client_map(0, self.num_edges)
        if server_ckks_manager is not None:
            public_bytes = server_ckks_manager.context.get_context_bytes()
            for eid, cids in mapping.items():
                self.edges[eid] = EdgeNode(
                    eid, cids, public_bytes, config['ckks'], poly_degree)
        else:
            logger.info(
                "HierarchyController: 无 CKKS 上下文（RFL/明文轨道），"
                "仅提供客户端-边缘关联分组，不创建密文边缘节点")

        # 服务器侧全局掩码投票器（复用配置）
        gmv_cfg = config.get('global_mask_vote', {})
        self.server_vote = GlobalMaskVote(
            mode=gmv_cfg.get('mode', 'none'),
            threshold_pct=gmv_cfg.get('threshold_pct', 0.2),
            topk_k=gmv_cfg.get('topk_count_k', 5),
            topk_pct=gmv_cfg.get('topk_pct', None),
        )

        self.rng = np.random.RandomState(config['system'].get('random_seed', 42))
        logger.info(
            f"HierarchyController: edges={self.num_edges} | "
            f"assignment={assignment_name} | edge_selection_rate={self.edge_selection_rate} | "
            f"clients/edge={[len(mapping[e]) for e in sorted(mapping)]}")

    # -----------------------------------------------------------------
    # 阶段 0/1：边缘选择与客户端分组
    # -----------------------------------------------------------------
    def select_edges(self, round_num: int) -> List[int]:
        """按 edge_selection_rate 选择本轮参与的边缘节点"""
        all_edges = sorted(self.edges.keys())
        k = max(1, int(round(self.num_edges * self.edge_selection_rate)))
        if k >= self.num_edges:
            return all_edges
        idx = self.rng.choice(len(all_edges), size=k, replace=False)
        return sorted(all_edges[i] for i in idx)

    def group_clients(self, selected_client_ids: List[int],
                      round_num: int) -> Dict[int, List[int]]:
        """
        把被选中的客户端按本轮边缘归属分组。

        客户端-边缘关联按轮次计算（UAV 模式下会随时间变化）。
        返回 {edge_id: [client_id, ...]}，只包含被选中的客户端。
        """
        mapping = self.assigner.assign(round_num)
        groups: Dict[int, List[int]] = {e: [] for e in range(self.num_edges)}
        for cid in selected_client_ids:
            eid = mapping.get(cid)
            if eid is not None and eid in groups:
                groups[eid].append(cid)
        return {e: cids for e, cids in groups.items() if cids}

    # -----------------------------------------------------------------
    # 阶段 2：层次化全局掩码投票
    # -----------------------------------------------------------------
    def vote_global_mask(
        self,
        client_masks: Dict[int, Optional[np.ndarray]],
        num_batches: int,
        round_num: int,
    ) -> Optional[np.ndarray]:
        """
        层次化全局掩码投票。

        流程：客户端 mask（本地 TopK）→ 各边缘合并部分计数 →
        服务器把各边缘计数相加，用与扁平投票相同的规则生成全局 mask。
        数学上与扁平投票严格等价（计数可加法分解）。
        """
        if self.server_vote.mode == 'none' or not client_masks:
            return None

        # 客户端 mask 先到边缘：按客户端当前归属分组
        mapping = self.assigner.assign(round_num)
        per_edge_masks: Dict[int, List[np.ndarray]] = {e: [] for e in self.edges}
        for cid, mask in client_masks.items():
            if mask is None:
                continue
            eid = mapping.get(cid)
            if eid is not None:
                per_edge_masks[eid].append(mask)

        total_counts = np.zeros(num_batches, dtype=np.float64)
        total_voters = 0
        for eid, masks in per_edge_masks.items():
            partial = self.edges[eid].aggregate_mask_counts(masks)
            if partial is not None:
                total_counts += partial
                total_voters += len(masks)

        if total_voters == 0:
            return None

        global_mask = self.server_vote.mask_from_counts(
            total_counts.astype(np.int64), num_batches, num_voters=total_voters)
        logger.info(
            f"HierarchicalMaskVote[{self.server_vote.mode}] | edges={len(self.edges)} | "
            f"voters={total_voters} | global_batches={int(global_mask.sum())}/{num_batches}")
        return global_mask

    # -----------------------------------------------------------------
    # 阶段 3：边缘密文聚合
    # -----------------------------------------------------------------
    def aggregate_edges(
        self,
        encrypted_updates: Dict[int, dict],
        client_weights: Dict[int, float],
        round_num: int,
    ) -> Tuple[List[dict], List[float], List[int]]:
        """
        边缘密文聚合：把每个边缘下辖客户端的密文加权平均，
        返回可供服务器继续聚合的边缘密文。

        Returns:
            edge_updates:   List[dict] 边缘级 __sparse__ 密文
            edge_weights:   List[float] 边缘权重 = 下辖客户端权重之和
            edge_sizes:     List[int]   边缘密文字节数（云侧通信统计用）
        """
        mapping = self.assigner.assign(round_num)
        groups: Dict[int, List[int]] = {e: [] for e in self.edges}
        for cid in encrypted_updates:
            eid = mapping.get(cid)
            if eid is not None:
                groups[eid].append(cid)

        # 全局总权重：客户端更新在加密前按 α_i = w_i / Σw 预缩放
        # （由边缘侧加权和不归一化完成一次密文×标量，云侧纯加法合并），
        # 数学上严格等于扁平 FedAvg，且只消耗一层乘法深度。
        total_weight = float(sum(client_weights.values()))

        edge_updates, edge_weights, edge_sizes = [], [], []
        for eid, cids in groups.items():
            if not cids:
                continue
            encs = [encrypted_updates[c] for c in cids]
            scaled_ws = [client_weights[c] / total_weight for c in cids]
            edge_result = self.edges[eid].aggregate_updates(
                encs, scaled_ws, normalize=False)
            edge_updates.append(edge_result)
            edge_weights.append(1.0)  # 云侧等权纯加法合并
            edge_sizes.append(
                self.edges[eid].ckks_manager.compute_encrypted_update_size(edge_result))
        return edge_updates, edge_weights, edge_sizes

    def info(self) -> dict:
        return {
            'num_edges': self.num_edges,
            'assignment': type(self.assigner).__name__,
            'edge_clients': {e: (self.edges[e].num_clients if e in self.edges
                                 else None)
                             for e in range(self.num_edges)},
        }
