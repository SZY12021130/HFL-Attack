"""
athfhe/：外层 CKKS × 内层 ATASSES（USENIX Sec'25，任意门限 FHE）两扩展方案
=====================================================================

- ckks_toy.py：公共外层——分圆环运算（limb 数参数化的 FFT negacyclic 乘法）、
  素数筛、教科书单模数 CKKS（系数编码）、按系数 Shamir 分片/Lagrange。
- hier.py：方案一 分簇层次架构（Edge-Assisted Hierarchical CKKS-ATASSES），
  内层升环 R_{M,Q'}（Q' > 2·n_c·Q²·B'），解密份额 Δ' 编码 + 取整去噪，
  内层底噪 100% 消除；支持 T1≠T2 异步掉线。
- twin.py：方案二 非共谋双服务器架构（Twin-Server Non-Colluding
  CKKS-ATASSES），PRG 种子展开均匀随机掩码私钥，(a'u')·ek 即完美一次性
  掩码——不升环、无取整，单轮发完即走。

两条方案均以普通 Python 类模拟实体（无网络），并由 athfhe/track.py
注册为实验轨道（HierAthfheTrack / TwinAthfheTrack）。

导入纪律：本包静态导入仅限 base 与第三方（numpy/hashlib/标准库）；
与 ckks.client/server 等的接线在 track.py 函数内惰性完成（照 ppbr/rfl 惯例）。
"""

from athfhe.ckks_toy import (
    RingContext,           # 环 R_{M,Q} 上下文（模数/次数/ limb 配置，环运算为其方法）
    next_prime,            # 素数筛（Miller-Rabin）
    outer_keygen, outer_encrypt, outer_add, outer_decrypt_core,
    shamir_share, shamir_recover, shamir_recover_scalar_secret,
    lagrange_coeffs,
)

__all__ = [
    'RingContext', 'next_prime',
    'outer_keygen', 'outer_encrypt', 'outer_add', 'outer_decrypt_core',
    'shamir_share', 'shamir_recover', 'shamir_recover_scalar_secret',
    'lagrange_coeffs',
]
