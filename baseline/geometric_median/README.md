# 几何中位数（Geometric Median）

- **出处**: 经典 Weiszfeld 算法；作为拜占庭鲁棒聚合见 Chen et al. POMACS'17 与 Blanchard et al. NDSS'17 的对比讨论。
- **原理**: 寻找使得到所有更新点的欧氏距离之和最小的点 `argmin_x Σ_i ‖x − Δ_i‖`，用 Weiszfeld 迭代求解（初始化为坐标中值）。比坐标中值更充分地利用全部客户端信息。
- **公式**: `x_{t+1} = Σ_i (Δ_i / ‖x_t − Δ_i‖) / Σ_i (1 / ‖x_t − Δ_i‖)`。
- **威胁模型前提**: 无严格比例上限（与 median 类似，破坏 1/2 以上仍可被带偏）。
- **参数**:

| 参数 | 默认 | 说明 |
|------|------|------|
| max_iter | 10 | Weiszfeld 最大迭代轮数 |
| tol | 1e-5 | 收敛阈值 |

- **复杂度**: O(max_iter·n·P)。
