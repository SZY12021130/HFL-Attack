"""
任意长度秘密的 Shamir (t,n) 分块分片/恢复
=============================================

直接复用 ppbr/sa.py 的 shamir_split / shamir_recover（素数域 2^521−1，
单段秘密 ≤64 字节）。本模块在其上处理"秘密任意长"的分块与按客户端聚合：

- 分片：秘密按 64 字节分块，每块独立 shamir_split，把同一客户端在所有
  块上的 (x,y) 对聚合为一个份额 blob（自定义简单二进制格式）；
- 恢复：任取 ≥t 个 blob，逐块 shamir_recover 后按原长度拼接；<t 个抛
  ValueError（t 内嵌于 blob 头，无需调用方传入）。

blob 二进制布局（大端）::

    偏移  长度  字段
    0     4    魔数 b"THS1"
    4     2    t        门限（uint16）
    6     4    total_len 原始秘密总长度（uint32）
    10    2    num_blocks 块数（uint16）
    12    2    block_len  标准块长（uint16，恒 64）
    14    num_blocks × 记录，每条记录：
          2    block_idx（uint16）
          1    x         求值点（1..n）
          66   y         份额值（定长，y < 2^521）

    单 blob 大小 = 14 + num_blocks × 69 字节。
"""

import struct
from typing import List, Sequence, Tuple

from base.sa import shamir_split, shamir_recover, _SHAMIR_P

_BLOCK_LEN = 64   # ppbr/sa.py 单段秘密上限（2^512 内，域 2^521−1）
_SHARE_LEN = 66   # 份额 y 值编码长度（ceil(521/8)，y ∈ [0, 2^521−1)）
_MAGIC = b"THS1"
_HEADER = struct.Struct(">4sH I H H")   # magic, t, total_len, num_blocks, block_len
_RECORD = struct.Struct(">HB66s")       # block_idx, x, y


def split_secret(secret: bytes, n: int, t: int) -> List[bytes]:
    """
    把任意长度秘密分成 n 份，任意 ≥t 份可恢复，<t 份信息论独立。

    Args:
        secret: 任意长度秘密字节
        n:      份额总数
        t:      恢复门限

    Returns:
        n 个份额 blob（bytes；索引 = 客户端编号 −1）

    Example:
        >>> blobs = split_secret(b"A" * 1000, n=10, t=4)
        >>> len(blobs)
        10
        >>> recover_secret([blobs[0], blobs[3], blobs[5], blobs[9]]) == b"A" * 1000
        True
    """
    if not (n >= t >= 1):
        raise ValueError(f"非法分片参数 n={n}, t={t}")
    if len(secret) > 0xFFFFFFFF:
        raise ValueError("秘密过长（>4GiB）")
    blocks = [secret[i:i + _BLOCK_LEN]
              for i in range(0, len(secret), _BLOCK_LEN)]
    # 每块独立 (t,n) 分片：block_shares[b] = [(x_1,y_1), ..., (x_n,y_n)]
    block_shares: List[List[Tuple[int, int]]] = [
        shamir_split(blk, n, t) for blk in blocks
    ]
    blobs = []
    for c in range(n):
        header = _HEADER.pack(_MAGIC, t, len(secret), len(blocks), _BLOCK_LEN)
        records = b"".join(
            _RECORD.pack(b, block_shares[b][c][0],
                         block_shares[b][c][1].to_bytes(_SHARE_LEN, "big"))
            for b in range(len(blocks))
        )
        blobs.append(header + records)
    return blobs


def parse_blob(blob: bytes) -> Tuple[int, int, List[int]]:
    """
    解析单个份额 blob，返回 (x_i, t, 各块的 y 值列表)。

    供门限部分解密使用：份额的 y 值本身就是 Shamir 份额，直接模 q 约化
    即可作为份额多项式的系数，无需重构秘密（见 threshold/toy_ckks.py）。
    """
    if len(blob) < _HEADER.size:
        raise ValueError("份额 blob 过短（头部不完整）")
    magic, t, total_len, num_blocks, block_len = _HEADER.unpack_from(blob)
    if magic != _MAGIC:
        raise ValueError("非法份额 blob（魔数不匹配）")
    if block_len != _BLOCK_LEN:
        raise ValueError(f"非法标准块长 {block_len}")
    expect = _HEADER.size + num_blocks * _RECORD.size
    if len(blob) != expect:
        raise ValueError(f"份额 blob 长度不符：期望 {expect}，实得 {len(blob)}")
    ys: List[int] = []
    x_val = 0
    for b in range(num_blocks):
        block_idx, x, y = _RECORD.unpack_from(blob, _HEADER.size + b * _RECORD.size)
        if block_idx != b:
            raise ValueError(f"份额 blob 块序错乱（期望 {b}，实得 {block_idx}）")
        if x_val == 0:
            x_val = x
        elif x != x_val:
            raise ValueError("同一 blob 内出现不同的求值点 x")
        ys.append(int.from_bytes(y, "big"))
    return x_val, t, ys


def recover_secret(share_blobs: Sequence[bytes]) -> bytes:
    """
    从 ≥t 个份额 blob 恢复秘密（使用全部给定 blob 做逐块插值）。

    Args:
        share_blobs: 份额 blob 列表（≥t 个；t 内嵌于 blob 头）

    Returns:
        恢复的秘密字节（与原始秘密逐字节相等）

    Raises:
        ValueError: blob 数 < 内嵌门限 t，或各 blob 元信息不一致

    Example:
        >>> blobs = split_secret(b"secret-bytes", n=5, t=3)
        >>> recover_secret(blobs[:2])
        Traceback (most recent call last):
            ...
        ValueError: 份额不足：需要 ≥3 份，实得 2 份
    """
    parsed = [parse_blob(b) for b in share_blobs]
    if not parsed:
        raise ValueError("未提供任何份额 blob")
    t = parsed[0][1]
    if len(parsed) < t:
        raise ValueError(f"份额不足：需要 ≥{t} 份，实得 {len(parsed)} 份")
    if len({x for x, _, _ in parsed}) != len(parsed) \
            and any(ys for _, _, ys in parsed):
        raise ValueError("份额 blob 中存在重复的求值点 x")
    num_blocks = (len(share_blobs[0]) - _HEADER.size) // _RECORD.size
    total_len = _HEADER.unpack_from(share_blobs[0])[2]
    block_len = _BLOCK_LEN
    for other in share_blobs[1:]:
        h = _HEADER.unpack_from(other)
        if h[3] != num_blocks or h[2] != total_len:
            raise ValueError("份额 blob 元信息不一致（可能来自不同秘密）")
    out = bytearray()
    for b in range(num_blocks):
        shares = [(x, ys[b]) for x, _, ys in parsed]
        length = block_len if b < num_blocks - 1 \
            else total_len - block_len * (num_blocks - 1)
        out.extend(shamir_recover(shares, length))
    return bytes(out)


# 兼容旧接口名（测试/外部脚本可能引用）
blob_share_values = parse_blob

__all__ = ['split_secret', 'recover_secret', 'parse_blob',
           'blob_share_values', '_SHAMIR_P']
