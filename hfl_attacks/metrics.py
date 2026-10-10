"""
通用攻击评价指标
================

针对四类攻击的通用评估指标（不依赖 scikit-learn 等第三方库）：

投毒/系统攻击（作用于训练）：
- clean_accuracy / attacked_accuracy / accuracy_drop / relative_accuracy_drop
- asr                 攻击成功率（后门类攻击：触发样本被预测为目标类的比例）
- convergence_round   达到目标准确率所需轮数
- wasted_comm_ratio   攻击造成的无效通信占比（系统攻击）

隐私推断攻击：
- mse / psnr          重建数据质量
- label_recovery_acc  标签恢复准确率（iDLG 等）
- cosine_similarity   重建梯度与真实梯度的余弦相似度
- membership_auc / membership_acc / membership_advantage  成员推断指标
"""

from typing import Dict, List, Sequence, Tuple

import numpy as np
import torch
import torch.nn.functional as F


# =====================================================================
# 投毒 / 系统攻击指标
# =====================================================================

def accuracy_drop(clean_acc: float, attacked_acc: float) -> float:
    """准确率绝对下降（百分点）"""
    return clean_acc - attacked_acc


def relative_accuracy_drop(clean_acc: float, attacked_acc: float) -> float:
    """准确率相对下降比例 [0,1]"""
    if clean_acc <= 0:
        return 0.0
    return max(0.0, (clean_acc - attacked_acc) / clean_acc)


def convergence_round(acc_history: Sequence[float], target_acc: float) -> int:
    """
    首次达到 target_acc 的轮次（1-based），未达到返回 -1。
    acc_history: 每轮全局模型测试准确率（%）
    """
    for i, acc in enumerate(acc_history):
        if acc >= target_acc:
            return i + 1
    return -1


@torch.no_grad()
def attack_success_rate(model: torch.nn.Module, triggered_loader, target_class: int,
                        device: str = "cpu") -> float:
    """
    攻击成功率（ASR）：触发样本被预测为目标类的比例。

    triggered_loader: 带触发器的测试数据（标签为原始标签，不用于计算）
    """
    model.eval()
    correct_trigger = 0
    total = 0
    for data, _ in triggered_loader:
        data = data.to(device)
        output = model(data)
        pred = output.argmax(dim=1)
        correct_trigger += (pred == target_class).sum().item()
        total += data.size(0)
    return 100.0 * correct_trigger / max(1, total)


def wasted_comm_ratio(rounds_under_attack: int, total_rounds: int) -> float:
    """无效通信占比：攻击期间未产生有效模型进展的轮次比例"""
    if total_rounds <= 0:
        return 0.0
    return rounds_under_attack / total_rounds


# =====================================================================
# 隐私推断指标（重建质量）
# =====================================================================

def mse(reconstructed: torch.Tensor, ground_truth: torch.Tensor) -> float:
    """均方误差"""
    return F.mse_loss(reconstructed.float(), ground_truth.float()).item()


def psnr(reconstructed: torch.Tensor, ground_truth: torch.Tensor) -> float:
    """峰值信噪比（dB），越高越好"""
    mse_val = mse(reconstructed, ground_truth)
    if mse_val <= 1e-12:
        return float('inf')
    return 10.0 * np.log10(1.0 / mse_val)


def cosine_similarity(a: torch.Tensor, b: torch.Tensor) -> float:
    """两个张量展平后的余弦相似度"""
    a_flat = a.detach().flatten().float()
    b_flat = b.detach().flatten().float()
    return F.cosine_similarity(a_flat, b_flat, dim=0).item()


def label_recovery_accuracy(recovered_labels: Sequence[int],
                            true_labels: Sequence[int]) -> float:
    """标签恢复准确率（%）"""
    if len(true_labels) == 0:
        return 0.0
    correct = sum(1 for r, t in zip(recovered_labels, true_labels) if int(r) == int(t))
    return 100.0 * correct / len(true_labels)


# =====================================================================
# 隐私推断指标（成员推断）
# =====================================================================

def membership_auc(member_scores: Sequence[float],
                   nonmember_scores: Sequence[float]) -> float:
    """
    成员推断 AUC：随机正样本（成员）分数高于随机负样本（非成员）的概率。
    用 Mann-Whitney U 统计量计算，无第三方依赖。0.5 = 无推断能力。
    """
    member = list(member_scores)
    nonmember = list(nonmember_scores)
    if not member or not nonmember:
        return 0.5
    wins, total = 0.0, 0
    for ms in member:
        for ns in nonmember:
            wins += 1.0 if ms > ns else (0.5 if ms == ns else 0.0)
            total += 1
    return wins / total


def membership_accuracy(member_scores: Sequence[float],
                        nonmember_scores: Sequence[float],
                        threshold: float = None) -> float:
    """
    成员推断准确率：在最佳阈值（或给定阈值）下的分类准确率。
    threshold=None 时取两类分数分布的中点自适应阈值。
    """
    member = list(member_scores)
    nonmember = list(nonmember_scores)
    if not member or not nonmember:
        return 0.5
    if threshold is None:
        threshold = (np.median(member) + np.median(nonmember)) / 2.0
    tp = sum(1 for s in member if s >= threshold)
    tn = sum(1 for s in nonmember if s < threshold)
    return (tp + tn) / (len(member) + len(nonmember))


def membership_advantage(member_acc: float) -> float:
    """成员推断优势 = acc - 0.5（0 表示无推断能力）"""
    return member_acc - 0.5


# =====================================================================
# 汇总工具
# =====================================================================

def summarize_rounds(acc_history: Sequence[float], target_acc: float = None) -> Dict:
    """汇总一轮攻击实验的通用指标"""
    hist = list(acc_history)
    summary = {
        'final_accuracy': hist[-1] if hist else 0.0,
        'best_accuracy': max(hist) if hist else 0.0,
        'mean_accuracy': float(np.mean(hist)) if hist else 0.0,
    }
    if target_acc is not None:
        summary['convergence_round'] = convergence_round(hist, target_acc)
    return summary
