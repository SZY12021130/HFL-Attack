# AThFHE：外层 CKKS × 内层 ATASSES 的两扩展方案（USENIX Sec'25）

本文档说明 `athfhe/` 包的两个联邦学习扩展方案——**方案一：分簇层次架构**
（Edge-Assisted Hierarchical CKKS-ATASSES，`athfhe/hier.py`）与**方案二：
非共谋双服务器架构**（Twin-Server Non-Colluding CKKS-ATASSES，
`athfhe/twin.py`）。两者都把 USENIX Security 2025 论文 *ATASSES*（任意门限
全同态加密，Go 参考实现见 Flute-81/PPSBA 的 `code/ApproxSS/`）的
「联合解密密钥 dk = S1 + S2 消元」机制嵌入外层 CKKS 梯度聚合的解密阶段，
实现**服务器全程拿不到私钥份额、客户端掉线容忍**的门限解密。

实现为 numpy 大整数仿真（CPU，无网络），外层是教科书单模数 CKKS（系数编码
简化，代数与 canonical embedding 版本完全一致），技术方案改写自
`threshold/toy_ckks.py`（limb 数参数化以支持内层大模数）。

## 0. 两圈加密各管什么

| 对比项 | 外层（CKKS） | 内层（ATASSES，BFV 式） |
|---|---|---|
| 加密对象 | 浮点梯度 g | 部分解密份额 b=c1·s 与掩码噪声 n |
| 使用阶段 | 全程（训练上传 → 密文聚合 → 最终解码） | 仅解密阶段两回合 |
| 多项式环 | R_{M,Q} | 方案一 R_{M,Q'}（Q'>Q，升环）；方案二复用 R_{M,Q} |
| 缩放与取整 | Δ_ckks 放大浮点，解码保留 ~1e−8 误差 | Δ'=⌊Q'/Q⌋ 放大整数，取整后底噪 **100% 消除** |
| 密钥 | 全局总私钥 S（簇间加法拆分 + 簇内 Shamir） | 每轮一次性密钥 ek1/ek2（仅簇内 Shamir / PRG 种子） |
| 保护目标 | 梯度数据的机密性 | 私钥份额的机密性（防止从份额反推密钥） |

一句话：**外层管「数据」（梯度），内层管「钥匙」（解密份额）**。内层加密
结束后份额取整还原成模 Q 的多项式交给云服务器，内层随即退场，外层继续
正常解码。

## 1. 方案一：分簇层次架构（athfhe_hier）

### 实体与威胁模型

云服务器 CS、C 个簇头 CH_c（边缘）、N 个客户端 P_{c,i}（第 c 簇 n_c 个，
Σn_c=N）。CS、所有 CH_c 及被腐化终端均为半诚实（honest-but-curious），
试图从交互视图反推私有梯度或外层总私钥 S；允许 CS、任意数量 CH_c 与每簇
最多 t_c−1 个腐化终端合谋（每簇至少 1 个诚实在线终端）。每轮解密阶段允许
每簇最多 n_c−t_c 个终端异步掉线，且**第一回合在线集 T1 与第二回合 T2
可以不同**（|T1|,|T2| ≥ t_c 即可）。

### 四阶段流程

- **阶段0 Hier-CKKS-Setup**：全局外层私钥 S 按**簇间加法秘密共享**拆为
  C 个簇级子私钥 S^(c)（前 C−1 个均匀随机、第 C 个补齐，S=ΣS^(c) mod Q）；
  每簇对内做 (t_c,n_c)-Shamir 秘密共享（公开评估点 α_{c,i}=1..n_c，模数取
  素数 Q 保证 Lagrange 分母可逆），客户端 P_{c,i} 持份额 s_{c,i}。
  两级重构闭环：Σ_{i∈T1} L^{(T1)}_{c,i}·s_{c,i} = S^(c)，Σ_c S^(c) = S。
- **阶段1 Outer-CKKS-Agg**：客户端把本地梯度块编码 m=round(Δ_ckks·g) 并用
  全局公钥 PK=(p0,p1)=(−a·S+e_pk, a) 加密（c0=p0·u+m+e0，c1=p1·u+e1），
  上传 CH_c；CH_c 簇内密文相加、CS 全量相加得 CT=(c0,c1)，满足
  c0+c1·S = M_agg + e_CT (mod Q)。CS 广播 c1 请求协同计算 b=c1·S。
- **阶段2 Cluster-R1**（在线集 T1）：客户端算外层局部解密份额
  b_{c,i}=c1·s_{c,i} mod Q；在更大环 R_{M,Q'} 上采样两把内层私钥
  ek_{c,i,1},ek_{c,i,2}（各自再做簇内 (t_c,n_c)-Shamir 分片
  ekShare^{(c)}_{j,i,1/2}）、掩码噪声 n_{c,i}←[−B_sm,B_sm]^M 与内层底噪
  e'（界 B'）；CRS 生成本轮公共基底 −a'_c·u'_c；构造内层双密文
  （对应论文 Eq.6 的两通道）：

      CTb_{c,i} = ((a'_c·ek1)·u'_c + Δ'·b_{c,i} + e'_{1},  −a'_c·u'_c)   (mod Q')
      CTn_{c,i} = ((a'_c·ek2)·u'_c + Δ'·n_{c,i} + e'_{2},  −a'_c·u'_c)   (mod Q')

  上传 CH_c；CH_c 广播 Lagrange 系数 {L^{(T1)}_{c,i}}。
- **阶段3 Cluster-R2**（在线集 T2，允许 ≠T1）：节点 j 本地合成联合解密密钥
  的份额（对应论文 Eq.3 的两把聚合子密钥份额相加）：

      dkShare_{c,j} = Σ_{i∈T1} L_i·ekShare^{(c)}_{i,j,1}
                    + Σ_{i∈T1}      ekShare^{(c)}_{i,j,2}    (mod Q')

  发给 CH_c；CH_c 用 {dkShare_{c,j}}_{j∈T2} 做 x=0 处 Shamir 插值恢复

      dk_c = S^(c)_in,1 + S^(c)_in,2 = Σ_{i∈T1}(L_i·ek_{c,i,1}+ek_{c,i,2})

  合成簇内总密文首项 C'_all,c,0 = Σ_{i∈T1}(L_i·CTb_{c,i}[0]+CTn_{c,i}[0])，
  然后**取整去噪**（论文 Eq.7）：

      b'_c = ⌊(C'_all,c,0 + (−a'_c u'_c)·dk_c)/Δ'⌉ mod Q
           = c1·S^(c) + Σ_{i∈T1} n_{c,i}    (mod Q)

  Lagrange 系数会把内层底噪放大约 n_c·Q 倍，只要
  **Q' > 2·n_c·Q²·B'**（ATASSES 参数界；本文档 §3），放大后的总噪声
  ‖E'‖ < Δ'/2，取整时被 100% 抹除，b'_c 精确落回外层环 R_{M,Q}。
- **阶段4 Cloud-CKKS-Decode**：CS 计算 m' = c0 + Σ_c b'_c = M_agg + e_CT +
  Σ_{c,i} n_{c,i} (mod Q)，解码 G = center_lift(m')/Δ_ckks = Σg + (e_CT+Σn)/Δ。

### 安全性质（测试覆盖）

- CH_c 的视图只有内层密文与 b'_c，b'_c − c1·S^(c) = Σn 且
  ‖·‖∞ ≤ |T1|·B_sm（掩码噪声界，防份额泄露）；
- 内层解密精确性：b'_c − (c1·S^(c)+Σn) = 0 mod Q（底噪 100% 消除）；
- 异步掉线：T1≠T2 每簇各 ≥t_c 即可（`test_hier_async_dropout_T1_neq_T2`）。

## 2. 方案二：非共谋双服务器架构（athfhe_twin）

### 实体与威胁模型

N 个客户端、聚合服务器 S_A、辅助密钥服务器 S_H，双服务器**非共谋**且均
半诚实；允许任一服务器与最多 T−1 个腐化客户端合谋。客户端解密阶段
**单轮（One-Shot）发完即走**、零 P2P；任意最多 N−T 个客户端掉线
（在线集 T 满足 |T|≥T）。

### 四阶段流程

- **阶段0 Twin-CKKS-Setup**：S、PK 同方案一；每客户端持 S 的 (T,N)-Shamir
  份额 s_i（Σ_{i∈T} L_i·s_i = S）；CRS 每轮生成公共 a'·u'∈R_{M,Q}。
- **阶段1**：外层加密上传 S_A，同态加得 CT=(c0,c1)，广播 c1。
- **阶段2 Client-OneShot-Upload**（单轮）：b_i=c1·s_i、n_i←[−B_sm,B_sm]^M；
  采两个 128-bit 种子，PRG（`hashlib.shake_256` 字节流展开）在 R_{M,Q} 上
  展开两把**均匀随机**掩码私钥 ek_{i,1},ek_{i,2}。关键优势：因为 ek 均匀
  随机且每轮刷新，(a'u')·ek 直接构成环上的完美一次性掩码（PRF/OTP），
  **无需内层 RLWE 底噪 e'，也彻底省去内层模数 Q' 与缩放因子 Δ'**（与方案一
  的开销差异根源）。构造同环两份掩码密文：

      c'_{b,i,0} = (a'·ek_{i,1})·u' + b_i,   c'_{n,i,0} = (a'·ek_{i,2})·u' + n_i,
      c'_1       = −a'·u'    (均 mod Q)

  P_i→S_A 发 (c'_{b,i,0}, c'_{n,i,0})，P_i→S_H 经 TLS 安全信道发两个种子
  （仅 32 字节）。
- **阶段3 Twin-Server-Coop**：S_A 计算 L_i^(T)、
  C'_all,0 = Σ_{i∈T}(L_i·c'_{b,i,0}+c'_{n,i,0})、C'_all,1=−a'u'，把在线名单
  T 发 S_H；S_H **先校验 |T|≥T**（防 S_A 伪造名单套取个体密钥），展开种子算
  S1=Σ L_i·ek_{i,1}、S2=Σ ek_{i,2}，合成 dk=S1+S2 (mod Q) 发 S_A。
  由于 S2 未知，S_A 无法从 dk 分离 S1 或任何 ek_{i,1}。
- **阶段4 Final-CKKS-Decode**：b' = C'_all,0 + C'_all,1·dk = c1·S + Σn_i
  (mod Q)（掩码项精确消元，**全程无取整**）；m'=c0+b'；解码。S_A 无种子时
  用错误 dk 只能解出垃圾（`test_twin_wrong_dk_decrypts_garbage`）。

S_H 的视图只含种子与名单，无任何密文/份额（`test_twin_helper_view_only_seeds_and_roster`）。

## 3. 参数表与论文参数界

| 参数 | 含义 | 方案一默认 | 方案二默认 |
|---|---|---|---|
| M | 环次数（2 的幂），M/2 = 每块打包的梯度维度 | 256 | 256 |
| Q | 外层模数（>2^outer_modulus_bits 的最小素数） | 60 bit | 60 bit |
| Δ_ckks | 外层缩放 2^scale_bits（解码误差 ~(e+Σn)/Δ） | 2^40 | 2^40 |
| C / t_c | 簇数 / 每簇门限 | 2 / 2 | — |
| T | 全局门限 | — | 3 |
| B_sm | 掩码噪声界 2^smudge_bits | 2^10 | 2^10 |
| B' | 内层底噪界 2^inner_noise_bits | 2^20 | —（不需要） |
| Q' | 内层模数：> 2·n_c·Q²·B' 的最小素数 | ~140+ bit | —（不升环） |
| Δ' | ⌊Q'/Q⌋（内层整数缩放） | ~2^80 | — |

- **论文对应关系**：内层双密文构造对应 ATASSES Eq.6（两通道分别保护份额
  与噪声，共享第二项 −a'u'）；dk 的份额合成对应 Eq.3（S1、S2 两把聚合子
  密钥，dk=S1+S2）；取整去噪对应 Eq.7（除以 Δ' 四舍五入）；参数界对应
  论文 Q0 > 2(P0)²B0·N（本文 Q'=Q0、P'=Q、B0=B'、N=n_c，且 Q' 初始化
  校验不满足即 raise ValueError）。
- **模数选择**：Q、Q' 均取素数（Miller-Rabin 确定基集），且 Q > N、
  Q' > n_c，保证所有 Lagrange 分母模意义下可逆。
- **精度**：外层解码误差 ≈ (‖e_CT‖+N·B_sm)/Δ_ckks ≈ 1e−8 量级；方案二
  内层精确（无取整，模 Q 恒等）；方案一内层取整后精确。

### 性能 trade-off

一轮协议开销 ≈ 切块数（参数量/(M/2)）× 在线客户端数 × 单次环运算代价。
内层升环使方案一的环乘法是方案二的 ~(nlimb'/nlimb)² ≈ 6 倍 limb 卷积数。
大模型/多客户端实验请调小 `ring_degree`（64/128）或降低
`outer_modulus_bits`/`inner_noise_bits`（Q' 位宽随之下降）， smoke 配置
见 `tests/test_athfhe.py` 的 `_write_cfg`（M=64、4 客户端、MLP-16、1 轮，
约 1 分钟内跑完）。

## 4. 轨道集成与运行

两条轨道由 `athfhe/track.py` 注册（`HierAthfheTrack` / `TwinAthfheTrack`），
互斥、与 ppbr / rfl / threshold 互斥，且要求 `encryption.enabled=true`
（自带外层 CKKS numpy 仿真，`wants_crypto=False` 不经 TenSEAL；冲突预检
在 `init_protocol` 内 raise，中文消息风格与 base/experiment.py 一致）。
与 RFL 同惯例，AThFHE 按全体客户端初始化密钥结构，开启后强制
`client_sampling_rate=1.0`。

```bash
# 方案二（双服务器）/ 方案一（分簇层次）——现成实验配置（extends 继承
# configs/config.yaml，只开启对应轨道；差异调项直接加在这两个小文件里）
python main.py --config configs/config_athfhe_twin.yaml
python main.py --config configs/config_athfhe_hier.yaml

# 两配置等价于在主配置末尾写（extends 机制见 base/config_loader.py）：
#   athfhe_twin:
#     enabled: true
#     threshold: 3
#     ring_degree: 256
#     outer_modulus_bits: 60
#     scale_bits: 40
#     smudge_bits: 10
#     dropout: 0
#   athfhe_hier:
#     enabled: true
#     num_clusters: 2
#     cluster_threshold: 2
#     ring_degree: 256
#     outer_modulus_bits: 60
#     scale_bits: 40
#     inner_noise_bits: 20
#     smudge_bits: 10
#     dropout: 0
```

单轮流程（`run_round`）：选客户端（全采样）→ 明文下发 global_params 本地
训练 → 攻击注入（同 CKKS/PPBR 威胁模型）→ update dict 展平/按 M/2 切块 →
`run_protocol(client_chunks, online...)`（注入配置掉线数；方案一 T1/T2 独立
抽样，方案二随机 |T|）→ 聚合和 ÷ 在线数 = 平均更新 → reshape 回 dict →
`p.add_` 更新全局模型 → 明文评估。

### 协议成本指标（每轮 record 进 exp.metrics）

| 指标 | 含义 |
|---|---|
| athfhe_setup_bytes | 阶段0 密钥分发（每客户端 1 个 R_{M,Q} 份额多项式） |
| athfhe_upload_bytes | 阶段1 外层密文上传（2 个多项式/客户端/块） |
| athfhe_inner_bytes | 解密阶段内层消息：方案一 R_{M,Q'} 密文 + ek 份额；方案二同环掩码密文 |
| athfhe_seed_bytes | （方案二）P_i→S_H 种子信道，32 B/客户端/块 |
| athfhe_crypto_time | 协议端到端 wall-clock（不含本地训练） |
| athfhe_num_online1/2、athfhe_num_online | 两回合在线客户端数（掉线审计） |
| athfhe_M / Q_bits / Qprime_bits / Δ_bits / Δ'_bits | 实际生效的环参数留痕 |

同时记录标准指标（test_loss/test_acc/upload_cost/download_cost/
expansion_ratio 等），报告与可视化沿用基类默认分节。

## 5. 测试

```bash
python -m pytest tests/test_athfhe.py -q          # 18 个单元/端到端测试
python -m pytest tests/test_athfhe.py -q -m slow  # 3 个轨道集成测试
```

覆盖：环运算对拍、外层 CKKS 精度（Δ=2^40 时 <1e-6）、Shamir 任意子集重构
与两级闭环、方案一异步掉线/内层精确性/Q' 界校验/CH 视图掩码界、方案二
掉线容忍/精确掩码恒等/错误 dk 解密失败/S_H 视图、两轨道 tiny 端到端
（test_acc 与成本指标留痕、模型参数确实更新、互斥冲突拒绝启动）。

## 6. 与 ATASSES 的复杂度对比

ATASSES（USENIX Sec'25）原版 = 单级 (T,N)-Shamir + BFV 内层加密 +
两回合解密。本仓库的 **ATASSES 基线轨道**（`athfhe_atasses`）是其忠实
复现：`HierATHFHE` 令 `num_clusters=1` 的退化（簇间加法拆分退化为平凡
S^(1)=S），与方案一/方案二可在完全相同参数下逐流程对比通信量
（`scripts/benchmark_athfhe.py`）。

### 6.1 大 O 对比表（论文 Table 1 风格）

K = 块数 τ，N = 客户端数，环元素为计数单位。

| 方案 | 通信 | 计算 | 客户端轮数 |
|---|---|---|---|
| ATASSES（论文） | O(N+K)（论文口径）；具体记账 O(NK) 环元素 | O(N²+NK) | 2 |
| 方案一（hier） | O(NK)，跨 WAN 项降为 O(C·K) | O(N²+NK)（簇内并行） | 2 |
| 方案二（twin） | O(NK)，单项常数最小（无 Q′ 膨胀、种子 O(1)） | **O(NK)**（消去 N² 项：密钥分片集中化到 S_H） | **1** |

脚注：

1. 论文 O(N+K) 的记账惯例把密钥相关通信（Setup 份额分发）视为与 K 无关
   的常数；这与正文计算复杂度 O(N²+NK) 的口径一致，并不矛盾。
2. 本表"具体记账"按逐流程字节统计折算的环元素数（`poly_bytes` =
   M × ceil(bitlen(q)/8) 为通信记账基础），即 cost dict 的
   `athfhe_comm_*` 逐流程键。
3. 方案二消去 N² 项的来源：ATASSES 中每个参与方需向其余 N−1 方分发
   2 把内层密钥的 Shamir 份额（P2P，O(N²) 份额运算/通信）；方案二改为
   客户端只向 S_H 发 32B 种子（TLS 记录），由 S_H 集中展开种子焊接
   联合密钥 dk = S1 + S2，客户端侧零 P2P、单轮发完即走。

### 6.2 同参数实测（M=64，τ=16，Q=2^60，B′=2^20，B_sm=2^10，T=N/2，dropout=0）

见 `scripts/benchmark_athfhe.py` 输出：三方案总通信随 N 线性增长
（N=24/N=6 ≈ 4×），方案二总通信约为 ATASSES/方案一的 1/3（消去 Q′
升环膨胀：内层多项式从 2 个 R_{M,Q'}（Q′≈2^143）降为 2 个 R_{M,Q}
外加 32B 种子），且 crypto_time 随 N 的斜率最低。
