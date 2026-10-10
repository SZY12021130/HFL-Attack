# HFL-Attack

层次化联邦学习鲁棒性隐私保护研究框架：在 **CKKS 同态加密** 保护的联邦学习环境中，系统评估多种 **模型/数据投毒攻击** 与 **鲁棒聚合防御** 的攻防效果。

核心机制：

- **CKKS 同态加密**：客户端上传的模型更新全程以密文形式参与服务器聚合，服务器不接触明文梯度。
- **密钥分离架构**：服务器仅持有公钥上下文，只能加密下发与同态聚合，**不能解密任何密文**；私钥只分发给客户端，全局模型的解密与评估都在客户端完成（模拟环境中由实验编排方充当密钥生成中心 KGC）。
- **FedAvg 联邦聚合**：支持加权平均及多种鲁棒聚合规则。
- **TopK 稀疏化 + Global Mask Vote**：客户端本地按 batch 打分做 TopK，只上传 0/1 掩码；服务器聚合各客户端投票生成全局掩码并广播，客户端按全局掩码加密上传对应 batch，显著降低通信量。
- **PTopk 残差补偿**（Error Feedback）：客户端维护明文残差累加器，未被选中的 batch 信息累积到下一轮，避免稀疏化造成永久信息丢失。
- **攻击 × 防御实验矩阵**：全部实验仅运行于加密模式。

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
│   ├── client.py              # FederatedClient / MaliciousClient
│   ├── server.py              # AggregationServer (聚合 + 掩码投票 + 防御过滤)
│   ├── client_selection.py    # 客户端随机选择 (random)
│   ├── models.py              # MLP / CNN / LeNet 等模型定义
│   ├── data.py                # 数据集加载与客户端数据划分 (IID / Dirichlet 非IID)
│   ├── attacks/               # 攻击实现 (model_attacks 等)
│   ├── defenses/              # 鲁棒聚合规则实现
│   └── experiments/
│       └── attack_runner.py   # 攻击×防御实验矩阵运行器
├── configs/
│   ├── config.yaml            # 主实验配置 (main.py)
│   └── attack_config.yaml     # 攻击×防御实验配置 (main_robustness.py)
├── scripts/                   # 辅助脚本
├── tests/                     # 单元/集成测试
├── main.py                    # 主实验入口: 单次加密联邦学习训练
├── main_robustness.py         # 攻击实验入口: 攻击×防御矩阵
└── requirements.txt
```

## 快速开始

```bash
pip install -r requirements.txt

# 主实验: 单次联邦学习训练 (CKKS 加密 + TopK 稀疏化 + PTopk)
python main.py

# 攻击×防御实验矩阵 (仅加密模式)
python main_robustness.py

# 只跑某一种攻击的所有防御组合
python main_robustness.py --attack sign_flipping

# 只跑某一种防御的所有攻击组合
python main_robustness.py --defense median

# 攻击+防御组合, 只跑一个实验
python main_robustness.py --attack sign_flipping --defense fedavg

# 使用其他配置文件
python main_robustness.py --config path/to/config.yaml
```

数据集（mnist / fashion_mnist / cifar10 等）首次运行会自动下载到 `data/` 目录。实验结果输出到配置中 `output.results_dir` 指定的目录（默认 `./results`）。

## 实验矩阵

`configs/attack_config.yaml` 中 `attacks` 定义攻击场景库、`defenses` 定义防御配置库；运行器默认构建 **攻击 × 防御** 的全组合矩阵（含无攻击基线）。

支持的攻击：`sign_flipping`、`scaling`、`gaussian_noise`、`backdoor`、`mimic_alie`、`constant`（恶意客户端可选叠加数据投毒，如标签翻转/后门触发器）。

支持的防御聚合规则 / 异常过滤：

| 防御 | aggregation_rule | anomaly_filter |
|------|------------------|----------------|
| fedavg | fedavg | - |
| median | median | - |
| trimmed_mean | trimmed_mean | - |
| krum / multi_krum | krum / multi_krum | - |
| geometric_median | geometric_median | - |
| norm_clip_fedavg | fedavg | norm_clip |
| cosine_fedavg | fedavg | cosine_similarity |
| multi_criteria_fedavg | fedavg | multi_criteria |

若配置文件中存在 `experiments` 列表，则按该白名单精确运行指定组合；否则回退到全组合模式。

## 配置文件说明

### configs/config.yaml（主实验）

| 配置段 | 作用 |
|--------|------|
| `system` | 客户端数量、轮数、本地 epoch、batch size、学习率、设备、随机种子、客户端采样率等 |
| `data` | 数据集名称、存储目录、类别数、IID/非IID 划分及 Dirichlet 参数 |
| `model` | 模型结构（mlp/cnn/lenet 等）及超参数 |
| `ckks` | CKKS 加密参数（多项式模次数、系数模链、缩放因子）、packing 策略、TopK/RandK 稀疏化、PTopk 残差补偿开关 |
| `global_mask_vote` | 服务器端掩码聚合模式（none/union/threshold/topk）及阈值 |
| `bandwidth` | 带宽模拟参数（用于虚拟时钟通信时间估计） |
| `metrics` | 指标采集开关（时间、通信量、准确率等） |
| `visualization` | 可视化开关与图像格式 |
| `output` | 结果/模型/图表输出目录 |

### configs/attack_config.yaml（攻击实验）

| 配置段 | 作用 |
|--------|------|
| `system` / `data` / `model` / `ckks` | 同主配置，控制实验运行的联邦学习设置 |
| `experiment_modes` | 实验模式，当前仅支持 `encrypted` |
| `attacks` | 攻击场景库：恶意客户端数量/选择方式、数据投毒参数、模型攻击参数 |
| `defenses` | 防御配置库：聚合规则、异常过滤方法及参数 |
| `experiments` | （可选）显式实验白名单，精确指定要运行的 攻击×防御 组合 |
| `output` | 实验结果输出目录 |

## 测试

```bash
python -m pytest tests/
```

## 环境

- Python + PyTorch (CPU 即可运行，支持 CUDA)
- TenSEAL (CKKS 同态加密)
- 其余依赖见 `requirements.txt`
