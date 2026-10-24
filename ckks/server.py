"""
联邦学习聚合服务器模块
实现模型聚合、全局模型更新和分发功能
扩展支持：多种鲁棒聚合规则与异常检测防御
"""

import torch
import torch.nn as nn
import numpy as np
from typing import Dict, List, Tuple, Optional, Any
import time
import logging
from ckks.defenses import aggregation_rules
from base import anomaly as anomaly_detection
from baseline import AGGREGATION_REGISTRY
from base.log_utils import (
    LogTag, tag_info,
    log_aggregation_result, log_evaluation_result,
)
from ckks.global_mask_vote import GlobalMaskVote

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class AggregationServer:
    """联邦学习聚合服务器：负责收集客户端更新、执行聚合和分发全局模型"""
    
    def __init__(self, model: nn.Module, config: Dict, device: str = "cpu", ckks_manager=None):
        self.model = model.to(device)
        self.config = config
        self.device = device
        self.ckks_manager = ckks_manager
        
        self.aggregation_time = 0.0
        self.communication_cost = 0.0
        self.upload_cost = 0.0
        self.download_cost = 0.0
        self.round_num = 0
        
        # 加密全局状态：服务器在密文域维护全局模型，不下发明文
        self.encrypted_global_state = None
        
        # ---- 全局掩码投票（TopK 稀疏化的服务器端投票核心）----
        gmv_cfg = config.get('global_mask_vote', {})
        self.global_mask_vote = GlobalMaskVote(
            mode=gmv_cfg.get('mode', 'none'),
            threshold_pct=gmv_cfg.get('threshold_pct', 0.2),
            topk_k=gmv_cfg.get('topk_count_k', 5),
            topk_pct=gmv_cfg.get('topk_pct', None),
        )
        self.current_global_mask = None  # 当前轮次的全局掩码
        
        self.history = {
            'train_loss': [], 'train_acc': [], 'test_loss': [], 'test_acc': [],
            'aggregation_time': [], 'communication_cost': [],
            'upload_cost': [], 'download_cost': []
        }
        
        # 防御配置
        defense_cfg = config.get('defense', {})
        self.aggregation_rule = defense_cfg.get('aggregation_rule', 'fedavg')
        self.anomaly_filter = defense_cfg.get('anomaly_filter', None)
        self.defense_params = defense_cfg.get('params', {})
        # 有状态聚合规则（如 baseline/dp_brem 的跨轮动量）的跨轮状态，
        # 由 _apply_defense 按规则名注入对应规则的 aggregate(..., state=...)
        self._rule_state: Dict[str, Dict] = {}
        
        tag_info(LogTag.SERVER, f"Initialized  |  rule={self.aggregation_rule}  |  filter={self.anomaly_filter}")
    
    def get_global_params(self) -> Dict[str, torch.Tensor]:
        params = {name: param.data.clone() for name, param in self.model.named_parameters()}
        for name, buf in self.model.named_buffers():
            if 'running_mean' in name or 'running_var' in name:
                params[name] = buf.clone()
        return params
    
    def set_global_params(self, params: Dict[str, torch.Tensor]):
        with torch.no_grad():
            for name, param in self.model.named_parameters():
                if name in params:
                    param.copy_(params[name])
            for name, buf in self.model.named_buffers():
                if name in params:
                    buf.copy_(params[name])
    
    def compute_download_cost(self) -> int:
        global_params = self.get_global_params()
        return sum(p.numel() * 4 for p in global_params.values())
    
    def set_encrypted_global_state(self, encrypted_state):
        self.encrypted_global_state = encrypted_state
    
    def get_encrypted_global_state(self):
        return self.encrypted_global_state
    
    def init_encrypted_global_state(self, ckks_manager):
        """初始化加密全局状态：用公钥加密当前明文模型"""
        self.ckks_manager = ckks_manager
        self.encrypted_global_state = ckks_manager.encrypt_model_update(self.get_global_params(), skip_sparsification=True)
        tag_info(LogTag.SERVER, "加密全局状态已初始化（服务器不下发明文模型）")
    
    def get_encrypted_global_params(self):
        """返回加密全局模型（下发给客户端的密文）

        安全边界：返回序列化字节形式的密文（模拟网络传输），
        客户端用自己的（含私钥）上下文反序列化后解密；
        服务器加密时使用的公钥上下文中不含任何私钥材料。
        """
        if self.encrypted_global_state is None:
            return None
        result = dict(self.encrypted_global_state)
        result['ciphertexts'] = [
            ct if isinstance(ct, bytes) else ct.serialize()
            for ct in self.encrypted_global_state['ciphertexts']
        ]
        return result
    
    def _is_encrypted_update(self, update) -> bool:
        """检测更新是否为密文格式（FedPHE 对齐：统一 __sparse__）"""
        if not isinstance(update, dict) or len(update) == 0:
            return False
        return update.get('__sparse__', False)
    
    def _update_plain_global_model(self, aggregated_update: Dict[str, torch.Tensor], learning_rate: float):
        """明文更新全局模型（兼容旧代码）"""
        upload_difference = self.config['system'].get('upload_difference', True)
        
        # PTopk 服务器累加器（明文路径）
        # 只在启用包级稀疏化时才生效，否则跳过
        from ckks.sparsification import NoSparsification
        ptopk = self.ckks_manager.ptopk if self.ckks_manager else None
        sparsification_enabled = (
            self.ckks_manager is not None and
            not isinstance(self.ckks_manager.sparsification_strategy, NoSparsification)
        )
        actual_lr = learning_rate
        if (ptopk is not None and ptopk.enabled and ptopk.server_mode != 'none'
                and sparsification_enabled):
            aggregated_update = ptopk.server_update_plain(aggregated_update, learning_rate)
            actual_lr = 1.0  # learning_rate 已在累加器内部应用
            # delayed 模式可能返回零更新，此时跳过模型参数更新
            has_nonzero = any(torch.any(v != 0).item() for v in aggregated_update.values())
            if not has_nonzero:
                self.round_num += 1
                tag_info(LogTag.SERVER, f"全局模型已更新(明文|PTopk {ptopk.server_mode} 跳过)  |  当前轮次={self.round_num}")
                return
        
        with torch.no_grad():
            if upload_difference:
                for name, param in self.model.named_parameters():
                    if name in aggregated_update:
                        update_tensor = aggregated_update[name].to(param.device)
                        param.add_(update_tensor, alpha=actual_lr)
                for name, buf in self.model.named_buffers():
                    if name in aggregated_update:
                        update_tensor = aggregated_update[name].to(buf.device)
                        buf.add_(update_tensor, alpha=actual_lr)
                mode_str = "差值累加"
            else:
                for name, param in self.model.named_parameters():
                    if name in aggregated_update:
                        update_tensor = aggregated_update[name].to(param.device)
                        param.copy_(update_tensor)
                for name, buf in self.model.named_buffers():
                    if name in aggregated_update:
                        update_tensor = aggregated_update[name].to(buf.device)
                        buf.copy_(update_tensor)
                mode_str = "完整权重替换"
        self.round_num += 1
        tag_info(LogTag.SERVER, f"全局模型已更新(明文)  |  当前轮次={self.round_num}  |  上传模式={mode_str}")
    
    def _update_encrypted_global_state(self, encrypted_update, learning_rate: float):
        """
        更新加密全局状态（FedPHE 对齐：统一 __sparse__ 格式，batch = N/2 slot = 一个密文）。
        upload_difference=true 时：同态累加 W += Δ * lr（按 batch 逐个加法）
        upload_difference=false 时：直接替换 W = new_W
        """
        if self.encrypted_global_state is None:
            raise RuntimeError("encrypted_global_state 未初始化，请先调用 init_encrypted_global_state()")
        if not encrypted_update.get('__sparse__'):
            raise ValueError("加密更新必须使用 __sparse__ 格式（FedPHE 对齐）")

        upload_difference = self.config['system'].get('upload_difference', True)
        agg_mask = encrypted_update['mask']
        agg_cts = encrypted_update['ciphertexts']
        global_cts = self.encrypted_global_state['ciphertexts']

        if upload_difference:
            # 按 batch 逐个同态累加：只对 mask=1 的 batch 执行加法
            ct_idx = 0
            for batch_idx, is_selected in enumerate(agg_mask):
                if is_selected:
                    ct = agg_cts[ct_idx]
                    if learning_rate != 1.0:
                        ct = self.ckks_manager.evaluator.multiply_scalar(ct, learning_rate)
                    global_cts[batch_idx] = self.ckks_manager.evaluator.add(
                        global_cts[batch_idx], ct
                    )
                    ct_idx += 1
            mode_str = "差值累加"
        else:
            # 客户端上传完整权重：直接替换对应 batch
            ct_idx = 0
            for batch_idx, is_selected in enumerate(agg_mask):
                if is_selected:
                    global_cts[batch_idx] = agg_cts[ct_idx]
                    ct_idx += 1
            mode_str = "完整权重替换"

        tag_info(LogTag.SERVER, f"全局模型已更新(密文累加)  |  当前轮次={self.round_num}  |  模式={mode_str}")

    def _apply_defense(self, client_updates: List[Dict[str, torch.Tensor]],
                       client_weights: List[float]) -> Tuple[Dict[str, torch.Tensor], List[int]]:
        """应用防御机制：先异常过滤，再用鲁棒聚合规则聚合"""
        kept_indices = list(range(len(client_updates)))
        filtered_updates = client_updates
        filtered_weights = client_weights
        
        # 第一步：异常检测过滤
        if self.anomaly_filter is not None and len(client_updates) > 1:
            if self.anomaly_filter == 'norm_clip':
                filtered_updates, filtered_weights, kept_indices = anomaly_detection.norm_clip_updates(
                    client_updates, client_weights,
                    max_norm_ratio=self.defense_params.get('max_norm_ratio', 3.0)
                )
            elif self.anomaly_filter == 'cosine_similarity':
                filtered_updates, filtered_weights, kept_indices = anomaly_detection.cosine_similarity_filter(
                    client_updates, client_weights,
                    threshold=self.defense_params.get('cos_threshold', 0.5)
                )
            elif self.anomaly_filter == 'mad':
                filtered_updates, filtered_weights, kept_indices = anomaly_detection.median_absolute_deviation_filter(
                    client_updates, client_weights,
                    threshold=self.defense_params.get('mad_threshold', 3.0)
                )
            elif self.anomaly_filter == 'multi_criteria':
                filtered_updates, filtered_weights, kept_indices = anomaly_detection.multi_criteria_filter(
                    client_updates, client_weights,
                    norm_ratio=self.defense_params.get('norm_ratio', 5.0),
                    cos_threshold=self.defense_params.get('cos_threshold', -0.1)
                )
            tag_info(LogTag.SERVER,
                     f"Anomaly filter [{self.anomaly_filter}]  |  "
                     f"original={len(client_updates)}  kept={len(filtered_updates)}  "
                     f"dropped={len(client_updates) - len(filtered_updates)}")
        
        # 第二步：鲁棒聚合——从 baseline.AGGREGATION_REGISTRY 查规则
        # （保留 fedavg/median/trimmed_mean/krum/multi_krum/geometric_median
        #   等旧名字兼容；未知规则回退 fedavg 并告警）
        spec = AGGREGATION_REGISTRY.get(self.aggregation_rule)
        if spec is None:
            logger.warning(f"未知聚合规则 {self.aggregation_rule}，回退到 FedAvg"
                           f"（可选: {sorted(AGGREGATION_REGISTRY)}）")
            aggregated = aggregation_rules.fedavg_aggregate(filtered_updates, filtered_weights)
        else:
            params = dict(spec.default_params)
            params.update(self.defense_params or {})
            if spec.stateful:
                # 有状态规则（如 dp_brem 跨轮动量）：服务器按规则名维护 state
                params['state'] = self._rule_state.setdefault(self.aggregation_rule, {})
            aggregated = spec.fn(filtered_updates, filtered_weights, **params)

        return aggregated, kept_indices
    
    def aggregate_plain_updates(self, client_updates: List[Dict[str, torch.Tensor]],
                                client_weights: List[float],
                                client_upload_sizes: List[int] = None) -> Dict[str, torch.Tensor]:
        """聚合明文模型更新（支持防御机制）"""
        start_time = time.time()
        if len(client_updates) == 0:
            raise ValueError("客户端更新列表为空")
        
        aggregated_update, kept_indices = self._apply_defense(client_updates, client_weights)
        self.aggregation_time = time.time() - start_time
        
        self.download_cost = self.compute_download_cost() * len(client_updates)
        if client_upload_sizes:
            self.upload_cost = sum([client_upload_sizes[i] for i in kept_indices])
        else:
            total_params = sum(p.numel() for p in self.model.parameters())
            self.upload_cost = total_params * 4 * len(client_updates)
        self.communication_cost = self.upload_cost + self.download_cost
        
        log_aggregation_result(
            num_clients=len(client_updates), kept=len(kept_indices),
            rule=self.aggregation_rule, agg_time=self.aggregation_time,
            upload=self.upload_cost, download=self.download_cost,
        )
        return aggregated_update
    
    def aggregate_encrypted_updates(self, encrypted_updates: List[Dict[str, List]],
                                    client_weights: List[float],
                                    ckks_manager,
                                    client_upload_sizes: List[int] = None,
                                    normalize: bool = True):
        """聚合加密的模型更新（CKKS同态聚合，返回密文结果）

        Args:
            normalize: True=扁平聚合（batch 内归一化）；False=层次聚合
                （边缘密文已按全局权重预缩放，云只做加权和，不再归一化）
        """
        start_time = time.time()
        if len(encrypted_updates) == 0:
            raise ValueError("加密更新列表为空")

        if normalize:
            total_weight = sum(client_weights)
            normalized_weights = [w / total_weight for w in client_weights]
        else:
            # 层次聚合：权重由上层（边缘）全局预缩放，原样使用
            normalized_weights = list(client_weights)
        tag_info(LogTag.AGG, f"Aggregating {len(encrypted_updates)} encrypted updates...")

        aggregated_encrypted = ckks_manager.aggregate_encrypted_updates(
            encrypted_updates, normalized_weights, normalize=normalize
        )

        self.aggregation_time = time.time() - start_time
        
        # 通信统计：单密钥方案
        # - upload_cost：客户端密文上传
        # - download_cost：服务器下发加密全局模型（密文）
        if self.encrypted_global_state is not None:
            download_size = ckks_manager.compute_encrypted_update_size(self.encrypted_global_state)
        else:
            download_size = self.compute_download_cost()  # 明文大小作为fallback
        self.download_cost = download_size * len(encrypted_updates)
        
        if client_upload_sizes:
            self.upload_cost = sum(client_upload_sizes)
        else:
            total_params = sum(p.numel() for p in self.model.parameters())
            ciphertext_expansion = 20
            self.upload_cost = total_params * 4 * ciphertext_expansion * len(encrypted_updates)
        self.communication_cost = self.upload_cost + self.download_cost
        
        log_aggregation_result(
            num_clients=len(encrypted_updates), kept=len(encrypted_updates),
            rule="CKKS+" + self.aggregation_rule, agg_time=self.aggregation_time,
            upload=self.upload_cost, download=self.download_cost,
        )
        return aggregated_encrypted

    def update_global_model(self, aggregated_update,
                           learning_rate: float = 1.0):
        """
        更新全局模型。
        如果 aggregated_update 是密文，执行同态更新（encrypted_global_state += update）。
        如果是明文，执行明文更新（兼容旧代码）。
        """
        if self._is_encrypted_update(aggregated_update):
            self._update_encrypted_global_state(aggregated_update, learning_rate)
        else:
            self._update_plain_global_model(aggregated_update, learning_rate)

    # ==================================================================
    # 全局掩码投票（TopK 稀疏化的服务器端投票核心）
    # ==================================================================
    def run_global_mask_vote(self, client_masks: List[Optional[np.ndarray]],
                              num_batches: int, round_num: int) -> Optional[np.ndarray]:
        """
        执行全局掩码投票流程。

        修正后的隐私安全流程：
          1. 客户端本地计算 mask（不上传明文梯度）
          2. 服务器只接收 client_masks（0/1 向量），聚合生成全局掩码
          3. 广播全局掩码给所有客户端

        Args:
            client_masks: 客户端 mask 列表，每个元素 shape=(num_batches,) 或 None
            num_batches: batch 总数
            round_num: 当前轮次

        Returns:
            全局掩码 np.ndarray（shape=(num_batches,)），如果 mode='none' 则返回 None
        """
        if self.global_mask_vote.mode == 'none':
            self.current_global_mask = None
            return None

        # 过滤掉 None（sparsification 未开启的客户端）
        valid_masks = [m for m in client_masks if m is not None]
        if not valid_masks:
            self.current_global_mask = None
            return None

        # 对齐 mask 长度
        aligned_masks = []
        for mask in valid_masks:
            if len(mask) != num_batches:
                aligned = np.zeros(num_batches, dtype=np.int8)
                copy_len = min(len(mask), num_batches)
                aligned[:copy_len] = mask[:copy_len]
                aligned_masks.append(aligned)
            else:
                aligned_masks.append(mask)

        # 服务器聚合生成全局掩码（传递 round_num/history 以支持自适应策略）
        global_mask = self.global_mask_vote.server_aggregate(
            aligned_masks, num_batches, round_num=round_num, history=self.history
        )
        self.current_global_mask = global_mask

        active_batches = int(np.sum(global_mask)) if global_mask is not None else 0
        tag_info(LogTag.SERVER,
                 f"GlobalMaskVote[{self.global_mask_vote.mode}] | "
                 f"batches={active_batches}/{num_batches} | "
                 f"clients={len(valid_masks)} | round={round_num}")

        return global_mask
    
    def get_current_global_mask(self) -> Optional[np.ndarray]:
        """获取当前轮次的全局掩码"""
        return self.current_global_mask

    def record_evaluation(self, test_loss: float, test_acc: float):
        """
        记录客户端上报的评估结果。

        服务器不持有私钥，无法自行解密全局模型评估；
        评估由客户端解密完成后通过本方法上报，服务器只负责记录指标。
        """
        self.history['test_loss'].append(test_loss)
        self.history['test_acc'].append(test_acc)
        self.history['aggregation_time'].append(self.aggregation_time)
        self.history['communication_cost'].append(self.communication_cost)
        self.history['upload_cost'].append(self.upload_cost)
        self.history['download_cost'].append(self.download_cost)

        log_evaluation_result(test_loss, test_acc)
    
    def get_history(self) -> Dict:
        return self.history.copy()
    
    def get_stats(self) -> Dict:
        return {
            'round_num': self.round_num,
            'total_aggregation_time': sum(self.history['aggregation_time']),
            'total_communication_cost': sum(self.history['communication_cost']),
            'total_upload_cost': sum(self.history['upload_cost']),
            'total_download_cost': sum(self.history['download_cost']),
            'avg_aggregation_time': sum(self.history['aggregation_time']) / max(1, len(self.history['aggregation_time'])),
            'avg_communication_cost': sum(self.history['communication_cost']) / max(1, len(self.history['communication_cost']))
        }
