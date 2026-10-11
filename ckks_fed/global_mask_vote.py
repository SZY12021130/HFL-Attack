"""
GlobalMaskVote: 可插拔的全局掩码投票模块
============================================

支持四种模式：
  - none:      关闭投票，使用原始独立稀疏化
  - union:     并集投票（所有选中位置的并集）
  - threshold: 阈值投票（超过 θ% 客户端选中才纳入）
  - topk:      TopK 计数投票（按选中次数取前 K 个）

流程：
  1. 采样客户端本地 TopK，生成 batch 掩码（0/1 向量）
  2. 上传掩码（明文位置，~10 bytes）给服务器
  3. 服务器聚合得到全局掩码
  4. 广播全局掩码给所有客户端
  5. 所有客户端按全局掩码加密上传（选中 batch 传真实值，未选中传 0）
"""

import numpy as np
from typing import List, Dict, Optional, Any
import logging

logger = logging.getLogger(__name__)


class GlobalMaskVote:
    """
    全局掩码投票器。

    Args:
        mode: 投票模式，"none" | "union" | "threshold" | "topk"
        threshold_pct: threshold 模式阈值（0.0 ~ 1.0）
        topk_k: topk 模式取前 K 个
    """

    MODES = ["none", "union", "threshold", "topk"]

    def __init__(self, mode: str = "none", threshold_pct: float = 0.2, topk_k: int = 5, topk_pct: float = None):
        if mode not in self.MODES:
            raise ValueError(f"mode must be one of {self.MODES}, got {mode}")
        self.mode = mode
        self.threshold_pct = threshold_pct
        self.topk_k = topk_k
        self.topk_pct = topk_pct

        if mode != "none":
            logger.info(f"GlobalMaskVote enabled | mode={mode} | threshold={threshold_pct} | topk_k={topk_k} | topk_pct={topk_pct}")
        else:
            logger.info("GlobalMaskVote disabled (mode=none)")

    # ==================================================================
    # 客户端侧：从 TopK 结果生成掩码
    # ==================================================================
    def client_compute_mask(
        self,
        selected_indices: List[int],
        total_batches: int,
    ) -> Optional[np.ndarray]:
        """
        客户端：将选中的 batch 索引编码为 bit mask。

        Returns:
            np.ndarray(int8) shape=(total_batches,) 或 None（mode=none）
        """
        if self.mode == "none":
            return None

        mask = np.zeros(total_batches, dtype=np.int8)
        for idx in selected_indices:
            if 0 <= idx < total_batches:
                mask[idx] = 1
        return mask

    @staticmethod
    def compute_mask_from_update(
        model_update: Dict[str, Any],
        batch_size: int,
        sparsity_rate: float = 0.1,
        scoring_method: str = "avg_abs",
        round_num: int = 0,
        total_batches: int = 0,
    ) -> Optional[np.ndarray]:
        """
        从模型更新字典计算 TopK 掩码（客户端本地执行，不上传明文梯度）。

        流程：
          1. 将模型更新扁平化为长数组
          2. 按 batch_size 切分为 batches（batch_size 必须和 packing 策略对齐）
          3. 用 TopK 策略选择最重要的 batch
          4. 编码为 bit mask

        Args:
            model_update: 模型参数字典 {name: tensor/array}
            batch_size: 每个 batch 的原始参数数量。
                        **必须等于 packing_strategy.get_values_per_chunk()**，
                        确保投票边界和加密 chunk 边界完全一致。
            sparsity_rate: TopK 保留比例
            scoring_method: 评分方式 avg_abs / l2_norm / l1_norm / max_abs
            round_num: 当前轮次
            total_batches: batch 总数（仅用于验证，已废弃，保持兼容）

        Returns:
            np.ndarray(int8) shape=(num_batches,)
        """
        import torch

        # 1) 扁平化
        all_values = []
        for name in sorted(model_update.keys()):
            param = model_update[name]
            if isinstance(param, torch.Tensor):
                flat = param.detach().cpu().numpy().flatten()
            else:
                flat = np.array(param).flatten()
            all_values.extend(flat.tolist())
        total_elements = len(all_values)

        # 2) 切分为 batches
        batches = []
        for i in range(0, total_elements, batch_size):
            chunk = np.array(all_values[i:i + batch_size], dtype=np.float64)
            if len(chunk) < batch_size:
                chunk = np.pad(chunk, (0, batch_size - len(chunk)), mode='constant')
            batches.append(chunk)

        actual_total_batches = len(batches)
        if total_batches > 0 and actual_total_batches != total_batches:
            logger.warning(
                f"mask_from_update: total_batches mismatch "
                f"actual={actual_total_batches} vs expected={total_batches}. "
                f"batch_size={batch_size}, total_elements={total_elements}. "
                f"请检查 batch_size 是否和 packing 策略对齐。"
            )

        # 3) TopK 选择
        topk = max(1, int(np.ceil(actual_total_batches * sparsity_rate)))

        if scoring_method == "avg_abs":
            scores = [float(np.average(np.abs(batch))) for batch in batches]
        elif scoring_method == "l2_norm":
            scores = [float(np.linalg.norm(batch)) for batch in batches]
        elif scoring_method == "l1_norm":
            scores = [float(np.sum(np.abs(batch))) for batch in batches]
        elif scoring_method == "max_abs":
            scores = [float(np.max(np.abs(batch))) for batch in batches]
        else:
            scores = [float(np.average(np.abs(batch))) for batch in batches]

        sorted_indices = np.argsort(scores)[::-1][:topk]

        # 4) 编码为 mask
        mask = np.zeros(actual_total_batches, dtype=np.int8)
        for idx in sorted_indices:
            mask[idx] = 1
        return mask

    def client_compute_mask_from_update(
        self,
        model_update: Dict[str, Any],
        batch_size: int,
        sparsity_rate: float = 0.1,
        scoring_method: str = "avg_abs",
        round_num: int = 0,
        total_batches: int = 0,
    ) -> Optional[np.ndarray]:
        """
        兼容旧接口：从模型更新字典直接计算 TopK 掩码。
        当 mode='none' 时返回 None，否则委托给 compute_mask_from_update。
        """
        if self.mode == "none":
            return None
        return self.compute_mask_from_update(
            model_update=model_update,
            batch_size=batch_size,
            sparsity_rate=sparsity_rate,
            scoring_method=scoring_method,
            round_num=round_num,
            total_batches=total_batches,
        )

        import torch

        # 1) 扁平化
        all_values = []
        for name in sorted(model_update.keys()):
            param = model_update[name]
            if isinstance(param, torch.Tensor):
                flat = param.detach().cpu().numpy().flatten()
            else:
                flat = np.array(param).flatten()
            all_values.extend(flat.tolist())
        total_elements = len(all_values)

        # 2) 切分为 batches
        # batch_size 必须和 packing 策略的 chunk 大小对齐（由调用方保证）。
        # 例如：Merged → slot_count (4096)；CRTStack → slot_count * layers (16384)。
        batches = []
        for i in range(0, total_elements, batch_size):
            chunk = np.array(all_values[i:i + batch_size], dtype=np.float64)
            if len(chunk) < batch_size:
                chunk = np.pad(chunk, (0, batch_size - len(chunk)), mode='constant')
            batches.append(chunk)

        actual_total_batches = len(batches)
        if total_batches > 0 and actual_total_batches != total_batches:
            # 警告但不中断，以实际计算为准
            logger.warning(
                f"mask_from_update: total_batches mismatch "
                f"actual={actual_total_batches} vs expected={total_batches}. "
                f"batch_size={batch_size}, total_elements={total_elements}. "
                f"请检查 batch_size 是否和 packing 策略对齐。"
            )

        # 3) TopK 选择
        topk = max(1, int(np.ceil(actual_total_batches * sparsity_rate)))

        if scoring_method == "avg_abs":
            scores = [float(np.average(np.abs(batch))) for batch in batches]
        elif scoring_method == "l2_norm":
            scores = [float(np.linalg.norm(batch)) for batch in batches]
        elif scoring_method == "l1_norm":
            scores = [float(np.sum(np.abs(batch))) for batch in batches]
        elif scoring_method == "max_abs":
            scores = [float(np.max(np.abs(batch))) for batch in batches]
        else:
            scores = [float(np.average(np.abs(batch))) for batch in batches]

        sorted_indices = np.argsort(scores)[::-1][:topk]

        # 4) 编码为 mask
        mask = np.zeros(actual_total_batches, dtype=np.int8)
        for idx in sorted_indices:
            mask[idx] = 1
        return mask

    # ==================================================================
    # 服务器侧：聚合客户端掩码
    # ==================================================================
    def mask_from_counts(
        self,
        counts: np.ndarray,
        total_batches: int,
        num_voters: int,
    ) -> Optional[np.ndarray]:
        """
        从“各 batch 被选中次数”的计数向量生成全局掩码。

        该接口同时支持扁平投票与层次投票：
          - 扁平：counts = 所有客户端 mask 之和，num_voters = 客户端数；
          - 层次（云-边-端）：counts = 各边缘部分计数之和，
            num_voters = 全体参与客户端数（threshold 模式需要）。

        Args:
            counts: shape=(total_batches,) 的选中次数向量
            total_batches: batch 总数
            num_voters: 参与投票的客户端总数

        Returns:
            np.ndarray(int8) shape=(total_batches,) 或 None（mode=none）
        """
        if self.mode == "none":
            return None

        if self.mode == "union":
            # 并集：任一客户端选中则纳入
            return (counts > 0).astype(np.int8)

        if self.mode == "threshold":
            threshold_count = max(1, int(self.threshold_pct * num_voters))
            return (counts >= threshold_count).astype(np.int8)

        if self.mode == "topk":
            # TopK：按选中次数排序，取前 K 个
            if self.topk_pct is not None and 0 < self.topk_pct <= 1.0:
                k = max(1, int(np.ceil(self.topk_pct * total_batches)))
            else:
                k = self.topk_k
            k = min(k, total_batches)
            topk_indices = np.argpartition(counts, -k)[-k:]
            global_mask = np.zeros(total_batches, dtype=np.int8)
            global_mask[topk_indices] = 1
            return global_mask

        raise ValueError(f"未知投票模式: {self.mode}")

    def server_aggregate(
        self,
        client_masks: List[np.ndarray],
        total_batches: int,
        **kwargs,
    ) -> Optional[np.ndarray]:
        """
        服务器：聚合所有客户端掩码，生成全局掩码。

        Args:
            client_masks: List[np.ndarray]，每个元素 shape=(total_batches,)
            total_batches: batch 总数

        Returns:
            np.ndarray(int8) shape=(total_batches,) 或 None（mode=none）
        """
        if self.mode == "none" or not client_masks:
            return None

        # 堆叠为矩阵 (num_clients, total_batches)
        mask_matrix = np.stack(client_masks, axis=0)
        counts = np.sum(mask_matrix, axis=0)
        global_mask = self.mask_from_counts(counts, total_batches,
                                            num_voters=len(client_masks))

        selected = int(global_mask.sum())
        
        # 详细日志：每个客户端的选中索引 + 聚合统计
        client_indices = [np.where(m == 1)[0].tolist() for m in client_masks]
        counts = np.sum(mask_matrix, axis=0)
        active_indices = [int(i) for i in np.where(global_mask == 1)[0]]
        
        logger.info(
            f"GlobalMaskVote[{self.mode}] | clients={len(client_masks)} | "
            f"global_batches={selected}/{total_batches} | sparsity={selected/total_batches:.1%}"
        )
        # 仅当 batch 数不多时打印详细索引（避免日志过长）
        if total_batches <= 80:
            for cid, indices in enumerate(client_indices):
                logger.info(f"  Client #{cid} selected: {indices} (count={len(indices)})")
            logger.info(f"  Batch counts: {counts.tolist()}")
            logger.info(f"  Global active: {active_indices} (count={len(active_indices)})")
        else:
            logger.info(f"  Per-client selected counts: {[len(idx) for idx in client_indices]}")
            logger.info(f"  Global active indices: {active_indices}")
        
        return global_mask

    # ==================================================================
    # 辅助：从全局掩码得到需要加密的 batch 索引列表
    # ==================================================================
    @staticmethod
    def get_active_batches(global_mask: Optional[np.ndarray]) -> List[int]:
        """返回全局掩码中值为 1 的 batch 索引列表。"""
        if global_mask is None:
            return []
        return [int(i) for i in np.where(global_mask == 1)[0]]

    # ==================================================================
    # 辅助：检查某个 batch 是否在全局掩码中
    # ==================================================================
    @staticmethod
    def is_batch_active(global_mask: Optional[np.ndarray], batch_idx: int) -> bool:
        """检查 batch_idx 是否在全局掩码中。"""
        if global_mask is None:
            return True  # mode=none 时所有 batch 都活跃
        return batch_idx < len(global_mask) and global_mask[batch_idx] == 1
