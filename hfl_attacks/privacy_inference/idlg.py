"""
改进版深度梯度泄漏攻击（iDLG）
================================

论文出处：
- Zhao et al., "iDLG: Improved Deep Leakage from Gradients", arXiv:2001.02610
  （ICCV 2021 前身）
  核心观察：交叉熵损失下，输出层 bias 的梯度为 (p − y)：真实标签对应分量为
  (p_c − 1) < 0，其余分量为 p_j > 0 —— 真实标签 = bias 梯度中最负（唯一负值）
  的分量下标，无需优化即可解析恢复标签，之后只需优化输入 x*。
- 参考开源：https://github.com/mit-han-lab/dlg

标签解析支持两种 victim_gradient 形式：
1. 逐样本梯度（bias 项形状 (batch, num_classes)，如逐样本裁剪的 DP-FL 场景中
   服务端可观察的形式）：对每个样本独立 argmin，可 100% 恢复；
2. 标准批量均值梯度（bias 项形状 (num_classes,)）：负分量只给出批内出现的
   不同标签集合（信息论上每样本标签不可分），按负值从大到小补齐 batch，
   label_recovery_acc 会受限于该固有歧义。
"""

from typing import Any, Dict, List, Optional

import os
import sys

import torch

# 允许直接运行本文件（python hfl_attacks/privacy_inference/idlg.py）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from hfl_attacks.base import PrivacyAttack, register_attack
from hfl_attacks.privacy_inference._utils import (
    align_victim_gradient,
    collapse_per_sample_gradient,
    compute_parameter_gradient,
    flat_gradient,
    inversion_metrics,
)


@register_attack
class IDLGAttack(PrivacyAttack):
    """
    iDLG：先解析恢复标签，再以硬标签优化输入。

    run() 关键 kwargs:
        victim_gradient: 受害者梯度（list 或 dict，逐样本或批量均值形式）
        ground_truth:    原始输入张量（用于评估）
        ground_truth_labels: 原始标签（用于评估 label_recovery_acc）
    """

    name = "privacy:idlg"

    def __init__(self, iters: int = 200, lr: float = 0.1):
        self.iters = iters
        self.lr = lr

    def _recover_labels(self, victim: List[torch.Tensor],
                        batch_size: int) -> torch.Tensor:
        """从输出层 bias 梯度符号解析真实标签（见模块 docstring）"""
        # 输出层 bias 梯度：逐样本形式 (batch, C) 或均值形式 (C,)
        bias_grad = None
        for g in reversed(victim):
            if g.dim() == 2 and g.shape[0] == batch_size:
                bias_grad = g
                break
            if g.dim() == 1:
                bias_grad = g
                break
        if bias_grad is None:
            raise ValueError("victim_gradient 中找不到输出层 bias 梯度，无法解析标签")

        if bias_grad.dim() == 2:  # 逐样本梯度：每行独立解析，可 100% 恢复
            return bias_grad.argmin(dim=1)

        # 批量均值梯度：负分量 = 批内出现的不同标签
        num_classes = bias_grad.numel()
        negative = (bias_grad < 0).nonzero(as_tuple=True)[0].tolist()
        if not negative:  # 数值上无负分量时退化为 argmin
            negative = [int(bias_grad.argmin())]
        if len(negative) >= batch_size:
            labels = negative[:batch_size]
        else:  # 标签有重复时，负分量数 < batch_size，按最负顺序补齐（存在固有歧义）
            order = bias_grad.argsort(descending=False).tolist()
            labels = list(negative)
            for c in order:
                if len(labels) >= batch_size:
                    break
                if c not in labels:
                    labels.append(c)
        return torch.tensor(labels[:batch_size], dtype=torch.long)

    def _single_run(self, model: torch.nn.Module, victim_vec: torch.Tensor,
                    labels: torch.Tensor, input_shape,
                    ground_truth: Optional[torch.Tensor], device: str):
        """单次重建：固定硬标签，只优化 x*，返回 (目标值, x*)"""
        x = torch.empty(input_shape, device=device).uniform_(0, 1)
        if ground_truth is not None:
            gt = ground_truth.to(device)
            x = (x - x.mean()) / (x.std() + 1e-8) * gt.std() + gt.mean()
        x.requires_grad_(True)

        optimizer = torch.optim.Adam([x], lr=self.lr)
        objective = None
        for _ in range(self.iters):
            optimizer.zero_grad(set_to_none=True)
            recon_grads = compute_parameter_gradient(model, x, labels, create_graph=True)
            diff = flat_gradient(recon_grads) - victim_vec
            objective = (diff ** 2).sum()
            objective.backward()
            optimizer.step()

        return objective.item(), x.detach().clone()

    def run(self, model: torch.nn.Module, device: str = "cpu",
            **kwargs) -> Dict[str, Any]:
        model.eval()
        victim_gradient = kwargs.get("victim_gradient")
        if victim_gradient is None:
            raise ValueError("iDLG 需要 kwargs['victim_gradient']")
        ground_truth = kwargs.get("ground_truth")
        ground_truth_labels = kwargs.get("ground_truth_labels")
        if ground_truth is None:
            raise ValueError("iDLG 需要 kwargs['ground_truth'] 确定输入形状并评估重建质量")

        victim = align_victim_gradient(victim_gradient, model)
        input_shape = tuple(ground_truth.shape)
        batch_size = input_shape[0]

        # 第 1 步：解析恢复标签（无需优化，基于未折叠的逐样本/均值 bias 梯度）
        recon_labels = self._recover_labels(victim, batch_size).to(device)

        # 第 2 步：固定标签，2 次随机重启优化输入，取目标最优
        # 逐样本梯度先折叠为均值梯度，与重建端（批量均值损失）匹配
        victim_mean = collapse_per_sample_gradient(victim, model, batch_size)
        victim_vec = flat_gradient(victim_mean).to(device)
        best_obj, best_x = float("inf"), None
        for _ in range(2):
            obj, x_recon = self._single_run(
                model, victim_vec, recon_labels, input_shape, ground_truth, device)
            if obj < best_obj:
                best_obj, best_x = obj, x_recon

        recon_grads = compute_parameter_gradient(model, best_x, recon_labels)

        metrics = inversion_metrics(
            best_x, ground_truth, recon_labels, ground_truth_labels,
            recon_grads, victim_mean)
        metrics["objective"] = best_obj
        metrics["reconstructed"] = best_x
        metrics["reconstructed_labels"] = recon_labels.tolist()
        return metrics


def _per_sample_victim_gradient(model: torch.nn.Module, x: torch.Tensor,
                                y: torch.Tensor) -> List[torch.Tensor]:
    """
    自测用：构造逐样本梯度观察（如逐样本裁剪的 DP-FL 中服务端可见的形式），
    每个参数的梯度项堆叠为 (batch, *param_shape)。
    """
    model.eval()
    grads_per_sample = [[] for _ in model.parameters()]
    for i in range(x.size(0)):
        model.zero_grad(set_to_none=True)
        loss = torch.nn.functional.cross_entropy(model(x[i:i + 1]), y[i:i + 1])
        grads = torch.autograd.grad(loss, model.parameters())
        for j, g in enumerate(grads):
            grads_per_sample[j].append(g)
    return [torch.stack(g) for g in grads_per_sample]


if __name__ == "__main__":
    torch.manual_seed(0)
    from hfl_attacks.privacy_inference._utils import (
        load_mnist_batch, make_test_model)

    model = make_test_model("mlp")
    x, y = load_mnist_batch(4)
    victim = _per_sample_victim_gradient(model, x, y)

    attack = IDLGAttack(iters=200, lr=0.1)
    res = attack.run(model, victim_gradient=victim,
                     ground_truth=x, ground_truth_labels=y)
    print(f"iDLG 重建（4 张 28x28 MNIST, iters=200, 2 次重启, 逐样本梯度）:")
    print(f"  mse                = {res['mse']:.6f}")
    print(f"  psnr               = {res['psnr']:.2f} dB")
    print(f"  cosine_similarity  = {res['cosine_similarity']:.4f}")
    print(f"  label_recovery_acc = {res['label_recovery_acc']:.1f}%")
    print(f"  objective          = {res['objective']:.6e}")
    print(f"  true labels        = {y.tolist()}")
    print(f"  recovered labels   = {res['reconstructed_labels']}")
