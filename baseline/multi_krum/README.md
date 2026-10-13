# Multi-Krum

- **出处**: Blanchard et al., NDSS 2017（Krum 的多选变体）。
- **说明**: 与 `baseline/krum/` 同一论文。Krum 只输出一个客户端的更新，方差大；Multi-Krum 选择得分最低的 m 个更新后做 FedAvg，更稳定。本包独立实现，参数与威胁模型前提见 `baseline/krum/README.md`。

| 参数 | 默认 | 说明 |
|------|------|------|
| f | None | 估计恶意客户端数（默认 (n−3)//2） |
| m | None | 选中客户端数（默认 n−f） |

- **复杂度**: O(n²·P)。
