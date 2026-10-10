"""
BadNets 补丁触发器后门攻击
==========================

论文出处：
- Gu, Liu, Dolan-Gavitt, Garg, "BadNets: Identifying Vulnerabilities in the
  ML Model Supply Chain", IEEE Access 2019 —— 在训练图像的固定位置叠加
  一个小方块补丁（patch trigger），并把标签改为目标类；模型学习
  "补丁 -> 目标类"的关联，推理时对带补丁的任意输入都会预测目标类。
- 参考开源：
  * fedlearn-backdoor-attacks
    (https://github.com/mtuann/fedlearn-backdoor-attacks) 的 badnets 实现
  * FLPoison 基准 (https://github.com/vio1etus/FLPoison)

apply_trigger(x) 供评估 runner 构建带触发器的测试集以计算 ASR
（攻击成功率，见 hfl_attacks.metrics.attack_success_rate）。
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
class BadNetsPatchTrigger(DataPoisoner):
    """
    BadNets 补丁触发器：poison_rate 比例的样本在 position 处打上
    trigger_size x trigger_size 的固定方块补丁，标签改为 target_class。

    支持 4 个角（bottom_right / bottom_left / top_right / top_left）
    与中心（center）位置。
    """

    name = "data:backdoor_badnets"

    def __init__(self, target_class: int = 0, poison_rate: float = 0.3,
                 trigger_size: int = 3, trigger_value: float = 1.0,
                 position: str = "bottom_right"):
        self.target_class = target_class
        self.poison_rate = poison_rate
        self.trigger_size = trigger_size
        self.trigger_value = trigger_value
        self.position = position

    def _trigger_coords(self, h: int, w: int):
        """返回补丁区域的 (row_start, col_start)，越界时截断"""
        s = min(self.trigger_size, h, w)
        if self.position == "bottom_right":
            r, c = h - s, w - s
        elif self.position == "bottom_left":
            r, c = h - s, 0
        elif self.position == "top_right":
            r, c = 0, w - s
        elif self.position == "top_left":
            r, c = 0, 0
        elif self.position == "center":
            r, c = (h - s) // 2, (w - s) // 2
        else:
            raise ValueError(f"未知 position: {self.position}")
        return r, c

    def apply_trigger(self, x: torch.Tensor) -> torch.Tensor:
        """
        对输入图像张量打补丁（不改标签）。

        x: (C,H,W) 或 (B,C,H,W) 张量。返回同形状新张量（不修改输入）。
        """
        single = x.dim() == 3
        if single:
            x = x.unsqueeze(0)
        out = x.clone()
        _, _, h, w = out.shape
        r, c = self._trigger_coords(h, w)
        s = min(self.trigger_size, h, w)
        out[:, :, r:r + s, c:c + s] = self.trigger_value
        if single:
            out = out.squeeze(0)
        return out

    def poison_loader(self, loader, num_classes: int = 10):
        x, y = _loader_to_tensors(loader)
        n = len(y)
        rng = np.random.RandomState(42)
        poison_mask = rng.rand(n) < self.poison_rate
        idx = np.where(poison_mask)[0]

        x_new = x.clone()
        y_new = y.clone()
        for i in idx:
            x_new[i] = self.apply_trigger(x[i])
            y_new[i] = int(self.target_class)

        poisoned = _tensors_to_loader(x_new, y_new, loader)
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

    attack = BadNetsPatchTrigger(target_class=0, poison_rate=0.3,
                                 trigger_size=3, trigger_value=1.0,
                                 position="bottom_right")
    poisoned = attack.poison_loader(loader, num_classes=10)

    x_orig, y_orig = _loader_to_tensors(loader)
    x_new, y_new = _loader_to_tensors(poisoned)
    n_poisoned = int((y_new == attack.target_class).sum()) - int(
        (y_orig == attack.target_class).sum())
    print(f"毒化比例: {(y_new != y_orig).float().mean().item():.3f} (期望 ~0.3)")
    print(f"标签分布(前): {torch.bincount(y_orig, minlength=10).tolist()}")
    print(f"标签分布(后): {torch.bincount(y_new, minlength=10).tolist()}")

    # 触发前后图像差异
    i = int((y_new != y_orig).nonzero()[0])
    diff = (x_new[i] - x_orig[i]).abs()
    nonzero = (diff > 0).nonzero()
    print(f"样本 {i}: 标签 {int(y_orig[i])} -> {int(y_new[i])}, "
          f"改动像素数 = {nonzero.shape[0]} (期望 9)")
    assert diff.max().item() <= 1.0
    assert nonzero.shape[0] == 9, "补丁区域应恰好 3x3"

    # apply_trigger 单样本 & batch 一致性
    single = attack.apply_trigger(x_orig[0])
    batched = attack.apply_trigger(x_orig[:4])
    assert single.dim() == 3 and batched.dim() == 4
    assert torch.equal(single, batched[0])
    assert torch.equal(x_orig[0], x_orig[0]), "输入不应被修改"
    print("self-test passed")
