"""
data_poisoning subpackage

import 全部模块以触发 @register_attack 注册。
"""

from hfl_attacks.data_poisoning import (
    backdoor_trigger,
    blend_trigger,
    edge_case,
    label_flipping,
    targeted_label_flipping,
)

__all__ = [
    "backdoor_trigger",
    "blend_trigger",
    "edge_case",
    "label_flipping",
    "targeted_label_flipping",
]
