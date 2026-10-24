"""
SA 层：Bonawitz'17 风格安全聚合（真实密钥协商 + Shamir 掉线恢复）
================================================================

借鉴的开源实现：
- ammartahir24/SecureAggregation（Bonawitz 论文的 Python 复现）：阶段划分
  （广播公钥 → 掩码上传 → 揭示）与成对掩码方向约定直接沿用；
- Flower (adap/flower) SecAgg+ 示例：X25519 + HKDF-SHA256 的密钥协商
  （替代参考实现中的玩具模幂 DH），现代且与生产实现一致；
- INRIA Declearn secagg.masking：PRG 掩码控制器设计。

掩码约定（对齐 PPBR 论文 [He 等 TMC 2026] 的 SA 描述）：
    z_j = input_j + Σ_{j'>j} PRG(s_{j,j'}) − Σ_{j'<j} PRG(s_{j',j}) + PRG(b_j)
  s 为 X25519+HKDF 导出的成对种子（双方各自计算 DH，输出一致），
  b_j 为自负载掩码种子（15 字节，适配 Shamir 素数域 2^127−1）。

相对参考实现补全的机制：
- b_j 以 Shamir (t, n−1) 分片发给同组其余参与方：掉线方已提交掩码输入但
  未揭示时，边缘用 ≥t 份份额重建 b_j 摘除其自负载掩码（Bonawitz 原文机制，
  ammartahir24 参考实现省略了 Shamir）；
- 单进程仿真中"广播/发份额"直接传对象，密码学计算（X25519、HKDF、
  Shamir）全部真实执行。

掉线两阶段语义（与 Bonawitz 一致）：
- 提交前掉线（early）：其 z 不在部分和中，幸存方揭示与该掉线方的成对掩码
  和（带方向），边缘扣除；
- 提交后掉线（late）：其 z 已在部分和中，边缘用 ≥t 份份额重建 b_j 摘除
  自负载掩码，该客户端的输入仍计入聚合。
"""

import hashlib
import secrets
from typing import Dict, List, Sequence, Set, Tuple

import numpy as np
import torch

# Shamir 素数域：2^521 − 1（Mersenne 素数）；支持 ≤64 字节秘密
# （b 种子 15 字节、X25519 私钥 32 字节均可整段分片）
_SHAMIR_P = 2 ** 521 - 1
_B_LEN = 15          # 自负载掩码种子长度（字节）
_SK_LEN = 32         # X25519 私钥长度（字节）
_PAIR_SEED_LEN = 32  # 成对种子长度（HKDF 输出）


# =====================================================================
# PRG：种子 → 伪随机向量（通信双方各自本地生成同一条掩码）
# =====================================================================
def prg_from_seed(seed: bytes, dim: int) -> np.ndarray:
    """SHA256 压缩到 int64 后驱动 torch.Generator（双方输出一致）"""
    digest = hashlib.sha256(seed).digest()
    g = torch.Generator().manual_seed(int.from_bytes(digest[:8], "little"))
    return torch.randn(dim, generator=g, dtype=torch.float64).numpy()


def _new_b_seed() -> bytes:
    return secrets.token_bytes(_B_LEN)


# =====================================================================
# X25519 + HKDF 密钥协商
# =====================================================================
def _hkdf(shared: bytes, info: str, length: int = _PAIR_SEED_LEN) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    return HKDF(algorithm=hashes.SHA256(), length=length,
                salt=None, info=info.encode()).derive(shared)


class SAKeyPair:
    """参与方的临时 X25519 密钥对（每组/每轮新生成，具前向安全性）"""

    def __init__(self):
        from cryptography.hazmat.primitives.asymmetric.x25519 import \
            X25519PrivateKey
        self._sk = X25519PrivateKey.generate()

    def public_bytes(self) -> bytes:
        from cryptography.hazmat.primitives import serialization
        return self._sk.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw)

    def private_bytes(self) -> bytes:
        """原始私钥字节（用于 Bonawitz 式 Shamir 私钥备份分片）"""
        from cryptography.hazmat.primitives import serialization
        return self._sk.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption())

    @staticmethod
    def from_private(raw: bytes) -> 'SAKeyPair':
        from cryptography.hazmat.primitives.asymmetric.x25519 import \
            X25519PrivateKey
        kp = SAKeyPair.__new__(SAKeyPair)
        kp._sk = X25519PrivateKey.from_private_bytes(raw)
        return kp

    def derive_seed(self, peer_public: bytes, info: str) -> bytes:
        """DH + HKDF：双方输入同一 info 导出同一成对种子"""
        from cryptography.hazmat.primitives.asymmetric.x25519 import \
            X25519PublicKey
        peer = X25519PublicKey.from_public_bytes(peer_public)
        shared = self._sk.exchange(peer)
        return _hkdf(shared, info)


# =====================================================================
# Shamir (t, n) 秘密共享（拉格朗日插值，素数域 2^127−1）
# =====================================================================
def shamir_split(secret: bytes, n: int, t: int) -> List[Tuple[int, int]]:
    """
    把 secret（≤64 字节）分成 n 份，任意 ≥t 份可恢复。

    Returns:
        [(x_i, y_i)]，x_i = 1..n，按目标参与方排序后逐一下发
    """
    s = int.from_bytes(secret, "big")
    assert s < _SHAMIR_P and n >= t >= 1, "秘密超长或分片参数非法"
    coeffs = [s] + [secrets.randbelow(_SHAMIR_P) for _ in range(t - 1)]
    shares = []
    for x in range(1, n + 1):
        y = 0
        for c in reversed(coeffs):  # Horner 法
            y = (y * x + c) % _SHAMIR_P
        shares.append((x, y))
    return shares


def shamir_recover(shares: Sequence[Tuple[int, int]],
                   length: int) -> bytes:
    """拉格朗日插值在 x=0 处求值，恢复 secret（调用方保证 ≥t 份）"""
    acc = 0
    for i, (xi, yi) in enumerate(shares):
        num, den = 1, 1
        for j, (xj, _) in enumerate(shares):
            if i == j:
                continue
            num = num * (-xj) % _SHAMIR_P
            den = den * (xi - xj) % _SHAMIR_P
        acc = (acc + yi * num * pow(den, _SHAMIR_P - 2, _SHAMIR_P)) % _SHAMIR_P
    return acc.to_bytes(length, "big")


def sa_threshold(num_members: int) -> int:
    """Bonawitz 式阈值：默认半数，至少 2（组太小时退化但不失效）"""
    return max(1, min(num_members - 1, max(2, (num_members - 1) // 2)))


# =====================================================================
# 参与方（客户端或边缘）侧
# =====================================================================
class SAMember:
    """
    一个 SA 参与方在某分组内的轮内状态。

    阶段 1：构造时生成临时密钥对，`register_peers` 模拟公钥广播并导出
    成对种子；阶段 2：`masked_input` 生成掩码上传与 b 的 Shamir 分片；
    阶段 3：`reveal_self` / `reveal_pairwise_sum` 生成揭示消息。
    """

    def __init__(self, member_id: int, group_tag: str, round_num: int,
                 threshold: int):
        self.member_id = member_id
        self.group_tag = group_tag
        self.round_num = round_num
        self.threshold = max(1, threshold)
        self.keypair = SAKeyPair()
        self.b_seed: bytes = b""
        self._pair_seed: Dict[int, bytes] = {}
        self._peers: List[int] = []

    # ---- 阶段 1：密钥交换 ----
    def public_key(self) -> bytes:
        return self.keypair.public_bytes()

    def register_peers(self, pubkeys: Dict[int, bytes]) -> None:
        """公钥经边缘广播后，与每个同组参与方导出成对种子"""
        for pid, pk in pubkeys.items():
            if pid == self.member_id:
                continue
            self._pair_seed[pid] = self.keypair.derive_seed(
                pk, self._pair_info(pid))
        self._peers = sorted(self._pair_seed)

    def _pair_info(self, pid: int) -> str:
        return (f"ppbr-sa-{self.group_tag}-"
                f"{min(self.member_id, pid)}-{max(self.member_id, pid)}-"
                f"{self.round_num}")

    def share_private_key(self) -> Dict[int, Tuple[int, int]]:
        """
        Bonawitz 私钥备份：把本轮 DH 私钥以 Shamir (t,n−1) 分片发给
        同组其余参与方。任一掉线方的私钥可由 ≥t 份份额重建，
        从而重算其全部成对掩码（这是掉线容忍的关键机制）。
        """
        sk = self.keypair.private_bytes()
        shares = shamir_split(sk, len(self._peers), self.threshold)
        return {pid: sh for pid, sh in zip(self._peers, shares)}

    # ---- 阶段 2：掩码上传 ----
    def masked_input(self, vec: np.ndarray
                     ) -> Tuple[np.ndarray, Dict[int, Tuple[int, int]]]:
        """
        z = input + Σ_{j'>j} PRG(s) − Σ_{j'<j} PRG(s) + PRG(b_j)

        Returns:
            (z, {peer_id: b 的 Shamir 份额})：份额数 = 同组其余参与方数
        """
        dim = len(vec)
        z = np.asarray(vec, dtype=np.float64).copy()
        for pid in self._peers:
            r = prg_from_seed(self._pair_seed[pid], dim)
            z += r if self.member_id < pid else -r
        self.b_seed = _new_b_seed()
        z += prg_from_seed(self.b_seed, dim)
        shares = shamir_split(self.b_seed, len(self._peers), self.threshold)
        return z, {pid: sh for pid, sh in zip(self._peers, shares)}

    # ---- 阶段 3：揭示 ----
    def reveal_self(self, dim: int) -> np.ndarray:
        """幸存方揭示自己的自负载掩码（聚合未完成前单独无意义）"""
        assert self.b_seed, "masked_input 尚未调用"
        return prg_from_seed(self.b_seed, dim)


# =====================================================================
# 边缘（聚合方）侧
# =====================================================================
class SABucket:
    """
    边缘侧的 SA 编排（Bonawitz 聚合服务器角色；PPBR 中边缘只见聚合
    结果——与论文"边缘只见掩码态个体输入与聚合均值"一致）。

    时序：
        bucket = SABucket(tag, round, threshold)
        for m in members: bucket.add_member(m.member_id, m.public_key())
        for m in members:
            m.register_peers(bucket.pubkeys())
            sk_shares[m.id] = m.share_private_key()      # 私钥备份分片
        # 掉线判定（提交前）
        for j in early: bucket.mark_early_dropout(j)
        for j in surviving:
            z, b_shares[j] = m_j.masked_input(vec_j)
            bucket.submit(j, z)
        for j in late: bucket.mark_late_dropout(j, z_j)   # 已提交未揭示
        total, n = bucket.aggregate(
            revealers={j: m_j.reveal_self(dim)},
            shares_sk={j: [幸存方代持的 j 的私钥份额, ≥t 份]},
            shares_b={j: [幸存方代持的 j 的 b 份额, ≥t 份]})
    """

    def __init__(self, group_tag: str, round_num: int, threshold: int):
        self.group_tag = group_tag
        self.round_num = round_num
        self.threshold = max(1, threshold)
        self._pubkeys: Dict[int, bytes] = {}
        self._submitted: Dict[int, np.ndarray] = {}
        self._early: Set[int] = set()
        self._late: Dict[int, np.ndarray] = {}

    # ---- 阶段 1：公钥收集（模拟广播） ----
    def add_member(self, member_id: int, pubkey: bytes) -> None:
        self._pubkeys[member_id] = pubkey

    def pubkeys(self) -> Dict[int, bytes]:
        return dict(self._pubkeys)

    @property
    def member_ids(self) -> List[int]:
        return sorted(self._pubkeys)

    # ---- 掉线标记 ----
    def mark_early_dropout(self, member_id: int) -> None:
        """提交前掉线：未上传 z，其输入不计入聚合"""
        self._early.add(member_id)

    def mark_late_dropout(self, member_id: int, z: np.ndarray) -> None:
        """提交后掉线：z 已在部分和中，其输入仍计入聚合（Bonawitz 语义）"""
        self._late[member_id] = z

    def submit(self, member_id: int, z: np.ndarray) -> None:
        self._submitted[member_id] = z

    def _pair_info(self, a: int, b: int) -> str:
        return (f"ppbr-sa-{self.group_tag}-{min(a, b)}-{max(a, b)}-"
                f"{self.round_num}")

    # ---- 聚合 ----
    def aggregate(self,
                  revealers: Dict[int, np.ndarray],
                  shares_sk: Dict[int, List[Tuple[int, int]]],
                  shares_b: Dict[int, List[Tuple[int, int]]],
                  dim: int) -> Tuple[np.ndarray, int]:
        """
        恢复幸存参与方（含 late dropout）的输入之和——Bonawitz 私钥
        恢复路径：任一掉线方的全部掩码项都可由其重建的 DH 私钥重算扣除，
        与掉线发生早晚、掉线方之间是否互为对偶无关。

        Args:
            revealers: {幸存方 id: PRG(b_j)}（自负载掩码揭示）
            shares_sk: {掉线方 id: [≥t 份 DH 私钥份额]}
            shares_b:  {late 掉线方 id: [≥t 份 b 份额]}
        Returns:
            (输入之和, 计入聚合的参与方数)
        """
        total = np.zeros(dim)
        n_counted = 0
        for z in list(self._submitted.values()) + list(self._late.values()):
            total += z
            n_counted += 1
        # 1) 摘除幸存方自负载掩码
        for b_vec in revealers.values():
            total -= b_vec
        # 2) 掉线方：重建私钥，按无序对逐对扣除成对掩码——
        #    对 (a,b)（a<b）的掩码 r 出现在 z_a（+r）与 z_b（−r）两处，
        #    谁提交了 z 就摘谁的项，每对最多摘两次、绝不重复；
        #    late 掉线方另需重建 b 摘除自负载掩码（其输入计入聚合）
        dropouts = set(self._early) | set(self._late)
        recovered: Dict[int, 'SAKeyPair'] = {}
        for d in sorted(dropouts):
            sh = shares_sk.get(d, [])
            assert len(sh) >= self.threshold, \
                f"掉线方 {d} 的私钥份额不足 {self.threshold}（实得 {len(sh)}）"
            recovered[d] = SAKeyPair.from_private(shamir_recover(sh, _SK_LEN))
        counted = set(self._submitted) | set(self._late)
        handled: Set[Tuple[int, int]] = set()
        for d in sorted(dropouts):
            sk = recovered[d]
            for x in self.member_ids:
                if x == d:
                    continue
                pair = (min(d, x), max(d, x))
                if pair in handled:
                    continue
                handled.add(pair)
                r = prg_from_seed(
                    sk.derive_seed(self._pubkeys[x],
                                   self._pair_info(*pair)), dim)
                a, b = pair
                if a in counted:
                    total -= r          # z_a 中的 +r
                if b in counted:
                    total += r          # z_b 中的 −r
        for d in sorted(self._late):
            shb = shares_b.get(d, [])
            assert len(shb) >= self.threshold, \
                f"掉线方 {d} 的 b 份额不足 {self.threshold}（实得 {len(shb)}）"
            total -= prg_from_seed(shamir_recover(shb, _B_LEN), dim)
        return total, n_counted

    @property
    def early_dropped(self) -> Set[int]:
        return set(self._early)

    @property
    def late_dropped(self) -> Set[int]:
        return set(self._late)
