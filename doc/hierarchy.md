# 层次联邦学习架构（云-边-端）

> 本文档说明 HFL-Attack 的层次化架构：`configs/config.yaml` 中 `hierarchy.enabled: true` 即启用。
> 参考文献见 `paperlist/README.md`（单密钥 HE 云边端论文、PPBR、HierFL、UAV-HFL 等）。

---

## 1. 架构总览

启用层次模式后，标准"客户端 ↔ 服务器"两方架构变为三层：

```
                        云服务器 (Cloud / Aggregation Server)
                        只持公钥：密文全局聚合、密文全局模型下发
                       /            |             \
              边缘节点 0          边缘节点 1  ...   边缘节点 E-1
              (UAV/边缘服务器)     只持公钥：下辖客户端密文部分聚合
             /      \            /      \
        客户端...   客户端...  客户端...  客户端...
        持完整密钥(含私钥)：训练、加密上传、解密评估
```

**一轮的完整流程（上行 + 下行）**：

```
上行：客户端训练 → 本地 TopK mask → mask 经边缘部分汇总 → 云合并生成全局 mask
      → 全局 mask 经边缘下发 → 客户端按全局 mask 加密更新
      → 客户端密文 → 边缘密文聚合(加权和不归一化) → 边缘密文 → 云密文聚合(纯加法)
下行：云密文全局模型 → 边缘转发 → 客户端解密、评估、上报
```

## 2. 关键设计

### 2.1 密钥边界

| 节点 | 持有密钥 | 能做什么 |
|---|---|---|
| 客户端 | 完整上下文（含私钥） | 训练、加密、**解密全局模型**、评估 |
| 边缘节点 | 仅公钥上下文（从服务器公钥字节反序列化） | 同态加法、密文×标量（加权）、mask 汇总、**不能解密** |
| 云服务器 | 仅公钥上下文 | 同态聚合、加密下发全局模型、**不能解密** |

安全自检：`EdgeNode.has_secret_key()` 返回 False；测试中边缘解密必须抛异常。

### 2.2 两级权重衔接（数学严格等价于扁平 FedAvg）

设边缘 e 下辖客户端集合 C_e，权重 w_i，全局总权重 W = Σw_i。

1. 边缘侧做**加权和不归一化**：`ct_e = Σ_{i∈C_e} (w_i/W) · ct_i`（一次密文×标量）
2. 云侧**纯加法合并**：`ct = Σ_e ct_e`（仅密文加法）

结果 `ct = Σ_i (w_i/W) · ct_i`，与扁平单服务器聚合**严格一致**（测试
`test_hierarchical_aggregation_math_equivalence` 验证解密后数值相等）。

### 2.3 CKKS 乘法深度控制（重要）

CKKS 密文×标量会消耗乘法层级。若边缘和云各自做归一化乘法（方案 B 两次），
scale 从 2^20 → 2^40 → 2^60，超出 `[40,20,40]` 模数链容量
（`scale out of bounds`）。因此层次路径采用"**边缘一次乘法 + 云零次乘法**"：

- 边缘：`weighted_average(..., normalize=False)`，按 α_i = w_i/W 预缩放后求和；
- 云：权重全 1，`weighted_average` 自动走纯加法快速路径（`ckks_fed/crypto.py`）。

GMV（全局掩码投票）模式下所有选中客户端按同一全局 mask 上传，每个 batch 的
贡献者集合相同，预缩放全局归一化严格正确。

### 2.4 层次化掩码投票

客户端本地 TopK mask（0/1 向量，不含明文梯度）→ 各边缘把下辖客户端 mask
求和为**部分计数向量** → 云把各边缘计数相加，用与扁平投票完全相同的
规则（`GlobalMaskVote.mask_from_counts`，支持 union/threshold/topk）生成全局
mask → 沿云→边→端下发。

因为计数可加法分解，层次投票与扁平投票**数学等价**
（测试 `test_hierarchical_mask_vote_equivalence` 对三种模式逐一验证）。

### 2.5 客户端-边缘关联策略（可插拔）

`hierarchy.assignment`：

- **`static`**：固定分片，边缘 e 负责连续一段客户端 `[e·n/E, (e+1)·n/E)`；
- **`uav`**：模拟 UAV-assisted FL。UAV 按随机路点（random waypoint）在
  `area_size²` 区域内移动（速度 `speed` 米/轮），客户端位置固定，
  每轮把客户端分配给**最近的 UAV**；`coverage_radius` 内视为被覆盖，
  `strict_coverage: true` 时半径外客户端本轮不参与，否则回退到最近 UAV。
  客户端-边缘关联随轮次动态变化。

`edge_selection_rate < 1` 时每轮只选部分边缘参与（其客户端本轮不训练）。

## 3. 配置说明

```yaml
hierarchy:
  enabled: false                # false = 标准单服务器架构（行为与旧版完全一致）
  num_edges: 3                  # 边缘节点/UAV 数量
  assignment: "static"          # static | uav
  edge_selection_rate: 1.0      # 每轮参与训练的边缘比例
  uav:
    area_size: 1000.0           # 仿真区域边长（米）
    speed: 30.0                 # UAV 每轮移动速度（米/轮）
    coverage_radius: 400.0      # 覆盖半径（米）
    strict_coverage: false      # true=半径外客户端本轮不参与
    seed: 42
```

全部参数可插拔：`enabled: false` 时代码路径与原版逐字节一致
（`main.py` 中 `self.hierarchy is None` 走原分支）。

## 4. 代码结构

```
ckks_fed/hierarchy.py
├── EdgeNode                  # 边缘节点：公钥上下文、mask 部分计数、密文部分聚合
├── StaticAssignment          # 固定分片关联
├── UAVAssignment             # UAV 随机路点移动 + 最近关联
└── HierarchyController       # 编排器：选边/分组/层次投票/边缘聚合

ckks_fed/global_mask_vote.py
└── GlobalMaskVote.mask_from_counts()   # 计数→全局 mask（扁平/层次通用）

ckks_fed/crypto.py
├── CKKSManager.aggregate_encrypted_updates(..., normalize=False)
└── CKKSEvaluator.weighted_average()    # 全 1 权重纯加法快速路径

main.py
├── _init_components(): HierarchyController 初始化（enabled 时）
└── run_round_encrypted(): 层次分支（层次投票 + 边缘聚合 + 云纯加法合并）
```

## 5. 测试

```bash
python -m pytest tests/test_hierarchy.py -v
```

| 测试 | 验证内容 |
|---|---|
| `test_static_assignment_*` | 固定分片完整覆盖、均衡 |
| `test_uav_assignment_*` | UAV 关联合法、随轮变化、严格覆盖模式 |
| `test_edge_node_has_no_secret_key` | 边缘无私钥、解密抛异常 |
| `test_hierarchical_mask_vote_equivalence` | 三种模式下层次投票 == 扁平投票 |
| `test_hierarchical_aggregation_math_equivalence` | 两级密文聚合 == 扁平聚合（CKKS 端到端） |
| `test_hierarchy_end_to_end_one_round` | 开启 hierarchy 跑一轮完整主流程 |

## 6. 当前限制与后续方向

1. **攻击评估 runner（`hfl_attacks/runner.py`）尚未接入层次路径**——攻击实验
   目前仍走标准单服务器架构；接入点与 `main.py` 相同（层次投票 + `aggregate_edges`）。
2. 通信开销统计沿用服务器侧口径（边缘→云段）；客户端→边缘段尚未单独记账。
3. 后续可引入（见 paperlist）：PPBR 双层抗拜占庭过滤、单密钥论文的哈希校验
   （防云偷解密）、UAV-HFL 无偏加权（关联非均匀时）。
4. 真实网络传输可参考 fed2tier 的 gRPC 骨架，把密文字节流跑在真实通道上。
