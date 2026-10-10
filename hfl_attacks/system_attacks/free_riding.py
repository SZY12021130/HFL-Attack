"""
系统攻击：搭便车攻击（Free-Riding）
=================================

论文出处：
- Fraboni, Vidal, Lorenzi, "Free-rider Attacks on Model Aggregation in
  Federated Learning", AISTATS 2021 (PMLR v130) —— 首次系统分析搭便车攻击：
  恶意客户端不上传真实训练更新，却能从聚合中获益。
  论文定义了三种攻击：
  - δ0（Delta-zeros）：上传全零更新；
  - δ1（Delta-previous）：直接上传收到的上一轮全局模型，冒充自己训练的；
  - 伪装版（disguised）：在 δ0 上叠加与诚实更新同尺度的高斯噪声，
    或使用与诚实更新同范数的随机方向更新，以躲避基于更新范数的异常检测。
- 参考开源：Accenture Labs 联邦学习代码库
  (https://github.com/accenture/labs-federated-learning) 实现了论文的
  搭便车攻击（delta0 / delta1）与 FoolsGold 防御评估。

上传语义说明：
本框架中客户端上传的是模型差值（system.upload_difference=True，
见 ckks_fed/client.py）：upload = new_params - global_params。
在此语义下：
- δ0 上传全零差值 → 全局模型不受该客户端影响；
- δ1 把全局模型完整权重当作"差值"上传，等效于零更新（客户端没有训练，
  其持有的仍是全局模型，诚实差值本应为零；上传完整权重与上传零差值
  对聚合结果等价——均不贡献任何训练信息）；
- 伪装版上传与诚实更新同 L2 范数的随机方向差值。
"""

import math
from typing import Dict, Optional

import torch

if __package__ in (None, ""):
    # 允许以脚本方式直接运行（python hfl_attacks/system_attacks/free_riding.py）
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from hfl_attacks.base import AttackContext, SystemAttack, register_attack


def _l2_norm(update: Dict[str, torch.Tensor]) -> float:
    """更新字典的整体 L2 范数（所有张量展平拼接）"""
    total = 0.0
    for v in update.values():
        total += float(v.detach().pow(2).sum())
    return math.sqrt(total)


def _zeros_like(update: Dict[str, torch.Tensor]) -> Dict[str, torch.Tensor]:
    return {k: torch.zeros_like(v) for k, v in update.items()}


@register_attack
class DeltaZerosAttack(SystemAttack):
    """
    δ0 搭便车攻击：上传全零更新。

    客户端不参与任何本地训练，向服务器上传全零差值，使全局模型
    聚合不受其影响，同时持续获得全局模型（Fraboni et al., AISTATS 2021）。
    该攻击在 FedAvg 下既不影响收敛精度，也无法通过更新范数直接检测
    （范数恰好为零），但会暴露于要求更新非零的防御/审计机制。
    """

    name = "system:free_riding_zeros"

    def attack(self, update: Dict[str, torch.Tensor],
               context: AttackContext) -> Dict[str, torch.Tensor]:
        return _zeros_like(update)


@register_attack
class DeltaPreviousAttack(SystemAttack):
    """
    δ1 搭便车攻击：上传上一轮全局模型，冒充自己训练的结果。

    客户端不训练，直接把它在本轮开始时收到的全局模型原样上传。
    在 upload_difference 语义（上传 new_params - global_params）下：
    未训练的客户端 new_params == global_params，诚实差值为零；而把
    完整全局权重当作"差值"上传，与上传零差值等价于零更新——两者都
    不向聚合贡献任何训练信息（Fraboni et al., AISTATS 2021 证明
    δ1 与 δ0 在 FedAvg 收敛行为上等价）。
    """

    name = "system:free_riding_previous"

    def attack(self, update: Dict[str, torch.Tensor],
               context: AttackContext) -> Dict[str, torch.Tensor]:
        if context.global_params is not None:
            return {k: v.clone() for k, v in context.global_params.items()}
        # 无全局模型上下文时退化为 δ0
        return _zeros_like(update)


@register_attack
class DeltaMaskedAttack(SystemAttack):
    """
    伪装版搭便车攻击：上传按诚实更新范数缩放的随机方向更新。

    在 δ0 基础上叠加伪装：生成与诚实客户端更新 L2 范数相当的随机高斯
    方向更新，使该客户端的更新在范数统计上与诚实更新不可区分，
    从而躲避基于更新范数/方向的异常检测（Fraboni et al., AISTATS 2021,
    Section 4 "disguised free-rider"）。

    诚实更新范数的估计：
    - 有合谋上下文（context.all_updates 非空）：取非合谋者更新的
      平均 L2 范数；
    - 无合谋上下文：退而用全局模型参数范数作为近似尺度。
    """

    name = "system:free_riding_masked"

    def __init__(self, seed: Optional[int] = None):
        self.seed = seed

    def _target_norm(self, update: Dict[str, torch.Tensor],
                     context: AttackContext) -> float:
        """估计诚实更新的平均 L2 范数"""
        if context.all_updates:
            malicious = set(context.malicious_indices)
            honest = [u for i, u in enumerate(context.all_updates)
                      if i not in malicious]
            if honest:
                norms = [_l2_norm(u) for u in honest]
                return sum(norms) / len(norms)
            # 只有合谋更新时，用全部更新的平均范数
            norms = [_l2_norm(u) for u in context.all_updates]
            if norms:
                return sum(norms) / len(norms)
        # 无合谋上下文：用全局模型参数范数近似
        if context.global_params is not None:
            return _l2_norm(context.global_params)
        # 最后退路：自身更新范数（未训练时应接近零，等价 δ0）
        return _l2_norm(update)

    def attack(self, update: Dict[str, torch.Tensor],
               context: AttackContext) -> Dict[str, torch.Tensor]:
        target = self._target_norm(update, context)
        generator = (torch.Generator(device="cpu").manual_seed(self.seed)
                     if self.seed is not None else None)

        masked = {}
        for k, v in update.items():
            noise = torch.randn(v.shape, generator=generator,
                                dtype=v.dtype, device=v.device)
            masked[k] = noise

        # 把随机方向整体归一化到单位范数，再缩放到目标范数
        norm = _l2_norm(masked)
        if norm > 0:
            scale = target / norm
            masked = {k: v * scale for k, v in masked.items()}
        return masked


if __name__ == "__main__":
    torch.manual_seed(0)
    update = {"fc.weight": torch.randn(4, 3), "fc.bias": torch.randn(4)}
    global_params = {"fc.weight": torch.randn(4, 3), "fc.bias": torch.randn(4)}
    all_updates = [
        {k: torch.randn_like(v) for k, v in update.items()} for _ in range(4)
    ]
    ctx = AttackContext(global_params=global_params, self_update=update,
                        all_updates=all_updates, self_index=4,
                        malicious_indices=[4])

    for cls in (DeltaZerosAttack, DeltaPreviousAttack, DeltaMaskedAttack):
        atk = cls() if cls is not DeltaMaskedAttack else cls(seed=42)
        out = atk.attack(update, ctx)
        norm = _l2_norm(out)
        honest_avg = sum(_l2_norm(u) for u in all_updates) / len(all_updates)
        print(f"{atk.name:32s} participates=True  L2={norm:.4f} "
              f"(honest_avg={honest_avg:.4f})")
        if cls is DeltaZerosAttack:
            assert norm == 0.0
        if cls is DeltaPreviousAttack:
            assert all(torch.equal(out[k], global_params[k]) for k in out)
        if cls is DeltaMaskedAttack:
            assert abs(norm - honest_avg) / honest_avg < 0.2
    print("free_riding self-test OK")
