# DnC（Detect and Clean，数据与计算筛选）

- **出处**: V. Shejwalkar, A. Houmansadr, "Manipulating the Byzantine: Optimizing Model Poisoning Attacks and Defenses for Federated Learning", USENIX Security 2021.（筛选思路亦见 Su & Xu 2019 的 Data&Computation Sieving。）
- **原理**: 单轮四步——
  1. **随机投影**：把更新随机采样到 sub_dim 个坐标降维；
  2. **谱检测**：中心化投影矩阵的 top 奇异方向（幂迭代），剔除投影得分最高的 num_byzantine 个离群更新；
  3. **聚类**：对幸存更新做 k-means (k=2)，取直径较小的簇；
  4. **截断均值**：对选中簇做 trimmed mean。
- **威胁模型前提**: 良性更新应形成直径较小的紧致簇；对高斯噪声/离群类攻击效果好，对紧贴良性簇的定向攻击（min-max/min-sum）较弱。
- **参数**:

| 参数 | 默认 | 说明 |
|------|------|------|
| num_byzantine | None | 估计恶意数（None 时按 trim_ratio·n 推算） |
| sub_dim | None | 随机投影维度（None=不降维） |
| num_iter | 1 | 谱检测迭代轮数 |
| trim_ratio | 0.1 | 聚类后 trimmed mean 截断比例 |
| seed | 42 | 随机投影/聚类种子 |

- **复杂度**: O(num_iter·n·d + n·P)，d = sub_dim。
