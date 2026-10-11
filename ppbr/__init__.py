"""
PPBR: Privacy-Preserving and Byzantine-Robust 可插拔实现
=========================================================

严格对应 He 等, IEEE TMC 2026 的算法描述（完整阅读笔记见 paperlist/ppbr_notes.md）。
**双轨架构**：开启 ``ppbr.enabled`` 后，整条加解密路线切换为 PPBR 协议，
不走全局掩码投票 / TopK 稀疏化 / 单密钥 CKKS 聚合；关闭时项目保持原有
CKKS 轨道（服务器零私钥）不变。两轨共享数据/模型/本地训练/攻击注入/分组/指标。

包结构：
- ``config.py``     PPBRConfig 超参数（论文默认值）
- ``cldp.py``       CLDP 扰动（式 2，精确采样）
- ``sign_stats.py`` 符号统计元组（正/负/零号比例）
- ``clustering.py`` trimmed k-means 边缘过滤（算法 2 c）
- ``controller.py`` 动量 / 边缘均值 / 云范数筛选+裁剪（算法 3/4/5 的数学机制）
- ``crypto.py``     PLHE 分块加解密 + SA 成对掩码（种子模拟 DH 协商）
- ``protocol.py``   PPBRProtocol 完整协议编排（云/边缘/客户端消息时序）

信任模型（与 CKKS 轨道不同，见 doc/ppbr.md）：云持 sk_A（论文原样）；
每个边缘自持 sk_{Ei}；客户端不持任何服务器密钥。云只知边缘范数与聚合
结果，边缘只见 CLDP 符号元组与掩码态更新。
"""

from ppbr.config import PPBRConfig
from ppbr.controller import PPBRController
from ppbr.crypto import PPBRKeyContext
from ppbr.protocol import PPBRProtocol

__all__ = ['PPBRConfig', 'PPBRController', 'PPBRKeyContext', 'PPBRProtocol']
