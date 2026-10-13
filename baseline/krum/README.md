# Krum / Multi-Krum

- **出处**: P. Blanchard, E. M. El Mhamdi, R. Guerraoui, J. Stainer, "Machine Learning with Adversaries: Byzantine Tolerant Gradient Descent", NDSS 2017.
- **原理**:
  - **Krum**: 计算更新间两两欧氏距离，选与最近的 n−f−2 个邻居距离之和最小的那个更新作为聚合结果（恶意更新彼此分散、远离良性簇，得分高）。
  - **Multi-Krum**（同论文变体，见 `baseline/multi_krum/`）：选得分最低的 m 个更新做 FedAvg，比单 Krum 稳定、保留更多信息。
- **公式（Krum）**: `score(i) = Σ_{j∈N(i, n−f−2)} ‖Δ_i − Δ_j‖²`，`agg = argmin_i score(i)`。
- **威胁模型前提**: **n > 2f + 2**（恶意客户端不超过约半数）；f 为估计恶意数，估计偏保守更安全。
- **参数**:

| 参数 | 默认 | 说明 |
|------|------|------|
| f | None | 估计恶意客户端数（默认 (n−3)//2） |
| m（仅 multi_krum） | None | 选中客户端数（默认 n−f） |

- **复杂度**: O(n²·P)（两两距离）+ O(n² log n)（排序）。
