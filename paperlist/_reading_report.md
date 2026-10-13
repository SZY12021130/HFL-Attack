# 同态加密 × 联邦学习 论文阅读与方案构建报告

> 范围：`paperlist/` 目录下 11 篇与 HE+FL 直接相关的 PDF。
> 视角：RLWE 派密码 × 联邦鲁棒性 × 无人机/边缘辅助层次联邦学习。
> 目标：(1) 总结这 11 篇方案的模式与内在发展链；(2) 在此基础上提出 5 个**结构与逻辑上优于本目录最新研究**的方案（2 个标准 FL + 3 个 HFL），每个方案给出场景、问题、创新、威胁模型、闭环实体交互与 RLWE 公式推导。

---

## 0. 论文清单（按重命名后文件）

| 编号 | 文件 | 简称 | 年份 | 出处 | 范式（密钥×架构） | 信任锚 | 解决的核心问题 |
|---|---|---|---|---|---|---|---|
| P1 | 17-SecAgg.pdf | SecAgg | 2017 | ACM CCS | 秘密共享（不加密）× 标准 FL | 半诚实服务器 + 客户端抗共谋 | 高维向量安全聚合、抗掉线 |
| P2 | 19-CDKS.pdf | CDKS / MK-CKKS | 2019 | ACM CCS | 多密钥 CKKS × 标准 FL | 无可信第三方 | 多密钥全同态聚合与外包推理 |
| P3 | 24-ResFed.pdf | ResFed | 2024 | IEEE TWC | 残差压缩（与密码正交）× 标准 FL | 标准 FL 假设 | 通信压缩 |
| P4 | 24-SecFed.pdf | SecFed / EMK-BFV | 2024 | IEEE TIFS | 多密钥 BFV + TEE × 标准 FL | TEE + 半诚实客户端 | 多密钥 BFV 抗掉线、TEE 卸载 |
| P5 | 25-ESAFL.pdf | ESAFL / ESHE | 2025 | IEEE TIFS | 单密钥聚合体 × 标准 FL | 半诚实服务器 | 无 TTP 抗密文分发与共谋 |
| P6 | 25-FedPHE.pdf | FedPHE | 2025 | IEEE TIFS | 单密钥 CKKS（打包）× 标准 FL | KDC 半诚实 | 打包压缩 + 抗 straggler + 抗合谋 |
| P7 | 25-MASER.pdf | MASER | 2025 | IEEE TDSC | 多密钥 CKKS × 标准 FL | 半诚实服务器 | MKHE 密文体积 / 密文分摊 |
| P8 | 25-SKHE.pdf | SKHE | 2025 | IEEE IoT-J | 单密钥 CKKS+ECC+TEE+Shamir × 端-边-云 IoT FL | TEE + 半诚实边 | 抗合谋 + 抗掉线 + 边云协同 |
| P9 | 26-CDKS-LSS.pdf | CDKS-LSS | 2026 | Inf. Sci. | 多密钥 CKKS + Shamir LSS × 标准 FL | 无 TTP、半诚实 | 抗部分解密份额泄露、阈值 t/N |
| P10 | 26-Ftopk.pdf | DSVFL | 2026 | IEEE TIFS | Paillier + Shamir（单密钥） × 标准 FL | TA 半诚实 | 可验证 + 抗掉线 + top-k 加速 |
| P11 | 26-PPBR.pdf | PPBR | 2026 | IEEE TMC | CLDP + PLHE（单密钥） × 端-边-云 HFL | 单一边服务器+云 | 抗拜占庭 + Non-IID + 隐私 + 抗掉线 |

---

## 1. 模式分类（四个维度）

### 1.1 加密范式维度

| 范式 | 代表 | 优点 | 缺点 |
|---|---|---|---|
| **秘密共享 + 掩码**（明文域） | P1 SecAgg | 通信轻、抗掉线 | 不能抗 server 共谋后看到 Σ |
| **单密钥 HE**（打包） | P5 ESAFL / P6 FedPHE / P8 SKHE / P10 DSVFL | 密文体积小、聚合便宜 | 需 KDC/TA，单点妥协 |
| **多密钥 HE（密文域）** | P2 CDKS / P4 EMK-BFV / P7 MASER / P9 CDKS-LSS | 无 TTP，客户端私钥本地 | 部分解密份额泄露；密文体积大 |
| **混合：HE + Shamir** | P8 SKHE / P9 CDKS-LSS / P10 DSVFL / P11 PPBR | 抗掉线 + 抗共谋 | 系统复杂度上升 |

### 1.2 架构维度

- **标准 FL（端-云）**：P1, P2, P3, P4, P5, P6, P7, P9, P10
- **层次 FL（端-边-云 / 端-UAV-云）**：P8（IoT 边-云）、P11（移动边-云）
- **与密码正交的通信优化**：P3 ResFed（残差压缩）

### 1.3 鲁棒性维度

| 鲁棒目标 | 方案 |
|---|---|
| 抗客户端掉线 | P1, P4, P5, P8, P9, P10, P11 |
| 抗客户端共谋 | P1, P5, P6, P8, P9 |
| 抗服务器作恶（可验证） | **仅 P10 DSVFL** 给出显式验证机制 |
| 抗拜占庭恶意更新 | P11 PPBR（CLDP+PLHE）|
| 抗 UAV/边服务器半诚实 | P8 SKHE（双层 Shamir+TEE）、P11 PPBR |

### 1.4 信任锚维度

- **无 TTP**：P2, P5, P7, P9（依赖密码学保证或聚合体性质）
- **依赖 TEE**：P4, P8（私钥分片托管在 TEE 端）
- **依赖 KDC/TA**：P6 FedPHE, P10 DSVFL（密钥/签名种子分发）
- **依赖半诚实边服务器**：P11 PPBR（PLHE 单密钥托管在边）

---

## 2. 内在发展链（因果递进）

### 2.1 隐私链：从明文聚合到多密钥 HE

```
P1 SecAgg (2017)
   │  用成对 Shamir 掩码做"看不到单个 w_i"的安全聚合
   │  ── 痛点：掩码无抗共谋后门、带宽依然 O(N·d)
   ▼
P2 CDKS/MK-CKKS (2019)
   │  多密钥全同态 + 打包：客户端各自 pk_i，密文域可加可乘
   │  ── 痛点：partial decryption 必须给 server，server(⋃共谋客户端)可逐项还原
   ▼
P4 SecFed / EMK-BFV (2024)
   │  把多密钥 BFV 拆给 TEE 加速，用 TEE 处理"部分解密"
   │  ── 痛点：TEE-HE 频繁交互、TEE 信任扩张
   ▼
P5 ESAFL / ESHE (2025)
   │  RLWE 加性 HE：客户端各自密钥但聚合结果可"直接加"，无 partial decryption
   │  ── 痛点：解密密文仍需一个共享私钥，密钥分发的"分片"问题没消除
   ▼
P7 MASER (2025)
   │  在 MKHE 之上加共识剪枝 + 分片 (slicing) 把密文体积砍掉
   │  ── 痛点：partial decrypt 仍然存在，server 仍能 (在合谋下) 反推
   ▼
P9 CDKS-LSS (2026)
   │  把 partial decrypt 本身用 Shamir LSS 拆分，threshold t/N，无 TTP
   │  ── 痛点：未验证 server 是否篡改聚合、客户端不能 spot-check
```

### 2.2 效率链：密文体积与计算

```
P2 CDKS (2019)        → MKHE 基准：N 客户端各一份密文，模数链深
P3 ResFed (2024)      → 残差代替权重（与密码正交）
P6 FedPHE (2025)      → 单密钥打包 + Top-K 稀疏 + 混淆
P7 MASER (2025)       → 共识剪枝掩码 + 分片并行
P10 DSVFL (2026)      → top-k 选梯度 + Paillier + Shamir
```

### 2.3 鲁棒链：从掉线到拜占庭

```
P1 (2017) 抗掉线
→ P9 (2026) 把抗掉线推到 partial decrypt 阶段
→ P10 (2026) 抗掉线 + 可验证
→ P11 PPBR (2026) 把"抗拜占庭"从客户端签名域 (FedInv 类) 扩展到边层动量域
   ── 痛点：单一边服务器+单 PLHE 密钥，妥协即全边层解密
```

### 2.4 架构链：从端-云到端-边/UAV-云

```
P2/P5/P6/P7/P9 (端-云) 单层
→ P8 SKHE (2025) 端-边-云（IoT，单密钥 CKKS + ECC + TEE）
→ P11 PPBR (2026) 端-边-云移动网络（CLDP+PLHE+拜占庭）
```

---

## 3. 各方案"模式一句话总结"

| 方案 | 一句话 |
|---|---|
| SecAgg | "我加密的不是数据，而是让对方只能拿到求和的掩码协议" |
| CDKS | "全同态的多密钥版，但最后一步要把各方的私钥贡献" |
| ResFed | "传输密文之前先用预测器把要传的东西压到残差里" |
| SecFed | "把多密钥的'heavy lifting' 塞进 TEE" |
| ESAFL | "加性就够了，多密钥就是密文相加，多余的解码能力换安全" |
| FedPHE | "打包进单密钥里，加上稀疏与混淆" |
| MASER | "MKHE 太贵？先共识剪枝再分片" |
| SKHE | "单密钥安全靠 ECC+TEE+Shamir 三件套" |
| CDKS-LSS | "partial decrypt 自己也要再做一次秘密共享" |
| DSVFL | "Paillier + Shamir 加上可验证签名链" |
| PPBR | "边层先签 + 密文包，云层再做拜占庭过滤" |

---

## 4. UAV / 边缘场景下的开放挑战

> 这是本报告提出 5 个新方案的**问题空间**。

1. **半诚实 ≠ 不可信**。UAV/边服务器物理暴露在野外、被劫持/被仿冒的概率显著高于云；现有方案默认半诚实。
2. **带宽-模数链两难**。CKKS 模数链决定可同态运算深度，深度越深密文越大；UAV 链路预算每 bit 都很贵，密文不能膨胀。
3. **掉线/移动性**。UAV 覆盖是临时的、客户端随 UAV 移动会换边；任何要求"全客户端在线"的协议（CDKS、CDKS-LSS）都会断。
4. **拜占庭与隐私正交**。PPBR 在边层做了 Byzantine 过滤但其单密钥 PLHE 在边妥协时全功尽弃；新方案需在"两层密钥 + 拜占庭过滤"上同时发力。
5. **可验证缺口**。除 DSVFL 之外，几乎所有方案都把 server 当半诚实；但 UAV-边-云三层架构下，"边替换聚合结果"或"云替换全局聚合"的攻击在野外场景下不可忽略。
6. **无 TTP 的密钥管理**。UAV 场景不适合常驻 TA；KDC/TA 长期在线假设在跨域、跨国 UAV 协同时几乎不成立。

---

## 5. 五个新方案

> 命名约定：每个方案都给出**目标问题 → 现有方法的局限 → 创新点 → 威胁模型 → 闭环设计**。公式以 RLWE 为基础密码假设（与本目录 P2/P5/P7/P9 一致），便于横向对比。
> 共同记号：环 $R=\mathbb Z[X]/\Phi_M(X)$，模 $q$；CDKS 风格的多密钥密文 $\theta=(c_0, c_1, \ldots, c_k)$ 对应密钥组 $(s_1,\ldots,s_k)$；CKKS 单密钥密文 $\mathsf{ct}=(c_0, c_1)$。

### 5.A 方案 A：**xLSS-CKKS** —— 多密钥 CKKS + 共识稀疏 + 阈值解密的**可审计**标准 FL

> 关键词：标准 FL / 多密钥 / 阈值 LSS / 可审计

#### 5.1 场景
跨机构、跨设备的客户端-云联邦学习（医疗联盟、银行风控联盟），云服务器是公共云，**不假设其诚实**。客户端数量 $N\ge 16$，每轮允许最多 $N-t$ 个掉线。

#### 5.2 要解决的问题
- **P9 CDKS-LSS 已解决**部分解密份额泄露，**但**：
  1. 它假设**服务器诚实**（仅做密文域加法），未验证 server 是否篡改聚合结果。
  2. 它未做密文稀疏/剪枝，密文体积随 $N$、$d$ 线性增长，对带宽是负担。
  3. partial decrypt 仍需要所有客户端在线发送（虽然用 LSS 容忍 $t$ 即可重构），但客户端上传密文本身没有稀疏化，浪费带宽。

#### 5.3 创新点 / 总体设计目标
在 CDKS-LSS 之上，**叠加两层新机制**：
1. **共识 Top-K 稀疏（沿用 P7 MASER 但用 RLWE-friendly mask）**：所有客户端先对本地更新做重要性排序，取 top-$\rho$ 坐标；通过多数投票协商出本轮的"全局重要掩码" $M^t$。
2. **RLWE 同态承诺链**：服务器为每轮聚合 $\mathsf{Agg}^t$ 发布一个同态承诺 $\mathsf{Com}^t$（基于 gadget 向量 $g$ 的 RLWE 承诺 $\mathsf{Com}=g\cdot \mathsf{Agg}+r$，$r$ 来自客户端联合贡献）；客户端可按 Fiat-Shamir 风格 spot-check。
3. **沿用 CDKS-LSS 的 Shamir LSS 阈值 $t$**：partial decryption 上再做一次秘密共享。

#### 5.4 威胁模型
- **服务器**：**主动恶意 (malicious / active)** —— 可篡改聚合、可提前终止、可重放、可替换密文；可与客户端共谋。
- **客户端**：半诚实，可共谋，但至少 $t$ 个客户端**必须**返回 partial-share，否则本轮失败。
- **信道**：经认证信道（TLS），密文可被窃听但不假设信道保密。
- **不假设**：TEE、长期在线的 KDC/TA。

#### 5.5 方案设计（实体 + 交互 + 公式推导）

**实体**：客户端 $\{C_1,\ldots, C_N\}$，服务器 $S$。

**Setup（沿用 CDKS）**：
- 公参 $pp=(d, q, \chi, B_\chi)$，RLWE 假设。
- 每个 $C_i$ 采样 $s_i\leftarrow \chi$，公开 $\mathsf{pk}_i=(-a_i s_i+e_i, a_i)$。
- 全局公钥组 $\mathsf{PK}=(\{\mathsf{pk}_i\})$，共同密文参考集 $T=\{1,\ldots,N\}$。

**轮 $t$ 协议**：

1. **共识掩码 $M^t$（创新点 1）**
   - 每个 $C_i$ 本地训练得 $w_i^t$，计算本地重要性 $r_i^t = \text{TopK}(|w_i^t|, \rho)$。
   - $C_i \to S$: $\hat h_i^t = H(r_i^t)$（仅哈希）。
   - $S$: 收集 $\{\hat h_i^t\}$，按多数票得出 $M^t$。
   - $S \to C_i$: $M^t$。

2. **本地加密**
   - $C_i$ 应用稀疏化：$\tilde w_i^t = w_i^t \odot M^t$。
   - $C_i$ 采样 $r_i\leftarrow\chi$，上传 $\mathsf{com}_i = a\cdot r_i$（盲承诺）。
   - $C_i$ 计算 CDKS 密文（packed single-slot $\tilde w_i^t$）：
     $$\theta_i^t = \mathrm{CDKS.Enc}_{\mathsf{pk}_i}(\tilde w_i^t) = (c_{i,0}, c_{i,1}) \in R_q^2$$
   - $C_i \to S$: $\theta_i^t$，$\mathsf{com}_i$。

3. **服务器聚合 + 承诺发布（创新点 2）**
   - $S$: 密文域求和（CDKS 同态加）：
     $$\theta^t = \mathrm{CDKS.Eval}(+, \{\theta_i^t\}) = \Bigl(\sum_i c_{i,0},\; \sum_i c_{i,1}\Bigr)$$
   - $S$ 选取公开 gadget $g\in R^d$，$d$ 维，发布承诺：
     $$\mathsf{Com}^t = g \cdot \theta^t + \sum_i \mathsf{com}_i \pmod q$$
   - 注意 $\sum_i \mathsf{com}_i = a\cdot(\sum_i r_i)$，这给 $C_i$ 之后能验证 $S$ 是否动过 $\theta^t$。

4. **partial decryption（沿用 P9 但加 LSS）**
   - $C_i$ 计算部分解密（$d_1^t=\theta^t[1]$）：
     $$p_i^t = d_1^t\cdot s_i + e_i', \quad e_i' \leftarrow \phi \;(\mathrm{Var}(\phi)>\mathrm{Var}(\chi))$$
   - $C_i$ 再用 Shamir $(t, N)$ 把 $p_i^t$ 拆为 $N$ 份 $\{(j, p_{i\to j}^t)\}_{j=1}^N$。
   - $C_i \to C_j$: $p_{i\to j}^t$（含认证）。

5. **客户端本地重构并验证**
   - 每个 $C_j$ 收齐 $\ge t$ 个 $p_{i\to *}^t$ 份额，Lagrange 插值恢复出 $p_i^t$。
   - 求和：$P^t = \sum_i p_i^t$。
   - 解密：
     $$m^t \equiv c_0^t + P^t \pmod q$$
   - **可审计 spot-check（创新点 2 闭环）**：客户端抽 $k$ 个 $C_i$，向 $S$ 索要 $\theta^t$ 与 $\mathsf{com}_i$ 的零知识证明 $\pi_i$（基于 RLWE 的 $\Sigma$-protocol）。任意验证失败即触发回滚。

**闭环（一条完整公式链）**：

$$
\begin{aligned}
\tilde w_i^t &= w_i^t \odot M^t \\
\theta^t &\xleftarrow{\mathrm{Enc}} \sum_i \mathrm{CDKS.Enc}_{\mathsf{pk}_i}(\tilde w_i^t) \\
\mathsf{Com}^t &= g\cdot \theta^t + a\cdot \textstyle\sum_i r_i \\
P^t &= \sum_i \bigl( d_1^t s_i + e_i' \bigr) \\
\bar w^t &= \mathrm{CKKS.Decode}\bigl( c_0^t + P^t \bigr) \cdot \Delta^{-1} \;\approx\; \sum_i w_i^t
\end{aligned}
$$

**与现有方案的差异（与 P7/P9 对比）**：
- 比 P9 多：共识稀疏层 + RLWE 承诺层。
- 比 P7 强：partial decrypt 本身 LSS 化，server 合谋也不能直接还原。

---

### 5.B 方案 B：**CARM-FL** —— Consensus-masked Additive-RLWE Multi-key FL（标准 FL）

> 关键词：标准 FL / 联合 DKG（无 TTP）/ 加性 RLWE / SecAgg 风格零和掩码

#### 5.1 场景
跨硅仓（cross-silo）FL：医院/银行/政府部门，$N$ 个客户端，**两两不能完全互信**，但需要"无 TTP + 无 TEE + 抗掉线"。

#### 5.2 要解决的问题
- **P5 ESAFL** 是单密钥聚合体，$N$ 个客户端实际是用**同一个共享秘密** $s$ 的密文加性；密钥如何"分发"到 $N$ 方且无 TTP，ESAFL 没讲清楚。
- **P9 CDKS-LSS** 用 MKHE + LSS，但 partial decrypt 与密文体积仍是瓶颈。
- **本方案目标**：用"联合 DKG（Pedersen DKG 思路）+ 加性 RLWE + SecAgg 风格零和掩码"取代 partial decrypt，达到"密文相加就是聚合 + server 看到的是加了零和掩码的密文"。

#### 5.3 创新点
1. **联合生成公共 RLWE 密钥**（无 TTP）：$N$ 方运行 Pedersen-style 分布式密钥生成，得到全局公钥 $\mathsf{pk}$ 与各方份额 $s_i$，满足 $s=\sum_i s_i$（Shamir 共享，$t$-of-$N$ 重构）。
2. **每轮加性 RLWE 加密 + 零和掩码**：每个 $C_i$ 加密 $w_i + \sigma_i$（$\sigma_i$ 是 SecAgg 风格零和掩码 $\sum_i \sigma_i=0$），server 收到 $\sum_i \mathsf{ct}_i = \mathsf{Enc}(s,\sum_i w_i + \text{noise})$。
3. **解密的阈值化**：与方案 A 一致但只对全局私钥 $s$ 做 Shamir，密文层面彻底消除 partial decryption。

#### 5.4 威胁模型
- **服务器**：半诚实（与 P5 相同；本方案不解决 server 主动篡改）。
- **客户端**：至多 $N-2$ 共谋；至少 2 个诚实以保证零和掩码不被还原。
- **信道**：TLS；密文可被窃听。
- **不假设**：TEE、KDC、TA。

#### 5.5 方案设计

**实体**：$N$ 客户端 $\{C_i\}$，服务器 $S$。

**Setup（Pedersen 风格 DKG，参考 Pedersen 1991）**：
- 公共 $a$。
- 每个 $C_i$ 选 $s_i\leftarrow\chi$，承诺 $t_i = a\cdot s_i + e_i$。
- 通过 VSS/Feldman 公开 $t_i$。
- 联合公钥：$\mathsf{pk}=(b,a)$，$b = a\cdot s + e$，$s=\sum_i s_i$。
- 阈值 $t$ 满足 Lagrange 插值：任何 $t$ 份 $s_i$ 可重构 $s$。

**轮 $t$ 协议**：

1. **本地训练** $w_i^t$。
2. **零和掩码** $\sigma_i^t$（沿用 SecAgg P1 的 Diffie-Hellman 共享种子思想）：$C_i$ 与 $C_j$ 协商种子 $s_{ij}$，令 $\sigma_i = \sum_{j<i} s_{ij} - \sum_{j>i} s_{ij}$，满足 $\sum_i \sigma_i = 0$。
3. **加密**：
   $$\mathsf{ct}_i^t = (c_{i,0},c_{i,1}) = (a\cdot s_i + e_i + w_i^t\cdot\Delta + \sigma_i^t,\; a) \pmod q$$
4. **服务器聚合**：
   $$\begin{aligned}
   \mathsf{ct}_{\mathrm{agg}}^t &= \sum_i \mathsf{ct}_i^t = \Bigl( a\cdot s + \textstyle\sum_i e_i + \Delta\textstyle\sum_i w_i^t + 0,\; N\cdot a \Bigr) \\
   &\equiv \mathrm{Enc}_{\mathsf{pk}}(\Delta\textstyle\sum_i w_i^t) \pmod q \quad (\text{模 } q\text{ 后})
   \end{aligned}$$
5. **阈值解密**：客户端各发自己的份额 $s_i$ 的"部分解密" $p_i = c_{1,\mathrm{agg}}\cdot s_i$（注意 $c_{1,\mathrm{agg}} = N\cdot a$）；$t$ 份可恢复 $c_{1,\mathrm{agg}}\cdot s$。
   $$m^t = c_{0,\mathrm{agg}} + c_{1,\mathrm{agg}}\cdot s \approx \Delta\sum_i w_i^t$$
6. **客户端各自 rescale** 得 $\bar w^t$。

**闭环公式**：

$$
\boxed{\;\bar w^t = \mathrm{CKKS.Decode}\Bigl( c_{0,\mathrm{agg}}^t + c_{1,\mathrm{agg}}^t \cdot \bigl(\sum_{i\in S} \lambda_i s_i\bigr) \Bigr)\cdot \Delta^{-1} \;\approx\; \sum_{i=1}^{N} w_i^t\;}
$$

其中 $S$ 为任一在线 $\ge t$ 个客户端的集合，$\lambda_i$ 为 Lagrange 系数。

**与现有方案的差异**：
- 比 **P5 ESAFL** 强：密钥分发**真正去 TTP**（Pedersen DKG），并补齐了"抗掉线"（阈值解密）。
- 比 **P9 CDKS-LSS** 轻：密文层面就是普通 CKKS，体积小、无 partial decrypt 泄露。
- 与 **P1 SecAgg** 互补：本方案是"加密 + 零和掩码"双保险。

---

### 5.C 方案 C：**CFLT** —— Clustered Federated Learning with LSS Threshold（端-UAV₁-UAV₂-云 HFL）

> 关键词：HFL / 双 UAV 冗余 / PLHE 单密钥 / Shamir 边层阈值 / 拜占庭+隐私联合

#### 5.1 场景
**UAV-assisted HFL**（移动边缘计算场景）：地面移动客户端 $\{U_i\}$ 分布在两架 UAV（$\mathrm{UAV}_A$、$\mathrm{UAV}_B$）共同覆盖区域；UAV 将部分聚合密文转发到云 $C$。两 UAV 间是相互备份的非可信对等节点。

#### 5.2 要解决的问题
- **P11 PPBR** 用 CLDP+PLHE 做了"端层隐私+边层拜占庭+云层聚合"，**但**：
  1. PLHE 是**单密钥**托管在**单一边服务器**；一旦该边被劫持（UAV 物理暴露风险高），整个边层动量被解密。
  2. PPBR 没有"边层解密密钥分片"机制。
- **P8 SKHE** 把私钥分片存 TEE，但 TEE 在 UAV 端并非总是可部署（功耗、ARM TrustZone 可信性争议）。

#### 5.3 创新点
1. **PLHE 密钥的 2-of-2 Shamir 分片**：把 PLHE 的私钥 $s$ 拆给两架 UAV，$\mathrm{UAV}_A$ 持 $s_A$，$\mathrm{UAV}_B$ 持 $s_B$，$s=s_A+s_B$。
2. **保留 PPBR 的 CLDP 客户端层 + 拜占庭过滤边层**：客户端 $U_i$ 发 CLDP 扰动后的符号向量给两 UAV；UAV 做 $k$-medoids 聚类 + 范数裁剪，**但裁剪的密文聚合需要两 UAV 联合才能解**。
3. **残差压缩（沿用 P3 ResFed）**：为 UAV-云链路节省带宽。

#### 5.4 威胁模型
- **任一 UAV**：**主动恶意** —— 可发布篡改的部分密文，但**无法独自解密**。
- **云**：半诚实。
- **客户端**：半诚实，可掉线（PPBR 已支持）。
- **不假设**：TEE、TA。

#### 5.5 方案设计

**实体**：移动客户端 $\{U_i\}_{i=1}^{n}$，$\mathrm{UAV}_A$，$\mathrm{UAV}_B$，云 $C$。

**Setup**：
- TA 一次性生成 PLHE 密钥（CKKS 单密钥风格）：$(s, a, e)$，$b = a\cdot s + e$。
- TA 把 $s$ 做 2-of-2 Shamir：$s_A + s_B = s$。下发 $s_A$ 给 $\mathrm{UAV}_A$、$s_B$ 给 $\mathrm{UAV}_B$，**TA 离线**。
- 公参 $(a, b)$ 公开。

**轮 $t$ 协议**：

1. **客户端层**（沿用 PPBR）：
   - $U_i$ 训练 $w_i^t$，发 $\tilde s_i^t = \mathrm{sign}(w_i^t) + \mathrm{Lap}(\Delta/\varepsilon)$ 给 $\mathrm{UAV}_A$（用于聚类），发 $\mathsf{ct}_i^t = \mathrm{PLHE.Enc}_{pk}(w_i^t \cdot \Delta)$ 给两 UAV。

2. **边层（UAV）聚合**：
   - $\mathrm{UAV}_A$ 与 $\mathrm{UAV}_B$ 各自由 $\tilde s_i^t$ 做 $k$-medoids 聚类，得簇 $\{C_1,\ldots, C_K\}$。
   - 对每簇 $C_j$ 各自做范数裁剪：
     $$\mathsf{agg}_j^t = \min\bigl(\|\sum_{i\in C_j} \mathsf{ct}_i^t\|,\; \tau\bigr) \cdot \frac{\sum_{i\in C_j} \mathsf{ct}_i^t}{\|\sum_{i\in C_j} \mathsf{ct}_i^t\|}$$
   - **两 UAV 各自只能算"部分"**：$\mathrm{UAV}_A$ 算 $\mathsf{agg}_j^t$ 密文的"左半 partial" $p_{A,j}^t = \langle \mathsf{agg}_j^t.d_1, s_A\rangle + e_A$；$\mathrm{UAV}_B$ 算 $p_{B,j}^t = \langle \mathsf{agg}_j^t.d_1, s_B\rangle + e_B$。

3. **云层**：
   - $C$ 收 $\mathsf{agg}_j^t$，$p_{A,j}^t$，$p_{B,j}^t$。
   - 云做最终解密：
     $$m_j^t = \mathsf{agg}_j^t.c_0 + p_{A,j}^t + p_{B,j}^t \pmod q$$
   - 反归一化得 $\bar w_j^t$；对 $K$ 簇求均值得 $\bar w_{\mathrm{edge}}^t$。
   - 跨 UAV 区（若 $\mathrm{UAV}_A$ 与 $\mathrm{UAV}_B$ 是两不同覆盖区，跨区聚合在云做）：$\bar w^t = \mathrm{mean}_j(\bar w_j^t)$。

4. **下传**（残差压缩）：
   - $C$ 计算残差 $\delta^t = \bar w^t - \hat w^t$（$\hat w^t$ 是 $C$ 的预测器输出，沿用 P3 ResFed 思想）。
   - $\delta^t$ 经量化/编码后下传至客户端。

**闭环公式**：

$$
\boxed{
\begin{aligned}
\mathsf{ct}_i^t &= (b_i + w_i^t\Delta,\; a_i) \\
\mathsf{agg}_j^t &= \sum_{i\in C_j} \mathsf{ct}_i^t \quad \text{(密文域加法)} \\
m_j^t &= \mathsf{agg}_j^t.c_0 + \langle\mathsf{agg}_j^t.d_1, s_A+s_B\rangle + e_A+e_B \\
      &\equiv \Delta \sum_{i\in C_j} w_i^t \pmod q \quad (\text{忽略噪声}) \\
\bar w_{\mathrm{edge}}^t &= \frac{1}{K}\sum_j m_j^t \cdot \Delta^{-1}
\end{aligned}
}
$$

**与现有方案的差异**：
- 比 **P11 PPBR** 强：边层密钥分片，单 UAV 妥协不解密。
- 比 **P8 SKHE** 轻：无需 TEE，PKG 离线即可。

---

### 5.D 方案 D：**VEGAS** —— Verifiable Edge Gradient Aggregation via Secrets（端-UAV-云 HFL，**可验证**）

> 关键词：HFL / RLWE 承诺 / 客户端可验证 / 拜占庭+验证双防护

#### 5.1 场景
UAV 辅助的广域 HFL：客户端稀疏分布，UAV 做边层聚合，云 $C$ 做全局聚合并广播。**客户端不信任 UAV/云**：可能篡改聚合，可能提前终止、可能替换全局模型。

#### 5.2 要解决的问题
- **P11 PPBR** 假设**边层半诚实**，未提供客户端对云的验证。
- **P10 DSVFL** 给出 Paillier+Shamir 风格验证，但仅限单服务器。
- **本方案**：把"RLWE 同态承诺 + Fiat-Shamir 抽查"嵌入到 HFL 的端-边-云链路上，使任何客户端都能以 $O(1)$ 的通信验证本轮全局模型。

#### 5.3 创新点
1. **每客户端 RLWE 同态承诺**：$C_i$ 对自己的 $w_i^t$ 发布承诺 $\mathsf{Com}_i$；承诺是 RLWE 上的"加法同态"：$\mathsf{Com}(\sum_i w_i)=\sum_i \mathsf{Com}_i$。
2. **UAV 端发布中间承诺**：$\mathrm{UAV}$ 收到所有 $\mathsf{Com}_i$ 与密文，发布 $\mathsf{Com}_{\mathrm{edge}} = \sum_i \mathsf{Com}_i$ + 密文 $\mathsf{ct}_{\mathrm{edge}}$。
3. **云端最终承诺 + 公开抽查**：云发布 $\mathsf{Com}_{\mathrm{global}}$；客户端可对云发起 Fiat-Shamir 挑战，要求云出示 $\mathsf{Com}_{\mathrm{edge}} \to \mathsf{Com}_{\mathrm{global}}$ 的"承诺转换证明"。

#### 5.4 威胁模型
- **云**：**主动恶意**（最高威胁）。
- **UAV**：**半诚实**（通常可信但仍验证）。
- **客户端**：半诚实，可掉线，可与云共谋，但本方案验证不依赖客户端在线。

#### 5.5 方案设计

**实体**：$n$ 客户端 $\{U_i\}$，UAV $E$，云 $C$。

**Setup**：
- 公参 $a$、承诺公钥 $\mathsf{ck}=(a_c, b_c)$，$\mathsf{Com}(m;r) = (a_c r + m,\; b_c)$（GSW 风格）。
- 任意 PPT 验证者可借助 $a_c, b_c$ 检查承诺的"打开正确性"。

**轮 $t$ 协议**：

1. **客户端 $U_i$**：
   - 训练 $w_i^t$；稀疏化 $\tilde w_i^t = \mathrm{TopK}(w_i^t, \rho)$。
   - 选随机 $r_i\leftarrow\chi$，承诺 $\mathsf{Com}_i = (a_c r_i + \tilde w_i^t,\; b_c)$。
   - 同时 PLHE 加密 $\mathsf{ct}_i = \mathrm{PLHE.Enc}_{pk}(\tilde w_i^t \cdot \Delta)$。
   - $U_i \to E$: $\mathsf{Com}_i$，$\mathsf{ct}_i$。

2. **UAV $E$**：
   - 聚合并承诺（利用承诺加法同态）：
     $$\mathsf{Com}_{\mathrm{edge}} = \sum_i \mathsf{Com}_i = (a_c \sum_i r_i + \sum_i \tilde w_i^t,\; n b_c)$$
   - 聚合密文 $\mathsf{ct}_{\mathrm{edge}} = \sum_i \mathsf{ct}_i$。
   - $E \to C$: $\mathsf{Com}_{\mathrm{edge}}$，$\mathsf{ct}_{\mathrm{edge}}$，附上"承诺打开"零知识证明 $\pi_E$（证明 $\mathsf{Com}_{\mathrm{edge}}$ 确由 $\sum_i \tilde w_i^t$ 打开）。

3. **云 $C$**：
   - 收到后做最终聚合（这里就是单点）：$\mathsf{Com}_{\mathrm{global}} = \mathsf{Com}_{\mathrm{edge}}$（本方案云不修改中间值，只转发到所有客户端）。
   - 客户端收到 $\mathsf{Com}_{\mathrm{global}}$，可发起挑战：
     - 选 $t$ 个客户端 $C_{i_1},\ldots, C_{i_t}$，向云索要 $\tilde w_{i_k}^t$ 与 $r_{i_k}$；
     - 自己验证 $\mathsf{Com}_{i_k} = (a_c r_{i_k} + \tilde w_{i_k}^t,\; b_c)$；
     - 验证加和等于 $\mathsf{Com}_{\mathrm{global}}$。
   - 同时，PLHE 密文域做"开方验证"：客户端要求 UAV 给出对 $\mathsf{ct}_{\mathrm{edge}}$ 的解密部分 $p_E$，与云对全局聚合的明文承诺互校。

4. **客户端重构全局模型**（沿用 P8 风格阈值解密）：
   - 客户端各自贡献 LSS 份额 $s_{i,\to j}$，$t$ 份可解 $\mathsf{ct}_{\mathrm{edge}}$ 得 $\bar w^t$。

**闭环公式**：

$$
\boxed{
\begin{aligned}
\mathsf{Com}_i &= (a_c r_i + \tilde w_i^t,\; b_c) \\
\mathsf{Com}_{\mathrm{edge}} &= \sum_i \mathsf{Com}_i = (a_c R + W,\; n b_c) \quad (R=\sum_i r_i, W=\sum_i \tilde w_i^t) \\
\bar w^t &= \mathrm{CKKS.Decode}\bigl(\mathrm{PartialDec}(\mathsf{ct}_{\mathrm{edge}},\, s_{\mathrm{Lagrange}})\bigr) \cdot \Delta^{-1} \;\approx\; \sum_i \tilde w_i^t
\end{aligned}
}
$$

**与现有方案的差异**：
- 比 **P11 PPBR** 强：客户端可主动验证云是否篡改。
- 比 **P10 DSVFL** 强：可嵌入到 HFL（端-边-云），不只是单服务器。

---

### 5.E 方案 E：**DRB-HFL** —— Differential & RLWE-Buffered HFL with Silo LSS（端-UAV-云 HFL，**带宽友好**）

> 关键词：HFL / 跨硅仓 / 单密钥 per-silo / FedPHE 打包 + ResFed 残差 / 硅仓内 Shamir

#### 5.1 场景
**多 UAV 跨区域协同**（例如应急通信、森林防火、边境巡检）：$L$ 个 UAV，每个 UAV 是某一"硅仓"（silo）的边缘聚合器；每 silo 内有 $N_l$ 个客户端；UAV 间长距离链路带宽极稀缺。**重点问题：UAV-云链路带宽**。

#### 5.2 要解决的问题
- **P11 PPBR** 没有"跨 silo"的密钥管理；UAV-云端是云再做一次单密钥聚合。
- **P6 FedPHE** 的"打包 + 稀疏 + 混淆"是为单密钥标准 FL 设计，没有跨 silo 协同。
- **P3 ResFed** 是密码正交的压缩，但与 HE 不联合设计。
- **本方案**：把 FedPHE 的"打包 + 稀疏 + 混淆"放到每 UAV（per-silo），silo 内用单密钥 CKKS；UAV 聚合后**仅发送一次密文**到云；云侧用 CDKS 风格 MKHE 合并跨 silo 密文；每 silo 内 $t$-of-$N_l$ Shamir 抗掉线。

#### 5.3 创新点
1. **Per-silo 单密钥 + 打包**：每 silo 共享 $\mathsf{pk}_l$，客户端稀疏+混淆后加密；UAV 把 $N_l$ 份密文聚合成**1 份**密文（密文加法同态），UAV-云链路负载 = $L$ 份密文（与 silo 数成正比，与客户端数无关）。
2. **残差压缩（P3 思想）**：UAV 端的预测器 $h_{\mathrm{predict}}$ 让"边到云的密文"再压一次。
3. **Silo 内 Shamir + 跨 silo CDKS**：silo 内的 $\mathsf{sk}_l$ 由 $N_l$ 个客户端 Shamir 共享（$t_l$-of-$N_l$），UAV 持 $\mathsf{pk}_l$ 但**不解密**；云做跨 silo MKHE 聚合；最终 partial decrypt 由各 silo 在线客户端联合完成。

#### 5.4 威胁模型
- **UAV**：半诚实（**不解密**密文，仅做密文加法）。
- **云**：半诚实。
- **Silo 内客户端**：半诚实，可掉线（$N_l - t_l$ 仍可完成）；共谋不超过 $t_l - 1$。
- **跨 silo**：无信任关系，密码保证。

#### 5.5 方案设计

**实体**：$L$ 个 silo，silo $l$ 有 UAV $E_l$，客户端 $\{C_{l,i}\}_{i=1}^{N_l}$；云 $C$。

**Setup**：
- 每 silo 共享单密钥 $(\mathsf{sk}_l, \mathsf{pk}_l)$，$\mathsf{sk}_l$ Shamir 拆给 silo 内 $N_l$ 客户端：$\mathsf{sk}_l$ 由 $\{s_{l,i}\}_{i=1}^{N_l}$ 共享，$t_l$-of-$N_l$ 阈值。

**轮 $t$ 协议**：

1. **客户端 $C_{l,i}$**：
   - $w_{l,i}^t$ → 稀疏 $\tilde w_{l,i}^t = \mathrm{TopK}(w_{l,i}^t, \rho)$ → 混淆 $\hat w_{l,i}^t = \tilde w_{l,i}^t + \sigma_{l,i}$（$\sigma_{l,i}$ 是 FedPHE 风格的本地零和掩码，由 KDC 周期性下发的 PRG 种子生成）。
   - 加密：$\mathsf{ct}_{l,i}^t = \mathrm{CKKS.Enc}_{\mathsf{pk}_l}(\hat w_{l,i}^t \cdot \Delta)$。
   - $C_{l,i} \to E_l$: $\mathsf{ct}_{l,i}^t$。

2. **UAV $E_l$（不解密）**：
   - 聚合：$\mathsf{ct}_l^t = \sum_i \mathsf{ct}_{l,i}^t$（单密钥 CKKS 加法同态）。
   - 应用预测器压缩：$\delta_l^t = \mathrm{ResFed.Encode}(\mathsf{ct}_l^t - \mathsf{predict}_l^t)$。
   - $E_l \to C$: $\delta_l^t$（仅 $L$ 份）。

3. **云 $C$**（MKHE 跨 silo）：
   - 收到 $\{\delta_l^t\}_{l=1}^L$，**用 CDKS 风格 MKHE 把多 silo 密文绑成一束**：
     $$\Theta^t = \mathrm{CDKS.Eval}(+, \{\delta_l^t\}) = \Bigl(\sum_l c_{l,0},\; \sum_l c_{l,1}\Bigr)$$
   - 云发 partial decrypt 请求到各 silo 在线客户端；silo $l$ 在线客户端用 $s_{l,i}$ 贡献 partial $\pi_{l,i}^t = \delta_l^t.d_1 \cdot s_{l,i} + e_{l,i}'$。
   - **每 silo 内部**通过 LSS 恢复出"完整 silo partial" $\Pi_l^t = \delta_l^t.d_1 \cdot \mathsf{sk}_l + E_l$。
   - 云汇总：$P^t = \sum_l \Pi_l^t$。
   - 全局解密：
     $$M^t = \sum_l c_{l,0} + P^t \pmod q$$

4. **下传（残差逆）**：
   - $C$ 用 $h_{\mathrm{predict}}$ 把 $M^t$ 解出 $\bar w^t$，下传残差 $\delta^t = \bar w^t - \hat w^{t-1}$；客户端恢复。

**闭环公式**：

$$
\boxed{
\begin{aligned}
\mathsf{ct}_{l,i}^t &= (a_l s_{l,i} + e_{l,i} + \Delta \hat w_{l,i}^t,\; a_l) \\
\mathsf{ct}_l^t &= \sum_i \mathsf{ct}_{l,i}^t \approx \mathrm{Enc}_{\mathsf{pk}_l}\bigl(\Delta\textstyle\sum_i \hat w_{l,i}^t\bigr) \\
\Theta^t &= \sum_l \mathsf{ct}_l^t \quad \text{(CDKS multi-key add)} \\
\Pi_l^t &= \delta_l^t.d_1 \cdot \mathsf{sk}_l + E_l \quad \text{(silo-internal LSS)} \\
M^t &= \sum_l c_{l,0} + \sum_l \Pi_l^t \equiv \Delta \sum_{l,i} \hat w_{l,i}^t \pmod q \\
\bar w^t &= \mathrm{CKKS.Decode}(M^t)\cdot \Delta^{-1} \;\approx\; \sum_{l,i} w_{l,i}^t
\end{aligned}
}
$$

**与现有方案的差异**：
- 比 **P11 PPBR** 强：UAV-云链路负载与 silo 数 $L$ 成正比（与客户端数 $N$ **无关**），UAV 不解密。
- 比 **P6 FedPHE** 强：跨 silo 协同（FedPHE 限于单 KDC、单密钥、单服务器）。
- 比 **P3 ResFed** 强：把残差压缩和密文加性同态**联合**优化，不是两层独立。

---

## 6. 五个方案总览对照

| 维度 | A xLSS-CKKS | B CARM-FL | C CFLT | D VEGAS | E DRB-HFL |
|---|---|---|---|---|---|
| 架构 | 标准 FL | 标准 FL | HFL（UAV×2） | HFL（UAV×1） | HFL（多 silo） |
| 加密范式 | 多密钥 CKKS + LSS | 联合 DKG + 加性 RLWE | 单密钥 PLHE + Shamir 边层 | 单密钥 PLHE + RLWE 承诺 | 单密钥 per-silo + MKHE 跨 silo |
| 信任锚 | 无 TTP/TEE | 无 TTP/TEE | 离线 TA | 无 TTP/TEE | 离线 TA / silo 内部 |
| 抗服务器作恶 | **是**（承诺+spot-check） | 否 | 否 | **是** | 否 |
| 抗 UAV/边作恶 | — | — | **是**（双 UAV 阈值） | **半**（抽查+承诺） | **半**（UAV 不解密） |
| 抗客户端掉线 | $t$-of-$N$ | $t$-of-$N$ | PPBR 兼容 | 兼容 | $t_l$-of-$N_l$ |
| 通信代价（UAV-云） | 高（多密文） | 低 | 中（PLHE 单密文/簇） | 中 | **低**（per-silo 单密文） |
| 与本目录 P11 PPBR 对比 | — | — | 强化边层密钥分片 | 强化可验证 | 强化带宽与跨 silo |

---

## 7. 自评与开放问题

1. **A 方案的 RLWE 承诺**仍需要一个公共的"承诺密钥" $\mathsf{ck}$；若 $\mathsf{ck}$ 由客户端联合生成（Fiat-Shamir 启发式），则与 B 方案的 DKG 思路可以统一为一个"两阶段联合 DKG"，是后续工程化方向。
2. **C 方案的 2-of-2 Shamir** 是"两 UAV 必须同时在线"；当 UAV 因天气/电量掉线，本 silo 失败；可拓展为 $t$-of-$L$ 让多 UAV 协同，本报告因篇幅不再展开。
3. **D 方案的可验证性**主要在"承诺层"，对"密文本身是否对应承诺的明文"还需要加一个 RLWE 零知识证明；可用 Baum17 / LYU17 风格承诺，复杂度可接受。
4. **E 方案的 per-silo 密钥**由 silo 内客户端联合持有，需要周期性的 silo 内 DKG 刷新；这与 B 方案的 DKG 是同源技术。
5. **横评**：本目录 11 篇中**没有一篇同时**做到"标准 FL 抗 server 作恶 + HFL 抗 UAV 作恶 + 多 silo 跨域带宽友好"；五个方案分别针对其中一个或两个目标，组合起来可形成"无人机联邦学习"完整工具箱。

---

*报告完。所有公式基于 RLWE 假设，与本目录 P2 / P5 / P7 / P9 一致。*
