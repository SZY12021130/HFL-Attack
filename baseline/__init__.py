"""
baseline/：明文鲁棒聚合基线包
==============================

SFLBR 论文对比表中的明文鲁棒聚合基线集合：每个算法一个子包，
统一接口 `aggregate(updates, weights=None, **params)`，
供"明文 FedAvg + 鲁棒防御"实验（configs: encryption.enabled=false）使用。

依赖方向：baseline → base（向 `base.aggregation` 注册规则）。
方案包（ckks 等）不 import 本包，统一从 base.aggregation 查表
（本包 re-export AGGREGATION_REGISTRY 仅为旧调用点兼容）。

注册表 AGGREGATION_REGISTRY：name -> RuleSpec(fn, 默认params, 论文出处, 一句话描述)。
服务器明文聚合路径（AggregationServer._apply_defense）按 defense.aggregation_rule
从注册表查找规则；dp_brem 为有状态规则（跨轮动量），通过 state 参数维护。
"""

from baseline import (
    fedavg,
    krum,
    median,
    trimmed_mean,
    multi_krum,
    geometric_median,
    centered_clipping,
    dnc,
    dp_brem,
    rflba,
)

from base.aggregation import (
    AGGREGATION_REGISTRY,
    RuleSpec,
    register_rule,
)

register_rule("fedavg", RuleSpec(
    fedavg.aggregate, {},
    "McMahan et al., AISTATS 2017",
    "标准加权平均，无防御基线",
))
register_rule("median", RuleSpec(
    median.aggregate, {},
    "Chen et al., POMACS 2017",
    "逐坐标中值，对异常值强鲁棒",
))
register_rule("trimmed_mean", RuleSpec(
    trimmed_mean.aggregate, {"trim_ratio": 0.1},
    "Chen et al., POMACS 2017",
    "逐坐标两端截断后取均值（要求恶意比例 < trim_ratio）",
))
register_rule("krum", RuleSpec(
    krum.aggregate, {"f": None},
    "Blanchard et al., NDSS 2017",
    "选与 n−f−2 个近邻距离和最小的单个更新（n > 2f+2）",
))
register_rule("multi_krum", RuleSpec(
    multi_krum.aggregate, {"f": None, "m": None},
    "Blanchard et al., NDSS 2017（Krum 多选变体）",
    "选 m 个最可信更新后 FedAvg，比 Krum 稳定",
))
register_rule("geometric_median", RuleSpec(
    geometric_median.aggregate, {"max_iter": 10, "tol": 1e-5},
    "Weiszfeld 算法（Chen et al. POMACS'17 对比讨论）",
    "几何中位数，Weiszfeld 迭代求解",
))
register_rule("centered_clipping", RuleSpec(
    centered_clipping.aggregate, {"num_iter": 3, "tau_factor": 1.3},
    "Karimireddy et al., ICML 2021",
    "围绕动量中心迭代裁剪，抗合谋攻击（ALIE/IPM）",
))
register_rule("dnc", RuleSpec(
    dnc.aggregate, {"num_byzantine": None, "sub_dim": None,
                    "num_iter": 1, "trim_ratio": 0.1, "seed": 42},
    "Shejwalkar & Houmansadr, USENIX Security 2021（Su & Xu 2019 筛选思路）",
    "随机投影 + 谱检测 + 聚类 + 截断均值",
))
register_rule("dp_brem", RuleSpec(
    dp_brem.aggregate, {"phi": 0.9, "sigma": 0.01, "clip_C": None, "num_iter": 3},
    "El Mhamdi et al., ICML 2018（BR moment + DP 噪声）",
    "服务器动量 m←φ·m+(1−φ)·g_agg，裁剪后加高斯噪声释放（有状态）",
    stateful=True,
))
register_rule("rflba", RuleSpec(
    rflba.aggregate, {"threshold": 0.5},
    "Cao et al., NDSS 2021（FLTrust 思路）",
    "余弦相似度过滤低相似更新，幸存者取质心",
))

__all__ = ["AGGREGATION_REGISTRY", "RuleSpec"]
