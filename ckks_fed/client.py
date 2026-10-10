"""
联邦学习客户端模块
实现本地训练、模型加密和上传功能
扩展支持：恶意客户端（数据攻击 + 模型攻击）
"""

import torch
import torch.nn as nn
import torch.optim as optim
from typing import Dict, List, Tuple, Optional
import numpy as np
import time
import logging
from ckks_fed.log_utils import (
    LogTag, tag_info, tag_warn,
    log_training_result, log_encryption_result,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class FederatedClient:
    """联邦学习客户端：负责本地训练、模型加密和上传"""
    
    def __init__(self, client_id: int, model: nn.Module, 
                 train_loader, config: Dict, device: str = "cpu"):
        self.client_id = client_id
        self.model = model.to(device)
        self.train_loader = train_loader
        self.config = config
        self.device = device
        
        self.num_epochs = config['system']['num_epochs']
        self.learning_rate = config['system']['learning_rate']
        self.batch_size = config['system']['batch_size']
        
        self.criterion = nn.CrossEntropyLoss()
        
        # ---- 可配置优化器（SGD / Adam / AdamW）----
        opt_cfg = config.get('system', {}).get('optimizer', {})
        opt_name = opt_cfg.get('name', 'sgd').lower()
        lr = opt_cfg.get('lr', config['system']['learning_rate'])
        
        if opt_name == 'adam':
            self.optimizer = optim.Adam(self.model.parameters(), lr=lr)
        elif opt_name == 'adamw':
            self.optimizer = optim.AdamW(self.model.parameters(), lr=lr)
        else:
            momentum = opt_cfg.get('momentum', 0.0)
            self.optimizer = optim.SGD(self.model.parameters(), lr=lr, momentum=momentum)
        
        # ---- 可配置学习率调度器 ----
        sched_cfg = config.get('system', {}).get('lr_scheduler', {})
        self.scheduler = None
        if sched_cfg.get('enabled', False):
            sched_name = sched_cfg.get('name', 'step').lower()
            if sched_name == 'step':
                self.scheduler = optim.lr_scheduler.StepLR(
                    self.optimizer,
                    step_size=sched_cfg.get('step_size', 5),
                    gamma=sched_cfg.get('gamma', 0.5)
                )
            elif sched_name == 'cosine':
                total_epochs = config['system']['num_rounds'] * config['system']['num_epochs']
                self.scheduler = optim.lr_scheduler.CosineAnnealingLR(
                    self.optimizer,
                    T_max=sched_cfg.get('T_max', total_epochs),
                    eta_min=sched_cfg.get('eta_min', 0.0)
                )
            elif sched_name == 'exponential':
                self.scheduler = optim.lr_scheduler.ExponentialLR(
                    self.optimizer,
                    gamma=sched_cfg.get('gamma', 0.95)
                )
            tag_info(LogTag.CLIENT, f"#{self.client_id} scheduler={sched_name} | step_size={sched_cfg.get('step_size', 'N/A')} | gamma={sched_cfg.get('gamma', 'N/A')}")
        
        self.local_train_time = 0.0
        self.encryption_time = 0.0
        self.plaintext_size = 0
        self.ciphertext_size = 0
        self.data_size = len(train_loader.dataset)
        
        # CKKS 管理器（明文客户端为 None，加密客户端由外部注入）
        self.ckks_manager = None
        
        # PTopk: 记录上次本地训练轮次，用于标准路径下判断跨轮残差衰减
        self._ptopk_last_trained_round = -1
        
        tag_info(LogTag.CLIENT, f"#{client_id} initialized  |  data_size={self.data_size}")
    
    def get_model_params(self) -> Dict[str, torch.Tensor]:
        params = {name: param.data.clone() for name, param in self.model.named_parameters()}
        for name, buf in self.model.named_buffers():
            if 'running_mean' in name or 'running_var' in name:
                params[name] = buf.clone()
        return params
    
    def set_model_params(self, params: Dict[str, torch.Tensor]):
        with torch.no_grad():
            for name, param in self.model.named_parameters():
                if name in params:
                    param.copy_(params[name])
            for name, buf in self.model.named_buffers():
                if name in params:
                    buf.copy_(params[name])
    
    def _apply_ptopk_stale_decay(self, round_num: int) -> None:
        """
        标准路径下：客户端本次被选中时，若距离上次训练有 gap>1 轮，
        则按 stale_gamma^(gap-1) 衰减本地残差累加器。
        """
        if (self.ckks_manager is not None and
            hasattr(self.ckks_manager, 'ptopk') and
            self.ckks_manager.ptopk.client_residual_enabled):
            if self._ptopk_last_trained_round >= 0 and round_num is not None:
                gap = round_num - self._ptopk_last_trained_round
                if gap > 1:
                    self.ckks_manager.ptopk.decay_residual_by_gap(gap)
                    tag_info(LogTag.CLIENT,
                             f"#{self.client_id} PTopk residual DECAY | gap={gap} | last=R{self._ptopk_last_trained_round}")
                else:
                    tag_info(LogTag.CLIENT,
                             f"#{self.client_id} PTopk residual KEEP | continuous | last=R{self._ptopk_last_trained_round}")
            else:
                tag_info(LogTag.CLIENT,
                         f"#{self.client_id} PTopk residual INIT | first training")

    def _decrypt_global_params(self, encrypted_global_params) -> Dict[str, torch.Tensor]:
        """客户端解密加密全局模型（仅客户端持有私钥）"""
        if self.ckks_manager is None:
            raise RuntimeError(f"Client {self.client_id}: ckks_manager not injected, cannot decrypt encrypted_global_params")
        shapes = {name: param.shape for name, param in self.model.named_parameters()}
        for name, buf in self.model.named_buffers():
            if 'running_mean' in name or 'running_var' in name:
                shapes[name] = buf.shape
        return self.ckks_manager.decrypt_model_update(encrypted_global_params, shapes)

    @torch.no_grad()
    def evaluate_encrypted(self, encrypted_global_params, test_loader,
                           criterion: nn.Module) -> Tuple[float, float]:
        """
        解密加密全局模型并在测试集上评估。

        服务器不持有私钥的架构下，全局模型的解密评估由客户端完成，
        服务器全程不接触明文模型。
        """
        global_params = self._decrypt_global_params(encrypted_global_params)
        self.set_model_params(global_params)

        self.model.eval()
        total_loss = 0.0
        correct = 0
        total = 0
        for data, target in test_loader:
            data, target = data.to(self.device), target.to(self.device)
            output = self.model(data)
            loss = criterion(output, target)
            total_loss += loss.item()
            _, predicted = output.max(1)
            total += target.size(0)
            correct += predicted.eq(target).sum().item()

        return total_loss / len(test_loader), 100. * correct / total

    def local_train(self, global_params: Optional[Dict[str, torch.Tensor]] = None,
                    encrypted_global_params: Optional[Dict[str, List]] = None,
                    round_num: int = 0,
                    ) -> Tuple[Dict[str, torch.Tensor], float, float]:
        start_time = time.time()

        # 如果提供了密文全局模型，客户端本地解密
        if encrypted_global_params is not None:
            global_params = self._decrypt_global_params(encrypted_global_params)
        
        if global_params is not None:
            self.set_model_params(global_params)
        old_params = self.get_model_params()
        
        # ---- PTopk: 标准路径下对跨轮残差进行陈旧衰减 ----
        self._apply_ptopk_stale_decay(round_num)
        
        # ---- 梯度裁剪配置 ----
        grad_clip_cfg = self.config.get('system', {}).get('gradient_clipping', {})
        grad_clip_enabled = grad_clip_cfg.get('enabled', False)
        grad_clip_max_norm = grad_clip_cfg.get('max_norm', 1.0)
        
        self.model.train()
        total_loss = 0.0
        correct = 0
        total = 0
        
        for epoch in range(self.num_epochs):
            epoch_loss = 0.0
            epoch_correct = 0
            epoch_total = 0
            for batch_idx, (data, target) in enumerate(self.train_loader):
                data, target = data.to(self.device), target.to(self.device)
                self.optimizer.zero_grad()
                output = self.model(data)
                loss = self.criterion(output, target)
                loss.backward()
                # ---- 梯度裁剪 (BatchCrypt/QuanCrypt-FL 推荐) ----
                if grad_clip_enabled:
                    torch.nn.utils.clip_grad_norm_(self.model.parameters(), grad_clip_max_norm)
                self.optimizer.step()
                epoch_loss += loss.item()
                _, predicted = output.max(1)
                epoch_total += target.size(0)
                epoch_correct += predicted.eq(target).sum().item()
            total_loss += epoch_loss / len(self.train_loader)
            correct += epoch_correct
            total += epoch_total
            # ---- 学习率调度器步进（每个本地 epoch）----
            if self.scheduler is not None:
                self.scheduler.step()
        
        avg_loss = total_loss / self.num_epochs
        accuracy = 100. * correct / total
        
        new_params = self.get_model_params()
        upload_difference = self.config['system'].get('upload_difference', True)
        if upload_difference:
            model_update = {name: new_params[name] - old_params[name] for name in old_params}
            mode_str = "差值"
        else:
            model_update = new_params
            mode_str = "完整权重"
        
        # ---- 模型更新裁剪 (Update Clipping, QuanCrypt-FL 风格) ----
        update_clip_cfg = self.config.get('system', {}).get('update_clipping', {})
        update_clip_enabled = update_clip_cfg.get('enabled', False)
        if update_clip_enabled and upload_difference:
            clip_method = update_clip_cfg.get('method', 'layerwise_mean')
            clip_factor = update_clip_cfg.get('clip_factor', 3.0)
            model_update = self._clip_model_update(model_update, clip_method, clip_factor)
        
        self.local_train_time = time.time() - start_time
        log_training_result(self.client_id, avg_loss, accuracy, self.local_train_time, mode_str)
        
        # 记录本轮训练轮次，供下次判断是否为连续参与
        self._ptopk_last_trained_round = round_num
        
        return model_update, avg_loss, accuracy
    
    def compute_local_mask(self, model_update: Dict[str, torch.Tensor], ckks_manager, round_num: int = 0) -> Optional[np.ndarray]:
        """
        客户端本地计算稀疏化 mask（隐私安全：不上传明文梯度，只返回 0/1 mask）。
        
        当 ckks.sparsification.strategy != 'none' 时，计算与 packing 策略对齐的 batch 级 mask。
        """
        ckks_cfg = self.config.get('ckks', {})
        sparsity_cfg = ckks_cfg.get('sparsification', {})
        strategy = sparsity_cfg.get('strategy', 'none')
        
        # 如果 sparsification 未开启，不进行稀疏化，返回 None
        if strategy == 'none' or strategy is None:
            return None
        
        # 读取 sparsification 配置（稀疏率、打分方式等）
        sparsity_rate = sparsity_cfg.get('sparsity_rate', 0.1)
        scoring_method = sparsity_cfg.get('scoring_method', 'avg_abs')
        
        # 计算与 packing 策略对齐的 batch_size
        packing = ckks_manager.packing_strategy if ckks_manager else None
        values_per_chunk = packing.get_values_per_chunk() if packing else 8192
        
        # 调用 GlobalMaskVote 的静态方法在本地计算 mask
        from ckks_fed.global_mask_vote import GlobalMaskVote
        mask = GlobalMaskVote.compute_mask_from_update(
            model_update=model_update,
            batch_size=values_per_chunk,
            sparsity_rate=sparsity_rate,
            scoring_method=scoring_method,
            round_num=round_num,
        )
        
        active = int(mask.sum()) if mask is not None else 0
        total = len(mask) if mask is not None else 0
        tag_info(LogTag.CLIENT, f"#{self.client_id} local mask computed | strategy={strategy} | "
                 f"active={active}/{total} | rate={sparsity_rate} | scoring={scoring_method}")
        return mask
    
    def encrypt_update(self, model_update: Dict[str, torch.Tensor], ckks_manager, round_num: int = 0,
                        global_mask=None) -> Dict[str, List]:
        start_time = time.time()
        # ---- Warmup 支持：前 N 轮跳过客户端稀疏化（可插拔）----
        ckks_cfg = self.config.get('ckks', {})
        sparsity_cfg = ckks_cfg.get('sparsification', {})
        use_warmup = sparsity_cfg.get('use_warmup_skip_sparse', False)
        warmup_rounds = sparsity_cfg.get('warmup_rounds', 0)
        skip_sparse = use_warmup and (round_num < warmup_rounds)
        if skip_sparse:
            tag_info(LogTag.CLIENT, f"#{self.client_id} warmup round {round_num} | skip sparsification")
        encrypted_update = ckks_manager.encrypt_model_update(
            model_update, round_num=round_num,
            skip_sparsification=skip_sparse,
            client_id=self.client_id,
            global_mask=global_mask,
        )
        self.encryption_time = time.time() - start_time
        # 稀疏模式：plaintext_size 应反映实际加密的参数数量
        if encrypted_update.get('__sparse__'):
            # 使用 total_elements 或 indices 长度计算实际明文大小
            actual_elements = encrypted_update.get('total_elements', len(encrypted_update.get('indices', [])))
            self.plaintext_size = actual_elements * 4
        else:
            self.plaintext_size = sum(p.numel() * 4 for p in model_update.values())
        self.ciphertext_size = ckks_manager.compute_encrypted_update_size(encrypted_update)
        if encrypted_update.get('__sparse__'):
            self.num_ciphertexts = len(encrypted_update['ciphertexts'])
        else:
            self.num_ciphertexts = sum(len(v) for v in encrypted_update.values())
        log_encryption_result(self.client_id, self.encryption_time, self.ciphertext_size, self.num_ciphertexts)

        # 安全边界：以序列化字节形式上传（模拟网络传输）。
        # 服务器用自己的公钥上下文加载后只做同态运算——密文字节中不携带私钥材料，
        # 服务器进程内永远不会出现含私钥的密文对象。
        if encrypted_update.get('__sparse__'):
            encrypted_update['ciphertexts'] = [
                ct.serialize() for ct in encrypted_update['ciphertexts']
            ]
        return encrypted_update
    
    def _clip_model_update(self, model_update: Dict[str, torch.Tensor], 
                           method: str, clip_factor: float) -> Dict[str, torch.Tensor]:
        """
        对模型更新进行逐层裁剪，防止量化溢出 (QuanCrypt-FL 风格 mean-based clipping)。
        
        Args:
            model_update: 模型更新字典
            method: 'layerwise_mean' (每层按均值裁剪) 或 'global_norm' (全局L2裁剪)
            clip_factor: 裁剪倍数 (mean-based: 超过 factor*mean_abs 被裁剪)
        """
        clipped = {}
        if method == 'layerwise_mean':
            for name, tensor in model_update.items():
                mean_abs = tensor.abs().mean().item()
                if mean_abs < 1e-12:
                    clipped[name] = tensor
                    continue
                max_val = clip_factor * mean_abs
                clipped[name] = torch.clamp(tensor, -max_val, max_val)
        elif method == 'global_norm':
            # 计算全局L2 norm，按比例裁剪
            total_norm = 0.0
            for tensor in model_update.values():
                total_norm += tensor.pow(2).sum().item()
            total_norm = total_norm ** 0.5
            if total_norm > 1e-12 and total_norm > clip_factor:
                scale = clip_factor / total_norm
                for name, tensor in model_update.items():
                    clipped[name] = tensor * scale
            else:
                clipped = dict(model_update)
        else:
            clipped = dict(model_update)
        return clipped
    
    def get_weight(self) -> float:
        return float(self.data_size)
    
    def get_stats(self) -> Dict:
        return {
            'client_id': self.client_id,
            'data_size': self.data_size,
            'local_train_time': self.local_train_time,
            'encryption_time': self.encryption_time,
            'plaintext_size': self.plaintext_size,
            'ciphertext_size': self.ciphertext_size
        }


class MaliciousClient(FederatedClient):
    """恶意联邦学习客户端：训练后对模型更新执行攻击"""
    
    def __init__(self, client_id: int, model: nn.Module, train_loader, config: Dict,
                 device: str = "cpu", model_attack=None):
        super().__init__(client_id, model, train_loader, config, device)
        self.model_attack = model_attack
        self.is_malicious = True
        tag_warn(LogTag.CLIENT, f"#{client_id} marked MALICIOUS  |  attack={model_attack.attack_name if model_attack else 'None'}")
    
    def local_train(self, global_params: Optional[Dict[str, torch.Tensor]] = None,
                    encrypted_global_params: Optional[Dict[str, List]] = None,
                    round_num: int = 0
                    ) -> Tuple[Dict[str, torch.Tensor], float, float]:
        model_update, avg_loss, accuracy = super().local_train(
            global_params, encrypted_global_params, round_num=round_num)
        # 攻击基于解密后的明文模型更新，global_params 可能为 None（当使用 encrypted_global_params 时）
        effective_params = global_params
        if effective_params is None and encrypted_global_params is not None:
            effective_params = {name: param.data.clone() for name, param in self.model.named_parameters()}
            for name, buf in self.model.named_buffers():
                if 'running_mean' in name or 'running_var' in name:
                    effective_params[name] = buf.clone()
        if self.model_attack is not None:
            model_update = self.model_attack.attack(model_update, effective_params)
            tag_warn(LogTag.CLIENT, f"#{self.client_id} ATTACK executed: {self.model_attack.attack_name}")
        return model_update, avg_loss, accuracy

