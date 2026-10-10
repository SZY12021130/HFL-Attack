# HFL-Attack 攻击库说明文档

> 位置：`doc/attacks.md`  
> 本文汇总 `hfl_attacks/` 攻击包中四类 24 个攻击的来源、原理与代码结构，并说明评价指标与当前全量实验配置。

---

## 1. 项目结构

```
HFL-Attack/
├── hfl_attacks/
│   ├── __init__.py
│   ├── base.py                # 统一接口与注册表
│   ├── metrics.py             # 通用评价指标
│   ├── runner.py              # CKKS+FedAvg 评估框架
│   ├── data_poisoning/        # 数据投毒攻击
│   ├── model_poisoning/       # 模型投毒攻击
│   ├── system_attacks/        # 系统/协议层攻击
│   └── privacy_inference/     # 隐私推断攻击
└── doc/                       # 解释性文档
    └── attacks.md             # 本文档
```

### 1.1 统一接口（`hfl_attacks/base.py`）

所有攻击通过 `@register_attack` 装饰器注册到全局字典 `ATTACK_REGISTRY`，按能力等级实现四类接口之一：

| 接口 | 攻击类型 | 核心方法 | 说明 |
|------|----------|----------|------|
| `ClientUpdateAttack` | 模型投毒、部分系统攻击 | `attack(update, context)` | 篡改客户端明文更新后上传 |
| `DataPoisoner` | 数据投毒 | `poison_loader(loader, num_classes)` | 污染本地训练数据 |
| `PrivacyAttack` | 隐私推断 | `run(model, device, **kwargs)` | 从梯度/模型推断隐私 |
| `SystemAttack` | 系统攻击 | `attack(...)` + `participates(round)` | 额外控制客户端是否参与本轮 |

`AttackContext` 描述攻击者可见信息，支持三种威胁等级：

1. 仅知道自身更新；
2. 额外知道本轮所有更新（合谋攻击，如 ALIE、Min-Max、女巫）；
3. 额外知道明文全局模型（本项目中服务器不持有私钥，因此 `global_params` 通常为 `None`）。

### 1.2 与加密流程的位置关系

攻击发生在**客户端明文训练完成后、CKKS 加密上传之前**。这对应“客户端被攻破”或“客户端本身就是恶意参与者”的威胁模型。服务器仅持公钥，永远不会拿到明文梯度，因此密文层面的防御（如异常检测）不能直接应用。

---

## 2. 数据投毒攻击（5 个）

实现目录：`hfl_attacks/data_poisoning/`

### 2.1 Random Label Flipping — `data:label_flipping_random`

- **出处**：Tolpegin et al., *Data Poisoning Attacks Against Federated Learning Systems*, ESORICS 2020。
- **开源参考**：<https://github.com/vio1etus/FLPoison>
- **原理**：以一定比例随机把样本标签改成错误标签，破坏本地训练目标。
- **代码文件**：`label_flipping.py`
- **参数**：`poison_rate=1.0`

### 2.2 Targeted Label Flipping — `data:label_flipping_targeted`

- **出处**：同上，标签翻转的标准变体。
- **原理**：把某个源类别的样本全部/按比例翻向目标类别，造成定向误分类。
- **代码文件**：`targeted_label_flipping.py`
- **参数**：`source_class=None`（`None` 表示所有非目标类）、`target_class=0`、`poison_rate=1.0`

### 2.3 BadNets Patch Backdoor — `data:backdoor_badnets`

- **出处**：Gu et al., *BadNets: Identifying Vulnerabilities in the Machine Learning Model Supply Chain*, IEEE Access 2019。
- **开源参考**：<https://github.com/vio1etus/FLPoison>、<https://github.com/mtuann/fedlearn-backdoor-attacks>
- **原理**：在图像右下角粘贴一个固定像素块作为触发器；带触发器的样本标签被标为目标类。训练后，模型对正常样本表现正常，对带触发器的样本会预测为攻击者指定目标。
- **代码文件**：`backdoor_trigger.py`
- **参数**：`target_class=0`、`poison_rate=0.3`、`trigger_size=3`、`trigger_value=1.0`、`position="bottom_right"`

### 2.4 Blend Backdoor — `data:backdoor_blend`

- **出处**：Chen et al., *Targeted Backdoor Attacks on Deep Learning Systems Using Data Poisoning*, arXiv:1712.05526。
- **原理**：把固定随机模式以较小混合比例叠加到正常图像上，触发器更隐蔽。
- **代码文件**：`blend_trigger.py`
- **参数**：`target_class=0`、`poison_rate=0.1`、`blend_ratio=0.2`

### 2.5 Edge-Case Backdoor — `data:backdoor_edgecase`

- **出处**：Wang et al., *Attack of the Tails: Yes, You Really Can Backdoor Federated Learning*, USENIX Security 2020。
- **开源参考**：<https://github.com/vio1etus/FLPoison>
- **原理**：Clean-label 退化实现——在源类中挑选“边缘”样本（默认用像素 L2 范数最小的尾部样本），只改标签不改图像，使其被模型错误学到目标类的特征。
- **代码文件**：`edge_case.py`
- **参数**：`target_class=0`、`source_class=7`、`edge_frac=1.0`

---

## 3. 模型投毒攻击（9 个）

实现目录：`hfl_attacks/model_poisoning/`

### 3.1 经典/简单攻击（`classic_attacks.py`）

| 注册名 | 攻击名 | 出处/参考 | 原理 |
|--------|--------|-----------|------|
| `model:sign_flipping` | Sign Flipping | 联邦学习中常见的拜占庭攻击变体 | 将本地梯度/更新符号整体反转 |
| `model:scaling` | Scaling Attack | 经典模型投毒 | 将更新乘以一个放大系数 |
| `model:gaussian_noise` | Gaussian Noise | 拜占庭/故障模型参考 | 用高斯噪声覆盖真实更新 |
| `model:constant` | Constant Update | 简单拜占庭示例 | 用固定常数张量替换更新 |
| `model:backdoor_model` | Model-level Backdoor Adapter | 将后门目标注入模型参数 | 在本地更新中加入与触发器相关项 |
| `model:mimic_alie` | Mimic ALIE | ALIE 的简化模仿 | 沿诚实更新均值方向做小幅偏移 |

### 3.2 A Little Is Enough（ALIE）— `model:alie` / `model:mimic_alie`

- **出处**：Baruch et al., *A Little Is Enough: Circumventing Defenses For Distributed Learning*, NeurIPS 2019。
- **开源参考**：<https://github.com/vio1etus/FLPoison>
- **原理**：攻击者知道诚实更新的一阶/二阶统计量，构造一个“不起眼”的恶意更新，使其在均值、方差上与诚实更新不可区分，从而绕过基于统计的异常检测和坐标中值等防御。
- **代码文件**：`alie.py`
- **依赖**：需要合谋上下文 `context.all_updates`。

### 3.3 Min-Max / Min-Sum 规避攻击 — `model:min_max`、`model:min_sum`

- **出处**：Shejwalkar & Houmansadr, *Manipulating the Byzantine: Optimizing Model Poisoning Attacks and Defenses for Federated Learning*, USENIX Security 2021。
- **开源参考**：<https://github.com/vio1etus/FLPoison>
- **原理**：
  - **Min-Max**：在不被任何诚实更新检测为异常的前提下，最大化全局模型距离真实均值有多远。
  - **Min-Sum**：最小化所有诚实更新到恶意更新的总距离之和，使恶意更新“挤进”诚实簇的中心。
- **代码文件**：`optimization_attacks.py`
- **依赖**：需要合谋上下文 `context.all_updates`。

---

## 4. 系统/协议层攻击（6 个）

实现目录：`hfl_attacks/system_attacks/`

### 4.1 搭便车攻击（Free-Riding）— `hfl_attacks/system_attacks/free_riding.py`

- **出处**：Fraboni et al., *Free-rider Attacks on Model Aggregation in Federated Learning*, AISTATS 2021。
- **参考实现**：Accenture Labs 联邦学习代码库 <https://github.com/accenture/labs-federated-learning>
- **三种实现**：
  1. `system:free_riding_zeros`（δ0）：上传全零更新，不参与训练但享受全局模型。
  2. `system:free_riding_previous`（δ1）：上传上一轮全局模型。在本项目 `upload_difference=True` 语义下，上传完整权重与上传零差值对聚合结果等价。
  3. `system:free_riding_masked`（伪装版）：生成与诚实更新同 L2 范数的随机方向更新，躲避基于范数的检测。

### 4.2 女巫合谋攻击 — `system:sybil_collusion`

- **出处**：Douceur, *The Sybil Attack*, IPTPS 2002。
- **参考**：arXiv 2505.09983、<https://github.com/sydelp>、<https://github.com/ITSEG-MQ>
- **原理**：多个伪客户端（Sybil）上传一致的恶意更新。即使每个 Sybil 只投少量权重，多个 Sybil 的加权平均会放大恶意影响。实现中每个合谋者上传所有合谋者更新的均值；`boost_factor` 可进一步向恶意方向拉伸。
- **代码文件**：`sybil.py`

### 4.3 拒绝服务/掉线攻击 — `hfl_attacks/system_attacks/dos_dropout.py`

- **出处**：JMLR 2023 拜占庭/掉线综述、FreqFed (NDSS 2024)。
- **两种实现**：
  1. `system:random_dropout`：每轮以 `drop_rate` 概率不参与，模拟随机掉线。
  2. `system:persistent_dos`：按 `active_every` 周期性地只参与少数轮次，其余轮次不上传任何更新。
- **原理**：通过降低恶意/受损客户端的有效参与度，减缓或破坏全局收敛。

---

## 5. 隐私推断攻击（4 个）

实现目录：`hfl_attacks/privacy_inference/`

### 5.1 Deep Leakage from Gradients（DLG）— `privacy:dlg`

- **出处**：Zhu et al., *Deep Leakage from Gradients*, NeurIPS 2019。
- **开源参考**：<https://github.com/mit-han-lab/dlg>
- **原理**：给定一个 batch 的模型梯度，联合优化虚拟输入 `x*` 和虚拟标签 `y*`，使虚拟输入产生的梯度与观察到的真实梯度在 L2 距离上最小化。当梯度完全匹配时，`x*` 会泄露真实训练数据。
- **代码文件**：`dlg.py`
- **参数**：`iters=200`、`lr=0.1`、2 次重启取最优。

### 5.2 Improved DLG（iDLG）— `privacy:idlg`

- **出处**：Zhao et al., *iDLG: Improved Deep Leakage from Gradients*, arXiv 2020。
- **原理**：利用交叉熵损失下输出层 bias 梯度的符号信息直接解析真实标签（真实类对应唯一负梯度分量），从而把联合优化问题简化为仅优化输入，重建质量显著优于同时优化标签的 DLG。
- **代码文件**：`idlg.py`
- **说明**：均值梯度只能恢复标签集合；若使用逐样本梯度（如 DP 逐样本裁剪场景），可实现 100% 标签恢复。

### 5.3 Inverting Gradients — `privacy:inverting_gradients`

- **出处**：Geiping et al., *Inverting Gradients - How Easy Is It to Break Privacy in Federated Learning?*, NeurIPS 2020。
- **开源参考**：<https://github.com/JonasGeiping/invertinggradients>
- **原理**：用余弦相似度而非 L2 距离匹配梯度，并加入 TV 正则与值域裁剪，使重建图像更符合自然图像先验。实现中同时优化软标签，以适应不同 batch 场景。
- **代码文件**：`inverting_gradients.py`
- **参数**：`iters=300`、`lr=0.1`。

### 5.4 成员推断攻击 — `privacy:membership_inference`

- **出处**：Shokri et al., *Membership Inference Attacks Against Machine Learning Models*, S&P 2017。
- **开源参考**：<https://github.com/yonsei-sslab/MIA>
- **原理**：判断某样本是否被用于训练目标模型。本实现为轻量版，不训练影子模型，直接以模型在样本上的 loss / 最大置信度 / 熵作为成员分数，通过 AUC / 准确率 / 优势量化推断能力。
- **代码文件**：`membership_inference.py`
- **参数**：`score="loss"` / `"confidence"` / `"entropy"`。

---

## 6. 通用评价指标

实现文件：`hfl_attacks/metrics.py`

### 6.1 投毒 / 系统攻击指标

| 指标 | 含义 |
|------|------|
| `final_accuracy` | 攻击结束后全局模型在干净测试集上的最终准确率（%） |
| `best_accuracy` | 训练过程中的最高测试准确率（%） |
| `mean_accuracy` | 各轮测试准确率的平均值（%） |
| `accuracy_drop` | 相对无攻击基线的绝对准确率下降（百分点） |
| `relative_accuracy_drop` | 相对下降比例 `(clean - attacked) / clean` |
| `convergence_round` | 首次达到目标准确率的轮次（1-based），未达为 -1 |
| `asr` | 攻击成功率（Attack Success Rate），仅后门类攻击计算：带触发器样本被预测为目标类的比例 |
| `wasted_comm_ratio` | 无效通信占比，用于系统攻击 |
| `elapsed_sec` | 单次攻击评估耗时 |

### 6.2 隐私推断攻击指标

| 指标 | 含义 |
|------|------|
| `mse` | 重建图像与真实图像的均方误差（越低越好） |
| `psnr` | 峰值信噪比（dB，越高越好） |
| `cosine_similarity` | 重建梯度与真实梯度的余弦相似度 |
| `label_recovery_acc` | 真实标签被正确恢复的比例（%） |
| `membership_auc` | 成员推断 AUC（0.5 表示无推断能力） |
| `membership_acc` | 成员推断最佳阈值下的分类准确率 |
| `membership_advantage` | `acc - 0.5`，衡量推断优势 |

---

## 7. 当前全量实验配置

当前后台运行的命令：

```bash
python -m hfl_attacks.runner --rounds 5 --out results/attack_eval_full
```

### 7.1 命令行参数

| 参数 | 默认值 | 本次设置 |
|------|--------|----------|
| `--config` | `configs/config.yaml` | 默认 |
| `--dataset` | `mnist` | 默认 |
| `--clients` | `10` | 默认 |
| `--malicious` | `2` | 默认 |
| `--rounds` | `5` | `5` |
| `--device` | `cpu` | 默认 |
| `--attacks` | `None`（跑全部） | 默认 |
| `--skip-privacy` | `False` | 默认（不跳过，跑隐私攻击） |
| `--out` | `results/attack_eval_<时间戳>` | `results/attack_eval_full` |

### 7.2 由 runner 覆盖的配置项

`build_eval_config()` 会在读取 `configs/config.yaml` 后，用以下评估专用参数覆盖，以保证轻量、可复现：

| 配置项 | 覆盖值 | 说明 |
|--------|--------|------|
| `system.num_clients` | `10` | 评估使用 10 个客户端 |
| `system.num_rounds` | `5` | 每轮攻击评估跑 5 轮 |
| `system.device` | `cpu` | 评估使用 CPU |
| `system.client_sampling_rate` | `0.5` | 每轮随机选 50% 客户端 |
| `data.dataset` | `mnist` | MNIST 数据集 |
| `data.iid` | `True` | 数据按 IID 划分 |
| `ckks.poly_modulus_degree` | `4096` | 轻量 CKKS 参数 |
| `ckks.coeff_mod_bit_sizes` | `[40, 20, 40]` | 轻量模数链 |
| `ckks.global_scale` | `1048576` | 2^20 |

### 7.3 来自 `configs/config.yaml` 的固定配置

| 配置项 | 值 |
|--------|-----|
| 模型 | `lenet` |
| 本地 epoch | `1` |
| batch size | `64` |
| 优化器 | `adam`，lr `0.001` |
| 上传方式 | `upload_difference: true`（上传差值） |
| 稀疏化策略 | `topk`，稀疏率 `0.1`，评分 `l2_norm` |
| 残差补偿 | `ptopk.enable: true` |
| 全局 mask 投票 | `mode: topk`，`topk_pct: 0.1` |
| 低带宽过滤 | `false`（避免因基础带宽低导致无客户端可采样） |

### 7.4 恶意客户端设置

- 恶意客户端编号：`[8, 9]`（10 个客户端中最后 2 个）。
- 每轮从 10 个客户端中按固定种子随机选 5 个参与（`np.random.RandomState(1000+round)`）。
- 数据投毒攻击只毒化这 2 个恶意客户端的本地训练数据。
- 模型投毒/系统攻击只在被选中且为恶意的客户端上篡改更新；合谋攻击（ALIE、Min-Max、Min-Sum、女巫）可访问本轮全部被选中客户端的明文更新。

### 7.5 隐私推断评估设置

- 目标模型：无攻击基线训练 5 轮后的全局 `lenet`。
- 受害者数据：客户端 0 的 1 个 batch（64 张 MNIST）。
- DLG / iDLG / Inverting Gradients：基于该 batch 的真实梯度重建图像，计算 MSE、PSNR、标签恢复准确率、梯度余弦相似度。
- 成员推断：从客户端 0 中取 4 个 batch 作为成员，从测试集中取 4 个 batch 作为非成员，分别用 loss / confidence / entropy 三种分数计算 AUC、准确率、优势。

---

## 8. 如何复现/扩展

### 8.1 跑单个攻击

```bash
python -m hfl_attacks.runner --rounds 5 --attacks model:sign_flipping --skip-privacy
```

### 8.2 跑全部攻击（含隐私）

```bash
python -m hfl_attacks.runner --rounds 5 --out results/attack_eval_full
```

### 8.3 添加新攻击

1. 在对应子目录新建 Python 文件；
2. 继承 `ClientUpdateAttack` / `DataPoisoner` / `PrivacyAttack` / `SystemAttack`；
3. 设置 `name` 属性并给类加 `@register_attack`；
4. 在子包 `__init__.py` 中 import 该模块；
5. `runner.py` 会自动发现并评估。

---

## 9. 常见问题与原理详解（结合梯度示例）

### 9.1 攻击到底发生在哪一层？是在明文梯度上吗？

**是的**。本框架中客户端上传的是模型更新（梯度或参数差值），攻击发生在**客户端本地、加密上传之前**。具体流程：

```
客户端本地训练  →  得到明文更新 ΔW  →  攻击者篡改 ΔW  →  CKKS 加密  →  TopK/打包/稀疏化  →  上传服务器
```

也就是说：

- 数据投毒：在训练前改数据，导致算出来的梯度本身就已经是“坏梯度”。
- 模型投毒/系统攻击：直接拿到明文更新 `ΔW` 后做修改。
- 隐私推断：攻击者想办法从能拿到的明文信息（如某个 batch 的梯度）反推隐私。

服务器收到的是**密文**，而且服务器不持有私钥，没法解密看梯度。

### 9.2 一个具体梯度例子

假设模型只有一层：`y = w·x + b`，当前全局参数为 `w=0.5, b=0.1`。某个诚实客户端在一个 batch 上训练后得到更新：

```text
Δw = -0.10    (梯度方向：让 w 变大)
Δb = +0.05
```

下面看不同攻击会对这个更新做什么：

#### 数据投毒（以标签翻转为例子）

数据没变，但标签被改了。客户端用错误标签算损失、反向传播，得到的梯度变成：

```text
Δw' = +0.08    (方向可能相反或混乱)
Δb' = -0.03
```

这个“毒梯度”再按正常流程加密上传。服务器聚合时会把一部分毒梯度混进去。

#### 模型投毒（直接改更新）

客户端可能正常训练，得到 `Δw=-0.10, Δb=+0.05`，但攻击者把它改成：

- **Sign flipping**：`Δw'=+0.10, Δb'=-0.05`（符号全反，拉着全局模型往反方向走）。
- **Scaling**（放大 10 倍）：`Δw'=-1.0, Δb'=+0.5`（恶意客户端权重被放大后主导聚合）。
- **Gaussian noise**：`Δw'=-0.10+ε1, Δb'=+0.05+ε2`（用噪声覆盖真实信号）。
- **Constant**：`Δw'=0.3, Δb'=0.3`（直接替换为固定常数）。
- **ALIE / Min-Max / Min-Sum**：需要看到其他客户端更新。假设本轮还有 4 个诚实客户端，更新分别是 `(-0.12, +0.04)`、`(-0.09, +0.06)`、`(-0.11, +0.05)`、`(-0.10, +0.05)`，诚实均值约 `(-0.105, +0.05)`。ALIE 会构造一个看起来“几乎像诚实更新”的恶意更新，比如 `(-0.115, +0.048)`，让基于统计的异常检测难以发现。

#### 系统攻击

- **Free-riding zeros（δ0）**：直接上传 `Δw'=0, Δb'=0`，不贡献任何信息但白嫖全局模型。
- **Free-riding previous（δ1）**：上传上一轮全局模型本身（在差值上传语义下等价于零更新）。
- **Free-riding masked**：生成随机方向但范数与诚实更新相当，例如 `Δw'=+0.07, Δb'=+0.07`，让服务器觉得它训练了。
- **Sybil collusion**：如果有 2 个恶意客户端，它们各自上传彼此更新的均值，放大恶意权重。
- **Random dropout**：本轮以 50% 概率直接不上传；本轮若被选中也不发送任何更新。
- **Persistent DoS**：每隔几轮才参与一次，其余轮次离线。

### 9.3 隐私推断攻击的评价指标是什么意思？

隐私推断攻击不是“判断某样本是否在训练集中”这一件事，它包含两大类：

#### ① 数据/梯度重建攻击（DLG / iDLG / Inverting Gradients）

**目标**：攻击者观测到一个 batch 的梯度，试图把该 batch 的原始图像/标签重建出来。

| 指标 | 含义 | 例子 |
|------|------|------|
| **MSE** | 重建图像与真实图像逐像素差的平方均值 | MSE=0.88 表示重建图和原图像素级差距很大 |
| **PSNR** | 峰值信噪比，越高越接近原图 | PSNR≈0.5 dB 表示重建质量很差（>20 dB 才比较清晰） |
| **标签恢复准确率** | 恢复出的标签中有多少和真实标签一致 | 25% 表示只猜对 1/4 |
| **梯度余弦相似度** | 重建样本产生的梯度与真实梯度的方向接近程度 | 0.92 表示方向很接近，但像素位置可能仍差很远 |

为什么 MSE/PSNR 差，不代表攻击没意义？因为梯度匹配重建本身就有歧义：同样的 batch 均值梯度可能对应多种像素排列。这些指标衡量的是“重建图像和真实图像有多像”，而不是“攻击者有没有得到敏感信息”。

#### ② 成员推断攻击（Membership Inference）

**目标**：给定一个样本和训练好的模型，判断这个样本是否被用于训练该模型。

| 指标 | 含义 |
|------|------|
| **AUC** | 成员分数高于非成员分数的概率，0.5 表示随机猜 |
| **成员准确率** | 用最佳阈值区分成员/非成员的准确率 |
| **成员优势** | `准确率 - 0.5`，衡量超出随机猜测的优势 |

本次实验中 AUC≈0.5005、准确率≈0.5117，优势≈0.01，基本等同于随机猜测。原因：只训练了 5 轮，模型对训练样本还没有形成明显过拟合，成员与非成员的 loss/置信度分布高度重叠。

### 9.4 实验架构：一个服务器 + 多个客户端？

是的，**经典的参数服务器架构**：

```
                    服务器 (Parameter Server)
                   / 只保存公钥上下文，不能解密  \
                  /                                \
        客户端 0   客户端 1   ...   客户端 9
        (可能恶意) (诚实)            (可能恶意)
```

- 服务器负责：采样客户端、接收密文更新、同态聚合、广播密文全局模型、记录指标。
- 客户端负责：本地训练、（被攻击时）产生恶意更新、加密上传、解密全局模型、本地评估。
- 本次实验：10 个客户端，其中 2 个是恶意客户端（编号 8、9）。每轮按固定种子随机选 5 个客户端参与。

### 9.6 加密和稀疏化到底保护了什么？

- **CKKS 加密**：保护传输中的更新，使被动服务器/窃听者无法直接读取明文梯度。
- **TopK 稀疏化 + global_mask_vote**：每个客户端只上传最重要的部分梯度（mask），并用全局投票决定哪些位置需要加密上传，减少通信量，同时服务器仍能在密文上做加权平均。

但加密**不能防止客户端作恶**。如果客户端本身就是恶意或被攻破的，它在加密前产生的梯度/更新已经是毒的；服务器即使拿到密文，也无法区分毒更新和诚实更新（除非引入额外的可信审计或密文下异常检测，这正是层次化防御要研究的问题）。

### 9.5 TopK 稀疏化、打包与补齐的完整流程

本小节专门回答：明文更新从产生到加密上传，到底经历了哪几步？

> **当前 `runner.py` 已启用两阶段 `global_mask_vote`**（2025-10 更新）：每个客户端先在本地算 TopK mask，服务器聚合出全局 mask 后广播，客户端按全局 mask 加密上传。下文按这个实际流程说明；如果把 `configs/config.yaml` 中 `global_mask_vote.mode` 改为 `none`，则退回到"每个客户端独立 TopK、服务器并集聚合"的旧路径。

以 LeNet、MNIST、CKKS `N=4096` 为例：

1. **扁平化（Flatten）**

   客户端训练得到更新 `ΔW`，它是一个参数字典（`conv1.weight`、`conv1.bias`、`fc.weight`、`fc.bias` 等）。先把所有参数按固定顺序展平并拼接成一条长向量（LeNet 约 4.4 万个参数）：

   ```text
   [v0, v1, v2, ..., v44k]
   ```

2. **切分为 batch（按 slot 填充）**

   CKKS 的 SIMD slot 数量 = `N / 2 = 2048`。把长向量每 2048 个元素切成一个 batch：

   ```text
   batch 0:  [v0, v1, ..., v2047]          (2048 个)
   batch 1:  [v2048, ..., v4095]           (2048 个)
   ...
   batch 21: [v43008, ..., v44k, 0, ..., 0]  (不足 2048，末尾补零)
   ```

   这样共约 22 个 batch，最后一个 batch 用 `0` 补齐到 2048。

3. **客户端本地 TopK：为投票计算 mask（不传明文梯度）**

   对每个 batch 计算评分。当前配置 `scoring_method="l2_norm"`，即：

   ```text
   score(batch_i) = ||batch_i||_2 = sqrt(sum(x_j^2))
   ```

   然后按分数从高到低排序，保留前 `sparsity_rate=10%` 的 batch。22 个 batch 中大约保留 2~3 个。客户端只把选中的索引编码成 0/1 mask（长度 22），上传给服务器。

4. **服务器聚合 mask，生成全局 mask 并广播**

   服务器收集所有参与客户端的 mask，按 `global_mask_vote.mode` 聚合：

   - `union`：任一客户端选中即纳入；
   - `threshold`：超过 `threshold_pct` 比例客户端选中才纳入；
   - `topk`（当前配置）：按选中次数排序，取前 `topk_pct × total_batches` 个 batch（22 × 10% ≈ 2~3 个）。

   聚合结果（全局 mask）广播给所有客户端。

5. **客户端按全局 mask 加密上传**

   每个客户端只加密全局 mask 中值为 1 的那几个 batch。每个 batch 编码成一个 CKKS 密文（2048 个 slot）。上传内容与 mask 对齐：

   ```text
   上传内容：
     - ciphertexts: [ct_0, ct_1, ct_2]   (2~3 个密文，对应全局选中的 batch)
     - mask:        全局 mask 的副本（0/1 向量，与服务器端对齐）
   ```

6. **服务器稀疏聚合**

   服务器拿到多个客户端的稀疏更新后，对每个 batch 索引分别处理：

   - 如果某个 batch 有 k 个客户端上传，就把这 k 个密文按权重做同态加权平均。
   - 如果某个 batch 没人上传，就当作 0 处理。

   因为所有客户端都按同一全局 mask 上传，mask 天然对齐，聚合更简单。

7. **解密与恢复形状**

   客户端解密聚合后的密文，按 `shapes` / `offsets` 把扁平向量重新 reshape 回 `conv1.weight`、`fc.bias` 等参数形状，并丢弃补齐的零。

#### 如果关闭 global_mask_vote（mode=none）

流程退化为：

1. 每个客户端独立做 TopK，各自选出自己的 top 2~3 个 batch；
2. 各自加密上传自己的 mask 和密文；
3. 服务器聚合时取所有 mask 的**并集**，对每个 batch 分别加权平均。

这种模式下不同客户端 mask 不对齐，聚合更复杂，但保留了每个客户端自己的稀疏选择。

#### 打包策略 `merged` 什么时候生效？

`packing.strategy=merged` 的完整实现位于 `ckks_fed/packing.py`，它会把所有层先 concat 成一个长数组，再统一分块。在当前 runner（已启用 `global_mask_vote`）中，`encrypt_model_update` 走 `global_mask` 路径，且 packing 策略非 layerwise，因此会调用 `MergedPacking.pack()`：先 concat 再按 `slot_count` 分块，保证投票 mask 的边界与加密 chunk 边界一致。

---

## 10. 安全与威胁模型说明

- **服务器不持有私钥**：`configs/config.yaml` 配合 `runner.py` 的密钥分离实现保证服务器只保存公钥上下文；明文更新只在客户端内存中出现。
- **攻击位置**：所有攻击作用于客户端明文空间，因此对应“客户端被攻破”或“恶意参与者”模型。如果攻击需要看到其他客户端更新（合谋攻击），则通过 `AttackContext.all_updates` 提供。
- **隐私攻击的假设**：CKKS 加密下被动服务器无法直接获取单个客户端梯度。隐私攻击评估假设攻击者处于“更新可见”的边界位置（如合谋参与者、被攻破的边缘聚合节点），用于量化一旦梯度泄漏后的隐私风险。
