"""
RFL 密码原语（论文 §V-A）
==============================

1) DigitalSignature —— 数字签名（式 8/9/14/16）：Ed25519（基于 ``cryptography``
   包，已是本项目的传递依赖，见 ppbr/sa.py 的 X25519/HKDF）。消息为
   ``meta || 向量 float32 确定性字节``，验证失败 = 身份伪造或消息被篡改。

2) HomomorphicHash —— 同态哈希（式 13/17）：H(x) = Π_i g_i^{x_i} mod p，
   满足 H(x+y) = H(x)·H(y)（乘性同态）。群参数为确定性推导的安全素数
   p = 2q+1（q 素数），g_i = SHA256(seed‖i)² mod p 落入 q 阶子群；指数
   模 q 归约。浮点梯度先做定点量化（×scale 取整）——验证式(17)全程在
   整数域进行，云对量化整数求和，哈希相等严格成立，无浮点误差。

⚠️ 仿真说明：素数用固定基 Miller-Rabin（40 轮），是研究仿真而非生产级
参数生成；论文原语语义（顺序 q 子群、乘性同态）保持不变。
"""

import hashlib
from functools import lru_cache
from typing import Iterable, List, Optional

import numpy as np


# =====================================================================
# 数字签名（式 8/9/14/16）
# =====================================================================

class DigitalSignature:
    """
    Ed25519 签名/验签。每个 EC / ES 各持一对 (sk, pk)。

    - ``sign(payload: bytes)`` / ``verify(payload, sig)``：原始字节消息；
    - ``sign_vector(vec, meta)`` / ``verify_vector(pk, vec, sig, meta)``：
      向量消息（float32 确定性字节编码 + 元数据，如轮次/实体编号）。
    """

    SIG_SIZE = 64  # Ed25519 签名字节数（线上审计用）

    def __init__(self, seed: Optional[bytes] = None):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import \
            Ed25519PrivateKey
        if seed is None:
            self._sk = Ed25519PrivateKey.generate()
        else:
            # 确定性密钥（仿真用，便于复现实验）
            self._sk = Ed25519PrivateKey.from_private_bytes(
                hashlib.sha512(seed).digest()[:32])
        from cryptography.hazmat.primitives import serialization
        self.public_key_bytes: bytes = self._sk.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw)

    # ---- 字节消息 ----
    def sign(self, payload: bytes) -> bytes:
        return self._sk.sign(payload)

    @staticmethod
    def _pk(pk_bytes: bytes):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import \
            Ed25519PublicKey
        return Ed25519PublicKey.from_public_bytes(pk_bytes)

    @classmethod
    def verify(cls, pk_bytes: bytes, payload: bytes, sig: bytes) -> bool:
        from cryptography.exceptions import InvalidSignature
        try:
            cls._pk(pk_bytes).verify(sig, payload)
            return True
        except InvalidSignature:
            return False

    # ---- 向量消息 ----
    @staticmethod
    def _vector_payload(vec: np.ndarray, meta: bytes) -> bytes:
        v = np.ascontiguousarray(np.asarray(vec), dtype=np.float32)
        return meta + b'|' + v.tobytes()

    def sign_vector(self, vec: np.ndarray, meta: bytes = b"") -> bytes:
        return self.sign(self._vector_payload(vec, meta))

    @classmethod
    def verify_vector(cls, pk_bytes: bytes, vec: np.ndarray,
                      sig: bytes, meta: bytes = b"") -> bool:
        return cls.verify(pk_bytes, cls._vector_payload(vec, meta), sig)

    # ---- 整数消息（哈希值签名，式 14）----
    @staticmethod
    def _int_payload(value: int, meta: bytes) -> bytes:
        n = max(1, (value.bit_length() + 7) // 8)
        return meta + b'#' + value.to_bytes(n, 'big')

    def sign_int(self, value: int, meta: bytes = b"") -> bytes:
        return self.sign(self._int_payload(value, meta))

    @classmethod
    def verify_int(cls, pk_bytes: bytes, value: int,
                   sig: bytes, meta: bytes = b"") -> bool:
        return cls.verify(pk_bytes, cls._int_payload(value, meta), sig)


# =====================================================================
# 同态哈希（式 13/17）
# =====================================================================

def _is_probable_prime(n: int) -> bool:
    """Miller-Rabin（40 个由 n 确定性派生的基），仿真级素性测试。"""
    if n < 2:
        return False
    small = [2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37]
    for p in small:
        if n % p == 0:
            return n == p
    d = n - 1
    r = 0
    while d % 2 == 0:
        d //= 2
        r += 1
    for i in range(40):
        base = int.from_bytes(
            hashlib.sha256(f"rfl-mr-{n}-{i}".encode()).digest(), 'big') % (n - 3) + 2
        x = pow(base, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


@lru_cache(maxsize=None)
def _group_params(bits: int, seed: bytes):
    """
    确定性搜索安全素数 p = 2q+1（p 为 bits 位，q 为 bits-1 位素数）。
    返回 (p, q)；超过迭代上限时退化为普通素数（q=None，指数不做阶归约，
    同态正确性不受影响）。
    """
    tag = hashlib.sha256(seed + b'group' + str(bits).encode()).digest()
    span = 1 << (bits - 1)
    q = (int.from_bytes(tag, 'big') % (span // 2)) | (1 << (bits - 3)) | 1
    for _ in range(300_000):
        p = 2 * q + 1
        if _is_probable_prime(q) and _is_probable_prime(p):
            return p, q
        q += 2
    # 退化路径：普通素数（非安全素数），正确性不变
    q = (int.from_bytes(tag, 'big') % span) | (1 << (bits - 2)) | 1
    while not _is_probable_prime(q):
        q += 2
    return q, None


class HomomorphicHash:
    """
    乘性同态哈希：H(x) = Π_i g_i^{x_i} mod p。

    - 群：安全素数 p = 2q+1 的 q 阶子群，g_i = SHA256(seed‖i)² mod p；
    - 同态：H(x+y) = H(x)·H(y) mod p（式 17 验证基础）；
    - 输入：定点量化后的整数向量（``quantize``）；浮点向量经 ``hash`` 自动量化。

    生成元惰性扩展（每维度一个），按维度数缓存。
    """

    def __init__(self, bits: int = 256, scale: int = 10000,
                 seed: bytes = b"rfl-hh"):
        self.p, self.q = _group_params(int(bits), seed)
        self.scale = int(scale)
        self._seed = seed
        self._gens: List[int] = []
        # 传输审计：一个哈希值的字节数
        self.hash_bytes = (self.p.bit_length() + 7) // 8

    # ---- 群生成元（惰性，按维度索引）----
    def _generator(self, i: int) -> int:
        while len(self._gens) <= i:
            idx = len(self._gens)
            h = int.from_bytes(
                hashlib.sha256(self._seed + idx.to_bytes(4, 'big')).digest(),
                'big') % self.p
            g = pow(h, 2, self.p)  # 进入 q 阶子群（p=2q+1 时任意元平方后阶整除 q）
            while g <= 1:
                h = (h + 1) % self.p
                g = pow(h, 2, self.p)
            self._gens.append(g)
        return self._gens[i]

    # ---- 定点量化 ----
    def quantize(self, vec: Iterable[float]) -> np.ndarray:
        return np.rint(np.asarray(vec, dtype=np.float64) * self.scale
                       ).astype(np.int64)

    def dequantize(self, ints: np.ndarray) -> np.ndarray:
        return ints.astype(np.float64) / self.scale

    # ---- 哈希 ----
    def hash_ints(self, x: np.ndarray) -> int:
        """整数向量哈希（验证式 17 直接用这个：H(Σ_v q_v)）。"""
        h = 1
        mod_q = self.q if self.q is not None else None
        for i, e in enumerate(x):
            e = int(e)
            if mod_q is not None:
                e %= mod_q
            h = (h * pow(self._generator(i), e, self.p)) % self.p
        return h

    def hash(self, vec: Iterable[float]) -> int:
        return self.hash_ints(self.quantize(vec))

    # ---- 聚合（乘性同态，HH.Eval）----
    def eval(self, hashes: Iterable[int]) -> int:
        h = 1
        for hv in hashes:
            h = (h * int(hv)) % self.p
        return h

    # ---- 便捷：验证式(17)：Π_v H(q_v) =?= H(Σ_v q_v) ----
    def verify_aggregate(self, hashes: List[int], summed: np.ndarray) -> bool:
        return self.eval(hashes) == self.hash_ints(summed)
