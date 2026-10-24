"""
base/experiment.py：实验编排基类与轨道协议
==========================================

架构：``Experiment`` 基类收编所有轨道无关的编排逻辑（配置加载、数据/模型、
服务器与客户端创建、攻击注入、训练主循环、结果落盘），各方案轨道
（ckks / ppbr / rfl 包内的 track 模块）以 ``Track`` 子类收编轨道专属逻辑
（密码设施、协议初始化、单轮执行、报告分节），并在模块末尾以
``register_track(XxxTrack())`` 注册实例。

隔离规则：
- 本模块静态导入仅限 base 内部模块（log_utils / data / models / metrics
  等）、torch / numpy / yaml / psutil 及标准库；
- 惰性导入（函数/方法内，唯一例外）：hfl_attacks 攻击注册表与 ckks 包的
  FederatedClient / AggregationServer / CKKSManager / CKKSContext /
  HierarchyController——实验编排需要把这些方案组件接线到基类，但静态
  依赖会让 base 失去"通用层"地位（依赖纪律：base 不得依赖任何方案包）；
- threshold 配置解析在 ckks/track.py 内惰性完成（threshold 是允许共享的
  基础设施，CKKS 轨道是其唯一消费者）。

轨道分发：TRACKS 为保序 dict，注册顺序 rfl → ppbr → ckks；
``Experiment.active_track`` 取第一个 ``enabled(config)`` 的轨道——
RFL / PPBR 开启时分别命中各自轨道，否则落到恒 enabled 的 CKKS 兜底轨道
（覆盖加密 / 明文 / threshold 三种子模式，靠 exp.plaintext_mode /
exp.threshold_cfg 区分）。
"""

import os
import json
import shutil
import logging
from datetime import datetime
from typing import Dict, List

import torch
import numpy as np
import yaml
import psutil

from base.log_utils import (
    banner, separator, blank_line, section, kv, tree, table,
    LogTag, tag_info, tag_warn,
    fmt_bw, fmt_pct,
    log_client_selection,
    safe_mean,
)
from base.models import create_model, print_model_summary
from base.data import create_client_dataloaders, get_dataset_num_classes
from base.metrics import MetricsCollector

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 轨道协议与注册表
# ---------------------------------------------------------------------------

class Track:
    """方案轨道协议：收编某一方案（CKKS / PPBR / RFL）的专属实验逻辑。

    子类化 + 默认空实现；轨道模块末尾以 ``register_track(XxxTrack())``
    注册实例。所有钩子以 ``exp``（Experiment 实例）为第一参数读取/回填
    实验状态，保持实例属性名与重构前 main.py 完全一致。
    """

    name: str = ''                 # 'ckks' / 'ppbr' / 'rfl'
    conflicts: tuple = ()          # 互斥轨道名（元数据；启动期冲突预检在
                                   # Experiment._init_components 中按轨道
                                   # 专属错误消息原文执行）
    supports_client_attacks: bool = True
    needs_checkpoint: bool = False

    # ---- 生命周期 ----------------------------------------------------------
    def enabled(self, config: dict) -> bool:
        """本轨道是否为 active 轨道（TRACKS 中第一个 enabled 者胜出）"""
        raise NotImplementedError

    def wants_crypto(self, config: dict) -> bool:
        """是否需要 CKKS 密码设施（setup_crypto/after_server/attach_client 的门槛）"""
        return False

    def setup_crypto(self, exp) -> None:
        """创建 exp.ckks_manager / exp.server_ckks_manager（并打印对应 tree 行）"""
        pass

    def after_server(self, exp) -> None:
        """服务器建好后：init_encrypted_global_state + 轨道横幅"""
        pass

    def attach_client(self, exp, client) -> None:
        """给客户端挂轨道设施（如 client.ckks_manager）"""
        pass

    def init_protocol(self, exp) -> None:
        """协议对象初始化（threshold 配置解析也在 CKKS 轨道的此方法内）"""
        pass

    def on_attacks_ready(self, exp) -> None:
        """攻击注入完成后的回填（默认空）"""
        pass

    # ---- 训练主循环 --------------------------------------------------------
    def record_key_distribution(self, exp) -> None:
        """记录密钥分发开销指标与日志（各轨道语义不同，默认不记录）"""
        pass

    def save_checkpoint(self, exp, ckpt: dict) -> None:
        """往 ckpt 塞本轨道跨轮状态（默认无跨轮状态）"""
        pass

    def load_checkpoint(self, exp, ckpt: dict) -> None:
        """恢复本轨道跨轮状态（默认无跨轮状态）"""
        pass

    def run_round(self, exp, round_num: int) -> dict:
        """执行一轮本轨道训练，返回本轮统计 dict"""
        raise NotImplementedError

    # ---- 展示与报告 --------------------------------------------------------
    def output_prefix(self, exp) -> str:
        """结果目录名前缀（experiment_<prefix>_<timestamp>_<dataset>）。
        默认取轨道名；子轨道（如 CKKS 的明文/threshold 子模式）可覆盖。"""
        return self.name

    def mode_label(self, exp) -> str:
        """实验横幅 kv("Mode", ...)"""
        return "Encrypted"

    def upload_label(self, exp) -> str:
        """实验横幅 kv("Upload", ...)"""
        return ("Δ" if exp.config['system'].get('upload_difference', True)
                else "Full")

    def print_final_summary(self, exp) -> None:
        """末尾横幅：默认 Client Participation + 通信公式（CKKS/明文/RFL 共用；
        PPBR 覆盖为线上传输审计）"""
        mm = exp.metrics.get_metrics()
        blank_line()
        banner("Client Participation", width=72)
        headers = ["Client", "Participated", "Total CTs"]
        rows = []
        for cid in sorted(exp.client_participation.keys()):
            ct_count = exp.client_total_ciphertexts.get(cid, 0)
            rows.append([cid, exp.client_participation[cid], ct_count])
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

    def report_formulas(self, exp) -> list:
        """markdown 报告 §5.1 公式表行（不含序号列，基类拼接 "| i | row |"）；
        默认明文套，加密套由 CkksTrack 覆盖"""
        return [
            "Plaintext Size / 明文大小 | `sum(v.numel() * 4 for v in model_update.values())`",
            "Upload Cost / 上传开销 | `sum(plaintext_size of selected clients)`",
            "Download Cost / 下载开销 | `global_model_size_bytes * num_selected_clients`",
            "Total Communication / 总通信 | `upload_cost + download_cost`",
        ]

    def report_stats_section(self, exp, lines, m, num_rounds, is_encrypted) -> None:
        """markdown 报告 §3：默认客户端参与统计表（PPBR 覆盖为线上传输审计）"""
        lines.append(f"## 3. Client Participation & Statistics / 客户端参与统计与详情")
        lines.append(f"")
        lines.append(f"| CID | Part / 参与轮次 | CTs / 密文数 | DL BW / 下行带宽 | UL BW / 上行带宽 |")
        lines.append(f"|-----|----------------|--------------|------------------|------------------|")
        for cid in sorted(exp.client_participation.keys()):
            ct_count = exp.client_total_ciphertexts.get(cid, 0) if is_encrypted else 0
            dl_bw = "-"
            ul_bw = "-"
            if exp.bandwidth_simulator is not None:
                dl_bw = f"{exp.bandwidth_simulator.get_dl_bw_kbps(cid):.0f} kbps"
                ul_bw = f"{exp.bandwidth_simulator.get_ul_bw_kbps(cid):.0f} kbps"
            lines.append(f"| {cid} | {exp.client_participation[cid]} | {ct_count} | {dl_bw} | {ul_bw} |")
        lines.append(f"")


# 保序注册表：rfl → ppbr → ckks（ckks 恒 enabled，作兜底，必须最后）
TRACKS: Dict[str, Track] = {}


def register_track(track: Track) -> None:
    """注册轨道实例（TRACKS 为保序 dict，重复注册同名轨道直接覆盖）"""
    TRACKS[track.name] = track


# ---------------------------------------------------------------------------
# 实验基类
# ---------------------------------------------------------------------------

class Experiment:
    """联邦学习实验基类：轨道无关的编排逻辑（轨道专属逻辑在各 Track 中）"""

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

        # 解析 active 轨道（输出目录命名需要轨道名前缀；冲突预检仍在
        # _init_components 中原样执行，错误消息不变）
        self._resolve_active_track()

        # 创建输出目录（带轨道前缀，如 experiment_rfl_20261020_143000_mnist，
        # 便于 results/ 下按方案一眼区分各实验；CKKS 子模式前缀见
        # CkksTrack.output_prefix：plain / ckks_threshold / ckks）
        self.timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dataset_name = self.config['data']['dataset']
        if output_dir:
            # 使用自定义输出目录作为父目录，在其下生成 轨道+时间+数据集 子目录
            base_dir = output_dir
        else:
            base_dir = self.config['output']['results_dir']
        self.output_dir = os.path.join(
            base_dir,
            f"experiment_{self.active_track.output_prefix(self)}_"
            f"{self.timestamp}_{dataset_name}"
        )
        os.makedirs(self.output_dir, exist_ok=True)

        # 保存配置文件副本，便于后续追踪和对比
        self.config_path = config_path
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

    def _resolve_active_track(self):
        """active 轨道解析：TRACKS（注册顺序 rfl → ppbr → ckks）中第一个
        enabled(config) 的轨道；ckks 恒 enabled 作兜底，必能解析到。"""
        self.active_track = None
        for track in TRACKS.values():
            if track.enabled(self.config):
                self.active_track = track
                break

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

        # 明文模式（encryption.enabled=false）：标准明文 FedAvg/鲁棒聚合轨道，
        # 要求 ppbr/threshold 均关闭（ppbr/threshold 都是密文轨道，冲突拒绝启动）
        self.plaintext_mode = not self.config.get('encryption', {}).get('enabled', True)
        if self.plaintext_mode:
            conflicts = []
            if self.config.get('ppbr', {}).get('enabled', False):
                conflicts.append("ppbr.enabled=true")
            if self.config.get('threshold', {}).get('enabled', False):
                conflicts.append("threshold.enabled=true")
            if conflicts:
                raise ValueError(
                    "明文模式（encryption.enabled=false）与密文轨道冲突："
                    + "、".join(conflicts)
                    + "。请关闭后再试（ppbr/threshold 均为密文轨道，"
                      "明文轮无法执行其加解密协议）。")

        # RFL 轨道（doc/rfl.md）：与 ppbr/threshold 互斥（均为完整协议轨道）。
        # RFL 的加密体系是数字签名 + 同态哈希，不经单密钥 CKKS——因此与
        # 明文模式（encryption.enabled=false）可共存，此时唯一轨道即 RFL。
        rfl_cfg = self.config.get('rfl') or {}
        self._rfl_enabled = bool(rfl_cfg.get('enabled', False))
        self.rfl = None
        if self._rfl_enabled:
            if self.config.get('ppbr', {}).get('enabled', False):
                raise ValueError(
                    "rfl.enabled=true 与 ppbr.enabled=true 冲突"
                    "（两条完整协议轨道，请只开启其一）")
            if self.config.get('threshold', {}).get('enabled', False):
                raise ValueError(
                    "rfl.enabled=true 与 threshold.enabled=true 冲突"
                    "（threshold 仅 CKKS 轨道，RFL 不经单密钥 CKKS）")

        # active 轨道解析（冲突预检之后）：TRACKS 中第一个 enabled(config) 的轨道
        # （注册顺序 rfl → ppbr → ckks，ckks 恒 enabled 作兜底）。
        # __init__ 已为输出目录命名调用过一次；此处按原顺序再解析（幂等）。
        self._resolve_active_track()

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
        # 明文模式（encryption.enabled=false）或 RFL 轨道：跳过全部 CKKS
        # 初始化——客户端/服务器 ckks_manager 置 None，不调用任何加密 API
        # （RFL 用数字签名 + 同态哈希，见 doc/rfl.md）
        self.ckks_manager = None
        self.server_ckks_manager = None
        for track in TRACKS.values():
            if track.wants_crypto(self.config):
                track.setup_crypto(self)
        if self.ckks_manager is None:
            tree([
                ("🔓", f"Plaintext mode  |  rule={self.config.get('defense', {}).get('aggregation_rule', 'fedavg')}"
                       f"（baseline/ 注册表）  |  CKKS skipped"),
            ], indent=1)

        from ckks.server import AggregationServer
        self.server = AggregationServer(self.global_model, self.config, self.device, self.server_ckks_manager)
        crypto_initialized = False
        for track in TRACKS.values():
            if track.wants_crypto(self.config):
                track.after_server(self)
                crypto_initialized = True
        if not crypto_initialized:
            tree([
                ("🖥️ ", f"Aggregation Server  |  rule={self.config.get('defense', {}).get('aggregation_rule', 'fedavg')}  |  plaintext_state"),
            ], indent=1)

        # 统一 CKKS 上下文：只反序列化一次，全体客户端共享
        # （原先每客户端各做一次 full-context 序列化+反序列化：240 客户端时
        #  初始化耗时 O(n)×单次加载且内存放大 ~n 倍，是长跑初始化瓶颈）
        if (not self.plaintext_mode and not self._rfl_enabled
                and self.ckks_manager is not None):
            # athfhe 轨道（自带 numpy 外层 CKKS 仿真，wants_crypto=False）无需
            # TenSEAL 上下文——无 ckks_manager 时跳过共享上下文创建
            from ckks.crypto import CKKSContext
            poly_degree = self.config['ckks'].get('poly_modulus_degree', 8192)
            self._shared_context = CKKSContext.from_full_bytes(
                self.ckks_manager.context.get_full_context_bytes(),
                poly_modulus_degree=poly_degree)
        else:
            self._shared_context = None

        from ckks.client import FederatedClient
        self.clients = []
        for i in range(self.config['system']['num_clients']):
            client_model = create_model(self.config['model']['name'], self.config)
            client = FederatedClient(
                client_id=i, model=client_model,
                train_loader=self.client_loaders[i],
                config=self.config, device=self.device
            )
            for track in TRACKS.values():
                if track.wants_crypto(self.config):
                    track.attach_client(self, client)
            self.clients.append(client)
        tree([
            ("👤", f"Created {len(self.clients)} clients  |  "
                   f"sampling_rate={self.config['system'].get('client_sampling_rate', 1.0)}"),
        ], indent=1)

        # 初始化客户端参与统计
        self.client_participation = {i: 0 for i in range(len(self.clients))}
        self.client_total_ciphertexts = {i: 0 for i in range(len(self.clients))}

        # 可插拔客户端选择策略
        from base.client_selection import create_client_selection_strategy
        self.selection_strategy = create_client_selection_strategy(
            self.config, num_clients=len(self.clients))

        # 协议初始化：对配置中所有已启用的轨道调 init_protocol。
        # CKKS 轨道恒启用（兜底），threshold 配置解析在其中——因此
        # ppbr+threshold 配置的冲突降级告警在 PPBR 轨道初始化后照常打印。
        self.ppbr = None
        for track in TRACKS.values():
            if track.enabled(self.config):
                track.init_protocol(self)

        # 层次联邦学习（云-边-端）：enabled 时创建边缘层
        # （明文模式的强制关闭告警在 CkksTrack.init_protocol 中，先于本块执行；
        #   RFL 轨道除外：RFL 的层次是协议内 split learning 实体，不经 CKKS）
        self.hierarchy = None
        if self.config.get('hierarchy', {}).get('enabled', False):
            from ckks.hierarchy import HierarchyController
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
            # 攻击就绪后的轨道回填（如 PPBR 的 _malicious_ids）与不适用的警告
            for track in TRACKS.values():
                if track.enabled(self.config):
                    track.on_attacks_ready(self)

        # 带宽模拟器（确保模拟 wall-clock 时间使用配置的异构带宽）
        from base.bandwidth_simulator import BandwidthSimulator
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

    def run_round_ppbr(self, round_num: int) -> dict:
        """兼容别名：tests/test_ppbr.py 直接调用本方法（重构前为 Experiment
        的实例方法）。委托 active 轨道的单轮执行（PPBR 轨道 = ppbr/track.py
        的 run_round_ppbr，内部经 exp.ppbr.run_round 取协议对象，测试 spy
        替换实例属性后照常生效）。"""
        return self.active_track.run_round(self, round_num)

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

    def run_experiment(self) -> Dict:
        """
        运行完整加密实验

        Returns:
            实验结果
        """
        upload_mode = self.active_track.upload_label(self)
        blank_line()
        banner("Experiment Start", width=72)
        kv("Mode", self.active_track.mode_label(self), indent=1)
        kv("Rounds", self.config['system']['num_rounds'], indent=1)
        kv("Upload", upload_mode, indent=1)
        if self.plaintext_mode:
            kv("Aggregation rule",
               self.config.get('defense', {}).get('aggregation_rule', 'fedavg')
               + "（baseline/ 注册表）", indent=1)
        separator(width=72)

        num_rounds = self.config['system']['num_rounds']
        results = []
        # 轨道分发：rfl.enabled → RFL 协议轨道（split learning + 可验证聚合）；
        # 否则 ppbr.enabled → PPBR 协议轨道；否则 encryption.enabled=false
        # → 明文 FedAvg 轨道（要求 ppbr/threshold 关闭，已在初始化校验）；
        # 否则 CKKS 加密轨道（threshold.enabled 时叠加门限解密）
        round_fn = self.active_track.run_round

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
        self.active_track.record_key_distribution(self)

        try:
            for round_num in range(start_round, num_rounds):
                result = round_fn(self, round_num)
                results.append(result)

                # 内存卫生：PPBR 每轮创建数千个 TenSEAL CKKSVector，native 堆
                # 增长/碎片会在 ~2h 后导致小额分配失败（观测到 1.79MB 分配崩溃）；
                # 每轮主动 GC + 释放 GPU 缓存，并记录进程 RSS 便于观测趋势
                import gc
                gc.collect()
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                _rss = psutil.Process().memory_info().rss / (1024 ** 3)
                self.metrics.record('process_rss_gb', round(_rss, 3))

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
        self.active_track.print_final_summary(self)

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
        # 明文轨道每轮记录 ciphertext_size=0（占位），不能仅凭列表非空判定加密
        is_encrypted = (not getattr(self, 'plaintext_mode', False)
                        and len(m.get('ciphertext_size', [])) > 0)
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
        if not self.active_track.needs_checkpoint:
            return
        ckpt = {
            'round': round_num,
            'model': {k: v.detach().cpu() for k, v in
                      self.server.model.state_dict().items()},
            'metrics': self.metrics.get_metrics(),
        }
        # 轨道跨轮状态（如 PPBR 的动量/rng）由轨道自行塞入
        self.active_track.save_checkpoint(self, ckpt)
        path = os.path.join(self.output_dir, f"checkpoint_round_{round_num}.pt")
        torch.save(ckpt, path)
        logger.info(f"检查点已保存（含 PPBR 动量/rng/指标）: {path}")

    def _load_resume_state(self) -> None:
        """从 config.resume.from 目录加载最新检查点并恢复跨轮状态。"""
        resume_cfg = self.config.get('resume') or {}
        src = resume_cfg.get('from')
        if not resume_cfg.get('enabled', False) or not src:
            return
        if not self.active_track.needs_checkpoint:
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
        # 轨道跨轮状态（如 PPBR 的动量/rng）由轨道自行恢复
        self.active_track.load_checkpoint(self, ckpt)
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
        # 明文轨道每轮记录 ciphertext_size=0（占位），不能仅凭列表非空判定加密
        is_encrypted = (not getattr(self, 'plaintext_mode', False)
                        and len(m.get('ciphertext_size', [])) > 0)
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

        # 客户端参与统计 / PPBR 线上传输审计（§3 内容按轨道分派）
        self.active_track.report_stats_section(self, lines, m, num_rounds, is_encrypted)

        # 每轮详细结果
        lines.append(f"## 4. Per-Round Results / 每轮详细结果")
        lines.append(f"")
        header = "| Round / 轮次 | Test Acc / 测试准确率 | Test Loss / 测试损失 | Round Time / 轮次耗时 | Upload Time / 上传时间 | Download Time / 下载时间 | Comm / 通信量 | CT Uploads / 密文上传数 | CT Downloads / 聚合密文数 |"
        if is_encrypted:
            header += " Encrypt / 加密耗时 | Expansion / 膨胀率 |"
        lines.append(header)

        sep = "|--------------|----------------------|---------------------|----------------------|----------------------|-----------------------|---------------|--------------------------|-------------------------|"
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
        # §5.1 公式表行按轨道分派（ppbr / encrypted / plain 三套）
        for i, formula_row in enumerate(self.active_track.report_formulas(self), start=1):
            lines.append(f"| {i} | {formula_row} |")
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
