"""
CKKS 模型更新可插拔打包策略模块
支持多种打包方式以减少 ciphertext 数量：
  - layerwise:   逐层加密（默认，兼容原代码）
  - merged:      将所有层合并成一个数组后统一分块加密
"""

import numpy as np
import torch
from typing import Dict, List, Tuple, Any
from abc import ABC, abstractmethod
import logging

logger = logging.getLogger(__name__)


class PackingMetadata:
    """打包后的元数据，用于解密恢复"""

    def __init__(self, strategy: str, data: Dict[str, Any]):
        self.strategy = strategy
        self.data = data

    def to_dict(self) -> Dict[str, Any]:
        return {"__packing_strategy__": self.strategy, "__packing_data__": self.data}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PackingMetadata":
        return cls(d["__packing_strategy__"], d["__packing_data__"])


class PackingStrategy(ABC):
    """模型更新打包策略抽象基类"""

    def __init__(self, slot_count: int):
        self.slot_count = slot_count

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @abstractmethod
    def pack(self, model_update: Dict[str, torch.Tensor]) -> Tuple[List[np.ndarray], PackingMetadata]:
        """
        将 model_update 打包为可加密块列表。
        返回: (chunks, metadata)
          chunks: List[np.ndarray]，每个数组长度为 slot_count（不足则 pad）
          metadata: 解密恢复所需元数据
        """
        pass

    @abstractmethod
    def unpack(self, decrypted_chunks: List[np.ndarray], metadata: PackingMetadata) -> Dict[str, torch.Tensor]:
        """
        从解密后的 chunks 恢复 model_update。
        """
        pass

    def _pad_chunk(self, chunk: np.ndarray) -> np.ndarray:
        """将数组填充到 slot_count 长度"""
        if len(chunk) < self.slot_count:
            return np.pad(chunk, (0, self.slot_count - len(chunk)), mode="constant")
        return chunk

    def get_values_per_chunk(self) -> int:
        """返回每个 chunk 对应的**原始参数数量**。

        用于 GlobalMaskVote 对齐切分边界：投票阶段的 batch 大小
        必须和加密阶段的 chunk 切分大小一致。

        默认实现：每个 chunk 存 slot_count 个原始值（Merged/Layerwise）。
        """
        return self.slot_count

    def create_zero_chunk(self) -> np.ndarray:
        """生成表示"零值"的 chunk，用于 mask=0 时的占位填充。"""
        return np.zeros(self.slot_count, dtype=np.float64)


class LayerwisePacking(PackingStrategy):
    """
    逐层打包（默认行为，完全兼容原代码）。
    每层独立 flatten 后分块，保持 Dict[str, List[CKKSVector]] 的层级结构。
    """

    @property
    def name(self) -> str:
        return "layerwise"

    def pack(self, model_update: Dict[str, torch.Tensor]) -> Tuple[List[np.ndarray], PackingMetadata]:
        """
        为了与上层 crypto.py 兼容，layerwise 模式下不真正合并，
        而是返回一个标记性的结构，由 crypto.py 按原逻辑逐层处理。
        这里仅返回空 chunks 和标记 metadata。
        """
        return [], PackingMetadata(self.name, {"layerwise": True})

    def unpack(self, decrypted_chunks: List[np.ndarray], metadata: PackingMetadata) -> Dict[str, torch.Tensor]:
        # layerwise 不走此接口，由 CKKSDecryptor.decrypt_to_tensor 逐层处理
        raise NotImplementedError("LayerwisePacking.unpack 不应被直接调用")


class MergedPacking(PackingStrategy):
    """
    合并打包：将所有层的参数按固定顺序 concat 成一个长数组，
    再按 slot_count 统一分块加密。
    可显著减少小层（bias）造成的密文碎片。
    """

    @property
    def name(self) -> str:
        return "merged"

    def pack(self, model_update: Dict[str, torch.Tensor]) -> Tuple[List[np.ndarray], PackingMetadata]:
        # 按固定顺序合并所有参数（排序保证确定性）
        all_values = []
        shapes = {}
        offsets = {}
        offset = 0

        for name in sorted(model_update.keys()):
            param = model_update[name]
            flat = param.detach().cpu().numpy().flatten()
            shapes[name] = list(param.shape)
            offsets[name] = (offset, param.numel())
            all_values.extend(flat.tolist())
            offset += param.numel()

        # 统一分块并填充
        chunks = []
        for i in range(0, len(all_values), self.slot_count):
            chunk = np.array(all_values[i : i + self.slot_count], dtype=np.float64)
            chunk = self._pad_chunk(chunk)
            chunks.append(chunk)

        metadata = PackingMetadata(
            self.name,
            {"shapes": shapes, "offsets": offsets, "total_elements": len(all_values)},
        )
        return chunks, metadata

    def unpack(self, decrypted_chunks: List[np.ndarray], metadata: PackingMetadata) -> Dict[str, torch.Tensor]:
        data = metadata.data
        shapes = data["shapes"]
        offsets = data["offsets"]
        total_elements = data["total_elements"]

        # 拼接所有 chunks 并截取有效长度
        all_values = np.concatenate([chunk[: self.slot_count] for chunk in decrypted_chunks])[:total_elements]

        result = {}
        for name in sorted(shapes.keys()):
            offset, numel = offsets[name]
            shape = shapes[name]
            result[name] = torch.tensor(all_values[offset : offset + numel].reshape(shape))
        return result


def create_packing_strategy(config: Dict, slot_count: int) -> PackingStrategy:
    """根据配置创建打包策略。仅支持 layerwise / merged，未知策略回退 merged 并警告。"""
    packing_config = config.get("packing", {})
    strategy_name = packing_config.get("strategy", "layerwise")

    if strategy_name == "layerwise":
        return LayerwisePacking(slot_count)
    elif strategy_name == "merged":
        return MergedPacking(slot_count)
    else:
        logger.warning(f"未知打包策略 '{strategy_name}'，回退到 merged 打包")
        return MergedPacking(slot_count)
