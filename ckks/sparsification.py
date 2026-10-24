"""
包级稀疏化策略模块 (Package-Level Sparsification)
迁移自 FedPHE 论文的 TopK / RandK 稀疏化机制。

核心思想：
  将模型更新扁平化为一个长数组，按固定 batch_size 切分为多个 batch（包），
  只选择最重要（TopK）或随机（RandK）的 batch 进行加密上传，
  从而大幅减少上游通信量。

"包"与 CKKS SIMD 的关系：
  在 CKKS 方案中，一个密文（ckks_vector）可同时编码 N/2 个浮点数（N 为 poly_modulus_degree）。
  这里的 "batch" 或 "包" 指的就是这样一个 SIMD 密文块。因此 batch_size 不应超过 slot_count，
  否则单个密文装不下，TenSEAL 会报错。CKKSManager 会自动将 batch_size 裁剪到 slot_count。

设计要点：
  - 可插拔策略：SparsificationStrategy 抽象基类
  - 与 packing 解耦：稀疏化在 flatten 之后、加密之前执行
  - 兼容现有架构：稀疏加密更新以 dict 格式传递，带 __sparse__ 标记
"""

import numpy as np
from abc import ABC, abstractmethod
from typing import Dict, List, Tuple, Any
import logging

logger = logging.getLogger(__name__)


class SparsificationStrategy(ABC):
    """包级稀疏化策略抽象基类"""

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @abstractmethod
    def select_batches(self, batches: List[np.ndarray], round_num: int = 0) -> Tuple[List[int], List[np.ndarray]]:
        """
        从所有 batch 中选择一部分返回。

        Args:
            batches: List[np.ndarray]，每个数组是一个 batch（已 padding 到 batch_size）
            round_num: 当前轮次（用于 RandK 的随机种子变化）

        Returns:
            selected_indices: 被选中的 batch 索引列表（已排序）
            selected_batches: 被选中的 batch 数组列表
        """
        pass


class NoSparsification(SparsificationStrategy):
    """无稀疏化：保留所有 batch"""

    @property
    def name(self) -> str:
        return "none"

    def select_batches(self, batches: List[np.ndarray], round_num: int = 0) -> Tuple[List[int], List[np.ndarray]]:
        indices = list(range(len(batches)))
        return indices, batches


class TopKSparsification(SparsificationStrategy):
    """
    TopK 包级稀疏化：按 batch 评分排序，保留最大的 top-k%。

    支持多种评分方式（可插拔）：
      - avg_abs : 绝对值平均值  avg(|x|)      【FedPHE 默认】
      - l2_norm : L2 范数        ||x||_2      【FedPHE 注释掉的方案】
      - l1_norm : L1 范数        sum(|x|)
      - max_abs : 最大绝对值     max(|x|)
    """

    SCORING_METHODS = ["avg_abs", "l2_norm", "l1_norm", "max_abs"]

    def __init__(self, sparsity_rate: float = 0.1, scoring_method: str = "avg_abs"):
        """
        Args:
            sparsity_rate: 保留的 batch 比例，如 0.1 表示保留 10% 的 batch
            scoring_method: 评分方式，可选 avg_abs / l2_norm / l1_norm / max_abs
        """
        if not 0 < sparsity_rate <= 1.0:
            raise ValueError(f"sparsity_rate 必须在 (0, 1.0] 之间，收到 {sparsity_rate}")
        if scoring_method not in self.SCORING_METHODS:
            raise ValueError(f"scoring_method 必须是 {self.SCORING_METHODS} 之一，收到 {scoring_method}")
        self.sparsity_rate = sparsity_rate
        self.scoring_method = scoring_method

    @property
    def name(self) -> str:
        return f"topk_{self.scoring_method}"

    def _compute_scores(self, batches: List[np.ndarray]) -> List[float]:
        """根据 scoring_method 计算每个 batch 的评分"""
        if self.scoring_method == "avg_abs":
            return [float(np.average(np.abs(batch))) for batch in batches]
        elif self.scoring_method == "l2_norm":
            return [float(np.linalg.norm(batch)) for batch in batches]
        elif self.scoring_method == "l1_norm":
            return [float(np.sum(np.abs(batch))) for batch in batches]
        elif self.scoring_method == "max_abs":
            return [float(np.max(np.abs(batch))) for batch in batches]
        else:
            # 防御性回退
            return [float(np.average(np.abs(batch))) for batch in batches]

    def select_batches(self, batches: List[np.ndarray], round_num: int = 0) -> Tuple[List[int], List[np.ndarray]]:
        batch_num = len(batches)
        if batch_num == 0:
            return [], []

        topk = max(1, int(np.ceil(batch_num * self.sparsity_rate)))

        # 根据 scoring_method 计算评分
        scores = self._compute_scores(batches)

        # 取最大的 topk 个值对应的索引
        sorted_indices = np.argsort(scores)[::-1][:topk]
        selected_indices = sorted(sorted_indices.tolist())
        selected_batches = [batches[i] for i in selected_indices]

        return selected_indices, selected_batches


class RandKSparsification(SparsificationStrategy):
    """
    RandK 包级稀疏化：随机保留 k% 的 batch。

    与 FedPHE 论文对齐：
      - 每轮使用 round_num 作为随机种子偏移，保证不同轮次随机性不同但可复现
      - 随机选择 k% 的 batch
    """

    def __init__(self, sparsity_rate: float = 0.1, seed: int = 42):
        if not 0 < sparsity_rate <= 1.0:
            raise ValueError(f"sparsity_rate 必须在 (0, 1.0] 之间，收到 {sparsity_rate}")
        self.sparsity_rate = sparsity_rate
        self.seed = seed

    @property
    def name(self) -> str:
        return "randk"

    def select_batches(self, batches: List[np.ndarray], round_num: int = 0) -> Tuple[List[int], List[np.ndarray]]:
        batch_num = len(batches)
        if batch_num == 0:
            return [], []

        randk = max(1, int(np.ceil(batch_num * self.sparsity_rate)))

        # 使用 round_num 作为随机种子偏移，保证不同轮次随机性不同
        rng = np.random.RandomState(self.seed + round_num)
        selected_indices = sorted(rng.choice(batch_num, size=randk, replace=False).tolist())
        selected_batches = [batches[i] for i in selected_indices]

        return selected_indices, selected_batches


def create_sparsification_strategy(config: Dict) -> SparsificationStrategy:
    """
    根据配置创建稀疏化策略。

    Args:
        config: CKKS 配置字典（通常为 config['ckks']）

    Returns:
        SparsificationStrategy 实例
    """
    sparsity_cfg = config.get("sparsification", {})
    strategy_name = sparsity_cfg.get("strategy", "none")

    if strategy_name == "none" or strategy_name is None:
        logger.info("包级稀疏化: 未启用")
        return NoSparsification()
    elif strategy_name == "topk":
        rate = sparsity_cfg.get("sparsity_rate", 0.1)
        scoring = sparsity_cfg.get("scoring_method", "avg_abs")
        logger.info(f"包级稀疏化: 启用 TopK  |  sparsity_rate={rate}  |  scoring={scoring}")
        return TopKSparsification(sparsity_rate=rate, scoring_method=scoring)
    elif strategy_name == "randk":
        rate = sparsity_cfg.get("sparsity_rate", 0.1)
        seed = sparsity_cfg.get("seed", 42)
        logger.info(f"包级稀疏化: 启用 RandK  |  sparsity_rate={rate}  |  seed={seed}")
        return RandKSparsification(sparsity_rate=rate, seed=seed)
    else:
        logger.warning(f"未知稀疏化策略 '{strategy_name}'，回退到无稀疏化")
        return NoSparsification()
