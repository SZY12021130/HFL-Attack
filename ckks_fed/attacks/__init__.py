"""
模型投毒攻击实现
为 hfl_attacks/model_poisoning/classic_attacks.py 提供后端攻击类
"""

from .model_attacks import (
    SignFlippingAttack,
    ScalingAttack,
    GaussianNoiseAttack,
    BackdoorModelAttack,
    AttackBase,
)

__all__ = [
    "AttackBase",
    "SignFlippingAttack",
    "ScalingAttack",
    "GaussianNoiseAttack",
    "BackdoorModelAttack",
]
