# HFL-Attack

层次化联邦学习鲁棒性隐私保护研究框架：在 **CKKS 同态加密** 保护的联邦学习环境中，系统评估 **数据投毒 / 模型投毒 / 系统攻击 / 隐私推断** 四类共 24 种攻击的效果。

核心机制：

- **CKKS 同态加密**：客户端上传的模型更新全程以密文形式参与服务器聚合，服务器不接触明文梯度。
- **密钥分离架构**：服务器仅持有公钥上下文，只能加密下发与同态聚合，**不能解密任何密文**；私钥只分发给客户端，全局模型的解密与评估都在客户端完成（模拟环境中由实验编排方充当密钥生成中心 KGC）。
- **FedAvg 联邦聚合**：支持加权平均及多种鲁棒聚合规则。
- **TopK 稀疏化 + Global Mask Vote**：客户端本地按 batch 打分做 TopK，只上传 0/1 掩码；服务器聚合各客户端投票生成全局掩码并广播，客户端按全局掩码加密上传对应 batch，显著降低通信量。
- **PTopk 残差补偿**（Error Feedback）：客户端维护明文残差累加器，未被选中的 batch 信息累积到下一轮，避免稀疏化造成永久信息丢失。
- **层次联邦学习（云-边-端）**：可选开启边缘层（UAV 场景），客户端密文 → 边缘密文聚合 → 云密文聚合，密文全局模型沿云→边→端逐级下发；边缘节点只持公钥，不能解密。
- **统一攻击框架**（`hfl_attacks/`）：四类攻击注册到统一注册表，由主配置 `attack` 段一键开关，攻击发生在客户端本地明文域、加密上传之前。

> **已知限制**：密文域只能执行同态加权平均（FedAvg），median/krum/trimmed_mean 等
> 需要明文坐标值的鲁棒聚合规则在纯密文服务器上无法执行——这是密钥分离架构的
> 固有约束，也是层次化方案（如边缘节点可信聚合 / 阈值解密）要解决的问题。

## 目录结构

```
HFL-Attack/
├── ckks_fed/                  # 核心包
│   ├── crypto.py              # CKKS 上下文/加密/解密/同态聚合 (CKKSManager)
│   ├── packing.py             # 打包策略: layerwise / merged
│   ├── sparsification.py      # 客户端本地 TopK/RandK 稀疏化
│   ├── global_mask_vote.py    # 服务器端全局掩码投票
│   ├── ptopk.py               # Packed TopK 残差补偿 (Error Feedback)
│   ├── client.py              # FederatedClient
│   ├── server.py              # AggregationServer (聚合 + 掩码投票 + 防御过滤)
│   ├── hierarchy.py           # 层次联邦: EdgeNode / UAV 关联 / 云-边-端密文聚合
│   ├── client_selection.py    # 客户端随机选择 (random)
│   ├── models.py              # MLP / CNN / LeNet 等模型定义
│   ├── data.py                # 数据集加载与客户端数据划分 (IID / Dirichlet 非IID)
│   ├── attacks/               # 模型投毒后端类 (供 hfl_attacks 适配)
│   └── defenses/              # 鲁棒聚合规则实现 (仅明文聚合路径生效)
├── hfl_attacks/               # 统一攻击库 (4 类 24 种, 注册表驱动)
│   ├── base.py                # 攻击接口 + ATTACK_REGISTRY
│   ├── metrics.py             # 攻击评价指标 (ASR / PSNR / MIA-AUC 等)
│   ├── runner.py              # 攻击离线评估 CLI
│   ├── data_poisoning/        # 数据投毒 ×5
│   ├── model_poisoning/       # 模型投毒 ×9
│   ├── system_attacks/        # 系统攻击 ×6
│   └── privacy_inference/     # 隐私推断 ×4
├── configs/
│   └── config.yaml            # 唯一主配置 (训练 + 攻击开关 + 层次架构)
├── scripts/                   # 辅助脚本
├── tests/                     # 单元/集成测试
├── doc/                       # 解释性文档 (攻击原理 / 层次架构等)
├── paperlist/                 # 参考论文列表
├── main.py                    # 主实验入口: 单次加密联邦学习训练
└── requirements.txt
```

## 快速开始

```bash
pip install -r requirements.txt

# 主实验: 单次联邦学习训练 (CKKS 加密 + TopK 稀疏化 + PTopk)
python main.py

# 开启攻击: 编辑 configs/config.yaml 的 attack 段
#   attack:
#     enabled: true
#     name: "model:sign_flipping"   # 注册表攻击名
#     malicious_count: 2
python main.py

# 隐私推断类攻击的离线评估 (成员推断 AUC / 梯度重建 PSNR 等)
python -m hfl_attacks.runner --help

# 使用其他配置文件
python main.py --config path/to/config.yaml
```

数据集（mnist / fashion_mnist / cifar10 等）首次运行会自动下载到 `data/` 目录。实验结果输出到配置中 `output.results_dir` 指定的目录（默认 `./results`）。

## 攻击配置

攻击在主配置 `configs/config.yaml` 的 `attack` 段统一控制（`enabled: false` 时代码路径与正常 FedAvg 完全一致）：

| 配置项 | 作用 |
|--------|------|
| `attack.enabled` | 攻击总开关：`false`=正常 FedAvg；`true`=注入攻击实验 |
| `attack.name` | 攻击注册名，前缀决定类别：`data:` / `model:` / `system:` / `privacy:` |
| `attack.malicious_count` | 恶意客户端数量（取编号最大的最后 N 个） |

攻击实现库 `hfl_attacks/` 与配置的关系：

- **`hfl_attacks/` = 攻击实现库**：24 个攻击类实现统一接口（`DataPoisoner` / `ClientUpdateAttack` / `SystemAttack` / `PrivacyAttack`），通过 `@register_attack` 注册到 `ATTACK_REGISTRY`；`main.py` 按 `attack.name` 实例化并注入训练循环（数据投毒替换恶意客户端 `train_loader`；模型/系统投毒在客户端本地明文域篡改更新；DoS 类按轮次剔除不上传的客户端；隐私推断不修改训练过程，指标用 `hfl_attacks/runner.py` 单独评估）。
- **防御**（`ckks_fed/defenses/`）：median / krum / trimmed_mean 等明文域聚合规则与异常检测。它们需要明文坐标值，**在当前 CKKS 密文聚合路径下不生效**（密文不可解密），仅保留作明文对照与未来在边缘可信节点上落地的候选。

| 类别 | 攻击 |
|------|------|
| 数据投毒 `data:` | label_flipping_random / label_flipping_targeted / backdoor_badnets / backdoor_blend / backdoor_edgecase |
| 模型投毒 `model:` | sign_flipping / scaling / gaussian_noise / backdoor_model / constant / mimic_alie / alie / min_max / min_sum |
| 系统攻击 `system:` | random_dropout / persistent_dos / free_riding_zeros / free_riding_previous / free_riding_masked / sybil_collusion |
| 隐私推断 `privacy:` | dlg / idlg / inverting_gradients / membership_inference |

## 配置文件说明

### configs/config.yaml（唯一主配置）

| 配置段 | 作用 |
|--------|------|
| `system` | 客户端数量、轮数、本地 epoch、batch size、学习率、设备、随机种子、客户端采样率等 |
| `data` | 数据集名称、存储目录、类别数、IID/非IID 划分及 Dirichlet 参数 |
| `model` | 模型结构（mlp/cnn/lenet 等）及超参数 |
| `ckks` | CKKS 加密参数（多项式模次数、系数模链、缩放因子）、packing 策略、TopK/RandK 稀疏化、PTopk 残差补偿开关 |
| `global_mask_vote` | 服务器端掩码聚合模式（none/union/threshold/topk）及阈值 |
| `hierarchy` | 层次联邦开关：边缘节点数量、客户端-边缘关联策略（static/uav）、边缘采样率、UAV 移动模型 |
| `attack` | 攻击总开关、攻击注册名、恶意客户端数量 |
| `bandwidth` | 带宽模拟参数（用于虚拟时钟通信时间估计） |
| `metrics` | 指标采集开关（时间、通信量、准确率等） |
| `visualization` | 可视化开关与图像格式 |
| `output` | 结果/模型/图表输出目录 |

## 测试

```bash
python -m pytest tests/
```

## 环境

- Python + PyTorch (CPU 即可运行，支持 CUDA)
- TenSEAL (CKKS 同态加密)
- 其余依赖见 `requirements.txt`
