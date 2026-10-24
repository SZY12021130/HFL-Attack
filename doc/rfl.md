# RFL 实现说明（鲁棒 + 可验证联邦学习轨道）

> 论文：Zhou, Dai, Yang, Xiang, *Robust Privacy-Preserving Federated Learning
> for Edge Computing With New Client Integration*, IEEE TDSC 2026
> （PDF：`paperlist/Robust/`，IFGIA 攻击 + RFL 防御的完整论文）。

## 0. 轨道定位：本项目框架与 RFL 的解耦

本项目是多轨道设计，RFL 是独立的一条完整协议轨道，通过 `rfl.enabled` 切换：

| | **CKKS / PPBR 轨道** | **RFL 轨道**（`rfl.enabled: true`） |
|---|---|---|
| 信任模型 | 云半诚实（CKKS 云零私钥）/ 恶意客户端 | **云 CS 可半诚实或恶意**（伪造客户端身份 = IFGIA、篡改/丢弃聚合结果）；ES、EC 诚实 |
| 隐私机制 | CKKS 同态加密 / SA 掩码 | **模型切分**（EC 只见浅层）+ **中间表示高斯 DP**（式 7，轻量化，无加解密开销） |
| 身份验证 | 无 | **EC 身份注册 + 数字签名验签（式 8/9）**：伪造客户端（IFGIA）被拒 |
| 聚合完整性 | 无（CKKS）/ 边缘过滤（PPBR） | **同态哈希 + 签名校验（式 13/14/16/17）**：云篡改被检出，全体 ES 中止 |
| 加密体系 | 单密钥 CKKS / PLHE + SA | **不经单密钥 CKKS**：Ed25519 签名 + 素数阶子群乘性同态哈希 |
| 客户端开销 | 随全局模型维度线性增长 | **维度无关**（Theorem 4）：EC 只训练固定维度浅层 |

互斥与共存：`rfl.enabled` 与 `ppbr.enabled` / `threshold.enabled` 互斥（启动校验报错）；
与 `encryption.enabled: false` 可共存（此时唯一轨道即 RFL，跳过全部 CKKS 初始化）。

两轨**共享**：数据加载与划分（`base/data.py`）、模型（`base/models.py`）、
边缘-客户端分组（`HierarchyController`，RFL 自动开启 `hierarchy`；RFL 模式下
hierarchy 只提供关联分组，不创建 CKKS 密文边缘节点）、指标采集。

> ⚠️ **威胁模型差异**：本项目 `hfl_attacks` 的 24 种攻击是"恶意客户端"视角，
> 作用于完整模型更新 Δ；RFL 的论文威胁模型是"恶意云"（IFGIA 伪造客户端 +
> 篡改聚合）。split learning 消息语义不同，RFL 轨道**不支持 hfl_attacks
> 注入**（启动时警告并跳过），改由两个内置仿真项演示：
> - `rfl.cs_tamper: drop_edge / scale_edge` —— 恶意云篡改聚合 → 式(17) 检出；
> - `rfl.simulate_ifgia: true` —— 恶意云伪造虚拟客户端 → 式(9) 拒绝。

## 1. 模块位置

```
rfl/
├── __init__.py     # 导出 RFLConfig / RFLProtocol / DigitalSignature /
│                   #   HomomorphicHash / build_split / dp_sigma
├── config.py       # RFLConfig：config.yaml rfl 段的类型化映射（论文默认值）
├── crypto.py       # DigitalSignature（Ed25519，式 8/9/14/16）+
│                   #   HomomorphicHash（乘性同态哈希，式 13/17）
├── splitnn.py      # 模型切分（MLP 按 hidden 层 / CNN 按 conv-fc）、
│                   #   扁平向量↔命名参数映射、DP 噪声尺度（式 7）
└── protocol.py     # RFLProtocol：论文算法 2 的完整消息时序编排
```

**可插拔开关**：`configs/config.yaml` 的 `rfl.enabled`；可直接运行的实验配置：
`configs/config_rfl.yaml`（MNIST + MLP + 3 边缘）。

## 2. 论文算法 2 → 代码映射

| 论文 | 内容 | 代码 |
|------|------|------|
| 步骤 1 | 各 EC/ES 生成密钥对；EC 向 ES 注册 pk，ES 间交换 pk | `protocol._client_key/_edge_key` + `registry`（注册通道 EC→ES 直连，CS 不在通道上） |
| 步骤 2 | EC 浅层前向 p_u = SL(p̲, D_u)（式 6） | `protocol.run_round` split learning 循环 + `splitnn.build_split` |
| 步骤 3 | 高斯 DP 扰动 p̂_u = p_u + δ_u（式 7，σ=c·d·S2/ε，S2=2C/\|D_u\|） | `splitnn.dp_sigma` + 协议内 `torch.randn_like` |
| 步骤 4 | EC 签名 z_u = DS.Sign(sk_u, p̂_u)（式 8） | `DigitalSignature.sign_vector` |
| 步骤 5 | ES 验签（式 9），失败拒绝 | `DigitalSignature.verify_vector` + 注册表身份比对 |
| 步骤 6 | ES 深层前向 p_v = DL(p̂_u, p̄)（式 10），回传 | `DeepMLP/DeepCNN.forward` |
| 步骤 7 | EC 输出梯度 g_u = ∇Loss(p_v, D_u)（式 11），回传 | `torch.autograd.grad(loss, logits)` |
| 步骤 8 | ES 深层反向 g_v = Update(g_u)（式 12）；∂Loss/∂p̂_u 回传 EC 更新浅层 | `torch.autograd.backward(logits, g_u)` + `h.backward(grad_back)` |
| 步骤 9 | ES 哈希 h_v = HH.Hash(g_v)（式 13）、签名 z_v（式 14），上传 CS 并广播 | `HomomorphicHash.hash_ints` + `sign_int` |
| 步骤 10 | CS 聚合 g_p = p + (1/M)Σ g_v（式 15），广播 | `protocol.run_round` 整数求和 S = Σ q_v |
| 步骤 11 | ES 互验签名（式 16）+ Π h_v ≡ H(M·(g_p−p))（式 17），失败退出 | `DigitalSignature.verify_int` + `HomomorphicHash.verify_aggregate` |
| §III | IFGIA：CS 伪造虚拟客户端 | `rfl.simulate_ifgia`：伪造身份无注册记录 → 式(9) 拒绝 |
| §IV-B | 恶意 CS 篡改聚合 | `rfl.cs_tamper`：式(17) 检出 → 全体 ES 中止（零更新） |

## 3. 关键设计决策

### 3.1 式(17) 在定点整数域校验（无浮点误差）

同态哈希的输入是定点量化整数 `q_v = round(u_v · s)`。云对**整数**求和
`S = Σ_v q_v`，每个 ES 校验 `Π_v h_v = H(S)`——两边都是同一整数向量上的
模幂乘积，严格相等；浮点累加误差不存在。全局更新 `g = dequantize(S)/M`。
（若直接对浮点聚合值哈希，舍入会使 Π h_v ≢ H(Σ)，校验失去意义。）

### 3.2 全模型统一更新向量

论文式(15) 只写深层梯度更新。实现将每个 ES 的更新取 **浅层∥深层** 拼接
向量统一量化、哈希、聚合与校验（式 13-17 对拼接向量原样成立，同态性是
按坐标的），使浅层与深层一致更新、全模型可用。浅层梯度经 ES 聚合（EC
签名上传），与论文"ES 聚合组内更新"的角色一致。

### 3.3 身份注册 = IFGIA 防御根基（新客户端集成）

论文攻击面在**新客户端集成**阶段：CS 伪造虚拟客户端骗过 SA 分组，代数
孤立目标梯度（式 1-3）后做梯度反演。RFL 的防御：EC 身份经 **EC→ES 直连
通道**注册 pk（算法 2 第 1 行），CS 不在注册通道上，无法为伪造客户端登记
合法身份；式(9) 验签 = 签名有效 ∧ pk 已在注册表，伪造客户端必然被拒。
`simulate_ifgia: true` 即演示此路径（日志可见"未知身份（IFGIA 伪造客户端）"
拒绝记录）。

### 3.4 split learning 语义补全

论文式(11)(12) 只描述输出梯度回传与深层反向；浅层参数的梯度
∂Loss/∂p̂_u 由 ES 在深层反向时一并算出并回传 EC（U 形 split learning 的
标准语义），否则浅层无法训练。中间表示上的 DP 噪声按论文式(7) 逐次
上传施加（σ=c·d·S2/ε，d 为中间表示维度）。

## 4. 信任模型与安全性说明

- **云 CS（半诚实/恶意）**：只能拿到加噪中间表示 p̂_u（DP 保护，式 7）、
  各 ES 的聚合更新 g_v 与同态哈希 h_v（ reveals Σg_v 与按坐标的指数和，
  不揭示单个 EC 数据）。伪造客户端被式(9) 拒；篡改/丢弃 g_v 被式(17) 检出，
  全体 ES 中止本轮（本轮全局模型不更新）。
- **ES（诚实）**：可见组内客户端的 p̂_u（DP 后）与 g_v；持 sk_v 对哈希签名；
  可自主完成式(16)(17) 全部校验，无需任何可信第三方或客户端在线。
- **EC（诚实）**：只训练浅层（固定维度，Theorem 4：计算/通信与全局模型
  维度无关）；持 sk_u 签名上传，私钥不出本地。

## 5. 运行

```bash
# 端到端（MNIST + MLP，10 轮，3 边缘服务器；loss 单调下降、每轮式 16/17 校验通过）
python main.py --config configs/config_rfl.yaml --output-dir results/test_rfl

# 恶意云篡改聚合 → 式(17) 检出、全体 ES 中止
# （把 config_rfl.yaml 的 cs_tamper 改为 "drop_edge" 或 "scale_edge"）

# IFGIA 伪造客户端 → 式(9) 拒绝
# （把 simulate_ifgia 改为 true）

# 测试
python -m pytest tests/test_rfl_crypto.py tests/test_rfl.py -v
```

## 6. 已知限制

- 同态哈希为纯 Python 大数模幂：开销 ~O(维度 × 群位宽)，默认 256-bit 群 +
  `hidden_dims: [64, 32]` 量级可用；更大模型需调小 `hash_bits` 或扩大维度预算。
- 群参数为确定性 Miller-Rabin 仿真参数（40 轮），非生产级素数生成。
- 不支持客户端掉线中途退出（诚实 EC 全勤假设；掉线容忍是未来工作）。
- 论文评测中的 IFGIA 成功率（99.5%→73.6%）依赖其完整梯度反演管线，
  本项目以"伪造身份被式(9) 拒绝"呈现防御语义，未复现梯度反演攻击本身。
