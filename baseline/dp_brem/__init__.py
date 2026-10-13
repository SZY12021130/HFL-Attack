"""DP-BREM：服务器动量 + 高斯 DP 噪声（El Mhamdi et al. ICML'18 BR moment 思路）

⚠️ 有状态规则：aggregate(..., state=dict) 必须跨轮传入同一个 state
（服务器在 AggregationServer._rule_state 中自动维护）。
"""

from .rule import aggregate
