"""
CKKS-Fed 联邦学习主程序
协调多客户端、聚合服务器和辅助节点

薄入口：实验编排逻辑在 base/experiment.py（Experiment 基类 + Track 协议），
各方案轨道在 ckks/track.py、ppbr/track.py、rfl/track.py。
"""

import os

# MKL(numpy/torch) 与 TenSEAL 各捆绑一份 libiomp5md.dll，后初始化的一份会
# 触发 OMP Error #15 直接 abort 进程。必须在任何 OpenMP 运行库加载前设置。
os.environ.setdefault('KMP_DUPLICATE_LIB_OK', 'TRUE')

import faulthandler
# 原生段错误/外部终止时把 Python 栈打到 stderr，便于诊断长时运行的偶发崩溃
faulthandler.enable()

import torch
import argparse
import logging

# 设置日志 — 简洁、对齐的格式
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)-7s %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# 导入日志工具
from base.log_utils import (
    banner, blank_line, section, kv,
    LogTag, tag_info,
    fmt_pct,
    safe_mean,
)
from base.data import get_dataset_num_classes
from base.config_loader import load_config
from base.experiment import Experiment

# 副作用导入：注册方案轨道到 base.experiment.TRACKS。
# 顺序即优先级：rfl → ppbr → athfhe → ckks（ckks 恒 enabled，作兜底，
# 必须最后注册）。
# 注意：athfhe.track 虽在源码顺序上列于 ckks.track 之前，但绝非笔误——
# ckks 恒 enabled 会遮蔽其后所有轨道，而 athfhe 两条轨道与 rfl/ppbr 同为
# enabled-gated 的完整协议轨道，必须在 ckks 兜底之前注册才能被命中。
import rfl.track  # noqa: F401
import ppbr.track  # noqa: F401
import athfhe.track  # noqa: F401  # 注册 AThFHE 两方案轨道（athfhe_hier / athfhe_twin，互斥，enabled 命中即胜出）
import ckks.track  # noqa: F401

# 兼容别名：tests/ 与 scripts/verify_attacks.py 依赖
# `from main import CKKSFedExperiment`，绝不能破坏
CKKSFedExperiment = Experiment


def _get_device_info() -> str:
    """检测并返回GPU/设备信息字符串"""
    if torch.cuda.is_available():
        gpu_name = torch.cuda.get_device_name(0)
        gpu_mem = torch.cuda.get_device_properties(0).total_memory / (1024**3)
        return f"CUDA available: True | Device: {gpu_name} | Memory: {gpu_mem:.1f} GB"
    else:
        return "CUDA available: False | Device: CPU"


def main():
    """主函数：运行标准 CKKS 加密联邦学习实验"""
    parser = argparse.ArgumentParser(description="CKKS-Fed 联邦学习实验")
    parser.add_argument(
        "--config", type=str, default="configs/config.yaml",
        help="配置文件路径 (默认: configs/config.yaml)"
    )
    parser.add_argument(
        "--output-dir", type=str, default=None,
        help="自定义实验输出目录 (默认: 使用配置文件中的 results_dir)"
    )
    args = parser.parse_args()

    blank_line()
    banner("CKKS-Fed: 基于CKKS同态加密的联邦学习框架", width=72)
    tag_info(LogTag.INIT, _get_device_info())

    # 加载配置（支持 extends 继承，与 Experiment 同一解析器，保证预检一致）
    main_config = load_config(args.config)

    # 自动推断 num_classes
    dataset_name = main_config.get('data', {}).get('dataset', 'cifar10')
    inferred_classes = get_dataset_num_classes(dataset_name)
    config_classes = main_config.get('data', {}).get('num_classes', inferred_classes)
    if config_classes != inferred_classes:
        logger.warning(
            f"[AutoFix] 配置中 num_classes={config_classes} 与数据集 '{dataset_name}' "
            f"不匹配，自动修正为 {inferred_classes}"
        )
        main_config['data']['num_classes'] = inferred_classes

    blank_line()
    section("🚀 启动 CKKS-Fed 实验（轨道由配置决定：加密 / 明文 / PPBR）", emoji="")
    experiment_enc = CKKSFedExperiment(
        args.config,
        output_dir=args.output_dir)
    results_enc = experiment_enc.run_experiment()

    blank_line()
    banner("实验已完成", width=72)

    enc_results = results_enc.get('results', [])
    enc_acc = enc_results[-1]['test_acc'] if enc_results else 0.0
    enc_metrics = results_enc['metrics']
    kv("最终测试准确率", fmt_pct(enc_acc), indent=1)
    kv("结果目录", experiment_enc.output_dir, indent=1)

    if enc_metrics.get('expansion_ratio') and not experiment_enc.plaintext_mode:
        blank_line()
        section("🔐 CKKS 加密指标汇总", emoji="")
        kv("密文膨胀率", f"{safe_mean(enc_metrics.get('expansion_ratio', [])):.2f}x", indent=1)
        kv("平均加密时间", f"{safe_mean(enc_metrics.get('encryption_time', [])):.3f} s/客户端", indent=1)


if __name__ == '__main__':
    main()
