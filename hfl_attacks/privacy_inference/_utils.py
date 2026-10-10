"""
隐私推断攻击共享工具
====================

梯度反演类攻击（DLG / iDLG / InvertingGradients）的公共辅助函数：
- victim_gradient 与模型参数对齐
- 前向 + 反向得到参数梯度
- 全变分（TV）正则
- 自测用 MNIST 小批量加载 / 测试模型构造
"""

from typing import Dict, List, Optional, Sequence, Union

import torch
import torch.nn.functional as F
from torch.utils.data import DataLoader

from hfl_attacks.metrics import cosine_similarity, mse, psnr, label_recovery_accuracy


def align_victim_gradient(victim_gradient, model: torch.nn.Module) -> List[torch.Tensor]:
    """
    把 victim_gradient（list / tuple / dict）对齐为与 model.parameters()
    顺序一致的梯度张量列表。
    """
    param_names = [n for n, _ in model.named_parameters()]
    if isinstance(victim_gradient, dict):
        if set(victim_gradient.keys()) == set(param_names):
            return [victim_gradient[n].detach().clone() for n in param_names]
        values = list(victim_gradient.values())
    else:
        values = list(victim_gradient)
    params = list(model.parameters())
    if len(values) != len(params):
        raise ValueError(
            f"victim_gradient 长度({len(values)})与模型参数数量({len(params)})不一致")
    return [v.detach().clone() for v in values]


def flat_gradient(grads: Sequence[torch.Tensor]) -> torch.Tensor:
    """把梯度列表展平拼接成一个向量"""
    return torch.cat([g.reshape(-1) for g in grads])


def collapse_per_sample_gradient(victim: List[torch.Tensor],
                                 model: torch.nn.Module,
                                 batch_size: int) -> List[torch.Tensor]:
    """
    若 victim 为逐样本梯度（每项形状 (batch, *param_shape)，如逐样本裁剪的
    DP-FL 中服务端可见的形式），折叠为与批量均值梯度一致的形式（对 batch 维取均值）。
    已是标准均值梯度时原样返回。
    """
    params = list(model.parameters())
    if len(victim) == len(params) and all(
            v.shape == (batch_size, *p.shape) for v, p in zip(victim, params)):
        return [v.mean(dim=0) for v in victim]
    return victim


def compute_parameter_gradient(model: torch.nn.Module,
                               x: torch.Tensor,
                               target: torch.Tensor,
                               soft_target: bool = False,
                               create_graph: bool = False) -> List[torch.Tensor]:
    """
    对单批次输入 x 计算损失对模型参数的梯度。

    soft_target=True 时 target 为 (batch, num_classes) 的软标签概率，
    否则为 (batch,) 的硬标签。返回与 model.parameters() 对齐的梯度列表。
    create_graph=True 时保留计算图（梯度可再对 x 求导，供梯度反演优化用）。
    """
    model.zero_grad(set_to_none=True)
    out = model(x)
    if soft_target:
        loss = -(target * F.log_softmax(out, dim=1)).sum(dim=1).mean()
    else:
        loss = F.cross_entropy(out, target)
    grads = torch.autograd.grad(loss, model.parameters(), create_graph=create_graph)
    return [g.clone() for g in grads]


def total_variation(x: torch.Tensor) -> torch.Tensor:
    """全变分（TV）正则项：相邻像素差绝对值的均值（图像先验）"""
    dh = (x[:, :, 1:, :] - x[:, :, :-1, :]).abs().mean()
    dw = (x[:, :, :, 1:] - x[:, :, :, :-1]).abs().mean()
    return dh + dw


def inversion_metrics(reconstructed: torch.Tensor,
                      ground_truth: Optional[torch.Tensor],
                      reconstructed_labels: Optional[Sequence[int]],
                      ground_truth_labels: Optional[Sequence[int]],
                      reconstructed_gradient: List[torch.Tensor],
                      victim_gradient: List[torch.Tensor]) -> Dict[str, float]:
    """
    汇总梯度反演类攻击的通用指标：
    mse / psnr（重建输入 vs 真实输入）、cosine_similarity（重建梯度 vs 真实梯度）、
    label_recovery_acc（恢复标签 vs 真实标签）。
    """
    metrics: Dict[str, float] = {}
    if ground_truth is not None:
        metrics["mse"] = mse(reconstructed, ground_truth)
        metrics["psnr"] = psnr(reconstructed, ground_truth)
    metrics["cosine_similarity"] = cosine_similarity(
        flat_gradient(reconstructed_gradient), flat_gradient(victim_gradient))
    if reconstructed_labels is not None and ground_truth_labels is not None:
        metrics["label_recovery_acc"] = label_recovery_accuracy(
            reconstructed_labels, ground_truth_labels)
    return metrics


# =====================================================================
# 自测辅助
# =====================================================================

def make_test_model(model_name: str = "mlp") -> torch.nn.Module:
    """构造 ckks_fed 的 MNIST 测试模型"""
    from ckks_fed.models import create_model
    config = {
        "data": {"dataset": "mnist", "num_classes": 10},
        "model": {"dropout": 0.0},
    }
    return create_model(model_name, config)


def load_mnist_batch(n: int = 4, root: str = "./data", train: bool = True):
    """
    取 MNIST 前 n 张图（与 ckks_fed 相同的预处理：
    ToTensor + Normalize((0.5,), (0.5,))），返回 (x, y)。
    """
    import torchvision
    import torchvision.transforms as transforms

    transform = transforms.Compose([
        transforms.ToTensor(),
        transforms.Normalize((0.5,), (0.5,)),
    ])
    dataset = torchvision.datasets.MNIST(
        root=root, train=train, download=True, transform=transform)
    loader = DataLoader(dataset, batch_size=n, shuffle=False)
    x, y = next(iter(loader))
    return x, y


def make_victim_gradient(model: torch.nn.Module, x: torch.Tensor,
                         y: torch.Tensor) -> List[torch.Tensor]:
    """自测用：以真实标签计算受害者梯度（模型切到 eval 保证确定性）"""
    model.eval()
    return compute_parameter_gradient(model, x, y)
