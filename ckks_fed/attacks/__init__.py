"""
联邦学习攻击模块
提供数据层面和模型层面的多种攻击实现
"""

from .data_attacks import (
    LabelFlippingAttack,
    BackdoorDataAttack,
    get_poisoned_loader
)

from .model_attacks import (
    SignFlippingAttack,
    ScalingAttack,
    GaussianNoiseAttack,
    BackdoorModelAttack,
    AttackBase
)

__all__ = [
    # 数据攻击
    "LabelFlippingAttack",
    "BackdoorDataAttack",
    "get_poisoned_loader",
    # 模型攻击
    "AttackBase",
    "SignFlippingAttack",
    "ScalingAttack",
    "GaussianNoiseAttack",
    "BackdoorModelAttack",
]
