"""
privacy_inference subpackage
隐私推断攻击：从公开信息（梯度/更新/模型输出）推断受害者私有数据
"""
from hfl_attacks.privacy_inference import (  # noqa: F401
    dlg,
    idlg,
    inverting_gradients,
    membership_inference,
)
from hfl_attacks.privacy_inference.dlg import DLGAttack
from hfl_attacks.privacy_inference.idlg import IDLGAttack
from hfl_attacks.privacy_inference.inverting_gradients import (
    InvertingGradientsAttack,
)
from hfl_attacks.privacy_inference.membership_inference import (
    MembershipInferenceAttack,
)

__all__ = [
    "DLGAttack",
    "IDLGAttack",
    "InvertingGradientsAttack",
    "MembershipInferenceAttack",
]
