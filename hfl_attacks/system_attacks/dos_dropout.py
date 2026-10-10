"""
系统攻击：拒绝服务 / 掉线攻击（DoS / Dropout）
===============================================

论文出处：
- Tolpegin, Truex, Gursoy, Liu, "Attacks against Federated Learning Defense
  Systems and their Mitigation", JMLR 2023 —— 对 FL 防御系统的攻击分类中
  包含网络层/系统层攻击：恶意客户端通过掉线、拖延或拒绝上传更新
  破坏训练过程与防御机制的有效性。
- Fereidooni et al., "FreqFed: Frequency-Based Federated Learning
  against Gradual Poisoning", NDSS 2024 —— 针对掉线/慢客户端的建模：
  客户端可用性随时间变化（间歇性参与）是真实 FL 系统的常态，
  也是攻击者可利用的通道（选择性参与以逃避基于历史行为的检测）。

攻击模型：
两类攻击均通过 SystemAttack.participates(round_num) 控制客户端的
参与决策：participates() 返回 False 的轮次，runner 跳过该客户端的
本地训练与更新上传（拒绝贡献算力/数据），从而降低全局模型收敛速度
或破坏对参与率敏感的防御（如 FoolsGold、历史信誉机制）。
attack() 在本类攻击中仅作为占位实现（返回全零更新），正常流程不会
调用到（participates()=False 的客户端不上传任何更新）。
"""

from typing import Dict, Optional

import numpy as np
import torch

if __package__ in (None, ""):
    # 允许以脚本方式直接运行（python hfl_attacks/system_attacks/dos_dropout.py）
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from hfl_attacks.base import AttackContext, SystemAttack, register_attack


@register_attack
class RandomDropoutAttack(SystemAttack):
    """
    随机掉线攻击：每轮以固定概率 drop_rate 拒绝参与。

    participates(round_num) 以概率 drop_rate 返回 False（该轮不上传
    任何更新），模拟不稳定/恶意客户端的随机拒绝服务。
    """

    name = "system:random_dropout"

    def __init__(self, drop_rate: float = 0.5, seed: Optional[int] = None):
        if not 0.0 <= drop_rate <= 1.0:
            raise ValueError(f"drop_rate 必须在 [0,1] 内，得到 {drop_rate}")
        self.drop_rate = drop_rate
        self._rng = np.random.RandomState(seed)

    def participates(self, round_num: int) -> bool:
        return bool(self._rng.rand() >= self.drop_rate)

    def attack(self, update: Dict[str, torch.Tensor],
               context: AttackContext) -> Dict[str, torch.Tensor]:
        # 占位：participates()=False 的客户端不会上传更新
        return {k: torch.zeros_like(v) for k, v in update.items()}


@register_attack
class PersistentDoSAttack(SystemAttack):
    """
    持续低参与 DoS 攻击：每 active_every 轮才参与一次。

    与随机掉线不同，该攻击是确定性的低频参与（round_num 能被
    active_every 整除的轮次才返回 True），用于模拟长期"半可用"
    客户端，最大化对收敛速度/参与率敏感防御的破坏。
    """

    name = "system:persistent_dos"

    def __init__(self, active_every: int = 3):
        if active_every < 1:
            raise ValueError(f"active_every 必须 >= 1，得到 {active_every}")
        self.active_every = active_every

    def participates(self, round_num: int) -> bool:
        return (round_num % self.active_every) == 0

    def attack(self, update: Dict[str, torch.Tensor],
               context: AttackContext) -> Dict[str, torch.Tensor]:
        # 占位：participates()=False 的客户端不会上传更新
        return {k: torch.zeros_like(v) for k, v in update.items()}


if __name__ == "__main__":
    torch.manual_seed(0)
    update = {"fc.weight": torch.randn(4, 3), "fc.bias": torch.randn(4)}

    # RandomDropoutAttack：统计多轮参与率
    atk = RandomDropoutAttack(drop_rate=0.5, seed=42)
    rounds = list(range(200))
    joined = [atk.participates(r) for r in rounds]
    rate = sum(joined) / len(joined)
    print(f"random_dropout drop_rate=0.5 -> 实际参与率 {rate:.3f}（期望约 0.5）")
    assert 0.35 < rate < 0.65

    # attack() 占位返回全零
    out = atk.attack(update, AttackContext(self_update=update))
    assert all(float(v.abs().sum()) == 0.0 for v in out.values())
    print("random_dropout attack() 返回全零占位更新")

    # PersistentDoSAttack：每 3 轮参与一次
    atk2 = PersistentDoSAttack(active_every=3)
    pattern = [atk2.participates(r) for r in range(9)]
    print(f"persistent_dos active_every=3 -> 参与模式 {pattern}")
    assert pattern == [True, False, False, True, False, False, True, False, False]

    out2 = atk2.attack(update, AttackContext(self_update=update))
    assert all(float(v.abs().sum()) == 0.0 for v in out2.values())
    print("persistent_dos attack() 返回全零占位更新")
    print("dos_dropout self-test OK")
