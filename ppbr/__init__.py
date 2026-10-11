"""
PPBR: Privacy-Preserving and Byzantine-Robust 可插拔实现
=========================================================

严格对应 He 等, IEEE TMC 2026 的算法描述（完整阅读笔记见 paperlist/ppbr_notes.md）：

- 算法 2 (CC)：`cldp.py` + `sign_stats.py` + `clustering.py`
  客户端符号统计元组 (正/负/零号比例) → CLDP 扰动 → 边缘 trimmed k-means 聚类过滤；
- 算法 3/4/5 (EA/EF/GA)：`controller.py`
  客户端动量 φ·m+(1−φ)·Δ → 边缘均值 → 云范数中位数筛选 [M−θ,M+θ] + 裁剪 x''=min(1,M/‖m‖)。

仿真约定：数学机制在明文上计算（单进程仿真拥有全量状态），但 API 信息流严格对齐
论文协议——边缘聚类只接收 3 维扰动符号元组（接收不到模型更新），云筛选只接收
边缘范数（接收不到边缘模型）。SA 成对掩码与 PLHE 盲化属于传输层机制：本项目传输
层已用 CKKS 且边缘/云均不持私钥，仿真以明文短路并在 last_stats 中留痕。
"""

from ppbr.config import PPBRConfig
from ppbr.controller import PPBRController

__all__ = ['PPBRConfig', 'PPBRController']
