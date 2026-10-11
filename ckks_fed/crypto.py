"""
CKKS同态加密工具封装模块
基于TenSEAL库实现CKKS方案的封装
"""

import tenseal as ts
import numpy as np
import torch
from typing import List, Dict, Tuple, Optional, Any
import logging
import os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from ckks_fed.packing import (
    PackingStrategy, PackingMetadata,
    LayerwisePacking, MergedPacking,
    create_packing_strategy,
)
from ckks_fed.sparsification import (
    SparsificationStrategy, NoSparsification,
    create_sparsification_strategy,
)
from ckks_fed.ptopk import PTopkModule, PTopkWrappedStrategy

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def aes_generate_key() -> bytes:
    """生成256-bit AES-GCM密钥"""
    return AESGCM.generate_key(bit_length=256)


def aes_encrypt_params(params: Dict[str, torch.Tensor], key: bytes) -> Dict:
    """用AES-GCM加密模型参数字典"""
    aesgcm = AESGCM(key)
    ciphertexts = {}
    metadata = {}
    for name, param in params.items():
        shape = list(param.shape)
        flat = param.detach().cpu().numpy().astype(np.float32).tobytes()
        nonce = os.urandom(12)
        ct = aesgcm.encrypt(nonce, flat, None)
        ciphertexts[name] = ct
        metadata[name] = {'shape': shape, 'nonce': nonce.hex()}
    return {'ciphertexts': ciphertexts, 'metadata': metadata}


def aes_decrypt_params(package: Dict, key: bytes) -> Dict[str, torch.Tensor]:
    """用AES-GCM解密模型参数字典"""
    aesgcm = AESGCM(key)
    params = {}
    for name, ct in package['ciphertexts'].items():
        meta = package['metadata'][name]
        nonce = bytes.fromhex(meta['nonce'])
        flat_bytes = aesgcm.decrypt(nonce, ct, None)
        flat_arr = np.frombuffer(flat_bytes, dtype=np.float32)
        params[name] = torch.tensor(flat_arr.reshape(meta['shape']))
    return params


class CKKSContext:
    """CKKS上下文管理器 - 封装TenSEAL的CKKSContext"""
    
    def __init__(self, poly_modulus_degree: int = 8192,
                 coeff_mod_bit_sizes: List[int] = None,
                 global_scale: float = 2**20):
        """
        初始化CKKS上下文
        
        Args:
            poly_modulus_degree: 多项式模数度 (1024, 2048, 4096, 8192, 16384)
            coeff_mod_bit_sizes: 系数模数比特大小列表
            global_scale: 全局缩放因子
        """
        if coeff_mod_bit_sizes is None:
            coeff_mod_bit_sizes = [40, 20, 40]
        
        self.poly_modulus_degree = poly_modulus_degree
        self.coeff_mod_bit_sizes = coeff_mod_bit_sizes
        self.global_scale = global_scale
        
        # 创建TenSEAL上下文
        self.context = ts.context(
            ts.SCHEME_TYPE.CKKS,
            poly_modulus_degree=poly_modulus_degree,
            coeff_mod_bit_sizes=coeff_mod_bit_sizes
        )
        
        # 设置全局缩放因子
        self.context.global_scale = global_scale
        
        # 启用自动重新线性化
        self.context.auto_relin = True
        
        # 启用自动模数切换
        self.context.auto_mod_switch = True
        
        # 生成Galois密钥（支持密文旋转操作）
        self.context.generate_galois_keys()
        
        logger.info(f"CKKS上下文创建成功: N={poly_modulus_degree}, "
                   f"mod_bits={coeff_mod_bit_sizes}, scale={global_scale}")
    
    def get_context_bytes(self) -> bytes:
        """序列化上下文（用于传输公钥信息）"""
        return self.context.serialize(save_public_key=True, save_secret_key=False)
    
    def get_secret_context_bytes(self) -> bytes:
        """序列化完整上下文（包含私钥，仅用于可信辅助节点）"""
        return self.context.serialize(save_public_key=True, save_secret_key=True)
    
    def get_full_context_bytes(self) -> bytes:
        """序列化完整上下文（包含私钥，用于统一密钥模型的客户端分发）"""
        return self.context.serialize(save_public_key=True, save_secret_key=True,
                                       save_galois_keys=True, save_relin_keys=True)
    
    @classmethod
    def from_bytes(cls, context_bytes: bytes):
        """从字节反序列化上下文"""
        context = ts.context_from(context_bytes)
        obj = cls.__new__(cls)
        obj.context = context
        return obj
    
    @classmethod
    def from_full_bytes(cls, context_bytes: bytes, poly_modulus_degree: int = 8192):
        """从字节反序列化完整上下文（恢复所有密钥）
        
        Args:
            context_bytes: 序列化的上下文字节
            poly_modulus_degree: 多项式模数度（TenSEAL反序列化后无法直接读取，需外部传入）
        """
        context = ts.context_from(context_bytes)
        obj = cls.__new__(cls)
        obj.context = context
        obj.poly_modulus_degree = poly_modulus_degree
        obj.coeff_mod_bit_sizes = []  # 反序列化后无法直接读取，留空表示未知
        obj.global_scale = getattr(context, 'global_scale', 2**20)
        return obj
    
    def get_noise_budget(self, ciphertext) -> float:
        """
        获取密文的噪声预算（近似估计）
        
        Args:
            ciphertext: CKKS密文
        
        Returns:
            噪声预算（比特）
        """
        try:
            total_bits = sum(self.coeff_mod_bit_sizes)
            return total_bits * 0.8
        except Exception as e:
            logger.warning(f"噪声预算估计失败: {e}")
            return 0.0


class CKKSEncryptor:
    """CKKS加密器 - 处理明文加密"""
    
    def __init__(self, context: CKKSContext, slot_count: int = None):
        self.context = context
        # CKKS slot 数量 = poly_modulus_degree // 2
        self.slot_count = slot_count or (context.poly_modulus_degree // 2)
    
    def encode(self, data: np.ndarray, scale: Optional[float] = None) -> ts.CKKSVector:
        """编码数据为CKKS明文向量"""
        if scale is None:
            scale = self.context.global_scale
        data = np.array(data, dtype=np.float64).flatten()
        ckks_vector = ts.ckks_vector(self.context.context, data, scale=scale)
        return ckks_vector
    
    def encrypt(self, data: np.ndarray, scale: Optional[float] = None) -> ts.CKKSVector:
        """加密数据"""
        return self.encode(data, scale)
    
    def encrypt_tensor(self, tensor: torch.Tensor, chunk_size: int = None) -> List[ts.CKKSVector]:
        """加密PyTorch张量（自动分块，支持Slot Packing）"""
        chunk_size = chunk_size or self.slot_count
        flat_data = tensor.detach().cpu().numpy().flatten()
        ciphertexts = []
        for i in range(0, len(flat_data), chunk_size):
            chunk = flat_data[i:i+chunk_size]
            if len(chunk) < chunk_size:
                chunk = np.pad(chunk, (0, chunk_size - len(chunk)), mode='constant')
            ct = self.encrypt(chunk)
            ciphertexts.append(ct)
        return ciphertexts

    def encrypt_chunks(self, chunks: List[np.ndarray]) -> List[ts.CKKSVector]:
        """加密已打包好的数组块列表"""
        ciphertexts = []
        for chunk in chunks:
            if np.iscomplexobj(chunk):
                # TenSEAL 支持复数数组编码
                ct = ts.ckks_vector(self.context.context, chunk, scale=self.context.global_scale)
            else:
                ct = self.encrypt(chunk)
            ciphertexts.append(ct)
        return ciphertexts


class CKKSDecryptor:
    """CKKS解密器 - 处理密文解密"""
    
    def __init__(self, context: CKKSContext):
        self.context = context
    
    def decrypt(self, ciphertext: ts.CKKSVector) -> np.ndarray:
        """解密密文"""
        return ciphertext.decrypt()
    
    def decrypt_to_tensor(self, ciphertexts: List[ts.CKKSVector], 
                          original_shape: Tuple[int, ...]) -> torch.Tensor:
        """解密CKKS密文列表并恢复为PyTorch张量"""
        decrypted_chunks = []
        for ct in ciphertexts:
            chunk = self.decrypt(ct)
            decrypted_chunks.extend(chunk)
        total_elements = np.prod(original_shape)
        flat_data = np.array(decrypted_chunks[:total_elements])
        return torch.tensor(flat_data.reshape(original_shape))

    def decrypt_chunks(self, ciphertexts: List[ts.CKKSVector]) -> List[np.ndarray]:
        """解密密文列表，返回数组块列表"""
        decrypted = []
        for ct in ciphertexts:
            decrypted.append(self.decrypt(ct))
        return decrypted


class CKKSEvaluator:
    """CKKS评估器 - 执行同态运算"""
    
    def __init__(self, context: CKKSContext):
        self.context = context
    
    def add(self, ct1: ts.CKKSVector, ct2: ts.CKKSVector) -> ts.CKKSVector:
        """密文加法"""
        return ct1 + ct2
    
    def sub(self, ct1: ts.CKKSVector, ct2: ts.CKKSVector) -> ts.CKKSVector:
        """密文减法：ct1 - ct2"""
        return ct1 - ct2
    
    def add_scalar(self, ct: ts.CKKSVector, scalar: float) -> ts.CKKSVector:
        """密文+标量"""
        return ct + scalar
    
    def multiply_scalar(self, ct: ts.CKKSVector, scalar: float) -> ts.CKKSVector:
        """密文*标量"""
        return ct * scalar
    
    def multiply(self, ct1: ts.CKKSVector, ct2: ts.CKKSVector) -> ts.CKKSVector:
        """密文乘法"""
        return ct1 * ct2
    
    def weighted_average(self, ciphertexts: List[ts.CKKSVector], 
                         weights: List[float]) -> ts.CKKSVector:
        """计算加权平均值（用于联邦学习聚合）"""
        if len(ciphertexts) != len(weights):
            raise ValueError("密文数量和权重数量必须相同")
        if len(ciphertexts) == 0:
            raise ValueError("密文列表不能为空")
        # 全 1 权重：纯密文加法，不引入新的缩放（层次聚合的云侧合并路径，
        # 避免在已乘过标量的边缘密文上再做密文×标量导致 scale 越界）
        if all(abs(w - 1.0) < 1e-12 for w in weights):
            result = ciphertexts[0]
            for ct in ciphertexts[1:]:
                result = self.add(result, ct)
            return result
        result = self.multiply_scalar(ciphertexts[0], weights[0])
        for i in range(1, len(ciphertexts)):
            weighted = self.multiply_scalar(ciphertexts[i], weights[i])
            result = self.add(result, weighted)
        return result


def merge_sparse_encrypted_updates(enc1: Dict, enc2: Dict, evaluator=None) -> Dict:
    """
    按 batch 索引对齐合并两个稀疏加密更新。

    关键：不能简单按 ciphertexts 列表索引相加，因为两个稀疏更新的 mask 可能不同。
    必须按 mask 中标记的 batch 位置逐一比对后累加。

    Args:
        enc1: 稀疏加密更新 dict（含 '__sparse__', 'ciphertexts', 'mask'）
        enc2: 同上
        evaluator: CKKSEvaluator 实例（服务器端用 evaluator.add，客户端可用 +）

    Returns:
        合并后的稀疏加密更新 dict
    """
    mask1 = enc1['mask']
    mask2 = enc2['mask']
    cts1 = enc1['ciphertexts']
    cts2 = enc2['ciphertexts']

    # Auto-deserialize bytes -> CKKSVector to avoid _add_plain weirdness
    ctx = evaluator.context.context if evaluator is not None else None
    if ctx is not None and cts1 and isinstance(cts1[0], bytes):
        cts1 = [ts.CKKSVector.load(ctx, c) for c in cts1]
    if ctx is not None and cts2 and isinstance(cts2[0], bytes):
        cts2 = [ts.CKKSVector.load(ctx, c) for c in cts2]

    batch_num = len(mask1)
    merged_mask = [0] * batch_num
    merged_cts = []

    idx1 = 0
    idx2 = 0
    for i in range(batch_num):
        has1 = mask1[i]
        has2 = mask2[i]
        if has1 and has2:
            try:
                if evaluator is not None:
                    merged_cts.append(evaluator.add(cts1[idx1], cts2[idx2]))
                else:
                    merged_cts.append(cts1[idx1] + cts2[idx2])
            except ValueError as e:
                ct1, ct2 = cts1[idx1], cts2[idx2]
                # 尝试序列化再反序列化后相加（刷新内部表示）
                try:
                    ctx = ct1.context
                    ct1_fresh = ts.CKKSVector.load(ctx, ct1.serialize())
                    ct2_fresh = ts.CKKSVector.load(ctx, ct2.serialize())
                    merged_cts.append(ct1_fresh + ct2_fresh)
                except ValueError as e2:
                    raise ValueError(
                        f"merge_sparse_encrypted_updates failed at batch {i}: "
                        f"scale1={getattr(ct1, 'scale', 'N/A')}, "
                        f"scale2={getattr(ct2, 'scale', 'N/A')}, "
                        f"size1={getattr(ct1, 'size', lambda: 'N/A')() if callable(getattr(ct1, 'size', None)) else getattr(ct1, 'size', 'N/A')}, "
                        f"size2={getattr(ct2, 'size', lambda: 'N/A')() if callable(getattr(ct2, 'size', None)) else getattr(ct2, 'size', 'N/A')}, "
                        f"context_same={getattr(ct1, 'context', None) is getattr(ct2, 'context', None)}. "
                        f"Original error: {e}; Refresh error: {e2}"
                    )
            merged_mask[i] = 1
            idx1 += 1
            idx2 += 1
        elif has1:
            # 直接引用原密文（不执行 +0，避免噪声累积）
            # 原列表在此 merge 后不再使用，直接引用安全
            merged_cts.append(cts1[idx1])
            merged_mask[i] = 1
            idx1 += 1
        elif has2:
            merged_cts.append(cts2[idx2])
            merged_mask[i] = 1
            idx2 += 1

    result = {
        '__sparse__': True,
        'ciphertexts': merged_cts,
        'mask': merged_mask,
        'batch_size': enc1['batch_size'],
        'total_elements': enc1['total_elements'],
        'shapes': enc1['shapes'],
        'offsets': enc1['offsets'],
    }
    # 传递 packing_metadata（CRTStack/TwoTier 解密必需）
    pm1 = enc1.get('packing_metadata')
    pm2 = enc2.get('packing_metadata')
    if pm1 is not None:
        result['packing_metadata'] = pm1
    elif pm2 is not None:
        result['packing_metadata'] = pm2
    return result


class CKKSManager:
    """CKKS管理器 - 整合所有CKKS功能"""
    
    def __init__(self, config: Dict, context: CKKSContext = None):
        self.config = config
        if context is not None:
            self.context = context
        else:
            self.context = CKKSContext(
                poly_modulus_degree=config.get('poly_modulus_degree', 8192),
                coeff_mod_bit_sizes=config.get('coeff_mod_bit_sizes', [40, 20, 40]),
                global_scale=config.get('global_scale', 2**20)
            )
        # Slot Packing: 根据多项式模数度计算最大slot数
        # 优先从config读取poly_modulus_degree（兼容外部传入的context）
        poly_degree = config.get('poly_modulus_degree', 8192)
        slot_count = poly_degree // 2 if config.get('slot_packing', True) else 4096
        self.encryptor = CKKSEncryptor(self.context, slot_count=slot_count)
        self.decryptor = CKKSDecryptor(self.context)
        self.evaluator = CKKSEvaluator(self.context)
        # 初始化可插拔打包策略
        self.packing_strategy = create_packing_strategy(config, slot_count)
        # 初始化可插拔包级稀疏化策略
        self.sparsification_strategy = create_sparsification_strategy(config)
        # 强制 batch_size = slot_count，与 FedPHE 对齐：一个 batch = N/2 个 slot = 一个密文
        self.batch_size = slot_count
        # PTopk: 初始化残差累积模块，并在启用时包装稀疏化策略
        self.ptopk = PTopkModule(config)
        if self.ptopk.client_residual_enabled and not isinstance(self.sparsification_strategy, NoSparsification):
            self.sparsification_strategy = self.ptopk.wrap_strategy(self.sparsification_strategy)
            logger.info(f"PTopk client_residual 已启用: 包装策略={self.sparsification_strategy.name}")
        elif self.ptopk.client_residual_enabled:
            logger.warning("PTopk client_residual 已启用，但 sparsification_strategy=none，残差补偿不生效")
        else:
            logger.info("PTopk client_residual 已禁用: 无残差补偿")
        logger.info(
            f"CKKS管理器初始化完成 (slot_count={slot_count}, "
            f"slot_packing={'ON' if config.get('slot_packing', True) else 'OFF'}, "
            f"packing={self.packing_strategy.name}, "
            f"sparsification={self.sparsification_strategy.name}, "
            f"batch_size={self.batch_size})"
        )
    
    def encrypt_model_update(self, model_update: Dict[str, torch.Tensor], round_num: int = 0,
                             skip_sparsification: bool = False,
                             client_id: int = 0,
                             global_mask: Optional[np.ndarray] = None) -> Dict[str, Any]:
        """加密模型更新（FedPHE 对齐：一个 batch = N/2 slot = 一个密文）

        Args:
            model_update: 模型参数字典
            round_num: 当前轮次（用于 RandK 种子）
            skip_sparsification: 是否跳过稀疏化。服务器端加密完整全局模型时必须设为 True。
            client_id: 客户端 ID（用于日志）
            global_mask: 全局掩码（global_mask_vote 模式用），shape=(num_batches,)，值为 1 的 batch 才加密
        """
        # 初始化 packing_metadata，避免在某些代码路径下未赋值
        packing_metadata = None
        
        # 1) 合并所有参数为一个长数组（FedPHE 风格）
        all_values = []
        shapes = {}
        offsets = {}
        offset = 0
        for name in sorted(model_update.keys()):
            param = model_update[name]
            flat = param.detach().cpu().numpy().flatten()
            shapes[name] = list(param.shape)
            offsets[name] = (offset, param.numel())
            all_values.extend(flat.tolist())
            offset += param.numel()
        total_elements = len(all_values)

        # 2) 按 slot_count (= batch_size) 切分为 batch，每个 batch 对应一个密文
        batches = []
        for i in range(0, total_elements, self.batch_size):
            chunk = np.array(all_values[i:i + self.batch_size], dtype=np.float64)
            if len(chunk) < self.batch_size:
                chunk = np.pad(chunk, (0, self.batch_size - len(chunk)), mode='constant')
            batches.append(chunk)

        num_batches = len(batches)

        # ================================================================
        # 全局掩码模式：按全局掩码决定哪些 batch 需要加密（TopK 稀疏化核心路径）
        # ================================================================
        if global_mask is not None and len(global_mask) > 0:
            # 检查是否使用非 layerwise 的 packing 策略（如 CRTStack）
            # 注意：global_mask 模式下，packing 独立于 sparsification 策略。
            # GlobalMaskVote 已经决定了哪些 chunk 上传，不需要额外的稀疏化检查。
            use_packing = self.packing_strategy.name != 'layerwise'

            if use_packing:
                # packing 路径：在 chunk 级别应用 global_mask，确保与全局状态的 chunk 语义一致
                # 注意：只加密被选中的 chunk（和原始 global_mask 分支一致），
                # 否则 ct_idx 在服务器端会错位。
                chunks, metadata = self.packing_strategy.pack(model_update)
                num_batches = len(chunks)
                selected_batches = []
                mask = [0] * num_batches
                for b in range(num_batches):
                    if b < len(global_mask) and global_mask[b] == 1:
                        mask[b] = 1
                        selected_batches.append(chunks[b])
                    # mask=0 的 chunk 不加密（服务器端会补零）
                ciphertexts = self.encryptor.encrypt_chunks(selected_batches)
                packing_metadata = metadata.data
                shapes = metadata.data['shapes']
                offsets = metadata.data['offsets']
                total_elements = metadata.data['total_elements']
                active_count = int(np.sum(global_mask[:num_batches]))
                logger.info(
                    f"[GMV] Client {client_id} encrypt | packing={self.packing_strategy.name} | "
                    f"global_batches={active_count}/{num_batches} | round={round_num}"
                )
                result = {
                    '__sparse__': True,
                    'ciphertexts': ciphertexts,
                    'mask': mask,
                    'batch_size': self.batch_size,
                    'total_elements': total_elements,
                    'shapes': shapes,
                    'offsets': offsets,
                    'strategy': 'global_mask',
                    'sparsity_rate': 1.0,
                    'packing_metadata': packing_metadata,
                }
                return result

            # 非 packing 路径：按原始 batch 级别处理 global_mask
            # 注意：global_mask 模式下，本地 sparsification 被忽略。
            # GlobalMaskVote 已经通过 server 聚合决定了哪些 batch 上传，
            # 不需要客户端再执行独立的 TopK/RandK 稀疏化（避免双重稀疏）。
            if not skip_sparsification and not isinstance(self.sparsification_strategy, NoSparsification):
                logger.warning(
                    f"[GMV] Client {client_id} | global_mask 模式下本地 sparsification "
                    f"({self.sparsification_strategy.name}) 被忽略，仅使用全局掩码。"
                    f"如需独立稀疏化，请设置 global_mask_vote.mode='none'。"
                )
            selected_indices = list(range(num_batches))
            
            # 按全局掩码构建加密 batch：
            #   - 全局掩码为 1 → 加密真实值
            #   - 全局掩码为 0 → 跳过（服务器端补零）
            selected_batches = []
            mask = [0] * num_batches
            for b in range(num_batches):
                if b < len(global_mask) and global_mask[b] == 1:
                    mask[b] = 1
                    selected_batches.append(batches[b])

            ciphertexts = self.encryptor.encrypt_chunks(selected_batches)
            active_count = int(np.sum(global_mask[:num_batches]))
            logger.info(
                f"[GMV] Client {client_id} encrypt | global_batches={active_count}/{num_batches} | "
                f"own_selected={len(selected_indices)} | round={round_num}"
            )
            return {
                '__sparse__': True,
                'ciphertexts': ciphertexts,
                'mask': mask,
                'batch_size': self.batch_size,
                'total_elements': total_elements,
                'shapes': shapes,
                'offsets': offsets,
                'strategy': 'global_mask',
                'sparsity_rate': 1.0,  # 全局掩码模式下由全局掩码决定
            }

        # ================================================================
        # 原始模式：独立稀疏化（无全局掩码）
        # ================================================================
        if not skip_sparsification and not isinstance(self.sparsification_strategy, NoSparsification):
            selected_indices, selected_batches = self.sparsification_strategy.select_batches(batches, round_num)

            mask = [0] * len(batches)
            for idx in selected_indices:
                mask[idx] = 1
            ciphertexts = self.encryptor.encrypt_chunks(selected_batches)
            logger.info(
                f"稀疏加密: strategy={self.sparsification_strategy.name}, "
                f"total_batches={len(batches)}, selected={len(selected_indices)}, "
                f"sparsity={len(selected_indices)/max(1,len(batches)):.2%}, round={round_num}"
            )
        else:
            # 非稀疏路径：使用 packing_strategy 打包（如果配置了非 layerwise 策略）
            use_packing = (
                self.packing_strategy.name != 'layerwise'
                and isinstance(self.sparsification_strategy, NoSparsification)
            )
            if use_packing:
                chunks, metadata = self.packing_strategy.pack(model_update)
                selected_batches = chunks
                mask = [1] * len(chunks)
                ciphertexts = self.encryptor.encrypt_chunks(selected_batches)
                packing_metadata = metadata.data
                # 从 metadata 中获取 shapes/offsets/total_elements
                shapes = metadata.data['shapes']
                offsets = metadata.data['offsets']
                total_elements = metadata.data['total_elements']
                logger.info(
                    f"打包加密: strategy={self.packing_strategy.name}, "
                    f"chunks={len(chunks)}, round={round_num}"
                )
            else:
                # 默认 layerwise 行为
                selected_indices = list(range(len(batches)))
                selected_batches = batches

                mask = [1] * len(batches)
                ciphertexts = self.encryptor.encrypt_chunks(selected_batches)
                packing_metadata = None

        result = {
            '__sparse__': True,
            'ciphertexts': ciphertexts,
            'mask': mask,
            'batch_size': self.batch_size,
            'total_elements': total_elements,
            'shapes': shapes,
            'offsets': offsets,
            'strategy': self.sparsification_strategy.name if not skip_sparsification else 'none',
            'sparsity_rate': getattr(self.sparsification_strategy, 'sparsity_rate', 1.0),
        }
        if packing_metadata is not None:
            result['packing_metadata'] = packing_metadata
        return result
    
    def decrypt_model_update(self, encrypted_update: Dict[str, Any],
                             original_shapes: Dict[str, Tuple]) -> Dict[str, torch.Tensor]:
        """解密模型更新（FedPHE 对齐：统一 __sparse__ 格式）"""
        if not encrypted_update.get('__sparse__'):
            raise ValueError("加密更新必须使用 __sparse__ 格式（FedPHE 对齐）")

        ciphertexts = encrypted_update['ciphertexts']
        batch_size = encrypted_update['batch_size']
        shapes = encrypted_update['shapes']
        offsets = encrypted_update['offsets']

        # 解密选中的 ciphertexts
        # 兼容序列化字节串（来自网络传输）
        if ciphertexts and isinstance(ciphertexts[0], bytes):
            ciphertexts = [ts.CKKSVector.load(self.context.context, c) for c in ciphertexts]
        decrypted_batches = self.decryptor.decrypt_chunks(ciphertexts)

        mask = encrypted_update['mask']
        total_elements = encrypted_update.get('total_elements', encrypted_update.get('num_params', 0))

        # 如果使用了 packing_strategy，通过 unpack 恢复
        packing_metadata = encrypted_update.get('packing_metadata')
        if packing_metadata is not None:
            # 按 mask 还原 decrypted chunks（未选中的补零值 chunk）
            decrypted_chunks = []
            ct_idx = 0
            for batch_idx, is_selected in enumerate(mask):
                if is_selected:
                    decrypted_chunks.append(np.array(decrypted_batches[ct_idx], dtype=np.float64))
                    ct_idx += 1
                else:
                    # 使用 packing_strategy 生成的零值 chunk，确保 unpack 后得到浮点 0
                    decrypted_chunks.append(self.packing_strategy.create_zero_chunk())
            metadata = PackingMetadata(packing_metadata.get('strategy', 'unknown'), packing_metadata)
            return self.packing_strategy.unpack(decrypted_chunks, metadata)

        # 根据 mask 还原所有 batch（未选中的补 0）
        all_values = []
        ct_idx = 0
        for batch_idx, is_selected in enumerate(mask):
            if is_selected:
                chunk = np.array(decrypted_batches[ct_idx], dtype=np.float64)
                ct_idx += 1
                all_values.extend(chunk.tolist())
            else:
                all_values.extend([0.0] * batch_size)

        all_values = np.array(all_values[:total_elements], dtype=np.float64)

        result = {}
        for name in sorted(shapes.keys()):
            off, numel = offsets[name]
            shape = shapes[name]
            result[name] = torch.tensor(all_values[off:off + numel].reshape(shape))
        return result

    def aggregate_encrypted_updates(self, encrypted_updates: List[Dict[str, Any]],
                                    weights: List[float],
                                    normalize: bool = True) -> Dict[str, Any]:
        """聚合加密的模型更新（FedPHE 对齐：统一 __sparse__ 格式）

        Args:
            encrypted_updates: 稀疏加密更新列表
            weights: 各更新对应的权重
            normalize: True = 每个 batch 内按贡献权重归一化（扁平单级聚合，
                一次密文×标量）；False = 仅做加权和不归一化（层次聚合用：
                边缘已按全局权重预缩放，服务器纯加法合并，避免二次乘法
                耗尽乘法深度）
        """
        if len(encrypted_updates) == 0:
            raise ValueError("加密更新列表为空")

        if not encrypted_updates[0].get('__sparse__'):
            raise ValueError("加密更新必须使用 __sparse__ 格式（FedPHE 对齐）")

        return self._aggregate_sparse_updates(encrypted_updates, weights,
                                              normalize=normalize)

    def _aggregate_sparse_updates(self, encrypted_updates: List[Dict[str, Any]],
                                  weights: List[float],
                                  normalize: bool = True) -> Dict[str, Any]:
        """
        稀疏加密更新的聚合。

        核心逻辑（与 FedPHE 论文对齐）：
          - 每个 batch 独立聚合
          - 只聚合提交了该 batch 的客户端
          - normalize=True 时按该 batch 贡献权重归一化（扁平聚合）
          - normalize=False 时做不归一化的加权和（层次聚合：权重已全局预缩放）
        """
        # 强制密文聚合路径：所有打包策略均通过同态运算聚合
        # 服务器不解密客户端更新，仅执行密文级别的加权加法操作
        # 安全边界：客户端上传的密文以序列化字节形式到达（模拟网络传输），
        # 用服务器自己的（公钥）上下文加载——加载后的密文对象不携带任何私钥材料
        for update in encrypted_updates:
            if update['ciphertexts'] and isinstance(update['ciphertexts'][0], bytes):
                update['ciphertexts'] = [
                    ts.CKKSVector.load(self.context.context, c)
                    for c in update['ciphertexts']
                ]

        batch_num = len(encrypted_updates[0]['mask'])
        batch_size = encrypted_updates[0]['batch_size']

        aggregated_ciphertexts = []
        aggregated_mask = [0] * batch_num
        sum_weights_per_batch = [0.0] * batch_num

        for batch_idx in range(batch_num):
            batch_cts = []
            batch_weights = []

            for client_idx, update in enumerate(encrypted_updates):
                if update['mask'][batch_idx] == 1:
                    # 找到该客户端这个 batch 在 ciphertexts 中的索引
                    ct_idx = sum(update['mask'][:batch_idx])
                    batch_cts.append(update['ciphertexts'][ct_idx])
                    batch_weights.append(weights[client_idx])
                    sum_weights_per_batch[batch_idx] += weights[client_idx]

            if len(batch_cts) > 0:
                if normalize:
                    # 扁平聚合：聚合时直接归一化（方案 B），解密时无需再除。
                    total_w = sum(batch_weights)
                    norm_weights = [w / total_w for w in batch_weights]
                    aggregated_batch = self.evaluator.weighted_average(batch_cts, norm_weights)
                else:
                    # 层次聚合：边缘已按全局权重预缩放，这里只做加权和不归一化，
                    # 避免二次密文×标量耗尽乘法深度（CKKS 加法不消耗层级）
                    aggregated_batch = self.evaluator.weighted_average(batch_cts, batch_weights)
                aggregated_ciphertexts.append(aggregated_batch)
                aggregated_mask[batch_idx] = 1

        # 详细记录每个客户端的 mask 和聚合后的 mask
        for cid, update in enumerate(encrypted_updates):
            active = [i for i, m in enumerate(update['mask']) if m == 1]
            logger.info(f"  Client #{cid} upload mask: {active} (count={len(active)})")
        agg_active = [i for i, m in enumerate(aggregated_mask) if m == 1]
        logger.info(f"  Aggregated active: {agg_active} (count={len(agg_active)})")
        
        logger.info(
            f"稀疏聚合: clients={len(encrypted_updates)}, "
            f"total_batches={batch_num}, aggregated_batches={sum(aggregated_mask)}"
        )

        result = {
            '__sparse__': True,
            'ciphertexts': aggregated_ciphertexts,
            'mask': aggregated_mask,
            'batch_size': batch_size,
            'total_elements': encrypted_updates[0]['total_elements'],
            'shapes': encrypted_updates[0]['shapes'],
            'offsets': encrypted_updates[0]['offsets'],
            'strategy': encrypted_updates[0]['strategy'],
            'sparsity_rate': encrypted_updates[0]['sparsity_rate'],
            '__sum_weights__': sum_weights_per_batch,
        }
        return result
    
    def get_key_distribution_size(self) -> int:
        """
        计算需要分发给每个客户端的公钥/计算密钥大小（字节）。
        不包含私钥。
        """
        return len(self.context.context.serialize(save_secret_key=False))

    def compute_aggregated_ct_size(self, num_participants: int = 1) -> int:
        """
        计算聚合密文大小（字节）。

        标准 CKKS（TenSEAL）：同态加法不改变密文大小，返回单个密文大小。
        """
        # 标准 CKKS：聚合不改变密文大小，懒加载计算单个密文大小
        if not hasattr(self, '_single_ct_size') or self._single_ct_size == 0:
            dummy = torch.zeros(self.batch_size, dtype=torch.float32)
            ct = self.encryptor.encrypt(dummy)
            self._single_ct_size = len(ct.serialize())
        return self._single_ct_size

    def compute_encrypted_update_size(self, encrypted_update: Dict[str, Any]) -> int:
        """计算加密更新的序列化大小（字节）"""
        total_size = 0
        if encrypted_update.get('__sparse__'):
            for ct in encrypted_update['ciphertexts']:
                try:
                    total_size += len(ct.serialize())
                except AttributeError:
                    # ct 已经是序列化后的 bytes
                    total_size += len(ct)
                except Exception:
                    total_size += 0
            return total_size
        raise ValueError("加密更新必须使用 __sparse__ 格式（FedPHE 对齐）")


def create_ckks_manager_from_config(config_path: str = "config.yaml") -> CKKSManager:
    """从配置文件创建CKKS管理器"""
    import yaml
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    ckks_config = config.get('ckks', {})
    return CKKSManager(ckks_config)


if __name__ == "__main__":
    logger.info("测试CKKS加密功能...")
    context = CKKSContext(poly_modulus_degree=4096, 
                         coeff_mod_bit_sizes=[40, 20, 40],
                         global_scale=2**20)
    encryptor = CKKSEncryptor(context)
    decryptor = CKKSDecryptor(context)
    evaluator = CKKSEvaluator(context)
    data1 = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    data2 = np.array([0.1, 0.2, 0.3, 0.4, 0.5])
    ct1 = encryptor.encrypt(data1)
    ct2 = encryptor.encrypt(data2)
    ct_add = evaluator.add(ct1, ct2)
    result_add = decryptor.decrypt(ct_add)
    logger.info(f"加法结果: {result_add[:5]}")
    logger.info(f"期望结果: {data1 + data2}")
    ct_scalar = evaluator.multiply_scalar(ct1, 2.0)
    result_scalar = decryptor.decrypt(ct_scalar)
    logger.info(f"标量乘法结果: {result_scalar[:5]}")
    logger.info(f"期望结果: {data1 * 2.0}")
    logger.info("CKKS测试完成!")
