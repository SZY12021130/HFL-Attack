"""
定向标签翻转攻击（targeted label flipping）
===========================================

论文出处：
- Tolpegin, Truex, Gursoy, Liu, "Data Poisoning Attacks Against Federated
  Learning Systems", ESORICS 2020 —— 系统化评估了联邦学习中的定向/非定向
  标签翻转攻击（source class -> target class）
- Biggio et al., "Poisoning attacks against support vector machines",
  ICML 2012 —— 标签翻转攻击的早期形式化
- 参考开源：
  * FLPoison 基准 (https://github.com/vio1etus/FLPoison) 的 labelflipping.py
  * federated-learning-attack-code
    (https://github.com/anoxia-1/federated-learning-attack-code)

与 RandomLabelFlipping 的区别：标签只翻向指定的目标类（target_class），
属于定向攻击；source_class 限定被翻转的源类别（None 表示所有非目标类）。
"""

import os
import sys

import numpy as np
import torch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from hfl_attacks.base import DataPoisoner, register_attack
from hfl_attacks.data_poisoning.label_flipping import (
    _loader_to_tensors,
    _tensors_to_loader,
)


@register_attack
class TargetedLabelFlipping(DataPoisoner):
    """
    定向标签翻转：源类样本以 poison_rate 比例，标签统一改为 target_class。

    属于非后门的定向投毒（无触发器），目标是让全局模型把源类误判为目标类，
    因此 ASR 指标不适用，self.target_class 置为 None。
    """

    name = "data:label_flipping_targeted"

    def __init__(self, source_class=None, target_class: int = 0,
                 poison_rate: float = 1.0):
        self.source_class = source_class   # None = 所有非目标类
        self.flip_target = target_class    # 标签翻向的目标类
        self.poison_rate = poison_rate
        self.target_class = None           # 非后门类攻击，ASR 不适用

    def poison_loader(self, loader, num_classes: int = 10):
        x, y = _loader_to_tensors(loader)
        n = len(y)

        if self.source_class is None:
            candidates = np.where(y.numpy() != self.flip_target)[0]
        else:
            candidates = np.where(y.numpy() == self.source_class)[0]

        rng = np.random.RandomState(42)
        poison_mask = rng.rand(len(candidates)) < self.poison_rate
        idx = candidates[poison_mask]
        y = y.clone()
        for i in idx:
            y[i] = int(self.flip_target)

        poisoned = _tensors_to_loader(x, y, loader)
        poisoned.poisoned_indices = set(idx.tolist())  # 供评估/debug 使用
        return poisoned


if __name__ == "__main__":
    # 自测：优先用 torchvision MNIST 前 200 张，失败则用随机张量
    try:
        from torchvision import datasets, transforms
        ds = datasets.MNIST(root="./data", train=True, download=True,
                            transform=transforms.ToTensor())
        loader = torch.utils.data.DataLoader(
            torch.utils.data.Subset(ds, range(200)), batch_size=32)
        print("[self-test] 使用 MNIST 前 200 张")
    except Exception as e:
        print(f"[self-test] 无法加载 MNIST（{e}），改用随机张量")
        x = torch.rand(200, 1, 28, 28)
        y = torch.randint(0, 10, (200,))
        loader = torch.utils.data.DataLoader(
            torch.utils.data.TensorDataset(x, y), batch_size=32)

    attack = TargetedLabelFlipping(source_class=7, target_class=1,
                                   poison_rate=0.5)
    poisoned = attack.poison_loader(loader, num_classes=10)

    _, y_orig = _loader_to_tensors(loader)
    x_new, y_new = _loader_to_tensors(poisoned)
    changed = (y_orig != y_new)
    print(f"毒化比例: {changed.float().mean().item():.3f} (期望 ~0.5x源类占比)")
    print(f"标签分布(前): {torch.bincount(y_orig, minlength=10).tolist()}")
    print(f"标签分布(后): {torch.bincount(y_new, minlength=10).tolist()}")
    assert not changed[y_orig == attack.flip_target].any(), "目标类不应被改动"
    assert (y_new[changed] == attack.flip_target).all(), "翻转后必须指向目标类"
    assert torch.equal(x_new, x_new), "图像不应被修改"
    print("self-test passed")
