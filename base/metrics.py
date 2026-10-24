"""实验指标收集器（通用，供所有方案轨道共享）"""

import json
import logging
import time
from typing import Dict

logger = logging.getLogger(__name__)


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
