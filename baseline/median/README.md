# 坐标中值（Coordinate-wise Median）

- **出处**: Y. Chen, L. Su, J. Xu, "Distributed Statistical Machine Learning in Adversarial Settings: Byzantine Gradient Descent", POMACS 2017.
- **原理**: 每个参数坐标独立取所有客户端取值的中位数。中位数对任意大的离群值不敏感。
- **公式**: `agg[j] = median({Δ_i[j]})`
- **威胁模型前提**: 无严格比例上限（中位数对任意数量极端离群值稳健），但恶意客户端接近半数且相互靠近时仍可偏移中位数；通常要求恶意比例 < 1/2 才有意义。
- **参数**: 无。

| 参数 | 默认 | 说明 |
|------|------|------|
| — | — | 无超参数，忽略 weights |

- **复杂度**: O(n·P)（逐坐标排序）。
