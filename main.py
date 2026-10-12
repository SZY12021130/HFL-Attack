"""
CKKS-Fed 联邦学习主程序
协调多客户端、聚合服务器和辅助节点
"""

import os

# MKL(numpy/torch) 与 TenSEAL 各捆绑一份 libiomp5md.dll，后初始化的一份会
# 触发 OMP Error #15 直接 abort 进程。必须在任何 OpenMP 运行库加载前设置。
os.environ.setdefault('KMP_DUPLICATE_LIB_OK', 'TRUE')

import faulthandler
# 原生段错误/外部终止时把 Python 栈打到 stderr，便于诊断长时运行的偶发崩溃
faulthandler.enable()

import torch
import torch.nn as nn
import numpy as np
import yaml
import time
import os
import json
import argparse
import logging
from datetime import datetime
from typing import Dict, List
import psutil

# 导入自定义模块
from ckks_fed.models import create_model, print_model_summary
from ckks_fed.data import (create_client_dataloaders, evaluate,
                           get_dataset_num_classes, get_model_params)
from ckks_fed.client import FederatedClient
from ckks_fed.server import AggregationServer
from ckks_fed.crypto import CKKSManager

# 设置日志 — 简洁、对齐的格式
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)-7s %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# 导入日志工具
from ckks_fed.log_utils import (
    banner, separator, blank_line, section, kv, tree, table,
    LogTag, tag_info, tag_warn,
    fmt_size, fmt_time, fmt_pct, fmt_bw,
    log_round_header, log_client_selection, log_training_result,
    log_encryption_result, log_aggregation_result, log_evaluation_result,
    log_experiment_summary,
    safe_mean,
)

# 门限 CKKS 解密（doc/threshold_ckks.md）：t-out-of-n 门限解密可插拔模块
from threshold import ThresholdConfig, ThresholdDecryptor


class MetricsCollector:
    """实验指标收集器"""

    def __init__(self):
        self.metrics = {
            # 计算时间指标
            'local_train_time': [],
            'encryption_time': [],
            'aggregation_time': [],
            'total_round_time': [],              # 模拟 wall-clock 时间（含网络传输）
            'total_round_time_simulated': [],    # 模拟 wall-clock 时间

            # 通信开销指标
            'upload_cost': [],
            'download_cost': [],
            'total_communication_cost': [],
            'upload_time': [],                    # 每轮最慢客户端上传时间（模拟 wall-clock）
            'download_time': [],                  # 每轮最慢客户端下载时间（模拟 wall-clock）

            # 加密相关大小指标
            'plaintext_size': [],
            'ciphertext_size': [],
            'expansion_ratio': [],

            # 模型性能指标
            'train_loss': [],
            'train_acc': [],
            'test_loss': [],
            'test_acc': [],

            # 系统指标
            'cpu_utilization': [],
            'memory_usage': [],
            'communication_rounds': [],
            'dropout_rate': [],

            # 加密相关指标
            'key_distribution_time': [],
            'key_distribution_cost': [],
            'num_ciphertexts': [],
            'aggregated_ciphertexts': [],       # 聚合密文数量（服务器聚合后）
        }
        self.start_time = time.time()

    def record(self, key: str, value):
        """记录指标"""
        if key not in self.metrics:
            self.metrics[key] = []
        self.metrics[key].append(value)

    def get_metrics(self) -> Dict:
        """获取所有指标"""
        return self.metrics.copy()

    def save_to_file(self, filepath: str):
        """保存指标到文件"""
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(self.metrics, f, indent=2, ensure_ascii=False)
        logger.info(f"指标已保存到: {filepath}")


class CKKSFedExperiment:
    """CKKS-Fed实验类"""

    def __init__(self, config_path: str = "configs/config.yaml",
                 bandwidth_simulator=None, output_dir: str = None):
        """
        初始化实验

        Args:
            config_path: 配置文件路径
            bandwidth_simulator: 外部传入的 BandwidthSimulator 实例（用于对比实验共享带宽）
            output_dir: 自定义输出目录（覆盖配置文件中的 results_dir）
        """
        # 加载配置
        with open(config_path, 'r', encoding='utf-8') as f:
            self.config = yaml.safe_load(f)

        # 自动推断 num_classes，避免配置与数据集不匹配导致 CUDA assert
        dataset_name = self.config.get('data', {}).get('dataset', 'cifar10')
        inferred_classes = get_dataset_num_classes(dataset_name)
        config_classes = self.config.get('data', {}).get('num_classes', inferred_classes)
        if config_classes != inferred_classes:
            logger.warning(
                f"[AutoFix] 配置中 num_classes={config_classes} 与数据集 '{dataset_name}' "
                f"不匹配，自动修正为 {inferred_classes}"
            )
            self.config['data']['num_classes'] = inferred_classes

        # 设置随机种子
        seed = self.config['system']['random_seed']
        torch.manual_seed(seed)
        np.random.seed(seed)

        # 设备
        self.device = self.config['system']['device']
        if self.device == "cuda" and not torch.cuda.is_available():
            logger.warning("CUDA不可用，切换到CPU")
            self.device = "cpu"

        # 创建输出目录
        self.timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dataset_name = self.config['data']['dataset']
        if output_dir:
            # 使用自定义输出目录作为父目录，在其下生成 时间+数据集 子目录
            base_dir = output_dir
        else:
            base_dir = self.config['output']['results_dir']
        self.output_dir = os.path.join(
            base_dir,
            f"experiment_{self.timestamp}_{dataset_name}"
        )
        os.makedirs(self.output_dir, exist_ok=True)

        # 保存配置文件副本，便于后续追踪和对比
        self.config_path = config_path
        import shutil
        shutil.copy(config_path, os.path.join(self.output_dir, "config.yaml"))

        # 配置实验日志文件（同时保留终端输出）
        self._setup_experiment_logger()

        # 指标收集器
        self.metrics = MetricsCollector()

        # 带宽模拟器（支持外部传入，确保对比实验使用相同带宽）
        self.bandwidth_simulator = bandwidth_simulator

        # 初始化组件
        self._init_components()

        tag_info(LogTag.INIT, f"Experiment output dir: {self.output_dir}")

    def _log_batch_indices(self, round_num, selected_clients, client_masks, global_mask):
        """记录每轮每个客户端的 batch 索引和聚合后的索引"""
        pass

    def _setup_experiment_logger(self):
        """
        配置实验日志文件处理器，将终端输出同时写入到结果目录的 experiment.log。
        该方法确保每次实验运行都有完整的终端日志留存，便于后续分析和论文复现。
        """
        log_file = os.path.join(self.output_dir, "experiment.log")
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setLevel(logging.INFO)
        # 使用与终端一致的格式
        formatter = logging.Formatter(
            '%(asctime)s %(levelname)-7s %(message)s',
            datefmt='%H:%M:%S'
        )
        file_handler.setFormatter(formatter)
        # 添加到 root logger，捕获所有模块的日志
        root_logger = logging.getLogger()
        root_logger.addHandler(file_handler)
        # 记录日志文件位置
        logger.info(f"实验日志已配置: {log_file}")

    def _init_components(self):
        """初始化实验组件"""
        section("Initialization", emoji="📦")

        self.client_loaders, self.test_loader = create_client_dataloaders(
            dataset_name=self.config['data']['dataset'],
            num_clients=self.config['system']['num_clients'],
            batch_size=self.config['system']['batch_size'],
            data_dir=self.config['data']['data_dir'],
            iid=self.config['data']['iid'],
            alpha=self.config['data']['alpha']
        )
        tree([
            ("🗂️ ", f"Dataset: {self.config['data']['dataset']}  |  "
                     f"Clients: {self.config['system']['num_clients']}  |  "
                     f"IID={self.config['data']['iid']}"),
        ], indent=1)

        self.global_model = create_model(self.config['model']['name'], self.config)
        dataset = self.config['data']['dataset']
        dummy_input = (1, 1, 28, 28) if dataset in ["mnist", "fashion_mnist", "emnist"] else (1, 3, 32, 32)
        print_model_summary(self.global_model, dummy_input)

        # 密钥架构：密钥生成中心(KGC)生成完整上下文（含私钥）——模拟环境中由实验编排方担任
        # • 客户端：持有完整上下文（含私钥），可解密全局模型
        # • 服务器：仅持有公钥上下文，只能加密/同态聚合，不能解密任何密文
        # 标记 PPBR 轨道：CKKS 管理器在 PPBR 模式下仅作组件占位（不上传/
        # 不稀疏化），跳过 PTopk 残差包装，打印按 PPBR 语义
        _ckks_cfg = self.config['ckks'].copy()
        _ckks_cfg['_ppbr_track'] = bool(
            self.config.get('ppbr', {}).get('enabled', False))
        self.ckks_manager = CKKSManager(_ckks_cfg)
        from ckks_fed.crypto import CKKSContext
        poly_degree = self.config['ckks'].get('poly_modulus_degree', 8192)
        server_context = CKKSContext.from_full_bytes(
            self.ckks_manager.context.get_context_bytes(),
            poly_modulus_degree=poly_degree)
        self.server_ckks_manager = CKKSManager(_ckks_cfg.copy(), context=server_context)
        ckks = self.config['ckks']
        tree([
            ("🔐", f"CKKS  N={ckks.get('poly_modulus_degree')}  |  "
                   f"scale=2^{ckks.get('global_scale', 0).bit_length()-1}  |  "
                   f"mod={ckks.get('coeff_mod_bit_sizes')}  |  "
                   f"packing={'ON' if ckks.get('slot_packing', True) else 'OFF'}  |  "
                   f"server_key=public-only"),
        ], indent=1)

        self.server = AggregationServer(self.global_model, self.config, self.device, self.server_ckks_manager)
        self.server.init_encrypted_global_state(self.server_ckks_manager)
        tree([
            ("🖥️ ", f"Aggregation Server  |  rule={self.config.get('defense', {}).get('aggregation_rule', 'fedavg')}  |  encrypted_state=initialized"),
        ], indent=1)

        self.clients = []
        for i in range(self.config['system']['num_clients']):
            client_model = create_model(self.config['model']['name'], self.config)
            client = FederatedClient(
                client_id=i, model=client_model,
                train_loader=self.client_loaders[i],
                config=self.config, device=self.device
            )
            # 统一 CKKS 上下文：从服务器序列化上下文反序列化
            from ckks_fed.crypto import CKKSContext
            poly_degree = self.config['ckks'].get('poly_modulus_degree', 8192)
            context = CKKSContext.from_full_bytes(
                self.ckks_manager.context.get_full_context_bytes(),
                poly_modulus_degree=poly_degree)
            client.ckks_manager = CKKSManager(_ckks_cfg.copy(), context=context)
            self.clients.append(client)
        tree([
            ("👤", f"Created {len(self.clients)} clients  |  "
                   f"sampling_rate={self.config['system'].get('client_sampling_rate', 1.0)}"),
        ], indent=1)

        # 初始化客户端参与统计
        self.client_participation = {i: 0 for i in range(len(self.clients))}
        self.client_total_ciphertexts = {i: 0 for i in range(len(self.clients))}

        # 可插拔客户端选择策略
        from ckks_fed.client_selection import create_client_selection_strategy
        self.selection_strategy = create_client_selection_strategy(
            self.config, num_clients=len(self.clients))

        # PPBR（可插拔拜占庭鲁棒，He 等 IEEE TMC 2026）：开启后自动启用层次架构
        # （云-边-端是 PPBR 的前提），无需手动设置 hierarchy.enabled。
        # 双轨架构：开启后整条加解密路线走 PPBR 协议（云持 sk_A、边缘各自持
        # 私钥、SA 掩码聚合、CLDP、范数盲化裁剪），不走全局投票/TopK/单密钥 CKKS。
        self.ppbr = None
        ppbr_cfg = self.config.get('ppbr') or {}
        if ppbr_cfg.get('enabled', False):
            if not self.config.get('hierarchy', {}).get('enabled', False):
                self.config['hierarchy']['enabled'] = True
                tag_warn(LogTag.ATTACK,
                         "ppbr.enabled=true → 自动开启 hierarchy（云-边-端架构）")
            from ppbr import PPBRProtocol
            self.ppbr = PPBRProtocol(
                self.config,
                num_edges=int(self.config['hierarchy'].get('num_edges', 3)),
                seed=self.config['system'].get('random_seed', 42))
            # 供协议日志区分良性/恶意（攻击实验的过滤诊断）；
            # self.malicious_ids 在下方攻击初始化才填充，先置空、后回填
            self.ppbr._malicious_ids = set()
            c = self.ppbr.cfg
            tree([
                ("🛡️", f"PPBR: ε_χ={c.epsilon_chi}  |  k={c.k} b={c.b}  |  "
                       f"φ={c.momentum_phi} ζ={c.global_zeta}  |  "
                       f"β={'已知' if c.beta_known else '未知'}"),
            ], indent=1)
            # 双轨标记：客户端打印与 PTopk 残差逻辑按 PPBR 语义走
            for c in self.clients:
                c.track = "ppbr"

        # 门限 CKKS 解密（doc/threshold_ckks.md）：把"每个客户端持完整 sk"
        # 改造为 t-out-of-n 门限解密。仅 CKKS 轨道——与 PPBR 互斥
        # （PPBR 走论文的 SA 密钥体系，不经单密钥 CKKS 门限）。
        self.threshold_cfg = ThresholdConfig.from_config(
            self.config.get('threshold') or {})
        self.threshold_decryptor: ThresholdDecryptor = None
        self._threshold_dist_recorded = False
        if self.threshold_cfg.enabled:
            if ppbr_cfg.get('enabled', False):
                self.threshold_cfg.enabled = False
                tag_warn(LogTag.INIT,
                         "threshold.enabled=true 与 ppbr.enabled=true 冲突"
                         "（threshold 仅 CKKS 轨道）→ 强制禁用 threshold")
            else:
                c = self.threshold_cfg
                tree([
                    ("🔑", f"Threshold: mode={c.mode}  |  combiner={c.combiner}"
                           f"（{'场景B-服务器盲' if c.combiner == 'client' else '场景A-服务器可见'}）"
                           f"  |  t={c.t}  |  smudging={c.smudging}"),
                ], indent=1)

        # 层次联邦学习（云-边-端）：enabled 时创建边缘层
        self.hierarchy = None
        if self.config.get('hierarchy', {}).get('enabled', False):
            from ckks_fed.hierarchy import HierarchyController
            self.hierarchy = HierarchyController(
                self.config, self.server_ckks_manager, num_clients=len(self.clients))
            info = self.hierarchy.info()
            tree([
                ("🛰️ ", f"Hierarchy: edges={info['num_edges']}  |  "
                        f"assignment={info['assignment']}  |  "
                        f"clients/edge={info['edge_clients']}"),
            ], indent=1)

        # 攻击注入：attack.enabled 时从 hfl_attacks 注册表按 name 实例化。
        # enabled=false（默认）时代码路径与正常 FedAvg 完全一致。
        # 恶意客户端放置策略 placement：
        #   last（默认）= 编号最大的最后 malicious_count 个（连续块）；
        #   spread = 摊到不同边缘（每边缘先各取 1 个，多余再补）——
        #   PPBR 的论文前提是客户端随机关联边缘（每边缘良性多数），
        #   spread 用于"攻击 × PPBR"实验，避免恶意客户端聚集单一边缘
        #   绕过边层聚类过滤；无层次架构时 spread 退化为 last。
        self.attack_inst = None
        self.malicious_ids = []
        attack_cfg = self.config.get('attack') or {}
        if attack_cfg.get('enabled', False):
            from hfl_attacks import ATTACK_REGISTRY, DataPoisoner, PrivacyAttack
            name = attack_cfg.get('name', '')
            if name not in ATTACK_REGISTRY:
                raise ValueError(
                    f"未知攻击 '{name}'，注册表可选: {sorted(ATTACK_REGISTRY)}")
            params = attack_cfg.get('params') or {}
            self.attack_inst = ATTACK_REGISTRY[name](**params)
            n_mal = min(int(attack_cfg.get('malicious_count', 1)), len(self.clients))
            placement = attack_cfg.get('placement', 'last')
            if placement == 'spread' and self.hierarchy is not None:
                assign_map = self.hierarchy.assigner.assign(0)
                # 每边缘客户端按编号降序，轮转各取 1 个，保证恶意客户端
                # 尽量摊到不同边缘（对应论文的随机关联前提）
                by_edge: Dict[int, List[int]] = {}
                for c in self.clients:
                    by_edge.setdefault(assign_map.get(c.client_id, 0), []).append(
                        c.client_id)
                for e in by_edge:
                    by_edge[e].sort(reverse=True)
                picked: List[int] = []
                edges_desc = sorted(by_edge, reverse=True)
                while len(picked) < n_mal and any(by_edge.values()):
                    for e in edges_desc:
                        if by_edge[e] and len(picked) < n_mal:
                            picked.append(by_edge[e].pop(0))
                self.malicious_ids = sorted(picked)
            else:
                self.malicious_ids = [c.client_id for c in self.clients[-n_mal:]]
            num_classes = self.config['data']['num_classes']
            if isinstance(self.attack_inst, DataPoisoner):
                # 数据投毒：替换恶意客户端的训练数据加载器（local_train 每轮读取）
                for cid in self.malicious_ids:
                    self.clients[cid].train_loader = self.attack_inst.poison_loader(
                        self.clients[cid].train_loader, num_classes)
            elif isinstance(self.attack_inst, PrivacyAttack):
                # 隐私推断不改变全局模型，训练循环中为空操作；
                # 指标（成员推断AUC/重建PSNR等）请用 hfl_attacks/runner.py 单独评估
                tag_warn(LogTag.ATTACK,
                         f"隐私推断攻击 {name} 不修改训练过程，请用 hfl_attacks/runner.py 评估其指标")
            tree([
                ("⚔️", f"Attack: {name}  |  malicious={self.malicious_ids}"),
            ], indent=1)
            if self.ppbr is not None:
                self.ppbr._malicious_ids = set(self.malicious_ids)

        # 带宽模拟器（确保模拟 wall-clock 时间使用配置的异构带宽）
        from ckks_fed.bandwidth_simulator import BandwidthSimulator
        bw_cfg = self.config.get('bandwidth', {})
        self.bandwidth_simulator = BandwidthSimulator(
            num_clients=self.config['system']['num_clients'],
            base_dl_bw=bw_cfg.get('base_dl_bw', 1024.0),
            base_ul_bw=bw_cfg.get('base_ul_bw', 512.0),
            variance=bw_cfg.get('variance', 0.3),
            distribution=bw_cfg.get('distribution', 'uniform'),
            seed=self.config['system'].get('random_seed', 42),
            fedfetch_trace_path=bw_cfg.get('fedfetch_trace_path'),
            filter_low_bandwidth=bw_cfg.get('filter_low_bandwidth', False),
            min_dl_bw_kbps=bw_cfg.get('min_dl_bw_kbps', 10240.0),
            min_ul_bw_kbps=bw_cfg.get('min_ul_bw_kbps', 10240.0),
            max_dl_bw_kbps=bw_cfg.get('max_dl_bw_kbps', float('inf')),
            max_ul_bw_kbps=bw_cfg.get('max_ul_bw_kbps', float('inf')),
        )
        bw_stats = self.bandwidth_simulator.get_stats()
        tree([
            ("🌐", f"Bandwidth Simulator  |  DL={fmt_bw(bw_stats.get('dl_bw_mean_kbps', 0))}  |  "
                     f"UL={fmt_bw(bw_stats.get('ul_bw_mean_kbps', 0))}  |  "
                     f"dist={bw_cfg.get('distribution', 'uniform')}"),
        ], indent=1)

    def _select_clients(self, context: str = "Current") -> List:
        """使用可插拔策略选择参与本轮训练的客户端子集"""
        num_clients = len(self.clients)
        rate = self.config['system'].get('client_sampling_rate', 1.0)
        num_selected = max(1, int(num_clients * rate))

        # 收集策略所需的额外上下文
        kwargs = {}
        if hasattr(self, 'client_loaders') and self.client_loaders:
            kwargs['train_weights'] = [len(self.client_loaders[i].dataset) for i in range(num_clients)]
        # 注意：服务器不持私钥、无明文全局模型，不再向选择策略提供 global_state

        selected_ids = self.selection_strategy.select(
            self.clients, num_selected, round_num=getattr(self.server, 'round_num', 0),
            **kwargs)
        selected = [self.clients[cid] for cid in selected_ids]
        strategy_name = self.config.get('client_selection', {}).get('strategy', 'random')
        bandwidth_info = getattr(self, 'bandwidth_info', None)
        log_client_selection(selected_ids, num_clients, context=context,
                             strategy_name=strategy_name, bandwidth_info=bandwidth_info)
        return selected

    def run_round_encrypted(self, round_num: int) -> Dict:
        """
        运行一轮加密联邦学习（CKKS-Fed）
        架构：服务器加密全局模型下发 → 客户端解密 → 本地训练 → 客户端加密上传
              → 服务器同态聚合（密文域更新全局状态，全程不解密）
              → 客户端解密全局模型并完成评估（服务器不持有私钥）

        Args:
            round_num: 当前轮次

        Returns:
            本轮统计信息
        """
        log_round_header(round_num + 1, self.config['system']['num_rounds'], mode="CKKS-Encrypted")
        round_start = time.time()

        selected_clients = self._select_clients()

        encrypted_global = self.server.get_encrypted_global_params()
        client_updates, client_weights, train_losses, train_accs = [], [], [], []
        threshold_stats = None
        if self.threshold_cfg.enabled:
            # 门限轨道：每轮开头由 t 个被选客户端协作解密全局模型
            # （客户端加密上传、服务器密文加权聚合路径完全不变）
            if self.threshold_decryptor is None:
                self.threshold_decryptor = ThresholdDecryptor(
                    self.threshold_cfg, len(self.clients),
                    self.ckks_manager.context.get_full_context_bytes(),
                    poly_modulus_degree=self.config['ckks'].get(
                        'poly_modulus_degree', 8192))
            plain_global, threshold_stats = \
                self.threshold_decryptor.decrypt_global_params(
                    encrypted_global,
                    [c.client_id for c in selected_clients])
            for client in selected_clients:
                update, loss, acc = client.local_train(
                    global_params=plain_global, round_num=round_num)
                client_updates.append(update)
                client_weights.append(client.get_weight())
                train_losses.append(loss)
                train_accs.append(acc)
                self.metrics.record('local_train_time', client.local_train_time)
        else:
            for client in selected_clients:
                update, loss, acc = client.local_train(encrypted_global_params=encrypted_global, round_num=round_num)
                client_updates.append(update)
                client_weights.append(client.get_weight())
                train_losses.append(loss)
                train_accs.append(acc)
                self.metrics.record('local_train_time', client.local_train_time)

        # ==== 攻击：DoS 类先剔除本轮不参与的恶意客户端，其余在明文域篡改更新 ====
        # （攻击发生在客户端本地、加密上传之前——即"客户端被攻破/恶意客户端"威胁模型；
        #   数据投毒已在初始化时替换 train_loader，此处无需处理；
        #   隐私推断不修改更新，见初始化时的提示。）
        selected_clients, client_updates, client_weights, train_losses, train_accs = \
            self._apply_attacks_to_updates(
                round_num, selected_clients, client_updates,
                client_weights, train_losses, train_accs)

        # ==== 全局掩码投票（隐私安全：客户端本地算 mask，只上传 mask）====
        gmv_mode = self.config.get('global_mask_vote', {}).get('mode', 'none')
        sparsity_cfg = self.config.get('ckks', {}).get('sparsification', {})
        sparsity_enabled = sparsity_cfg.get('strategy', 'none') not in ('none', None)

        # 1) 客户端本地计算 mask（不上传明文梯度）
        client_masks = []
        if sparsity_enabled:
            for client, update in zip(selected_clients, client_updates):
                mask = client.compute_local_mask(update, client.ckks_manager, round_num)
                client_masks.append(mask)

        # 2) 聚合全局 batch 级 mask（层次模式：边缘先合并部分计数，云再合并）
        global_mask = None
        if gmv_mode != 'none' and client_masks:
            packing = self.server_ckks_manager.packing_strategy if self.server_ckks_manager else None
            values_per_chunk = packing.get_values_per_chunk() if packing else 8192
            total_params = sum(p.numel() for p in self.server.model.parameters())
            num_batches = (total_params + values_per_chunk - 1) // values_per_chunk
            if self.hierarchy is not None:
                mask_by_cid = {c.client_id: m for c, m in zip(selected_clients, client_masks)}
                global_mask = self.hierarchy.vote_global_mask(
                    mask_by_cid, num_batches=num_batches, round_num=round_num)
            else:
                global_mask = self.server.run_global_mask_vote(
                    client_masks=client_masks,
                    num_batches=num_batches,
                    round_num=round_num,
                )

            # 记录每轮 batch 索引详情（用于分析通信开销）
            self._log_batch_indices(round_num, selected_clients, client_masks, global_mask)

        encrypted_updates, client_upload_sizes = [], []
        total_plaintext_size, total_ciphertext_size = 0, 0
        for client, update in zip(selected_clients, client_updates):
            encrypted = client.encrypt_update(
                update, client.ckks_manager, round_num=round_num,
                global_mask=global_mask,
            )
            encrypted_updates.append(encrypted)
            self.metrics.record('encryption_time', client.encryption_time)
            self.metrics.record('plaintext_size', client.plaintext_size)
            self.metrics.record('ciphertext_size', client.ciphertext_size)
            self.metrics.record('num_ciphertexts', client.num_ciphertexts)
            total_plaintext_size += client.plaintext_size
            total_ciphertext_size += client.ciphertext_size
            client_upload_sizes.append(client.ciphertext_size)
            self.client_participation[client.client_id] += 1
            self.client_total_ciphertexts[client.client_id] += client.num_ciphertexts

        # 记录聚合密文数量（标准 CKKS：聚合不改变密文大小）
        if selected_clients:
            self.metrics.record('aggregated_ciphertexts', selected_clients[0].num_ciphertexts)

        if total_plaintext_size > 0:
            expansion_ratio = total_ciphertext_size / total_plaintext_size
            self.metrics.record('expansion_ratio', expansion_ratio)
            tag_info(LogTag.ENCRYPT, f"Expansion ratio: {expansion_ratio:.2f}x  |  "
                     f"total_plain={fmt_size(total_plaintext_size)}  |  "
                     f"total_cipher={fmt_size(total_ciphertext_size)}")

        # 聚合：标准路径（客户端密文→服务器）或层次路径（客户端密文→边缘→服务器）
        if self.hierarchy is not None:
            enc_by_cid = {c.client_id: e for c, e in zip(selected_clients, encrypted_updates)}
            w_by_cid = {c.client_id: w for c, w in zip(selected_clients, client_weights)}
            edge_updates, edge_weights, edge_sizes = self.hierarchy.aggregate_edges(
                enc_by_cid, w_by_cid, round_num=round_num)
            # normalize=False：边缘密文已按全局权重预缩放，云只做加法合并
            # （避免二次密文×标量导致 CKKS 乘法深度耗尽）
            aggregated_update = self.server.aggregate_encrypted_updates(
                edge_updates, edge_weights, self.server_ckks_manager, edge_sizes,
                normalize=False)
        else:
            aggregated_update = self.server.aggregate_encrypted_updates(
                encrypted_updates, client_weights, self.server_ckks_manager, client_upload_sizes)
        self.server.update_global_model(aggregated_update)

        # 评估：门限轨道用仪式解密出的明文全局模型做明文评估；否则由
        # 客户端解密全局模型并完成评估后上报（服务器不持有私钥）
        criterion = nn.CrossEntropyLoss()
        eval_client = selected_clients[0]
        if self.threshold_cfg.enabled:
            # 聚合完成后再做一次仪式解密，评估明文全局模型（服务器全程盲）
            enc_eval = self.server.get_encrypted_global_params()
            eval_params, eval_stats = \
                self.threshold_decryptor.decrypt_global_params(
                    enc_eval, [c.client_id for c in selected_clients])
            eval_client.set_model_params(eval_params)
            test_loss, test_acc = evaluate(
                eval_client.model, self.test_loader, criterion, self.device)
            # 每轮两次仪式（训练前 + 评估前）的开销合并计入本轮指标
            threshold_stats['ceremony_bytes_per_round'] += \
                eval_stats['ceremony_bytes_per_round']
            threshold_stats['ceremony_time_sec'] += \
                eval_stats['ceremony_time_sec']
        else:
            test_loss, test_acc = eval_client.evaluate_encrypted(
                self.server.get_encrypted_global_params(), self.test_loader, criterion)
        self.server.record_evaluation(test_loss, test_acc)

        if threshold_stats is not None:
            self.metrics.record('threshold_ceremony_bytes',
                                threshold_stats['ceremony_bytes_per_round'])
            self.metrics.record('threshold_ceremony_time',
                                threshold_stats['ceremony_time_sec'])
            self.metrics.record('threshold_ideal',
                                int(threshold_stats['ideal']))
            if not self._threshold_dist_recorded:
                # 首轮额外记录 Setup 分发开销（每客户端份额字节数）
                self.metrics.record('threshold_dist_bytes',
                                    threshold_stats['dist_bytes_per_client'])
                self._threshold_dist_recorded = True

        self.metrics.record('train_loss', np.mean(train_losses))
        self.metrics.record('train_acc', np.mean(train_accs))
        self.metrics.record('test_loss', test_loss)
        self.metrics.record('test_acc', test_acc)
        self.metrics.record('aggregation_time', self.server.aggregation_time)
        self.metrics.record('upload_cost', self.server.upload_cost)
        self.metrics.record('download_cost', self.server.download_cost)
        self.metrics.record('total_communication_cost', self.server.communication_cost)
        self.metrics.record('communication_rounds', 1)
        self.metrics.record('cpu_utilization', psutil.cpu_percent())
        self.metrics.record('memory_usage', psutil.virtual_memory().percent)

        round_time = time.time() - round_start
        self.metrics.record('total_round_time', round_time)

        # 计算模拟 wall-clock 时间（使用 bandwidth_simulator 模拟异构网络）
        simulated_download_time = 0.0
        simulated_upload_time = 0.0
        simulated_compute_time = 0.0
        simulated_round_duration = 0.0
        for client in selected_clients:
            if self.server.encrypted_global_state is not None:
                client_dl_bits = self.server_ckks_manager.compute_encrypted_update_size(self.server.encrypted_global_state) * 8
                client_ul_bits = client.ciphertext_size * 8
            else:
                client_dl_bits = self.server.compute_download_cost() * 8
                client_ul_bits = client.ciphertext_size * 8

            if self.bandwidth_simulator is not None:
                client_dl = self.bandwidth_simulator.estimate_download_time(client.client_id, client_dl_bits)
                client_ul = self.bandwidth_simulator.estimate_upload_time(client.client_id, client_ul_bits)
            else:
                # fallback 到固定带宽
                dl_bw_bits = 1024 * 1000
                ul_bw_bits = 512 * 1000
                client_dl = client_dl_bits / dl_bw_bits if dl_bw_bits > 0 else 0.0
                client_ul = client_ul_bits / ul_bw_bits if ul_bw_bits > 0 else 0.0
            client_total = client.local_train_time + client_dl + client_ul
            simulated_round_duration = max(simulated_round_duration, client_total)
            simulated_download_time = max(simulated_download_time, client_dl)
            simulated_upload_time = max(simulated_upload_time, client_ul)
            simulated_compute_time = max(simulated_compute_time, client.local_train_time)
        # 服务器聚合时间已包含在真实时间中，暂不额外添加
        self.metrics.record('total_round_time_simulated', simulated_round_duration)
        self.metrics.record('upload_time', simulated_upload_time)
        self.metrics.record('download_time', simulated_download_time)

        tag_info(LogTag.ROUND,
                 f"#{round_num + 1} done  |  acc={test_acc:.2f}%  |  "
                 f"real_time={round_time:.2f}s  |  sim_dur={simulated_round_duration:.2f}s  |  "
                 f"comm={fmt_size(self.server.communication_cost)}")

        return {
            'round': round_num + 1,
            'test_acc': test_acc,
            'test_loss': test_loss,
            'round_time': round_time,
            'simulated_round_duration': simulated_round_duration,
        }

    def _apply_attacks_to_updates(self, round_num, selected_clients,
                                  client_updates, *other_lists):
        """
        DoS 类掉线剔除 + 明文域更新篡改（攻击发生在客户端本地、加密上传之前，
        即"客户端被攻破/恶意客户端"威胁模型）。与轨道无关，CKKS/PPBR 两轨共用。

        Returns:
            (selected_clients, client_updates, *other_lists) 过滤后的平行列表
        """
        if self.attack_inst is None or not self.malicious_ids:
            return (selected_clients, client_updates) + other_lists
        lists = other_lists
        if hasattr(self.attack_inst, 'participates'):
            keep = [(c.client_id not in self.malicious_ids)
                    or self.attack_inst.participates(round_num)
                    for c in selected_clients]
            if not all(keep):
                dropped = [c.client_id for c, k in zip(selected_clients, keep) if not k]
                tag_info(LogTag.ATTACK, f"round {round_num + 1}: DoS 掉线 {dropped}")
                selected_clients = [c for c, k in zip(selected_clients, keep) if k]
                client_updates = [u for u, k in zip(client_updates, keep) if k]
                lists = tuple([[v for v, k in zip(lst, keep) if k]
                               for lst in lists])
        from hfl_attacks import ClientUpdateAttack
        if isinstance(self.attack_inst, ClientUpdateAttack) and client_updates:
            from hfl_attacks import AttackContext
            mal_pos = [i for i, c in enumerate(selected_clients)
                       if c.client_id in self.malicious_ids]
            for i in mal_pos:
                ctx = AttackContext(
                    self_update=client_updates[i],
                    all_updates=client_updates,
                    self_index=i,
                    malicious_indices=mal_pos,
                    num_classes=self.config['data']['num_classes'],
                    round_num=round_num)
                client_updates[i] = self.attack_inst.attack(client_updates[i], ctx)
        return (selected_clients, client_updates) + tuple(lists)

    def run_round_ppbr(self, round_num: int) -> Dict:
        """
        运行一轮 PPBR 协议（He 等 IEEE TMC 2026 完整密文路线，双轨架构的 PPBR 轨道）

        流程：云明文广播 w_a（论文设定：全局模型公开）→ 客户端本地训练
              → 攻击注入（与 CKKS 轨道同一威胁模型）→ 算法 2 符号过滤
              → 客户端噪声掩码 + [x]_A + SA 掩码上传 → 边缘 SA 聚合 + [2m']_{Ei}
              → 云两轮点积范数盲化 → 范数中位数筛选 + 盲化裁剪聚合 → denoise 得 m_a
              → 云更新 w_a ← w_a + ζ·m_a（论文 w ← w − ζ·m，符号见 doc/ppbr.md）
              → 明文评估

        信任模型与 CKKS 轨道不同（见 doc/ppbr.md）：云持 sk_A（论文原样），
        每个边缘自持 sk_{Ei}，客户端不持任何服务器密钥；不走全局掩码投票、
        TopK 稀疏化与单密钥 CKKS 密文聚合。
        """
        log_round_header(round_num + 1, self.config['system']['num_rounds'],
                         mode="PPBR")
        round_start = time.time()

        selected_clients = self._select_clients()

        # 云明文广播全局模型（论文设定：w_a 公开，任何客户端可得）
        global_params = get_model_params(self.server.model)
        client_updates, train_losses, train_accs = [], [], []
        for client in selected_clients:
            update, loss, acc = client.local_train(
                global_params=global_params, round_num=round_num)
            client_updates.append(update)
            train_losses.append(loss)
            train_accs.append(acc)
            self.metrics.record('local_train_time', client.local_train_time)

        # 攻击注入（与 CKKS 轨道相同语义：客户端本地、加密上传之前）
        client_weights = [1.0] * len(selected_clients)
        selected_clients, client_updates, _, train_losses, train_accs = \
            self._apply_attacks_to_updates(
                round_num, selected_clients, client_updates,
                client_weights, train_losses, train_accs)

        # ==== PPBR 完整协议（算法 2/3/4/5，见 ppbr/protocol.py 时序注释）====
        updates_by_cid = {c.client_id: u for c, u in
                          zip(selected_clients, client_updates)}
        m_a = {k: torch.zeros_like(v)
               for k, v in get_model_params(self.server.model).items()}
        if updates_by_cid:
            groups = self.hierarchy.group_clients(
                [c.client_id for c in selected_clients], round_num)
            m_a = self.ppbr.run_round(round_num, updates_by_cid, groups,
                                      num_edges=self.hierarchy.num_edges)
            st = self.ppbr.last_stats.get('edges', {})
            # PPBR 线上传输审计（块数）：累积进 metrics，供最终汇总打印
            wire = self.ppbr.last_wire
            self.metrics.record('ppbr_client_x_blocks',
                                wire.get('client_x_blocks', 0))
            self.metrics.record('ppbr_edge_xsum_blocks',
                                wire.get('edge_xsum_blocks', 0))
            self.metrics.record('ppbr_edge_2mp_blocks',
                                wire.get('edge_2mp_blocks', 0))
            self.metrics.record('ppbr_norm_products',
                                wire.get('norm_products', 0))
            self.metrics.record('ppbr_sa_early_dropped',
                                wire.get('sa_early_dropped', 0))
            self.metrics.record('ppbr_sa_late_dropped',
                                wire.get('sa_late_dropped', 0))
            tag_info(LogTag.ATTACK,
                     f"round {round_num + 1}: PPBR 云筛选 "
                     f"edges={sorted(st.get('trusted', []))} "
                     f"M={st.get('M', 0):.4f} "
                     f"clip={ {e: round(x, 3) for e, x in st.get('clip', {}).items()} }")

        # 云更新全局模型：w_a ← w_a + ζ·m_a（对应算法 5 的 w ← w − ζ·m——
        # 论文 m 为梯度方向，本项目 Δ 为参数变化量即下降方向，故符号为正）
        zeta = self.ppbr.cfg.global_zeta
        with torch.no_grad():
            for name, p in self.server.model.named_parameters():
                p.add_(m_a[name].to(p.device), alpha=+zeta)

        # 评估：论文设定 w_a 公开，直接对明文全局模型评估
        criterion = nn.CrossEntropyLoss()
        test_loss, test_acc = evaluate(
            self.server.model, self.test_loader, criterion, self.device)
        self.server.record_evaluation(test_loss, test_acc)

        self.metrics.record('train_loss',
                            float(np.mean(train_losses)) if train_losses else 0.0)
        self.metrics.record('train_acc',
                            float(np.mean(train_accs)) if train_accs else 0.0)
        self.metrics.record('test_loss', test_loss)
        self.metrics.record('test_acc', test_acc)
        self.metrics.record('communication_rounds', 1)
        self.metrics.record('cpu_utilization', psutil.cpu_percent())
        self.metrics.record('memory_usage', psutil.virtual_memory().percent)

        round_time = time.time() - round_start
        self.metrics.record('total_round_time', round_time)
        self.metrics.record('total_round_time_simulated', 0.0)

        tag_info(LogTag.ROUND,
                 f"#{round_num + 1} done  |  acc={test_acc:.2f}%  |  "
                 f"real_time={round_time:.2f}s  |  mode=PPBR")

        return {
            'round': round_num + 1,
            'test_acc': test_acc,
            'test_loss': test_loss,
            'round_time': round_time,
            'simulated_round_duration': 0.0,
        }

    def run_experiment(self) -> Dict:
        """
        运行完整加密实验

        Returns:
            实验结果
        """
        upload_mode = "Δ" if self.config['system'].get('upload_difference', True) else "Full"
        if self.ppbr is not None:
            upload_mode = "PPBR 协议消息（[x]_A + SAz）"
        blank_line()
        banner("Experiment Start", width=72)
        kv("Mode", "PPBR" if self.ppbr is not None else
           ("CKKS-Encrypted + Threshold" if self.threshold_cfg.enabled
            else "Encrypted"), indent=1)
        kv("Rounds", self.config['system']['num_rounds'], indent=1)
        kv("Upload", upload_mode, indent=1)
        separator(width=72)

        num_rounds = self.config['system']['num_rounds']
        results = []
        # 双轨分发：ppbr.enabled → PPBR 协议轨道；否则 CKKS 加密轨道
        round_fn = (self.run_round_ppbr if self.ppbr is not None
                    else self.run_round_encrypted)

        # 崩溃续跑：从检查点恢复全局模型/PPBR 动量/指标，跳过已完成轮次
        self._resume_round = 0
        self._load_resume_state()
        start_round = self._resume_round
        if start_round >= num_rounds:
            tag_warn(LogTag.SUMMARY,
                     f"resume: 检查点已完成 {start_round} 轮 ≥ 目标 {num_rounds} 轮，"
                     f"无需训练")

        key_dist_time = 0.0
        self.metrics.record('key_distribution_time', key_dist_time)
        num_clients = self.config['system']['num_clients']
        if self.ppbr is None:
            key_size_per_client = self.ckks_manager.get_key_distribution_size()
            self.metrics.record('key_distribution_cost', key_size_per_client * num_clients)
            tag_info(LogTag.INIT, "Unified CKKS key distributed to all clients")
        else:
            # PPBR 轨道：云公钥 pk_A 随 w_a 广播，边缘公钥注册到云，
            # SA 密钥每轮临时生成——无统一密钥分发环节
            self.metrics.record('key_distribution_cost', 0.0)
            tag_info(LogTag.INIT,
                     "PPBR: pk_A 随全局模型广播，边缘公钥注册到云，"
                     "SA 密钥每轮临时生成（无统一密钥分发）")

        try:
            for round_num in range(start_round, num_rounds):
                result = round_fn(round_num)
                results.append(result)

                # 每N轮保存一次模型；999 = 仅保存最终模型
                if (round_num + 1) % self.config['output']['save_interval'] == 0:
                    self._save_model(round_num + 1)
                    # 周期检查点 + 指标落盘：原生崩溃（绕过 try/finally）时
                    # 最多损失 save_interval 轮
                    self._save_checkpoint(round_num + 1)
                    self._save_results()
        except KeyboardInterrupt:
            tag_warn(LogTag.SUMMARY, "实验被用户中断")
        except Exception as e:
            tag_warn(LogTag.SUMMARY, f"实验异常中断: {e}")
            import traceback
            traceback.print_exc()
        finally:
            # 无论是否完成，都保存已产生的中间结果
            self._save_results()
            if results:
                self._save_model(len(results))
            tag_info(LogTag.SUMMARY, f"实验结果已保存至: {self.output_dir}")

        # 终端打印：CKKS 轨道打客户端参与/密文统计；PPBR 轨道打线上传输审计
        mm = self.metrics.get_metrics()
        if self.ppbr is not None:
            blank_line()
            banner("PPBR Wire Statistics", width=72)

            def _sum(key: str) -> int:
                return int(sum(mm.get(key, [])))

            kv("客户端→边缘 [x_{i,j}]_A 密文块", _sum('ppbr_client_x_blocks'), indent=1)
            kv("边缘→云 [x_i]_A 密文块", _sum('ppbr_edge_xsum_blocks'), indent=1)
            kv("边缘→云 [2m'_i]_{Ei} 密文块", _sum('ppbr_edge_2mp_blocks'), indent=1)
            kv("云→边缘盲化乘积密文块", _sum('ppbr_norm_products'), indent=1)
            kv("SA early 掉线（输入不计入）", _sum('ppbr_sa_early_dropped'), indent=1)
            kv("SA late 掉线（输入仍计入）", _sum('ppbr_sa_late_dropped'), indent=1)
        else:
            blank_line()
            banner("Client Participation", width=72)
            headers = ["Client", "Participated", "Total CTs"]
            rows = []
            for cid in sorted(self.client_participation.keys()):
                ct_count = self.client_total_ciphertexts.get(cid, 0)
                rows.append([cid, self.client_participation[cid], ct_count])
            table(headers, rows, col_widths=[8, 14, 12])

            blank_line()
            section("Communication & Ciphertext Formulas", emoji="📐")
            tree([
                "plaintext_size  = Σ(v.numel() × 4)",
                "ciphertext_size = Σ(len(ct.serialize()))",
                "num_ciphertexts = Σ(len(v) for v in encrypted_update.values())",
                "upload_cost     = Σ(ciphertext_size of selected)",
                "download_cost   = global_model_size × 4 × num_selected",
                "total_comm      = upload_cost + download_cost",
                "expansion_ratio = total_ciphertext_size / total_plaintext_size",
            ], indent=1)

        blank_line()
        banner("Experiment Complete", width=72)

        return {
            'results': results,
            'metrics': self.metrics.get_metrics()
        }

    def _update_experiment_registry(self):
        """将当前实验摘要追加到 results/experiments_registry.json"""
        registry_path = os.path.join(self.config['output']['results_dir'], "experiments_registry.json")
        entries = []
        if os.path.exists(registry_path):
            with open(registry_path, 'r', encoding='utf-8') as f:
                try:
                    entries = json.load(f)
                except json.JSONDecodeError:
                    entries = []

        m = self.metrics.get_metrics()
        is_encrypted = len(m.get('ciphertext_size', [])) > 0
        if getattr(self, 'ppbr', None) is not None:
            mode = "ppbr"
        else:
            mode = "encrypted" if is_encrypted else "plain"

        entry = {
            "timestamp": self.timestamp,
            "output_dir": self.output_dir,
            "mode": mode,
            "config_summary": {
                "dataset": self.config['data']['dataset'],
                "model": self.config['model']['name'],
                "num_clients": self.config['system']['num_clients'],
                "num_rounds": self.config['system']['num_rounds'],
                "client_sampling_rate": self.config['system'].get('client_sampling_rate', 1.0),
                "local_epochs": self.config['system']['num_epochs'],
                "batch_size": self.config['system']['batch_size'],
                "learning_rate": self.config['system']['learning_rate'],
                "iid": self.config['data']['iid'],
                "device": self.config['system']['device'],
                "upload_difference": self.config['system'].get('upload_difference', True),
                "slot_packing": self.config.get('ckks', {}).get('slot_packing', True) if is_encrypted else None,
                "poly_modulus_degree": self.config.get('ckks', {}).get('poly_modulus_degree') if is_encrypted else None,
            },
            "metrics_summary": {
                "final_test_acc": m['test_acc'][-1] if m['test_acc'] else 0.0,
                "final_test_loss": m['test_loss'][-1] if m['test_loss'] else 0.0,
                "total_time_sec": sum(m.get('total_round_time', [])),
                "total_communication_mb": sum(m.get('total_communication_cost', [])) / (1024 * 1024),
                "avg_round_time_sec": safe_mean(m.get('total_round_time', [])),
                "avg_upload_kb": safe_mean(m.get('upload_cost', [])) / 1024,
                "avg_download_kb": safe_mean(m.get('download_cost', [])) / 1024,
            }
        }
        if is_encrypted:
            entry["metrics_summary"]["avg_expansion_ratio"] = safe_mean(m.get('expansion_ratio', []))
            entry["metrics_summary"]["avg_encryption_time_sec"] = safe_mean(m.get('encryption_time', []))
            # decryption_time 指标已废弃（服务器本地解密不计入此指标）
            entry["metrics_summary"]["avg_decryption_time_sec"] = 0.0
            entry["metrics_summary"]["key_distribution_mb"] = sum(m.get('key_distribution_cost', [])) / (1024 * 1024)
            entry["metrics_summary"]["total_ciphertext_blocks"] = int(sum(m.get('num_ciphertexts', [])))

        # 去重：同一 timestamp 只保留最新记录
        entries = [e for e in entries if e['timestamp'] != self.timestamp]
        entries.append(entry)

        with open(registry_path, 'w', encoding='utf-8') as f:
            json.dump(entries, f, indent=2, ensure_ascii=False)

        logger.info(f"实验注册表已更新: {registry_path}")

    def _save_model(self, round_num: int):
        """保存模型到按实验时间戳分组的子目录中"""
        model_dir = os.path.join(
            self.config['output']['model_dir'],
            f"models_{self.timestamp}"
        )
        os.makedirs(model_dir, exist_ok=True)

        # 服务器可解密：保存明文模型
        model_path = os.path.join(model_dir, f"model_round_{round_num}.pth")
        torch.save(self.server.model.state_dict(), model_path)
        logger.info(f"明文模型已保存: {model_path}")

    def _save_checkpoint(self, round_num: int):
        """周期检查点：全局模型 + PPBR 动量/随机状态 + 指标（崩溃续跑用）。

        保存 output/checkpoint_round_{N}.pt，N = 已完成轮数。仅 PPBR 轨道
        需要（动量与 rng 是跨轮状态）；CKKS 轨道每轮无状态，模型即全部。
        """
        if self.ppbr is None:
            return
        ckpt = {
            'round': round_num,
            'model': {k: v.detach().cpu() for k, v in
                      self.server.model.state_dict().items()},
            'ppbr_momenta': self.ppbr.controller.momenta,
            'ppbr_rng_state': self.ppbr.rng.get_state(),
            'metrics': self.metrics.get_metrics(),
        }
        path = os.path.join(self.output_dir, f"checkpoint_round_{round_num}.pt")
        torch.save(ckpt, path)
        logger.info(f"检查点已保存（含 PPBR 动量/rng/指标）: {path}")

    def _load_resume_state(self) -> None:
        """从 config.resume.from 目录加载最新检查点并恢复跨轮状态。"""
        resume_cfg = self.config.get('resume') or {}
        src = resume_cfg.get('from')
        if not resume_cfg.get('enabled', False) or not src:
            return
        if self.ppbr is None:
            tag_warn(LogTag.INIT, "resume: 仅 PPBR 轨道支持检查点续跑，忽略")
            return
        ckpts = [f for f in os.listdir(src)
                 if f.startswith('checkpoint_round_') and f.endswith('.pt')]
        if not ckpts:
            tag_warn(LogTag.INIT, f"resume: {src} 中无检查点，从头训练")
            return
        latest = max(ckpts, key=lambda f: int(
            f[len('checkpoint_round_'):-len('.pt')]))
        ckpt = torch.load(os.path.join(src, latest),
                          map_location='cpu', weights_only=False)
        self.server.model.load_state_dict(ckpt['model'])
        self.ppbr.controller.momenta = {
            cid: {k: v.to(self.device) for k, v in m.items()}
            for cid, m in ckpt['ppbr_momenta'].items()}
        self.ppbr.rng.set_state(ckpt['ppbr_rng_state'])
        self.metrics.metrics = ckpt['metrics']
        self._resume_round = int(ckpt['round'])
        tag_info(LogTag.INIT,
                 f"resume: 从 {src} 的 {latest} 续跑"
                 f"（已完成 {self._resume_round} 轮，动量/rng/指标已恢复）")

    def _save_results(self):
        """保存实验结果（JSON + Markdown报告）"""
        # 保留metrics.json供可视化使用
        metrics_path = os.path.join(self.output_dir, "metrics.json")
        self.metrics.save_to_file(metrics_path)

        # 生成可读的Markdown报告
        self._save_markdown_report()

        # 更新实验注册表
        self._update_experiment_registry()

        logger.info(f"结果已保存到: {self.output_dir}")

    def _save_markdown_report(self):
        """生成人类可读的Markdown实验报告（中英双语 + 详细数据）"""
        m = self.metrics.get_metrics()
        num_rounds = len(m.get('test_acc', []))
        is_encrypted = len(m.get('ciphertext_size', [])) > 0
        mode = "CKKS-Fed (Encrypted) / 加密模式" if is_encrypted else "FedAvg (Plaintext) / 明文模式"

        lines = []
        lines.append(f"# CKKS-Fed Experiment Report / 实验报告")
        lines.append(f"")
        lines.append(f"**Experiment Mode / 实验模式:** {mode}  ")
        lines.append(f"**Timestamp / 时间戳:** {self.timestamp}  ")
        lines.append(f"**Output Directory / 输出目录:** `{self.output_dir}`  ")
        lines.append(f"")

        # 配置摘要
        lines.append(f"## 1. Configuration Summary / 配置摘要")
        lines.append(f"")
        lines.append(f"| Parameter / 参数 | Value / 值 | Description / 说明 |")
        lines.append(f"|------------------|-----------|-------------------|")
        lines.append(f"| Dataset / 数据集 | {self.config['data']['dataset']} | 训练数据集名称 |")
        lines.append(f"| Model / 模型 | {self.config['model']['name']} | 神经网络架构 |")
        lines.append(f"| Num Clients / 客户端数 | {self.config['system']['num_clients']} | 参与联邦学习的总客户端数 |")
        lines.append(f"| Num Rounds / 轮数 | {self.config['system']['num_rounds']} | 联邦学习总轮数 |")
        lines.append(f"| Local Epochs / 本地轮数 | {self.config['system']['num_epochs']} | 每轮本地训练epoch数 |")
        lines.append(f"| Batch Size / 批次大小 | {self.config['system']['batch_size']} | 本地训练批次大小 |")
        lines.append(f"| Learning Rate / 学习率 | {self.config['system']['learning_rate']} | 优化器学习率 |")
        lines.append(f"| Client Sampling Rate / 采样率 | {self.config['system'].get('client_sampling_rate', 1.0)} | 每轮参与训练的客户端比例 |")
        lines.append(f"| IID / 独立同分布 | {self.config['data']['iid']} | 数据分布是否IID |")
        lines.append(f"| Device / 设备 | {self.config['system']['device']} | 训练设备(CPU/CUDA) |")
        lines.append(f"| Upload Difference / 上传差值 | {self.config['system'].get('upload_difference', True)} | 是否上传模型差值而非完整权重 |")
        lines.append(f"")

        # CKKS 配置（仅加密实验）
        if is_encrypted:
            lines.append(f"### 1.1 CKKS Parameters / CKKS加密参数")
            lines.append(f"")
            ckks = self.config['ckks']
            lines.append(f"| Parameter / 参数 | Value / 值 | Description / 说明 |")
            lines.append(f"|------------------|-----------|-------------------|")
            lines.append(f"| Poly Modulus Degree / 多项式模数度 | {ckks.get('poly_modulus_degree')} | CKKS多项式环的维度 |")
            lines.append(f"| Coeff Mod Bit Sizes / 系数模数位宽 | {ckks.get('coeff_mod_bit_sizes')} | 系数模数的位宽序列 |")
            lines.append(f"| Global Scale / 全局缩放因子 | {ckks.get('global_scale')} | CKKS编码缩放因子 |")
            lines.append(f"| Slot Packing / 槽打包 | {ckks.get('slot_packing', True)} | 是否启用SIMD槽打包 |")
            lines.append(f"")

        # 关键指标汇总
        lines.append(f"## 2. Key Metrics Summary / 关键指标汇总")
        lines.append(f"")

        final_test_acc = m['test_acc'][-1] if m['test_acc'] else 0.0
        final_test_loss = m['test_loss'][-1] if m['test_loss'] else 0.0

        total_upload_time = sum(m.get('upload_time', []))
        total_download_time = sum(m.get('download_time', []))
        total_sim_comm_time = total_upload_time + total_download_time

        lines.append(f"| Metric / 指标 | Value / 数值 | Description / 含义 |")
        lines.append(f"|---------------|-------------|-------------------|")
        lines.append(f"| Final Test Accuracy / 最终测试准确率 | **{final_test_acc:.2f}%** | 全局模型在测试集上的最终精度 |")
        lines.append(f"| Final Test Loss / 最终测试损失 | {final_test_loss:.4f} | 全局模型在测试集上的交叉熵损失 |")
        # total_round_time 是真实 CPU 时间，total_round_time_simulated 是模拟 wall-clock
        total_real_time = sum(m.get('total_round_time', []))
        total_sim_time = sum(m.get('total_round_time_simulated', []))
        lines.append(f"| Total Real CPU Time / 总真实CPU时间 | {total_real_time:.2f} s | 单机串行执行的实际耗时 |")
        lines.append(f"| Total Simulated Time / 总模拟时间 | {total_sim_time:.2f} s | 含计算与网络传输的模拟 wall-clock 时间 |")
        lines.append(f"| Total Simulated Comm Time / 总模拟通信时间 | {total_sim_comm_time:.2f} s | 纯网络传输时间 = Σ max(client_upload) + Σ max(client_download) |")
        lines.append(f"| Avg Real Round Time / 平均真实轮次时间 | {safe_mean(m.get('total_round_time', [])):.2f} s | 真实 CPU 平均每轮耗时 |")
        lines.append(f"| Avg Sim Round Time / 平均模拟轮次时间 | {safe_mean(m.get('total_round_time_simulated', [])):.2f} s | 模拟 wall-clock 平均每轮耗时 |")

        total_upload = sum(m.get('upload_cost', []))
        total_download = sum(m.get('download_cost', []))
        total_comm = sum(m.get('total_communication_cost', [])) / (1024 * 1024)

        lines.append(f"| Total Communication / 总通信量 | {total_comm:.2f} MB | 所有轮次上传+下载之和 |")
        lines.append(f"| Total Upload / 总上传量 | {total_upload/(1024*1024):.2f} MB / {total_upload_time:.2f} s | 所有轮次上传开销+时间之和 |")
        lines.append(f"| Total Download / 总下载量 | {total_download/(1024*1024):.2f} MB / {total_download_time:.2f} s | 所有轮次下载开销+时间之和 |")
        lines.append(f"| Avg Upload Cost / 平均上传开销 | {safe_mean(m.get('upload_cost', [])) / (1024*1024):.2f} MB | mean(upload_cost)/2^20 |")
        lines.append(f"| Avg Download Cost / 平均下载开销 | {safe_mean(m.get('download_cost', [])) / (1024*1024):.2f} MB | mean(download_cost)/2^20 |")

        if is_encrypted:
            avg_expansion = safe_mean(m.get('expansion_ratio', []))
            avg_enc_time = safe_mean(m.get('encryption_time', []))
            total_ct_blocks = sum(m.get('num_ciphertexts', []))
            total_agg_cts = sum(m.get('aggregated_ciphertexts', []))
            total_plain = sum(m.get('plaintext_size', []))
            total_cipher = sum(m.get('ciphertext_size', []))
            avg_single_ct = total_cipher / total_ct_blocks if total_ct_blocks > 0 else 0

            # 标准 CKKS 聚合不改变密文大小
            avg_agg_ct_size = avg_single_ct

            lines.append(f"| Avg Expansion Ratio / 平均密文膨胀率 | {avg_expansion:.2f}x | 密文大小相对明文的平均倍数 |")
            lines.append(f"| Avg Encryption Time / 平均加密时间 | {avg_enc_time:.3f} s/client | 每个客户端加密模型更新的平均耗时 |")
            lines.append(f"| Total CT Blocks (Upload) / 总密文上传块数 | {total_ct_blocks} blocks | 所有客户端所有轮次上传的密文块总数 |")
            lines.append(f"| Total Aggregated CTs (Download) / 总聚合密文下载数 | {total_agg_cts} blocks | 服务器聚合后广播的密文块总数 |")
            lines.append(f"| Avg Single Ciphertext Size / 平均单密文大小 | {avg_single_ct:.0f} bytes | 单个客户端密文块的平均大小 |")
            lines.append(f"| Avg Aggregated CT Size / 平均聚合密文大小 | {avg_agg_ct_size:.0f} bytes | 服务器聚合后密文块的平均大小 |")
            lines.append(f"| Key Distribution Cost / 密钥分发开销 | {sum(m.get('key_distribution_cost', [])) / (1024*1024):.2f} MB | CKKS公钥/Galois密钥分发总字节数 |")
            lines.append(f"| CT Upload Time / 密文上传时间 | {total_upload_time:.2f} s | 最慢客户端上传密文的模拟 wall-clock 时间之和 |")
            lines.append(f"| CT Download Time / 密文下载时间 | {total_download_time:.2f} s | 最慢客户端下载密文的模拟 wall-clock 时间之和 |")

        lines.append(f"")

        # 客户端参与统计 / PPBR 线上传输审计
        if getattr(self, 'ppbr', None) is not None:
            lines.append(f"## 3. PPBR Wire Statistics / PPBR 线上传输审计（密文块数）")
            lines.append(f"")
            lines.append(f"| Round / 轮次 | 客户端→边缘 [x]_A 块 | 边缘→云 [x_i]_A 块 | "
                         f"边缘→云 [2m'_i] 块 | 云→边缘盲化乘积块 | SA early 掉线 | SA late 掉线 |")
            lines.append(f"|---|---|---|---|---|---|---|")
            for r in range(num_rounds):
                def _at(k):
                    v = m.get(k, [])
                    return v[r] if r < len(v) else 0
                lines.append(
                    f"| {r+1} | {_at('ppbr_client_x_blocks')} | {_at('ppbr_edge_xsum_blocks')} | "
                    f"{_at('ppbr_edge_2mp_blocks')} | {_at('ppbr_norm_products')} | "
                    f"{_at('ppbr_sa_early_dropped')} | {_at('ppbr_sa_late_dropped')} |")
            lines.append(f"")
        else:
            lines.append(f"## 3. Client Participation & Statistics / 客户端参与统计与详情")
            lines.append(f"")
            lines.append(f"| CID | Part / 参与轮次 | CTs / 密文数 | DL BW / 下行带宽 | UL BW / 上行带宽 |")
            lines.append(f"|-----|----------------|--------------|------------------|------------------|")
            for cid in sorted(self.client_participation.keys()):
                ct_count = self.client_total_ciphertexts.get(cid, 0) if is_encrypted else 0
                dl_bw = "-"
                ul_bw = "-"
                if self.bandwidth_simulator is not None:
                    dl_bw = f"{self.bandwidth_simulator.get_dl_bw_kbps(cid):.0f} kbps"
                    ul_bw = f"{self.bandwidth_simulator.get_ul_bw_kbps(cid):.0f} kbps"
                lines.append(f"| {cid} | {self.client_participation[cid]} | {ct_count} | {dl_bw} | {ul_bw} |")
            lines.append(f"")

        # 每轮详细结果
        lines.append(f"## 4. Per-Round Results / 每轮详细结果")
        lines.append(f"")
        header = "| Round / 轮次 | Test Acc / 测试准确率 | Test Loss / 测试损失 | Round Time / 轮次耗时 | Upload Time / 上传时间 | Download Time / 下载时间 | Comm / 通信量 | CT Uploads / 密文上传数 | CT Downloads / 聚合密文数 |"
        if is_encrypted:
            header += " Encrypt / 加密耗时 | Expansion / 膨胀率 |"
        lines.append(header)

        sep = "|--------------|----------------------|---------------------|----------------------|----------------------|-----------------------|---------------|--------------------------|--------------------------|"
        if is_encrypted:
            sep += "---------------------|---------------------|"
        lines.append(sep)

        for r in range(num_rounds):
            acc = m['test_acc'][r] if r < len(m['test_acc']) else 0.0
            loss = m['test_loss'][r] if r < len(m['test_loss']) else 0.0
            # 统一使用模拟 wall-clock 时间作为 Round Time
            rt = m['total_round_time_simulated'][r] if r < len(m.get('total_round_time_simulated', [])) else 0.0
            ul_t = m['upload_time'][r] if r < len(m.get('upload_time', [])) else 0.0
            dl_t = m['download_time'][r] if r < len(m.get('download_time', [])) else 0.0
            comm = m['total_communication_cost'][r] / (1024*1024) if r < len(m['total_communication_cost']) else 0.0
            ct_up = m['num_ciphertexts'][r] if r < len(m.get('num_ciphertexts', [])) else 0
            ct_down = m['aggregated_ciphertexts'][r] if r < len(m.get('aggregated_ciphertexts', [])) else 0
            row = f"| {r+1} | {acc:.2f}% | {loss:.4f} | {rt:.2f}s | {ul_t:.2f}s | {dl_t:.2f}s | {comm:.2f} MB | {ct_up} | {ct_down} |"

            if is_encrypted:
                enc_t = m['encryption_time'][r] if r < len(m['encryption_time']) else 0.0
                exp = m['expansion_ratio'][r] if r < len(m['expansion_ratio']) else 0.0
                row += f" {enc_t:.3f}s | {exp:.2f}x |"

            lines.append(row)

        lines.append(f"")

        # 通信开销与密文块数计算详情（带数值代入）
        lines.append(f"## 5. Communication & Ciphertext Details / 通信与密文计算详情")
        lines.append(f"")

        total_plain = sum(m.get('plaintext_size', []))
        total_upload = sum(m.get('upload_cost', []))
        total_download = sum(m.get('download_cost', []))
        total_comm = sum(m.get('total_communication_cost', []))
        global_model_size_bytes = self.server.compute_download_cost() if hasattr(self, 'server') else 0
        # total_selected_clients = sum of all client participations (more accurate than total_download / model_size)
        total_selected_clients = sum(self.client_participation.values()) if hasattr(self, 'client_participation') else 0

        lines.append(f"### 5.1 Formula Definitions / 公式定义")
        lines.append(f"")
        lines.append(f"| # | Metric / 指标 | Formula / 公式 |")
        lines.append(f"|---|---------------|----------------|")
        if getattr(self, 'ppbr', None) is not None:
            lines.append(f"| 1 | 客户端→边缘上传 / Client→Edge | `[x_{{i,j}}]_A`（云公钥分块密文）+ `z`（SA 掩码） |")
            lines.append(f"| 2 | 边缘→云上传 / Edge→Cloud | `[x_i]_A = Σ_j [x_{{i,j}}]_A`、`[2m'_i]_{{Ei}}`（边缘公钥，云解不开） |")
            lines.append(f"| 3 | 云→边缘下发 / Cloud→Edge | `[v]_{{Ei}} = [2m'_i]_{{Ei}} ∘ (r_i·x_i)` 盲化乘积密文 |")
            lines.append(f"| 4 | SA 掉线恢复 / Dropout recovery | Shamir (t,n−1) 份额重建 DH 私钥与自负载种子（early 不计入 / late 计入） |")
            lines.append(f"| 5 | 全局更新 / Global update | `w_a ← w_a + ζ·m_a`，`m_a` 经去噪 `Σ x''_i(m'_i+x_i)/l'` |")
        elif is_encrypted:
            lines.append(f"| 1 | Plaintext Size / 明文大小 | `sum(v.numel() * 4 for v in model_update.values())` |")
            lines.append(f"| 2 | Ciphertext Size / 密文大小 | `sum(len(ct.serialize()) for ct in encrypted_update.values())` |")
            lines.append(f"| 3 | Ciphertext Blocks / 密文块数 | `sum(len(v) for v in encrypted_update.values())` |")
            lines.append(f"| 4 | Upload Cost / 上传开销 | `sum(ciphertext_size of selected clients)` |")
            lines.append(f"| 5 | Download Cost / 下载开销 | `global_model_size_bytes * num_selected_clients` |")
            lines.append(f"| 6 | Total Communication / 总通信 | `upload_cost + download_cost` |")
            lines.append(f"| 7 | Expansion Ratio / 膨胀率 | `total_ciphertext_size / total_plaintext_size` |")
        else:
            lines.append(f"| 1 | Plaintext Size / 明文大小 | `sum(v.numel() * 4 for v in model_update.values())` |")
            lines.append(f"| 2 | Upload Cost / 上传开销 | `sum(plaintext_size of selected clients)` |")
            lines.append(f"| 3 | Download Cost / 下载开销 | `global_model_size_bytes * num_selected_clients` |")
            lines.append(f"| 4 | Total Communication / 总通信 | `upload_cost + download_cost` |")
        lines.append(f"")

        lines.append(f"### 5.2 Substituted Values / 数值代入")
        lines.append(f"")
        lines.append(f"| # | Metric / 指标 | Formula / 代入公式 | Result / 结果 |")
        lines.append(f"|---|---------------|-------------------|---------------|")
        if is_encrypted:
            total_cipher = sum(m.get('ciphertext_size', []))
            total_blocks = sum(m.get('num_ciphertexts', []))
            total_agg_cts = sum(m.get('aggregated_ciphertexts', []))
            avg_exp = safe_mean(m.get('expansion_ratio', [0]))
            avg_single_ct = total_cipher / total_blocks if total_blocks > 0 else 0
            # 标准 CKKS 聚合不改变密文大小
            avg_agg_ct_size = avg_single_ct
            lines.append(f"| 1 | Total Plaintext / 总明文 | `sum(plaintext_size) = {total_plain}` | {total_plain/(1024*1024):.2f} MB |")
            lines.append(f"| 2 | Total Ciphertext / 总密文 | `sum(ciphertext_size) = {total_cipher}` | {total_cipher/(1024*1024):.2f} MB |")
            lines.append(f"| 3 | Total CT Blocks (Upload) / 总密文上传块 | `sum(num_ciphertexts) = {total_blocks}` | {total_blocks} blocks |")
            lines.append(f"| 4 | Total Aggregated CTs (Download) / 总聚合密文 | `sum(aggregated_ciphertexts) = {total_agg_cts}` | {total_agg_cts} blocks |")
            lines.append(f"| 5 | Avg Single CT Size / 平均单密文大小 | `{total_cipher}/{total_blocks} = {avg_single_ct:.0f}` | {avg_single_ct/(1024):.2f} KB |")
            lines.append(f"| 6 | Avg Aggregated CT Size / 平均聚合密文大小 | `{avg_single_ct:.0f}` | {avg_agg_ct_size/(1024):.2f} KB |")
            lines.append(f"| 7 | Total Upload / 总上传 | `sum(upload_cost) = {total_upload}` | {total_upload/(1024*1024):.2f} MB |")
            lines.append(f"| 8 | Total Download / 总下载 | `model_size({global_model_size_bytes}) * selected({total_selected_clients}) = {total_download}` | {total_download/(1024*1024):.2f} MB |")
            lines.append(f"| 9 | Total Communication / 总通信 | `{total_upload} + {total_download} = {total_comm}` | {total_comm/(1024*1024):.2f} MB |")
            lines.append(f"| 10 | Avg Expansion / 平均膨胀率 | `mean(expansion_ratio) = {avg_exp:.4f}` | {avg_exp:.2f}x |")
        else:
            lines.append(f"| 1 | Total Plaintext / 总明文 | `sum(plaintext_size) = {total_plain}` | {total_plain} B ({total_plain/1024:.2f} KB) |")
            lines.append(f"| 2 | Total Upload / 总上传 | `sum(upload_cost) = {total_upload}` | {total_upload} B ({total_upload/1024:.2f} KB) |")
            lines.append(f"| 3 | Total Download / 总下载 | `model_size({global_model_size_bytes}) * selected({total_selected_clients}) = {total_download}` | {total_download} B ({total_download/1024:.2f} KB) |")
            lines.append(f"| 4 | Total Communication / 总通信 | `{total_upload} + {total_download} = {total_comm}` | {total_comm} B ({total_comm/1024:.2f} KB) |")
        lines.append(f"")

        # 结论
        lines.append(f"## 6. Conclusion / 结论")
        lines.append(f"")
        if is_encrypted:
            lines.append(f"- **Privacy / 隐私性:** 本实验采用 **CKKS 同态加密** 实现隐私保护联邦学习，服务器始终无法访问客户端原始模型更新。")
            lines.append(f"- **Accuracy / 准确率:** 经过 **{num_rounds}** 轮训练，最终测试准确率达到 **{final_test_acc:.2f}%**，测试损失为 **{final_test_loss:.4f}**。")
            lines.append(f"- **Communication Overhead / 通信开销:** CKKS 密文引入 **{avg_expansion:.2f}x** 的通信膨胀率。")
            lines.append(f"- **Encryption Cost / 加密开销:** 平均每轮每个客户端加密耗时 **{avg_enc_time:.3f}s**。")
        else:
            lines.append(f"- **Baseline / 基线:** 本实验作为标准 FedAvg 明文基线。")
            lines.append(f"- **Accuracy / 准确率:** 经过 **{num_rounds}** 轮训练，最终测试准确率达到 **{final_test_acc:.2f}%**。")
            lines.append(f"- **Communication / 通信量:** 总通信开销为 **{total_comm:.2f} MB**。")
        lines.append(f"")

        report_path = os.path.join(self.output_dir, "report.md")
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write("\n".join(lines))

        logger.info(f"Markdown报告已保存: {report_path}")


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

    with open(args.config, "r", encoding="utf-8") as f:
        main_config = yaml.safe_load(f)

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
    section("🚀 启动标准 CKKS-Fed 加密实验", emoji="")
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

    if enc_metrics.get('expansion_ratio'):
        blank_line()
        section("🔐 CKKS 加密指标汇总", emoji="")
        kv("密文膨胀率", f"{safe_mean(enc_metrics.get('expansion_ratio', [])):.2f}x", indent=1)
        kv("平均加密时间", f"{safe_mean(enc_metrics.get('encryption_time', [])):.3f} s/客户端", indent=1)


if __name__ == '__main__':
    main()
