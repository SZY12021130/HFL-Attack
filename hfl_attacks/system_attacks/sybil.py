"""
系统攻击：女巫合谋攻击（Sybil Collusion）
==========================================

论文出处：
- Douceur, "The Sybil Attack", IPTPS 2002 —— 经典女巫攻击定义：
  单一恶意实体伪造多个身份（Sybil），破坏基于冗余的协议
  （FL 中即破坏多客户端聚合的鲁棒性假设）。
- arXiv 2505.09983, "Sybil-based Virtual Data Poisoning Attacks in
  Federated Learning" —— 将女巫身份注入 FL 聚合以放大投毒投票权重的
  近期形式化。
- 参考开源：
  - https://github.com/ITSEG-MQ/Sybil-Attacks-and-Defense-on-DP-FL
    （DP-FL 场景下的女巫攻击与防御实现）
  - https://github.com/brandon-mosqueda/sydelp
    （Sybil 投毒攻击实现，含多种恶意更新生成策略）

攻击模型：
攻击者控制 k 个伪客户端（context.malicious_indices 中的客户端）。
各 Sybil 上传彼此一致的恶意更新：本实现让每个合谋者上传"所有合谋者
更新的均值"。这样：
- 合谋者对聚合结果的贡献 = k/n * mean(colluding)，相当于把单一恶意
  更新复制 k 份，等效放大了恶意方向的投票权重（k 个 Sybil 的均值的
  均值仍等于恶意均值）；
- 每个 Sybil 的单独更新是合谋更新的均值而非极端更新，在基于更新
  范数/距离的防御（如 Krum、中位数）下不如单一极端更新显眼。

可选 boost_factor：若给定，则将合谋均值相对诚实均值的偏差按
boost_factor 放大（honest_mean + boost_factor * (colluding_mean -
honest_mean)），用于更强的定向攻击；None 表示仅依赖多身份本身
复制均分权重的放大效果。
"""

from typing import Dict, List, Optional

import torch

if __package__ in (None, ""):
    # 允许以脚本方式直接运行（python hfl_attacks/system_attacks/sybil.py）
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from hfl_attacks.base import AttackContext, SystemAttack, register_attack


def _mean_updates(updates: List[Dict[str, torch.Tensor]]) -> Dict[str, torch.Tensor]:
    """更新字典列表的逐元素均值"""
    result = {}
    for k in updates[0]:
        result[k] = torch.stack([u[k].float() for u in updates]).mean(dim=0)
    return result


@register_attack
class SybilCollusionAttack(SystemAttack):
    """
    女巫合谋攻击：多个受控伪客户端上传一致的（合谋均值）恶意更新。

    算法：attack() 中读取 context.all_updates 与 context.malicious_indices，
    计算所有合谋者更新的均值，并将其作为每个合谋者的上传更新（每个
    合谋者被分别调用 attack()，返回相同结果，从而上传彼此一致的更新）。

    - boost_factor=None（默认）：仅复制恶意更新，依靠多身份的均分权重
      放大聚合中的恶意投票份额；
    - boost_factor=γ>1：honest_mean + γ·(合谋均值 − honest_mean)，
      将合谋方向相对诚实方向的偏差额外放大 γ 倍。

    无合谋上下文（all_updates 或 malicious_indices 为空）时退化为：
    boost_factor 给定时上传自身更新的 γ 倍，否则原样上传自身更新。
    """

    name = "system:sybil_collusion"

    def __init__(self, boost_factor: Optional[float] = None):
        self.boost_factor = boost_factor

    def attack(self, update: Dict[str, torch.Tensor],
               context: AttackContext) -> Dict[str, torch.Tensor]:
        colluding = None
        if context.all_updates and context.malicious_indices:
            colluding = [context.all_updates[i]
                         for i in context.malicious_indices
                         if 0 <= i < len(context.all_updates)]

        if not colluding:
            # 无合谋上下文：只能放大自身更新
            if self.boost_factor is not None:
                return {k: v * self.boost_factor for k, v in update.items()}
            return update

        colluding_mean = _mean_updates(colluding)

        if self.boost_factor is None:
            return colluding_mean

        # boost_factor：将合谋均值相对诚实均值的偏差放大
        honest = [u for i, u in enumerate(context.all_updates)
                  if i not in set(context.malicious_indices)]
        if not honest:
            return {k: v * self.boost_factor for k, v in colluding_mean.items()}
        honest_mean = _mean_updates(honest)
        return {k: honest_mean[k] + self.boost_factor * (colluding_mean[k] - honest_mean[k])
                for k in colluding_mean}


if __name__ == "__main__":
    torch.manual_seed(0)
    shape = {"fc.weight": (4, 3), "fc.bias": (4,)}

    def rand_update():
        return {k: torch.randn(*s) for k, s in shape.items()}

    honest = [rand_update() for _ in range(4)]
    sybil_raw = [rand_update() for _ in range(2)]
    all_updates = honest + sybil_raw

    # 场景 1：默认 boost_factor=None —— 每个合谋者上传合谋均值
    atk = SybilCollusionAttack()
    ctx = AttackContext(all_updates=all_updates, self_update=sybil_raw[0],
                        self_index=4, malicious_indices=[4, 5])
    out_a = atk.attack(sybil_raw[0], ctx)
    out_b = atk.attack(sybil_raw[1], ctx)
    mean_expected = _mean_updates(sybil_raw)
    assert all(torch.allclose(out_a[k], mean_expected[k]) for k in out_a)
    assert all(torch.allclose(out_a[k], out_b[k]) for k in out_a)
    print("sybil_collusion (boost_factor=None): 两个 Sybil 上传一致 = 合谋均值")

    # 场景 2：boost_factor=2.0 —— 偏差放大
    atk2 = SybilCollusionAttack(boost_factor=2.0)
    out2 = atk2.attack(sybil_raw[0], ctx)
    honest_mean = _mean_updates(honest)
    for k in out2:
        expected = honest_mean[k] + 2.0 * (mean_expected[k] - honest_mean[k])
        assert torch.allclose(out2[k], expected)
    print("sybil_collusion (boost_factor=2.0): 合谋偏差已相对诚实均值放大 2 倍")

    # 场景 3：无合谋上下文 —— 退化行为
    atk3 = SybilCollusionAttack(boost_factor=3.0)
    solo = rand_update()
    out3 = atk3.attack(solo, AttackContext(self_update=solo))
    assert all(torch.allclose(out3[k], solo[k] * 3.0) for k in out3)
    print("sybil_collusion (无合谋上下文): 退化为自身更新 × boost_factor")
    print("sybil self-test OK")
