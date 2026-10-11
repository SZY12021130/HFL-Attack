# Paper List —— 层次联邦学习 / 密文聚合 / UAV-assisted FL 参考文献

> 本目录存放 HFL-Attack 项目架构设计参考的论文 PDF 与文献清单。
> 标注 ✅ 的为本项目实现直接参考；标注 🔧 的为可进一步引入的机制。

## 一、已有 PDF

| 文件 | 论文 | 出处 | 借鉴点 |
|---|---|---|---|
| `25-（云边端）single-key homomorphic encryption.pdf` | Single-Key Homomorphic Encryption Based Efficient Privacy-Enhanced Federated Learning | IEEE Internet of Things Journal, 2025 (Liu 等) | ✅ **密文流转基线**：客户端 CKKS 加密 → 边缘密文部分聚合（样本数加权）→ 云密文全局聚合 → 只解密全局结果；密钥托管 + Shamir 分片 + TEE 重建私钥 🔧 |
| `2026 - He 等 - PPBR ... edge-assisted hierarchical federated learning in mobil.pdf` | PPBR: Privacy-Preserving and Byzantine-Robust Edge-Assisted Hierarchical Federated Learning in Mobile Networks | IEEE TMC, 2026 (He 等) | ✅ 云-边-端三层职责划分；🔧 边层签名统计聚类过滤 + 云层范数中位数裁剪（抗拜占庭）；掉线容忍 |

## 二、架构参考开源实现

| 仓库 | 说明 | 借鉴点 |
|---|---|---|
| https://github.com/LuminLiu/HierFL | ICLR 2020 Workshop, Client-Edge-Cloud 层次 FL（HierFAVG 官方代码） | ✅ 端→边→云两级聚合调度骨架（`num_edge_aggregation` / `num_communication` 双时间尺度） |
| https://github.com/apoorvakliv/fed2tier | ACM AI-ML Systems 2023，两层 FL（gRPC 真实分布式，支持 Jetson/Pi） | 🔧 真实消息流转（gRPC 双向流）；本地已有克隆 `E:\GithubProjects\fed2tier-main` |
| https://github.com/NishNilanka/Hierarchical-Federated-Learning | Flower + PyTorch 三层 HFL（2024） | ✅ `HierFL_Dynamic_allocation.py` 每轮动态客户端-边缘分配（对应 UAV 移动关联） |
| https://github.com/wang2506/UAV-PFL | UAV-assisted multi-tier personalized FL | ✅ UAV 作为中间层的层次 FL 建模 |

## 三、UAV-assisted FL 论文

| 论文 | 出处 | 链接 | 借鉴点 |
|---|---|---|---|
| UAV-assisted Unbiased HFL | IEEE TMLCN 2025 (Zhagypar 等) | https://arxiv.org/abs/2407.07739 | ✅ UAV 移动边缘聚合节点 + 客户端关联变化的无偏加权聚合建模（客户端移动导致非均匀关联时的加权修正） |
| Energy-Efficient FL with Wireless Powered UAV | 2023 (E2FL) | https://github.com/phamqv/Energy-Efficient-FL-Wireless-Powered-UAV | 🔧 UAV 轨迹/能量模型 |
| UAV-assisted Online ML over Multi-tiered Networks | 2023 (wang2506) | https://github.com/wang2506/UAV-PFL | ✅ 多层级嵌套个性化 FL |

## 四、加密 FL 实现参考

| 仓库 | 说明 |
|---|---|
| https://github.com/yooopan/FedSHE | 分段 CKKS 突破加密长度限制（本地已有克隆 `FedSHE/`） |
| https://github.com/PythonDefBetter/Federated-Learning-with-CKKS-Homomorphic-Encryption | 单层 CKKS-FL 参考实现 |

## 五、调研结论

1. **"层次密文聚合"无现成开源实现**——端→边→云两段 CKKS 密文聚合是本项目的创新空间。
2. 密文流转设计遵循单密钥 HE 云边端论文的基线：客户端加密 → 边缘部分聚合 → 云全局聚合 → 仅全局结果可解密（本项目：客户端持私钥解密，云/边均只持公钥）。
3. CKKS 乘法深度是层次聚合的关键约束：边缘"加权和不归一化（全局权重预缩放）+ 云纯加法合并"只消耗一层乘法深度，兼容 `[40,20,40]` 模数链（见 `ckks_fed/hierarchy.py` 设计说明）。
4. 后续可引入：PPBR 的双层抗拜占庭过滤、单密钥论文的哈希校验（防云偷解密单个更新）、Zhagypar 的无偏加权（UAV 关联非均匀时）。
