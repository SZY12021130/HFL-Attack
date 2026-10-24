"""
客户端-边缘关联策略（通用分组逻辑，与各方案无关）
==================================================

云-边-端层次架构（含 UAV 移动模型）的客户端→边缘关联划分。
base 包通用层：被 CKKS 轨道的层次密文聚合、PPBR / RFL 轨道的
边缘分组（protocol.run_round 的 groups 参数）共同复用。

方案包不得互相 import；需要分组时一律依赖本模块。
"""

import logging
from typing import Dict, List

import numpy as np

logger = logging.getLogger(__name__)


class BaseAssignment:
    """客户端-边缘关联策略基类"""

    def assign(self, round_num: int) -> Dict[int, int]:
        """返回 {client_id: edge_id}"""
        raise NotImplementedError

    def edge_client_map(self, round_num: int, num_edges: int) -> Dict[int, List[int]]:
        mapping = self.assign(round_num)
        result = {e: [] for e in range(num_edges)}
        for cid, eid in mapping.items():
            result[eid].append(cid)
        for eid in result:
            result[eid].sort()
        return result


class StaticAssignment(BaseAssignment):
    """固定分片：边缘 e 负责连续的一段客户端 [e*n/E, (e+1)*n/E)"""

    def __init__(self, num_clients: int, num_edges: int):
        self.num_clients = num_clients
        self.num_edges = num_edges

    def assign(self, round_num: int) -> Dict[int, int]:
        mapping = {}
        per = self.num_clients // self.num_edges
        rem = self.num_clients % self.num_edges
        start = 0
        for e in range(self.num_edges):
            size = per + (1 if e < rem else 0)
            for cid in range(start, start + size):
                mapping[cid] = e
            start += size
        return mapping


class UAVAssignment(BaseAssignment):
    """
    UAV 关联：UAV 按随机路点移动，客户端固定位置，
    每轮把客户端分配给最近的 UAV（覆盖半径内）；超出半径且无
    strict_coverage 时回退到最近 UAV。

    模拟 UAV-assisted FL 中"边缘节点移动 → 客户端-边缘关联动态变化"。
    """

    def __init__(self, num_clients: int, num_edges: int,
                 area_size: float = 1000.0, speed: float = 30.0,
                 coverage_radius: float = 400.0,
                 strict_coverage: bool = False, seed: int = 42):
        self.num_clients = num_clients
        self.num_edges = num_edges
        self.area_size = area_size
        self.speed = speed
        self.coverage_radius = coverage_radius
        self.strict_coverage = strict_coverage
        self.seed = seed

        rng = np.random.RandomState(seed)
        # 客户端固定位置
        self.client_pos = rng.uniform(0, area_size, size=(num_clients, 2))
        # UAV 初始位置与随机路点目标
        self.uav_pos = rng.uniform(0, area_size, size=(num_edges, 2))
        self.uav_target = rng.uniform(0, area_size, size=(num_edges, 2))
        self.uav_online = np.ones(num_edges, dtype=bool)

    def _move_uavs(self, round_num: int):
        """随机路点移动：朝目标移动 speed 米，到达后选新目标"""
        for e in range(self.num_edges):
            pos, tgt = self.uav_pos[e], self.uav_target[e]
            dist = float(np.linalg.norm(tgt - pos))
            if dist <= self.speed:
                self.uav_pos[e] = tgt
                rng = np.random.RandomState(self.seed * 100003 + round_num * 97 + e)
                self.uav_target[e] = rng.uniform(0, self.area_size, size=2)
            else:
                direction = (tgt - pos) / dist
                self.uav_pos[e] = pos + direction * self.speed

    def assign(self, round_num: int) -> Dict[int, int]:
        self._move_uavs(round_num)
        mapping = {}
        for cid in range(self.num_clients):
            dists = np.linalg.norm(self.uav_pos - self.client_pos[cid], axis=1)
            nearest = int(np.argmin(dists))
            if self.strict_coverage and dists[nearest] > self.coverage_radius:
                continue  # 无 UAV 覆盖，本轮不参与
            mapping[cid] = nearest
        return mapping

    def coverage_stats(self, round_num: int) -> dict:
        """返回最近 UAV 距离统计（用于实验记录）"""
        dists = np.linalg.norm(
            self.client_pos[:, None, :] - self.uav_pos[None, :, :], axis=2)
        nearest = dists.min(axis=1)
        covered = nearest <= self.coverage_radius
        return {
            "mean_dist": float(nearest.mean()),
            "max_dist": float(nearest.max()),
            "coverage_ratio": float(covered.mean()),
        }


def create_assignment(config: dict, num_clients: int, num_edges: int) -> BaseAssignment:
    """按 hierarchy 配置段构造关联策略（ckks/hierarchy.py 使用）。"""
    hcfg = (config or {}).get('hierarchy', {})
    name = hcfg.get('assignment', 'static')
    if name == 'static':
        return StaticAssignment(num_clients, num_edges)
    if name == 'uav':
        ucfg = hcfg.get('uav', {})
        return UAVAssignment(
            num_clients, num_edges,
            area_size=ucfg.get('area_size', 1000.0),
            speed=ucfg.get('speed', 30.0),
            coverage_radius=ucfg.get('coverage_radius', 400.0),
            strict_coverage=ucfg.get('strict_coverage', False),
            seed=ucfg.get('seed', 42))
    raise ValueError(f"未知关联策略: {name}")
