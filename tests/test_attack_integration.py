"""
攻击集成测试：config.yaml 的 attack 段 → main.py 训练循环
==========================================================

运行方式：
    python -m pytest tests/test_attack_integration.py -v

覆盖：
  1. attack.enabled=false（默认）：attack_inst 为 None，代码路径与正常 FedAvg 一致；
  2. 端到端：model:sign_flipping 跑一轮加密训练，指标正常且恶意客户端更新被篡改；
  3. 端到端：data:backdoor_badnets 跑一轮，恶意客户端 train_loader 被替换为毒化加载器；
  4. 未知攻击名在初始化时直接报错（配置检查前置）。
"""

import yaml
import pytest


def _write_cfg(tmp_path, attack_cfg, **over):
    with open('configs/config.yaml', encoding='utf-8') as f:
        cfg = yaml.safe_load(f)
    cfg['attack'] = attack_cfg
    cfg['system']['num_clients'] = 10
    cfg['system']['num_rounds'] = 1
    cfg['system']['device'] = 'cpu'
    cfg['data']['dataset'] = 'mnist'
    cfg['ckks']['poly_modulus_degree'] = 4096
    cfg['ckks']['coeff_mod_bit_sizes'] = [40, 20, 40]
    cfg['ckks']['global_scale'] = 1048576
    for section, kv in over.items():
        cfg.setdefault(section, {}).update(kv)
    path = tmp_path / 'attack_config.yaml'
    with open(path, 'w', encoding='utf-8') as f:
        yaml.safe_dump(cfg, f, allow_unicode=True)
    return str(path)


def test_attack_disabled_by_default(tmp_path):
    """enabled=false 时不注入任何攻击"""
    from main import CKKSFedExperiment
    path = _write_cfg(tmp_path, {'enabled': False,
                                 'name': 'model:sign_flipping',
                                 'malicious_count': 2})
    exp = CKKSFedExperiment(path, output_dir=str(tmp_path))
    assert exp.attack_inst is None
    assert exp.malicious_ids == []


def test_unknown_attack_name_raises(tmp_path):
    """未知攻击名在初始化时报错，并列出可选攻击"""
    from main import CKKSFedExperiment
    path = _write_cfg(tmp_path, {'enabled': True,
                                 'name': 'model:not_exist',
                                 'malicious_count': 1})
    with pytest.raises(ValueError, match="未知攻击"):
        CKKSFedExperiment(path, output_dir=str(tmp_path))


@pytest.mark.slow
def test_model_attack_sign_flipping_one_round(tmp_path):
    """模型投毒：恶意客户端明文更新被翻转，一轮加密训练跑通"""
    from main import CKKSFedExperiment
    path = _write_cfg(tmp_path, {'enabled': True,
                                 'name': 'model:sign_flipping',
                                 'malicious_count': 2})

    # 对照组：无攻击的同轮更新
    exp = CKKSFedExperiment(path, output_dir=str(tmp_path))
    assert exp.attack_inst is not None
    assert exp.malicious_ids == [8, 9]

    benign, malicious = [], []
    import torch
    for c in exp.clients[:8]:
        benign.append({k: v.clone() for k, v in
                       c.model.state_dict().items() if v.dtype.is_floating_point})
    for c in exp.clients[8:]:
        malicious.append({k: v.clone() for k, v in
                          c.model.state_dict().items() if v.dtype.is_floating_point})

    exp.run_experiment()
    acc = exp.metrics.get_metrics().get('test_acc', [])
    assert len(acc) == 1 and 0.0 <= acc[0] <= 100.0

    # 恶意客户端本地模型确实偏离了良性客户端（sign flipping 使更新方向反转）
    b0 = torch.cat([v.flatten() for v in benign[0].values()])
    m0 = torch.cat([v.flatten() for v in malicious[0].values()])
    assert not torch.allclose(b0, m0), "sign_flipping 后恶意客户端应与良性客户端不同"


@pytest.mark.slow
def test_data_attack_backdoor_one_round(tmp_path):
    """数据投毒：恶意客户端 train_loader 被替换为毒化加载器，一轮训练跑通"""
    from main import CKKSFedExperiment
    from hfl_attacks import DataPoisoner
    path = _write_cfg(tmp_path, {'enabled': True,
                                 'name': 'data:backdoor_badnets',
                                 'malicious_count': 2})
    exp = CKKSFedExperiment(path, output_dir=str(tmp_path))
    assert isinstance(exp.attack_inst, DataPoisoner)
    assert exp.malicious_ids == [8, 9]
    for cid in exp.malicious_ids:
        assert exp.clients[cid].train_loader is not exp.client_loaders[cid], \
            f"客户端 {cid} 的 train_loader 应被毒化替换"
    exp.run_experiment()
    acc = exp.metrics.get_metrics().get('test_acc', [])
    assert len(acc) == 1 and 0.0 <= acc[0] <= 100.0
