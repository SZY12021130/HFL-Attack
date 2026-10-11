"""
PPBRController —— 每轮编排（算法 2/3/4/5 的仿真映射）
======================================================
对应论文一轮训练的完整流程（时序见 paperlist/ppbr_notes.md §6）：

  算法 2 (CC)  客户端符号元组 → CLDP 扰动 → 边缘 trimmed k-means 过滤
  算法 3 (EA)  可信客户端动量 m=φ·m+(1−φ)·Δ → 边缘均值 m_i
  算法 4 (EF)  云：范数中位数 M = median({‖m_i‖₂})，[M−θ, M+θ] 带 + 数量 l' 筛选
  算法 5 (GA)  裁剪系数 x''_i = min(1, M/‖m_i‖)；全局更新 w ← w − ζ·Σ x''_i·w_i·m_i

仿真约定见包 docstring：明文计算数学机制，API 信息流对齐协议
（边缘只拿到符号元组，云只拿到范数）；结果留痕于 last_stats 供审计。
"""

import logging
from typing import Dict, List, Optional, Set, Tuple

import numpy as np
import torch

from ppbr.cldp import perturb_sign_tuple
from ppbr.clustering import select_trusted_clients
from ppbr.config import PPBRConfig
from ppbr.sign_stats import choose_coordinates, sign_tuple

logger = logging.getLogger(__name__)


class PPBRController:
    """PPBR 可插拔控制器：由 main.py 在 attack 注入之后、加密上传之前调用"""

    def __init__(self, config: dict, num_edges: int, seed: int = 42):
        self.cfg = PPBRConfig.from_dict(config.get('ppbr', {}))
        self.num_edges_cfg = max(1, int(num_edges))
        self.coord_seed = seed
        self.rng = np.random.RandomState(seed)
        # 客户端动量状态 m_{i,j}（跨轮持久，算法 3 第 1 行）
        self.momenta: Dict[int, Dict[str, torch.Tensor]] = {}
        # 上轮过滤/筛选诊断信息
        self.last_stats: dict = {}

    # -----------------------------------------------------------------
    # 算法 2 (CC)：符号统计 + CLDP + 边缘聚类过滤
    # -----------------------------------------------------------------
    def select_coordinates(self, total_params: int, round_num: int) -> np.ndarray:
        """云广播坐标子集 G"""
        return choose_coordinates(total_params, self.cfg.num_coords,
                                  self.coord_seed, round_num)

    def client_sign_tuple(self, update: Dict[str, torch.Tensor],
                          coord_idx: np.ndarray) -> np.ndarray:
        """客户端本地：符号统计元组 s（3 维）"""
        return sign_tuple(update, coord_idx)

    def perturb(self, s: np.ndarray) -> np.ndarray:
        """客户端本地：CLDP 扰动（式 2），发给边缘的只是这个结果"""
        return perturb_sign_tuple(s, self.cfg.epsilon_chi, self.cfg.L,
                                  self.cfg.L_prime, self.rng)

    def filter_clients(self, tuples: Dict[int, np.ndarray],
                       groups: Dict[int, List[int]]) -> Set[int]:
        """
        边缘侧：对每个边缘覆盖的客户端做 trimmed k-means 聚类过滤。

        Args:
            tuples: {client_id: CLDP 带噪符号元组}（边缘唯一可见信息）
            groups: {edge_id: [client_id, ...]} 本轮边缘-客户端分组
        Returns:
            可信客户端集 U^(T)
        """
        trusted: Set[int] = set()
        for eid, cids in groups.items():
            present = [c for c in cids if c in tuples]
            if len(present) < max(3, self.cfg.k + 1):
                # 样本太少无法聚类：全部保留（拜占庭鲁棒性让位于可用性）
                trusted.update(present)
                continue
            points = np.stack([tuples[c] for c in present])
            trusted |= select_trusted_clients(
                points, present, k=self.cfg.k, b=self.cfg.b,
                trim_ratio=self.cfg.trim_ratio, rng=self.rng)
        self.last_stats['clients'] = {'submitted': len(tuples), 'trusted': len(trusted)}
        return trusted

    # -----------------------------------------------------------------
    # 算法 3 (EA)：客户端动量 + 边缘均值（明文仿真）
    # -----------------------------------------------------------------
    def update_momenta(self, client_ids: List[int],
                       updates: List[Dict[str, torch.Tensor]]) -> None:
        """m ← φ·m + (1−φ)·Δ（对每个可信客户端）"""
        phi = self.cfg.momentum_phi
        for cid, upd in zip(client_ids, updates):
            m = self.momenta.get(cid)
            if m is None:
                self.momenta[cid] = {k: (1.0 - phi) * v.detach().clone().float()
                                     for k, v in upd.items()}
            else:
                for k, v in upd.items():
                    m[k].mul_(phi).add_(v.detach().float(), alpha=1.0 - phi)

    def momentum_of(self, client_id: int) -> Dict[str, torch.Tensor]:
        return self.momenta[client_id]

    def edge_means(self, groups: Dict[int, List[int]],
                   alive_ids: Set[int]) -> Dict[int, Dict[str, torch.Tensor]]:
        """
        边缘聚合语义：对可信客户端的动量取均值 m_i = (1/n'_i)Σ m_{i,j}。
        （论文中由 SA 在带噪掩码态完成，云/边缘只见均值；仿真直接对明文动量取均值。）
        """
        out: Dict[int, Dict[str, torch.Tensor]] = {}
        for eid, cids in groups.items():
            present = [c for c in cids if c in alive_ids and c in self.momenta]
            if not present:
                continue
            keys = sorted(self.momenta[present[0]])
            out[eid] = {k: torch.stack([self.momenta[c][k] for c in present]).mean(0)
                        for k in keys}
        return out

    @staticmethod
    def _l2_norm(update: Dict[str, torch.Tensor]) -> float:
        return float(np.sqrt(sum(v.double().pow(2).sum().item()
                                 for v in update.values())))

    # -----------------------------------------------------------------
    # 算法 4 (EF) + 算法 5 (GA)：云范数中位数筛选 + 裁剪系数
    # -----------------------------------------------------------------
    def cloud_filter_edges(self, edge_means: Dict[int, Dict[str, torch.Tensor]]
                           ) -> Tuple[Set[int], Dict[int, float], float]:
        """
        云侧（明文入口，测试/基线用）：由边缘均值计算范数后筛选。

        PPBR 协议轨道请用 cloud_filter_norms（范数由两轮点积协议盲化得出，
        云不接触边缘模型明文）。
        """
        norms = {e: self._l2_norm(m) for e, m in edge_means.items()}
        return self.cloud_filter_norms(norms)

    def cloud_filter_norms(self, norms: Dict[int, float]
                           ) -> Tuple[Set[int], Dict[int, float], float]:
        """
        云侧：范数中位数筛选 + 裁剪（云唯一可见信息是各边缘范数）。

        Returns:
            trusted_edges: A^(T)
            clip:          {edge_id: x''_i}，x''_i = min(1, M/‖m_i‖₂)
            M:             范数中位数
        """
        if not norms:
            return set(), {}, 0.0
        M = float(np.median(list(norms.values())))

        # l'：已知 β → (1−β)l；未知 → 0.5l（论文关键性质：无需先知拜占庭比例）
        l_s = len(norms)
        if self.cfg.beta_known:
            l_target = min((1.0 - self.cfg.beta) * self.num_edges_cfg, l_s)
        else:
            l_target = min(0.5 * self.num_edges_cfg, l_s)
        l_target = max(1, int(round(l_target)))

        trusted = list(norms.keys())
        if self.cfg.theta is not None:
            band = [e for e in trusted
                    if M - self.cfg.theta <= norms[e] <= M + self.cfg.theta]
            if band:  # θ 带为空时回退为不按带过滤
                trusted = band
        # 迭代剔除偏离中位数最远的边缘，直到 |A^(T)| ≤ l'
        while len(trusted) > l_target:
            worst = max(trusted, key=lambda e: abs(norms[e] - M))
            trusted.remove(worst)

        clip = {e: min(1.0, M / norms[e]) for e in trusted}
        self.last_stats['edges'] = {'norms': norms, 'M': M,
                                    'trusted': sorted(trusted), 'clip': clip}
        return set(trusted), clip, M

    def scale_of(self, edge_id: Optional[int], clip: Dict[int, float]) -> float:
        """裁剪系数 x''_i 折入上传权重，再乘全局步长 ζ（算法 5 第 4-5 行）"""
        if edge_id is None:
            return 0.0
        return clip.get(edge_id, 0.0) * self.cfg.global_zeta
