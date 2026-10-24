"""
ckks/track.py：CKKS 轨道（加密 / 明文 / threshold 门限三子模式的兜底轨道）

收编原 main.py 中 CKKS 轨道专属的实验编排：CKKS 密码设施（KGC 上下文、
服务器公钥上下文、客户端共享上下文）、threshold 门限解密配置解析、
加密轮与明文轮的单轮执行。

导入纪律：静态导入仅限 base、threshold（共享基础设施）与第三方；
ckks 包自身模块（crypto 等）在方法内惰性导入——与 base/experiment.py
保持同一例外理由（轨道模块属于方案包，模块级自 import 无意义）。
"""

import time

import torch
import torch.nn as nn
import numpy as np
import psutil

from base.experiment import Track, register_track
from base.data import evaluate
from base.log_utils import (
    tree,
    LogTag, tag_info, tag_warn,
    fmt_size,
    log_round_header,
)

# 门限 CKKS 解密（doc/threshold_ckks.md）：t-out-of-n 门限解密可插拔模块。
# threshold 是允许共享的基础设施（base 依赖纪律明文豁免），静态导入。
from threshold import ThresholdDecryptor


class CkksTrack(Track):
    """CKKS 兜底轨道：恒 enabled，覆盖加密 / 明文 / threshold 三子模式"""

    name = 'ckks'
    conflicts = ('ppbr', 'rfl')
    supports_client_attacks = True
    needs_checkpoint = False

    def enabled(self, config: dict) -> bool:
        # 兜底轨道：恒启用（active 轨道解析落到此处 = 加密或明文或 threshold）
        return True

    def wants_crypto(self, config: dict) -> bool:
        # 明文模式（encryption.enabled=false）或 RFL 轨道：跳过全部 CKKS 初始化
        return bool(config.get('encryption', {}).get('enabled', True)) \
            and not bool((config.get('rfl') or {}).get('enabled', False))

    def setup_crypto(self, exp) -> None:
        from ckks.crypto import CKKSManager, CKKSContext
        _ckks_cfg = exp.config['ckks'].copy()
        _ckks_cfg['_ppbr_track'] = bool(
            exp.config.get('ppbr', {}).get('enabled', False))
        exp.ckks_manager = CKKSManager(_ckks_cfg)
        poly_degree = exp.config['ckks'].get('poly_modulus_degree', 8192)
        server_context = CKKSContext.from_full_bytes(
            exp.ckks_manager.context.get_context_bytes(),
            poly_modulus_degree=poly_degree)
        exp.server_ckks_manager = CKKSManager(_ckks_cfg.copy(), context=server_context)
        ckks = exp.config['ckks']
        tree([
            ("🔐", f"CKKS  N={ckks.get('poly_modulus_degree')}  |  "
                   f"scale=2^{ckks.get('global_scale', 0).bit_length()-1}  |  "
                   f"mod={ckks.get('coeff_mod_bit_sizes')}  |  "
                   f"packing={'ON' if ckks.get('slot_packing', True) else 'OFF'}  |  "
                   f"server_key=public-only"),
        ], indent=1)

    def after_server(self, exp) -> None:
        exp.server.init_encrypted_global_state(exp.server_ckks_manager)
        tree([
            ("🖥️ ", f"Aggregation Server  |  rule={exp.config.get('defense', {}).get('aggregation_rule', 'fedavg')}  |  encrypted_state=initialized"),
        ], indent=1)

    def attach_client(self, exp, client) -> None:
        from ckks.crypto import CKKSManager
        _ckks_cfg = exp.config['ckks'].copy()
        _ckks_cfg['_ppbr_track'] = bool(
            exp.config.get('ppbr', {}).get('enabled', False))
        client.ckks_manager = CKKSManager(_ckks_cfg.copy(), context=exp._shared_context)

    def init_protocol(self, exp) -> None:
        # 门限 CKKS 解密（doc/threshold_ckks.md）：把"每个客户端持完整 sk"
        # 改造为 t-out-of-n 门限解密。仅 CKKS 轨道——与 PPBR 互斥
        # （PPBR 走论文的 SA 密钥体系，不经单密钥 CKKS 门限）。
        from threshold import ThresholdConfig
        exp.threshold_cfg = ThresholdConfig.from_config(
            exp.config.get('threshold') or {})
        exp.threshold_decryptor: ThresholdDecryptor = None
        exp._threshold_dist_recorded = False
        if exp.threshold_cfg.enabled:
            if exp.config.get('ppbr', {}).get('enabled', False):
                exp.threshold_cfg.enabled = False
                tag_warn(LogTag.INIT,
                         "threshold.enabled=true 与 ppbr.enabled=true 冲突"
                         "（threshold 仅 CKKS 轨道）→ 强制禁用 threshold")
            else:
                c = exp.threshold_cfg
                tree([
                    ("🔑", f"Threshold: mode={c.mode}  |  combiner={c.combiner}"
                           f"（{'场景B-服务器盲' if c.combiner == 'client' else '场景A-服务器可见'}）"
                           f"  |  t={c.t}  |  smudging={c.smudging}"),
                ], indent=1)
        # 明文模式：层次密文聚合不适用于明文轮，强制关闭并告警
        # （RFL 轨道除外：RFL 的层次是协议内 split learning 实体，不经 CKKS）
        if (exp.plaintext_mode and not exp._rfl_enabled
                and exp.config.get('hierarchy', {}).get('enabled', False)):
            exp.config['hierarchy']['enabled'] = False
            tag_warn(LogTag.INIT,
                     "plaintext mode → 强制关闭 hierarchy（层次密文聚合不适用于明文轮）")

    def record_key_distribution(self, exp) -> None:
        num_clients = exp.config['system']['num_clients']
        if exp.plaintext_mode:
            # 明文轨道：无密钥分发环节
            exp.metrics.record('key_distribution_cost', 0.0)
            tag_info(LogTag.INIT, "Plaintext mode: 无 CKKS 密钥分发")
        else:
            key_size_per_client = exp.ckks_manager.get_key_distribution_size()
            exp.metrics.record('key_distribution_cost', key_size_per_client * num_clients)
            tag_info(LogTag.INIT, "Unified CKKS key distributed to all clients")

    def output_prefix(self, exp) -> str:
        # 三种子模式一眼区分：明文基线 / 门限解密叠加 / 标准密文。
        # 注意：本方法在 __init__ 输出目录创建时调用，此时 exp.plaintext_mode
        # 尚未设置（_init_components 才赋值），只能直接读 config 判定
        if not exp.config.get('encryption', {}).get('enabled', True):
            return "plain"
        if exp.config.get('threshold', {}).get('enabled', False):
            return "ckks_threshold"
        return "ckks"

    def mode_label(self, exp) -> str:
        if exp.plaintext_mode:
            return "Plaintext FedAvg + 鲁棒防御"
        if exp.threshold_cfg.enabled:
            return "CKKS-Encrypted + Threshold"
        return "Encrypted"

    def upload_label(self, exp) -> str:
        if exp.plaintext_mode:
            return "plaintext（明文更新）"
        return super().upload_label(exp)

    def report_formulas(self, exp) -> list:
        m = exp.metrics.get_metrics()
        # 与基类报告同一判定：明文轨道每轮记录 ciphertext_size=0（占位）
        is_encrypted = (not getattr(exp, 'plaintext_mode', False)
                        and len(m.get('ciphertext_size', [])) > 0)
        if not is_encrypted:
            return super().report_formulas(exp)
        return [
            "Plaintext Size / 明文大小 | `sum(v.numel() * 4 for v in model_update.values())`",
            "Ciphertext Size / 密文大小 | `sum(len(ct.serialize()) for ct in encrypted_update.values())`",
            "Ciphertext Blocks / 密文块数 | `sum(len(v) for v in encrypted_update.values())`",
            "Upload Cost / 上传开销 | `sum(ciphertext_size of selected clients)`",
            "Download Cost / 下载开销 | `global_model_size_bytes * num_selected_clients`",
            "Total Communication / 总通信 | `upload_cost + download_cost`",
            "Expansion Ratio / 膨胀率 | `total_ciphertext_size / total_plaintext_size`",
        ]

    def run_round(self, exp, round_num: int) -> dict:
        # CKKS 兜底轨道覆盖明文子模式：encryption.enabled=false → 明文轮
        if exp.plaintext_mode:
            return run_round_plaintext(exp, round_num)
        return run_round_encrypted(exp, round_num)


register_track(CkksTrack())


def run_round_encrypted(exp, round_num: int) -> dict:
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
    log_round_header(round_num + 1, exp.config['system']['num_rounds'], mode="CKKS-Encrypted")
    round_start = time.time()

    selected_clients = exp._select_clients()

    encrypted_global = exp.server.get_encrypted_global_params()
    client_updates, client_weights, train_losses, train_accs = [], [], [], []
    threshold_stats = None
    if exp.threshold_cfg.enabled:
        # 门限轨道：每轮开头由 t 个被选客户端协作解密全局模型
        # （客户端加密上传、服务器密文加权聚合路径完全不变）
        if exp.threshold_decryptor is None:
            exp.threshold_decryptor = ThresholdDecryptor(
                exp.threshold_cfg, len(exp.clients),
                exp.ckks_manager.context.get_full_context_bytes(),
                poly_modulus_degree=exp.config['ckks'].get(
                    'poly_modulus_degree', 8192))
        plain_global, threshold_stats = \
            exp.threshold_decryptor.decrypt_global_params(
                encrypted_global,
                [c.client_id for c in selected_clients])
        for client in selected_clients:
            update, loss, acc = client.local_train(
                global_params=plain_global, round_num=round_num)
            client_updates.append(update)
            client_weights.append(client.get_weight())
            train_losses.append(loss)
            train_accs.append(acc)
            exp.metrics.record('local_train_time', client.local_train_time)
    else:
        for client in selected_clients:
            update, loss, acc = client.local_train(encrypted_global_params=encrypted_global, round_num=round_num)
            client_updates.append(update)
            client_weights.append(client.get_weight())
            train_losses.append(loss)
            train_accs.append(acc)
            exp.metrics.record('local_train_time', client.local_train_time)

    # ==== 攻击：DoS 类先剔除本轮不参与的恶意客户端，其余在明文域篡改更新 ====
    # （攻击发生在客户端本地、加密上传之前——即"客户端被攻破/恶意客户端"威胁模型；
    #   数据投毒已在初始化时替换 train_loader，此处无需处理；
    #   隐私推断不修改更新，见初始化时的提示。）
    selected_clients, client_updates, client_weights, train_losses, train_accs = \
        exp._apply_attacks_to_updates(
            round_num, selected_clients, client_updates,
            client_weights, train_losses, train_accs)

    # ==== 全局掩码投票（隐私安全：客户端本地算 mask，只上传 mask）====
    gmv_mode = exp.config.get('global_mask_vote', {}).get('mode', 'none')
    sparsity_cfg = exp.config.get('ckks', {}).get('sparsification', {})
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
        packing = exp.server_ckks_manager.packing_strategy if exp.server_ckks_manager else None
        values_per_chunk = packing.get_values_per_chunk() if packing else 8192
        total_params = sum(p.numel() for p in exp.server.model.parameters())
        num_batches = (total_params + values_per_chunk - 1) // values_per_chunk
        if exp.hierarchy is not None:
            mask_by_cid = {c.client_id: m for c, m in zip(selected_clients, client_masks)}
            global_mask = exp.hierarchy.vote_global_mask(
                mask_by_cid, num_batches=num_batches, round_num=round_num)
        else:
            global_mask = exp.server.run_global_mask_vote(
                client_masks=client_masks,
                num_batches=num_batches,
                round_num=round_num,
            )

        # 记录每轮 batch 索引详情（用于分析通信开销）
        exp._log_batch_indices(round_num, selected_clients, client_masks, global_mask)

    encrypted_updates, client_upload_sizes = [], []
    total_plaintext_size, total_ciphertext_size = 0, 0
    for client, update in zip(selected_clients, client_updates):
        encrypted = client.encrypt_update(
            update, client.ckks_manager, round_num=round_num,
            global_mask=global_mask,
        )
        encrypted_updates.append(encrypted)
        exp.metrics.record('encryption_time', client.encryption_time)
        exp.metrics.record('plaintext_size', client.plaintext_size)
        exp.metrics.record('ciphertext_size', client.ciphertext_size)
        exp.metrics.record('num_ciphertexts', client.num_ciphertexts)
        total_plaintext_size += client.plaintext_size
        total_ciphertext_size += client.ciphertext_size
        client_upload_sizes.append(client.ciphertext_size)
        exp.client_participation[client.client_id] += 1
        exp.client_total_ciphertexts[client.client_id] += client.num_ciphertexts

    # 记录聚合密文数量（标准 CKKS：聚合不改变密文大小）
    if selected_clients:
        exp.metrics.record('aggregated_ciphertexts', selected_clients[0].num_ciphertexts)

    if total_plaintext_size > 0:
        expansion_ratio = total_ciphertext_size / total_plaintext_size
        exp.metrics.record('expansion_ratio', expansion_ratio)
        tag_info(LogTag.ENCRYPT, f"Expansion ratio: {expansion_ratio:.2f}x  |  "
                 f"total_plain={fmt_size(total_plaintext_size)}  |  "
                 f"total_cipher={fmt_size(total_ciphertext_size)}")

    # 聚合：标准路径（客户端密文→服务器）或层次路径（客户端密文→边缘→服务器）
    if exp.hierarchy is not None:
        enc_by_cid = {c.client_id: e for c, e in zip(selected_clients, encrypted_updates)}
        w_by_cid = {c.client_id: w for c, w in zip(selected_clients, client_weights)}
        edge_updates, edge_weights, edge_sizes = exp.hierarchy.aggregate_edges(
            enc_by_cid, w_by_cid, round_num=round_num)
        # normalize=False：边缘密文已按全局权重预缩放，云只做加法合并
        # （避免二次密文×标量导致 CKKS 乘法深度耗尽）
        aggregated_update = exp.server.aggregate_encrypted_updates(
            edge_updates, edge_weights, exp.server_ckks_manager, edge_sizes,
            normalize=False)
    else:
        aggregated_update = exp.server.aggregate_encrypted_updates(
            encrypted_updates, client_weights, exp.server_ckks_manager, client_upload_sizes)
    exp.server.update_global_model(aggregated_update)

    # 评估：门限轨道用仪式解密出的明文全局模型做明文评估；否则由
    # 客户端解密全局模型并完成评估后上报（服务器不持有私钥）
    criterion = nn.CrossEntropyLoss()
    eval_client = selected_clients[0]
    if exp.threshold_cfg.enabled:
        # 聚合完成后再做一次仪式解密，评估明文全局模型（服务器全程盲）
        enc_eval = exp.server.get_encrypted_global_params()
        eval_params, eval_stats = \
            exp.threshold_decryptor.decrypt_global_params(
                enc_eval, [c.client_id for c in selected_clients])
        eval_client.set_model_params(eval_params)
        test_loss, test_acc = evaluate(
            eval_client.model, exp.test_loader, criterion, exp.device)
        # 每轮两次仪式（训练前 + 评估前）的开销合并计入本轮指标
        threshold_stats['ceremony_bytes_per_round'] += \
            eval_stats['ceremony_bytes_per_round']
        threshold_stats['ceremony_time_sec'] += \
            eval_stats['ceremony_time_sec']
    else:
        test_loss, test_acc = eval_client.evaluate_encrypted(
            exp.server.get_encrypted_global_params(), exp.test_loader, criterion)
    exp.server.record_evaluation(test_loss, test_acc)

    if threshold_stats is not None:
        exp.metrics.record('threshold_ceremony_bytes',
                           threshold_stats['ceremony_bytes_per_round'])
        exp.metrics.record('threshold_ceremony_time',
                           threshold_stats['ceremony_time_sec'])
        exp.metrics.record('threshold_ideal',
                           int(threshold_stats['ideal']))
        if not exp._threshold_dist_recorded:
            # 首轮额外记录 Setup 分发开销（每客户端份额字节数）
            exp.metrics.record('threshold_dist_bytes',
                               threshold_stats['dist_bytes_per_client'])
            exp._threshold_dist_recorded = True

    exp.metrics.record('train_loss', np.mean(train_losses))
    exp.metrics.record('train_acc', np.mean(train_accs))
    exp.metrics.record('test_loss', test_loss)
    exp.metrics.record('test_acc', test_acc)
    exp.metrics.record('aggregation_time', exp.server.aggregation_time)
    exp.metrics.record('upload_cost', exp.server.upload_cost)
    exp.metrics.record('download_cost', exp.server.download_cost)
    exp.metrics.record('total_communication_cost', exp.server.communication_cost)
    exp.metrics.record('communication_rounds', 1)
    exp.metrics.record('cpu_utilization', psutil.cpu_percent())
    exp.metrics.record('memory_usage', psutil.virtual_memory().percent)

    round_time = time.time() - round_start
    exp.metrics.record('total_round_time', round_time)

    # 计算模拟 wall-clock 时间（使用 bandwidth_simulator 模拟异构网络）
    simulated_download_time = 0.0
    simulated_upload_time = 0.0
    simulated_compute_time = 0.0
    simulated_round_duration = 0.0
    for client in selected_clients:
        if exp.server.encrypted_global_state is not None:
            client_dl_bits = exp.server_ckks_manager.compute_encrypted_update_size(exp.server.encrypted_global_state) * 8
            client_ul_bits = client.ciphertext_size * 8
        else:
            client_dl_bits = exp.server.compute_download_cost() * 8
            client_ul_bits = client.ciphertext_size * 8

        if exp.bandwidth_simulator is not None:
            client_dl = exp.bandwidth_simulator.estimate_download_time(client.client_id, client_dl_bits)
            client_ul = exp.bandwidth_simulator.estimate_upload_time(client.client_id, client_ul_bits)
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
    exp.metrics.record('total_round_time_simulated', simulated_round_duration)
    exp.metrics.record('upload_time', simulated_upload_time)
    exp.metrics.record('download_time', simulated_download_time)

    tag_info(LogTag.ROUND,
             f"#{round_num + 1} done  |  acc={test_acc:.2f}%  |  "
             f"real_time={round_time:.2f}s  |  sim_dur={simulated_round_duration:.2f}s  |  "
             f"comm={fmt_size(exp.server.communication_cost)}")

    return {
        'round': round_num + 1,
        'test_acc': test_acc,
        'test_loss': test_loss,
        'round_time': round_time,
        'simulated_round_duration': simulated_round_duration,
    }


def run_round_plaintext(exp, round_num: int) -> dict:
    """
    运行一轮明文联邦学习（encryption.enabled=false）

    流程：服务器明文中继全局模型 → 客户端本地训练 → 攻击注入（明文域）→
          客户端上传明文更新（不加密）→ 服务器 aggregate_plain_updates()
          按 baseline/ 注册表规则做明文鲁棒聚合 → update_global_model()
          → 明文评估。密文类指标（encryption_time/expansion_ratio 等）记 0。

    Args:
        round_num: 当前轮次

    Returns:
        本轮统计信息
    """
    log_round_header(round_num + 1, exp.config['system']['num_rounds'],
                     mode="Plaintext")
    round_start = time.time()

    selected_clients = exp._select_clients()

    # 服务器明文中继全局模型（明文模式：服务器可见全局模型）
    global_params = exp.server.get_global_params()
    client_updates, client_weights, train_losses, train_accs = [], [], [], []
    for client in selected_clients:
        update, loss, acc = client.local_train(
            global_params=global_params, round_num=round_num,
            upload_desc="plaintext")
        client_updates.append(update)
        client_weights.append(client.get_weight())
        train_losses.append(loss)
        train_accs.append(acc)
        exp.metrics.record('local_train_time', client.local_train_time)

    # 攻击注入（与加密轮同一语义：客户端本地明文域篡改更新；
    # 攻击在加密上传前生效，明文模式下天然可用）
    selected_clients, client_updates, client_weights, train_losses, train_accs = \
        exp._apply_attacks_to_updates(
            round_num, selected_clients, client_updates,
            client_weights, train_losses, train_accs)

    # 客户端上传明文更新：通信量 = 明文大小，无密文开销
    client_upload_sizes = []
    for client, update in zip(selected_clients, client_updates):
        plaintext_size = sum(p.numel() * 4 for p in update.values())
        client_upload_sizes.append(plaintext_size)
        exp.metrics.record('plaintext_size', plaintext_size)
        # 密文类指标对齐加密轮（记 0，report 据此识别明文轨道）
        exp.metrics.record('encryption_time', 0.0)
        exp.metrics.record('ciphertext_size', 0)
        exp.metrics.record('num_ciphertexts', 0)
        exp.client_participation[client.client_id] += 1
    if selected_clients:
        exp.metrics.record('aggregated_ciphertexts', 0)
        # 明文膨胀率 = 1.0（无密文开销）
        exp.metrics.record('expansion_ratio', 1.0)

    # 明文鲁棒聚合（defense.aggregation_rule 从 baseline/ 注册表分派）
    aggregated_update = exp.server.aggregate_plain_updates(
        client_updates, client_weights, client_upload_sizes)
    exp.server.update_global_model(aggregated_update)

    # 明文评估（全局模型在服务器明文可见）
    criterion = nn.CrossEntropyLoss()
    test_loss, test_acc = evaluate(
        exp.server.model, exp.test_loader, criterion, exp.device)
    exp.server.record_evaluation(test_loss, test_acc)

    exp.metrics.record('train_loss', np.mean(train_losses))
    exp.metrics.record('train_acc', np.mean(train_accs))
    exp.metrics.record('test_loss', test_loss)
    exp.metrics.record('test_acc', test_acc)
    exp.metrics.record('aggregation_time', exp.server.aggregation_time)
    exp.metrics.record('upload_cost', exp.server.upload_cost)
    exp.metrics.record('download_cost', exp.server.download_cost)
    exp.metrics.record('total_communication_cost', exp.server.communication_cost)
    exp.metrics.record('communication_rounds', 1)
    exp.metrics.record('cpu_utilization', psutil.cpu_percent())
    exp.metrics.record('memory_usage', psutil.virtual_memory().percent)

    round_time = time.time() - round_start
    exp.metrics.record('total_round_time', round_time)

    # 模拟 wall-clock 时间：明文上传/下载按明文大小估计
    simulated_download_time = 0.0
    simulated_upload_time = 0.0
    simulated_round_duration = 0.0
    for idx, client in enumerate(selected_clients):
        client_dl_bits = exp.server.compute_download_cost() * 8
        client_ul_bits = (client_upload_sizes[idx] if idx < len(client_upload_sizes)
                          else 0) * 8
        if exp.bandwidth_simulator is not None:
            client_dl = exp.bandwidth_simulator.estimate_download_time(client.client_id, client_dl_bits)
            client_ul = exp.bandwidth_simulator.estimate_upload_time(client.client_id, client_ul_bits)
        else:
            dl_bw_bits = 1024 * 1000
            ul_bw_bits = 512 * 1000
            client_dl = client_dl_bits / dl_bw_bits if dl_bw_bits > 0 else 0.0
            client_ul = client_ul_bits / ul_bw_bits if ul_bw_bits > 0 else 0.0
        client_total = client.local_train_time + client_dl + client_ul
        simulated_round_duration = max(simulated_round_duration, client_total)
        simulated_download_time = max(simulated_download_time, client_dl)
        simulated_upload_time = max(simulated_upload_time, client_ul)
    exp.metrics.record('total_round_time_simulated', simulated_round_duration)
    exp.metrics.record('upload_time', simulated_upload_time)
    exp.metrics.record('download_time', simulated_download_time)

    tag_info(LogTag.ROUND,
             f"#{round_num + 1} done  |  acc={test_acc:.2f}%  |  "
             f"real_time={round_time:.2f}s  |  sim_dur={simulated_round_duration:.2f}s  |  "
             f"comm={fmt_size(exp.server.communication_cost)}  |  mode=plaintext")

    return {
        'round': round_num + 1,
        'test_acc': test_acc,
        'test_loss': test_loss,
        'round_time': round_time,
        'simulated_round_duration': simulated_round_duration,
    }
