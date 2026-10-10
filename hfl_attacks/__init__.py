"""
hfl_attacks: 联邦学习攻击代码库
================================

四类攻击，每类至少 3 种实现，统一接口，可在 HFL-Attack（CKKS + FedAvg）上评估：

1. data_poisoning    数据投毒   —— 污染客户端本地训练数据
2. model_poisoning   模型投毒   —— 篡改客户端上传的模型更新
3. privacy_inference 隐私推断   —— 从共享更新/模型推断受害者的私有数据信息
4. system_attacks    系统攻击   —— 利用协议/系统弱点（搭便车、女巫、拒绝服务）

每个攻击模块的 docstring 均给出论文出处与参考开源实现。
"""

from hfl_attacks.base import (
    AttackContext,
    ClientUpdateAttack,
    DataPoisoner,
    PrivacyAttack,
    SystemAttack,
    ATTACK_REGISTRY,
    register_attack,
)

__all__ = [
    'AttackContext', 'ClientUpdateAttack', 'DataPoisoner', 'PrivacyAttack',
    'SystemAttack', 'ATTACK_REGISTRY', 'register_attack',
]


def _register_builtin():
    """导入全部内置攻击并注册到 ATTACK_REGISTRY"""
    from hfl_attacks import data_poisoning, model_poisoning, privacy_inference, system_attacks  # noqa: F401


_register_builtin()
