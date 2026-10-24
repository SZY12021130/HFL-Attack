"""
PTopk: Packed TopK with Error Feedback
基于 STC (Sparse Ternary Compression, Sattler et al., IEEE TNNLS 2020)
的 Error-Feedback 残差累积机制，在同态加密 + 打包框架下的可插拔实现。

核心公式（客户端）:
    r_{t+1} = (Δ_t + r_t) − TopK(Δ_t + r_t)
    upload_t = TopK(Δ_t + r_t)

服务器端可选密文/明文累加器:
    - none:     直接 global_state += aggregated_delta
    - delayed:  每 K 轮同步一次（平滑噪声）
    - momentum: accumulator = β·acc + (1−β)·delta（动量更新）

使用方式:
    1. 包装现有策略: strategy = ptopk.wrap_strategy(base_strategy)
    2. 加密时自动应用残差补偿和更新
    3. 服务器可选配置累加器模式
"""

import numpy as np
import torch
from typing import Dict, List, Tuple, Optional, Any
from ckks.sparsification import SparsificationStrategy


class PTopkModule:
    """
    PTopk 残差累积模块。
    
    客户端维护明文残差累加器 (List[np.ndarray])，补偿 TopK 压缩损失。
    服务器可选维护累加器，支持 delayed / momentum 模式。
    """

    def __init__(self, config: Dict[str, Any]):
        ptopk_cfg = config.get('ptopk', {})
        self.enabled = ptopk_cfg.get('enable', False)
        
        # 细粒度控制：客户端残差补偿器
        client_res_cfg = ptopk_cfg.get('client_residual', {})
        self.client_residual_enabled = client_res_cfg.get('enabled', self.enabled)
        
        # 跨轮陈旧残差衰减（FedPrefetch 场景：客户端间歇性参与）
        # stale_gamma: 跨轮衰减基数，gap=k 时残差乘以 stale_gamma^(k-1)
        # decay_gamma: 每轮自然衰减，防止连续多轮未选中导致残差爆炸
        self.client_residual_stale_gamma = float(client_res_cfg.get('stale_gamma', 1.0))
        self.client_residual_decay = float(client_res_cfg.get('decay_gamma', 1.0))
        
        # 可选：残差范数裁剪（0=关闭）
        self.client_residual_clip_tau = float(client_res_cfg.get('clip_tau', 0.0))
        
        # 互斥衰减模式：true = 连续参与只用 decay_gamma，缺席回来只用 stale_gamma（不同时应用）
        self.mutually_exclusive = bool(client_res_cfg.get('mutually_exclusive', False))
        self._skip_next_decay = False
        
        # 细粒度控制：服务器明文累加器
        server_acc_cfg = ptopk_cfg.get('server_accumulator', {})
        self.server_accumulator_enabled = server_acc_cfg.get('enabled', self.enabled)
        self.server_mode = server_acc_cfg.get(
            'mode', ptopk_cfg.get('server_mode', 'none')
        )
        self.sync_interval = server_acc_cfg.get(
            'sync_interval', ptopk_cfg.get('sync_interval', 5)
        )
        self.momentum_beta = server_acc_cfg.get(
            'momentum_beta', ptopk_cfg.get('momentum_beta', 0.9)
        )
        
        # 客户端残差累加器: List[np.ndarray]，每个元素是一个 batch 的残差
        self.client_residual: Optional[List[np.ndarray]] = None
        
        # 服务器累加器（明文格式，便于可解密架构）
        self.server_accumulator_plain: Optional[Dict[str, torch.Tensor]] = None
        
        self.round_counter = 0

    # ==================================================================
    # 客户端接口
    # ==================================================================
    def wrap_strategy(self, base_strategy: SparsificationStrategy) -> 'PTopkWrappedStrategy':
        """包装现有策略，使其自动应用残差累积"""
        return PTopkWrappedStrategy(base_strategy, self)

    def apply_residual(self, batches: List[np.ndarray]) -> List[np.ndarray]:
        """
        把历史残差加到当前 batches 上。
        应在 select_batches 之前调用。
        """
        if not self.client_residual_enabled or self.client_residual is None:
            return batches
        
        if len(batches) != len(self.client_residual):
            # batch_num 变化时重置（通常不会发生）
            self.client_residual = None
            return batches
        
        compensated = []
        for i in range(len(batches)):
            compensated.append(batches[i] + self.client_residual[i])
        return compensated

    def update_residual(self, original_batches: List[np.ndarray], 
                        selected_indices: List[int]) -> None:
        """
        根据选中结果更新残差累加器。
        
        Args:
            original_batches: 已经加了残差后的完整 batches
            selected_indices: 被选中的 batch 索引
        """
        if not self.client_residual_enabled:
            return
        
        # 互斥模式：如果本轮之前执行了 stale decay（缺席回来），则跳过 decay_gamma
        should_apply_decay = self.client_residual_decay < 0.9999
        if self.mutually_exclusive and self._skip_next_decay:
            should_apply_decay = False
            self._skip_next_decay = False
        
        # 新残差 = 完整 batches（已补偿后）
        # 对选中的 batch，残差清零（信息已上传）
        # 对未选中的 batch，残差保留（累积到下一轮）
        new_residual = [b.copy() for b in original_batches]
        for idx in selected_indices:
            new_residual[idx] = np.zeros_like(original_batches[idx])
        
        # 可选：每轮自然衰减，防止连续多轮未选中导致残差无限增长
        if should_apply_decay:
            for j in range(len(new_residual)):
                if j not in selected_indices:
                    new_residual[j] = new_residual[j] * self.client_residual_decay
        
        self.client_residual = new_residual

    def decay_residual_by_gap(self, gap: int) -> None:
        """
        根据客户端掉线轮数对现有残差进行衰减。
        
        公式: R_new = R_old * stale_gamma^(gap-1)
        gap=1（连续参与）时不衰减；gap>1 时按指数衰减。
        
        互斥模式：若执行了 stale decay，则标记跳过下一轮 update_residual 中的 decay_gamma。
        
        Args:
            gap: 当前轮 - 上次训练轮（>= 2 才生效）
        """
        if self.client_residual is None or gap <= 1:
            return
        gamma = self.client_residual_stale_gamma
        if gamma >= 0.9999:
            return
        scale = gamma ** (gap - 1)
        for i in range(len(self.client_residual)):
            self.client_residual[i] = self.client_residual[i] * scale
        
        # 互斥模式：执行 stale decay 后，跳过下次 update_residual 中的 decay_gamma
        if self.mutually_exclusive:
            self._skip_next_decay = True
        
        import logging
        logging.getLogger(__name__).info(
            f"PTopk client residual decayed by {scale:.4f} (gap={gap}, gamma={gamma})"
        )

    def reset_client_residual(self):
        """重置客户端残差（如客户端重新同步全局模型时）"""
        self.client_residual = None

    # ==================================================================
    # 服务器接口（明文累加器，可解密架构）
    # ==================================================================
    def server_update_plain(self, aggregated_delta_plain: Dict[str, torch.Tensor],
                            learning_rate: float = 1.0) -> Dict[str, torch.Tensor]:
        """
        服务器端累加器更新（明文版本）。
        返回应实际应用到 global_state 上的更新量。
        
        Args:
            aggregated_delta_plain: 本轮聚合后的明文 delta
            learning_rate: 学习率（用于 delayed/momentum 模式）
        
        Returns:
            实际应应用的更新量（可能为 0，取决于模式）
        """
        if not self.server_accumulator_enabled or self.server_mode == 'none':
            return aggregated_delta_plain
        
        if self.server_accumulator_plain is None:
            self.server_accumulator_plain = {k: torch.zeros_like(v) 
                                             for k, v in aggregated_delta_plain.items()}
        
        self.round_counter += 1
        
        if self.server_mode == 'delayed':
            # 延迟同步模式：累加 delta，每隔 sync_interval 轮释放一次
            for k in aggregated_delta_plain:
                self.server_accumulator_plain[k] += aggregated_delta_plain[k] * learning_rate
            if self.round_counter % self.sync_interval == 0:
                update = {k: v.clone() for k, v in self.server_accumulator_plain.items()}
                for k in self.server_accumulator_plain:
                    self.server_accumulator_plain[k].zero_()
                return update
            else:
                # 本轮不更新全局状态（返回零更新）
                return {k: torch.zeros_like(v) for k, v in aggregated_delta_plain.items()}
        
        elif self.server_mode == 'momentum':
            # 动量模式：accumulator = β·acc + (1−β)·delta
            # 全局状态 += accumulator
            beta = self.momentum_beta
            for k in self.server_accumulator_plain:
                self.server_accumulator_plain[k] = (
                    beta * self.server_accumulator_plain[k] + 
                    (1 - beta) * aggregated_delta_plain[k] * learning_rate
                )
            return {k: v.clone() for k, v in self.server_accumulator_plain.items()}
        
        return aggregated_delta_plain

    def reset_server_accumulator(self):
        """重置服务器累加器"""
        self.server_accumulator_plain = None
        self.round_counter = 0


class PTopkWrappedStrategy(SparsificationStrategy):
    """
    PTopk 包装策略：自动在 TopK/RandK 前后应用残差累积。
    对外暴露与 SparsificationStrategy 完全相同的接口。
    """

    def __init__(self, base_strategy: SparsificationStrategy, ptopk: PTopkModule):
        self.base = base_strategy
        self.ptopk = ptopk

    @property
    def name(self) -> str:
        return f"ptopk({self.base.name})"

    @property
    def sparsity_rate(self) -> float:
        return getattr(self.base, 'sparsity_rate', 1.0)

    def select_batches(self, batches: List[np.ndarray], round_num: int = 0) -> Tuple[List[int], List[np.ndarray]]:
        # 1. 加残差
        compensated = self.ptopk.apply_residual(batches)
        
        # 2. 调用原策略做 TopK/RandK
        selected_indices, selected_batches = self.base.select_batches(compensated, round_num)
        
        # 3. 更新残差
        self.ptopk.update_residual(compensated, selected_indices)
        
        return selected_indices, selected_batches
