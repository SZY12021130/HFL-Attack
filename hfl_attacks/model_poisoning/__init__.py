"""
model_poisoning subpackage

模块：
- classic_attacks.py       经典攻击适配层（sign_flipping / scaling /
                           gaussian_noise / backdoor_model / constant / mimic_alie）
- alie.py                  ALIE 合谋攻击（Baruch et al., NeurIPS 2019）
- optimization_attacks.py  Min-Max / Min-Sum 距离规避攻击
                           （Shejwalkar & Houmansadr, USENIX Security 2021）
- ipm.py                   IPM 内积操纵合谋攻击（Xie et al., USENIX Security 2021；
                           PPBR 论文评测攻击之一）
"""

from hfl_attacks.model_poisoning import (  # noqa: F401
    classic_attacks,
    alie,
    optimization_attacks,
    ipm,
)

__all__ = ["classic_attacks", "alie", "optimization_attacks", "ipm"]
