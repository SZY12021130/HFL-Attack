"""
成员推断攻击（Membership Inference）
=====================================

论文出处：
- Shokri et al., "Membership Inference Attacks Against Machine Learning
  Models", IEEE S&P 2017 —— 用影子模型训练攻击分类器判断样本是否参与训练
- 联邦场景变体：Melis et al., "Exploiting Unintended Feature Leakage in
  Collaborative Learning", IEEE S&P 2019；FedMIA（Liar-Mask/FedMIA）等
- 参考开源：
  https://github.com/yonsei-sslab/MIA
  https://github.com/AdrienBenamira/membership_inference_attack
  https://github.com/Liar-Mask/FedMIA

本实现为无影子模型的轻量变体（loss-based MIA，Salem et al. 2018 证明
无需影子模型即可达到相近效果）：直接利用目标模型对样本的输出计算成员
分数，分数高者判为成员——
    score="loss":        −loss        （过拟合模型对成员样本 loss 更小）
    score="confidence":  max 类概率
    score="entropy":     −预测分布熵
"""

from typing import Any, Dict, List

import os
import sys

import torch
import torch.nn.functional as F

# 允许直接运行本文件（python hfl_attacks/privacy_inference/membership_inference.py）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from hfl_attacks.base import PrivacyAttack, register_attack
from hfl_attacks.metrics import (
    membership_accuracy,
    membership_advantage,
    membership_auc,
)


@register_attack
class MembershipInferenceAttack(PrivacyAttack):
    """
    成员推断：比较目标模型在成员/非成员样本上的输出分数。

    run() 关键 kwargs:
        member_loader:    成员样本 DataLoader（参与过目标模型训练）
        nonmember_loader: 非成员样本 DataLoader
    """

    name = "privacy:membership_inference"

    def __init__(self, score: str = "loss"):
        if score not in ("loss", "confidence", "entropy"):
            raise ValueError(f"未知 score={score}，可选 loss/confidence/entropy")
        self.score = score

    @torch.no_grad()
    def _member_scores(self, model: torch.nn.Module, loader,
                       device: str) -> List[float]:
        """对 loader 中全部样本计算成员分数（分数越高越可能是成员）"""
        model.eval()
        scores: List[float] = []
        for data, target in loader:
            data = data.to(device)
            target = target.to(device)
            logits = model(data)
            log_probs = F.log_softmax(logits, dim=1)
            probs = log_probs.exp()

            if self.score == "loss":
                # 负交叉熵：成员样本 loss 更低 → 分数更高
                batch_score = -F.nll_loss(log_probs, target, reduction="none")
            elif self.score == "confidence":
                # 最大类概率：成员样本置信度更高
                batch_score = probs.max(dim=1).values
            else:  # entropy
                # 负熵：成员样本分布更尖锐
                batch_score = (probs * log_probs).sum(dim=1)  # −H(p)

            scores.extend(batch_score.cpu().tolist())
        return scores

    def run(self, model: torch.nn.Module, device: str = "cpu",
            **kwargs) -> Dict[str, Any]:
        member_loader = kwargs.get("member_loader")
        nonmember_loader = kwargs.get("nonmember_loader")
        if member_loader is None or nonmember_loader is None:
            raise ValueError("MembershipInference 需要 kwargs['member_loader'] 与 "
                             "kwargs['nonmember_loader']")

        member_scores = self._member_scores(model, member_loader, device)
        nonmember_scores = self._member_scores(model, nonmember_loader, device)

        acc = membership_accuracy(member_scores, nonmember_scores)
        return {
            "membership_auc": membership_auc(member_scores, nonmember_scores),
            "membership_acc": acc,
            "membership_advantage": membership_advantage(acc),
            "member_scores": member_scores,
            "nonmember_scores": nonmember_scores,
        }


if __name__ == "__main__":
    from torch.utils.data import DataLoader, Subset

    from hfl_attacks.privacy_inference._utils import make_test_model

    torch.manual_seed(0)
    model = make_test_model("mlp")

    # 轻量场景：用 512 张训练图快速训练（过拟合），成员=其中前 100，
    # 非成员=测试集前 100
    import torchvision
    import torchvision.transforms as transforms

    transform = transforms.Compose([
        transforms.ToTensor(),
        transforms.Normalize((0.5,), (0.5,)),
    ])
    train_ds = torchvision.datasets.MNIST(
        root="./data", train=True, download=True, transform=transform)
    test_ds = torchvision.datasets.MNIST(
        root="./data", train=False, download=True, transform=transform)

    fit_loader = DataLoader(Subset(train_ds, range(512)), batch_size=64,
                            shuffle=True)
    optimizer = torch.optim.SGD(model.parameters(), lr=0.05, momentum=0.9)
    model.train()
    for epoch in range(15):
        for data, target in fit_loader:
            optimizer.zero_grad()
            loss = F.cross_entropy(model(data), target)
            loss.backward()
            optimizer.step()
    print(f"预训练完成（512 样本 x 15 epochs, final loss={loss.item():.4f}）")

    member_loader = DataLoader(Subset(train_ds, range(100)), batch_size=50,
                               shuffle=False)
    nonmember_loader = DataLoader(Subset(test_ds, range(100)), batch_size=50,
                                  shuffle=False)

    for score in ("loss", "confidence", "entropy"):
        attack = MembershipInferenceAttack(score=score)
        res = attack.run(model, member_loader=member_loader,
                         nonmember_loader=nonmember_loader)
        print(f"MembershipInference (score={score}):")
        print(f"  membership_auc        = {res['membership_auc']:.4f}")
        print(f"  membership_acc        = {res['membership_acc']:.4f}")
        print(f"  membership_advantage  = {res['membership_advantage']:.4f}")
