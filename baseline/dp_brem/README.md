# DP-BREM（服务器动量 + 高斯 DP 噪声）

- **出处**: E. M. El Mhamdi, R. Guerraoui, S. Rouault, "The Hidden Vulnerability of Distributed Learning in Byzantium", ICML 2018.（BR moment / 动量聚合 + 差分隐私噪声释放的思路。）
- **原理**: 服务器维护跨轮动量，每轮先对客户端更新做 BR moment 拜占庭鲁棒裁剪均值 g_agg，再做动量累积、L2 裁剪，最后加高斯噪声释放，实现相邻轮级别的 DP 保护：
  1. `g_agg = BR moment`：迭代 `v ← v + (1/n)Σ_i clip_C(Δ_i − v)`（El Mhamdi et al. 2018 的 Byzantine-Robust moment；`clip_C=None` 时取客户端范数的**中位数**作阈值）；
  2. `m ← φ·m + (1 − φ)·g_agg`（**跨轮状态**，存于 `state["momentum"]`）；
  3. `m ← m · min(1, C/‖m‖)`；
  4. `m̃ = m + N(0, σ²C²·I)`，返回 m̃。
- **⚠️ 与纯规则型基线的差异**: 本规则是**有状态**的（动量跨轮累积）。
  - 接口：`aggregate(updates, weights=None, state=dict, phi=..., sigma=..., clip_C=..., seed=...)`；
  - 必须跨轮传入**同一个** `state` dict（服务器在 `AggregationServer._rule_state` 中按规则名自动维护并注入）；
  - `state=None` 时退化为无状态单轮动量（可用但跨轮语义丢失）；
  - 测试断言时建议传 `seed` 固定噪声，或将 `sigma` 调小。
- **威胁模型前提**: 噪声 σ 足够大时提供 (ε,δ)-DP（相邻轮保护）；动量 + 裁剪同时给出去若干拜占庭鲁棒性（BR moment），但对紧贴良性方向的合谋攻击保护有限。
- **参数**:

| 参数 | 默认 | 说明 |
|------|------|------|
| phi | 0.9 | 动量系数 φ ∈ [0,1) |
| sigma | 0.01 | 高斯噪声乘子 σ（标准差 = σ·C） |
| clip_C | None | L2 裁剪阈值（None = 取客户端范数中位数） |
| num_iter | 3 | BR moment 迭代轮数 |
| seed | None | 噪声随机种子（None = 不可复现） |
| state | None | 跨轮状态 dict（由服务器注入，勿手动置 None） |

- **复杂度**: O(n·P)。
