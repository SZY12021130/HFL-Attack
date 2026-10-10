"""
模型层面攻击 (Model-level Attacks)
在客户端本地训练完成后，对模型更新进行篡改再上传
"""

import torch
from typing import Dict
import copy


class AttackBase:
    """模型攻击基类"""
    
    def __init__(self, attack_name: str = "base"):
        self.attack_name = attack_name
    
    def attack(self, model_update: Dict[str, torch.Tensor],
               global_params: Dict[str, torch.Tensor] = None) -> Dict[str, torch.Tensor]:
        """
        对模型更新执行攻击
        
        Args:
            model_update: 原始模型更新（差值或完整权重）
            global_params: 当前全局模型参数（部分攻击需要）
        
        Returns:
            被篡改的模型更新
        """
        raise NotImplementedError


class SignFlippingAttack(AttackBase):
    """
    符号翻转攻击 (Sign-flipping Attack)
    
    将模型更新的符号全部或部分翻转，使聚合后的全局模型向相反方向更新。
    这是最简单的拜占庭攻击之一。
    """
    
    def __init__(self, flip_rate: float = 1.0, scale: float = 1.0):
        """
        Args:
            flip_rate: 翻转比例 (1.0 = 全部翻转)
            scale: 翻转后的缩放系数
        """
        super().__init__("sign_flipping")
        self.flip_rate = flip_rate
        self.scale = scale
    
    def attack(self, model_update, global_params=None):
        poisoned = {}
        for name, tensor in model_update.items():
            if torch.rand(1).item() < self.flip_rate:
                poisoned[name] = -tensor * self.scale
            else:
                poisoned[name] = tensor
        return poisoned


class ScalingAttack(AttackBase):
    """
    缩放攻击 (Scaling Attack / Model Poisoning)
    
    恶意客户端将其模型更新乘以一个极大的系数，
    使得在加权平均时，恶意更新的影响力远超其应有权重。
    常与数据投毒（后门）结合使用，称为 "PGD attack" 或 "model replacement"。
    """
    
    def __init__(self, scale_factor: float = 10.0):
        """
        Args:
            scale_factor: 缩放系数（通常 > 1，越大攻击越强）
        """
        super().__init__("scaling")
        self.scale_factor = scale_factor
    
    def attack(self, model_update, global_params=None):
        poisoned = {}
        for name, tensor in model_update.items():
            poisoned[name] = tensor * self.scale_factor
        return poisoned


class GaussianNoiseAttack(AttackBase):
    """
    高斯噪声攻击 (Gaussian Noise Attack)
    
    在模型更新中添加大量高斯噪声，破坏全局模型的收敛。
    属于非定向的拜占庭攻击。
    """
    
    def __init__(self, std: float = 1.0, mean: float = 0.0):
        """
        Args:
            std: 噪声标准差
            mean: 噪声均值
        """
        super().__init__("gaussian_noise")
        self.std = std
        self.mean = mean
    
    def attack(self, model_update, global_params=None):
        poisoned = {}
        for name, tensor in model_update.items():
            noise = torch.randn_like(tensor) * self.std + self.mean
            poisoned[name] = tensor + noise
        return poisoned


class BackdoorModelAttack(AttackBase):
    """
    后门模型攻击 (Backdoor Model Attack)
    
    恶意客户端在本地训练一个带有后门的模型，
    然后上传被放大后的后门更新，使全局模型也带有后门。
    
    这是数据后门攻击 + 缩放攻击的组合。
    """
    
    def __init__(self, scale_factor: float = 10.0):
        super().__init__("backdoor_model")
        self.scale_factor = scale_factor
    
    def attack(self, model_update, global_params=None):
        # 后门模型攻击本质上是：恶意客户端先用后门数据训练，
        # 然后对得到的更新进行放大
        poisoned = {}
        for name, tensor in model_update.items():
            poisoned[name] = tensor * self.scale_factor
        return poisoned


class MimicAttack(AttackBase):
    """
    模仿攻击 / "A Little Is Enough" (ALIE)
    
    恶意客户端不发送完全随机的更新，而是发送一个经过精心计算的、
    接近正常更新但略有偏差的向量。这种攻击很难被基于统计的防御检测到。
    
    实现方式：计算正常更新的均值和方差，然后沿某个方向偏移 z 倍标准差。
    """
    
    def __init__(self, z: float = 1.5):
        """
        Args:
            z: 偏移的标准差倍数
        """
        super().__init__("mimic_alie")
        self.z = z
    
    def attack(self, model_update, global_params=None):
        # ALIE 攻击需要在多客户端之间协调，单客户端实现简化版：
        # 对自身更新进行缩放和微小偏移
        poisoned = {}
        for name, tensor in model_update.items():
            std = torch.std(tensor)
            mean = torch.mean(tensor)
            # 向正方向偏移 z * std
            poisoned[name] = tensor + self.z * std
        return poisoned


class ConstantAttack(AttackBase):
    """
    常数攻击 (Constant Attack)
    
    恶意客户端发送一个固定常数（如全0、全1）的模型更新，
    试图拉低全局模型的性能。
    """
    
    def __init__(self, constant_value: float = 0.0):
        super().__init__("constant")
        self.constant_value = constant_value
    
    def attack(self, model_update, global_params=None):
        poisoned = {}
        for name, tensor in model_update.items():
            poisoned[name] = torch.full_like(tensor, self.constant_value)
        return poisoned
