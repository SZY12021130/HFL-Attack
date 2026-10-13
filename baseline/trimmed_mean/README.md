# 截断均值（Trimmed Mean / TrMean）

- **出处**: Y. Chen, L. Su, J. Xu, "Distributed Statistical Machine Learning in Adversarial Settings: Byzantine Gradient Descent", POMACS 2017.
- **原理**: 每个参数坐标排序后，去掉最高和最低各 trim_ratio 比例的取值，对剩余取均值。
- **公式**: `agg[j] = mean({Δ_(k)[j] : n·β ≤ k ≤ n−n·β})`，β = trim_ratio。
- **威胁模型前提**: **恶意客户端比例必须 < trim_ratio**（两端合计截断 2β，即要求 2β < 1 且实际恶意数 ≤ 每端截断数），否则恶意值可残留在截断区间内。
- **参数**:

| 参数 | 默认 | 说明 |
|------|------|------|
| trim_ratio | 0.1 | 每端截断比例 (0~0.5)，应 ≥ 估计恶意比例 |

- **复杂度**: O(n·P)（逐坐标排序）。
