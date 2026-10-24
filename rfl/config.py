"""
RFL 配置（对应 config.yaml 的 rfl 段）
=========================================

    论文：Zhou et al., "Robust Privacy-Preserving Federated Learning for Edge
    Computing With New Client Integration", IEEE TDSC 2026（见 paperlist/Robust/）。
"""

from dataclasses import dataclass


@dataclass
class RFLConfig:
    """RFL 超参数（论文 §V 默认值）"""

    enabled: bool = False
    # ---- 轻量差分隐私（式 7）：p̂_u = p_u + δ_u，δ_u ~ N(0, σ_u²) ----
    # σ_u = c·d·S2/ε，c = √(2ln(1.25/δ))，S2 = 2C/|D_u|（灵敏度，式 7 下方）
    epsilon: float = 5.0             # 隐私预算 ε
    delta_dp: float = 1e-5           # DP 失败概率 δ
    clip_threshold: float = 1.0      # 裁剪阈值 C（决定灵敏度 S2 = 2C/|D_u|）
    client_dp_noise: bool = True     # false=关闭客户端 DP 噪声（对照实验）
    # ---- 模型切分：切给 EC 的浅层隐藏层数 Ls（论文默认 Ls=1）----
    # 浅层输出维度恒定 → EC 计算/通信开销与全局模型维度无关（Theorem 4）
    num_shallow_layers: int = 1
    # ---- 同态哈希 HH（式 13/17）：素数阶乘法子群，H(x)=Π g_i^{x_i} mod p ----
    hash_bits: int = 256             # 群规模位宽（安全素数 p=2q+1，p 为 hash_bits 位）
    hash_scale: int = 10000          # 浮点梯度 → 定点整数量化缩放（验证在整数域精确成立）
    # ---- 训练 ----
    local_epochs: int = 1            # 每轮客户端本地 split learning 迭代轮数
    global_zeta: float = 0.1         # 全局步长：w ← w − ζ·g（g 为聚合梯度）
    # ---- 恶意云仿真（可验证性演示，论文威胁模型 §IV-B）----
    # none=诚实聚合；drop_edge=云丢弃某边缘更新；scale_edge=云缩放某边缘更新。
    # 两种篡改均使式(17) 同态哈希校验失败 → 全体 ES 中止本轮
    cs_tamper: str = "none"
    cs_tamper_scale: float = 1.5     # scale_edge 模式的缩放因子
    # ---- IFGIA 仿真（论文 §III）：云伪造虚拟客户端注入分组 ----
    # 伪造客户端无合法注册身份（pk 未在 ES 注册）→ ES 验签（式 9）拒绝
    simulate_ifgia: bool = False
    ifgia_per_edge: int = 1         # 每个边缘注入的伪造客户端数

    @classmethod
    def from_dict(cls, d: dict) -> 'RFLConfig':
        d = d or {}
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore
        return cls(**{k: v for k, v in d.items() if k in known})
