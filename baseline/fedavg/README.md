# FedAvg（无防御基线）

- **出处**: McMahan et al., "Communication-Efficient Learning of Deep Networks from Decentralized Data", AISTATS 2017.
- **原理**: 按客户端数据量加权平均所有本地更新，无任何防御。
- **公式**: `w ← w + Σ_i (n_i / n) · Δ_i`
- **威胁模型前提**: 无——所有客户端必须诚实，否则恶意更新直接污染全局模型（作为对比表基线）。
- **参数**: 无（weights 为客户端数据量权重）。

| 参数 | 默认 | 说明 |
|------|------|------|
| — | — | 仅 weights |

- **复杂度**: O(P)，P 为模型参数量。
