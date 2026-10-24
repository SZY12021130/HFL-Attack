"""
可插拔客户端选择策略模块 (Pluggable Client Selection Strategies)
================================================================

支持以下策略：
  - random: 纯随机采样（默认）

用法:
    from base.client_selection import create_client_selection_strategy
    strategy = create_client_selection_strategy(config, num_clients=10)
    selected_ids = strategy.select(clients, num_needed=5, round_num=0)
"""

import logging
from abc import ABC, abstractmethod
from typing import Dict, List

import numpy as np

from base.log_utils import LogTag, tag_info

logger = logging.getLogger(__name__)


class ClientSelectionStrategy(ABC):
    """客户端选择策略抽象基类。"""

    @abstractmethod
    def select(
        self,
        clients: List,
        num_needed: int,
        round_num: int = 0,
        **kwargs,
    ) -> List[int]:
        """
        选择参与本轮训练的客户端。

        Args:
            clients: 全部客户端对象列表
            num_needed: 需要选择的客户端数量
            round_num: 当前轮次
            **kwargs: 策略特定参数

        Returns:
            被选中的客户端 ID 列表
        """
        pass

    def update_signatures(self, client_id: int, params_dict: Dict[str, np.ndarray]):
        """可选：更新客户端模型参数签名。"""
        pass

    def on_round_end(self, selected_ids: List[int], round_num: int, dropped_ids: List[int] = None):
        """可选：每轮结束后的回调。"""
        pass

    def get_stats(self) -> Dict:
        """可选：返回策略统计信息。"""
        return {}


class RandomSelection(ClientSelectionStrategy):
    """纯随机客户端采样。"""

    def __init__(self, seed: int = 42):
        self.rng = np.random.RandomState(seed)

    def select(self, clients, num_needed, round_num=0, **kwargs):
        num_clients = len(clients)
        num_needed = min(num_needed, num_clients)
        selected = self.rng.choice(num_clients, num_needed, replace=False).tolist()
        tag_info(LogTag.CLIENT_SELECT, f"Random  |  selected={sorted(selected)}")
        return selected


def create_client_selection_strategy(config: Dict, num_clients: int, **kwargs):
    """
    根据配置创建客户端选择策略。仅支持 random，未知策略回退 random 并警告。

    Args:
        config: 配置字典（通常是 self.config）
        num_clients: 客户端总数
        **kwargs: 额外参数（预留）

    Returns:
        ClientSelectionStrategy 实例
    """
    cfg = config.get('client_selection', {})
    strategy_name = cfg.get('strategy', 'random')
    seed = config.get('system', {}).get('random_seed', 42)

    if strategy_name == 'random':
        base_strategy = RandomSelection(seed=seed)
    else:
        logger.warning(f"Unknown client_selection strategy '{strategy_name}', fallback to random")
        base_strategy = RandomSelection(seed=seed)

    tag_info(LogTag.CLIENT_SELECT, f"Strategy created: {strategy_name}")
    return base_strategy
