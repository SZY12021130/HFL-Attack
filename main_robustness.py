"""
CKKS-Fed 鲁棒性研究主入口
联邦学习 + CKKS同态加密 + 攻击与防御实验（仅加密模式）

运行方式:
    # 运行配置文件中的全部实验（默认）
    python main_robustness.py

    # 只运行某一种攻击的所有防御组合
    python main_robustness.py --attack scaling

    # 只运行某一种防御的所有攻击组合
    python main_robustness.py --defense median

    # 同时指定攻击和防御，只跑一个实验
    python main_robustness.py --attack sign_flipping --defense median

    # 指定其他配置文件
    python main_robustness.py --config path/to/config.yaml

配置文件:
    configs/attack_config.yaml

功能:
    1. 自动构建 (攻击 x 防御) 加密模式实验矩阵
    2. 支持命令行过滤，只运行感兴趣的子集
    3. 对每种组合运行联邦学习训练
    4. 生成汇总对比报告
"""

import argparse
from ckks_fed.experiments.attack_runner import AttackExperimentRunner
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(
        description="CKKS-Fed 鲁棒性攻击与防御实验平台"
    )
    parser.add_argument(
        "--attack", type=str, default=None,
        help="只运行指定攻击的实验 (如: sign_flipping, scaling, backdoor, gaussian_noise, mimic_alie, constant)"
    )
    parser.add_argument(
        "--defense", type=str, default=None,
        help="只运行指定防御的实验 (如: fedavg, median, trimmed_mean, krum, multi_krum, geometric_median, norm_clip_fedavg, cosine_fedavg, multi_criteria_fedavg)"
    )
    parser.add_argument(
        "--config", type=str, default="configs/attack_config.yaml",
        help="配置文件路径 (默认: configs/attack_config.yaml)"
    )
    args = parser.parse_args()

    logger.info("=" * 70)
    logger.info("CKKS-Fed 鲁棒性研究: 联邦学习攻击与防御实验 (加密模式)")
    logger.info("=" * 70)
    logger.info(f"读取配置: {args.config}")
    if args.attack:
        logger.info(f"过滤条件 - 攻击: {args.attack}")
    if args.defense:
        logger.info(f"过滤条件 - 防御: {args.defense}")
    
    runner = AttackExperimentRunner(
        config_path=args.config,
        filter_attack=args.attack,
        filter_defense=args.defense
    )
    runner.run_all()
    
    logger.info("=" * 70)
    logger.info("所有实验已完成，请查看结果目录获取详细报告")
    logger.info("=" * 70)


if __name__ == "__main__":
    main()
