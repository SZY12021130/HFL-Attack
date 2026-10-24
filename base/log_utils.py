"""
日志美化工具模块
提供统一的日志格式化函数，使终端输出更加优雅、清晰、有层次。

设计原则：
  1. 视觉层次：用边框、缩进、符号区分不同级别的信息
  2. 信息密度：关键指标右对齐，单位统一
  3. 语义明确：每个日志消息前缀标明来源（组件+操作）
  4. 兼容性好：不使用 ANSI 颜色码，依靠 Unicode 符号和排版
"""

import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# 边框与分隔符
# ═══════════════════════════════════════════════════════════════════════

def banner(title: str, width: int = 72, char: str = "═"):
    """打印一个居中的横幅标题"""
    pad = max(2, width - len(title) - 4)
    left = pad // 2
    right = pad - left
    line = char * left + f"  {title}  " + char * right
    logger.info(line)


def separator(width: int = 72, char: str = "─"):
    """打印分隔线"""
    logger.info(char * width)


def blank_line():
    """打印空行"""
    logger.info("")


# ═══════════════════════════════════════════════════════════════════════
# 结构化输出
# ═══════════════════════════════════════════════════════════════════════

def section(title: str, emoji: str = "▶"):
    """打印一个小节标题"""
    logger.info(f"{emoji} {title}")


def kv(key: str, value: Any, indent: int = 0, width: int = 28):
    """
    打印键值对，key 左对齐，value 紧跟其后。
    indent: 缩进层级（每级 2 个空格）
    width:  key 的最大宽度（用于对齐）
    """
    prefix = "  " * indent
    key_str = f"{key}:"
    key_padded = key_str.ljust(width)
    logger.info(f"{prefix}{key_padded} {value}")


def tree(lines: List[str], indent: int = 0):
    """
    打印树形结构列表。
    lines 中每个元素可以是字符串或 (emoji, text) 元组。
    """
    for i, line in enumerate(lines):
        prefix = "  " * indent
        if i < len(lines) - 1:
            branch = "├─"
        else:
            branch = "└─"
        if isinstance(line, tuple) and len(line) == 2:
            text = f"{line[0]} {line[1]}"
        else:
            text = str(line)
        logger.info(f"{prefix}{branch} {text}")


def info_block(title: str, items: Dict[str, Any], emoji: str = "📋", indent: int = 0):
    """打印一个带标题的键值块"""
    prefix = "  " * indent
    logger.info(f"{prefix}{emoji} {title}")
    max_key_len = max((len(str(k)) for k in items.keys()), default=0)
    for k, v in items.items():
        key_padded = f"{k}:".ljust(max_key_len + 2)
        logger.info(f"{prefix}  {key_padded} {v}")


# ═══════════════════════════════════════════════════════════════════════
# 表格输出
# ═══════════════════════════════════════════════════════════════════════

def table(headers: List[str], rows: List[List[Any]], col_widths: Optional[List[int]] = None):
    """
    打印对齐的文本表格。

    Args:
        headers:   表头字符串列表
        rows:      每行数据列表
        col_widths: 每列宽度，若为 None 则自动计算
    """
    if not rows:
        return

    # 自动计算列宽
    if col_widths is None:
        col_widths = []
        for ci in range(len(headers)):
            header_len = len(str(headers[ci]))
            max_data_len = max((len(str(row[ci])) for row in rows if ci < len(row)), default=0)
            col_widths.append(max(header_len, max_data_len) + 2)

    def fmt_row(cells):
        parts = []
        for ci, cell in enumerate(cells):
            w = col_widths[ci] if ci < len(col_widths) else 12
            parts.append(str(cell).ljust(w))
        return "│ " + " │ ".join(parts) + " │"

    def border(left, mid, right):
        parts = []
        for ci in range(len(headers)):
            w = col_widths[ci] if ci < len(col_widths) else 12
            parts.append("─" * (w + 2))
        return left + mid.join(parts) + right

    logger.info(border("┌", "┬", "┐"))
    logger.info(fmt_row(headers))
    logger.info(border("├", "┼", "┤"))
    for row in rows:
        logger.info(fmt_row(row))
    logger.info(border("└", "┴", "┘"))


# ═══════════════════════════════════════════════════════════════════════
# 快捷标签日志
# ═══════════════════════════════════════════════════════════════════════

class LogTag:
    """日志标签常量，统一各组件的日志前缀（中英双语）"""
    INIT      = "[初始化/INIT]    "
    ROUND     = "[轮次/ROUND]     "
    SELECT    = "[选择/SELECT]    "
    CLIENT_SELECT = "[选客户/CLSEL]   "
    TRAIN     = "[训练/TRAIN]     "
    ENCRYPT   = "[加密/ENCRYPT]   "
    AGG       = "[聚合/AGG]       "
    EVAL      = "[评估/EVAL]      "
    SERVER    = "[服务器/SERVER]  "
    CLIENT    = "[客户端/CLIENT]  "
    SUMMARY   = "[汇总/SUMMARY]   "
    BANDWIDTH = "[带宽/BANDWIDTH] "
    ATTACK    = "[攻击/ATTACK]    "


def tag_info(tag: str, message: str):
    """带标签的 info 日志"""
    logger.info(f"{tag} {message}")


def tag_warn(tag: str, message: str):
    """带标签的 warning 日志"""
    logger.warning(f"{tag} {message}")


def tag_error(tag: str, message: str):
    """带标签的 error 日志"""
    logger.error(f"{tag} {message}")


# ═══════════════════════════════════════════════════════════════════════
# 数值格式化快捷函数
# ═══════════════════════════════════════════════════════════════════════

def fmt_size(size_bytes: float) -> str:
    """将字节数格式化为人类可读字符串"""
    if size_bytes >= 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"
    if size_bytes >= 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    if size_bytes >= 1024:
        return f"{size_bytes / 1024:.2f} KB"
    return f"{size_bytes:.0f} B"


def fmt_time(seconds: float) -> str:
    """将秒数格式化为人类可读字符串"""
    if seconds >= 3600:
        return f"{seconds / 3600:.2f} h"
    if seconds >= 60:
        return f"{seconds / 60:.2f} min"
    if seconds >= 1:
        return f"{seconds:.2f} s"
    if seconds >= 0.001:
        return f"{seconds * 1000:.2f} ms"
    return f"{seconds * 1e6:.2f} us"


def fmt_pct(value: float) -> str:
    """格式化百分比"""
    return f"{value:.2f}%"


def fmt_bw(kbps: float) -> str:
    """格式化带宽"""
    if kbps >= 1024 * 1024:
        return f"{kbps / (1024 * 1024):.2f} Gbps"
    if kbps >= 1024:
        return f"{kbps / 1024:.2f} Mbps"
    return f"{kbps:.1f} kbps"


def safe_mean(arr):
    """安全求均值：空列表/None 返回 0.0，避免 numpy 的 RuntimeWarning"""
    if not arr:
        return 0.0
    import numpy as np
    return float(np.mean(arr))


# ═══════════════════════════════════════════════════════════════════════
# 常用复合日志模式
# ═══════════════════════════════════════════════════════════════════════

def log_round_header(round_num: int, total_rounds: int, mode: str = "Encrypted"):
    """打印轮次标题横幅"""
    mode_map = {
        "Encrypted": "CKKS加密",
        "Plaintext": "明文",
        "CipherFetch": "CipherFetch预取",
    }
    mode_zh = mode_map.get(mode, mode)
    blank_line()
    banner(f"第 {round_num}/{total_rounds} 轮  |  {mode_zh}", width=72)


def log_client_selection(selected_ids: List[int], total: int, context: str = "本轮",
                            target_round: int = None,
                            strategy_name: str = "",
                            bandwidth_info: Dict[int, float] = None):
    """打印客户端选择结果
    
    Args:
        context: "Current"=本轮训练, "Future"=未来轮预采样
        target_round: 如果是未来轮预采样，标注目标轮次
        strategy_name: 策略名称（如 Random / Sticky / PoC 等）
        bandwidth_info: 客户端带宽字典 {cid: kbps}，用于打印带宽统计
    """
    ctx_map = {
        "Current": "本轮",
        "Sticky-Current": "Sticky-本轮",
        "Future": "预采样",
    }
    ctx_zh = ctx_map.get(context, context)
    target_info = f" → R{target_round + 1}" if target_round is not None else ""
    strategy_tag = f"[{strategy_name}] " if strategy_name else ""

    # 带宽统计
    bw_summary = ""
    if bandwidth_info and selected_ids:
        bws = [bandwidth_info.get(cid, 0.0) for cid in selected_ids]
        bws = [b for b in bws if b > 0]
        if bws:
            avg_bw = safe_mean(bws)
            min_bw = min(bws)
            max_bw = max(bws)
            bw_summary = f"  |  带宽: avg={fmt_bw(avg_bw)}  min={fmt_bw(min_bw)}  max={fmt_bw(max_bw)}"

    tag_info(LogTag.SELECT,
             f"▶ {strategy_tag}{ctx_zh}{target_info}  |  "
             f"选中 {len(selected_ids)}/{total} 个: {selected_ids}{bw_summary}")


def log_training_result(client_id: int, loss: float, acc: float, time_s: float, mode: str = "Δ"):
    """打印单个客户端训练结果"""
    tag_info(LogTag.TRAIN,
             f"客户端#{client_id}  |  损失={loss:.4f}  |  准确率={acc:.2f}%  |  耗时={time_s:.2f}s  |  上传={mode}")


def log_encryption_result(client_id: int, enc_time: float, ct_size: int, num_cts: int):
    """打印加密结果"""
    tag_info(LogTag.ENCRYPT,
             f"客户端#{client_id}  |  加密耗时={enc_time:.2f}s  |  密文大小={fmt_size(ct_size)}  |  密文块数={num_cts}")


def log_aggregation_result(num_clients: int, kept: int, rule: str, agg_time: float,
                           upload: float, download: float):
    """打印聚合结果"""
    tag_info(LogTag.AGG,
             f"参与={num_clients}人  |  保留={kept}人  |  规则={rule}  |  "
             f"聚合耗时={agg_time:.3f}s  |  上传总量={fmt_size(upload)}  |  下载总量={fmt_size(download)}")


def log_evaluation_result(loss: float, acc: float):
    """打印评估结果"""
    tag_info(LogTag.EVAL, f"测试损失={loss:.4f}  |  测试准确率={acc:.2f}%")


def log_experiment_summary(final_acc: float, total_time: float, total_comm: float,
                           expansion_ratio: Optional[float] = None):
    """打印实验摘要"""
    blank_line()
    banner("实验结果汇总", width=72)
    kv("最终测试准确率", fmt_pct(final_acc), indent=1)
    kv("总训练时间", fmt_time(total_time), indent=1)
    kv("总通信量", fmt_size(total_comm), indent=1)
    if expansion_ratio is not None:
        kv("密文膨胀率", f"{expansion_ratio:.2f}x", indent=1)
    separator(width=72)
