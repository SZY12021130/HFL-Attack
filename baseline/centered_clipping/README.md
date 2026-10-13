# Centered Clipping

- **出处**: S. P. Karimireddy, L. He, M. Jaggi, "Learning from History for Byzantine Robust Optimization", ICML 2021.
- **原理**: 迭代围绕动量中心裁剪：从 v=0 出发，每轮加上各更新相对中心的裁剪残差的均值，收敛到"中心"。恶意更新（如 ALIE/IPM 合谋）偏离中心被裁剪，贡献被限制在 τ 以内。
- **公式**: `v ← v + (1/n) Σ_i clip_τ(Δ_i − v)`，`clip_τ(x) = x·min(1, τ/‖x‖)`，`τ = tau_factor · max_i ‖Δ_i‖`。
- **威胁模型前提**: 无严格比例上限；对合谋攻击（ALIE/IPM）的鲁棒性显著优于朴素 clipping。
- **参数**:

| 参数 | 默认 | 说明 |
|------|------|------|
| num_iter | 3 | 围绕中心的迭代轮数 |
| tau_factor | 1.3 | 裁剪阈值相对最大范数的倍数 |

- **复杂度**: O(num_iter·n·P)。
