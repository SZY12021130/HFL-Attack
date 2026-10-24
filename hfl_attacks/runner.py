"""
HFL-Attack 攻击评估 Runner
==========================

在 CKKS + FedAvg（服务器仅持公钥）上系统化评估 hfl_attacks 中的全部攻击，
并使用通用评价指标输出对比报告。

评估协议
--------
训练阶段攻击（数据投毒 / 模型投毒 / 系统攻击）：
  固定种子跑 R 轮加密联邦学习。恶意客户端（默认 2/10）按攻击定义污染数据或
  篡改上传更新；攻击作用于客户端明文更新之后、CKKS 加密之前（恶意客户端场景）。
  指标：每轮全局测试准确率（客户端解密评估）、相对无攻击基线的准确率下降、
  收敛轮数、后门类攻击的 ASR。

隐私推断攻击：
  以无攻击基线训练得到的全局模型为目标。威胁模型说明：CKKS 加密下被动服务器
  无法获得单个客户端更新，本评估将攻击者置于可观察到明文更新/模型的边界位置
  （如合谋参与者、被攻破的边缘聚合节点），用于量化"一旦更新可见"时的泄漏程度。

用法：
  python -m hfl_attacks.runner                      # 全部攻击
  python -m hfl_attacks.runner --attacks model:alie,data:backdoor_badnets
  python -m hfl_attacks.runner --skip-privacy --rounds 5
"""

import argparse
import json
import os
import time
from datetime import datetime
from typing import Dict, List, Optional

import numpy as np
import torch
import torch.nn as nn
import yaml
from torch.utils.data import DataLoader, TensorDataset

# 依赖纪律：hfl_attacks 不得静态 import 其他方案包——ckks 组件在
# FLEnvironment.__init__ 内惰性导入（本 runner 是独立评估入口，用法见
# 模块 docstring，惰性导入不影响命令行行为）
from base.models import create_model
from base.data import create_client_dataloaders
from hfl_attacks.base import (
    ATTACK_REGISTRY, AttackContext, ClientUpdateAttack, DataPoisoner, PrivacyAttack,
)
from hfl_attacks import metrics as attack_metrics


# =====================================================================
# 评估配置
# =====================================================================

def build_eval_config(args) -> Dict:
    from base.config_loader import load_config
    cfg = load_config(args.config)
    cfg['system']['num_clients'] = args.clients
    cfg['system']['num_rounds'] = args.rounds
    cfg['system']['device'] = args.device
    cfg['system']['client_sampling_rate'] = 0.5
    cfg['data']['dataset'] = args.dataset
    cfg['data']['iid'] = True
    # 小规模评估用轻量 CKKS 参数（与 configs/config.yaml 的"轻量快速"预设一致）
    cfg['ckks']['poly_modulus_degree'] = 4096
    cfg['ckks']['coeff_mod_bit_sizes'] = [40, 20, 40]
    cfg['ckks']['global_scale'] = 1048576
    return cfg


# =====================================================================
# 加密联邦学习环境（服务器仅持公钥）
# =====================================================================

class FLEnvironment:
    """一次攻击评估的加密 FL 环境（密钥分离架构）"""

    def __init__(self, config: Dict, malicious_ids: List[int],
                 poisoned_loaders: Optional[Dict[int, DataLoader]] = None):
        self.config = config
        self.device = config['system']['device']
        self.malicious_ids = malicious_ids

        self.client_loaders, self.test_loader = create_client_dataloaders(
            dataset_name=config['data']['dataset'],
            num_clients=config['system']['num_clients'],
            batch_size=config['system']['batch_size'],
            data_dir=config['data']['data_dir'],
            iid=config['data']['iid'],
            alpha=config['data'].get('alpha', 0.5),
        )
        if poisoned_loaders:
            for cid, loader in poisoned_loaders.items():
                self.client_loaders[cid] = loader

        self.global_model = create_model(config['model']['name'], config)

        # 惰性导入（见模块顶部说明）：本 runner 运行时才有 ckks 依赖
        from ckks.crypto import CKKSManager, CKKSContext
        from ckks.server import AggregationServer
        from ckks.client import FederatedClient

        # 密钥分离：KGC 完整上下文 / 服务器公钥上下文 / 客户端完整上下文
        self.master_manager = CKKSManager(config['ckks'].copy())
        poly_degree = config['ckks'].get('poly_modulus_degree', 8192)
        self.server_manager = CKKSManager(
            config['ckks'].copy(),
            context=CKKSContext.from_full_bytes(
                self.master_manager.context.get_context_bytes(),
                poly_modulus_degree=poly_degree))
        self.server = AggregationServer(self.global_model, config, self.device,
                                        self.server_manager)
        self.server.init_encrypted_global_state(self.server_manager)

        client_ctx_bytes = self.master_manager.context.get_full_context_bytes()
        self.clients = []
        for i, loader in enumerate(self.client_loaders):
            client = FederatedClient(
                client_id=i,
                model=create_model(config['model']['name'], config),
                train_loader=loader, config=config, device=self.device)
            client.ckks_manager = CKKSManager(
                config['ckks'].copy(),
                context=CKKSContext.from_full_bytes(client_ctx_bytes,
                                                    poly_modulus_degree=poly_degree))
            self.clients.append(client)

        self.eval_client = self.clients[0]
        self.acc_history: List[float] = []

    def select(self, round_num: int) -> List[int]:
        rng = np.random.RandomState(1000 + round_num)
        n = len(self.clients)
        k = max(1, int(n * self.config['system']['client_sampling_rate']))
        return sorted(rng.choice(n, k, replace=False).tolist())

    def run_round(self, round_num: int, attack_inst: Optional[ClientUpdateAttack] = None) -> None:
        """跑一轮加密训练；attack_inst 对恶意客户端的明文更新施加攻击"""
        selected = self.select(round_num)
        encrypted_global = self.server.get_encrypted_global_params()

        # 第一阶段：所有选中客户端本地训练（解密全局模型 → 训练 → 得明文更新）
        raw_updates, weights = {}, {}
        for cid in selected:
            client = self.clients[cid]
            update, _, _ = client.local_train(encrypted_global_params=encrypted_global,
                                              round_num=round_num)
            raw_updates[cid] = update
            weights[cid] = client.get_weight()

        # 第二阶段：恶意客户端按攻击定义篡改更新（合谋攻击可见全部更新）；
        # 系统攻击可选择不参与本轮（如 DoS/dropout），此时该客户端完全不回传
        dropped: set = set()
        if attack_inst is not None:
            selected_list = list(selected)
            malicious_in_round = [i for i, cid in enumerate(selected_list)
                                  if cid in self.malicious_ids]
            for i, cid in enumerate(selected_list):
                if cid not in self.malicious_ids:
                    continue
                participates = getattr(attack_inst, 'participates', None)
                if callable(participates) and not participates(round_num):
                    dropped.add(cid)
                    continue
                ctx = AttackContext(
                    global_params=None,  # 服务器不持明文全局模型
                    self_update=raw_updates[cid],
                    all_updates=[raw_updates[c] for c in selected_list],
                    self_index=i,
                    malicious_indices=malicious_in_round,
                    num_classes=self.config['data'].get('num_classes', 10),
                    round_num=round_num,
                )
                raw_updates[cid] = attack_inst.attack(raw_updates[cid], ctx)

        # 第二、三阶段之间：全局 mask 投票（global_mask_vote.mode != 'none' 时启用）
        # 流程：客户端各自本地 TopK 算 mask（不上传明文梯度）→ 服务器聚合出
        # 全局 mask 并广播 → 客户端按全局 mask 加密上传选中的 batch。
        active_clients = [cid for cid in selected if cid not in dropped]
        global_mask = None
        if self.server.global_mask_vote.mode != 'none':
            client_masks = []
            for cid in active_clients:
                client = self.clients[cid]
                m = client.compute_local_mask(raw_updates[cid], client.ckks_manager,
                                              round_num=round_num)
                client_masks.append(m)
            valid_masks = [m for m in client_masks if m is not None]
            if valid_masks:
                global_mask = self.server.run_global_mask_vote(
                    client_masks, num_batches=len(valid_masks[0]),
                    round_num=round_num)

        # 第三阶段：加密上传 → 服务器同态聚合 → 密文域更新
        encrypted_updates, client_weights, upload_sizes = [], [], []
        for cid in active_clients:
            client = self.clients[cid]
            enc = client.encrypt_update(raw_updates[cid], client.ckks_manager,
                                        round_num=round_num,
                                        global_mask=global_mask)
            encrypted_updates.append(enc)
            client_weights.append(weights[cid])
            upload_sizes.append(client.ciphertext_size)

        aggregated = self.server.aggregate_encrypted_updates(
            encrypted_updates, client_weights, self.server_manager, upload_sizes)
        self.server.update_global_model(aggregated)

        # 客户端解密评估
        criterion = nn.CrossEntropyLoss()
        test_loss, test_acc = self.eval_client.evaluate_encrypted(
            self.server.get_encrypted_global_params(), self.test_loader, criterion)
        self.server.record_evaluation(test_loss, test_acc)
        self.acc_history.append(test_acc)

    def run(self, rounds: int, attack_inst: Optional[ClientUpdateAttack] = None) -> List[float]:
        for r in range(rounds):
            self.run_round(r, attack_inst)
        return list(self.acc_history)

    def evaluate_model(self, loader: DataLoader) -> float:
        """用当前全局模型（解密到 eval_client）在任意 loader 上算准确率"""
        criterion = nn.CrossEntropyLoss()
        self.eval_client.evaluate_encrypted(
            self.server.get_encrypted_global_params(), self.test_loader, criterion)
        self.eval_client.model.eval()
        correct, total = 0, 0
        with torch.no_grad():
            for data, target in loader:
                data, target = data.to(self.device), target.to(self.device)
                pred = self.eval_client.model(data).argmax(dim=1)
                correct += (pred == target).sum().item()
                total += target.size(0)
        return 100.0 * correct / max(1, total)


# =====================================================================
# 训练阶段攻击评估
# =====================================================================

def evaluate_update_attack(name: str, attack_cls, args, config, baseline_acc) -> Dict:
    """评估一个模型投毒/系统攻击"""
    malicious_ids = list(range(args.clients - args.malicious, args.clients))
    env = FLEnvironment(config, malicious_ids)
    attack_inst = attack_cls()
    t0 = time.time()
    acc_history = env.run(args.rounds, attack_inst)
    elapsed = time.time() - t0

    result = attack_metrics.summarize_rounds(acc_history,
                                             target_acc=baseline_acc)
    result.update({
        'attack': name,
        'category': getattr(attack_inst, 'category', 'model_poisoning'),
        'acc_history': [round(a, 2) for a in acc_history],
        'elapsed_sec': round(elapsed, 1),
    })
    if baseline_acc is not None:
        result['accuracy_drop'] = attack_metrics.accuracy_drop(baseline_acc, result['final_accuracy'])
        result['relative_drop'] = attack_metrics.relative_accuracy_drop(baseline_acc, result['final_accuracy'])
    return result


def evaluate_data_poison(name: str, poisoner: DataPoisoner, args, config, baseline_acc) -> Dict:
    """评估一个数据投毒攻击（毒化恶意客户端的本地数据）"""
    malicious_ids = list(range(args.clients - args.malicious, args.clients))

    # 拿到各客户端 loader 后毒化恶意客户端的数据，再注入环境重建
    client_loaders, test_loader = create_client_dataloaders(
        dataset_name=config['data']['dataset'],
        num_clients=config['system']['num_clients'],
        batch_size=config['system']['batch_size'],
        data_dir=config['data']['data_dir'],
        iid=config['data']['iid'], alpha=config['data'].get('alpha', 0.5))
    poisoned = {}
    for cid in malicious_ids:
        poisoned[cid] = poisoner.poison_loader(client_loaders[cid],
                                               config['data'].get('num_classes', 10))

    env = FLEnvironment(config, malicious_ids, poisoned_loaders=poisoned)
    t0 = time.time()
    acc_history = env.run(args.rounds)
    elapsed = time.time() - t0

    result = attack_metrics.summarize_rounds(acc_history, target_acc=baseline_acc)
    result.update({
        'attack': name,
        'category': 'data_poisoning',
        'accuracy_drop': attack_metrics.accuracy_drop(baseline_acc, result['final_accuracy']),
        'relative_drop': attack_metrics.relative_accuracy_drop(baseline_acc, result['final_accuracy']),
        'acc_history': [round(a, 2) for a in acc_history],
        'elapsed_sec': round(elapsed, 1),
    })

    # 后门类攻击：计算 ASR（用投毒器自己的触发器构造触发测试集）
    target_class = getattr(poisoner, 'target_class', None)
    apply_trigger = getattr(poisoner, 'apply_trigger', None)
    if target_class is not None and callable(apply_trigger):
        xs, ys = [], []
        for x, y in env.test_loader.dataset:
            xs.append(x)
            ys.append(y)
        x_all = torch.stack(xs)
        triggered = DataLoader(
            TensorDataset(apply_trigger(x_all), torch.tensor(ys)),
            batch_size=256, shuffle=False)
        result['asr'] = round(attack_metrics.attack_success_rate(
            env.eval_client.model, triggered, target_class, env.device), 2)
        result['clean_acc_final'] = round(env.evaluate_model(env.test_loader), 2)
    return result


# =====================================================================
# 隐私推断攻击评估
# =====================================================================

def evaluate_privacy_attacks(args, config, baseline_env: FLEnvironment) -> List[Dict]:
    """以基线全局模型为目标，评估全部隐私推断攻击"""
    device = config['system']['device']
    model = create_model(config['model']['name'], config)
    # 用基线最终全局参数加载目标模型
    criterion = nn.CrossEntropyLoss()
    baseline_env.eval_client.evaluate_encrypted(
        baseline_env.server.get_encrypted_global_params(),
        baseline_env.test_loader, criterion)
    model.load_state_dict(baseline_env.eval_client.model.state_dict())

    results = []
    victim_loader = baseline_env.client_loaders[0]
    model.to(device).eval()

    # 受害者场景：客户端 0 在本地一个 batch 上训练产生的梯度
    x, y = next(iter(victim_loader))
    x, y = x.to(device), y.to(device)
    for param in model.parameters():
        param.requires_grad_(True)
    model.zero_grad()
    loss = criterion(model(x), y)
    loss.backward()
    victim_gradient = [p.grad.clone() for p in model.parameters()]

    # 成员/非成员样本（成员=客户端0的训练数据，非成员=测试集）
    member_x, member_y = [], []
    for i, (bx, by) in enumerate(victim_loader):
        if i >= 4:
            break
        member_x.append(bx)
        member_y.append(by)
    member_loader = DataLoader(
        TensorDataset(torch.cat(member_x), torch.cat(member_y)), batch_size=64)

    nonmember_x, nonmember_y = [], []
    for i, (bx, by) in enumerate(baseline_env.test_loader):
        if i >= 4:
            break
        nonmember_x.append(bx)
        nonmember_y.append(by)
    nonmember_loader = DataLoader(
        TensorDataset(torch.cat(nonmember_x), torch.cat(nonmember_y)), batch_size=64)

    for name, cls in sorted(ATTACK_REGISTRY.items()):
        if not name.startswith('privacy:'):
            continue
        if args.attacks and name not in args.attacks:
            continue
        inst: PrivacyAttack = cls()
        t0 = time.time()
        try:
            if 'membership' in name:
                out = inst.run(model, device=device,
                               member_loader=member_loader,
                               nonmember_loader=nonmember_loader)
            else:
                # 梯度反演：用小 batch（前 4 张）控制运行时间
                out = inst.run(model, device=device,
                               victim_gradient=victim_gradient,
                               ground_truth=x[:4].detach().cpu(),
                               ground_truth_labels=y[:4].detach().cpu())
            out = {k: (round(v, 4) if isinstance(v, float) else v)
                   for k, v in out.items() if not torch.is_tensor(v)}
            out['attack'] = name
            out['category'] = 'privacy_inference'
            out['elapsed_sec'] = round(time.time() - t0, 1)
            results.append(out)
        except Exception as e:
            results.append({'attack': name, 'category': 'privacy_inference',
                            'error': f'{type(e).__name__}: {e}'})
    return results


# =====================================================================
# 报告
# =====================================================================

def generate_report(all_results: List[Dict], args, out_dir: str) -> None:
    lines = [
        "# HFL-Attack 攻击评估报告",
        "",
        f"- 数据集/模型: {args.dataset} / LeNet",
        f"- 客户端: {args.clients}（恶意 {args.malicious}） | 轮数: {args.rounds} "
        f"| 采样率: 0.5 | 加密: CKKS N=4096 [40,20,40] 2^20",
        f"- 密钥架构: 服务器仅持公钥，客户端解密评估",
        "",
        "## 训练阶段攻击（数据投毒 / 模型投毒 / 系统攻击）",
        "",
        "| 攻击 | 类别 | 最终准确率% | 准确率下降(pp) | 相对下降 | ASR% | 收敛轮数 |",
        "|---|---|---|---|---|---|---|",
    ]
    baseline = next((r for r in all_results if r.get('attack') == 'CLEAN_BASELINE'), None)
    for r in all_results:
        if r.get('attack') in ('CLEAN_BASELINE',) or r.get('category') == 'privacy_inference':
            continue
        asr = r.get('asr', '-')
        conv = r.get('convergence_round', '-')
        lines.append(
            f"| {r['attack']} | {r['category']} | {r['final_accuracy']:.2f} "
            f"| {r['accuracy_drop']:.2f} | {r['relative_drop']*100:.1f}% | {asr} | {conv} |")
    if baseline:
        lines += ["", f"**无攻击基线**: 最终准确率 {baseline['final_accuracy']:.2f}%"
                      f"（历史: {baseline.get('acc_history')}）", ""]

    lines += ["## 隐私推断攻击", "",
              "| 攻击 | MSE↓ | PSNR↑ | 标签恢复% | 梯度余弦相似度 | 成员AUC | 成员准确率 |",
              "|---|---|---|---|---|---|---|"]
    for r in all_results:
        if r.get('category') != 'privacy_inference':
            continue
        if 'error' in r:
            lines.append(f"| {r['attack']} | 错误: {r['error']} | | | | | |")
            continue
        lines.append(
            f"| {r['attack']} | {r.get('mse', '-')} | {r.get('psnr', '-')} "
            f"| {r.get('label_recovery_acc', '-')} | {r.get('cosine_similarity', '-')} "
            f"| {r.get('membership_auc', '-')} | {r.get('membership_acc', '-')} |")

    report_path = os.path.join(out_dir, 'report.md')
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    print(f"报告已保存: {report_path}")


# =====================================================================
# 主流程
# =====================================================================

def main():
    parser = argparse.ArgumentParser(description='HFL-Attack 攻击评估')
    parser.add_argument('--config', default='configs/config.yaml')
    parser.add_argument('--dataset', default='mnist')
    parser.add_argument('--clients', type=int, default=10)
    parser.add_argument('--malicious', type=int, default=2)
    parser.add_argument('--rounds', type=int, default=5)
    parser.add_argument('--device', default='cpu')
    parser.add_argument('--attacks', nargs='*', default=None,
                        help='只跑指定攻击（注册名），如 model:alie data:backdoor_badnets')
    parser.add_argument('--skip-privacy', action='store_true')
    parser.add_argument('--out', default=None)
    args = parser.parse_args()

    config = build_eval_config(args)
    ts = datetime.now().strftime('%m%d_%H%M%S')
    out_dir = args.out or os.path.join('results', f'attack_eval_{ts}')
    os.makedirs(out_dir, exist_ok=True)

    all_results: List[Dict] = []
    baseline_env: Optional[FLEnvironment] = None

    # ---------- 无攻击基线 ----------
    if not args.attacks or 'CLEAN_BASELINE' in args.attacks:
        print("=" * 70)
        print("[1/3] 无攻击基线")
        malicious_ids = list(range(args.clients - args.malicious, args.clients))
        baseline_env = FLEnvironment(config, malicious_ids)
        acc_history = baseline_env.run(args.rounds)
        baseline = attack_metrics.summarize_rounds(acc_history)
        baseline['attack'] = 'CLEAN_BASELINE'
        baseline['acc_history'] = [round(a, 2) for a in acc_history]
        all_results.append(baseline)
        baseline_acc = baseline['final_accuracy']
        print(f"基线最终准确率: {baseline_acc:.2f}% | 历史: {baseline['acc_history']}")
    else:
        # 未跑基线时用 configs 的经验值做参照（报告中注明）
        baseline_acc = None

    # ---------- 训练阶段攻击 ----------
    print("[2/3] 训练阶段攻击")
    for name, cls in sorted(ATTACK_REGISTRY.items()):
        if args.attacks and name not in args.attacks:
            continue
        if name.startswith('data:'):
            print(f"  评估数据投毒: {name}")
            r = evaluate_data_poison(name, cls(), args, config, baseline_acc)
        elif name.startswith('model:') or name.startswith('system:'):
            print(f"  评估更新攻击: {name}")
            r = evaluate_update_attack(name, cls, args, config, baseline_acc)
        else:
            continue
        all_results.append(r)
        print(f"    -> 最终准确率 {r['final_accuracy']:.2f}%"
              + (f" | ASR {r.get('asr')}%" if 'asr' in r else ""))

    # ---------- 隐私推断攻击 ----------
    if not args.skip_privacy:
        print("[3/3] 隐私推断攻击")
        if baseline_env is None:
            print("  (未跑基线，为隐私攻击单独训练基线模型...)")
            malicious_ids = list(range(args.clients - args.malicious, args.clients))
            baseline_env = FLEnvironment(config, malicious_ids)
            baseline_env.run(args.rounds)
        priv_results = evaluate_privacy_attacks(args, config, baseline_env)
        all_results.extend(priv_results)
        for r in priv_results:
            print(f"  {r['attack']}: { {k: v for k, v in r.items() if k not in ('attack', 'category')} }")

    # ---------- 保存 ----------
    with open(os.path.join(out_dir, 'results.json'), 'w', encoding='utf-8') as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    if baseline_acc is not None:
        generate_report(all_results, args, out_dir)


if __name__ == '__main__':
    main()
