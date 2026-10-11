"""
CKKS-Fed 联邦学习主程序
协调多客户端、聚合服务器和辅助节点
"""

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
from ckks_fed.data import create_client_dataloaders, evaluate, get_dataset_num_classes
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
        self.ckks_manager = CKKSManager(self.config['ckks'].copy())
        from ckks_fed.crypto import CKKSContext
        poly_degree = self.config['ckks'].get('poly_modulus_degree', 8192)
        server_context = CKKSContext.from_full_bytes(
            self.ckks_manager.context.get_context_bytes(),
            poly_modulus_degree=poly_degree)
        self.server_ckks_manager = CKKSManager(self.config['ckks'].copy(), context=server_context)
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
            client.ckks_manager = CKKSManager(self.config['ckks'].copy(), context=context)
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
        # 恶意客户端 = 编号最大的最后 malicious_count 个。
        self.attack_inst = None
        self.malicious_ids = []
        attack_cfg = self.config.get('attack') or {}
        if attack_cfg.get('enabled', False):
            from hfl_attacks import ATTACK_REGISTRY, DataPoisoner, PrivacyAttack
            name = attack_cfg.get('name', '')
            if name not in ATTACK_REGISTRY:
                raise ValueError(
                    f"未知攻击 '{name}'，注册表可选: {sorted(ATTACK_REGISTRY)}")
            self.attack_inst = ATTACK_REGISTRY[name]()
            n_mal = min(int(attack_cfg.get('malicious_count', 1)), len(self.clients))
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
        if self.attack_inst is not None and self.malicious_ids:
            if hasattr(self.attack_inst, 'participates'):
                keep = [(c.client_id not in self.malicious_ids)
                        or self.attack_inst.participates(round_num)
                        for c in selected_clients]
                if not all(keep):
                    dropped = [c.client_id for c, k in zip(selected_clients, keep) if not k]
                    tag_info(LogTag.ATTACK, f"round {round_num + 1}: DoS 掉线 {dropped}")
                    selected_clients = [c for c, k in zip(selected_clients, keep) if k]
                    client_updates = [u for u, k in zip(client_updates, keep) if k]
                    client_weights = [w for w, k in zip(client_weights, keep) if k]
                    train_losses = [l for l, k in zip(train_losses, keep) if k]
                    train_accs = [a for a, k in zip(train_accs, keep) if k]
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

        # 评估：服务器不持私钥，由客户端解密全局模型并完成评估后上报
        criterion = nn.CrossEntropyLoss()
        eval_client = selected_clients[0]
        test_loss, test_acc = eval_client.evaluate_encrypted(
            self.server.get_encrypted_global_params(), self.test_loader, criterion)
        self.server.record_evaluation(test_loss, test_acc)

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

    def run_experiment(self) -> Dict:
        """
        运行完整加密实验

        Returns:
            实验结果
        """
        upload_mode = "Δ" if self.config['system'].get('upload_difference', True) else "Full"
        blank_line()
        banner("Experiment Start", width=72)
        kv("Mode", "Encrypted", indent=1)
        kv("Rounds", self.config['system']['num_rounds'], indent=1)
        kv("Upload", upload_mode, indent=1)
        separator(width=72)

        num_rounds = self.config['system']['num_rounds']
        results = []

        key_dist_time = 0.0
        self.metrics.record('key_distribution_time', key_dist_time)
        num_clients = self.config['system']['num_clients']
        key_size_per_client = self.ckks_manager.get_key_distribution_size()
        self.metrics.record('key_distribution_cost', key_size_per_client * num_clients)
        tag_info(LogTag.INIT, "Unified CKKS key distributed to all clients")

        try:
            for round_num in range(num_rounds):
                result = self.run_round_encrypted(round_num)
                results.append(result)

                # 每N轮保存一次模型
                if (round_num + 1) % self.config['output']['save_interval'] == 0:
                    self._save_model(round_num + 1)
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

        # 终端打印客户端参与统计（表格形式）
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

        # 客户端参与统计
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
        if is_encrypted:
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
