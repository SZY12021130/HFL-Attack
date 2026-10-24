"""
base：通用基础包
================

所有方案包（ckks / ppbr / threshold / rfl / baseline / hfl_attacks）共同
依赖的通用逻辑层：数据集读取与划分、模型动物园、日志与实验输出、客户端
选择、带宽模拟、明文聚合规则注册表、异常检测、通用密码原语（SA）、
客户端-边缘关联分组。

依赖纪律（方案包必须遵守）：
- 方案包只能 import `base`、共享基础设施 `threshold` 及第三方库；
- 方案包之间禁止互相 import（peer 方案包：ckks / ppbr / rfl / baseline /
  hfl_attacks）；
- 已消除的历史违规：threshold→ppbr.sa（sa 上移至 base/sa.py）、
  ckks↔baseline 环（规则注册表在 base/aggregation.py，baseline 只做注册）。
- base/experiment.py 的唯一例外：实验编排需要惰性导入 hfl_attacks 攻击
  注册表与 ckks 包的 FederatedClient/AggregationServer/CKKSManager
  （函数内 import，非模块级静态依赖）。
"""

from base import (
    aggregation,
    anomaly,
    bandwidth_simulator,
    client_selection,
    data,
    experiment,
    grouping,
    log_utils,
    metrics,
    models,
    sa,
)

__all__ = [
    "aggregation",
    "anomaly",
    "bandwidth_simulator",
    "client_selection",
    "data",
    "experiment",
    "grouping",
    "log_utils",
    "metrics",
    "models",
    "sa",
]
