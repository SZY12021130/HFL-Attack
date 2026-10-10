"""
system_attacks subpackage

系统攻击 —— 利用协议/系统弱点：
- free_riding   搭便车攻击（δ0 全零更新 / δ1 上一轮全局模型 / 伪装版随机方向）
- sybil         女巫合谋攻击（多伪客户端上传一致恶意更新放大投票权重）
- dos_dropout   拒绝服务/掉线攻击（随机掉线 / 持续低参与）
"""

from hfl_attacks.system_attacks import free_riding, sybil, dos_dropout  # noqa: F401

__all__ = ['free_riding', 'sybil', 'dos_dropout']
