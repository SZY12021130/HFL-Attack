# PPBR 实现说明（可插拔模块）

> 论文完整阅读笔记（含全部公式与消息时序）：`paperlist/ppbr_notes.md`
> 论文：He 等, *PPBR: Privacy-Preserving and Byzantine-Robust Edge-Assisted Hierarchical
> Federated Learning in Mobile Networks*, IEEE TMC 2026。

## 1. 模块位置与可插拔性

PPBR 实现为独立顶层包 `ppbr/`，与 `ckks_fed/`（外层框架）解耦：

```
ppbr/
├── __init__.py     # 导出 PPBRController / PPBRConfig
├── config.py       # PPBRConfig：config.yaml ppbr 段的类型化映射（含论文默认值）
├── cldp.py         # 算法 2b：式(1)(2) CLDP 扰动（拒绝采样）
├── sign_stats.py   # 算法 2a：符号统计元组 (正/负/零号比例) + 云广播坐标子集 G
├── clustering.py   # 算法 2c：trimmed k-means，取 top-b 大簇为可信集
└── controller.py   # 每轮编排：算法 2/3/4/5 的完整流程 + 客户端动量状态
```

**可插拔开关**：`configs/config.yaml` 的 `ppbr.enabled`。
- `ppbr.enabled: false`（默认）：代码不进入 PPBR 分支，行为与之前完全一致；
- `ppbr.enabled: true`：**自动开启 `hierarchy`**（云-边-端是 PPBR 的前提，
  无需手动设置 `hierarchy.enabled`），并打印告警。与 `attack` 段组合即
  "攻击 × PPBR 防御"实验。

## 2. 论文算法 → 代码映射

| 论文章节 | 内容 | 代码 |
|----------|------|------|
| 算法 2a | 云选 ι 个坐标 G 广播；客户端统计符号比例三元组 | `controller.select_coordinates` / `client_sign_tuple` |
| 算法 2b | 客户端施加 ε_χ-CLDP 扰动（式 2） | `controller.perturb` → `cldp.perturb_sign_tuple` |
| 算法 2c | 边缘 trimmed k-means，取 top-b 大簇为可信集 U_i^(T) | `controller.filter_clients` → `clustering.select_trusted_clients` |
| 算法 3 | 客户端动量 m←φ·m+(1−φ)·Δ；边缘对可信客户端动量取均值 | `controller.update_momenta` / `edge_means` |
| 算法 4 | 云：M=median({‖m_i‖₂})，[M−θ,M+θ] 带 + 数量 l′ 筛选 | `controller.cloud_filter_edges` |
| 算法 5 | 裁剪 x″_i=min(1, M/‖m_i‖)；w←w−ζ·Σx″_i·w_i·m_i | `cloud_filter_edges` 返回 clip；`scale_of` 折入上传权重 |

## 3. 仿真约定（与论文的差异，必读）

PPBR 论文的隐私外壳由三种原语组成：CLDP（只用于 3 维符号元组）、
SA（Bonawitz'17 成对掩码安全聚合）、PLHE（BFV + 边缘独立密钥 + 随机数盲化）。
本项目仿真环境的对应处理：

1. **信息流严格对齐协议**：`filter_clients` 的输入只有 CLDP 扰动后的 3 维符号
   元组（边缘拿不到模型更新）；`cloud_filter_edges` 的输入只有边缘动量范数
   （云拿不到边缘模型）。仿真在明文上计算数学机制，但每个实体的 API 可见性
   与论文一致。
2. **SA/PLHE 以明文短路**：本项目传输层已用 CKKS 且云/边缘均不持私钥，
   不另建第二套加密栈。论文中 SA 掩码与 PLHE 盲化的净效果（服务器只见均值、
   云只见盲化范数）在仿真中直接成立；若未来需要严格复现盲化流程，
   `cloud_filter_edges` 是唯一需要替换的实现点（接口不变）。
3. **动量语义换算**：论文动量作用在完整本地模型上，全局步长 ζ=0.0125；
   本项目上传差值更新，动量 m=φ·m+(1−φ)·Δ 使首轮有效更新缩小 (1−φ) 倍，
   故默认 `global_zeta = 1/(1−φ) = 10` 补偿，保证无攻击时精度与 FedAvg 基线一致。
4. **边缘掉线**：SA 的 Shamir 阈值掉线容忍在仿真中天然成立（单进程不会掉线）；
   `hierarchy.edge_selection_rate < 1` 时参与边缘数减少，`l′` 按幸存数自适应。

## 4.1 PPBR 论文评测攻击与对比基线的实现状态

- **论文评测的 6 种攻击**：label flipping ✅（`data:label_flipping_random/targeted`）、
  sign flipping ✅（`model:sign_flipping`）、Gaussian ✅（`model:gaussian_noise`）、
  ALIE ✅（`model:alie`）、IPM ✅（`model:ipm`，本项目按论文公式补齐）、
  MinMax ✅（`model:min_max`）。
- **论文对比基线**：Median / TrimmedMean / GeoMed / Multi-Krum / **DnC** /
  **CenteredClipping** 已全部实现于 `ckks_fed/defenses/aggregation_rules.py`
  （后两者为本轮补齐）。注意它们与 PPBR 不同：PPBR 可在密文流程下工作
  （云只见范数、边缘只见符号元组），而这些基线需要明文坐标值，仅作明文对照。

## 4. 一轮训练中的调用位置（main.py `run_round_encrypted`）

```
本地训练 → 攻击注入（可选，hfl_attacks）
        → PPBR 算法2：符号统计 + CLDP → 边缘聚类过滤（剔除客户端）
        → PPBR 算法3：动量更新，上传动量替代原始更新
        → PPBR 算法4/5：云范数中位数筛选边缘 + 裁剪系数
        → 权重 w_i ← w_i · x″_i · ζ
        → 掩码投票 → CKKS 加密上传 → 边缘密文聚合 → 云加法合并
        → 客户端解密评估
```

fail-open：若云筛选把全部边缘筛掉（极端情况），本轮所有上传权重置 0，
全局模型不更新，训练继续。

## 5. 复现实验

```bash
# 无攻击 + PPBR（精度应与 FedAvg 基线基本一致）
# configs/config.yaml: ppbr.enabled: true
python main.py

# 攻击 × PPBR 防御
# configs/config.yaml: attack.enabled: true, attack.name: "model:sign_flipping"
#                      ppbr.enabled: true
python main.py

# 单元 + 端到端测试
python -m pytest tests/test_ppbr.py -v
```
