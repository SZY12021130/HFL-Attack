# HE+FL 演进链调研与可实践方案（基于 2026-10 GitHub 实况）

> 调研方法：4 路并行检索 GitHub/arXiv（MKHE-FL、门限 HE-FL、SA 混合、HFL/UAV-FL），
> 只收录有代码或明确出处的工作。stars/活跃度为调研时点实测。

## 一、演进链推理（你说的那条链，延伸之后）

```
MK-CKKS(Chen'19)
  └─ 缺陷1：部分解密泄露单项明文——服务器持有 (c0, μ_i=c1·s_i) 即 c0+μ_i≈m_i 一行式还原
       └─ xMK-CKKS(Ma'21)：只把 MK 适配到 FL，没修缺陷1
            └─ SMHE(arXiv:2506.20101, 2025, 有C++代码)：masked expansion 修缺陷1，但无 FL 工程
            └─ CDKS-LSS(Wang'26)：LSS 保护 μ_i 传输 + t-out-of-n，但引入客户端间份额分发轮
                 └─ ESAFL(TDSC'25)：共享解密密钥 s=Σs_i 结构，一步解密、免份额分发，
                      但回到"客户端互信 + 初始化 TTP + 无掉线容忍"
  └─ 缺陷2：部分解密需全员在线，掉线即失败（MK 密文引用全部 N 个公钥）
       └─ Hosseini MPHE+SS(arXiv:2503.00581, 2025)：setup 期 Shamir，理论补丁，无代码
       └─ Hyb-Agg(arXiv:2511.23252, 2025)：MK-CKKS+ECDH 掩码 one-shot，绕开 HE 解密，无代码
  └─ 缺陷3：开销不可部署
       └─ KKLSS(CCS'23, Go 实现)：渐近更快的 gadget 分解
       └─ MASER(BigData'25, 有代码)：共识剪枝+slicing，降 3-8 倍——但建立在有漏洞的 CDKS 流程上
门限路线：BatchCrypt(单密钥+TTP) → 门限CKKS(OpenFHE/Lattigo, C++/Go, Python 空白)
       → DKG 免 TTP(ePrint 2025/2057 稀疏密钥 DKG, 无代码) → 一轮式 SA(Willow/OPA/TACITA CRYPTO'25, 无代码)
可验证路线：SecAgg'17 → LightVeriFL'24 → V-MHESA'25 —— 全部基于精确算术，与 CKKS 近似性不兼容
层次/UAV 路线：纯通信优化为主，密码学介入 ≈ 0，开源空白
```

**调研给出的三个硬空白（多方独立确认）**：

1. **"CKKS 加权平均 + t-of-n 门限解密 + 客户端掉线容忍"没有可用开源方案**——
   MK-CKKS 系（MASER/Hyb-Agg）要求大多数参与方返回部分解密；门限库（OpenFHE/Lattigo）
   假设参与方集合静态；SA 系（LightSecAgg/Willow）不加密。三者交集为空。
2. **"多密钥 → 单密钥"转换构造无 FL 落地实现**——把异密钥密文一次性转到公共密钥
   （从而复用全部单密钥+门限基建、免除份额交换）只有零星理论提及。
3. **安全的 MK-FL 全栈不存在**——SMHE 有安全无工程，MASER 有工程但流程可被
   SMHE 的一行式攻击打穿；没有人把 masked MK-CKKS + 掉线鲁棒 + 效率优化整合成一个系统。

辅助事实：Python+TenSEAL 生态没有任何 MK/门限 CKKS；唯一 Python 可调 MK-CKKS 是
xMKCKKS_Go2Py（Go cgo → ctypes，接口：公钥聚合/部分解密/份额聚合）；OpenFHE 有
门限 CKKS 的 Python 绑定（MultipartyKeyGen/threshold-fhe.py 示例）；FedPHE（44★）
是单密钥 CKKS 打包稀疏的工程标杆；SamsungLabs/mlsys26_disagg 是最新 SA 基线集。

## 二、五个可实践方案（按"工程可行性 × 论文价值"排序）

每个方案给出：对应的空白点、核心机制一句话、在我们仓库里的实现路径、工作量预估。

### 方案 A：边缘代理门限解密——掉线鲁棒的层次 MK-FL ★首推

- **空白点**：#1（掉线容忍的门限 CKKS 无实现）+ 层次/UAV 空白带。
- **一句话机制**：MK-CKKS 聚合密文引用簇内全部成员密钥（分量 c_i 对应 s_i）；客户端
  不自己做部分解密，而是把 μ_i = c_i·s_i 用 Shamir 拆成 (t_e, n_e) 份额发给簇内；
  边缘节点收集 ≥t_e 份额重构各 μ_i，汇总 μ=Σμ_i 上云，云 + c0 得聚合明文。
  客户端掉线无碍——只要簇内任意 t_e 方（含边缘自身）存活。**MK-CKKS 的"全员在线"
  硬约束被降为"簇内 t_e 在线"**。
- **实现路径**（组件全在仓库里）：
  - MK 分量结构：`threshold/toy_ckks.py` 的 RLWE 密文本身就是 (c0,c1) 分量形式，
    MK 加法=分量拼接，toy 级可实现；或桥接 xMKCKKS_Go2Py（ctypes）。
  - 份额分发/重构：`threshold/sharing.py`（Shamir + Lagrange 已有）。
  - 层次/UAV：`ckks/hierarchy.py`（assignment=uav 已有）。
  - 评估：`hfl_attacks`（ALIE/IPM 直接打这个方案，故事完整）。
- **对照实验**：vs 原 MK-CKKS（MASER 流程，掉线即失败）、vs CDKS-LSS（多一轮份额分发）、
  vs 我们的 PPBR（单密钥层次）。通信/掉线率/精度三维表。
- **工作量**：M（2-4 周）。**论文价值**：高（空白 #1 正中，且有"首个 Python 层次 MK 门限"卖点）。

### 方案 B：one-shot 混合安全聚合（Hyb-Agg 复现 + 层次化改进）

- **空白点**：Hyb-Agg 未开源；one-shot SA（Willow/OPA）无 FL 工程。
- **一句话机制**：单密钥 CKKS 加法聚合 + ECDH pairwise 掩码（掩码只保证服务器只见
  聚合和），客户端每轮**只发一条消息**，彻底取消部分解密交换轮。
- **我们的改进（区别于 Hyb-Agg）**：层次化——边缘内 one-shot 掩码聚合，边缘对云
  再掩码，单客户端通信从 O(N) 降到 O(簇大小)；且掩码基础设施直接复用 `ppbr/sa.py`
  的 X25519+HKDF+Shamir。
- **实现路径**：`ppbr/sa.py`（掩码）+ 单密钥 CKKS（已有）+ 新写 ~300 行 one-shot
  协议层；明文对照用 `baseline/fedavg`。
- **工作量**：S（1-2 周）。**论文价值**：中-高（复现+改进未开源方案本身是贡献，
  且通信对比数据好讲）。

### 方案 C：CKKS 兼容的可验证聚合（抽样坐标校验和）

- **空白点**：可验证 SA 全基于精确算术，"密文态可验证 CKKS 聚合"无人做。
- **一句话机制**：绕开"对近似数做 ZKP"的死路——客户端额外上传 k 个随机抽样坐标的
  明文校验和（哈希承诺）；服务器公布聚合结果后，任何客户端可重算抽样坐标的聚合并与
  公布值比对，检测到服务器伪造/篡改聚合（catch 概率 1-(1-k/d)^ cheat 比例）。
- **实现路径**：协议层 ~200 行 + `hfl_attacks` 加 `system:server_forgery` 攻击
  （服务器篡改聚合结果）验证检测率——**这个攻击目前整个仓库没有，补上就是新评估维度**。
- **工作量**：S。**论文价值**：中（单独偏薄，适合作为方案 A/B 的"完整性"增强章节）。

### 方案 D：把 SMHE masked-MK 修补移植成"安全 MK-FL 全栈"

- **空白点**：#3（SMHE 有安全无工程 × MASER 有工程无安全）。
- **一句话机制**：在 MK 密文扩展时附加结构化掩码三元组 (ct, cz, Γ)，使任何中间视图
  （含服务器收集全部 μ_i）都还原不出单项明文；把它装进我们的层次 FL 训练管线。
- **实现路径**：参考 SMHE 的 C++ 代码（github.com/JiahuiWu2022/SMHE，8★）移植 masked
  expansion 到 toy-MK-CKKS；**先复现攻击**：用 SMHE 一行式 `⌊t/Q·(c0+μ_i)⌉≈m_i` 打穿
  MASER 流程（xMKCKKS_Go2Py 可直接实验），再展示修补后免疫——攻击+修补对照是论文主线。
- **工作量**：L（4-6 周，密码学校验成本高）。**论文价值**：最高（空白 #3，
  "首个端到端安全的 MK-FL 开源系统"），适合作为主要成果。

### 方案 E：UAV 场景的门限密钥全生命周期（Python 生态首个）

- **空白点**：DKG→训练→份额刷新→成员变更 的完整生命周期无任何 Python 实现
  （OpenFHE/Lattigo 都是静态参与方假设）。
- **一句话机制**：我们的 `threshold/` 模块（TTP/DKG 两模式已有）+ proactive 份额刷新
  （每 R 轮刷新 Shamir 份额，旧份额作废，抗长期潜伏泄露）+ UAV 移动触发的
  resharing（客户端换簇时份额跨簇重分发，参考 Lattigo dckks 的 resharing 设计）。
- **实现路径**：`threshold/sharing.py` 加 refresh（零常数项多项式加法，~50 行）；
  `hierarchy.py` assignment=uav 换簇钩子触发 resharing；对比实验 = 泄露窗口
  （无刷新 vs 有刷新）× 攻击者份额获取概率。
- **工作量**：M。**论文价值**：中-高（生态空白真实，且与方案 A 组合成
  "移动场景门限 FL 全生命周期"大故事）。

## 三、建议路线

1. **起步（1-2 周）**：方案 B（快、稳、数据好拿）+ 方案 C 的服务器伪造攻击（评估维度补全）。
2. **主线（2-4 周）**：方案 A——三个已有模块（threshold/sharing、hierarchy、attacks）
   组合出的新系统，空白正中，实验故事完整（掉线率扫描 × 攻击 × 精度）。
3. **增强（可选）**：方案 E 接在 A 后面；方案 D 作为长期主线冲顶会。

附：必须引用的竞品/基线清单（有代码）：xMKCKKS_Go2Py/MKHE-KKLSS（MK 底座）、
FedPHE（效率标杆）、mlsys26_disagg（SA 基线）、OpenFHE threshold（门限参照）、
SMHE（攻击来源）、LightSecAgg（SA 对照）。论文声称未开源：Hyb-Agg、
DKG-2025/2057、Willow/OPA/TACITA、Hosseini MPHE+SS——写 related work 时标注。
