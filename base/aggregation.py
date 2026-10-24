"""
明文鲁棒聚合规则注册表（base 通用层）
========================================

RuleSpec 与 AGGREGATION_REGISTRY 的**定义**所在地（依赖倒置的解环点）：
- `baseline/` 包（明文鲁棒聚合规则库）在导入时向本注册表登记各规则；
- 方案包（如 ckks 的 AggregationServer）只从本模块查表，**不 import
  baseline**，从而保持 base ← baseline ← 方案 的单向依赖
  （历史上 server.py 直接 import baseline 与 baseline.rflba 回引
  ckks.defenses 构成环，见 git 历史）。

dp_brem 为有状态规则（跨轮动量），通过 RuleSpec.stateful 标记，
由服务器在聚合时自动注入 state dict。
"""

from typing import Any, Callable, Dict, NamedTuple


class RuleSpec(NamedTuple):
    """聚合规则注册项

    fn:             aggregate(updates, weights=None, **params) 函数
    default_params: 默认超参数（可被 defense.params 覆盖）
    citation:       论文出处
    description:    一句话描述
    stateful:       是否需要跨轮状态（服务器自动注入 state dict）
    """
    fn: Callable
    default_params: Dict[str, Any]
    citation: str
    description: str
    stateful: bool = False


AGGREGATION_REGISTRY: Dict[str, RuleSpec] = {}


def register_rule(name: str, spec: RuleSpec) -> None:
    """向全局注册表登记一条聚合规则（baseline 包导入时调用）。"""
    if name in AGGREGATION_REGISTRY:
        raise ValueError(f"聚合规则 '{name}' 已注册")
    AGGREGATION_REGISTRY[name] = spec


__all__ = ["RuleSpec", "AGGREGATION_REGISTRY", "register_rule"]
