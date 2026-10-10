"""
边缘案例（edge-case）后门攻击
==============================

论文出处：
- Wang, Severi, Britt, Jia, Cina, Liu, Xiong, "Attack of the Tails: Yes, You
  Really Can Backdoor Federated Learning", USENIX Security 2020 —— edge-case
  后门原始论文。核心思想：目标类的"边缘/尾部"样本（看起来像目标类、
  但自然地被标注为其它类的离群样本，即分布尾部的 anomaly）在联邦中
  很少见，攻击者注入这类样本（改标注为目标类、或保持标注）即可让模型
  学到"边缘特征 -> 目标类"的关联，同时几乎不影响干净准确率。
- 参考开源：
  * FLPoison 基准 (https://github.com/vio1etus/FLPoison)
  * federated-learning-attack-code
    (https://github.com/anoxia-1/federated-learning-attack-code)

MNIST 上的语义实现方式
-----------------------
原始论文在 CIFAR-10 / 自动驾驶语义分割等任务上使用预训练编码器在
特征空间挑选尾部样本。在 MNIST 上可行的语义版本：
- 用预训练自编码器（或 LeNet 中间层特征 + kNN 距离）找出 source_class
  中离类中心最远、且视觉上接近 target_class 的样本（例如长得像 1 的
  7、长得像 0 的 6），即自然出现的"边缘样本"；
- 将这些样本的标签改为 target_class（clean-label 语义：图像本身不动，
  只动那些"标注与视觉不一致"的边缘样本），即可在极低毒化率下植入
  只对尾部样本生效的后门。

本实现采用任务书规定的最简单形式：选取 source_class 中"最边缘"
（此处用像素 L2 范数最小作为廉价尾部代理指标，可用
select_fn 换成任意语义评分）的样本，标签改为 target_class，
图像不做修改——即 clean-label edge-case 攻击的退化形式。
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
class EdgeCaseBackdoor(DataPoisoner):
    """
    边缘案例后门：source_class 中边缘（尾部）样本的标签改为
    target_class，图像保持不变（clean-label 退化形式）。

    edge_frac 控制选取源类中边缘评分最高（最靠分布尾部）的比例；
    select_fn 可替换为语义评分函数（如预训练特征空间离群度），
    默认用像素 L2 范数（越小越"细瘦"，在 MNIST 上近似"像 1 的 7"）。
    """

    name = "data:backdoor_edgecase"

    def __init__(self, target_class: int = 0, source_class: int = 7,
                 edge_frac: float = 1.0, select_fn=None):
        self.target_class = target_class
        self.source_class = source_class
        self.edge_frac = edge_frac
        self.select_fn = select_fn  # None = 默认 L2 范数尾部代理

    def _edge_scores(self, x: torch.Tensor) -> torch.Tensor:
        """边缘评分：越大越"边缘"（默认取像素 L2 范数的相反数）"""
        flat = x.flatten(start_dim=1).float()
        if self.select_fn is not None:
            return self.select_fn(flat)
        return -flat.norm(dim=1)  # 范数小 -> 评分高 -> 更边缘

    def poison_loader(self, loader, num_classes: int = 10):
        x, y = _loader_to_tensors(loader)
        src_idx = np.where(y.numpy() == self.source_class)[0]
        if len(src_idx) == 0:
            return _tensors_to_loader(x, y, loader)

        scores = self._edge_scores(x[src_idx])
        k = max(1, int(round(len(src_idx) * self.edge_frac)))
        # 评分最高的 k 个（最边缘）样本
        edge_pos = scores.argsort(descending=True)[:k].numpy()
        idx = src_idx[edge_pos]

        y_new = y.clone()
        for i in idx:
            y_new[i] = int(self.target_class)

        poisoned = _tensors_to_loader(x, y_new, loader)
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

    attack = EdgeCaseBackdoor(target_class=0, source_class=7, edge_frac=0.5)
    poisoned = attack.poison_loader(loader, num_classes=10)

    x_orig, y_orig = _loader_to_tensors(loader)
    x_new, y_new = _loader_to_tensors(poisoned)
    n_src = int((y_orig == attack.source_class).sum())
    n_flip = int(((y_orig == attack.source_class) & (y_new == attack.target_class)).sum())
    print(f"源类(7)样本数: {n_src}, 边缘翻转数: {n_flip} (期望 ~{n_src // 2})")
    print(f"标签分布(前): {torch.bincount(y_orig, minlength=10).tolist()}")
    print(f"标签分布(后): {torch.bincount(y_new, minlength=10).tolist()}")
    print(f"触发前后图像差异: {(x_new - x_orig).abs().max().item()} (应为 0)")

    assert torch.equal(x_new, x_orig), "clean-label：图像不应被修改"
    changed = (y_orig != y_new)
    assert changed[y_orig != attack.source_class].sum().item() == 0, \
        "只应翻转源类样本"
    assert (y_new[changed] == attack.target_class).all()
    print("self-test passed")
