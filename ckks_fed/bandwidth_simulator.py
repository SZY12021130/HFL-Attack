"""
带宽模拟器 (Bandwidth Simulator)
===============================
借鉴 FedFetch 的客户端带宽模型，为每个客户端分配独立的上下行带宽。
在 SIMULATION_MODE 下，用带宽和数据量计算理论下载/上传时间，
替代真实的网络 I/O。

关键公式:
    download_time = data_size_bits / (dl_bw_kbps * 1024)
    upload_time   = data_size_bits / (ul_bw_kbps * 1024)

配置项 (config.yaml -> bandwidth):
    base_dl_bw: 基础下行带宽 (kbps)
    base_ul_bw: 基础上行带宽 (kbps)
    variance:   带宽波动系数 [0, 1)，每个客户端在 base 上下浮动
    distribution: "uniform" | "lognormal" 带宽分布类型
"""

import json
import os
import pickle
import random
import numpy as np
from typing import Dict, Optional


class BandwidthSimulator:
    """
    联邦学习客户端带宽模拟器。

    为每个客户端生成独立的上下行带宽，支持均匀分布和对数正态分布。
    所有时间计算均基于理论带宽，不涉及真实网络传输。
    """

    def __init__(
        self,
        num_clients: int,
        base_dl_bw: float = 1024.0,      # kbps
        base_ul_bw: float = 512.0,       # kbps
        variance: float = 0.3,
        distribution: str = "uniform",
        seed: int = 42,
        fedfetch_trace_path: Optional[str] = None,
        filter_low_bandwidth: bool = False,
        min_dl_bw_kbps: float = 10240.0,   # 10 Mbps
        min_ul_bw_kbps: float = 10240.0,   # 10 Mbps
        max_dl_bw_kbps: float = float('inf'),  # 默认不限制
        max_ul_bw_kbps: float = float('inf'),  # 默认不限制
    ):
        """
        Args:
            num_clients: 客户端总数
            base_dl_bw:  基础下行带宽 (kbps)
            base_ul_bw:  基础上行带宽 (kbps)
            variance:    带宽变异系数，每个客户端在 [base*(1-v), base*(1+v)] 之间
            distribution: "uniform" 或 "lognormal"
            seed:        随机种子，保证实验可复现
            fedfetch_trace_path: FedFetch 真实带宽 trace 文件路径 (pickle 格式)
            filter_low_bandwidth: 是否过滤低带宽客户端（默认 False，保持原有逻辑）
            min_dl_bw_kbps: 最低下行带宽阈值 (kbps)，filter_low_bandwidth=true 时生效
            min_ul_bw_kbps: 最低上行带宽阈值 (kbps)，filter_low_bandwidth=true 时生效
            max_dl_bw_kbps: 最高下行带宽阈值 (kbps)，默认 inf 不限制
            max_ul_bw_kbps: 最高上行带宽阈值 (kbps)，默认 inf 不限制
        """
        random.seed(seed)
        np.random.seed(seed)

        self.num_clients = num_clients
        self.dl_bandwidth: Dict[int, float] = {}   # client_id -> kbps
        self.ul_bandwidth: Dict[int, float] = {}   # client_id -> kbps
        self.distribution = distribution
        self.filter_low_bandwidth = filter_low_bandwidth
        self.min_dl_bw_kbps = min_dl_bw_kbps
        self.min_ul_bw_kbps = min_ul_bw_kbps
        self.max_dl_bw_kbps = max_dl_bw_kbps
        self.max_ul_bw_kbps = max_ul_bw_kbps

        # 如果提供了 FedFetch trace 文件，优先使用真实带宽
        if fedfetch_trace_path and os.path.exists(fedfetch_trace_path):
            self._load_from_fedfetch(fedfetch_trace_path, seed)
            return

        self._generate_synthetic(
            num_clients, base_dl_bw, base_ul_bw, variance, distribution
        )

    def _generate_synthetic(self, num_clients: int, base_dl_bw: float,
                            base_ul_bw: float, variance: float, distribution: str):
        """合成带宽生成，支持低带宽过滤和最高带宽限制。"""
        for cid in range(num_clients):
            dl, ul = self._draw_bandwidth(base_dl_bw, base_ul_bw, variance, distribution)

            # 应用最高带宽限制（始终生效，默认 inf 不限制）
            dl = min(dl, self.max_dl_bw_kbps)
            ul = min(ul, self.max_ul_bw_kbps)

            # 如果开启过滤，重新生成直到满足阈值
            if self.filter_low_bandwidth:
                max_attempts = 10000
                attempts = 0
                while (dl < self.min_dl_bw_kbps or ul < self.min_ul_bw_kbps) \
                        and attempts < max_attempts:
                    dl, ul = self._draw_bandwidth(
                        base_dl_bw, base_ul_bw, variance, distribution
                    )
                    # 重采样后也应用最高限制
                    dl = min(dl, self.max_dl_bw_kbps)
                    ul = min(ul, self.max_ul_bw_kbps)
                    attempts += 1
                if attempts >= max_attempts:
                    raise ValueError(
                        f"无法在 {max_attempts} 次尝试内生成满足带宽要求的客户端 "
                        f"(DL≥{self.min_dl_bw_kbps}, UL≥{self.min_ul_bw_kbps}, "
                        f"DL≤{self.max_dl_bw_kbps}, UL≤{self.max_ul_bw_kbps})。"
                        f"请检查 base_dl_bw/base_ul_bw、variance 和 max 配置是否矛盾。"
                    )

            self.dl_bandwidth[cid] = dl
            self.ul_bandwidth[cid] = ul

    def _draw_bandwidth(self, base_dl_bw: float, base_ul_bw: float,
                        variance: float, distribution: str) -> tuple:
        """抽取一组 DL/UL 带宽，并应用最高限制。"""
        if distribution == "uniform":
            dl = base_dl_bw * (1.0 + random.uniform(-variance, variance))
            ul = base_ul_bw * (1.0 + random.uniform(-variance, variance))
        elif distribution == "lognormal":
            dl = base_dl_bw * max(0.1, np.random.lognormal(0.0, variance))
            ul = base_ul_bw * max(0.1, np.random.lognormal(0.0, variance))
        else:
            dl = base_dl_bw
            ul = base_ul_bw
        # 应用最高带宽限制（始终生效，默认 inf 不限制）
        dl = min(dl, self.max_dl_bw_kbps)
        ul = min(ul, self.max_ul_bw_kbps)
        return dl, ul

    def _load_from_fedfetch(self, trace_path: str, seed: int):
        """从 FedFetch 真实带宽 trace 文件加载带宽数据，支持过滤。"""
        with open(trace_path, 'rb') as f:
            fedfetch_data = pickle.load(f)

        all_entries = list(fedfetch_data.values())

        # 如果开启过滤，先过滤所有 entries
        if self.filter_low_bandwidth:
            all_entries = [
                e for e in all_entries
                if float(e['dl_kbps']) >= self.min_dl_bw_kbps
                and float(e['ul_kbps']) >= self.min_ul_bw_kbps
            ]
            if len(all_entries) < self.num_clients:
                raise ValueError(
                    f"带宽过滤后只剩 {len(all_entries)} 个客户端（要求 "
                    f"DL≥{self.min_dl_bw_kbps} kbps, UL≥{self.min_ul_bw_kbps} kbps），"
                    f"但需要 {self.num_clients} 个客户端。请调低阈值或关闭过滤。"
                )

        random.seed(seed)
        sampled = random.sample(all_entries, min(self.num_clients, len(all_entries)))

        for cid, entry in enumerate(sampled):
            dl = float(entry['dl_kbps'])
            ul = float(entry['ul_kbps'])
            # 应用最高带宽限制
            dl = min(dl, self.max_dl_bw_kbps)
            ul = min(ul, self.max_ul_bw_kbps)
            self.dl_bandwidth[cid] = dl
            self.ul_bandwidth[cid] = ul

        # 如果需要的客户端数超过 FedFetch 的样本数，循环复用
        if self.num_clients > len(sampled):
            for cid in range(len(sampled), self.num_clients):
                entry = sampled[cid % len(sampled)]
                dl = float(entry['dl_kbps']) * (0.9 + 0.2 * random.random())
                ul = float(entry['ul_kbps']) * (0.9 + 0.2 * random.random())
                # 应用最高带宽限制
                dl = min(dl, self.max_dl_bw_kbps)
                ul = min(ul, self.max_ul_bw_kbps)
                self.dl_bandwidth[cid] = dl
                self.ul_bandwidth[cid] = ul

    # ------------------------------------------------------------------
    # 查询接口
    # ------------------------------------------------------------------
    def get_dl_bw_kbps(self, client_id: int) -> float:
        """返回客户端下行带宽 (kbps)"""
        return self.dl_bandwidth.get(client_id, 1024.0)

    def get_ul_bw_kbps(self, client_id: int) -> float:
        """返回客户端上行带宽 (kbps)"""
        return self.ul_bandwidth.get(client_id, 512.0)

    def get_dl_bw_bits(self, client_id: int) -> float:
        """返回客户端下行带宽 (bits/s)，用于时间计算"""
        return self.get_dl_bw_kbps(client_id) * 1024.0

    def get_ul_bw_bits(self, client_id: int) -> float:
        """返回客户端上行带宽 (bits/s)"""
        return self.get_ul_bw_kbps(client_id) * 1024.0

    # ------------------------------------------------------------------
    # 时间计算接口
    # ------------------------------------------------------------------
    def estimate_download_time(self, client_id: int, size_bits: float) -> float:
        """估算下载指定数据量所需的理论时间 (秒)"""
        bw = self.get_dl_bw_bits(client_id)
        return size_bits / bw if bw > 0 else float('inf')

    def estimate_upload_time(self, client_id: int, size_bits: float) -> float:
        """估算上传指定数据量所需的理论时间 (秒)"""
        bw = self.get_ul_bw_bits(client_id)
        return size_bits / bw if bw > 0 else float('inf')

    def get_stats(self) -> Dict:
        """返回带宽统计摘要"""
        dl_vals = list(self.dl_bandwidth.values())
        ul_vals = list(self.ul_bandwidth.values())
        return {
            "dl_bw_mean_kbps": float(np.mean(dl_vals)),
            "dl_bw_min_kbps": float(np.min(dl_vals)),
            "dl_bw_max_kbps": float(np.max(dl_vals)),
            "ul_bw_mean_kbps": float(np.mean(ul_vals)),
            "ul_bw_min_kbps": float(np.min(ul_vals)),
            "ul_bw_max_kbps": float(np.max(ul_vals)),
        }

    # ------------------------------------------------------------------
    # 持久化接口：确保对比实验使用完全相同的带宽
    # ------------------------------------------------------------------
    def to_dict(self) -> Dict:
        """序列化为字典"""
        return {
            "num_clients": self.num_clients,
            "distribution": self.distribution,
            "dl_bandwidth": self.dl_bandwidth,
            "ul_bandwidth": self.ul_bandwidth,
            "filter_low_bandwidth": self.filter_low_bandwidth,
            "min_dl_bw_kbps": self.min_dl_bw_kbps,
            "min_ul_bw_kbps": self.min_ul_bw_kbps,
            "max_dl_bw_kbps": self.max_dl_bw_kbps,
            "max_ul_bw_kbps": self.max_ul_bw_kbps,
        }

    @classmethod
    def from_dict(cls, data: Dict, expected_num_clients: int = None) -> "BandwidthSimulator":
        """从字典反序列化
        
        Args:
            data: 序列化后的字典
            expected_num_clients: 期望的客户端数量，如果不匹配则抛出异常
        """
        saved_num_clients = data.get("num_clients", 0)
        if expected_num_clients is not None and saved_num_clients != expected_num_clients:
            raise ValueError(
                f"Bandwidth config mismatch: saved for {saved_num_clients} clients, "
                f"but current experiment has {expected_num_clients} clients. "
                f"Please delete the old config or it will be auto-regenerated."
            )
        sim = cls.__new__(cls)
        sim.num_clients = saved_num_clients
        sim.distribution = data.get("distribution", "uniform")
        sim.dl_bandwidth = {int(k): float(v) for k, v in data.get("dl_bandwidth", {}).items()}
        sim.ul_bandwidth = {int(k): float(v) for k, v in data.get("ul_bandwidth", {}).items()}
        # 从序列化数据中恢复限制参数（兼容旧数据，默认不限制）
        sim.filter_low_bandwidth = data.get("filter_low_bandwidth", False)
        sim.min_dl_bw_kbps = data.get("min_dl_bw_kbps", 10240.0)
        sim.min_ul_bw_kbps = data.get("min_ul_bw_kbps", 10240.0)
        sim.max_dl_bw_kbps = data.get("max_dl_bw_kbps", float('inf'))
        sim.max_ul_bw_kbps = data.get("max_ul_bw_kbps", float('inf'))
        return sim

    def save(self, filepath: str):
        """保存带宽配置到 JSON 文件"""
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)

    @classmethod
    def load(cls, filepath: str, expected_num_clients: int = None) -> "BandwidthSimulator":
        """从 JSON 文件加载带宽配置
        
        Args:
            filepath: JSON 文件路径
            expected_num_clients: 期望的客户端数量，用于验证配置是否匹配
        """
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return cls.from_dict(data, expected_num_clients=expected_num_clients)
