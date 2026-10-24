"""
实验配置加载器：extends 继承 + 深度合并
=========================================

解决「每个实验一份全量配置」的维护负担：

- 实验配置可用 ``extends: <基础配置路径>`` 声明继承（路径相对于本文件
  所在目录，支持链式继承与多代覆盖）；
- 合并规则：dict 递归合并（子配置键覆盖父配置），其余类型整体替换
  （list/scalar 不做元素级合并）；
- 继承链成环或父配置不存在时抛 ConfigError（ValueError 子类）；
- 合并后的完整配置即实验实际生效配置，Experiment 会把它落盘到结果
  目录（可复现性等价于原来的"复制 config.yaml"）。

典型用法（configs/config_athfhe_twin.yaml 只有 5 行）::

    extends: config.yaml        # 公共参数全继承
    athfhe_twin:
      enabled: true             # 只写本次实验的差异
"""

import copy
import os

import yaml


class ConfigError(ValueError):
    """配置加载错误（extends 缺失/成环、文件格式非法等）"""


def _deep_merge(base: dict, override: dict) -> dict:
    """dict 递归合并：override 优先；非 dict 整体替换（深拷贝）"""
    merged = dict(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def load_config(path: str, _seen: frozenset = frozenset()) -> dict:
    """加载实验配置（含 extends 继承解析与深度合并）。

    Args:
        path: 配置文件路径
        _seen: 内部参数：继承链上已访问的绝对路径（成环检测）

    Returns:
        合并后的完整配置 dict

    Raises:
        ConfigError: 文件不存在/格式非法/extends 成环/父配置缺失
    """
    abs_path = os.path.normpath(os.path.abspath(path))
    if abs_path in _seen:
        chain = " -> ".join(list(_seen) + [abs_path])
        raise ConfigError(f"extends 继承链成环: {chain}")
    if not os.path.exists(abs_path):
        raise ConfigError(f"配置文件不存在: {path}")

    with open(abs_path, 'r', encoding='utf-8') as f:
        cfg = yaml.safe_load(f)
    if cfg is None:
        cfg = {}
    if not isinstance(cfg, dict):
        raise ConfigError(f"配置文件顶层必须是 mapping: {path}")

    extends = cfg.pop('extends', None)
    if extends is None:
        return cfg
    if not isinstance(extends, str) or not extends:
        raise ConfigError(f"extends 必须是非空字符串（配置路径）: {path}")

    parent_path = (extends if os.path.isabs(extends)
                   else os.path.join(os.path.dirname(abs_path), extends))
    parent = load_config(parent_path, _seen | {abs_path})
    return _deep_merge(parent, cfg)
