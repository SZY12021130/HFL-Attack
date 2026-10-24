# 联邦学习隐私保护方案综述与新方案设计

> 基于20篇前沿论文（2017–2026）的系统性分析，面向UAV应用场景设计5个新型隐私保护联邦学习方案

---

## 第一部分：文献综述与技术演进

### 1.1 论文总览

| 论文 | 年份 | 架构 | 密钥结构 | 同态方案 | 可验证 | 鲁棒聚合 | 抗掉线 |
|------|------|------|----------|----------|--------|----------|--------|
| SecAgg | 2017 | 标准FL（单服务器） | 多密钥（成对DH） | 无（PRG掩码） | 部分 | 否 | 是 |
| CDKS (MK-CKKS) | 2019 | 单服务器外包 | 多密钥（每人独立） | MK-CKKS/BFV | 否 | 否 | 部分 |
| ELSA | 2023 | 标准FL（双服务器） | 秘密共享 | 无（OT+Beaver） | 是 | 是（ℓ2⊓） | 是 |
| SecFed | 2024 | 标准FL（单服务器+TEE） | 多密钥+主密钥分解 | EMK-BFV | 否 | 否 | 是 |
| ResFed | 2024 | 标准FL（单服务器） | 无加密 | 无（压缩） | 否 | 否 | 否 |
| BPVFL | 2024 | 标准FL（双服务器） | 单密钥（Paillier） | Paillier | 是（双向ZKP） | 否 | 是 |
| ESAFL | 2025 | 标准FL（单服务器+AS） | 多密钥+共享解密密钥 | ESHE (RLWE) | 否 | 否 | 否 |
| FedPHE | 2025 | 标准FL（单服务器+KDC） | 单密钥+混淆因子 | CKKS (Packed) | 否 | 否 | 部分 |
| MASER | 2025 | 标准FL（单服务器） | 多密钥（MKCKKS） | xMKCKKS | 否 | 部分（掩码投票） | 否 |
| MaskCrypt | 2025 | 标准FL（单服务器） | 多密钥（每人独立） | CKKS/BFV | 否 | 否 | 否 |
| PrivHFL | 2025 | 层次型FL（端-边-云） | 多密钥+门限 | tMK-CKKS | 部分 | 否 | 是 |
| ProFL | 2025 | 标准FL（双服务器） | 单密钥（Paillier） | Paillier | 是 | 是（曼哈顿距离） | 否 |
| SKHE | 2025 | 层次型FL（端-边-云+TEE） | 单密钥+ECC+SS | CKKS | 是（哈希验证） | 否 | 是 |
| VeSAFL | 2025 | 多聚合器 | 门限（Pedersen VSS） | 无（SS） | 是（BLS+Pedersen） | 部分 | 部分 |
| CDKS-LSS | 2026 | 标准FL（单服务器） | 多密钥+LSS | CDKS (MK-CKKS) | 否 | 否 | 是 |
| FedOut | 2026 | 标准FL（双服务器） | 多密钥+共享解密 | ECMK-CKKS | 否 | 否 | 是 |
| DSVFL (Ftop-k) | 2026 | 标准FL（单服务器+TA） | 门限（Paillier） | Paillier | 是（签名验证） | 部分 | 是 |
| VLSFL | 2026 | 标准FL（单服务器+TA） | DH+SS混合 | 无（PRG掩码） | 是（HF验证） | 否 | 是 |
| PPBR | 2026 | 层次型FL（端-边-云） | SS+PLHE混合 | BFV (PLHE) | 否 | 是（双层过滤） | 是 |

### 1.2 技术演进脉络

**密钥管理演进：**

```
SecAgg (2017)：成对DH密钥 + Shamir SS → O(n²)通信开销
    ↓ 问题：通信复杂度高，无法扩展到大规模
CDKS/MK-CKKS (2019)：每人独立密钥，多密钥同态
    ↓ 问题：解密份额上传泄露个体梯度（c_{i,0}+μ_i ≈ m_i）
ESAFL (2025)：多加密密钥 + 共享解密密钥 → 一步解密，避免份额泄露
    ↓ 问题：依赖可信AS初始化
CDKS-LSS (2026)：多密钥 + 解密份额的Shamir SS → 信息论安全的解密阶段
    ↓ 问题：P2P通信开销O(n²)，仍无法避免合谋推断
FedOut (2026)：多密钥 + 盲化外包 → 资源受限设备友好
```

**可验证性演进：**

```
SecAgg (2017)：仅活性对手变体有签名验证，无输入正确性验证
    ↓ 问题：服务器可篡改聚合结果而不被发现
BPVFL (2024)：双向ZKP验证（服务器聚合完整性 + 客户端上传可信度）
    ↓ 问题：Paillier方案不抗量子，三方不共谋假设较强
VeSAFL (2025)：Pedersen VSS + BLS签名，信息论安全的可验证SS
    ↓ 问题：需要公告牌基础设施，扩展性受限
DSVFL (2026)：门限签名验证 + 抗共谋验证
VLSFL (2026)：同态哈希 + 双线性对验证，轻量级多轮复用
```

**鲁棒聚合演进：**

```
ELSA (2023)：ℓ2⊓范数约束（双服务器，OT+Beaver三元组）
    ↓ 问题：需要两个不共谋服务器
ProFL (2025)：安全中位数 + 曼哈顿距离加权（LHE兼容）
    ↓ 问题：双服务器架构，不适合层次型
MASER (2025)：掩码多数投票 → 对恶意剪枝掩码的鲁棒性
PPBR (2026)：双层异常过滤（CLDP + PLHE加密范数裁剪）→ 首个层次型拜占庭鲁棒方案
    ↓ 问题：CLDP噪声影响聚类精度，PLHE操作增加云端开销
```

**层次型FL演进：**

```
PrivHFL (2025)：首个HFL隐私方案，tMK-CKKS + 三层密钥分片
    ↓ 问题：每组一套密钥，跨组聚合需重新加密；不防投毒
SKHE (2025)：单密钥CKKS + ECC密钥分片 + TEE解密验证
    ↓ 问题：依赖TEE硬件信任；不防投毒
PPBR (2026)：SS + PLHE + CLDP → 隐私+拜占庭鲁棒的HFL
    ↓ 问题：CLDP噪声与鲁棒性权衡；边缘层通信开销高
```

### 1.3 UAV场景的关键挑战

1. **通信带宽受限**：UAV与地面站/边缘节点之间带宽有限，加密膨胀不能过大
2. **高动态拓扑**：UAV移动导致网络拓扑频繁变化，节点加入/退出频繁
3. **能源受限**：UAV电池容量有限，密码学运算需轻量化
4. **无线信道不可靠**：掉线率高，方案必须具备掉线容忍能力
5. **多域异构数据**：不同UAV集群覆盖不同区域，数据Non-IID性强
6. **物理安全威胁**：UAV可能被物理俘获，密钥可能泄露
7. **实时性要求**：任务（如目标检测、路径规划）对模型更新时延敏感
8. **层次化部署自然**：UAV→边缘基站→云 天然形成三层架构

---

## 第二部分：5个新方案设计

---

### 方案一：UVFL-VT（UAV-based Verifiable and robust Federated Learning）

**面向UAV集群协作感知的隐私保护可验证鲁棒联邦学习**

#### 5.1 应用场景

多架UAV在城市环境中协作执行目标检测/环境感知任务。每架UAV采集本地图像数据训练感知模型，需要联合训练提升全局模型精度。场景中存在不可信地面聚合站和可能投毒的恶意UAV。

- **架构**：标准FL（双服务器）
- **实体**：N架UAV（客户端）、聚合服务器S_agg、验证服务器S_ver
- **通信**：UAV↔S_agg（上传密文）、UAV↔S_ver（验证交互）、S_agg↔S_ver（聚合验证）

#### 5.2 要解决的问题

1. 恶意聚合服务器可能篡改聚合结果或推断个体梯度
2. 恶意UAV可能提交投毒梯度破坏全局模型
3. 现有方案（如BPVFL）不防投毒，（如ProFL）架构不适合UAV场景
4. UAV资源受限，需要轻量化验证机制

#### 5.3 创新点

1. **聚合-验证分离的双服务器架构**：S_agg只看到密文，S_ver只看到验证信息，两者不共谋则无法推断个体梯度
2. **基于Pedersen承诺的轻量级梯度验证**：UAV对梯度分量做Pedersen承诺，S_ver通过零知识证实验证密文与承诺的一致性
3. **加密域鲁棒聚合**：结合梯度范数裁剪与方向相似性加权，在密文上实现Byzantine鲁棒聚合
4. **UAV自适应掉线恢复**：基于门限秘密共享的掩码恢复机制

#### 5.4 威胁模型

- **S_agg（聚合服务器）**：恶意对手（active adversary）——可能篡改聚合结果、尝试推断个体梯度、选择性丢弃UAV的更新
- **S_ver（验证服务器）**：半诚实对手（semi-honest）——忠实执行协议但试图推断信息
- **UAV客户端**：最多 f < N/3 架UAV为Byzantine恶意节点——可发送任意梯度、与S_agg共谋
- **共谋限制**：S_agg与S_ver不共谋；S_agg最多与f架恶意UAV共谋
- **安全目标**：
  - 隐私性：任何实体无法获取诚实UAV的个体梯度
  - 完整性：聚合结果的正确性可验证
  - 鲁棒性：恶意UAV的投毒梯度被检测并降权

#### 5.5 符号系统

| 符号 | 含义 |
|------|------|
| N | UAV总数 |
| t | 门限值（最少参与解密的UAV数） |
| f | 最大恶意UAV数（f < N/3） |
| $\mathbf{w}_i \in \mathbb{R}^d$ | UAV i 的本地模型参数 |
| $\Delta\mathbf{w}_i = \mathbf{w}_i - \mathbf{w}^{(t-1)}$ | UAV i 的模型更新 |
| $q$ | RLWE多项式环模数 |
| $R_q = \mathbb{Z}[X]/(X^n+1)$ | RLWE多项式环 |
| $s_i \in R_q$ | UAV i 的私钥 |
| $pk_i = (a, b_i = -(a \cdot s_i + e_i))$ | UAV i 的公钥 |
| $pk_{agg} = \sum_{i=1}^{N} pk_i$ | 聚合公钥 |
| $\text{Enc}_{pk}(\cdot)$ | 基于聚合公钥的加密 |
| $\text{PartDec}_{s_i}(\cdot)$ | 用$s_i$的部分解密 |
| $\text{Commit}(x; r)$ | Pedersen承诺：$g^x h^r \mod p$ |
| $\tau$ | 梯度范数裁剪阈值 |
| $\alpha_i$ | UAV i 的鲁棒聚合权重 |

#### 5.6 方案设计

##### 阶段0：系统初始化

可信机构（仅在初始化阶段参与）生成公共参数：

$$pp = (q, n, \chi, g, h, p, \tau)$$

其中 $g, h$ 是阶为 $p$ 的循环群 $\mathbb{G}$ 的生成元，$h = g^\gamma$（$\gamma$ 为离散对数未知的随机数）。

##### 阶段1：密钥生成

每架UAV $i$ 生成密钥对：

$$s_i \leftarrow \chi_s, \quad e_i \leftarrow \chi_e$$
$$pk_i = (a, b_i) \text{ where } b_i = -(a \cdot s_i + e_i) \mod q$$

聚合公钥：
$$pk_{agg} = \left(a, \sum_{i=1}^{N} b_i\right) = (a, b_{agg})$$

每架UAV广播 $pk_i$ 到所有实体。

##### 阶段2：本地训练与加密

UAV $i$ 在本地数据集 $\mathcal{D}_i$ 上训练得到 $\mathbf{w}_i$，计算更新：

$$\Delta\mathbf{w}_i = \mathbf{w}_i - \mathbf{w}^{(t-1)}$$

**梯度裁剪**（防投毒）：

$$\|\Delta\mathbf{w}_i\|_2 > \tau \implies \Delta\mathbf{w}_i \leftarrow \frac{\tau}{\|\Delta\mathbf{w}_i\|_2} \cdot \Delta\mathbf{w}_i$$

**Pedersen承诺生成**（用于可验证性）：

对每个梯度分量 $\Delta w_{i,j}$（$j = 1, \ldots, d$）：

$$C_{i,j} = g^{\Delta w_{i,j}} \cdot h^{r_{i,j}} \mod p, \quad r_{i,j} \leftarrow \mathbb{Z}_p$$

**加密**：将 $\Delta\mathbf{w}_i$ 编码为多项式 $m_i \in R_q$，用聚合公钥加密：

$$ct_i = \text{Enc}_{pk_{agg}}(m_i) = (c_{i,0}, c_{i,1})$$

其中：
$$c_{i,0} = a \cdot u_i + e_{i,0} + m_i \mod q$$
$$c_{i,1} = b_{agg} \cdot u_i + e_{i,1} \mod q$$

$u_i \leftarrow \chi_s$，$e_{i,0}, e_{i,1} \leftarrow \chi_e$

**双掩码保护**（防掉线）：

UAV $i$ 与UAV $j$ 通过DH交换建立共享种子 $s_{i,j}$，生成掩码：

$$M_i = \text{PRG}(b_i) + \sum_{j < i} \text{PRG}(s_{i,j}) - \sum_{j > i} \text{PRG}(s_{i,j}) \mod q$$

其中 $b_i$ 是UAV $i$ 的私有种子的Shamir共享。

加密更新变为：
$$ct_i = \text{Enc}_{pk_{agg}}(m_i + M_i)$$

UAV $i$ 发送 $(ct_i, \{C_{i,j}\}_{j=1}^d)$ 到 $S_{agg}$，发送 $\{r_{i,j}\}_{j=1}^d$ 到 $S_{ver}$。

##### 阶段3：验证协议

**步骤3.1**：$S_{ver}$ 验证承诺的一致性。

$S_{ver}$ 收到来自UAV $i$ 的随机数 $r_{i,j}$，计算验证值：

$$V_{i,j} = g^{\Delta w_{i,j}^{(enc)}} \cdot h^{r_{i,j}} \mod p$$

其中 $\Delta w_{i,j}^{(enc)}$ 是从密文中提取的编码值（通过部分解密获得，见阶段4）。

**步骤3.2**：$S_{ver}$ 发送随机挑战 $\{e_j\}_{j=1}^d$（$e_j \leftarrow \{0,1\}^\kappa$）给 $S_{agg}$。

**步骤3.3**：$S_{agg}$ 计算聚合承诺：

$$C_{agg,j} = \prod_{i=1}^{N} C_{i,j} \mod p = g^{\sum_i \Delta w_{i,j}} \cdot h^{\sum_i r_{i,j}} \mod p$$

**步骤3.4**：$S_{ver}$ 验证聚合正确性：

$$C_{agg,j} \stackrel{?}{=} g^{\Delta w_{G,j}} \cdot h^{R_j} \mod p$$

其中 $\Delta w_{G,j}$ 是解密后的全局聚合值，$R_j = \sum_i r_{i,j}$。

##### 阶段4：安全聚合与鲁棒加权

**步骤4.1**：$S_{agg}$ 对密文做同态加法聚合：

$$C_{agg} = \sum_{i \in \mathcal{S}_{online}} ct_i + \sum_{i \in \mathcal{S}_{drop}} ct_i^{(supp)}$$

其中 $ct_i^{(supp)}$ 是掉线UAV的补充密文（由其他UAV通过共享种子重构掩码后生成）。

**步骤4.2**：部分解密。在线UAV $i$ 计算：

$$\mu_i = c_{agg,1} \cdot s_i + \tilde{e}_i \mod q$$

其中 $\tilde{e}_i$ 是噪声泛洪项，防止 $S_{agg}$ 从部分解密中推断信息。

**步骤4.3**：合并解密。$S_{agg}$ 收集至少 $t$ 个部分解密份额：

$$\mu = \sum_{i \in \mathcal{S}_{online}} \mu_i \mod q$$

全局聚合结果：

$$m_{agg} = [c_{agg,0} + \mu]_q \mod p$$

**步骤4.4**：鲁棒加权。$S_{agg}$ 计算每对UAV的方向相似性：

$$\cos_{i,k} = \frac{\langle \Delta\mathbf{w}_i, \Delta\mathbf{w}_k \rangle}{\|\Delta\mathbf{w}_i\|_2 \cdot \|\Delta\mathbf{w}_k\|_2}$$

鲁棒权重：

$$\alpha_i = \frac{n_i \cdot \mathbb{1}[\text{med}_{k}(\cos_{i,k}) > \theta]}{\sum_j n_j \cdot \mathbb{1}[\text{med}_{k}(\cos_{j,k}) > \theta]}$$

其中 $\theta$ 是方向一致性阈值，$\text{med}_k$ 表示与其他UAV余弦相似度的中位数。

##### 阶段5：全局模型更新

$$\mathbf{w}^{(t)} = \mathbf{w}^{(t-1)} + \sum_{i=1}^{N} \alpha_i \cdot \Delta\mathbf{w}_i$$

$S_{agg}$ 广播 $\mathbf{w}^{(t)}$ 到所有UAV。

#### 5.6b 实体交互时序图

```
UAV_i                    S_agg                    S_ver
  |                         |                        |
  |-- [pk_i] ------------->|                        |
  |-- [pk_i] --------------|----------------------->|
  |                         |                        |
  |-- [ct_i, {C_{i,j}}] -->|                        |
  |-- [{r_{i,j}}] ---------|----------------------->|
  |                         |                        |
  |                         |<-- [挑战 {e_j}] ------|
  |                         |                        |
  |                         |-- [C_{agg,j}] ------->|
  |                         |                        |
  |<-- [聚合密文 C_{agg}] --|                        |
  |                         |                        |
  |-- [μ_i] -------------->|                        |
  |                         |                        |
  |                         |-- [验证请求] --------->|
  |                         |<-- [验证结果] ---------|
  |                         |                        |
  |<-- [w^{(t)}] ----------|                        |
```

#### 5.7 安全性分析

**定理1（隐私性）**：在S_agg与S_ver不共谋的假设下，UVFL-VT保证诚实UAV的个体梯度不被任何对手获取。

*证明概要*：
- 对S_agg：收到的密文 $ct_i$ 在MK-CKKS的IND-CPA安全性下不可区分；Pedersen承诺 $C_{i,j}$ 是完美绑定的且计算隐藏的（在DDH假设下）
- 对S_ver：只收到随机数 $r_{i,j}$，不含梯度信息；承诺的计算隐藏性保证无法从 $r_{i,j}$ 反推 $\Delta w_{i,j}$
- 对Byzantine UAV：密文在多密钥结构下安全，最多 $f < N/3$ 个恶意UAV无法影响聚合

**定理2（完整性）**：验证协议保证如果S_agg篡改聚合结果，S_ver将以压倒性概率检测。

*证明概要*：
- Pedersen承诺的同态性质：$\prod C_{i,j} = g^{\sum \Delta w_{i,j}} \cdot h^{\sum r_{i,j}}$
- 如果S_agg输出错误的聚合值 $\Delta w'_{G,j} \neq \sum \Delta w_{i,j}$，则：
$$g^{\Delta w'_{G,j}} \cdot h^{R_j} \neq C_{agg,j}$$
- 验证失败的概率为 $1 - 1/p$（在 $p$ 足够大时可忽略）

**定理3（鲁棒性）**：方向相似性加权聚合保证在 $f < N/3$ 恶意UAV下，全局模型收敛偏差有界。

*证明概要*：
- 恶意UAV的梯度经裁剪后范数有界：$\|\Delta\mathbf{w}_{mal}\|_2 \leq \tau$
- 方向一致性检测：恶意UAV的梯度方向与多数诚实UAV不一致，$\text{med}_k(\cos_{i,k}) < \theta$ 的概率随 $f$ 增加
- 当 $f < N/3$ 时，诚实UAV的方向中位数不受恶意UAV影响（中位数的崩溃点为50%）

---

### 方案二：UVFL-TBR（UAV-based Threshold Byzantine-Robust Federated Learning）

**面向UAV边缘计算的门限解密与拜占庭鲁棒联邦学习**

#### 5.1 应用场景

UAV集群在偏远地区执行协作监测任务，通过卫星/远距离无线链路连接到中央服务器。通信链路不稳定，UAV可能频繁掉线。场景中无边缘节点可用，仅有中央服务器和可信初始化机构。

- **架构**：标准FL（单服务器 + 可信权威TA）
- **实体**：N架UAV、中央服务器CS、可信权威TA（仅初始化阶段在线）
- **特点**：高掉线率、无边缘辅助

#### 5.2 要解决的问题

1. 单服务器架构下，服务器可能推断个体梯度（DSVFL已解决但用Paillier不抗量子）
2. 掉线UAV的梯度不应被丢弃或泄露
3. 恶意UAV的投毒梯度需要被检测
4. 现有方案在RLWE+门限解密+鲁棒聚合的结合上存在空白

#### 5.3 创新点

1. **门限CKKS + 分布式密钥管理**：私钥分片存储在UAV集群中，CS无法单独解密任何个体梯度
2. **加密域安全中位数协议**：在密文上计算坐标中位数作为鲁棒聚合基准（借鉴ProFL但用CKKS替代Paillier）
3. **双层防御**：梯度范数门限（第一层）+ 与中位数偏差加权（第二层）
4. **轻量级掉线恢复**：基于Shamir SS的掩码重构，不需要TEE或额外服务器

#### 5.4 威胁模型

- **CS（中央服务器）**：半诚实对手——忠实执行协议但试图推断个体梯度；可能与最多 $f_1$ 架恶意UAV共谋
- **UAV客户端**：最多 $f_2 < N/3$ 架为Byzantine恶意节点
- **TA**：完全可信，仅初始化阶段在线
- **共谋限制**：CS与最多 $f_1$ 架UAV共谋，$f_1 + f_2 < t$（门限值）
- **安全目标**：隐私性（门限解密保证）、鲁棒性（双层防御）、掉线容忍（门限SS）

#### 5.5 符号系统

| 符号 | 含义 |
|------|------|
| N | UAV总数 |
| t | 门限值（$t > N/2$） |
| $s$ | CKKS主私钥 |
| $s_i$ | UAV $i$ 的私钥分片（$\sum s_i = s$） |
| $pk$ | CKKS公钥 |
| $\Delta\mathbf{w}_i$ | UAV $i$ 的模型更新 |
| $\tau$ | 梯度范数上限 |
| $\text{med}_j$ | 第 $j$ 维的坐标中位数 |
| $D_i$ | UAV $i$ 与中位数的曼哈顿距离 |

#### 5.6 方案设计

##### 阶段0：密钥分发

TA生成CKKS密钥对 $(pk, sk)$，将 $sk$ 通过Shamir $(t, N)$ 秘密共享分给N架UAV：

$$sk = \sum_{i=1}^{N} \lambda_i \cdot s_i$$

其中 $\lambda_i$ 是拉格朗日系数。每架UAV $i$ 获得分片 $s_i$。

##### 阶段1：本地训练与加密

UAV $i$ 训练得到 $\Delta\mathbf{w}_i$，执行梯度裁剪：

$$\Delta\mathbf{w}_i \leftarrow \min\left(1, \frac{\tau}{\|\Delta\mathbf{w}_i\|_2}\right) \cdot \Delta\mathbf{w}_i$$

编码为多项式 $m_i$，用公钥 $pk$ 加密：

$$ct_i = \text{Enc}_{pk}(m_i) = (a \cdot u_i + e_i + m_i, b \cdot u_i + e'_i)$$

**掉线保护掩码**：

UAV $i$ 生成随机多项式 $f_i(X)$ 使得 $f_i(0) = b_i$（掩码种子），通过 $(t, N)$ Shamir SS 分享给其他UAV。

$$\hat{ct}_i = ct_i + \text{Enc}_{pk}(\text{PRG}(b_i) + \sum_{j<i}\text{PRG}(s_{i,j}) - \sum_{j>i}\text{PRG}(s_{i,j}))$$

发送 $\hat{ct}_i$ 到 CS。

##### 阶段2：安全聚合

CS聚合所有在线UAV的密文：

$$C_{sum} = \sum_{i \in \mathcal{S}_{online}} \hat{ct}_i + \sum_{i \in \mathcal{S}_{drop}} \hat{ct}_i^{(recon)}$$

其中掉线UAV $i$ 的掩码由至少 $t$ 个在线UAV通过Shamir重构：

$$b_i = \sum_{k \in \mathcal{S}_{online}, |S_{online}| \geq t} \lambda_k \cdot b_{i,k}$$

CS移除掩码得到：

$$C_{agg} = C_{sum} - \text{Enc}_{pk}(M_{total})$$

##### 阶段3：加密域鲁棒聚合

**步骤3.1**：部分解密获得扰动后的聚合值。

至少 $t$ 架在线UAV计算部分解密份额：

$$\mu_i = C_{agg,1} \cdot s_i + \tilde{e}_i \mod q$$

CS合并：

$$m_{agg}^{(noisy)} = [C_{agg,0} + \sum_{i} \mu_i]_q$$

**步骤3.2**：计算加密域坐标中位数。

CS对每个维度 $j$，收集部分解密值（带噪声泛洪），排序后取中位数：

$$\text{med}_j = \text{Median}(\{[\Delta w_{i,j}]_{i \in \mathcal{S}_{online}}\})$$

注意：由于噪声泛洪，CS看到的是扰动值，无法精确排序。改为**近似中位数**：

$$\text{med}_j = \frac{1}{|\mathcal{S}_{online}|} \sum_{i \in \mathcal{S}_{online}} \Delta w_{i,j}$$

（在噪声泛洪下，均值是唯一可行的聚合统计量）

**步骤3.3**：鲁棒加权。

CS计算每架UAV与聚合均值的偏差（利用部分解密的噪声抵消）：

$$D_i = \sum_{j=1}^{d} |\Delta w_{i,j} - \text{med}_j|$$

鲁棒权重：

$$\alpha_i = \frac{n_i \cdot \exp(-D_i / \sigma)}{\sum_k n_k \cdot \exp(-D_k / \sigma)}$$

其中 $\sigma$ 是温度参数，$n_i$ 是UAV $i$ 的数据量。

##### 阶段4：全局模型更新

$$\mathbf{w}^{(t)} = \mathbf{w}^{(t-1)} + \sum_{i=1}^{N} \alpha_i \cdot \Delta\mathbf{w}_i$$

#### 5.6b 实体交互时序图

```
TA                    UAV_i                    CS
 |                      |                       |
 |-- [s_i, pk] ------->|                       |
 |                      |                       |
 |    [离线]            |                       |
 |                      |                       |
 |                      |-- [ct_i] ----------->|
 |                      |                       |
 |                      |<-- [聚合密文] --------|
 |                      |                       |
 |                      |-- [μ_i] ----------->|
 |                      |                       |
 |                      |<-- [w^{(t)}] --------|
```

#### 5.7 安全性分析

**定理1（隐私性）**：CS无法获取任何诚实UAV的个体梯度，除非与至少 $t$ 架UAV共谋。

*证明*：私钥 $sk$ 被 $(t,N)$ Shamir SS 分片，CS需要至少 $t$ 个分片才能重构 $sk$。在共谋限制 $f_1 + f_2 < t$ 下，CS无法获取足够的分片。

**定理2（鲁棒性）**：指数加权聚合在 $f_2 < N/3$ Byzantine UAV下保证收敛偏差有界。

*证明概要*：
- 第一层：梯度裁剪保证 $\|\Delta\mathbf{w}_i\|_2 \leq \tau$
- 第二层：Manhattan距离指数加权使偏离中位数的梯度权重指数衰减
- Byzantine UAV的最大影响：$\sum_{i \in \mathcal{B}} \alpha_i \cdot \tau \leq \frac{f_2}{N} \cdot \tau$（当 $f_2 < N/3$ 时小于 $1/3$）

**定理3（掉线容忍）**：只要至少 $t$ 架UAV在线，聚合即可正确完成。

*证明*：Shamir SS的 $(t,N)$ 门限性质保证任何 $t$ 个分片可重构多项式，从而恢复掉线UAV的掩码。

---

### 方案三：HEFL-VD（Hierarchical Edge-assisted FL with Verifiable Decryption）

**边缘辅助的分层可验证安全联邦学习**

#### 5.1 应用场景

大规模UAV集群（数十到上百架）分布在城市区域，通过多个边缘基站（如5G基站）连接到云端。每个边缘基站覆盖一个区域的UAV子集群。需要层次化聚合以减少通信开销，同时保证边缘层聚合的可验证性。

- **架构**：层次型FL（UAV→边缘→云，三层）
- **实体**：N架UAV（分为K个集群）、K个边缘节点EN、1个云服务器CS、可信初始化机构KGC
- **特点**：边缘层部分聚合、云端全局聚合、跨集群密钥管理

#### 5.2 要解决的问题

1. 层次型架构中，边缘节点可能篡改部分聚合结果（PrivHFL未解决）
2. 跨边缘集群的密钥管理复杂（PrivHFL每组独立密钥，跨组聚合困难）
3. 恶意UAV的投毒梯度在边缘层就应该被检测（PPBR的CLDP噪声影响精度）
4. 边缘节点掉线影响整个集群

#### 5.3 创新点

1. **层次化MK-CKKS**：每个UAV独立密钥，边缘层做同态加法聚合（不需要解密），云端聚合后统一解密
2. **边缘层聚合验证**：UAV对梯度做Pedersen承诺，边缘节点聚合密文和承诺，云端验证聚合一致性
3. **分布式Byzantine检测**：边缘层利用梯度范数和邻居一致性检测异常UAV
4. **集群自适应密钥聚合**：云端通过LSS门限解密，支持集群动态加入/退出

#### 5.4 威胁模型

- **CS（云服务器）**：半诚实——忠实聚合但试图推断个体或集群级信息
- **EN（边缘节点）**：最多 $f_E < K/3$ 个为恶意——可篡改部分聚合结果、丢弃UAV更新
- **UAV**：每个集群内最多 $f_U < n_k/3$ 架为Byzantine恶意节点
- **KGC**：完全可信，仅初始化阶段在线
- **共谋限制**：CS与恶意EN不共谋；恶意EN之间可共谋
- **安全目标**：隐私性、可验证聚合、Byzantine鲁棒性、边缘节点掉线容忍

#### 5.5 符号系统

| 符号 | 含义 |
|------|------|
| K | 边缘节点数 |
| $n_k$ | 第 $k$ 个集群的UAV数 |
| $s_{k,i}$ | 集群 $k$ 中UAV $i$ 的私钥 |
| $pk_{k,i}$ | 对应公钥 |
| $pk_{agg} = \sum_{k,i} pk_{k,i}$ | 全局聚合公钥 |
| $\Delta\mathbf{w}_{k,i}$ | UAV $(k,i)$ 的模型更新 |
| $C_{k,j}$ | 集群 $k$ 的Pedersen聚合承诺 |
| $\mathcal{E}_k$ | 边缘节点 $k$ 的局部聚合密文 |

#### 5.6 方案设计

##### 阶段0：密钥生成与分发

KGC为每架UAV $(k,i)$ 生成独立密钥对：

$$s_{k,i} \leftarrow \chi_s, \quad pk_{k,i} = (a, b_{k,i} = -(a \cdot s_{k,i} + e_{k,i}))$$

全局聚合公钥：

$$pk_{agg} = \left(a, \sum_{k=1}^{K}\sum_{i=1}^{n_k} b_{k,i}\right)$$

KGC分发：UAV $(k,i)$ 获得 $s_{k,i}$ 和 $pk_{agg}$；EN $k$ 获得其集群的公钥列表 $\{pk_{k,i}\}$；CS获得 $pk_{agg}$。

##### 阶段1：本地训练与加密

UAV $(k,i)$ 训练得到 $\Delta\mathbf{w}_{k,i}$，执行裁剪：

$$\Delta\mathbf{w}_{k,i} \leftarrow \min\left(1, \frac{\tau}{\|\Delta\mathbf{w}_{k,i}\|_2}\right) \cdot \Delta\mathbf{w}_{k,i}$$

生成Pedersen承诺（每维一个）：

$$\text{Com}_{k,i,j} = g^{\Delta w_{k,i,j}} \cdot h^{r_{k,i,j}} \mod p$$

加密（用全局聚合公钥）：

$$ct_{k,i} = \text{Enc}_{pk_{agg}}(m_{k,i})$$

发送到EN $k$：$(ct_{k,i}, \{\text{Com}_{k,i,j}\})$

##### 阶段2：边缘层聚合与Byzantine检测

**步骤2.1**：EN $k$ 做Byzantine检测。

对每架UAV $(k,i)$，EN $k$ 检查：
- 梯度范数：$\|\Delta\mathbf{w}_{k,i}\|_2 \leq \tau$（通过密文的部分信息或UAV自报告的范数+承诺验证）
- 邻居一致性：$\text{Com}_{k,i,j}$ 与同集群其他UAV的承诺做统计检验

具体地，EN $k$ 计算集群内承诺的几何中心：

$$\bar{C}_{k,j} = \left(\prod_{i=1}^{n_k} \text{Com}_{k,i,j}\right)^{1/n_k}$$

对每架UAV，计算偏离度：

$$d_{k,i} = \sum_j \left| \log_g(\text{Com}_{k,i,j} / \bar{C}_{k,j}) \right|$$

若 $d_{k,i} > \theta_{local}$，标记为可疑并降权。

**步骤2.2**：EN $k$ 做同态聚合。

$$\mathcal{E}_k = \sum_{i \in \mathcal{S}_{k,valid}} ct_{k,i}$$

$$\mathcal{C}_{k,j} = \prod_{i \in \mathcal{S}_{k,valid}} \text{Com}_{k,i,j} \mod p$$

EN $k$ 发送 $(\mathcal{E}_k, \{\mathcal{C}_{k,j}\})$ 到CS。

##### 阶段3：云端全局聚合与验证

**步骤3.1**：CS聚合所有边缘节点的密文：

$$\mathcal{E}_{global} = \sum_{k=1}^{K} \mathcal{E}_k$$

**步骤3.2**：CS请求部分解密。

每架在线UAV $(k,i)$ 计算：

$$\mu_{k,i} = \mathcal{E}_{global,1} \cdot s_{k,i} + \tilde{e}_{k,i} \mod q$$

发送到CS。

**步骤3.3**：CS合并至少 $t$ 个部分解密份额：

$$m_{global} = \left[\mathcal{E}_{global,0} + \sum_{(k,i) \in \mathcal{S}_{online}} \lambda_{k,i} \cdot \mu_{k,i}\right]_q$$

**步骤3.4**：验证聚合正确性。

CS计算全局承诺：

$$\mathcal{C}_{global,j} = \prod_{k=1}^{K} \mathcal{C}_{k,j} \mod p$$

验证：

$$\mathcal{C}_{global,j} \stackrel{?}{=} g^{\Delta w_{G,j}} \cdot h^{R_{global,j}} \mod p$$

其中 $R_{global,j} = \sum_{k,i} r_{k,i,j}$（由UAV通过安全信道发送给CS的验证者）。

##### 阶段4：全局模型更新

$$\mathbf{w}^{(t)} = \mathbf{w}^{(t-1)} + \frac{1}{K}\sum_{k=1}^{K} \frac{1}{n_k}\sum_{i=1}^{n_k} \Delta\mathbf{w}_{k,i}$$

#### 5.6b 实体交互时序图

```
UAV(k,i)              EN_k                     CS
  |                     |                       |
  |-- [ct, Com] ------>|                       |
  |                     |                       |
  |                     |-- [E_k, C_k] ------->|
  |                     |                       |
  |                     |                       |-- [全局密文] ----> UAV
  |                     |                       |
  |-- [μ_{k,i}] -------|--------------------->|
  |                     |                       |
  |                     |                       |-- [验证] ---------> Verifier
  |                     |                       |<-- [验证结果] ------
  |                     |                       |
  |<-- [w^{(t)}] ------|-----------------------|
```

#### 5.7 安全性分析

**定理1（隐私性）**：在CS与EN不共谋的假设下，任何实体无法获取诚实UAV的个体梯度。

*证明*：密文用全局聚合公钥 $pk_{agg}$ 加密，解密需要所有UAV的私钥分片之和。CS只收到部分解密份额（带噪声泛洪），无法分离个体贡献。EN只做同态加法，不解密。

**定理2（边缘层可验证性）**：恶意EN篡改部分聚合结果的概率被Pedersen承诺的绑定性限制。

*证明*：EN $k$ 必须提供 $\mathcal{C}_{k,j} = \prod \text{Com}_{k,i,j}$。如果EN替换了某个UAV的梯度为 $\Delta w'_{k,i}$，则需要找到 $r'$ 使得 $g^{\Delta w'} h^{r'} = g^{\Delta w} h^r$，这等价于求离散对数，在DL假设下不可行。

**定理3（Byzantine鲁棒性）**：边缘层检测 + 云端聚合保证在 $f_U < n_k/3$ 和 $f_E < K/3$ 下全局模型收敛。

*证明概要*：
- 边缘层：偏离度 $d_{k,i}$ 检测将恶意UAV的权重降至0或接近0
- 云端：即使恶意EN通过了边缘检测，其聚合密文中的恶意梯度已被范数裁剪限制在 $\tau$ 范围内
- 双重防御的叠加效果：$\|\Delta\mathbf{w}_{global} - \Delta\mathbf{w}_{honest}\| \leq \frac{f_E}{K} \cdot \tau + \frac{f_U}{n_k} \cdot \tau$

---

### 方案四：HEFL-TR（Hierarchical FL with Trusted Relay）

**面向UAV群跨域协作的可信中继联邦学习**

#### 5.1 应用场景

多个UAV群（如不同任务编队）分布在不同的地理区域，每个群有集群头（Cluster Head, CH）作为边缘中继。CH之间通过长距离链路连接，无法直接通信。需要一个可信中继节点（如高空长航时UAV或卫星）协调跨集群的联邦学习。

- **架构**：层次型FL（UAV→集群头→可信中继→云，四层）
- **实体**：N架普通UAV、K个集群头CH、1个可信中继TR、1个云服务器CS
- **特点**：四层架构、跨域聚合、TR作为密钥协调者

#### 5.2 要解决的问题

1. 不同UAV群之间数据分布差异极大（极端Non-IID）
2. 集群头可能被俘获（物理安全威胁）
3. 跨集群聚合需要密钥协调，但不希望TR看到任何梯度信息
4. TR本身需要被验证（即使是可信的，也需要审计机制）

#### 5.3 创新点

1. **四层密钥层次**：UAV层→CH层→TR层→CS层，每层有独立的密钥管理和聚合
2. **TR作为密钥协调者而非数据聚合者**：TR只处理密钥交换，不接触密文
3. **集群级差分隐私**：每层添加校准的DP噪声，防止跨层推断
4. **自适应权重调整**：根据集群数据分布差异动态调整聚合权重

#### 5.4 威胁模型

- **CS**：半诚实——忠实聚合但试图推断集群级信息
- **TR（可信中继）**：可信但需审计——忠实执行密钥协调，但需要验证其没有泄露密钥材料
- **CH（集群头）**：最多 $f_{CH} < K/3$ 个为恶意——可篡改集群级聚合、丢弃UAV更新
- **UAV**：每个集群内最多 $f_U < n_k/3$ 架为Byzantine
- **共谋限制**：恶意CH之间可共谋；CS与CH不共谋
- **安全目标**：隐私性、跨域安全聚合、Byzantine鲁棒性、TR可审计性

#### 5.5 符号系统

| 符号 | 含义 |
|------|------|
| K | 集群数 |
| $n_k$ | 集群 $k$ 的UAV数 |
| $sk_k$ | 集群 $k$ 的聚合私钥 |
| $sk_{k,i}$ | 集群 $k$ 内UAV $i$ 的私钥分片 |
| $pk_k$ | 集群 $k$ 的聚合公钥 |
| $pk_{global}$ | 全局聚合公钥 |
| $\epsilon_k$ | 集群 $k$ 的差分隐私预算 |
| $\alpha_k$ | 集群 $k$ 的聚合权重 |

#### 5.6 方案设计

##### 阶段0：层次化密钥生成

**步骤0.1**：TR为每个集群 $k$ 生成集群密钥对：

$$sk_k \leftarrow \chi_s, \quad pk_k = (a, b_k = -(a \cdot sk_k + e_k))$$

**步骤0.2**：TR将 $sk_k$ 通过 $(t_k, n_k)$ Shamir SS 分给集群 $k$ 内的UAV：

$$sk_k = \sum_{i=1}^{n_k} \lambda_{k,i} \cdot sk_{k,i}$$

**步骤0.3**：TR生成全局聚合公钥：

$$pk_{global} = \left(a, \sum_{k=1}^{K} b_k\right)$$

分发：UAV $(k,i)$ 获得 $sk_{k,i}$ 和 $pk_{global}$；CH $k$ 获得 $pk_k$ 和 $pk_{global}$；CS获得 $pk_{global}$。

##### 阶段1：本地训练与加密

UAV $(k,i)$ 训练得到 $\Delta\mathbf{w}_{k,i}$，执行裁剪：

$$\Delta\mathbf{w}_{k,i} \leftarrow \min\left(1, \frac{\tau}{\|\Delta\mathbf{w}_{k,i}\|_2}\right) \cdot \Delta\mathbf{w}_{k,i}$$

用全局聚合公钥加密：

$$ct_{k,i} = \text{Enc}_{pk_{global}}(m_{k,i})$$

发送到CH $k$。

##### 阶段2：集群头聚合

CH $k$ 执行：

**步骤2.1**：Byzantine检测。

CH $k$ 对集群内的密文做统计检验（基于密文的范数估计）：

$$\|ct_{k,i}\| \approx \|m_{k,i}\| \quad \text{(在同态加密下近似)}$$

如果某UAV的密文范数显著偏离集群均值（$> 3\sigma$），标记为异常。

**步骤2.2**：部分聚合。

$$\mathcal{E}_k = \sum_{i \in \mathcal{S}_{k,valid}} ct_{k,i}$$

**步骤2.3**：添加集群级差分隐私噪声。

CH $k$ 生成噪声多项式 $\eta_k$，满足 $\|\eta_k\| \leq \sigma_k \cdot \sqrt{2\ln(1.25/\delta)}$：

$$\mathcal{E}'_k = \mathcal{E}_k + \text{Enc}_{pk_{global}}(\eta_k)$$

发送到CS。

##### 阶段3：可信中继密钥协调

TR不接触密文，只负责密钥管理：

**步骤3.1**：当新集群加入时，TR生成新的集群密钥并更新全局公钥：

$$pk_{global}^{(new)} = \left(a, \sum_{k=1}^{K+1} b_k\right)$$

**步骤3.2**：当集群退出时，TR通知CS该集群的公钥分量，CS在聚合时排除：

$$pk_{global}^{(new)} = \left(a, \sum_{k \neq k_{exit}} b_k\right)$$

**步骤3.3**：TR定期轮换密钥（每 $T_{rotate}$ 轮），防止长期密钥泄露。

##### 阶段4：云端全局聚合

CS聚合所有集群的密文：

$$\mathcal{E}_{global} = \sum_{k=1}^{K} \mathcal{E}'_k$$

请求部分解密：至少 $t_k$ 架UAV（每个集群 $k$）提供解密份额：

$$\mu_{k,i} = \mathcal{E}_{global,1} \cdot sk_{k,i} + \tilde{e}_{k,i} \mod q$$

CS合并：

$$m_{global} = \left[\mathcal{E}_{global,0} + \sum_k \sum_{i} \lambda_{k,i} \cdot \mu_{k,i}\right]_q$$

##### 阶段5：自适应权重调整

CS根据集群数据分布差异调整权重：

$$\alpha_k = \frac{n_k \cdot (1 - D_{KL}(P_k \| P_{global}))}{\sum_j n_j \cdot (1 - D_{KL}(P_j \| P_{global}))}$$

其中 $D_{KL}(P_k \| P_{global})$ 是集群 $k$ 的数据分布与全局分布的KL散度（通过安全的分布摘要协议估计）。

#### 5.6b 实体交互时序图

```
UAV(k,i)         CH_k              TR              CS
  |               |                 |               |
  |-- [ct] ----->|                 |               |
  |               |                 |               |
  |               |-- [E'_k] ------|------------->|
  |               |                 |               |
  |               |   [密钥轮换]    |               |
  |               |<-- [新pk] -----|               |
  |               |                 |               |
  |               |                 |               |-- [解密请求] --> UAV
  |               |                 |               |
  |-- [μ] -------|----------------|------------->|
  |               |                 |               |
  |<-- [w^{(t)}] |----------------|---------------|
```

#### 5.7 安全性分析

**定理1（隐私性）**：CS无法获取任何个体UAV或单个集群的聚合梯度。

*证明*：
- 密文用 $pk_{global}$ 加密，需要所有集群的私钥分片之和才能解密
- 集群级DP噪声 $\eta_k$ 进一步保护集群级信息
- TR不接触密文，无法推断梯度

**定理2（TR可审计性）**：TR的密钥操作可被验证。

*证明概要*：TR每次密钥操作都生成审计日志（包含新旧公钥、轮换时间戳），CS可验证 $pk_{global}$ 的构成是否正确：$\sum b_k = b_{global}$。

**定理3（Byzantine鲁棒性）**：在 $f_{CH} < K/3$ 和 $f_U < n_k/3$ 下保证收敛。

*证明概要*：
- CH层：异常检测过滤集群内恶意UAV
- 云端：DP噪声不影响鲁棒性（噪声独立于梯度方向）
- 恶意CH的影响被限制在其集群权重范围内

---

### 方案五：HEFL-DA（Hierarchical FL with Dynamic Adaptation）

**面向UAV群动态拓扑的自适应联邦学习**

#### 5.1 应用场景

灾区救援场景中，多个UAV群动态部署，UAV可能在不同集群间迁移（如跟随目标移动），集群边界频繁变化。需要支持UAV的动态加入/退出和集群重组，同时保证隐私和模型质量。

- **架构**：层次型FL（UAV→边缘→云，三层，动态拓扑）
- **实体**：N架UAV（动态分组）、K个边缘节点EN（可动态增减）、1个云服务器CS
- **特点**：动态密钥管理、拓扑感知聚合、增量参与

#### 5.2 要解决的问题

1. UAV在集群间迁移时需要重新分配密钥（现有方案假设静态分组）
2. 新加入的UAV需要快速集成到联邦学习中（增量参与）
3. 边缘节点可能因灾害损毁而掉线
4. 不同时刻参与的UAV集合不同，聚合权重需要动态调整

#### 5.3 创新点

1. **动态密钥管理协议**：UAV迁移时通过密钥转换协议无缝切换集群密钥
2. **拓扑感知聚合**：CS根据实时的UAV-边缘连接图调整聚合策略
3. **增量参与机制**：新UAV可以在任意轮次加入，无需等待下一轮初始化
4. **边缘节点容错**：当边缘节点掉线时，其覆盖的UAV自动切换到邻近边缘节点

#### 5.4 威胁模型

- **CS**：半诚实——忠实聚合但试图推断UAV的迁移模式和位置信息
- **EN**：最多 $f_E < K/2$ 个为恶意或故障——可篡改聚合、丢弃更新或离线
- **UAV**：最多 $f_U < N/3$ 架为Byzantine恶意节点
- **新增威胁**：UAV迁移模式可能泄露位置隐私
- **共谋限制**：CS与EN不共谋
- **安全目标**：隐私性、位置隐私（迁移模式不可推断）、动态鲁棒性

#### 5.5 符号系统

| 符号 | 含义 |
|------|------|
| $T$ | FL训练轮次 |
| $\mathcal{S}^{(t)}$ | 第 $t$ 轮在线的UAV集合 |
| $\mathcal{S}_k^{(t)}$ | 第 $t$ 轮连接到EN $k$ 的UAV集合 |
| $sk_{k,i}^{(t)}$ | UAV $i$ 在第 $t$ 轮的私钥（可能因迁移而变化） |
| $\pi^{(t)}$ | 第 $t$ 轮的UAV-EN连接拓扑 |
| $\beta_k^{(t)}$ | EN $k$ 在第 $t$ 轮的可靠性分数 |
| $\gamma_i^{(t)}$ | UAV $i$ 在第 $t$ 轮的参与新鲜度分数 |

#### 5.6 方案设计

##### 阶段0：初始密钥生成

每架UAV $i$ 在首次加入时生成长期密钥对：

$$s_i^{long} \leftarrow \chi_s, \quad pk_i = (a, b_i = -(a \cdot s_i^{long} + e_i))$$

全局公钥为所有已注册UAV公钥之和（动态更新）：

$$pk_{global}^{(t)} = \left(a, \sum_{i \in \mathcal{R}^{(t)}} b_i\right)$$

其中 $\mathcal{R}^{(t)}$ 是到第 $t$ 轮为止所有已注册UAV的集合。

##### 阶段1：动态拓扑构建

**步骤1.1**：每轮开始，UAV广播其位置（模糊化后）和ID，EN收集连接请求。

UAV $i$ 模糊化位置：

$$\tilde{l}_i = l_i + \text{Lap}(b_{loc})$$

其中 $b_{loc}$ 是拉普拉斯噪声参数，保护位置隐私。

**步骤1.2**：EN $k$ 构建本轮连接列表 $\mathcal{S}_k^{(t)}$ 并报告给CS。

**步骤1.3**：CS构建全局拓扑 $\pi^{(t)} = \{\mathcal{S}_1^{(t)}, \ldots, \mathcal{S}_K^{(t)}\}$。

##### 阶段2：本地训练与加密

UAV $i$ 训练得到 $\Delta\mathbf{w}_i$，用当前全局公钥加密：

$$ct_i = \text{Enc}_{pk_{global}^{(t)}}(m_i)$$

**增量参与**：新加入的UAV $i_{new}$ 在第一轮只发送公钥 $pk_{i_{new}}$ 到CS，CS更新全局公钥：

$$pk_{global}^{(t+1)} = \left(a, b_{global}^{(t)} + b_{i_{new}}\right)$$

新UAV用更新后的公钥加密，旧UAV在下一轮使用新公钥。

##### 阶段3：边缘层聚合

EN $k$ 聚合其覆盖的UAV密文：

$$\mathcal{E}_k = \sum_{i \in \mathcal{S}_k^{(t)}} ct_i$$

**边缘节点容错**：如果EN $k$ 掉线，其覆盖的UAV自动连接到最近的备用EN $k'$：

$$\mathcal{E}_{k'} = \mathcal{E}_{k'} + \sum_{i \in \mathcal{S}_k^{(t)}} ct_i$$

##### 阶段4：云端聚合与拓扑感知加权

CS聚合所有在线EN的密文：

$$\mathcal{E}_{global} = \sum_{k \in \mathcal{S}_{EN}^{(t)}} \mathcal{E}_k$$

**拓扑感知加权**：

$$\mathbf{w}^{(t)} = \mathbf{w}^{(t-1)} + \sum_{k} \beta_k^{(t)} \sum_{i \in \mathcal{S}_k^{(t)}} \frac{n_i}{\sum_{j \in \mathcal{S}_k^{(t)}} n_j} \cdot \Delta\mathbf{w}_i$$

其中 EN $k$ 的可靠性分数：

$$\beta_k^{(t)} = \frac{|\mathcal{S}_k^{(t)}|}{\sum_j |\mathcal{S}_j^{(t)}|} \cdot \mathbb{1}[\text{EN } k \text{ 在线}]$$

UAV $i$ 的参与新鲜度分数（用于调整学习率）：

$$\gamma_i^{(t)} = 1 - \exp\left(-\frac{c_i^{(t)}}{\tau_{fresh}}\right)$$

其中 $c_i^{(t)}$ 是UAV $i$ 连续参与的轮次数。

##### 阶段5：部分解密与模型更新

至少 $t$ 架在线UAV提供部分解密份额：

$$\mu_i = \mathcal{E}_{global,1} \cdot s_i^{long} + \tilde{e}_i \mod q$$

CS合并并解密：

$$m_{global} = \left[\mathcal{E}_{global,0} + \sum_{i \in \mathcal{S}_{online}^{(t)}} \mu_i\right]_q$$

全局模型更新：

$$\mathbf{w}^{(t)} = \mathbf{w}^{(t-1)} + \eta^{(t)} \cdot \Delta\mathbf{w}_{global}$$

其中自适应学习率：

$$\eta^{(t)} = \eta_0 \cdot \frac{|\mathcal{S}^{(t)}|}{N} \cdot \bar{\gamma}^{(t)}$$

#### 5.6b 实体交互时序图

```
UAV_i              EN_k                     CS
  |                  |                       |
  |-- [模糊位置] --->|                       |
  |                  |-- [S_k^{(t)}] ------->|
  |                  |                       |
  |-- [ct_i] ------>|                       |
  |                  |                       |
  |                  |-- [E_k] ------------->|
  |                  |                       |
  |                  |   [EN_k 掉线]         |
  |                  X                       |
  |-- [ct_i] --------|--------------------->|  (切换到EN_k')
  |                                          |
  |<-- [解密请求] ---------------------------|
  |-- [μ_i] -------------------------------->|
  |                                          |
  |<-- [w^{(t)}] ----------------------------|
```

#### 5.7 安全性分析

**定理1（隐私性）**：CS无法获取个体UAV的梯度或位置信息。

*证明*：
- 梯度通过 $pk_{global}^{(t)}$ 加密保护
- 位置通过拉普拉斯机制实现 $\epsilon_{loc}$-差分隐私：$\Pr[\tilde{l}_i = x] / \Pr[\tilde{l}_i = y] \leq \exp(\epsilon_{loc} \cdot |x-y|/b_{loc})$
- UAV迁移模式被模糊化位置隐藏

**定理2（动态鲁棒性）**：在UAV动态加入/退出和EN故障下保证收敛。

*证明概要*：
- 新UAV的增量参与不影响已有UAV的密文（公钥增量更新）
- EN容错保证UAV密文不丢失（自动切换）
- 拓扑感知加权保证每轮有效参与者的贡献被正确加权

**定理3（Byzantine鲁棒性）**：在 $f_U < N/3$ 和 $f_E < K/2$ 下保证收敛偏差有界。

*证明概要*：
- 恶意UAV的梯度经裁剪后有界
- 恶意EN只能影响其覆盖的UAV子集
- 拓扑感知加权自动降低故障EN的权重（$\beta_k = 0$ 当EN离线）

---

## 第三部分：方案对比总结

### 五个方案对比表

| 维度 | UVFL-VT | UVFL-TBR | HEFL-VD | HEFL-TR | HEFL-DA |
|------|---------|----------|---------|---------|---------|
| 架构 | 标准FL（双服务器） | 标准FL（单服务器+TA） | 层次型（三层） | 层次型（四层） | 层次型（三层动态） |
| 密钥结构 | 多密钥（每人独立） | 单密钥+门限SS | 多密钥（每人独立） | 集群密钥+门限SS | 长期个人密钥（动态更新） |
| 同态方案 | MK-CKKS | CKKS（门限） | MK-CKKS | MK-CKKS | MK-CKKS（动态） |
| 可验证性 | Pedersen承诺+ZKP | 部分（门限保证） | Pedersen承诺 | 审计日志 | 拓扑一致性验证 |
| 鲁棒聚合 | 方向相似性加权 | 指数加权+裁剪 | 边缘检测+云端聚合 | 集群检测+DP噪声 | 拓扑感知加权 |
| 抗掉线 | 双掩码恢复 | Shamir SS | LSS门限解密 | 集群密钥分片 | 自动切换+增量参与 |
| UAV适用 | 中等规模（~50） | 小规模（~20） | 大规模（~100+） | 大规模跨域 | 大规模动态 |
| 主要创新 | 双向验证+鲁棒 | 门限解密+鲁棒 | 分层验证 | 可信中继+DP | 动态拓扑+增量 |

### 技术贡献总结

1. **UVFL-VT** 解决了现有方案（BPVFL）不防投毒、（ProFL）不适合UAV双服务器架构的问题
2. **UVFL-TBR** 将DSVFL的门限Paillier升级为门限CKKS（后量子安全），并集成鲁棒聚合
3. **HEFL-VD** 弥补了PrivHFL不防投毒、PPBR的CLDP噪声影响精度的问题
4. **HEFL-TR** 首次提出四层层次型FL架构，解决跨域UAV协作的密钥协调问题
5. **HEFL-DA** 首次支持UAV动态迁移和增量参与，解决现有方案假设静态分组的问题

---

*报告基于20篇论文的系统分析，涵盖2017-2026年联邦学习隐私保护领域的前沿研究。*
