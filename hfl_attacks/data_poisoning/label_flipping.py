"""
数据投毒攻击（参考实现样例：标签翻转）
=====================================

论文出处：
- Tolpegin et al., "Data Poisoning Attacks Against Federated Learning Systems",
  ESORICS 2020 —— targeted label flipping（source→target）
- Biggio et al., "Poisoning attacks against support vector machines",
  ICML 2012 —— 标签翻转攻击的早期形式化
- 参考开源：FLPoison 基准 (https://github.com/vio1etus/FLPoison) 的 labelflipping.py
"""

import numpy as np
import torch
from torch.utils.data import DataLoader, TensorDataset

from hfl_attacks.base import DataPoisoner, register_attack


def _loader_to_tensors(loader):
    """把 DataLoader 的数据收集为 (x, y) 张量（兼容 TensorDataset / Subset）"""
    xs, ys = [], []
    for x, y in loader.dataset:
        xs.append(x)
        ys.append(y)
    return torch.stack(xs), torch.tensor(ys)


def _tensors_to_loader(x, y, loader):
    """保持原 loader 的 batch_size/shuffle 与样本顺序，构造新的 DataLoader"""
    return DataLoader(
        TensorDataset(x, y),
        batch_size=loader.batch_size,
        shuffle=getattr(loader, "shuffle", False),
        drop_last=getattr(loader, 'drop_last', False),
    )


@register_attack
class RandomLabelFlipping(DataPoisoner):
    """
    随机标签翻转：以 poison_rate 比例随机选取样本，标签改为其它任意类别。

    属于非定向(untargeted)数据投毒，目标是降低全局模型整体准确率。
    """

    name = "data:label_flipping_random"

    def __init__(self, poison_rate: float = 0.5):
        self.poison_rate = poison_rate

    def poison_loader(self, loader, num_classes: int = 10):
        x, y = _loader_to_tensors(loader)
        n = len(y)
        rng = np.random.RandomState(42)
        poison_mask = rng.rand(n) < self.poison_rate
        idx = np.where(poison_mask)[0]
        for i in idx:
            choices = [c for c in range(num_classes) if c != int(y[i])]
            y[i] = int(rng.choice(choices))
        return _tensors_to_loader(x, y, loader)
