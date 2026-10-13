# RFLBA（余弦相似度过滤 + 质心估计）

- **出处**: X. Cao, M. Fang, J. Liu, N. Z. Gong, "FLTrust: Byzantine-robust Federated Learning via Trust Bootstrapping", NDSS 2021.（以中值更新为参考方向做余弦相似度剔除，FLTrust 原方案用服务器根数据集作参考；本实现基于 `ckks_fed/defenses/anomaly_detection.py` 的 `cosine_similarity_filter` 包装。）
- **原理**:
  1. 以全体更新的**坐标中值**为参考方向，剔除与参考方向余弦相似度低于 threshold 的更新；
  2. 对幸存更新做（加权）均值，得到质心估计。
- **公式**: `keep = {i : cos(Δ_i, median) ≥ threshold}`，`agg = mean({Δ_i : i ∈ keep})`。
- **威胁模型前提**: 恶意更新与良性更新方向差异明显（如符号翻转）时过滤效果好；min-max/min-sum 等刻意贴近参考方向的规避攻击可绕过。
- **参数**:

| 参数 | 默认 | 说明 |
|------|------|------|
| threshold | 0.5 | 余弦相似度阈值 |

- **复杂度**: O(n·P)（中值参考 + 余弦相似度 + 均值）。
