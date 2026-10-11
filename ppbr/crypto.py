"""
PPBR 传输层加密原语（PLHE 打包块 + SA 成对掩码）
================================================

对应论文（He 等, IEEE TMC 2026）的密码学构件：

- PLHE：CKKS 槽打包。客户端噪声 x 用云的公钥 pk_A 加密为分块密文
  ``[x]_A``；边缘模型 m'_i 用边缘自己的公钥 pk_{Ei} 加密为 ``[2m'_i]_{Ei}``。
  扁平更新向量按槽位切块，每块一个 CKKSVector。
- SA 掩码：成对伪随机掩码使多方求和时掩码相消。真实部署中掩码种子由
  Diffie-Hellman 协商（客户端 j 与 j' 各自计算
  seed = KDF(DH(sk_j, pk_j'))）；仿真中无法跨进程协商，用
  ``hash("ppbr-<tag>", min_id, max_id, round)`` 模拟双方导出同一种子，
  论文的安全性质不受影响（仿真仅替代密钥协商，不替代掩码机制本身）。

信任模型（PPBR 模式，与 CKKS 模式不同，见 doc/ppbr.md）：
  云持有 sk_A（论文原样）；每个边缘自持 sk_{Ei}，只把公钥字节发给云；
  客户端不持有任何服务器密钥。
"""

import hashlib
from typing import Dict, List

# 注意导入顺序：必须先于 tenseal 初始化 torch（及其 OpenMP 运行时）。
# 本环境 MKL(numpy/torch) 与 TenSEAL 各带一份 libiomp5md.dll，
# 若 tenseal 先加载、MKL 后初始化会触发 OMP Error #15 直接 abort
# （与 ckks_fed/crypto.py 相同的导入约定）。
import numpy as np
import torch

DEFAULT_POLY_MODULUS_DEGREE = 8192
DEFAULT_COEFF_MOD_BIT_SIZES = [60, 40, 60]
DEFAULT_GLOBAL_SCALE = 2 ** 40

# 范数盲化协议的精度门槛：低于该 scale 时，‖m‖² = ‖m'‖²+‖x‖²+2m'·x 的
# 灾难性抵消（噪声项远大于信号项）会使 CKKS 误差淹没真值
MIN_GLOBAL_SCALE = 2 ** 30


def pair_seed(tag: str, id_a: int, id_b: int, round_num: int) -> int:
    """
    模拟 DH 密钥协商的输出：通信双方输入同一对 (id_a, id_b) 与轮次，
    导出完全相同的伪随机种子（真实部署中为 KDF(DH(sk, pk'))）。
    """
    lo, hi = min(id_a, id_b), max(id_a, id_b)
    digest = hashlib.sha256(f"ppbr-{tag}-{lo}-{hi}-{round_num}".encode()).digest()
    return int.from_bytes(digest[:8], "little")


def prg_vector(seed: int, dim: int) -> np.ndarray:
    """确定性伪随机向量（PRG），通信双方各自本地生成同一条掩码"""
    g = torch.Generator().manual_seed(seed)
    return torch.randn(dim, generator=g, dtype=torch.float64).numpy()


class PPBRKeyContext:
    """
    一个实体的 CKKS 密钥上下文（含私钥，实体自持）。

    用途：
      - 云：ctx_A（加密 sk_A 保存，可解密 [x]_A 与边缘重加密的贡献）
      - 边缘：ctx_{Ei}（sk_{Ei} 边缘自持，公钥字节注册到云；
        可解密云转来的盲化点积密文）
    """

    def __init__(self, poly_modulus_degree: int = DEFAULT_POLY_MODULUS_DEGREE,
                 coeff_mod_bit_sizes: List[int] = None,
                 global_scale: int = DEFAULT_GLOBAL_SCALE):
        import tenseal as ts
        self._ts = ts
        self.context = ts.context(
            ts.SCHEME_TYPE.CKKS,
            poly_modulus_degree=poly_modulus_degree,
            coeff_mod_bit_sizes=coeff_mod_bit_sizes or DEFAULT_COEFF_MOD_BIT_SIZES)
        self.context.global_scale = global_scale
        self.context.generate_galois_keys()
        self._slots = poly_modulus_degree // 2

    @property
    def slots(self) -> int:
        return self._slots

    def public_bytes(self) -> bytes:
        """只含公钥的上下文字节（发给其他实体）"""
        return self.context.serialize(save_secret_key=False)

    # -----------------------------------------------------------------
    # 分块加解密
    # -----------------------------------------------------------------
    def encrypt_blocks(self, vec: np.ndarray) -> List:
        """扁平向量 → 槽位分块 → 每块一个 CKKSVector 密文"""
        vec = np.asarray(vec, dtype=np.float64).ravel()
        return [self._ts.ckks_vector(self.context, vec[i:i + self.slots])
                for i in range(0, len(vec), self.slots)]

    def decrypt_blocks(self, blocks: List, total_len: int) -> np.ndarray:
        """分块密文 → 解密拼接 → 截断到原始长度"""
        if not blocks:
            return np.zeros(0, dtype=np.float64)
        out = np.concatenate([np.asarray(b.decrypt(), dtype=np.float64)
                              for b in blocks])
        return out[:total_len]


def blocks_of(ctx: 'PPBRKeyContext', vec: np.ndarray) -> List[np.ndarray]:
    """把明文向量按槽位切成与 encrypt_blocks 对齐的明文块（供同态乘）"""
    vec = np.asarray(vec, dtype=np.float64).ravel()
    return [vec[i:i + ctx.slots] for i in range(0, len(vec), ctx.slots)]


def ct_sum(a: List, b: List) -> List:
    """分块密文逐块相加（同态 Σ）"""
    return [x + y for x, y in zip(a, b)]


def ct_mul_plain(blocks: List, plain_blocks: List[np.ndarray]) -> List:
    """分块密文 × 明文块（同态点乘），用于范数盲化协议"""
    ts = __import__('tenseal')
    return [ct * ts.plain_tensor(pb) for ct, pb in zip(blocks, plain_blocks)]
