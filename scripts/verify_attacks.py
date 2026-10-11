"""
攻击全量生效验证脚本（非层次模式）
==================================

对 hfl_attacks 注册表中全部 24 个攻击逐一验证：

- data:/model:/system: 攻击：构造临时配置（hierarchy 关闭），跑 1 轮完整
  CKKS 加密训练，断言攻击实例已注入、恶意客户端已标记、训练不报错、指标产出。
- privacy: 攻击：不修改训练过程，验证初始化注入成功并给出 runner 提示。

用法：
    python scripts/verify_attacks.py [--quick]   # --quick 时只验证初始化不跑训练轮
"""

import argparse
import sys
import tempfile
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hfl_attacks import ATTACK_REGISTRY, DataPoisoner, PrivacyAttack  # noqa: E402

TRAIN_ATTACKS = [n for n in ATTACK_REGISTRY if not n.startswith('privacy:')]
PRIVACY_ATTACKS = [n for n in ATTACK_REGISTRY if n.startswith('privacy:')]


def build_config(tmp: Path, attack_name: str) -> str:
    with open('configs/config.yaml', encoding='utf-8') as f:
        cfg = yaml.safe_load(f)
    cfg['attack'] = {'enabled': True, 'name': attack_name, 'malicious_count': 2}
    # 非层次模式：确保云-边-端关闭
    cfg['hierarchy']['enabled'] = False
    # 轻量配置，保证单轮可在数十秒内完成
    cfg['system']['num_clients'] = 10
    cfg['system']['num_rounds'] = 1
    cfg['system']['device'] = 'cpu'
    cfg['data']['dataset'] = 'mnist'
    cfg['ckks']['poly_modulus_degree'] = 4096
    cfg['ckks']['coeff_mod_bit_sizes'] = [40, 20, 40]
    cfg['ckks']['global_scale'] = 1048576
    path = tmp / f'cfg_{attack_name.replace(":", "_")}.yaml'
    with open(path, 'w', encoding='utf-8') as f:
        yaml.safe_dump(cfg, f, allow_unicode=True)
    return str(path)


def verify_one(attack_name: str, quick: bool, tmp: str) -> str:
    from main import CKKSFedExperiment
    cfg_path = build_config(Path(tmp), attack_name)
    exp = CKKSFedExperiment(cfg_path, output_dir=tmp)
    assert exp.hierarchy is None, "verify_attacks 必须在非层次模式下运行"
    assert exp.attack_inst is not None, f"{attack_name}: 攻击未注入"
    assert exp.malicious_ids == [8, 9], \
        f"{attack_name}: 恶意客户端错误 {exp.malicious_ids}"

    if isinstance(exp.attack_inst, DataPoisoner):
        for cid in exp.malicious_ids:
            assert exp.clients[cid].train_loader is not exp.client_loaders[cid], \
                f"{attack_name}: 客户端 {cid} 数据未毒化"
    if isinstance(exp.attack_inst, PrivacyAttack):
        return 'OK (训练空操作, runner 评估)'

    if quick:
        return 'OK (init)'

    exp.run_experiment()
    acc = exp.metrics.get_metrics().get('test_acc', [])
    assert len(acc) == 1 and 0.0 <= acc[0] <= 100.0, \
        f"{attack_name}: 指标异常 acc={acc}"
    return f'OK (1 round, acc={acc[0]:.2f}%)'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--quick', action='store_true', help='只验证初始化，不跑训练轮')
    args = parser.parse_args()

    names = sorted(TRAIN_ATTACKS) + sorted(PRIVACY_ATTACKS)
    failed = []
    # 每个攻击使用独立输出目录（实验日志 FileHandler 在进程内不关闭，
    # 同目录复用会导致 Windows 下日志文件被占用，故不做临时目录自动清理）
    root = Path(tempfile.mkdtemp(prefix='hfl_attack_verify_'))
    for name in names:
        out_dir = root / name.replace(':', '_')
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            status = verify_one(name, args.quick, str(out_dir))
            print(f"[PASS] {name:<34} {status}", flush=True)
        except Exception as e:  # noqa: BLE001
            failed.append(name)
            print(f"[FAIL] {name:<34} {type(e).__name__}: {e}", flush=True)

    print(f"\n输出目录: {root}")
    print(f"{len(names) - len(failed)}/{len(names)} 攻击验证通过"
          + (f"，失败: {failed}" if failed else ""))
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
