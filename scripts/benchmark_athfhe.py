"""
scripts/benchmark_athfhe.py：AThFHE 三方案同参数通信量对比基准（论文表）

纯协议级（不训练）：相同合成梯度输入下跑
  - ATASSES 基线 = HierATHFHE(num_clusters=1)（论文 USENIX Sec'25 原版：
    单级 (T,N)-Shamir + BFV 内层两回合的忠实复现）
  - 方案一     = HierATHFHE(num_clusters=2)（分簇层次，跨 WAN 项降为 O(C·K)）
  - 方案二     = TwinATHFHE（双服务器种子掩码、不升环、单轮）

统一参数：N ∈ {6, 12, 24}（T=N/2，dropout=0），M=64、outer 60bit、
scale 40、inner_noise 20、smudge 10，τ=16 块 × M/2 维随机梯度（seed 固定）。
每组输出逐流程通信表、总通信字节、等效真实 218-bit 折算、crypto_time，
最后汇总论文样式 Markdown 对比表与增长斜率结论。

用法：python scripts/benchmark_athfhe.py
"""

import math
import sys
import time
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import numpy as np

from athfhe.hier import HierATHFHE
from athfhe.twin import TwinATHFHE

# ---- 统一参数 ----
M = 64                 # 环次数（M/2 = 每块打包维度）
TAU = 16               # 块数 τ
OUTER_BITS = 60
SCALE_BITS = 40
INNER_NOISE_BITS = 20
SMUDGE_BITS = 10
SEED = 42
N_LIST = (6, 12, 24)
REAL_COEFF_BITS = 218  # 等效真实 CKKS 折算（0 = 关闭）

HIER_FLOW = (('athfhe_comm_p1_upload', '阶段1 密文上传 P→CH/CS'),
             ('athfhe_comm_p1_broadcast_c1', '阶段1 广播 c1'),
             ('athfhe_comm_p2_ct', '阶段2 内层双密文 P→CH'),
             ('athfhe_comm_p2_ek', '阶段2 ek 份额 P→CH'),
             ('athfhe_comm_p3_dkshare', '阶段3 dkShare 节点→CH'),
             ('athfhe_comm_p3_bc', '阶段3 簇级份额 CH→CS'))
TWIN_FLOW = (('athfhe_comm_p1_upload', '阶段1 密文上传 P_i→S_A'),
             ('athfhe_comm_p1_broadcast_c1', '阶段1 广播 c1'),
             ('athfhe_comm_p2_mask', '阶段2 掩码密文 P_i→S_A'),
             ('athfhe_comm_p2_seed', '阶段2 种子 P_i→S_H'),
             ('athfhe_comm_p3_list', '阶段3 在线名单 S_A→S_H'),
             ('athfhe_comm_p3_dk', '阶段3 联合密钥 dk S_H→S_A'))


def _grad_chunks(n_clients, n_blocks, slots, seed=SEED):
    rng = np.random.RandomState(seed)
    return [[rng.uniform(-0.5, 0.5, size=slots) for _ in range(n_blocks)]
            for _ in range(n_clients)]


def _fmt(n):
    return f"{n:>12,}"


def _fmt_mb(n):
    return f"{n / (1024 * 1024):>8.2f}"


def run_one(kind, n_clients, threshold):
    """跑单协议实例，返回 (cost, crypto_time, 描述)"""
    kw = dict(ring_degree=M, outer_modulus_bits=OUTER_BITS,
              scale_bits=SCALE_BITS, smudge_bits=SMUDGE_BITS, seed=SEED,
              parallel=True, parallel_workers=4)
    if kind == 'atasses':
        proto = HierATHFHE(num_clusters=1, cluster_threshold=threshold,
                           inner_noise_bits=INNER_NOISE_BITS,
                           cluster_sizes=[n_clients], **kw)
        desc = f'Hier C=1（ATASSES 基线）'
    elif kind == 'hier':
        base, rem = divmod(n_clients, 2)
        proto = HierATHFHE(num_clusters=2, cluster_threshold=threshold,
                           inner_noise_bits=INNER_NOISE_BITS,
                           cluster_sizes=[base + (1 if c < rem else 0)
                                          for c in range(2)], **kw)
        desc = f'Hier C=2（方案一）'
    else:
        proto = TwinATHFHE(threshold=threshold, num_clients=n_clients, **kw)
        desc = 'Twin（方案二）'
    chunks = _grad_chunks(n_clients, TAU, M // 2)
    t0 = time.time()
    agg, cost = proto.run_protocol(chunks)
    crypto = time.time() - t0
    proto._close_pool()
    return proto, cost, crypto, desc


def real_factor(q_bits):
    if REAL_COEFF_BITS <= 0:
        return 0.0
    return math.ceil(REAL_COEFF_BITS / 8) / math.ceil(q_bits / 8)


def main():
    print(f"# AThFHE 三方案同参数通信量对比（M={M}, τ={TAU}, "
          f"Q=2^{OUTER_BITS}, B'=2^{INNER_NOISE_BITS}, B_sm=2^{SMUDGE_BITS}, "
          f"dropout=0, seed={SEED}）")
    summary = {}   # (kind, N) -> dict
    for n in N_LIST:
        T = max(2, n // 2)
        print(f"\n## N={n}（T={T}）")
        for kind in ('atasses', 'hier', 'twin'):
            proto, cost, crypto, desc = run_one(kind, n, T)
            q_bits = cost['athfhe_Q_bits']
            fac = real_factor(q_bits)
            total = cost['athfhe_comm_total_bytes']
            setup = cost['athfhe_setup_bytes']
            summary[(kind, n)] = dict(total=total, setup=setup,
                                      real=total * fac, crypto=crypto,
                                      q_bits=q_bits)
            flow = TWIN_FLOW if kind == 'twin' else HIER_FLOW
            print(f"\n### {desc}  N={n}")
            print(f"| 流程 | 通信量(字节) |")
            print(f"|---|---|")
            for k, label in flow:
                print(f"| {label} | {_fmt(cost[k])} |")
            print(f"| 阶段0 Setup（一次性） | {_fmt(setup)} |")
            print(f"\n- 总通信（逐流程和）：{total:,} B ({total / (1024 * 1024):.2f} MB)"
                  f"；等效真实 {REAL_COEFF_BITS}-bit CKKS：{total * fac:,.0f} B"
                  f"（系数 {fac:.2f}，仿真 Q={q_bits}bit）")
            print(f"- crypto_time：{crypto:.2f}s")

    print("\n## 汇总对比表（论文样式）")
    hdr = "| 方案 \\ N |" + "".join(f" {n}（总通信 / 等效真实 / 耗时） |"
                                   for n in N_LIST)
    print(hdr)
    print("|---|" + "---|" * len(N_LIST))
    names = {'atasses': 'ATASSES（论文基线）', 'hier': '方案一（hier C=2）',
             'twin': '方案二（twin）'}
    for kind in ('atasses', 'hier', 'twin'):
        row = f"| {names[kind]} |"
        for n in N_LIST:
            s = summary[(kind, n)]
            row += (f" {s['total'] / (1024 * 1024):.2f} MB / "
                    f"{s['real'] / (1024 * 1024):.2f} MB / "
                    f"{s['crypto']:.2f}s |")
        print(row)
    print(f"\n> 等效真实 CKKS 折算系数 = ceil({REAL_COEFF_BITS}/8)/ceil(Q_bits/8)"
          f"（Q={summary[('atasses', N_LIST[0])]['q_bits']}bit → 系数 "
          f"{real_factor(summary[('atasses', N_LIST[0])]['q_bits']):.2f}）；"
          f"仿真 numpy 大整数记账 ≠ 真实实现通信。")

    print("\n## 增长斜率结论")
    for kind in ('atasses', 'hier', 'twin'):
        t6 = summary[(kind, 6)]['total']
        t24 = summary[(kind, 24)]['total']
        c6 = summary[(kind, 6)]['crypto']
        c24 = summary[(kind, 24)]['crypto']
        print(f"- {names[kind]}：通信 N=24/N=6 = {t24 / t6:.2f}×"
              f"（线性预期 4×）；耗时 N=24/N=6 = {c24 / c6:.2f}×")


if __name__ == '__main__':
    main()
