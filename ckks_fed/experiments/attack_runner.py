"""
攻击实验统一运行器
支持矩阵式实验：多种攻击 x 多种防御（仅加密模式）
"""

import torch
import torch.nn as nn
import numpy as np
import yaml
import time
import os
import json
import logging
from datetime import datetime
from typing import Dict, List, Tuple
import psutil
import copy

from ckks_fed.models import create_model, print_model_summary
from ckks_fed.data import create_client_dataloaders, create_poisoned_client_dataloaders, evaluate
from ckks_fed.client import FederatedClient, MaliciousClient
from ckks_fed.server import AggregationServer
from ckks_fed.crypto import CKKSManager, CKKSContext
from ckks_fed.attacks.model_attacks import (
    SignFlippingAttack, ScalingAttack, GaussianNoiseAttack,
    BackdoorModelAttack, MimicAttack, ConstantAttack
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class MetricsCollector:
    """实验指标收集器"""
    
    def __init__(self):
        self.metrics = {
            'local_train_time': [], 'encryption_time': [], 'aggregation_time': [],
            'decryption_time': [], 'total_round_time': [], 'upload_cost': [],
            'download_cost': [], 'total_communication_cost': [], 'plaintext_size': [],
            'ciphertext_size': [], 'expansion_ratio': [], 'train_loss': [],
            'train_acc': [], 'test_loss': [], 'test_acc': [], 'cpu_utilization': [],
            'memory_usage': [], 'communication_rounds': [], 'dropout_rate': [],
            'key_distribution_time': [], 'key_distribution_cost': [],
            'noise_budget': [], 'num_ciphertexts': []
        }
    
    def record(self, key: str, value):
        if key in self.metrics:
            self.metrics[key].append(value)
    
    def get_metrics(self) -> Dict:
        return self.metrics.copy()
    
    def save_to_file(self, filepath: str):
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(self.metrics, f, indent=2, ensure_ascii=False)
        logger.info(f"指标已保存到: {filepath}")


class AttackExperimentRunner:
    """
    攻击实验运行器
    
    运行流程:
    1. 根据 attack_config 生成实验矩阵
    2. 对每个 (攻击, 防御) 组合运行联邦学习
    3. 收集指标并生成对比报告
    """
    
    def __init__(self, config_path: str = "configs/attack_config.yaml",
                 filter_attack: str = None,
                 filter_defense: str = None):
        with open(config_path, 'r', encoding='utf-8') as f:
            self.config = yaml.safe_load(f)
        
        self.filter_attack = filter_attack
        self.filter_defense = filter_defense
        
        seed = self.config['system']['random_seed']
        torch.manual_seed(seed)
        np.random.seed(seed)
        
        self.device = self.config['system']['device']
        if self.device == "cuda" and not torch.cuda.is_available():
            logger.warning("CUDA不可用，切换到CPU")
            self.device = "cpu"
        
        self.timestamp = datetime.now().strftime("Y%m%d_%H%M%S")
        self.output_dir = os.path.join(
            self.config['output']['results_dir'],
            f"attack_experiment_{self.timestamp}"
        )
        os.makedirs(self.output_dir, exist_ok=True)
        import shutil
        shutil.copy(config_path, os.path.join(self.output_dir, "config.yaml"))
        
        self.all_results = []
        self._init_components()
        logger.info(f"攻击实验运行器初始化完成: 输出目录={self.output_dir}")
    
    def _init_components(self):
        logger.info("创建数据加载器...")
        self.client_loaders, self.test_loader = create_client_dataloaders(
            dataset_name=self.config['data']['dataset'],
            num_clients=self.config['system']['num_clients'],
            batch_size=self.config['system']['batch_size'],
            data_dir=self.config['data']['data_dir'],
            iid=self.config['data']['iid'],
            alpha=self.config['data']['alpha']
        )
        
        logger.info("创建全局模型...")
        self.global_model = create_model(self.config['model']['name'], self.config)
        dataset = self.config['data']['dataset']
        dummy_input = (1, 1, 28, 28) if dataset in ["mnist", "fashion_mnist", "emnist"] else (1, 3, 32, 32)
        print_model_summary(self.global_model, dummy_input)
    
    def _build_attack_matrix(self) -> List[Dict]:
        """构建实验矩阵：优先使用显式配置的 experiments 列表，否则回退到全组合"""
        attack_scenarios = self.config.get('attacks', [])
        defense_methods = self.config.get('defenses', [])
        mode = 'encrypted'
        explicit_experiments = self.config.get('experiments', None)
        
        # 构建查找字典
        attack_map = {a['name']: a for a in attack_scenarios}
        defense_map = {d['name']: d for d in defense_methods}
        
        matrix = []
        if explicit_experiments is not None:
            # 显式白名单模式：精确控制每个实验
            for exp in explicit_experiments:
                attack_name = exp.get('attack', 'none')
                defense_name = exp.get('defense', 'fedavg')
                attack_cfg = attack_map.get(attack_name) if attack_name != 'none' else None
                defense_cfg = defense_map.get(defense_name, {'name': defense_name, 'aggregation_rule': 'fedavg', 'anomaly_filter': None, 'params': {}})
                matrix.append({
                    'mode': mode,
                    'attack_name': attack_name,
                    'attack_cfg': attack_cfg,
                    'defense_name': defense_name,
                    'defense_cfg': defense_cfg
                })
            logger.info(f"使用显式实验列表，共 {len(matrix)} 个配置")
        else:
            # 全组合模式（笛卡尔积）
            for attack_cfg in attack_scenarios:
                # 无攻击 baseline
                matrix.append({
                    'mode': mode,
                    'attack_name': 'none',
                    'attack_cfg': None,
                    'defense_name': defense_methods[0]['name'] if defense_methods else 'fedavg',
                    'defense_cfg': defense_methods[0] if defense_methods else {}
                })
                # 每种攻击配合每种防御
                for defense_cfg in defense_methods:
                    matrix.append({
                        'mode': mode,
                        'attack_name': attack_cfg['name'],
                        'attack_cfg': attack_cfg,
                        'defense_name': defense_cfg['name'],
                        'defense_cfg': defense_cfg
                    })
            logger.info(f"使用全组合实验矩阵，共 {len(matrix)} 个配置")
        
        # 应用命令行过滤条件
        if self.filter_attack is not None:
            matrix = [m for m in matrix if m['attack_name'] == self.filter_attack]
            logger.info(f"按攻击过滤后: {len(matrix)} 个配置 (attack={self.filter_attack})")
        if self.filter_defense is not None:
            matrix = [m for m in matrix if m['defense_name'] == self.filter_defense]
            logger.info(f"按防御过滤后: {len(matrix)} 个配置 (defense={self.filter_defense})")
        
        if len(matrix) == 0:
            logger.warning("警告: 过滤后实验矩阵为空，请检查过滤条件是否与配置文件匹配！")
        
        return matrix
    
    def _create_model_attack(self, attack_cfg: Dict):
        """根据配置创建模型攻击实例"""
        if attack_cfg is None:
            return None
        atype = attack_cfg.get('type', 'sign_flipping')
        if atype == 'sign_flipping':
            return SignFlippingAttack(
                flip_rate=attack_cfg.get('flip_rate', 1.0),
                scale=attack_cfg.get('scale', 1.0)
            )
        elif atype == 'scaling':
            return ScalingAttack(scale_factor=attack_cfg.get('scale_factor', 10.0))
        elif atype == 'gaussian_noise':
            return GaussianNoiseAttack(std=attack_cfg.get('std', 1.0))
        elif atype == 'backdoor_model':
            return BackdoorModelAttack(scale_factor=attack_cfg.get('scale_factor', 10.0))
        elif atype == 'mimic_alie':
            return MimicAttack(z=attack_cfg.get('z', 1.5))
        elif atype == 'constant':
            return ConstantAttack(constant_value=attack_cfg.get('constant_value', 0.0))
        else:
            raise ValueError(f"未知的模型攻击类型: {atype}")
    
    def _create_clients(self, attack_cfg: Dict, client_loaders: List):
        """创建客户端列表（区分正常/恶意）"""
        malicious_ids = []
        if attack_cfg is not None:
            mal_cfg = attack_cfg.get('malicious_clients', {})
            count = mal_cfg.get('count', 0)
            if count > 0:
                mode_select = mal_cfg.get('mode', 'random')
                if mode_select == 'first':
                    malicious_ids = list(range(count))
                else:
                    all_ids = list(range(len(client_loaders)))
                    np.random.shuffle(all_ids)
                    malicious_ids = sorted(all_ids[:count])
        
        # 数据攻击配置
        data_attack_configs = []
        if attack_cfg is not None and 'data_attack' in attack_cfg:
            dac = attack_cfg['data_attack'].copy()
            dac['num_classes'] = self.config['data'].get('num_classes', 10)
            data_attack_configs = [dac] * len(malicious_ids)
        
        # 生成有毒数据加载器
        if malicious_ids and data_attack_configs:
            poisoned_loaders = create_poisoned_client_dataloaders(
                client_loaders, malicious_ids, data_attack_configs,
                batch_size=self.config['system']['batch_size']
            )
        else:
            poisoned_loaders = list(client_loaders)
        
        # 模型攻击
        model_attack = self._create_model_attack(attack_cfg)
        
        clients = []
        for i, loader in enumerate(poisoned_loaders):
            client_model = create_model(self.config['model']['name'], self.config)
            if i in malicious_ids:
                client = MaliciousClient(
                    client_id=i, model=client_model, train_loader=loader,
                    config=self.config, device=self.device, model_attack=model_attack
                )
            else:
                client = FederatedClient(
                    client_id=i, model=client_model, train_loader=loader,
                    config=self.config, device=self.device
                )
            clients.append(client)
        
        return clients, malicious_ids
    
    def _run_single_experiment(self, exp_cfg: Dict) -> Dict:
        """运行单个实验配置"""
        mode = exp_cfg['mode']
        attack_name = exp_cfg['attack_name']
        attack_cfg = exp_cfg['attack_cfg']
        defense_name = exp_cfg['defense_name']
        defense_cfg = exp_cfg['defense_cfg']
        
        logger.info("\n" + "="*70)
        logger.info(f"开始实验 | 模式={mode} | 攻击={attack_name} | 防御={defense_name}")
        logger.info("="*70)
        
        # 深拷贝全局模型
        model = copy.deepcopy(self.global_model)
        
        # 组装防御配置
        run_config = copy.deepcopy(self.config)
        run_config['defense'] = defense_cfg
        
        # CKKS 加密（密钥架构：KGC 生成完整上下文含私钥；服务器仅持公钥；客户端持完整上下文）
        master_manager = CKKSManager(run_config['ckks'])
        poly_degree = run_config['ckks'].get('poly_modulus_degree', 8192)
        server_context = CKKSContext.from_full_bytes(
            master_manager.context.get_context_bytes(),
            poly_modulus_degree=poly_degree)
        server_ckks_manager = CKKSManager(run_config['ckks'], context=server_context)

        # 创建服务器并初始化加密全局状态（密文域维护全局模型）
        server = AggregationServer(model, run_config, self.device, server_ckks_manager)
        server.init_encrypted_global_state(server_ckks_manager)

        # 创建客户端并注入完整上下文（含私钥，仅客户端可解密）
        clients, malicious_ids = self._create_clients(attack_cfg, self.client_loaders)
        client_context_bytes = master_manager.context.get_full_context_bytes()
        for client in clients:
            client.ckks_manager = CKKSManager(
                run_config['ckks'],
                context=CKKSContext.from_full_bytes(client_context_bytes,
                                                    poly_modulus_degree=poly_degree))
        logger.info(f"恶意客户端: {malicious_ids}")

        metrics = MetricsCollector()
        num_rounds = run_config['system']['num_rounds']
        results = []

        for round_num in range(num_rounds):
            result = self._run_round_encrypted(
                round_num, server, clients, server_ckks_manager, metrics
            )
            results.append(result)
        
        # 保存指标
        exp_tag = f"{mode}_{attack_name}_{defense_name}"
        safe_tag = exp_tag.replace('/', '_').replace(' ', '_')
        metrics_path = os.path.join(self.output_dir, f"metrics_{safe_tag}.json")
        metrics.save_to_file(metrics_path)
        
        summary = {
            'exp_tag': exp_tag,
            'mode': mode,
            'attack': attack_name,
            'defense': defense_name,
            'malicious_clients': malicious_ids,
            'final_test_acc': results[-1]['test_acc'] if results else 0.0,
            'final_test_loss': results[-1]['test_loss'] if results else 0.0,
            'metrics_path': metrics_path,
            'results': results,
            'metrics': metrics.get_metrics()
        }
        self.all_results.append(summary)
        return summary
    
    def _select_clients(self, clients: List) -> List:
        num_clients = len(clients)
        rate = self.config['system'].get('client_sampling_rate', 1.0)
        num_selected = max(1, int(num_clients * rate))
        selected = np.random.choice(clients, num_selected, replace=False).tolist()
        logger.info(f"本轮选择的客户端: {[c.client_id for c in selected]} ({num_selected}/{num_clients})")
        return selected
    
    def _run_round_encrypted(self, round_num: int, server, clients, server_ckks_manager, metrics) -> Dict:
        logger.info(f"===== 加密轮次 {round_num + 1} =====")
        round_start = time.time()
        selected = self._select_clients(clients)
        encrypted_global = server.get_encrypted_global_params()

        updates = []
        weights = []
        train_losses = []
        train_accs = []

        for client in selected:
            update, loss, acc = client.local_train(
                encrypted_global_params=encrypted_global, round_num=round_num)
            updates.append(update)
            weights.append(client.get_weight())
            train_losses.append(loss)
            train_accs.append(acc)
            metrics.record('local_train_time', client.local_train_time)

        encrypted_updates = []
        upload_sizes = []
        total_plain = 0
        total_cipher = 0
        for client, update in zip(selected, updates):
            encrypted = client.encrypt_update(update, client.ckks_manager, round_num=round_num)
            encrypted_updates.append(encrypted)
            metrics.record('encryption_time', client.encryption_time)
            metrics.record('plaintext_size', client.plaintext_size)
            metrics.record('ciphertext_size', client.ciphertext_size)
            metrics.record('num_ciphertexts', client.num_ciphertexts)
            total_plain += client.plaintext_size
            total_cipher += client.ciphertext_size
            upload_sizes.append(client.ciphertext_size)

        if total_plain > 0:
            metrics.record('expansion_ratio', total_cipher / total_plain)

        aggregated = server.aggregate_encrypted_updates(
            encrypted_updates, weights, server_ckks_manager, upload_sizes
        )
        server.update_global_model(aggregated)

        # 评估：服务器不持私钥，由客户端解密全局模型并完成评估后上报
        criterion = nn.CrossEntropyLoss()
        eval_client = selected[0]
        test_loss, test_acc = eval_client.evaluate_encrypted(
            server.get_encrypted_global_params(), self.test_loader, criterion)
        server.record_evaluation(test_loss, test_acc)
        
        metrics.record('train_loss', np.mean(train_losses))
        metrics.record('train_acc', np.mean(train_accs))
        metrics.record('test_loss', test_loss)
        metrics.record('test_acc', test_acc)
        metrics.record('aggregation_time', server.aggregation_time)
        metrics.record('upload_cost', server.upload_cost)
        metrics.record('download_cost', server.download_cost)
        metrics.record('total_communication_cost', server.communication_cost)
        metrics.record('cpu_utilization', psutil.cpu_percent())
        metrics.record('memory_usage', psutil.virtual_memory().percent)
        
        rt = time.time() - round_start
        metrics.record('total_round_time', rt)
        return {'round': round_num + 1, 'test_acc': test_acc, 'test_loss': test_loss, 'round_time': rt}
    
    def run_all(self):
        """运行实验矩阵中的所有实验"""
        matrix = self._build_attack_matrix()
        logger.info(f"实验矩阵共 {len(matrix)} 个配置")
        for i, exp_cfg in enumerate(matrix):
            logger.info(f"\n>> 进度: {i+1}/{len(matrix)}")
            try:
                self._run_single_experiment(exp_cfg)
            except Exception as e:
                logger.error(f"实验失败: {exp_cfg}，错误: {e}", exc_info=True)
        self._generate_summary_report()
    
    def _generate_summary_report(self):
        """生成所有实验的汇总对比报告"""
        lines = []
        lines.append("# CKKS-Fed 鲁棒性实验汇总报告")
        lines.append(f"**时间戳:** {self.timestamp}  ")
        lines.append(f"**输出目录:** `{self.output_dir}`  ")
        lines.append("")
        lines.append("## 实验配置")
        lines.append(f"- 数据集: {self.config['data']['dataset']}")
        lines.append(f"- 模型: {self.config['model']['name']}")
        lines.append(f"- 客户端数: {self.config['system']['num_clients']}")
        lines.append(f"- 轮数: {self.config['system']['num_rounds']}")
        lines.append("")
        lines.append("## 最终测试准确率对比")
        lines.append("")
        lines.append("| 攻击 | 防御 | 最终准确率 | 最终损失 | 恶意客户端 |")
        lines.append("|------|------|------------|----------|------------|")
        for r in self.all_results:
            lines.append(f"| {r['attack']} | {r['defense']} | "
                        f"{r['final_test_acc']:.2f}% | {r['final_test_loss']:.4f} | {r['malicious_clients']} |")
        lines.append("")
        lines.append("## 关键结论")
        lines.append("")
        attacks = sorted(set(r['attack'] for r in self.all_results if r['attack'] != 'none'))
        for atk in attacks:
            atk_results = [r for r in self.all_results if r['attack'] == atk]
            fedavg_res = [r for r in atk_results if r['defense'] == 'fedavg']
            best_def = max(atk_results, key=lambda x: x['final_test_acc'])
            if fedavg_res:
                lines.append(f"- **{atk}:** FedAvg下准确率 {fedavg_res[0]['final_test_acc']:.2f}%，"
                            f"最佳防御 [{best_def['defense']}] 提升至 {best_def['final_test_acc']:.2f}%")
        
        report_path = os.path.join(self.output_dir, "summary_report.md")
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write("\n".join(lines))
        logger.info(f"\n汇总报告已保存: {report_path}")


def main():
    runner = AttackExperimentRunner("configs/attack_config.yaml")
    runner.run_all()


if __name__ == "__main__":
    main()
