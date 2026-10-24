"""
经典模型投毒攻击（新接口适配层）
================================

对 hfl_attacks/common.py（原 ckks_fed/attacks/model_attacks.py）中已有的攻击类做组合式适配：
包装类内部实例化旧类，`attack(update, context)` 中调用旧类的
`attack(update, context.global_params)`。不修改旧文件。

论文出处：
- Sign-flipping：Lamport et al., "The Byzantine Generals Problem"（拜占庭攻击的经典形式）
- Scaling / Model Replacement：Bhagoji et al., "Analyzing Federated Learning
  through an Adversarial Lens", ICML 2019
- Gaussian Noise：非定向拜占庭噪声攻击（Blanchard et al., "Machine Learning
  with Adversaries: Byzantine Tolerant Gradient Descent", NeurIPS 2017 的评测基线之一）
- Backdoor Model：Bagdasaryan et al., "How To Backdoor Federated Learning",
  AISTATS 2020（数据后门 + 模型缩放组合）
- Constant：非定向拜占庭基线
- Mimic / ALIE 近似版：Baruch et al., "A Little Is Enough: Circumventing
  Defenses For Distributed Learning", NeurIPS 2019

参考开源：FLPoison 基准 (https://github.com/vio1etus/FLPoison)
"""

import os
import sys

import torch

# 允许直接运行本文件（python hfl_attacks/model_poisoning/xxx.py）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from hfl_attacks.base import ClientUpdateAttack, register_attack
from hfl_attacks.common import (
    SignFlippingAttack,
    ScalingAttack,
    GaussianNoiseAttack,
    BackdoorModelAttack,
    MimicAttack,
    ConstantAttack,
)


@register_attack
class SignFlipping(ClientUpdateAttack):
    """符号翻转攻击：将更新符号全部/部分翻转（参数透传 flip_rate / scale）"""

    name = "model:sign_flipping"

    def __init__(self, flip_rate: float = 1.0, scale: float = 1.0):
        self._attack = SignFlippingAttack(flip_rate=flip_rate, scale=scale)

    def attack(self, update, context):
        return self._attack.attack(update, context.global_params)


@register_attack
class Scaling(ClientUpdateAttack):
    """缩放攻击：恶意更新乘以极大系数以支配聚合（参数透传 scale_factor）"""

    name = "model:scaling"

    def __init__(self, scale_factor: float = 10.0):
        self._attack = ScalingAttack(scale_factor=scale_factor)

    def attack(self, update, context):
        return self._attack.attack(update, context.global_params)


@register_attack
class GaussianNoise(ClientUpdateAttack):
    """高斯噪声攻击：以高斯噪声作为更新（PPBR 论文 §VIII 攻击 3）。

    两种标定方式（via config attack.params）：
    - absolute（默认保持旧行为）：update ← N(mean, std²)，std 绝对值。
      注意：std 取 1.0 而良性 Δ 每维幅度 ~1e-3 时噪声范数大良性更新 ~100 倍，
      任何防御都不可能守住，不属于论文评测语义，仅用于压测。
    - relative（论文语义）：以本轮良性客户端更新的逐坐标标准差为尺度，
      update ← N(0, (rel_std·σ_benign)²)。σ_benign 由 context 中良性更新
      实时估计（σ_benign,i = std_honest[coord_i]），与 CenteredClipping
      （Karimireddy et al. 2021）等文献的高斯攻击标定一致——恶意更新
      在幅值统计上与良性不可区分，才能检验防御的真实鲁棒性。
    """

    name = "model:gaussian_noise"

    def __init__(self, std: float = 1.0, mean: float = 0.0,
                 mode: str = "absolute", rel_std: float = 1.0):
        self.std = std
        self.mean = mean
        self.mode = mode
        self.rel_std = rel_std

    def attack(self, update, context):
        if self.mode == "relative":
            honest = [u for j, u in enumerate(context.all_updates or [])
                      if j not in (context.malicious_indices or [])]
            if honest:
                out = {}
                for k, v in update.items():
                    sigma = torch.stack([u[k].float() for u in honest]).std(dim=0)
                    out[k] = torch.randn_like(v, dtype=torch.float32) \
                        * (self.rel_std * sigma)
                return out
        # absolute 回退（兼容旧行为）
        import numpy as np
        return {k: v + torch.from_numpy(
            np.random.normal(self.mean, self.std, v.shape)).to(v.device, v.dtype)
            for k, v in update.items()}


@register_attack
class BackdoorModel(ClientUpdateAttack):
    """后门模型攻击：放大后门训练得到的更新（参数透传 scale_factor）"""

    name = "model:backdoor_model"

    def __init__(self, scale_factor: float = 10.0):
        self._attack = BackdoorModelAttack(scale_factor=scale_factor)

    def attack(self, update, context):
        return self._attack.attack(update, context.global_params)


@register_attack
class Constant(ClientUpdateAttack):
    """常数攻击：上传固定常数更新（参数透传 constant_value）"""

    name = "model:constant"

    def __init__(self, constant_value: float = 0.0):
        self._attack = ConstantAttack(constant_value=constant_value)

    def attack(self, update, context):
        return self._attack.attack(update, context.global_params)


@register_attack
class MimicALIE(ClientUpdateAttack):
    """模仿攻击 / ALIE 单客户端近似版（ckks_fed 已实现的简化版本）"""

    name = "model:mimic_alie"

    def __init__(self, z: float = 1.5):
        self._attack = MimicAttack(z=z)

    def attack(self, update, context):
        return self._attack.attack(update, context.global_params)


if __name__ == "__main__":
    from hfl_attacks.base import AttackContext

    torch.manual_seed(0)
    update = {"w": torch.randn(4, 5), "b": torch.randn(5)}
    global_params = {"w": torch.zeros(4, 5), "b": torch.zeros(5)}
    ctx = AttackContext(global_params=global_params, self_update=update)

    for cls in (SignFlipping, Scaling, GaussianNoise,
                BackdoorModel, Constant, MimicALIE):
        atk = cls()
        out = atk.attack(update, ctx)
        diff = sum(torch.norm(out[k] - update[k]).item() for k in update)
        print(f"{atk.name:>24}: poisoned | delta from clean = {diff:.4f}")
    print("classic_attacks self-test passed")
