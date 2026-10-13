# 同态加密 + 联邦学习 + 无人机边缘计算：论文综述与四个新方案提案

> 本报告基于 `HFL-Attack/paperlist/` 目录下 11 篇已读论文的结构化摘要，系统梳理“同态加密 + 联邦学习（HE+FL）”从单密钥到多密钥、从标准参数服务器到云-边-端层次型、从可信机构到无可信机构的演进脉络，并针对 UAV/无人机辅助联邦学习的独特约束提出 4 个新方案（2 个标准 FL、2 个层次型 FL）。报告力求自包含：读者无需阅读原文即可理解主要技术关系与新方案设计。

---

## Executive Summary

`paperlist/` 中的 11 篇论文覆盖了隐私保护联邦学习（PPFL）的几条主线：

1. **掩码/安全聚合路线**：`17-SecAgg` 用 Diffie-Hellman 协商 + 双掩码 + Shamir 秘密分享实现了首个可实用的单服务器安全聚合，但 $O(n^2)$ 的客户端通信、同步轮次与输入有效性验证缺失使其难以直接套用到高移动、高掉线的 UAV 群。
2. **多密钥同态加密（MKHE）路线**：`19-CDKS` 把 RLWE 多密钥 CKKS/BFV 从理论推向实用；`24-SecFed` 用 TEE 加速 MK-BFV 并解决掉线补充密文；`25-MASER` 用共识剪枝 + slicing 把 MKHE 通信/计算压下来；`26-CDKS-LSS` 则用 $R_q$ 上的线性秘密分享堵住 CDKS 部分解密泄露并给出阈值解密。它们共同证明：MKHE 可以在无 TTP 下实现强隐私，但掉线容忍、P2P 份额通信与计算开销仍是瓶颈。
3. **单密钥 HE 路线**：`25-ESAFL` 用“多密钥加密、单聚合密钥解密”把 MKHE 的效率压到接近 SKHE；`25-FedPHE` 用单密钥 CKKS + pack-level 稀疏 + Shamir 混淆 + 贡献感知选择解决密文膨胀与 straggler；`25-SKHE` 用 ECC+Shamir+TEE 把单密钥 CKKS 安全地用于云-边-端 IoT。它们的共同代价是信任锚（KDC/TA/TEE）或共享密钥的脆弱性。
4. **层次型/边缘辅助路线**：`26-PPBR` 首次在端-边-云 HFL 中同时做 CLDP 隐私与拜占庭鲁棒聚合；`25-SKHE` 提供云-边-端单密钥 CKKS 方案；`26-Ftopk/DSVFL` 用 Paillier+Shamir 做可验证聚合但仍是标准端-云架构。
5. **通信压缩路线**：`24-ResFed` 用残差预测 + 深度压缩显著降低通信量，但完全不提供隐私，可与其它 HE/SA 方案正交叠加。

**UAV 场景的特殊性**在于：高移动性导致拓扑与簇成员频繁变化、空地链路间歇性、能量与算力受限、节点掉线常态化、地面站/边缘节点可能半可信、空域攻击面（GPS 欺骗、Sybil、MITM）大、数据/任务地理异构。现有方案没有同时解决：

- 无 TTP 下的动态阈值密钥管理；
- 高掉线率下的 MKHE 解密；
- 半可信边缘节点下的密文级拜占庭过滤；
- 高速拓扑变化下的轻量密钥再分配。

本报告随后提出 4 个新方案：

| 方案 | 名称 | 架构 | 解决的核心缺口 |
|---|---|---|---|
| 方案 1 | UMT-FL：UAV 群阈值多密钥 CKKS + SA 辅助份额转发 | 标准 FL | 高掉线下无 TTP 的 MKHE 解密，堵住部分解密泄露 |
| 方案 2 | UDK-FL：DKG + 打包 RLWE 加法同态 + 贡献感知选择 | 标准 FL / cross-silo | 单密钥互信问题、无 TTP 密钥生成、压缩与掉队选择 |
| 方案 3 | UEC-BR：UAV-Edge-Cloud 层次 CKKS + 边缘拜占庭过滤 + 云阈值解密 | 层次型 FL | 半可信边缘、边缘层密文聚合、云层轻量验证 |
| 方案 4 | UCL-HFL：自组织 UAV 簇 + 分层 DKG + 簇间阈值解密 | 层次型 FL | 高速移动拓扑变化、轻量密钥再分配、簇间阈值解密 |

每个方案均给出实体、流程、RLWE/CKKS 加解密与门限重构的闭环公式，以及明确的威胁模型、合谋上限与掉线容忍。

---

## 1. 11 篇论文在 HE+FL 演进中的位置

### 1.1 大汇总表

| 论文 | 年份 | 架构 | 密钥管理 | 信任模型 | 核心解决的问题 | 主要局限 |
|---|---|---|---|---|---|---|
| **17-SecAgg**<br>Bonawitz et al., CCS'17 | 2017 | 标准参数服务器 FL；服务器转发 pairwise 掩码 | 多密钥掩码 + DH 协商 + Shamir $(t,n)$ 分享私钥/自掩码种子；**不基于 HE** | 半诚实 / 恶意（需 PKI+签名）；服务器可与 $<t$ 客户端合谋仍保护诚实输入；最多容忍 $n-t$ 掉线 | 首个大规模实用安全聚合；低通信扩展（约 1.7–2×）；任意子集掉线仍可去掩码 | $O(n^2)$ 客户端通信/存储；无输入范围验证；服务器去掩码计算 $O(mn^2)$；高掉线率代价大；需同步轮次 |
| **19-CDKS**<br>Chen et al., CCS'19 | 2019 | 云辅助安全推理（数据方/模型方/云） | **多密钥 RLWE CKKS/BFV**；各方独立 $(sk_i,pk_i)$；EvalKey/GaloisKey 公开 | 云半诚实；参与方可与云合谋但无法获他方私钥；CRS 模型 | 首个打包密文 MKHE 实用实现；低客户端负担；支持 oblivious NN inference | 计算随参与方数 $O(k^2)$；EvalKey 体积大；**部分解密值 $oldsymbol{\mu_i=c_i s_i}$ 直接披露会泄露个体明文**；非 FL 聚合协议 |
| **24-ResFed**<br>Song et al., IoT-J'23 | 2023 | 标准参数服务器 FL | **无加密/无密钥** | 未形式化；隐假设诚实参与方 | 残差预测 + Top-K + 1-bit + Huffman 同时压缩上下行通信 | 完全不保护隐私；无掉线/恶意/鲁棒性；需预测器一致 |
| **24-SecFed**<br>Cai et al., TDSC'24 | 2024 | 标准 FL + 云侧 TEE | **多密钥 BFV（EMK-BFV）**；TEE 生成主密钥并拆分为 $sk_i$；恢复密钥 $rk_i$ | TEE 完全可信；GP/用户半诚实；用户可与 GP 合谋；最多 $N-2$ 掉线 | TEE 加速重线性化/噪声刷新/乘法；TEE 生成补充密文解决 MKHE 掉线 | 强依赖 TEE 安全；不防投毒；GP–TEE 交互开销；更适合 cross-silo；无阈值解密 |
| **25-ESAFL**<br>Wu et al., TDSC'25 | 2025 | 标准 cross-silo FL | **多密钥加密 + 单聚合解密密钥 ESHE**；AS（可信管理服务器）初始化分发 $s_i$ 与 $s=\sum s_i$；无 SA | AS 可信（可用 MPC 替代）；客户端/云半诚实；最多 $N-2$ 客户端合谋 | 接近 SKHE 效率；避免 MKHE 部分解密泄露；无需每轮安全信道 | 依赖初始化 TTP/AS；仅加法同态；未显式讨论掉线；半诚实模型 |
| **25-FedPHE**<br>Li et al., TDSC'25 | 2025 | 标准参数服务器 cross-silo FL | **单密钥 CKKS** + Shamir 混淆数 $r_i,r_d$；KDC 完全可信 | KDC 可信；PS/客户端半诚实；最多 $\|\mathcal{S}_t\|-2$ 客户端与 PS 合谋 | 打包 + pack-level Top-$\zeta$ 稀疏 + 贡献感知选择 + 密文加权聚合；抗 straggler | 强依赖 KDC；仍是单密钥（靠混淆弥补）；不防恶意/投毒；Shamir 恢复需 $th$ 人在线 |
| **25-MASER**<br>Omar et al., arXiv'25 | 2025 | 标准 cross-silo FL | **多密钥 CKKS（xMKCKKS）**；可信 Key Manager 聚合公钥；各方私钥独立 | 服务器半诚实；客户端可恶意（合谋 $<50\%$）；Key Manager setup 可信 | 共识剪枝 + slicing 把 MKHE 开销降到明文 FedAvg 1.48–5×；非 IID 下精度稳健 | **无掉线容忍**（需所有客户端 partial decryption）；依赖可信 Key Manager；多数投票上限 50% |
| **25-SKHE**<br>Liu et al., IoT-J'25 | 2025 | 云-边-端层次型 FL | **单密钥 CKKS**；TA 生成 CKKS 密钥；ECC 加密 $sk_x$；Shamir 拆分 ECC 私钥给 EN；TEE 内重建 | TA 可信；CN/EN/设备半诚实；EN 与 CN 可合谋；TEE 可信 | 以单密钥 CKKS 成本获得近似多密钥的抗合谋；掉线容忍；无精度损失 | 依赖可信 TA 与 TEE；不防恶意攻击；哈希明文传输；云是单点 |
| **26-CDKS-LSS**<br>Wang et al., Inf. Sci.'26 | 2026 | 标准 FedAvg FL | **多密钥 CKKS + $R_q$ 上 Shamir LSS**；无私钥托管 | 无 TTP；服务器/参与方半诚实；服务器可与 $<t$ 方合谋；$t$-out-of-$n$ 掉线容忍 | 堵住 CDKS 部分解密泄露；阈值解密；后量子 | P2P 份额通信量大（每轮约百 MB）；半诚实模型；无拜占庭鲁棒聚合 |
| **26-Ftopk / DSVFL**<br>Li et al., TMC'26 | 2026 | 标准端-云边缘 FL | **单密钥阈值 Paillier**；TA 生成密钥并把私钥 Shamir 份额给客户端 | TA 可信；GS 恶意；LCs 半诚实；GS 可与 $<\tau$ 客户端合谋；$\tau$-out-of-$m$ 掉线 | Ftop-k 压缩 + 阈值 Paillier 聚合 + 服务器聚合结果可验证 | 依赖强 TA；Paillier 非 RLWE，大模型下可扩展性差；不防 Byzantine 更新；标准架构 |
| **26-PPBR**<br>He et al., TMC'26 | 2026 | 端-边-云 EHFL | **多密钥场景下的 BFV/PLHE + SA**；云与边缘各持自己的 PLHE 密钥对；SA 无 TTP | 云/边缘/大多数客户端半诚实；$<50\%$ Byzantine；边缘/客户端合谋上限 $<n_i-1$、$<l-1$ | 首次在 HFL 中同时实现 CLDP 隐私、PLHE 保护边缘级模型、SA 层次聚合、拜占庭过滤 | BFV 整数运算，浮点需量化；通信/计算开销大（ tens of MB/轮）；参数调参复杂；无 CKKS |

### 1.2 架构-密钥-信任三维定位

- **单密钥 vs 多密钥 vs 阈值**：`17-SecAgg`、`25-FedPHE`、`25-SKHE`、`26-Ftopk` 都属于单密钥或共享密钥路线，依赖 KDC/TA/TEE 或 Shamir 混淆来弥补互信缺陷；`19-CDKS`、`24-SecFed`、`25-MASER`、`26-CDKS-LSS` 走多密钥 RLWE 路线，其中只有 `26-CDKS-LSS` 实现了真正的 $(t,n)$ 阈值门限解密；`25-ESAFL` 是“多密钥加密、单聚合密钥解密”的中间形态。
- **标准 FL vs HFL / 云边端 / UAV 辅助**：`17-SecAgg`、`19-CDKS`、`24-SecFed`、`25-ESAFL`、`25-FedPHE`、`25-MASER`、`26-CDKS-LSS`、`26-Ftopk` 均为标准参数服务器或端-云架构；`25-SKHE` 与 `26-PPBR` 进入云-边-端/端-边-云层次型架构，但均非针对 UAV 高移动性设计。
- **可信机构 vs 无可信机构**：`25-FedPHE`、`25-SKHE`、`26-Ftopk` 依赖在线或离线 TTP/KDC/TA；`24-SecFed` 以 TEE 为信任锚；`17-SecAgg`（半诚实模型）与 `19-CDKS`、`25-ESAFL`、`25-MASER`、`26-CDKS-LSS`、`26-PPBR` 可在无独立 TTP 下运行，但各自付出通信、计算或掉线容忍代价。
- **隐私 vs 鲁棒性 vs 通信效率**：`17-SecAgg` 与 `24-ResFed` 分别代表隐私和通信两个极端；`26-PPBR` 首次在 HFL 中把隐私与拜占庭鲁棒性统一，但牺牲了通信效率；`25-FedPHE`、`25-MASER` 在 cross-silo 中分别用稀疏/打包和剪枝/slicing 降低通信；`25-SKHE` 用 TEE 在层次架构中降低单密钥 HE 成本。

---
## 2. 循序渐进的演进脉络：问题-方案链

### 2.1 文字脉络

可以把这 11 篇论文看成围绕“**如何在不可信聚合者面前安全地计算模型更新的和/加权平均**”这一核心问题逐步加约束、逐步加深化的过程。

1. **起点：明文聚合没有任何隐私（FedAvg 基线）**。服务器直接看到每个客户端的梯度/模型更新，可被反演攻击恢复训练数据。

2. **第一步：用 pairwise 掩码把“和”藏起来（`17-SecAgg`）**。Bonawitz 等人发现，如果客户端两两协商随机种子并构造互为相反数的掩码，那么服务器只能看到 $\sum_i x_i$，单个更新被掩盖。掉线问题通过 Shamir 分享自掩码与 pairwise 掩码种子解决。代价是 $O(n^2)$ 的客户端通信和同步轮次，且不支持对聚合结果做进一步同态运算（如加权平均、范数裁剪）。

3. **第二步：用同态加密把“和”直接算在密文上（单密钥 HE）**。`BatchCrypt` / `FATE` 等（目录外）以及 `25-FedPHE`、`25-SKHE` 采用 CKKS/BFV 单密钥 HE，服务器对密文做加法即可得到全局更新。问题转为“所有客户端必须共享同一解密密钥”，导致诚实但好奇的客户端可以解密他人更新；于是 `25-FedPHE` 用 Shamir 混淆数掩盖明文，`25-SKHE` 用 ECC+Shamir+TEE 把私钥“可用但不可见”。

4. **第三步：每个参与方各持一把钥匙（`19-CDKS` 多密钥 HE）**。CDKS 把 RLWE 公钥结构扩展到多个独立私钥，云可以在密文上做跨密钥运算。但分布式解密时每个参与方必须披露 $\mu_i=c_i s_i$；由于服务器也知道 $c_{i,0}$，它可以直接恢复 $c_{i,0}+\mu_i\approx m_i$，**个体更新泄露**。

5. **第四步：把部分解密值也分享起来（`26-CDKS-LSS`）**。CDKS-LSS 将每个 $\mu_i$ 用 $R_q$ 上的 Shamir LSS 拆分，所有参与方先本地聚合份额再把聚合份额发给服务器；服务器收到任意 $t$ 个聚合份额即可重构 $\sum_i \mu_i$，从而得到聚合明文。这一步同时实现了**无 TTP**、**阈值掉线容忍**和**部分解密隐私**，但引入参与方之间每轮 $O(n^2)$ 的 $R_q$ 元素通信。

6. **第五步：用 TEE 或共享聚合密钥绕过掉线（`24-SecFed`、`25-ESAFL`）**。SecFed 把重线性化、噪声刷新与“补充密文”放进 TEE，允许最多 $N-2$ 个用户掉线，但信任锚是 TEE；ESAFL 让各客户端用不同 $s_i$ 加密，但共享一个聚合解密密钥 $s=\sum_i s_i$，从而把效率压到接近 SKHE，同时避免公共信道上的部分解密结果泄露，但初始化依赖 AS。

7. **第六步：把 MKHE 的开销压下来（`25-MASER`）**。MASER 在 xMKCKKS 之前先对模型做共识剪枝 + slicing，把密文体积和运算量从 $O(N^2)$ 降到接近明文 FedAvg 的 1.48–5×，但保留了“所有客户端必须在线做 partial decryption”的弱点——这正是 CDKS-LSS 下一步要补的。

8. **第七步：在层次型边缘网络中同时做隐私与拜占庭鲁棒（`26-PPBR`、`25-SKHE`）**。PPBR 把 CLDP（保护符号统计）、PLHE/BFV（保护范数裁剪与边缘级模型）和 SA（保护层次平均聚合）三层叠加，实现端-边-云同时隐私+鲁棒；SKHE 则用单密钥 CKKS+TEE 在云-边-端做高效聚合。两者都没解决 UAV 高移动性带来的动态簇、动态密钥、频繁掉线问题。

9. **第八步：通信压缩与可验证性（`24-ResFed`、`26-Ftopk`）**。ResFed 用残差预测把通信量压到极低，但完全不隐私；Ftopk 用 Paillier+Shamir 实现压缩、掉线容错与服务器可验证，但密码体制不是 RLWE/CKKS，且依赖强 TA。

### 2.2 问题-方案链图/表

下面用一张链式表格把上述关系显式化。每一行的“仍留问题”是下一行的研究动机。

| 阶段 | 核心问题 | 代表性方案 | 关键机制 | 解决的问题 | 仍留问题 |
|---|---|---|---|---|---|
| 0 | 服务器可见单个更新 | FedAvg 基线 | 明文上传 | 无 | 隐私泄露 |
| 1 | 如何隐藏单个更新并求和 | `17-SecAgg` | DH pairwise 掩码 + 自掩码 + Shamir 分享 | 单服务器安全聚合、任意掉线 | $O(n^2)$ 通信；无加权/裁剪；无输入验证 |
| 2 | 服务器能否直接对密文求和 | 单密钥 HE（`25-FedPHE`、`25-SKHE`） | CKKS/BFV 加法同态 + 打包 | 服务器不见明文即可聚合 | 单密钥下客户端互信弱；KDC/TA/TEE 信任锚 |
| 3 | 如何在不共享密钥下跨用户密文运算 | `19-CDKS` | MK-CKKS/BFV、重线性化、打包 | 多方各持密钥仍可同态推理 | 部分解密 $\mu_i$ 泄露个体更新 |
| 4 | 如何保护部分解密过程 | `26-CDKS-LSS` | $R_q$ 上 Shamir LSS 分享 $\mu_i$ | 无 TTP、阈值解密、防泄露 | P2P 份额通信大；半诚实；无 Byzantine |
| 5a | 如何兼顾 MKHE 与掉线/效率 | `24-SecFed` | EMK-BFV + TEE 补充密文 | 多密钥 + 掉线容忍 | 强依赖 TEE；不防投毒 |
| 5b | 如何把 MKHE 效率压到 SKHE 级 | `25-ESAFL` | ESHE：多密钥加密、单聚合密钥解密 | 避免部分解密泄露、接近 SKHE 效率 | 初始化 AS；仅加法；未讨论掉线 |
| 5c | 如何降低 MKHE 密文/运算开销 | `25-MASER` | 共识剪枝 + slicing + xMKCKKS | MKHE 通信/计算大幅下降 | 无掉线；依赖 Key Manager |
| 6 | 如何在层次边缘网络中同时隐私+鲁棒 | `26-PPBR` | CLDP + PLHE + SA + 拜占庭过滤 | 端-边-云 HFL 隐私 + Byzantine 鲁棒 | BFV 整数、通信大、未针对 UAV 移动性 |
| 7 | 如何压缩通信并验证服务器 | `24-ResFed`/`26-Ftopk` | 残差压缩 / Ftop-k + Paillier + Shamir 可验证 | 通信效率 / 可验证聚合 | 无隐私 / 非 RLWE / 强 TA |

**一句话总结**：从 `SecAgg` 的掩码到 `CDKS` 的 MKHE，再到 `CDKS-LSS` 的 LSS 阈值解密，隐私层不断加固；从 `SecFed`/`ESAFL`/`MASER` 开始，效率与掉线成为主要矛盾；`PPBR` 把战线推进到层次型边缘网络并首次统一隐私与拜占庭鲁棒性；**UAV 场景则要求在上述所有基础上再叠加“高移动、高掉线、半可信边缘、能量受限”四个新约束**，这正是本报告第 4 节要补上的缺口。

---

## 3. UAV/无人机辅助联邦学习的特殊挑战与现有方案未解之处

### 3.1 UAV 场景的独特约束

UAV 群与地面站/边缘节点/云层协同完成 FL 时，面临以下与数据中心 cross-silo FL 或静态 IoT 截然不同的约束：

1. **高移动性**：UAV 速度可达数十米每秒，簇成员、簇头、与地面站的连接关系每轮都可能变化。
2. **间歇性链路**：空地信道受遮挡、多径、干扰、雨衰影响，丢包率高、时延抖动大。
3. **能量受限**：机载电池决定计算与通信预算，重同态运算或大量 P2P 协商会快速耗尽电量。
4. **算力受限**：机载 CPU/GPU 性能远低于云服务器，MKHE 重线性化、Bootstrapping 通常无法承受。
5. **节点频繁掉线**：电量耗尽、飞出覆盖区、被攻击、任务切换都会导致任意 UAV 随时离线。
6. **地面站/边缘节点可能半可信**：地面站位于任务区域，可能被俘获或收买；不能完全信任其执行去掩码或解密。
7. **空域攻击面大**：GPS 欺骗可导致 UAV 位置同步错误；Sybil 攻击可注入虚假客户端；MITM 可篡改密文/份额； Jamming 可导致聚合轮失败。
8. **数据异构且任务地理分布**：不同空域的 UAV 采集数据分布差异大（Non-IID），拜占庭/异常更新与正常 Non-IID 更新难以区分。

### 3.2 这些挑战如何映射到现有方案的缺口

| UAV 挑战 | 对 HE+FL 方案的具体要求 | 现有方案的不足 |
|---|---|---|
| 高移动性 / 拓扑变化 | 密钥管理不能绑定固定参与者；簇头可动态选举；份额可再分配 | `17-SecAgg` 需要固定 pairwise 关系；`26-CDKS-LSS` 需要固定 $n$ 与 P2P 份额交换；`25-SKHE` 固定云-边-端拓扑 |
| 间歇性链路 / 高掉线 | 解密/去掩码应只需 $t$-out-of-$n$ 在线，且对异步/延迟友好 | `19-CDKS`、`25-MASER` 需所有参与方 partial decryption；`24-SecFed` 用 TEE 补充密文，但不适合 UAV 端无 TEE；`25-ESAFL` 未讨论掉线 |
| 能量/算力受限 | 客户端只做轻量加密/份额生成；避免重线性化、大量 P2P 协商 | `19-CDKS`、`25-MASER` MKHE 计算重；`17-SecAgg` $O(n^2)$ 协商；`25-SKHE` 依赖 TEE，机载难部署 |
| 半可信边缘/地面站 | 边缘不能单独解密；去掩码需要阈值或多密钥 | `25-SKHE` 的 TEE 在云端，边缘仍可能看到部分信息；`26-PPBR` 的边缘做 SA 与过滤，但未用阈值加密保护边缘级模型 |
| 空域攻击面 | 需要认证、抗 Sybil、可验证聚合、份额/密文完整性 | `17-SecAgg` 主动模型需 PKI；`26-Ftopk` 可验证但依赖 TA；`26-PPBR` 无显式密文完整性验证 |
| 数据异构 / 地理分布 | 需要隐私保护的拜占庭过滤，且对 Non-IID 不敏感 | `26-PPBR` 提供 CLDP+聚类，但仅适用于边缘服务器可见符号统计的场景；MKHE 方案均无 Byzantine 过滤 |

### 3.3 设计启示

针对上述缺口，四个新方案分别聚焦：

- **标准 FL 方案 1（UMT-FL）**：把 `26-CDKS-LSS` 的 LSS 阈值解密与 `17-SecAgg` 的服务器辅助加密份额转发结合起来，**让高掉线 UAV 群在无 TTP 下完成 MKHE 聚合**。
- **标准 FL 方案 2（UDK-FL）**：用**分布式密钥生成（DKG）+ 打包 RLWE 加法同态 + 贡献感知选择**，在 cross-silo UAV 编队中消除单密钥互信与 TTP，同时压缩通信并规避 straggler。
- **层次型 FL 方案 1（UEC-BR）**：在 UAV-Edge-Cloud 架构中让边缘只进行**CKKS 密文聚合 + 基于 DP-sketch 的拜占庭过滤**，云层通过**阈值解密**获得全局模型，从而边缘半可信、边缘级模型不泄露。
- **层次型 FL 方案 2（UCL-HFL）**：UAV 自组织成簇，簇头即边缘；通过**分层 DKG + 簇内密文聚合 + 簇间阈值解密**，让簇成员变化时无需重新生成全局密钥，只更新簇头份额。

---
## 4. 四个面向 UAV 场景的新方案

> 下面统一使用如下 RLWE/CKKS 记号：
> - $R=\mathbb{Z}[X]/(X^n+1)$，$R_q=R/qR$，$n$ 为 2 的幂；
> - 小秘密分布 $\chi_s$、小误差分布 $\chi_e$（如离散高斯/有界均匀）；
> - 公开参数 $a\leftarrow U(R_q)$（可由 CRS 或每轮 PRG 种子 $B$ 本地同步生成）；
> - CKKS 缩放因子 $\Delta$（正整数）；编码 $m=\textsf{Ecd}(\mathbf{z})\in R_q$，解码 $\mathbf{z}=\Delta^{-1}\cdot\textsf{Dcd}(m)$；
> - 对集合 $T$，Lagrange 插值系数 $\lambda_i^{(T)}=\prod_{j\in T,j\neq i}\frac{\alpha_j}{\alpha_j-\alpha_i}\in R_q$，其中 $\alpha_i$ 为公开求值点且 $\alpha_i-\alpha_j$ 在 $R$ 中可逆。

---

### 方案 1：UMT-FL — UAV 群阈值多密钥 CKKS + SA 辅助份额转发

#### 5.1 场景与应用动机

设想一支由数十至数百架 UAV 组成的侦察/搜救编队，每架 UAV 在本地采集图像并训练一个轻量目标检测模型。由于链路 intermittent，每轮训练只有部分 UAV 能成功上传更新；地面站（Ground Station, GS）作为参数服务器是不可信的。要求：

- GS 不能看到任何单个 UAV 的模型更新；
- 任意少于 $t$ 个 UAV 与 GS 合谋仍不能推断诚实 UAV 的更新；
- 只要本轮有至少 $t$ 个 UAV 完成份额上报，即可解密全局聚合结果；
- 不依赖 TTP、TEE 或 KDC。

#### 5.2 要解决的问题（与现有工作的差距）

- `19-CDKS` 的部分解密值 $\mu_i=c_{i,1}s_i$ 直接交给服务器会泄露 $m_i$；
- `26-CDKS-LSS` 用 LSS 解决了泄露，但要求所有参与方两两交换 $R_q$ 元素份额，UAV 群的高掉线与间歇链路使 P2P 份额交换不可靠；
- `17-SecAgg` 的 pairwise 掩码掉线容忍好，但不支持密文加权/裁剪，且 $O(n^2)$ 客户端通信；
- `24-SecFed` 用 TEE 补充密文，但 UAV 端通常无 TEE，且信任锚过强。

**UMT-FL 的核心差距**：把 CDKS-LSS 的 LSS 阈值解密与 SecAgg 风格的“服务器转发加密份额”结合起来，用一次轻量 pairwise 密钥协商保护份额转发，降低 UAV-to-UAV 直接链路依赖，同时保持无 TTP 与 $t$-out-of-$n$ 掉线容忍。

#### 5.3 创新点与总体设计目标

1. **无 TTP 的阈值多密钥 CKKS**：每架 UAV 自己生成 RLWE 密钥对，GS 只聚合密文与份额，不掌握任何私钥。
2. **SA 辅助的份额转发**：UAV 之间不直接建 P2P 链路，而是把 Shamir 份额用 pairwise 会话密钥加密后交给 GS 转发；GS 只见密文份额。
3. **动态掉线容忍**：只要 $t$ 个 UAV 完成份额聚合并上报，GS 即可重构 $\sum_i \mu_i$；掉线 UAV 的更新只要其密文已上传即可参与聚合，其部分解密由存活 UAV 持有的份额补齐。
4. **与现有方案的关系**：比 `19-CDKS` 安全（无部分解密泄露）；比 `26-CDKS-LSS` 更适合 UAV 间歇链路（无需稳定 P2P）；比 `24-SecFed` 轻量（无 TEE、无补充密文）；比 `17-SecAgg` 功能更强（支持 CKKS 密文域聚合与后续加权/裁剪）。

#### 5.4 威胁模型

- **地面站 GS**：诚实但好奇，严格执行协议但试图推断 UAV 隐私；可能与部分 UAV 合谋。
- **UAV 客户端**：诚实但好奇；最多 $t-1$ 个可与 GS 合谋。不考虑主动拜占庭/投毒攻击（可叠加现有鲁棒聚合作为未来工作）。
- **外部敌手**：可窃听 GS↔UAV 公共信道，无法攻破 RLWE 与认证加密。
- **合谋上限**：$<t$ 个客户端与 GS 合谋无法重构任何诚实 UAV 的个体更新或聚合解密密钥分量。
- **掉线容忍**：每轮最多 $n-t$ 个 UAV 在份额聚合阶段掉线；若密文已上传但在份额阶段掉线，其更新仍可被聚合（因为份额已由其它 UAV 持有）。
- **可信实体**：无在线 TTP/TEE/KDC；仅依赖 CRS 或公开 PRG 种子生成 $a$。

#### 5.5 方案设计：实体、流程、闭环公式推导

**实体集合**：
- $U=\{U_1,\dots,U_n\}$：UAV 客户端；
- GS：地面站/参数服务器。

**流程（每轮）**：

1. **初始化/密钥生成**。每架 UAV $i$ 本地采样：
   $$
   s_i\leftarrow\chi_s,\quad e_i\leftarrow\chi_e,\quad b_i=-a\cdot s_i+e_i\pmod q.
   $$
   公布公钥 $pk_i=(b_i,a)$ 给 GS；私钥 $sk_i=(1,s_i)$ 本地保存。GS 维护在线集合 $\mathcal{U}$。

2. **Pairwise 会话密钥（仅用于份额转发，轻量）**。每架 UAV $i$ 生成临时 DH 密钥对 $(x_i, g^{x_i})$ 并广播；对邻居/所有 $j$ 计算共享种子 $\sigma_{ij}=g^{x_i x_j}$，派生对称密钥 $k_{ij}=\textsf{KDF}(\sigma_{ij})$。该步骤仅在集群组成变化时执行，每轮训练本身不依赖 P2P 链路。

3. **本地训练与多密钥加密**。UAV $i$ 训练得到本地更新 $w_i$，CKKS 编码并缩放：
   $$
   m_i=\Delta\cdot\textsf{Ecd}(w_i)\in R_q.
   $$
   采样 $v_i\leftarrow\chi_s$，$e_{i,0},e_{i,1}\leftarrow\chi_e$，计算 RLWE 多密钥密文：
   $$
   c_{i,0}=v_i\cdot b_i+m_i+e_{i,0}\pmod q,\qquad c_{i,1}=v_i\cdot a+e_{i,1}\pmod q.
   $$
   为减少上传量并遵循 CDKS-LSS 结构，UAV $i$ 只把 $c_{i,0}$ 上传 GS；$c_{i,1}$ 保留本地用于后续部分解密。

4. **GS 密文聚合**。GS 对存活 UAV 集合 $\mathcal{U}_{\text{live}}$ 的密文做分量加法：
   $$
   c_0=\sum_{i\in\mathcal{U}_{\text{live}}} c_{i,0}\pmod q,\qquad c_i=c_{i,1}\ (i\in\mathcal{U}_{\text{live}}).
   $$
   聚合后的多密钥密文为 $\mathbf{ct}=(c_0,c_1,\dots,c_{|\mathcal{U}_{\text{live}}|})$，其相位满足：
   $$
   \langle\mathbf{ct},\mathbf{sk}\rangle=c_0+\sum_{i\in\mathcal{U}_{\text{live}}} c_i\cdot s_i\approx\Delta\sum_{i\in\mathcal{U}_{\text{live}}} m_i\pmod q.
   $$

5. **部分解密与 LSS 分享**。每架 UAV $i$ 计算自己的部分解密值（带噪声淹没 $e_i^{\text{pd}}\leftarrow\chi_e^{\text{large}}$）：
   $$
   \mu_i=c_i\cdot s_i+e_i^{\text{pd}}\pmod q.
   $$
   为防 GS 从 $\mu_i$ 反推 $m_i$（因为 $c_{i,0}+\mu_i\approx m_i$），UAV $i$ 将 $\mu_i$ 用 $(t,n)$-Shamir 在 $R_q$ 上拆分为多项式 $f_i(X)$，满足 $f_i(0)=\mu_i$、$\deg f_i=t-1$。对每个 $j\in\mathcal{U}$ 生成份额：
   $$
   \tilde\mu_{i,j}=f_i(\alpha_j)\pmod q.
   $$
   UAV $i$ 用 $k_{ij}$ 加密份额得到 $e_{i,j}=\textsf{AE}_{k_{ij}}(\tilde\mu_{i,j})$ 并发给 GS，GS 转发给 $U_j$。GS 无法打开 $e_{i,j}$。

6. **本地份额聚合与上报**。每架存活 UAV $j$ 收到所有（或足够多）加密份额后解密，并只对本集合 $\mathcal{U}_{\text{live}}$ 中的加密者求和：
   $$
   \tilde\mu_j=\sum_{i\in\mathcal{U}_{\text{live}}} \tilde\mu_{i,j}=F(\alpha_j)\pmod q,
   $$
   其中 $F(X)=\sum_{i\in\mathcal{U}_{\text{live}}} f_i(X)$ 仍是次数 $t-1$ 的多项式，且
   $$
   F(0)=\sum_{i\in\mathcal{U}_{\text{live}}} \mu_i.
   $$
   UAV $j$ 将 $\tilde\mu_j$ 发送给 GS。

7. **GS 阈值重构与全局解密**。GS 收集到任意 $T\subseteq\mathcal{U}_{\text{live}}$，$|T|\ge t$ 个聚合份额后，用 Lagrange 插值重构：
   $$
   \mu^{*}=\sum_{j\in T}\lambda_j^{(T)}\tilde\mu_j=\sum_{i\in\mathcal{U}_{\text{live}}}\mu_i\pmod q,
   $$
   其中
   $$
   \lambda_j^{(T)}=\prod_{k\in T,k\neq j}\frac{\alpha_k}{\alpha_k-\alpha_j}\in R_q.
   $$
   最终全局更新为：
   $$
   M=c_0+\mu^{*}\pmod q,
   \qquad w_G=\frac{1}{|\mathcal{U}_{\text{live}}|}\cdot\textsf{Dcd}\!\left(\frac{M}{\Delta}\right).
   $$

**公式闭环检查**：
- 密钥生成：$b_i=-as_i+e_i$；
- 加密：$c_{i,0}+c_{i,1}s_i\approx\Delta m_i$；
- 聚合：$c_0+\sum_i c_i s_i\approx\Delta\sum_i m_i$；
- 部分解密：$\mu_i=c_i s_i+e_i^{\text{pd}}$；
- 门限重构：$\mu^{*}=\sum_j\lambda_j\tilde\mu_j=\sum_i\mu_i$；
- 解密：$M=c_0+\mu^{*}\approx\Delta\sum_i m_i$。

整个链从密钥到明文聚合结果完全闭合。

---
### 方案 2：UDK-FL — DKG + 打包 RLWE 加法同态 + 贡献感知客户端选择

#### 5.1 场景与应用动机

设想多个机构级 UAV 编队（如不同搜救队、不同电力巡检中队）通过卫星/地面站链路参与 cross-silo FL。这些编队之间**不愿意共享解密密钥**，但又希望避免传统 MKHE 的高开销与 TTP 依赖。此外，编队之间计算能力、链路质量差异大，存在严重 straggler 与 Non-IID 数据分布。要求：

- 无 KDC/TA/TEE 生成密钥；
- 任一客户端无法单独解密其他客户端的更新；
- 通信量通过打包与选择机制控制在可接受范围；
- 对高掉队率有容忍能力。

#### 5.2 要解决的问题（与现有工作的差距）

- `25-ESAFL` 需要初始化 AS 分发聚合解密密钥 $s=\sum_i s_i$，AS 可信假设强；
- `25-FedPHE` 使用单密钥 CKKS 并靠 KDC+Shamir 混淆掩盖明文，本质上仍是共享密钥；
- `25-MASER` 使用多密钥 CKKS，但依赖可信 Key Manager 聚合公钥，且无掉线容忍；
- `17-SecAgg` 无 HE，无法做加权聚合或打包；`24-ResFed` 只压缩不隐私。

**UDK-FL 的核心差距**：用分布式密钥生成（DKG）让各 UAV 编队独立贡献密钥份额，聚合成一个全局 RLWE 公钥；任何客户端只持有 $s$ 的一个 Shamir 份额，无法单独解密；结合 CKKS 打包与基于 DP-sketch 的贡献感知选择，把通信和掉队问题一起解决。

#### 5.3 创新点与总体设计目标

1. **无 TTP 的阈值单密钥 RLWE**：通过“各客户端本地生成 $s_i$、公开 $pk_i$、再用 Shamir 分享 $s_i$”实现 DKG 效果，全局秘密 $s=\sum_i s_i$ 从不完整出现。
2. **打包 RLWE 加法同态**：仅使用 CKKS 加法同态（无需乘法/重线性化），客户端计算轻量，适合 UAV。
3. **贡献感知 + 掉队规避选择**：服务器在扰动 sketch 上聚类并优先选择高贡献、低延迟 UAV，降低 straggler 影响。
4. **与现有方案的关系**：比 `25-ESAFL` 去掉 AS；比 `25-FedPHE` 去掉 KDC 与共享密钥风险；比 `25-MASER` 支持掉线容忍；比 `17-SecAgg` 支持密文域聚合与打包。

#### 5.4 威胁模型

- **参数服务器 S**：诚实但好奇，可能与 $<t$ 个客户端合谋。
- **UAV 客户端**：诚实但好奇；最多 $t-1$ 个合谋（包括与 S 合谋）无法重构全局秘密 $s$ 或解密聚合前的任何单个更新。
- **外部敌手**：RLWE 语义安全保证密文不可区分。
- **掉线容忍**：最多 $n-t$ 个客户端在解密阶段掉线；选择阶段可容忍任意比例慢客户端（只选最快/高贡献子集）。
- **可信实体**：无在线 TTP；初始 CRS/PRG 种子可公开；可选 ZKPoK 验证每个 $pk_i$ 格式正确。

#### 5.5 方案设计：实体、流程、闭环公式推导

**实体集合**：
- $C=\{C_1,\dots,C_n\}$：UAV/编队客户端；
- S：中心参数服务器。

**流程（每轮）**：

1. **分布式密钥生成（DKG）与公钥聚合**。每个客户端 $i$ 本地采样：
   $$
   s_i\leftarrow\chi_s,\quad e_i\leftarrow\chi_e,\quad b_i=-a\cdot s_i+e_i\pmod q.
   $$
   公开 $pk_i=(b_i,a)$。服务器聚合全局公钥：
   $$
   b=\sum_{i=1}^n b_i=-a\cdot\underbrace{\left(\sum_{i=1}^n s_i\right)}_{s}+\underbrace{\sum_{i=1}^n e_i}_{e}\pmod q.
   $$
   全局公钥为 $pk=(b,a)$，对应全局秘密 $s=\sum_i s_i$。**没有任何单一实体知道 $s$**。

2. **Shamir 阈值化 $s$**。为使解密可在 $t$-out-of-$n$ 下完成，每个客户端 $i$ 将 $s_i$ 用 $(t,n)$-Shamir 拆分为 $f_i(X)$（$f_i(0)=s_i$，$\deg f_i=t-1$），把份额 $s_{i,j}=f_i(\alpha_j)$ 安全发送给客户端 $j$。客户端 $j$ 聚合所有收到的份额：
   $$
   \tilde s_j=\sum_{i=1}^n s_{i,j}=F(\alpha_j)\pmod q,
   $$
   其中 $F(X)=\sum_i f_i(X)$ 满足 $F(0)=s$。因此 $\tilde s_j$ 是 $s$ 的一个 Shamir 份额。

3. **本地训练与 CKKS 打包加密**。客户端 $i$ 训练得到更新 $w_i$，编码并缩放：
   $$
   m_i=\Delta\cdot\textsf{Ecd}(w_i)\in R_q.
   $$
   采样 $v_i\leftarrow\chi_s$，$e_{i,0},e_{i,1}\leftarrow\chi_e$，用全局公钥加密：
   $$
   c_{i,0}=v_i\cdot b+m_i+e_{i,0}\pmod q,\qquad c_{i,1}=v_i\cdot a+e_{i,1}\pmod q.
   $$
   由于 CKKS 支持 SIMD 打包，一个密文可装载 $n/2$ 个浮点参数，显著降低通信量。

4. **贡献感知客户端选择（可选，每 $r$ 轮执行一次）**。每个客户端计算本地梯度/更新的轻量 sketch（如 sign proportions 或 Top-$k$ 坐标索引），加入 DP 噪声后上报 S。S 用 Gap Statistic + K-means 聚类，选择覆盖主要数据分布且历史延迟低的子集 $\mathcal{S}\subseteq[n]$，并向 $\mathcal{S}$ 广播选择结果。未被选中的客户端本轮不上传全量密文。

5. **服务器密文聚合**。对选中集合 $\mathcal{S}$：
   $$
   C_0=\sum_{i\in\mathcal{S}} c_{i,0}\pmod q,\qquad C_1=\sum_{i\in\mathcal{S}} c_{i,1}\pmod q.
   $$
   聚合密文满足：
   $$
   C_0+C_1\cdot s=\sum_{i\in\mathcal{S}}(v_i b+m_i+e_{i,0})+\left(\sum_{i\in\mathcal{S}} v_i a+e_{i,1}\right)s\pmod q.
   $$
   代入 $b=-as+e$ 得：
   $$
   C_0+C_1\cdot s=\sum_{i\in\mathcal{S}} m_i+\underbrace{\sum_{i\in\mathcal{S}}(v_i e+e_{i,0}+e_{i,1}s)}_{\text{噪声}}\pmod q.
   $$

6. **阈值部分解密与重构**。每个在线客户端 $j\in\mathcal{S}_{\text{dec}}$（$|\mathcal{S}_{\text{dec}}|\ge t$）用其份额 $\tilde s_j$ 计算部分解密：
   $$
   pd_j=C_1\cdot\tilde s_j+\eta_j\pmod q,
   $$
   其中 $\eta_j\leftarrow\chi_e^{\text{large}}$ 为噪声淹没项。S 收集任意 $t$ 个 $pd_j$ 后重构：
   $$
   \mu=\sum_{j\in T}\lambda_j^{(T)} pd_j\approx C_1\cdot s\pmod q.
   $$

7. **全局解密与模型更新**。S 计算：
   $$
   M=C_0+\mu\pmod q,\qquad w_G=\frac{1}{|\mathcal{S}|}\cdot\textsf{Dcd}\!\left(\frac{M}{\Delta}\right).
   $$

**公式闭环检查**：
- DKG：$b=\sum_i b_i=-as+e$；
- 份额聚合：$\tilde s_j=F(\alpha_j)$，$F(0)=s$；
- 加密：$c_{i,0}+c_{i,1}s\approx\Delta m_i$；
- 聚合：$C_0+C_1s\approx\Delta\sum_i m_i$；
- 部分解密：$pd_j\approx C_1\tilde s_j$；
- 重构：$\mu\approx C_1s$；
- 解密：$M\approx\Delta\sum_i m_i$。

**与 paperlist 的差异总结**：UDK-FL 用 DKG 彻底去掉 `25-ESAFL` 的 AS 与 `25-FedPHE` 的 KDC；用阈值份额解密解决 `25-MASER` 的掉线脆弱性；用打包+选择降低 `17-SecAgg` 的通信复杂度；与 `26-CDKS-LSS` 相比，UDK-FL 是单公钥结构，份额更小、份额转发无需按不同密钥分量分别处理，更适合 UAV 的轻量需求。

---
### 方案 3：UEC-BR — UAV-Edge-Cloud 层次 CKKS + 边缘拜占庭过滤 + 云阈值解密

#### 5.1 场景与应用动机

设想一个城市级 UAV 巡检系统：大量小型 UAV 先连接到本地边缘服务器（如楼顶基站、车载边缘节点），边缘服务器再与远程云交互。边缘服务器由第三方运营，**诚实但好奇**——它会按协议转发/聚合，但可能试图推断其覆盖 UAV 的隐私；云中也可能存在好奇管理员。此外，部分 UAV 可能被攻击者控制，上传拜占庭更新。要求：

- 边缘节点不能看到单个 UAV 的明文更新，也不能看到其它边缘辖区的聚合模型；
- 边缘层能过滤掉本地拜占庭 UAV；
- 云层只需轻量验证即可获得全局模型；
- 支持边缘节点与 UAV 掉线。

#### 5.2 要解决的问题（与现有工作的差距）

- `26-PPBR` 在边缘层使用 BFV/PLHE + SA，虽然同时实现隐私与拜占庭鲁棒，但使用整数 BFV，浮点模型需量化，且边缘级模型最终仍会以明文形式进入云的后续处理；
- `25-SKHE` 是云-边-端单密钥 CKKS 方案，依赖 TA 与云端 TEE，边缘节点只是转发；
- `25-FedPHE` 只做标准 cross-silo，无边缘层；
- `17-SecAgg` 的 pairwise 掩码在边缘层做 $O(n_i^2)$ 协商会迅速耗尽 UAV 电量。

**UEC-BR 的核心差距**：让边缘服务器只进行 **CKKS 密文聚合** 与 **基于 DP-sketch 的轻量拜占庭过滤**，云层通过 **Shamir 阈值解密** 获得全局模型；边缘节点不持有云私钥，云层单独或与少数边缘合谋无法解密，从而在“半可信边缘 + 拜占庭 UAV + 掉线”三重约束下实现隐私-鲁棒-可用统一。

#### 5.3 创新点与总体设计目标

1. **边缘层 CKKS 密文聚合**：UAV 用云公钥加密更新，边缘只对密文求和，不见明文。
2. **边缘侧拜占庭过滤**：UAV 上传 DP 扰动的符号统计 sketch；边缘用 trimmed k-means 聚类剔除异常 UAV，再聚合可信 UAV 的密文。
3. **云阈值解密**：云私钥 $s_A$ 通过 $(t,l)$-Shamir 分散在 $l$ 个边缘节点；任意 $t$ 个边缘节点提供部分解密份额，云才能解密全局模型。
4. **轻量云验证**：云解密后检查全局更新范数/符号一致性，拒绝异常聚合；边缘上传密文哈希链供云验证完整性。
5. **与现有方案的关系**：比 `26-PPBR` 使用 CKKS 浮点同态并引入阈值云解密，避免整数量化与边缘级模型泄露；比 `25-SKHE` 把隐私边界从“云端 TEE”推进到“边缘阈值份额”；比 `25-FedPHE` 扩展到层次型架构并加入拜占庭过滤。

#### 5.4 威胁模型

- **UAV 客户端**：诚实但好奇，但每个边缘辖区内最多 $\beta<0.5$ 比例为拜占庭恶意（可上传任意错误更新）。
- **边缘服务器 $A_i$**：诚实但好奇，可与云合谋；但最多 $t-1$ 个边缘与云合谋无法重构云私钥 $s_A$。
- **云服务器 A**：诚实但好奇，不主动破坏协议，但试图从接收到的密文/统计量中推断隐私。
- **KGA（密钥生成机构）**：仅在初始化阶段存在，生成云密钥对并分发份额后离线删除私钥；KGA 不与运行期实体合谋。
- **掉线容忍**：每个边缘辖区内只要可信 UAV 数 $|U_i^{(T)}|\ge t_i$ 即可完成本地过滤与密文聚合；全局解密需要至少 $t$ 个边缘在线。

#### 5.5 方案设计：实体、流程、闭环公式推导

**实体集合**：
- $U_{i,j}\in U_i$：边缘 $A_i$ 覆盖下的 UAV 客户端；
- $A_i$（$i=1,\dots,l$）：边缘服务器/基站；
- $A$：远程云服务器；
- KGA：离线密钥生成机构。

**流程（每轮）**：

1. **云密钥生成与阈值分发**。KGA 采样云私钥 $s_A\leftarrow\chi_s$、误差 $e_A\leftarrow\chi_e$，生成云公钥：
   $$
   b_A=-a\cdot s_A+e_A\pmod q,\qquad pk_A=(b_A,a).
   $$
   用 $(t,l)$-Shamir 把 $s_A$ 拆成份额 $\{s_{A,i}\}_{i=1}^l$，通过安全信道分发给各边缘 $A_i$；KGA 随后删除 $s_A$ 并离线。所有 UAV 只持有 $pk_A$。

2. **云广播与本地训练**。云广播全局模型 $w^{(r)}$、$pk_A$ 与公开参数。UAV $U_{i,j}$ 本地训练得到更新 $\Delta w_{i,j}$，编码并缩放：
   $$
   m_{i,j}=\Delta\cdot\textsf{Ecd}(\Delta w_{i,j})\in R_q.
   $$

3. **客户端 CKKS 加密**。UAV $U_{i,j}$ 采样 $v_{i,j}\leftarrow\chi_s$，$e_{i,j,0},e_{i,j,1}\leftarrow\chi_e$，加密：
   $$
   c_{i,j,0}=v_{i,j}\cdot b_A+m_{i,j}+e_{i,j,0}\pmod q,\qquad c_{i,j,1}=v_{i,j}\cdot a+e_{i,j,1}\pmod q.
   $$
   同时计算一个低维 DP-sketch（如符号比例向量）$\mathbf{y}_{i,j}=\mathbf{s}_{i,j}+\boldsymbol\xi_{i,j}\in\mathbb{R}^3$，其中 $\boldsymbol\xi$ 为 CLDP/Laplace 噪声；并把密文哈希 $h_{i,j}=H(c_{i,j,0},c_{i,j,1})$ 一并提交给 $A_i$。

4. **边缘拜占庭过滤**。边缘 $A_i$ 收到辖区内所有 $(c_{i,j},\mathbf{y}_{i,j},h_{i,j})$ 后：
   - 用 trimmed k-means 对 $\{\mathbf{y}_{i,j}\}$ 聚类，剔除离群簇，得到可信 UAV 集合 $U_i^{(T)}$；
   - 验证 $h_{i,j}$ 与收到的密文一致。

5. **边缘层 CKKS 密文聚合**。$A_i$ 对可信 UAV 的密文做分量加法：
   $$
   C_i=(C_{i,0},C_{i,1}),\quad C_{i,0}=\sum_{j\in U_i^{(T)}} c_{i,j,0}\pmod q,\quad C_{i,1}=\sum_{j\in U_i^{(T)}} c_{i,j,1}\pmod q.
   $$
   $A_i$ 将 $C_i$ 与 $|U_i^{(T)}|$ 上报云 $A$。注意 $A_i$ 没有 $s_A$，无法解密 $C_i$。

6. **云层全局聚合**。云聚合所有边缘密文：
   $$
   C=(C_0,C_1),\quad C_0=\sum_{i=1}^l C_{i,0}\pmod q,\quad C_1=\sum_{i=1}^l C_{i,1}\pmod q.
   $$
   其相位满足：
   $$
   C_0+C_1\cdot s_A\approx\Delta\sum_{i=1}^l\sum_{j\in U_i^{(T)}} m_{i,j}\pmod q.
   $$

7. **云阈值解密**。云向任意在线边缘子集 $T\subseteq[l]$，$|T|\ge t$ 请求部分解密份额。每个边缘 $A_i$ 用其份额 $s_{A,i}$ 计算：
   $$
   pd_i=C_1\cdot s_{A,i}+\eta_i\pmod q,
   $$
   其中 $\eta_i\leftarrow\chi_e^{\text{large}}$。云收到后重构：
   $$
   \mu=\sum_{i\in T}\lambda_i^{(T)} pd_i\approx C_1\cdot s_A\pmod q.
   $$

8. **全局解密与轻量验证**。云计算：
   $$
   M=C_0+\mu\pmod q,\qquad w_G^{(r+1)}=w^{(r)}-\eta\cdot\frac{1}{N}\textsf{Dcd}\!\left(\frac{M}{\Delta}\right),
   $$
   其中 $N=\sum_i |U_i^{(T)}|$。云进一步做轻量验证：
   - 范数检查：$\|\textsf{Dcd}(M/\Delta)\|_2\le \Theta$（预设阈值）；
   - 哈希链检查：确认边缘上传的 $C_i$ 与 UAV 哈希 $h_{i,j}$ 聚合一致（边缘上传聚合哈希 $H_i=\sum_{j\in U_i^{(T)}} h_{i,j}$，云可比对）。
   若验证失败则丢弃该轮。

**公式闭环检查**：
- 密钥：$b_A=-as_A+e_A$；
- 份额：$s_A=\sum_{i\in T}\lambda_i^{(T)}s_{A,i}$；
- 加密：$c_{i,j,0}+c_{i,j,1}s_A\approx\Delta m_{i,j}$；
- 边缘聚合：$C_{i,0}+C_{i,1}s_A\approx\Delta\sum_j m_{i,j}$；
- 云聚合：$C_0+C_1s_A\approx\Delta\sum_i\sum_j m_{i,j}$；
- 部分解密：$pd_i\approx C_1s_{A,i}$；
- 重构：$\mu\approx C_1s_A$；
- 解密：$M\approx\Delta\sum_i\sum_j m_{i,j}$。

**与 paperlist 的差异总结**：UEC-BR 把 `26-PPBR` 的 BFV+SA 替换为 CKKS+阈值云解密，使边缘不掌握解密密钥；把 `25-SKHE` 的云端 TEE 替换为边缘阈值份额，降低对机载/云端硬件安全区的依赖；把 `25-FedPHE` 的标准 cross-silo 选择机制扩展到层次型边缘过滤；与 `26-Ftopk` 相比，UEC-BR 使用 RLWE/CKKS 而非 Paillier，更适配浮点模型。

---
### 方案 4：UCL-HFL — 自组织 UAV 簇 + 分层 DKG + 簇间阈值解密

#### 5.1 场景与应用动机

设想一片广阔灾区中多架 UAV 自组织执行搜救任务：UAV 根据位置、电量、信号强度动态结成若干簇，每簇选举一个簇头（Cluster Head, CH）作为边缘聚合节点；簇头再与远程云或地面指挥中心交互。由于 UAV 高速移动，簇成员与簇头每轮都可能变化；同时簇头本身可能被俘获或掉线。要求：

- 簇结构动态变化时不需要重新初始化全局密钥；
- 任何单个簇头不能解密全局聚合结果；
- 任意 $t$ 个簇头在线即可解密；
- 簇内 UAV 掉线不影响全局解密；
- 不依赖 TTP/TEE。

#### 5.2 要解决的问题（与现有工作的差距）

- `17-SecAgg` 的 pairwise 掩码要求参与者集合相对固定，簇变化时需重新协商所有 pairwise 种子；
- `26-CDKS-LSS` 的 P2P 份额交换量随 $n$ 增长，UAV 高速移动导致份额再分配困难；
- `25-SKHE` 固定云-边-端拓扑，边缘节点就是固定 EN，不支持动态簇头；
- `24-SecFed` 用 TEE 补充密文，簇头若被俘获且 TEE 被攻破则全局密钥丢失；
- `25-MASER` 的多密钥 CKKS 需要所有客户端在线 partial decryption，无法容忍簇头掉线。

**UCL-HFL 的核心差距**：把全局 RLWE 密钥 $s$ 通过**两层秘密分享**分散到簇头：每个簇头 $c$ 只掌握 $s$ 的一个 Shamir 份额 $\tilde s_c$，而 $\tilde s_c$ 又是由簇内 UAV 的子份额聚合而来。簇成员变化时只需在簇内重新分配子份额，不影响全局 $\tilde s_c$；簇头变化时通过 proactive resharing 把旧簇头份额迁移到新簇头，无需重新生成 $s$。

#### 5.3 创新点与总体设计目标

1. **分层 DKG**：簇头级密钥份额由簇内 UAV 的本地秘密 $s_i$ 聚合而成，全局公钥 $pk$ 由所有簇头公钥 $pk_c$ 相加得到，无 TTP。
2. **簇内密文聚合**：UAV 用全局公钥加密，簇头仅做 CKKS 加法聚合，不见明文。
3. **簇间阈值解密**：任意 $t$ 个簇头即可提供部分解密份额，云重构全局模型；单个/少数簇头掉线或被俘不影响解密。
4. **动态份额再分配**：簇分裂/合并/簇头更换时，旧簇头通过 proactive Shamir resharing 把其全局份额迁移到新簇头集合，保持 $s$ 不变。
5. **与现有方案的关系**：比 `17-SecAgg` 更适配动态拓扑；比 `26-CDKS-LSS` 减少 P2P 份额量（份额只在簇内和簇头两层交换）；比 `25-SKHE` 去掉 TA/TEE；比 `25-MASER` 支持簇头掉线。

#### 5.4 威胁模型

- **UAV 客户端**：诚实但好奇；簇内最多 $<t_c$ 个合谋无法重构簇秘密分量（$t_c$ 为簇内阈值）。
- **簇头 CH**：诚实但好奇，可与云合谋；但云与 $<t$ 个簇头合谋无法重构全局 $s$。
- **云服务器**：诚实但好奇，只聚合簇头密文与部分解密份额。
- **外部敌手**：可监听、可发动 Sybil/GPS 欺骗；方案依赖认证信道与身份绑定抵御 Sybil。
- **掉线容忍**：簇内聚合无需秘密，任意子集 UAV 上传即可；全局解密需 $t$-out-of-$l$ 簇头在线；簇头更换通过 proactive resharing 完成。
- **可信实体**：无在线 TTP/TEE/KDC；初始 CRS/PRG 种子公开；簇头身份通过预共享证书/PKI 认证。

#### 5.5 方案设计：实体、流程、闭环公式推导

**实体集合**：
- UAV $i$：簇 $C_c$ 的成员；
- $CH_c$：簇 $c$ 的簇头（由簇内 UAV 选举产生，如电量/连接度最高者）；
- Cloud：远程云/指挥中心。

**流程（初始化阶段）**：

1. **簇头级 DKG**。每个簇头 $CH_c$ 本地采样一个簇级秘密分量 $s_c\leftarrow\chi_s$ 与误差 $e_c\leftarrow\chi_e$，发布簇公钥：
   $$
   b_c=-a\cdot s_c+e_c\pmod q,\qquad pk_c=(b_c,a).
   $$
   云（或所有簇头共同）聚合全局公钥：
   $$
   b=\sum_{c=1}^l b_c=-a\cdot\underbrace{\left(\sum_{c=1}^l s_c\right)}_{s}+\underbrace{\sum_{c=1}^l e_c}_{e}\pmod q.
   $$
   全局公钥 $pk=(b,a)$ 公开给所有 UAV；全局秘密 $s=\sum_c s_c$ 不单独存在。

2. **簇间 $(t,l)$-Shamir 阈值化**。每个簇头 $CH_c$ 将 $s_c$ 用 $(t,l)$-Shamir 拆分为 $f_c(X)$（$f_c(0)=s_c$，$\deg f_c=t-1$），把份额 $f_c(c')$ 安全发送给其它簇头 $CH_{c'}$。每个簇头 $c'$ 聚合所有簇头的份额：
   $$
   \tilde s_{c'}=\sum_{c=1}^l f_c(c')=F(c')\pmod q,
   $$
   其中 $F(X)=\sum_c f_c(X)$ 满足 $F(0)=s$。因此 $\tilde s_{c'}$ 是全局秘密 $s$ 的一个 Shamir 份额。

3. **簇内子份额绑定（可选，用于簇头更换）**。簇头 $CH_c$ 还可以把 $s_c$ 在簇内用 $(t_c,n_c)$-Shamir 分享给 UAV 成员。当 $CH_c$ 需要更换时，任意 $t_c$ 个 UAV 可重构 $s_c$ 并重新执行步骤 2 给新簇头，而**全局 $s$ 与 $pk$ 保持不变**。

**流程（每轮训练）**：

4. **簇内 UAV 加密**。UAV $i\in C_c$ 训练得到更新 $w_i$，编码缩放 $m_i=\Delta\cdot\textsf{Ecd}(w_i)$，采样 $v_i,e_{i,0},e_{i,1}$，用全局公钥加密：
   $$
   c_{i,0}=v_i\cdot b+m_i+e_{i,0}\pmod q,\qquad c_{i,1}=v_i\cdot a+e_{i,1}\pmod q.
   $$
   UAV 将 $c_i$ 发送给 $CH_c$。

5. **簇内密文聚合**。$CH_c$ 聚合簇内 UAV 密文：
   $$
   C_c=(C_{c,0},C_{c,1}),\quad C_{c,0}=\sum_{i\in C_c} c_{i,0}\pmod q,\quad C_{c,1}=\sum_{i\in C_c} c_{i,1}\pmod q.
   $$
   将 $C_c$ 上报云。

6. **云全局聚合**。云聚合所有簇密文：
   $$
   C=(C_0,C_1),\quad C_0=\sum_{c=1}^l C_{c,0}\pmod q,\quad C_1=\sum_{c=1}^l C_{c,1}\pmod q.
   $$
   相位满足：
   $$
   C_0+C_1\cdot s\approx\Delta\sum_{c=1}^l\sum_{i\in C_c} m_i\pmod q.
   $$

7. **簇间阈值解密**。云向任意 $t$ 个在线簇头 $T\subseteq[l]$ 请求部分解密。每个簇头 $CH_{c'}$ 用全局份额 $\tilde s_{c'}$ 计算：
   $$
   pd_{c'}=C_1\cdot\tilde s_{c'}+\eta_{c'}\pmod q,
   $$
   其中 $\eta_{c'}\leftarrow\chi_e^{\text{large}}$。云重构：
   $$
   \mu=\sum_{c'\in T}\lambda_{c'}^{(T)} pd_{c'}\approx C_1\cdot s\pmod q.
   $$

8. **全局解密与模型下发**。云计算：
   $$
   M=C_0+\mu\pmod q,\qquad w_G=\frac{1}{N}\textsf{Dcd}\!\left(\frac{M}{\Delta}\right),
   $$
   其中 $N=\sum_c |C_c|$，并将 $w_G$ 下发给所有簇头，再由簇头转发给 UAV。

**簇头更换与 proactive resharing（动态拓扑维护）**：

当簇 $c$ 的簇头因移动/掉线/被俘获需要更换为 $CH_c^{\text{new}}$ 时：
- 旧簇头 $CH_c$（或簇内任意 $t_c$ 个 UAV）知道 $s_c$；
- $CH_c$ 重新采样一个 $(t,l)$ 次多项式 $g_c(X)$ 满足 $g_c(0)=s_c$，并把新份额 $g_c(c')$ 安全发送给所有（新的）簇头 $c'$；
- 其它簇头 $c''$ 收到新的 $g_c(c'')$ 后，用它替换旧的 $f_c(c'')$，重新计算自己的全局份额 $\tilde s_{c''}=\sum_c g_c(c'')$；
- 全局 $s$ 不变，全局公钥 $pk$ 不变，无需重新生成 UAV 加密密钥。

**公式闭环检查**：
- DKG：$b=\sum_c b_c=-as+e$；
- 簇间份额：$\tilde s_{c'}=F(c')$，$F(0)=s$；
- 加密：$c_{i,0}+c_{i,1}s\approx\Delta m_i$；
- 簇聚合：$C_{c,0}+C_{c,1}s\approx\Delta\sum_{i\in C_c} m_i$；
- 云聚合：$C_0+C_1s\approx\Delta\sum_c\sum_i m_i$；
- 部分解密：$pd_{c'}\approx C_1\tilde s_{c'}$；
- 重构：$\mu\approx C_1s$；
- 解密：$M\approx\Delta\sum_c\sum_i m_i$。

**与 paperlist 的差异总结**：UCL-HFL 把 `26-CDKS-LSS` 的 LSS 思想从“所有参与方两两交换份额”压缩为“簇内+簇头两层交换”，并通过 proactive resharing 支持 UAV 高速移动；与 `25-SKHE` 相比，UCL-HFL 不需要 TA/TEE，且把密钥管理下放到自组织簇头；与 `26-PPBR` 相比，UCL-HFL 用 CKKS 阈值解密替代 BFV+SA，使簇头掉线不再阻断全局解密；与 `25-MASER` 相比，UCL-HFL 的多密钥结构嵌入阈值门限，支持任意 $t$ 个簇头完成 partial decryption。

---
## 5. 未来工作与 Open Problems

基于第 1–4 节的分析，下面是值得进一步探索的几个方向：

1. **动态参与者下的阈值维护**。UAV 群成员每轮都在变化，而 Shamir 阈值方案的公开求值点 $\alpha_i$ 通常绑定固定身份。当新 UAV 加入或旧 UAV 永久退出时，如何在不重启全局密钥的前提下安全地增减份额、更新 Lagrange 系数，是一个开放问题。Proactive secret sharing 与分布式伪随机函数（DPRF）可能是突破口。

2. **恶意多数与投毒攻击下的 MKHE 聚合**。本报告四个方案主要覆盖诚实但好奇模型（方案 3 允许边缘层拜占庭过滤，但全局解密仍假设簇头/云半诚实）。如何在密文域直接实现拜占庭鲁棒聚合（例如：同态范数裁剪、同态中位数、零知识证明输入范围）而不让聚合者看到明文，仍是理论与工程双重挑战。

3. **与通信压缩正交叠加**。`24-ResFed` 的残差压缩、`25-FedPHE` 的 pack-level 稀疏、`25-MASER` 的共识剪枝都可以叠加到本报告的方案中。如何在加密前先对更新稀疏化/量化，同时保证 RLWE 噪声不溢出、精度损失可控，需要精细的参数调优与误差分析。

4. **能量-延迟联合优化**。UAV 电池与链路质量高度动态，未来的方案应当把加密/通信开销纳入能量模型，并通过在线学习（如 MDP/强化学习）动态选择：是否加密、是否参与、是否担任簇头、是否使用阈值 $t$。

5. **空域攻击的完整防御**。GPS 欺骗、Sybil、MITM 需要身份认证、位置证明、可验证聚合等机制。本报告假设存在认证信道/PKI，但如何在无基础设施环境下用物理层特征、视觉/雷达相对定位或区块链轻量身份实现 Sybil 抵抗，仍是开放问题。

6. **形式化安全证明与实现**。四个方案虽然给出了公式闭环，但完整的安全性证明（IND-CPA、阈值语义安全、可组合安全）以及基于 Microsoft SEAL / Lattigo / OpenMHE 的原型实现、UAV 仿真链路上的性能评估，都是下一步必须完成的工作。

7. **后量子签名与认证**。RLWE/CKKS 提供后量子加密，但认证仍常依赖传统 ECDSA/Schnorr。设计基于格的后量子签名用于 UAV 身份认证与份额完整性，将使整个系统在后量子威胁模型下更自洽。

---

## 附录：四个新方案核心对比

| 维度 | UMT-FL（方案 1） | UDK-FL（方案 2） | UEC-BR（方案 3） | UCL-HFL（方案 4） |
|---|---|---|---|---|
| 架构 | 标准参数服务器 FL | 标准 cross-silo FL | UAV-Edge-Cloud HFL | 自组织 UAV 簇 HFL |
| HE 原语 | 多密钥 CKKS | 单公钥 RLWE/CKKS | 单公钥 CKKS（云密钥） | 多密钥公钥聚合型 CKKS（簇头各自持钥，全局公钥为簇公钥之和） |
| 密钥管理 | 各 UAV 独立 $s_i$；LSS 分享部分解密值 | DKG：$s=\sum_i s_i$；Shamir 份额分散在客户端 | KGA 离线生成云密钥；Shamir 份额分散在边缘 | 分层 DKG：簇头 $s_c$；全局 $s=\sum_c s_c$；两层 Shamir |
| 阈值 | $t$-out-of-$n$（份额层） | $t$-out-of-$n$（解密层） | $t$-out-of-$l$（云解密） | $t$-out-of-$l$（簇间解密）+$t_c$-out-of-$n_c$（簇内 resharing） |
| 掉线容忍 | 密文上传后任意 $n-t$ 掉线 | 最多 $n-t$ 解密掉线；选择阶段可过滤 straggler | 边缘 UAV 任意掉线；最多 $l-t$ 边缘掉线 | 簇内 UAV 任意掉线；最多 $l-t$ 簇头掉线；簇头可更换 |
| 可信实体 | 无在线 TTP/TEE/KDC | 无在线 TTP | 离线 KGA（可替换为 DKG） | 无在线 TTP/TEE |
| 拜占庭鲁棒 | 半诚实模型 | 半诚实模型 | 边缘层 DP-sketch 过滤 | 半诚实模型（可叠加簇内过滤） |
| 主要优势 | 无 TTP、阈值 MKHE、SA 辅助份额转发 | 无 TTP、打包轻量、贡献感知选择 | 半可信边缘、边缘密文聚合、云阈值解密 | 动态拓扑、簇头即边缘、两层阈值 |
| 主要代价 | P2P 份额仍存在（经服务器加密转发） | 需要 ZKPoK 或广播验证 $pk_i$ | KGA 离线存在；需要 DP-sketch | 簇头选举与 proactive resharing 协议复杂 |

---

**报告文件路径**：`E:\GithubProjects\HFL-Attack\paperlist\survey_proposals.md`
