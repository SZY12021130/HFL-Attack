"""
Threshold: CKKS 门限解密可插拔模块
====================================

在标准 CKKS 轨道（客户端统一持完整私钥）之外，提供 t-out-of-n 门限解密
路线：把私钥的持有/使用权从"每个客户端一份完整 sk"改造为"任意 ≥t 个
参与方协作才能解密"，配合 smudging 噪声使单个参与方的部分解密在统计上
不泄露明文信息（doc/threshold_ckks.md §3/§9.1）。

两种模式（doc §4/§5）：
- ``ttp``（可信分发）：初始化时由实验编排方（可信时刻的 Dealer）把序列化
  上下文的私钥字节以 Shamir (t,n) 分块分片，分发后销毁完整副本；每轮由
  t 个被选客户端把份额交给组合器重构一次性解密上下文。
- ``dkg``（分布式密钥生成）：Pedersen DKG 模拟集体生成密钥——任何单点
  从不持有完整 secret；解密走理想路径（统计标 ``ideal=True``），真实化
  路线见 doc §9.5（OpenFHE MultipartyKeyGen / Lattigo mpCKKS）。

组合器两场景（doc §6）：``combiner="server"`` = 场景 A（服务器可解密聚合
结果）；``combiner="client"`` = 场景 B（服务器盲，推荐）。

包结构：
- ``sharing.py``  任意长度秘密的 Shamir (t,n) 分块分片/恢复（复用 ppbr/sa.py）
- ``dkg.py``      Pedersen DKG（真实数学：承诺 + 份额验证 + 集体公钥）
- ``toy_ckks.py`` 教科书 CKKS + 真·门限部分解密演示（numpy，仅 CPU）
- ``ceremony.py`` ThresholdDecryptor：仪式编排（份额分发/重构/解密/开销统计）
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class ThresholdConfig:
    """CKKS 门限解密配置（对应 config.yaml 的 threshold 段，doc §10.2）"""

    enabled: bool = False
    # 模式："ttp" = 可信分发（doc §4，Shamir 分片完整私钥，组合器瞬时重构）；
    #       "dkg" = Pedersen 分布式密钥生成（doc §5，理想解密模型，
    #               统计标 ideal=True）
    mode: str = "ttp"
    # 门限 t：任意 ≥t 个参与方协作可解密，<t 不可（Shamir (t,n)，doc §3.2）
    t: int = 5
    # 组合器（doc §6）："client" = 场景 B，指定客户端组合，服务器盲（推荐）；
    #                   "server" = 场景 A，服务器组合，可见聚合明文
    combiner: str = "client"
    # 部分解密是否加 smudging 噪声（doc §9.1；真·部分解密的噪声语义见
    # threshold/toy_ckks.py——ttp 重构路径下由组合器本地解密，仅作标注）
    smudging: bool = True

    def __post_init__(self):
        if self.t < 2:
            raise ValueError(f"threshold.t 必须 ≥2（实得 {self.t}）")
        if self.mode not in ("ttp", "dkg"):
            raise ValueError(
                f"threshold.mode 必须是 'ttp'/'dkg'（实得 {self.mode!r}）")
        if self.combiner not in ("client", "server"):
            raise ValueError(
                f"threshold.combiner 必须是 'client'/'server'"
                f"（实得 {self.combiner!r}）")

    @classmethod
    def from_config(cls, cfg: Optional[dict]) -> 'ThresholdConfig':
        """从 config.yaml 的 threshold 段构造（缺省段 = 全默认关闭）"""
        cfg = dict(cfg or {})
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore
        return cls(**{k: v for k, v in cfg.items() if k in known})


# 子模块导入放在 ThresholdConfig 定义之后（ceremony.py 依赖上面的定义）
from threshold.sharing import split_secret, recover_secret, parse_blob
from threshold.dkg import (
    DKGParticipant, DKGResult, run_dkg,
    recover_from_shares, recover_secret_from_shares,
)
from threshold.toy_ckks import (
    toy_keygen, toy_encrypt, toy_decrypt, share_key,
    partial_decrypt, combine,
)
from threshold.ceremony import ThresholdDecryptor

__all__ = ['ThresholdConfig', 'split_secret', 'recover_secret', 'parse_blob',
           'DKGParticipant', 'DKGResult', 'run_dkg', 'recover_from_shares',
           'recover_secret_from_shares',
           'toy_keygen', 'toy_encrypt', 'toy_decrypt', 'share_key',
           'partial_decrypt', 'combine', 'ThresholdDecryptor']
