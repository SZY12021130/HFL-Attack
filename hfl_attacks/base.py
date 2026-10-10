"""
统一攻击接口定义
================

所有攻击实现以下四类接口之一，并通过 register_attack 注册到 ATTACK_REGISTRY，
供评估 runner 按名称实例化：

- ClientUpdateAttack  模型投毒 / 系统攻击（搭便车、女巫）—— 篡改客户端上传的更新
- DataPoisoner        数据投毒 —— 污染客户端本地训练数据（DataLoader）
- PrivacyAttack       隐私推断 —— 从公开信息（更新/梯度/模型）推断受害者隐私
- SystemAttack        系统攻击（ClientUpdateAttack 子类）—— 额外可控制客户端是否参与本轮

设计原则：
1. 纯 torch/numpy 实现，不引入额外依赖
2. 每个攻击可在 MNIST/LeNet 规模上几分钟内完成评估
3. 与 ckks_fed 的加密流程解耦：攻击作用在明文更新/数据上，
   加密传输发生在攻击之后（即攻击者对客户端被攻破或客户端本身是恶意的场景）
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import torch

# 全局注册表: {攻击名: 攻击类}
ATTACK_REGISTRY: Dict[str, type] = {}


def register_attack(cls):
    """类装饰器：将攻击类注册到 ATTACK_REGISTRY"""
    ATTACK_REGISTRY[cls.name] = cls
    return cls


@dataclass
class AttackContext:
    """
    攻击上下文：一次攻击调用时可见的信息。

    模拟不同能力等级的攻击者：
    - 仅知道自己更新的普通恶意客户端：global_params + self_update
    - 合谋攻击（ALIE / Min-Max / Min-Sum / 女巫）：all_updates + malicious_indices
    """
    global_params: Optional[Dict[str, torch.Tensor]] = None   # 当前全局模型（明文）
    self_update: Optional[Dict[str, torch.Tensor]] = None     # 自己的本地更新
    all_updates: Optional[List[Dict[str, torch.Tensor]]] = None  # 本轮全部更新（合谋者可见）
    self_index: int = 0                                        # 自己在 all_updates 中的下标
    malicious_indices: List[int] = field(default_factory=list)  # 合谋恶意客户端下标
    num_classes: int = 10
    round_num: int = 0
    extra: Dict[str, Any] = field(default_factory=dict)


class ClientUpdateAttack(ABC):
    """客户端更新攻击接口（模型投毒 / 系统攻击中的伪造更新）"""

    name: str = "base"
    category: str = "model_poisoning"

    @abstractmethod
    def attack(self, update: Dict[str, torch.Tensor],
               context: AttackContext) -> Dict[str, torch.Tensor]:
        """接收客户端本地更新，返回（可能被篡改的）上传更新"""
        raise NotImplementedError

    # ---- 兼容 ckks_fed MaliciousClient 的旧接口（attack(update, global_params)）----
    def legacy_attack(self, model_update, global_params=None):
        ctx = AttackContext(global_params=global_params, self_update=model_update)
        return self.attack(model_update, ctx)

    @property
    def attack_name(self) -> str:
        """兼容 MaliciousClient 读取的 attack_name 属性"""
        return self.name


class DataPoisoner(ABC):
    """数据投毒接口：污染客户端本地训练数据"""

    name: str = "base"
    category: str = "data_poisoning"

    @abstractmethod
    def poison_loader(self, loader, num_classes: int = 10):
        """接收一个 DataLoader，返回毒化后的 DataLoader"""
        raise NotImplementedError


class PrivacyAttack(ABC):
    """隐私推断攻击接口"""

    name: str = "base"
    category: str = "privacy_inference"

    @abstractmethod
    def run(self, model: torch.nn.Module, device: str = "cpu", **kwargs) -> Dict[str, Any]:
        """
        执行攻击，返回指标字典（键名与 hfl_attacks.metrics 中的定义一致）。

        kwargs 由具体攻击定义，通常包括：
            victim_gradient / victim_update: 观察到的受害者梯度/更新
            ground_truth: 受害者的原始数据（用于评估重建质量）
            dataset / loader: 辅助数据（成员推断的影子数据等）
        """
        raise NotImplementedError


class SystemAttack(ClientUpdateAttack):
    """系统攻击接口：在更新攻击之外，还可控制客户端的参与行为"""

    category: str = "system_attacks"

    def participates(self, round_num: int) -> bool:
        """本轮是否参与训练（DoS/掉线类攻击返回 False）"""
        return True
