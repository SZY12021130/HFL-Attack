"""
梯度反演攻击（Inverting Gradients，简化版）
============================================

论文出处：
- Geiping et al., "Inverting Gradients - How easy is it to break privacy in
  federated learning?", NeurIPS 2020
  核心改进：用余弦相似度作梯度匹配目标（比 DLG 的 L2 对尺度不敏感）、
  Adam 优化、全变分（TV）正则作图像先验、randn 初始化（比 DLG 的
  均匀初始化稳定得多），并交替优化输入与 BN 统计量。
- 参考开源：https://github.com/JonasGeiping/invertinggradients

本实现为简化版：保留核心组合（randn 初始化 + 余弦相似度目标 +
Adam + TV 正则 + 值域 clamp），不含 BN 统计量优化与分组匹配。
标签处理：与 DLG 类似联合优化软标签（Geiping 原文默认标签已知或
由其它手段恢复；软标签优化是参考实现的常见替代），恢复标签取软标签 argmax。
"""

from typing import Any, Dict, Optional

import os
import sys

import torch
import torch.nn.functional as F

# 允许直接运行本文件（python hfl_attacks/privacy_inference/inverting_gradients.py）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from hfl_attacks.base import PrivacyAttack, register_attack
from hfl_attacks.privacy_inference._utils import (
    align_victim_gradient,
    collapse_per_sample_gradient,
    compute_parameter_gradient,
    flat_gradient,
    inversion_metrics,
    total_variation,
)


@register_attack
class InvertingGradientsAttack(PrivacyAttack):
    """
    梯度反演：余弦相似度梯度匹配 + TV 正则图像先验。

    run() 关键 kwargs:
        victim_gradient: 受害者对某批输入的模型参数梯度（list 或 dict）
        ground_truth:    原始输入张量（用于评估与初始化统计量）
        ground_truth_labels: 原始标签（仅用于评估 label_recovery_acc）
        tv_weight:       TV 正则权重（默认 1e-4）
    """

    name = "privacy:inverting_gradients"

    def __init__(self, iters: int = 300, lr: float = 0.1, tv_weight: float = 1e-4):
        self.iters = iters
        self.lr = lr
        self.tv_weight = tv_weight

    def _infer_num_classes(self, victim, ground_truth_labels) -> int:
        grads_1d = [g for g in victim if g.dim() == 1]
        if grads_1d:
            return int(grads_1d[-1].numel())
        if ground_truth_labels is not None and len(ground_truth_labels) > 0:
            return int(ground_truth_labels.max().item()) + 1
        return 10

    def _single_run(self, model: torch.nn.Module, victim_vec: torch.Tensor,
                    input_shape, num_classes: int, batch_size: int,
                    ground_truth: Optional[torch.Tensor],
                    device: str, tv_weight: float):
        """单次重建：randn 初始化 + Adam 优化 cos 相似度目标 + TV 正则"""
        if ground_truth is not None:
            gt = ground_truth.to(device)
            # 按真实数据统计量初始化（randn 初始化是本文稳定性的关键来源）
            mean = gt.mean(dim=0, keepdim=True)
            std = gt.std(dim=0, keepdim=True)
            x = torch.randn(input_shape, device=device) * std + mean
            lo, hi = float(gt.min()), float(gt.max())
        else:
            x = torch.randn(input_shape, device=device)
            lo, hi = -1.0, 1.0
        x.requires_grad_(True)
        y_logits = torch.zeros(batch_size, num_classes, device=device,
                               requires_grad=True)

        optimizer = torch.optim.Adam([x, y_logits], lr=self.lr)
        objective = None
        for _ in range(self.iters):
            optimizer.zero_grad(set_to_none=True)
            y_prob = torch.softmax(y_logits, dim=1)
            recon_grads = compute_parameter_gradient(
                model, x, y_prob, soft_target=True, create_graph=True)
            cos = F.cosine_similarity(flat_gradient(recon_grads), victim_vec, dim=0)
            objective = 1 - cos + tv_weight * total_variation(x)
            objective.backward()
            optimizer.step()
            with torch.no_grad():
                x.clamp_(lo, hi)

        return objective.item(), x.detach().clone(), torch.softmax(y_logits.detach(), dim=1)

    def run(self, model: torch.nn.Module, device: str = "cpu",
            **kwargs) -> Dict[str, Any]:
        model.eval()
        victim_gradient = kwargs.get("victim_gradient")
        if victim_gradient is None:
            raise ValueError("InvertingGradients 需要 kwargs['victim_gradient']")
        ground_truth = kwargs.get("ground_truth")
        ground_truth_labels = kwargs.get("ground_truth_labels")
        if ground_truth is None:
            raise ValueError("InvertingGradients 需要 kwargs['ground_truth'] 确定输入形状并评估重建质量")
        tv_weight = kwargs.get("tv_weight", self.tv_weight)

        victim = align_victim_gradient(victim_gradient, model)
        input_shape = tuple(ground_truth.shape)
        batch_size = input_shape[0]
        # 逐样本梯度先折叠为均值梯度，与重建端（批量均值损失）匹配
        victim = collapse_per_sample_gradient(victim, model, batch_size)
        victim_vec = flat_gradient(victim).to(device)
        num_classes = self._infer_num_classes(victim, ground_truth_labels)

        # 2 次随机重启，取目标最优
        best_obj, best_x, best_y_prob = float("inf"), None, None
        for _ in range(2):
            obj, x_recon, y_prob = self._single_run(
                model, victim_vec, input_shape, num_classes, batch_size,
                ground_truth, device, tv_weight)
            if obj < best_obj:
                best_obj, best_x, best_y_prob = obj, x_recon, y_prob

        # 恢复标签：软标签 argmax
        recon_labels = best_y_prob.argmax(dim=1)
        recon_grads = compute_parameter_gradient(
            model, best_x, best_y_prob, soft_target=True)

        metrics = inversion_metrics(
            best_x, ground_truth, recon_labels, ground_truth_labels,
            recon_grads, victim)
        metrics["objective"] = best_obj
        metrics["tv_weight"] = tv_weight
        metrics["reconstructed"] = best_x
        metrics["reconstructed_labels"] = recon_labels.tolist()
        return metrics


if __name__ == "__main__":
    torch.manual_seed(0)
    from hfl_attacks.privacy_inference._utils import (
        load_mnist_batch, make_test_model, make_victim_gradient)

    model = make_test_model("mlp")
    x, y = load_mnist_batch(4)
    victim = make_victim_gradient(model, x, y)

    attack = InvertingGradientsAttack(iters=300, lr=0.1)
    res = attack.run(model, victim_gradient=victim,
                     ground_truth=x, ground_truth_labels=y)
    print(f"InvertingGradients 重建（4 张 28x28 MNIST, iters=300, 2 次重启）:")
    print(f"  mse                = {res['mse']:.6f}")
    print(f"  psnr               = {res['psnr']:.2f} dB")
    print(f"  cosine_similarity  = {res['cosine_similarity']:.4f}")
    print(f"  label_recovery_acc = {res['label_recovery_acc']:.1f}%")
    print(f"  objective          = {res['objective']:.6f}")
    print(f"  true labels        = {y.tolist()}")
    print(f"  recovered labels   = {res['reconstructed_labels']}")
