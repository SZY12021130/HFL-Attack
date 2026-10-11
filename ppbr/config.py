"""
PPBR 配置（对应 config.yaml 的 ppbr 段）
=========================================
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class PPBRConfig:
    """PPBR 超参数（论文默认值见 paperlist/ppbr_notes.md §5）"""

    enabled: bool = False
    # CLDP（式 2）：隐私预算 ε_χ 与距离阈值 L, L'（论文默认 L=6, L'=7）
    epsilon_chi: float = 3.0
    L: float = 6.0
    L_prime: float = 7.0
    # 符号统计：云端每轮广播的坐标子集大小 ι
    num_coords: int = 100
    # trimmed k-means：簇数 k、取前 b 个大簇、每轮迭代剔除比例（论文 k=2, b=1）
    k: int = 2
    b: int = 1
    trim_ratio: float = 0.3
    # 动量：客户端 φ，全局步长 ζ。m = φ·m + (1−φ)·Δ 使首轮更新缩小 (1−φ) 倍，
    # ζ = 1/(1−φ) 补偿该缩放，保证无攻击时与 FedAvg 基线精度一致（论文 ζ=0.0125
    # 对应其"完整本地模型"语义；本项目上传差值，故按差值语义换算）
    momentum_phi: float = 0.9
    global_zeta: float = 10.0
    # 云边缘范数过滤阈值 θ（None = 仅按数量 l' 过滤，不做范数带约束）
    theta: Optional[float] = None
    # 拜占庭比例：beta_known=true 时 l'=(1−β)l；否则 l'=0.5l（论文关键性质：
    # 无需事先知道拜占庭比例）
    beta_known: bool = False
    beta: float = 0.3

    @classmethod
    def from_dict(cls, d: dict) -> 'PPBRConfig':
        d = d or {}
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore
        return cls(**{k: v for k, v in d.items() if k in known})
