"""
RFL: Robust Privacy-Preserving Federated Learning 可插拔实现
=============================================================

严格对应 Zhou et al., IEEE TDSC 2026（完整阅读笔记见 paperlist/Robust/）的
算法 2。**轨道架构**：开启 ``rfl.enabled`` 后，整条训练路线切换为 RFL 协议
（三层 云CS/边缘ES/客户端EC、模型切分、中间表示 DP、数字签名 + 同态哈希
可验证聚合），不走全局掩码投票 / TopK 稀疏化 / 单密钥 CKKS 聚合；关闭时
项目保持原有 CKKS / PPBR 轨道不变。轨道间共享数据/模型/分组/指标采集。

包结构：
- ``config.py``   RFLConfig 超参数（论文默认值）
- ``crypto.py``   DigitalSignature（Ed25519，式 8/9/14/16）+
                  HomomorphicHash（乘性同态哈希，式 13/17，定点整数域验证）
- ``splitnn.py``  模型切分（浅层 SL→EC / 深层 DL→ES，式 6/10）与
                  扁平向量↔命名参数映射、DP 噪声尺度（式 7）
- ``protocol.py`` RFLProtocol 完整协议编排（论文算法 2 的 15 步消息流）

信任模型（与 CKKS/PPBR 轨道不同，见 doc/rfl.md）：**云 CS 可半诚实或恶意**
——可伪造虚拟客户端身份（IFGIA）、篡改/丢弃聚合结果；ES 与 EC 诚实。
RFL 的防御：EC 身份经 EC→ES 直连通道注册 + 式(9) 签名验证（伪造身份
被拒）；ES 间同态哈希 + 式(17) 聚合校验（篡改/丢弃被检出，全体 ES 中止）。
"""

from rfl.config import RFLConfig
from rfl.crypto import DigitalSignature, HomomorphicHash
from rfl.protocol import RFLProtocol
from rfl.splitnn import build_split, dp_sigma

__all__ = ['RFLConfig', 'RFLProtocol', 'DigitalSignature', 'HomomorphicHash',
           'build_split', 'dp_sigma']
