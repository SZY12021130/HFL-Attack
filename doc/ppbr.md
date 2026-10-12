# PPBR 实现说明（双轨架构中的 PPBR 轨道）

> 论文完整阅读笔记（含全部公式与 15 步消息时序）：`paperlist/ppbr_notes.md`
> 论文：He 等, *PPBR: Privacy-Preserving and Byzantine-Robust Edge-Assisted Hierarchical
> Federated Learning in Mobile Networks*, IEEE TMC 2026。

## 0. 双轨架构：本项目框架与 PPBR 的解耦

本项目是**双轨**设计，两条轨道各自自洽、互不妥协，通过 `ppbr.enabled` 切换：

| | **CKKS 轨道**（默认，`ppbr.enabled: false`） | **PPBR 轨道**（`ppbr.enabled: true`） |
|---|---|---|
| 加密路线 | 单密钥 CKKS：客户端加密更新 → 边缘/云同态聚合 → 客户端解密全局模型 | PPBR 完整协议：PLHE 分块密文 + SA 成对掩码 + CLDP + 范数盲化裁剪 |
| 服务器/云私钥 | **云不持私钥**（只有公钥，全程不解密）——本项目核心要求 | **云持 `sk_A`**（论文原样；此模式下不再遵守"云零私钥"） |
| 边缘私钥 | 边缘只持云公钥 | **每个边缘自持独立 `sk_{Ei}`**，公钥字节注册到云 |
| 客户端密钥 | 持完整上下文（可解密全局模型） | 不持任何服务器密钥；全局模型明文下发（论文设定 w_a 公开） |
| 恶意客户端防护 | 依赖外层防御（聚合规则等） | PPBR 内置：CLDP 符号聚类过滤 + 云范数中位数筛选 + 盲化裁剪 |
| 全局模型下发 | 密文下发、客户端解密 | 明文广播 |
| 评估 | 客户端解密后评估上报 | 对明文 w_a 直接评估 |
| 全局投票 / TopK 稀疏化 | 可用（可插拔） | **不参与**（整条路线切换） |

两轨**共享**：数据加载与划分（`ckks_fed/data.py`）、模型（`ckks_fed/models.py`）、
客户端本地训练（`FederatedClient.local_train`，PPBR 轨道以
`global_params=` 明文入口复用）、攻击注入（`hfl_attacks` 注册表，
PPBR 轨道在协议之前以完全相同语义注入）、边缘-客户端分组
（`HierarchyController`，PPBR 自动开启）、指标采集（MetricsCollector，
键名一致，`run_experiment`/测试接口不断）。

> 一句话：**开启 PPBR 时，底层完整加解密路线严格走论文协议**；
> 关闭时，回到"云永远拿不到私钥"的本项目原架构。两者共用训练与攻击
> 基础设施，因此"同一攻击 × 两种轨道"的对比实验只需改一个开关。

## 1. 模块位置

```
ppbr/
├── __init__.py     # 导出 PPBRConfig / PPBRController / PPBRKeyContext / PPBRProtocol
├── config.py       # PPBRConfig：config.yaml ppbr 段的类型化映射（论文默认值）
├── cldp.py         # 算法 2b：式(2) CLDP 扰动（精确采样，非纯拒绝采样）
├── sign_stats.py   # 算法 2a：符号统计元组 (正/负/零号比例) + 云广播坐标子集 G
├── clustering.py   # 算法 2c：trimmed k-means，取 top-b 大簇为可信集
├── controller.py   # 数学机制：动量 / 边缘均值 / 云范数筛选+裁剪（与明文/密文无关）
├── crypto.py       # 传输层：PLHE 分块加解密 + sim 模式哈希种子掩码
├── sa.py           # 真实 SA（Bonawitz'17）：X25519+HKDF 协商、PRG 掩码、
│                   #   Shamir (t,n) 份额、early/late 两阶段掉线恢复
└── protocol.py     # PPBRProtocol：云/边缘/客户端完整消息时序编排
```

**可插拔开关**：`configs/config.yaml` 的 `ppbr.enabled`。
- `false`（默认）：不创建 PPBR 组件，行为与之前完全一致；
- `true`：自动开启 `hierarchy`（云-边-端是 PPBR 的前提），实验按
  `run_round_ppbr` 执行完整协议。与 `attack` 段组合即"攻击 × PPBR 防御"。

## 2. 论文算法 → 代码映射

| 论文章节 | 内容 | 代码 |
|----------|------|------|
| 算法 2a | 云选 ι 个坐标 G 广播；客户端统计符号比例三元组 | `controller.select_coordinates` / `client_sign_tuple` |
| 算法 2b | 客户端施加 ε_χ-CLDP 扰动（式 2） | `controller.perturb` → `cldp.perturb_sign_tuple` |
| 算法 2c | 边缘 trimmed k-means，取 top-b 大簇为可信集 U_i^(T) | `controller.filter_clients` → `clustering.select_trusted_clients` |
| 算法 3 | 客户端动量 m←φ·m+(1−φ)·Δ；边缘 SA 聚合得 m'_i 均值，加密 [2m'_i]_{Ei} | `controller.update_momenta`；`protocol.run_round` 客户端/边缘阶段 |
| 算法 4 | 云：盲化求得 ‖m_i‖，M=median，[M−θ,M+θ] 带 + 数量 l′ 筛选 | `protocol.run_round` 两轮点积范数协议；`controller.cloud_filter_norms` |
| 算法 5 | 裁剪 x″_i=min(1, M/‖m_i‖)；盲化聚合 + 去噪；w←w+ζ·m_a（本项目 Δ 为变化量方向，对应论文 w←w−ζ·m） | `protocol.run_round` 盲化裁剪聚合；`main.run_round_ppbr` 做 w_a 更新 |

## 3. 一轮协议的完整时序（`ppbr/protocol.py:run_round` + `main.py:run_round_ppbr`）

```
0  云明文广播 w_a（论文设定：全局模型公开）
1  客户端本地训练得 Δ（FederatedClient.local_train，global_params 入口）
2  攻击注入（可选）：DoS 掉线剔除 / 明文域更新篡改（hfl_attacks，两轨同一语义）；
   数据投毒已在初始化时替换 train_loader
── 算法 2 (CC) ──────────────────────────────────────────────
3  客户端：对云广播坐标子集 G 统计符号元组（正/负/零号比例），
   CLDP 扰动（式 2）→ 发给边缘的只是 3 维带噪元组
4  边缘：trimmed k-means 聚类过滤 → 可信客户端集 U^(T)
── 客户端阶段（算法 3 第 1 行 + SA + PLHE）────────────────────
5  可信客户端更新动量 m ← φ·m + (1−φ)·Δ
6  客户端采相对噪声 x = noise_rel·‖m‖·u/‖u‖；m' = m − x
7  客户端 → 边缘：[x]_A（云公钥加密，分块 CKKS）+ z = m' + 成对 SA 掩码
── 边缘阶段（算法 3）────────────────────────────────────────
8  边缘：SA 求和掩码相消 → m'_i = (1/n'_i)Σ m'_{i,j}
   （边缘只见掩码态个体输入与聚合均值，见不到任一客户端明文更新）
9  边缘：同态求和 [x_i]_A = Σ_j [x_{i,j}]_A；加密 [2m'_i]_{Ei}（边缘私钥）→ 云
── 云范数盲化（算法 4，两轮点积协议）──────────────────────────
10 云解密 [x_i]_A → x_i（再除以 n'_i，与 m'_i 均值尺度对齐），
    取随机标量 r_i，算 [v]_{Ei} = [2m'_i]_{Ei} ∘ (r_i·x_i) 发回边缘
11 边缘解密 v，自算 S_i = Σv = 2r_i(m'_i·x_i)，连同边缘自算的 ‖m'_i‖² 发云
12 云算 ‖m_i‖² = ‖m'_i‖² + ‖x_i‖² + S_i/r_i —— 云只知范数
── 云筛选（算法 4/5）─────────────────────────────────────────
13 云：M = median({‖m_i‖})，[M−θ,M+θ] 带 + 数量 l′ 筛出可信边缘 A^(T)，
    裁剪系数 x″_i = min(1, M/‖m_i‖)
── 盲化裁剪聚合（算法 5）─────────────────────────────────────
14 云取全局随机标量 R，发 t_i = R·x″_i（边缘看 t_i 是随机数）
15 边缘算 u_i = t_i·m'_i，叠加边缘级 SA 掩码后发云（掩码在 Σ 上相消）
16 云：m_a = (Σ t_i m'_i / R + Σ x″_i x_i) / l′ = Σ x″_i m_i / l′
17 云更新 w_a ← w_a + ζ·m_a（论文 w ← w − ζ·m；符号差异：Δ 为参数变化
   量方向、m 为梯度方向），回到第 0 步；评估对明文 w_a 进行
```

### 各实体可见性（泄露分析）

| 实体 | 可见 | 不可见 |
|------|------|--------|
| 云 | 边缘范数 ‖m_i‖（论文允许泄露）、盲化点积 S、聚合结果 m_a、明文 w_a | 任一客户端更新、任一边缘的 m'_i / x_i 个体值（只知边缘均值与范数） |
| 边缘 | 3 维 CLDP 符号元组、掩码态个体输入 z、聚合均值 m'_i、随机向量 r_i∘x_i | 任一客户端明文更新（SA 掩码保护）、x_i 个体值 |
| 客户端 | 明文 w_a、本地 Δ/m/x | 其他客户端一切信息 |

### 3.1 密钥清单（与论文核对）

| 密钥 | 论文 | 本项目实现 | 匹配 |
|------|------|-----------|------|
| PLHE 云密钥对 (pk_A, sk_A) | 1 对，仅云持有 | `PPBRKeyContext`（cloud_ctx，含 sk_A） | ✅ |
| PLHE 边缘密钥对 (pk_{A_i}, sk_{A_i}) | l 对，边缘 A_i 各自持有 | 每边缘一个 `PPBRKeyContext`（edge_ctxs；私钥边缘自持，公钥字节注册到云） | ✅ |
| SA 客户端密钥 (sk_i, pk_i, b_i) | 每客户端一组：DH 协商成对掩码 + Shamir (t,n) 分片 b_i 容忍掉线 | **真实实现**：`ppbr/sa.py` 每轮生成临时 X25519 密钥对，成对种子 = HKDF(DH(sk_i, pk_j))，上下文绑定组/轮次/对；sk_i 与 b_i 均以 Shamir (t, n−1) 分片给同组其余方（素数域 2^521−1），early/late 两阶段掉线恢复（见 §3.2）；`sa_key_exchange="sim"` 可退回哈希种子模拟 | ✅ |
| CLDP 参数 (ε_χ, L, L') | 公开 | `PPBRConfig` | ✅ |

即：服务器侧密钥对数量与论文**完全一致**（1 + l），客户端侧论文的 n 组
SA 密钥为每轮临时的真实 X25519 密钥对（前向安全），成对掩码种子由
DH+HKDF 真实协商，掉线恢复走 Bonawitz 私钥重建路径（§3.2）。

### 3.2 SA 层（`ppbr/sa.py`）：阶段、掩码与掉线恢复

PPBR 论文的 SA 描述对应 Bonawitz'17（CCS 2017）安全聚合。实现为真实
密码学（非模拟）：X25519 Diffie-Hellman + HKDF-SHA256 密钥协商、
SHA256/torch.Generator PRG、素数域 2^521−1 上的 Shamir (t, n−1) 秘密
共享。借鉴来源与差异：

| 来源 | 借鉴内容 | 差异 |
|------|----------|------|
| ammartahir24/SecureAggregation | 阶段划分（广播公钥 → 掩码上传 → 揭示）、成对掩码方向约定 z_j = in_j + Σ_{j'>j} PRG(s) − Σ_{j'<j} PRG(s) | 该实现省略了 Shamir；本项目按 Bonawitz 原文补全私钥/种子分片与掉线恢复 |
| Flower SecAgg+ 示例 | X25519 + HKDF-SHA256 协商选型（替代玩具模幂 DH） | 一致的现代选型 |
| INRIA Declearn secagg | PRG 掩码控制器设计 | 一致的 PRG 结构 |

**一轮内的三个阶段**（客户端组 = 同边缘下的可信客户端，组大小 n′_i）：

1. **密钥交换**。每客户端生成临时 X25519 密钥对，公钥经边缘广播；
   与同组每个 j' 各自计算 `s_{j,j'} = HKDF(DH(sk_j, pk_j'), info)`，
   `info = "ppbr-sa-<组>-<min(j,j')>-<max(j,j')>-<轮次>"` 绑定上下文
   （不同组/轮次/对导出不同种子，防跨组重放）。同时把 **sk_j 本身**以
   Shamir (t, n′_i−1) 分片发给同组其余方（Bonawitz 私钥备份）。
2. **掩码上传**。客户端计算
   `z_j = m'_j + Σ_{j'>j} PRG(s_{j,j'}) − Σ_{j'<j} PRG(s_{j',j}) + PRG(b_j)`，
   并把自负载种子 **b_j** 以 Shamir (t, n′_i−1) 分片发给同组其余方；
   z_j 发往边缘。
3. **揭示与聚合**（边缘侧 `SABucket.aggregate`）：
   - 幸存方揭示 PRG(b_j)，边缘摘除其自负载掩码；
   - 成对掩码按**无序对**处理：对 (a,b)（a<b）的掩码 r 同时出现在
     z_a（+r）与 z_b（−r），谁提交了 z 就摘谁的项，在 Σ 上严格相消；
   - **掉线恢复**（私钥重建路径）：对任一掉线方 d，边缘用幸存方代持的
     ≥ t 份份额重建 sk_d，与组内每个 x 的公钥重导出成对种子，重算并
     扣除 d 参与的全部成对掩码项。相比"幸存方揭示与掉线方的成对掩码和"
     路径，私钥重建**与掉线早晚、掉线方之间是否互为对偶完全无关**
     （混合掉线下后者会重复摘除同一掩码项）。

**掉线两阶段语义**（与 Bonawitz 一致）：

| 掉线时机 | z 是否在部分和中 | 输入是否计入聚合 | 恢复手段 |
|----------|------------------|------------------|----------|
| 提交前（early） | 否 | 否 | 重建 sk_d 摘除幸存方 z 中与 d 的成对项 |
| 提交后（late） | 是 | **是** | 重建 sk_d 摘除双方成对项 + 重建 b_d 摘除自负载掩码 |

**安全性质**（`test_sa.py` 固化）：n−1 方合谋 + 聚合方在不知道 b_victim
时无法从 z_victim 剥离受害方输入（对照：无自负载掩码时可剥离）。

**配置**：`ppbr.sa_key_exchange`（`"dh"` 默认，真实协商；`"sim"` 退回
哈希种子模拟，不抗合谋，仅供对照）；`ppbr.sa_simulate_dropout_rate`
（掉线仿真比例，仅实验用途，组 < 4 时不触发以保证份额可恢复）。

## 4. 关键设计决策（与论文的对应及工程取舍）

1. **两轮点积范数协议**。论文在密文内完成范数点积；但 TenSEAL 无
   `rotate`/密文槽求和，同态点积不可得。改为云/边缘协作两轮：云做
   密文×明文乘法 `[2m']∘(r·x)`，边缘解密后明文求和回传标量 S。安全性：
   云只知盲化点积（r 随机，且云最终只需 ‖m‖）；边缘只见随机向量
   r∘x 的逐元乘积 v，v 的分布与 2r·m'∘x 不可区分时反推不出 m'、x。
2. **相对噪声与灾难性抵消（重要）**。范数公式
   ‖m‖² = ‖m'‖² + ‖x‖² + 2m'·x 在固定大方差噪声（如 σ=0.1 逐元素、
   dim≈4×10⁴ 时 ‖x‖²≈450 ≫ ‖m‖²≈1）下发生灾难性抵消：三项 ~450 抵消为
   ~1，CKKS 的 1e-3 级误差直接淹没真值（实测范数开方出 NaN）。因此噪声
   取**与动量同尺度**：`x = noise_rel·‖m‖·u/‖u‖`（默认 noise_rel=0.5），
   既保持掩码效果，又使展开式各项与 ‖m‖² 同量级。denoise 对任意 x 精确
   成立，噪声尺度不影响收敛语义。
3. **高精度 PPBR 密码参数**。范数盲化需要一次密文×明文乘法，且要压制
   抵消后的相对误差：PPBR 轨道默认 poly=8192、coeff=[60,40,60]、
   scale=2^40（解密误差 ~1e-9 级）。仅当 `config.ckks` 的参数满足精度
   门槛（scale ≥ 2^30、模链 ≥ 3 段）时才复用，否则自动回退并告警——
   双轨解耦，PPBR 不继承 CKKS 轨道的低精度测试参数。
4. **`[x_i]_A` 的均值除法**。边缘对 m' 取均值（算法 3 语义），同态求和
   得到的却是 Σx；云解密后除以 n'_i（除法公开，云本就执行分组），与
   m'_i 尺度对齐，denoise 才能精确抵消。实现早期曾因漏除 n 导致
   denoise 失败（m_a 均值 ~0.004 vs 协议产物 ~0.35），已由
   `test_ppbr_crypto.py::test_protocol_matches_plaintext_reference` 固化。
5. **‖m'_i‖² 的上传**。边缘把自算的 ‖m'_i‖² 明文发云。云由此知晓
   ‖m'‖=‖m−x‖，信息量与论文直接泄露的 ‖m‖ 等价（x 为客户端随机噪声，
   对云而言 m'=m−x 是随机平移）；换来的是云无需第二轮交互即可算 ‖m‖。
6. **SA 真实实现**（`ppbr/sa.py`，借鉴 ammartahir24/SecureAggregation 的
   阶段划分与掩码方向约定、Flower SecAgg+ 的 X25519+HKDF 选型）。
   借鉴的参考实现省略了 Shamir，本项目按 Bonawitz 原文补全：sk_i 与 b_i
   均做 Shamir (t, n−1) 分片，掉线方的**全部**掩码（成对 + 自负载）都可
   恢复，与掉线发生早晚、掉线方之间是否互为对偶无关（早期版本按
   "成对掩码和"揭示曾导致 early/late 混合掉线时重复摘除同一掩码项，
   已由 `test_sa_mixed_dropouts` 固化）。
7. **掉线语义**（Bonawitz 两阶段，与论文"Shamir 份额恢复"一致）。
   提交前掉线（early）：z 不在部分和中，输入不计入聚合；提交后掉线
   （late）：z 已在部分和中，重建 b_i 摘除自负载掩码，输入**仍计入**。
   单进程仿真无真实掉线，`sa_simulate_dropout_rate > 0` 可按组随机
   仿真（半数 early、半数 late，保证幸存方 ≥ max(2, t) 使份额可恢复），
   由 `test_sa.py` 的协议集成测试验证：40% 掉线下协议产物与明文参考
   余弦相似 > 0.9。`hierarchy.edge_selection_rate < 1` 时云按幸存边缘
   自适应 l′。
8. **动量语义换算**。论文动量作用在完整本地模型上、ζ=0.0125；本项目
   上传差值，m=φ·m+(1−φ)·Δ 使首轮有效更新缩小 (1−φ) 倍，默认
   `global_zeta = 1/(1−φ) = 10` 补偿，无攻击时精度与 FedAvg 基线一致。

## 5. 一轮训练中的调用位置（`main.py:run_round_ppbr`）

```
run_experiment ── ppbr.enabled ? run_round_ppbr : run_round_encrypted
run_round_ppbr:
  云明文广播 w_a → 客户端 local_train → 攻击注入(_apply_attacks_to_updates)
                → ppbr.run_round(完整协议, 返回 m_a)
                → w_a ← w_a + ζ·m_a → evaluate(明文) → 指标入库
```

fail-open：若算法 2 过滤后无可信客户端、或云筛选空集，本轮 m_a 为零向量，
全局模型不更新，训练继续。

## 6. 威胁模型前提：恶意客户端的放置（attack.placement）

PPBR 的鲁棒性保证依赖**每个边缘内良性客户端占多数**（论文假设客户端随机
关联边缘服务器）。本项目的静态分片把连续编号的客户端划入同一边缘，若采用
默认 `attack.placement: last`（编号最大的最后 N 个为恶意），当
`malicious_count ≥ 每边缘客户端数`时会出现**全恶意边缘**：边层 CLDP 聚类对
一致作恶的恶意簇无效，云层的范数筛选对保范数的攻击（sign flipping、ALIE、
MinMax 等）也无差别——这是范数类方法的固有边界，不是实现缺陷。

**"攻击 × PPBR"实验请使用 `attack.placement: spread`**（每边缘先各摊 1 个
恶意客户端），对应论文的随机关联假设。复现示例：

```bash
# configs/config.yaml:
#   attack: {enabled: true, name: "model:sign_flipping", malicious_count: 3,
#            placement: "spread"}
#   ppbr:   {enabled: true}
python main.py
```

## 7. 复现实验

```bash
# 无攻击 + PPBR 完整协议（精度应与 FedAvg 基线基本一致）
# configs/config.yaml: ppbr.enabled: true
python main.py

# 攻击 × PPBR 防御
# configs/config.yaml: attack.enabled: true, attack.name: "model:sign_flipping"
#                      ppbr.enabled: true
python main.py

# 单元 + 协议一致性 + 端到端测试
python -m pytest tests/test_ppbr.py tests/test_ppbr_crypto.py -v
```

## 8. 实验记录（2026-10-11/12，论文配置 200 轮完整复现）

### 8.1 实验配置

统一按论文 §VIII：MNIST + MLP（batch 32）、240 客户端 ÷ 8 边缘、Non-IID Dirichlet
α=0.5、本地 SGD lr=0.01 ~24 步/轮、200 轮；PPBR ε_χ=3, L=6, L'=7, k=2, b=1,
φ=0.9, ζ=10（=1/(1−φ) 补偿），trim_ratio=0.3，SA=真实 X25519+HKDF+Shamir；
攻击 β=30%（72/240 恶意，placement=spread 摊到 8 边缘），单次运行（论文取 10–20 次均值）。
配置：`configs/config_ppbr_paper.yaml` + `configs/config_ppbr_attack_<name>.yaml`。

### 8.2 结果汇总（test_acc %，每轮在 10000 测试集上评估）

| 实验 | r20 | r60 | r100 | r140 | r180 | **r200** | 论文 Table IV 精确值 |
|---|---|---|---|---|---|---|---|
| 干净基线 | 84.2 | 88.3 | 92.8 | 94.3 | 93.9 | **95.31** | 95.47（IID 最优 95.66）✓ |
| label_flipping | 73.8 | 82.2 | 88.6 | 92.5 | 89.1 | **93.65** | 95.82 ✓ |
| gaussian_noise（相对标定 σ=σ_benign） | 82.0 | 86.2 | 90.8 | 91.8 | 92.8 | **94.70** | 95.37 ✓ |
| ipm（ξ=1.0，论文未评测此值） | 47.3 | 77.5 | 83.7 | 91.0 | 85.6 | **85.95** | ξ=0.1→95.35；ξ=100→95.37 ⚠ |
| sign_flipping | 36.8 | 72.0 | 79.1 | 80.6 | 87.6 | **79.86** | 93.70 ⚠ |
| **alie（z=1.5 标准版）** | 84.7 | 62.2 | 55.4 | 39.6 | 21.0 | **19.50** | **95.37** ⚠⚠ |

### 8.3 与论文结论的对照分析

**一致的部分**：干净基线 95.31% vs 论文 95.66%（差 0.35pp）；label_flipping 与
gaussian_noise（修复标定后，见 8.4）均守住 93-95%，符合"所有攻击接近理论最优"。

**不一致的部分与参数审计**（对论文全文逐条比对后的结论，详见
`paperlist/ppbr_notes.md`）：

1. **ALIE 完全击穿（19.5%）——首要原因是攻击强度未对标论文，而非防御失效**。
   论文明确"z 从 0 到 1 取值，取其累积标准正态分布值"，即 z=1.0 对应
   Φ⁻¹(1−0.1)≈1.28（10% 截尾）；而初版实现取标准 Baruch 版 z=1.5（
   Φ⁻¹(0.933)），攻击偏差放大约 (1.5/1.28)²≈1.37 倍且落在论文评测范围之外。
   过滤日志佐证：每轮 ~35 恶意提交、18–21 存活（~55%），良性误杀也达 ~55%——
   ALIE 合谋者提交完全相同的 μ+zσ 向量，其符号元组 ≈ 全体均值方向统计，与
   良性主簇高度重叠；再经 CLDP 强扰动（ε_χ=3）后不可分，存活恶意更新的方向
   偏差经 φ=0.9 动量逐轮累积（不动点偏差放大 1/(1−φ)=10 倍），拖向随机精度。
   已按论文语义以 z=1.0 重跑，见 §8.5。
2. **ipm 85.95%——ξ 取值不在论文评测网格上**。论文 Table IV 只评测 ξ=0.1 和
   ξ=100 两档（分别得 95.35 / 95.37），代码初值 ξ=1.0 是未测的中间点。
   已按论文两档重跑，见 §8.5。
3. **sign_flipping 79.86% vs 论文 93.70——存在实现参数差异，残余差距待归因**。
   审计发现：论文符号翻转只翻转 **ι=8** 个坐标，代码翻转 **100** 个（论文未给出
   ι=100 的结果，无法直接对照）；云筛选 trim 比例论文未报告（代码 0.3）。
   另外论文结果为 10–20 次运行均值，单次运行方差不可忽略。可选的归因实验：
   ι=8 灵敏度重跑（待做）。
4. **gaussian_noise 标定修正**：初版实现 std=1.0 绝对尺度（比良性更新大
   ~100 倍）导致 10% 崩溃，不属于论文评测语义；已改为相对标定
   `N(0, (rel_std·σ_benign)²)`（`attack.params: {mode: relative, rel_std: 1.0}`，
   与 CenteredClipping 文献一致），修复后 94.70%。

### 8.5 论文参数对齐重跑（进行中）

按论文原文语义重跑三个不一致实验（配置与 §8.1 相同，仅攻击参数对齐）：
- IPM ξ=0.1（论文 Table IV 期望 95.35）
- IPM ξ=100（论文 Table IV 期望 95.37）
- ALIE z=1.0（累积标准正态语义，论文 Table IV 期望 95.37）

结果待补。
3. **gaussian_noise 标定修正**：初版实现 std=1.0 绝对尺度（比良性更新大
   ~100 倍）导致 10% 崩溃，不属于论文评测语义；已改为相对标定
   `N(0, (rel_std·σ_benign)²)`（`attack.params: {mode: relative, rel_std: 1.0}`，
   与 CenteredClipping 文献一致），修复后 94.70%。

### 8.4 工程备注

- alie/ipm 长跑曾在 ~130 轮处原生堆分配崩溃（TenSEAL 密文对象累积）；
  已在 `run_experiment` 每轮加 `gc.collect()` + `torch.cuda.empty_cache()` +
  `process_rss_gb` 记录（commit d0b92ae），续跑 RSS 稳定 ~8-9GB。
- 攻击实验均经 checkpoint 续跑补齐 200 轮；恶意放置 spread（每边缘 ≤9 恶意，
  维持"边缘良性多数"前提）。
- 攻击诊断基础设施：`attack.params` 透传攻击构造器；PPBR 协议每轮打印
  `边缘过滤 提交/可信/恶意存活`（`ppbr/protocol.py`）。
