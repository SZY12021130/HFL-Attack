"""
深度梯度泄漏攻击（DLG）
========================

论文出处：
- Zhu, Liu, Han, "Deep Leakage from Gradients", NeurIPS 2019
  （从共享梯度联合优化虚拟输入与虚拟标签，min ||∇W(x*, y*) − ∇W(x, y)||²）
- 参考开源：https://github.com/mit-han-lab/dlg

算法要点：
1. 随机初始化虚拟输入 x*（按真实数据统计量归一化）与 one-hot 软标签 y*；
2. 以梯度匹配（L2 距离）为目标，用 Adam 联合优化 x* 和 y*；
3. 随机初始化不稳定，内部跑 2 次取目标值最优的一次。
"""

from typing import Any, Dict, List, Optional

import os
import sys

import torch

# 允许直接运行本文件（python hfl_attacks/privacy_inference/dlg.py）
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
class DLGAttack(PrivacyAttack):
    """
    深度梯度泄漏：从一批样本的梯度重建输入图像与标签。

    run() 关键 kwargs:
        victim_gradient: 受害者对某批输入的模型参数梯度（list 或 dict）
        ground_truth:    原始输入张量（仅用于评估重建质量，攻击本身不使用标签外的信息）
        ground_truth_labels: 原始标签（用于评估 label_recovery_acc）
        criterion:       损失函数（默认交叉熵；优化内部固定为软标签交叉熵）
    """

    name = "privacy:dlg"

    def __init__(self, iters: int = 200, lr: float = 0.1):
        self.iters = iters
        self.lr = lr

    def _infer_num_classes(self, victim: List[torch.Tensor],
                           ground_truth_labels: Optional[torch.Tensor]) -> int:
        # 优先从输出层 bias 梯度（最后一个 1-D 梯度）推断类别数
        grads_1d = [g for g in victim if g.dim() == 1]
        if grads_1d:
            return int(grads_1d[-1].numel())
        if ground_truth_labels is not None and len(ground_truth_labels) > 0:
            return int(ground_truth_labels.max().item()) + 1
        return 10

    def _single_run(self, model: torch.nn.Module, victim_vec: torch.Tensor,
                    input_shape, num_classes: int, batch_size: int,
                    ground_truth: Optional[torch.Tensor], device: str):
        """单次重建：联合优化 x* 与软标签 y*，返回 (目标值, x*, y_prob)"""
        # 随机初始化，并按真实数据统计量归一化（与原版 DLG 一致）
        x = torch.empty(input_shape, device=device).uniform_(0, 1)
        if ground_truth is not None:
            gt = ground_truth.to(device)
            x = (x - x.mean()) / (x.std() + 1e-8) * gt.std() + gt.mean()
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
            diff = flat_gradient(recon_grads) - victim_vec
            objective = (diff ** 2).sum()
            objective.backward()
            optimizer.step()

        x_recon = x.detach().clone()
        y_prob = torch.softmax(y_logits.detach(), dim=1)
        return objective.item(), x_recon, y_prob

    def run(self, model: torch.nn.Module, device: str = "cpu",
            **kwargs) -> Dict[str, Any]:
        model.eval()
        victim_gradient = kwargs.get("victim_gradient")
        if victim_gradient is None:
            raise ValueError("DLG 需要 kwargs['victim_gradient']")
        ground_truth = kwargs.get("ground_truth")
        ground_truth_labels = kwargs.get("ground_truth_labels")

        victim = align_victim_gradient(victim_gradient, model)
        if ground_truth is None:
            raise ValueError("DLG 需要 kwargs['ground_truth'] 确定输入形状并评估重建质量")
        input_shape = tuple(ground_truth.shape)
        batch_size = input_shape[0]
        # 逐样本梯度先折叠为均值梯度，与重建端（批量均值损失）匹配
        victim = collapse_per_sample_gradient(victim, model, batch_size)
        victim_vec = flat_gradient(victim).to(device)
        num_classes = self._infer_num_classes(victim, ground_truth_labels)

        # 2 次随机重启，取梯度匹配目标最优的一次
        best_obj, best_x, best_y_prob = float("inf"), None, None
        for _ in range(2):
            obj, x_recon, y_prob = self._single_run(
                model, victim_vec, input_shape, num_classes, batch_size,
                ground_truth, device)
            if obj < best_obj:
                best_obj, best_x, best_y_prob = obj, x_recon, y_prob

        recon_labels = best_y_prob.argmax(dim=1)
        recon_grads = compute_parameter_gradient(model, best_x, recon_labels)

        metrics = inversion_metrics(
            best_x, ground_truth, recon_labels, ground_truth_labels,
            recon_grads, victim)
        metrics["objective"] = best_obj
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

    attack = DLGAttack(iters=200, lr=0.1)
    res = attack.run(model, victim_gradient=victim,
                     ground_truth=x, ground_truth_labels=y)
    print(f"DLG 重建（4 张 28x28 MNIST, iters=200, 2 次重启）:")
    print(f"  mse                = {res['mse']:.6f}")
    print(f"  psnr               = {res['psnr']:.2f} dB")
    print(f"  cosine_similarity  = {res['cosine_similarity']:.4f}")
    print(f"  label_recovery_acc = {res['label_recovery_acc']:.1f}%")
    print(f"  objective          = {res['objective']:.6e}")
    print(f"  true labels        = {y.tolist()}")
    print(f"  recovered labels   = {res['reconstructed_labels']}")
