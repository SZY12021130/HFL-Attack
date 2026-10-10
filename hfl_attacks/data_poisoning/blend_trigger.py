"""
混合（半透明）触发器后门攻击（Blend / SIG-style blending）
==========================================================

论文出处：
- Chen, Liu, Zhao, Ma, "Targeted Backdoor Attacks on Deep Learning Systems
  Using Data Poisoning", arXiv:1712.05526 (2017) —— Blend 攻击：以固定权重
  blend_ratio 把一个固定随机模式叠加到图像上并改标签；触发器半透明、
  散布全图，比 BadNets 的局部补丁更隐蔽。
- 参考开源：
  * fedlearn-backdoor-attacks
    (https://github.com/mtuann/fedlearn-backdoor-attacks) 的 blend 实现
  * FLPoison 基准 (https://github.com/vio1etus/FLPoison)

触发模式由固定种子生成（攻击者掌握的知识），保证毒化样本与评估时
apply_trigger 构造的触发测试集使用同一模式。
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

_PATTERN_SEED = 0  # 固定种子：攻击者与评估方共享同一触发模式


@register_attack
class BlendTrigger(DataPoisoner):
    """
    Blend 触发器：poison_rate 比例的样本以 blend_ratio 权重叠加固定
    随机模式（与图像同尺寸，取值 [0,1]），标签改为 target_class。
    """

    name = "data:backdoor_blend"

    def __init__(self, target_class: int = 0, poison_rate: float = 0.1,
                 blend_ratio: float = 0.2, pattern_seed: int = _PATTERN_SEED):
        self.target_class = target_class
        self.poison_rate = poison_rate
        self.blend_ratio = blend_ratio
        self.pattern_seed = pattern_seed
        self._pattern_cache = {}

    def _pattern(self, c: int, h: int, w: int, device) -> torch.Tensor:
        """按 (C,H,W) 生成/复用固定种子随机模式"""
        key = (c, h, w)
        if key not in self._pattern_cache:
            rng = np.random.RandomState(self.pattern_seed)
            pattern = rng.rand(c, h, w).astype(np.float32)
            self._pattern_cache[key] = torch.from_numpy(pattern).to(device)
        return self._pattern_cache[key]

    def apply_trigger(self, x: torch.Tensor) -> torch.Tensor:
        """
        对输入图像张量叠加混合触发模式（不改标签）。

        x: (C,H,W) 或 (B,C,H,W) 张量，取值范围假定 [0,1]。返回同形状新张量。
        """
        single = x.dim() == 3
        if single:
            x = x.unsqueeze(0)
        c, h, w = x.shape[1], x.shape[2], x.shape[3]
        pattern = self._pattern(c, h, w, x.device)
        out = (1.0 - self.blend_ratio) * x + self.blend_ratio * pattern
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

    attack = BlendTrigger(target_class=0, poison_rate=0.1, blend_ratio=0.2)
    poisoned = attack.poison_loader(loader, num_classes=10)

    x_orig, y_orig = _loader_to_tensors(loader)
    x_new, y_new = _loader_to_tensors(poisoned)
    print(f"毒化比例: {(y_new != y_orig).float().mean().item():.3f} (期望 ~0.1)")
    print(f"标签分布(前): {torch.bincount(y_orig, minlength=10).tolist()}")
    print(f"标签分布(后): {torch.bincount(y_new, minlength=10).tolist()}")

    # 触发前后图像差异：全图均被扰动，但幅度受 blend_ratio 限制
    i = int((y_new != y_orig).nonzero()[0])
    diff = (x_new[i] - x_orig[i]).abs()
    expected = 0.2 * (attack._pattern(1, 28, 28, x_orig.device)[0] - x_orig[i]).abs()
    print(f"样本 {i}: 标签 {int(y_orig[i])} -> {int(y_new[i])}, "
          f"diff 均值 = {diff.mean().item():.4f}, 最大 = {diff.max().item():.4f}")
    assert diff.max().item() <= attack.blend_ratio + 1e-6
    assert torch.allclose(diff, expected, atol=1e-6), "应严格等于加权混合"

    # apply_trigger 单样本 & batch 一致性、模式确定性
    assert torch.equal(attack.apply_trigger(x_orig[0]),
                       attack.apply_trigger(x_orig[0]))
    assert torch.equal(attack.apply_trigger(x_orig[0]),
                       attack.apply_trigger(x_orig[:2])[0])
    print("self-test passed")
