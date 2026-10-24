"""base/config_loader.py 单元测试：extends 继承、深度合并、错误检测"""

import textwrap

import pytest
import yaml

from base.config_loader import ConfigError, load_config


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(textwrap.dedent(text), encoding='utf-8')
    return str(p)


BASE = """
system:
  num_clients: 50
  num_rounds: 50
  optimizer:
    name: adam
    lr: 0.001
data:
  dataset: cifar10
  iid: false
model:
  name: cnn
ckks:
  poly_modulus_degree: 8192
  coeff_mod_bit_sizes: [60, 40, 40, 60]
"""


class TestLoadConfig:
    def test_plain_config_no_extends(self, tmp_path):
        p = _write(tmp_path, "cfg.yaml", BASE)
        cfg = load_config(p)
        assert cfg['system']['num_clients'] == 50
        assert cfg['data']['dataset'] == 'cifar10'

    def test_extends_inherits_and_overrides(self, tmp_path):
        _write(tmp_path, "base.yaml", BASE)
        child = _write(tmp_path, "child.yaml", """
            extends: base.yaml
            system:
              num_rounds: 10          # 覆盖父值
            data:
              dataset: mnist          # 覆盖父值
            attack:
              enabled: true           # 父没有的段直接新增
        """)
        cfg = load_config(child)
        assert cfg['system']['num_rounds'] == 10          # 子覆盖
        assert cfg['system']['num_clients'] == 50         # 父继承
        assert cfg['data']['dataset'] == 'mnist'          # 子覆盖
        assert cfg['data']['iid'] is False                # 父继承（深层）
        assert cfg['attack']['enabled'] is True           # 新增段
        assert cfg['ckks']['poly_modulus_degree'] == 8192 # 父继承

    def test_extends_deep_merge_dict_not_replace(self, tmp_path):
        _write(tmp_path, "base.yaml", BASE)
        child = _write(tmp_path, "child.yaml", """
            extends: base.yaml
            system:
              num_rounds: 10          # optimizer 段应保留而不是被整个替换
        """)
        cfg = load_config(child)
        assert cfg['system']['optimizer'] == {'name': 'adam', 'lr': 0.001}

    def test_extends_chain(self, tmp_path):
        _write(tmp_path, "base.yaml", BASE)
        _write(tmp_path, "mid.yaml", """
            extends: base.yaml
            system:
              num_rounds: 20
        """)
        leaf = _write(tmp_path, "leaf.yaml", """
            extends: mid.yaml
            system:
              num_rounds: 5
        """)
        cfg = load_config(leaf)
        assert cfg['system']['num_rounds'] == 5    # 最里层优先
        assert cfg['system']['num_clients'] == 50  # 穿透两层继承

    def test_list_and_scalar_replaced_whole(self, tmp_path):
        _write(tmp_path, "base.yaml", BASE)
        child = _write(tmp_path, "child.yaml", """
            extends: base.yaml
            ckks:
              coeff_mod_bit_sizes: [40, 20, 40]   # list 整体替换
        """)
        cfg = load_config(child)
        assert cfg['ckks']['coeff_mod_bit_sizes'] == [40, 20, 40]

    def test_extends_removed_from_result(self, tmp_path):
        _write(tmp_path, "base.yaml", BASE)
        child = _write(tmp_path, "child.yaml", """
            extends: base.yaml
            system:
              num_rounds: 10
        """)
        assert 'extends' not in load_config(child)

    def test_cycle_detected(self, tmp_path):
        _write(tmp_path, "a.yaml", "extends: b.yaml\nsystem: {num_rounds: 1}\n")
        _write(tmp_path, "b.yaml", "extends: a.yaml\nsystem: {num_rounds: 2}\n")
        with pytest.raises(ConfigError, match="成环"):
            load_config(str(tmp_path / "a.yaml"))

    def test_missing_parent(self, tmp_path):
        child = _write(tmp_path, "child.yaml", """
            extends: nonexistent.yaml
            system:
              num_rounds: 10
        """)
        with pytest.raises(ConfigError, match="不存在"):
            load_config(child)

    def test_missing_file(self, tmp_path):
        with pytest.raises(ConfigError, match="配置文件不存在"):
            load_config(str(tmp_path / "nope.yaml"))

    def test_extends_must_be_string(self, tmp_path):
        bad = _write(tmp_path, "bad.yaml", "extends: [1, 2]\n")
        with pytest.raises(ConfigError, match="非空字符串"):
            load_config(bad)


class TestExperimentExtendsIntegration:
    """Experiment 经 extends 配置正确解析轨道与覆盖项"""

    def test_experiment_with_extends_config(self, tmp_path):
        import main  # noqa: F401  注册全部轨道（含 athfhe）
        from base.experiment import Experiment

        base = tmp_path / "base.yaml"
        base.write_text(yaml.safe_dump({
            'system': {'num_clients': 4, 'num_rounds': 50, 'num_epochs': 1,
                       'batch_size': 64, 'learning_rate': 0.001,
                       'device': 'cpu', 'random_seed': 42,
                       'client_sampling_rate': 1.0, 'upload_difference': True},
            'data': {'dataset': 'mnist', 'data_dir': './data',
                     'num_classes': 10, 'iid': True, 'alpha': 1},
            'model': {'name': 'mlp', 'hidden_dims': [16], 'dropout': 0.0},
            'encryption': {'enabled': True},
            'defense': {'aggregation_rule': 'fedavg'},
            'hierarchy': {'enabled': False, 'num_edges': 2},
            'ckks': {'poly_modulus_degree': 4096},
            'attack': {'enabled': False},
            'ppbr': {'enabled': False}, 'rfl': {'enabled': False},
            'athfhe_hier': {'enabled': False},
            'athfhe_twin': {'enabled': False, 'threshold': 2,
                            'ring_degree': 64, 'outer_modulus_bits': 60,
                            'scale_bits': 40, 'smudge_bits': 10,
                            'dropout': 0},
            'bandwidth': {},
            'output': {'results_dir': str(tmp_path / 'results'),
                       'model_dir': str(tmp_path / 'models'),
                       'save_interval': 999},
        }), encoding='utf-8')

        child = _write(tmp_path, "exp_twin.yaml", """
            extends: base.yaml
            system:
              num_rounds: 1            # 覆盖：验证差异项生效
            athfhe_twin:
              enabled: true            # 覆盖：开启双服务器轨道
        """)

        exp = Experiment(child, output_dir=str(tmp_path / 'out'))
        assert exp.active_track.name == 'athfhe_twin'   # 轨道选中
        assert exp.config['system']['num_rounds'] == 1  # 覆盖生效
        assert exp.config['system']['num_clients'] == 4  # 继承生效


class TestExperimentProfileConfigs:
    """configs/ 下的实验档案配置语义（消融对照的"模块"边界）"""

    def test_ckks_fedavg_profile_all_efficiency_off(self):
        cfg = load_config('configs/config_ckks_fedavg.yaml')
        assert cfg['encryption']['enabled'] is True
        assert cfg['ckks']['slot_packing'] is False
        assert cfg['ckks']['sparsification']['strategy'] == 'none'
        assert cfg['ckks']['ptopk']['enable'] is False
        assert cfg['global_mask_vote']['mode'] == 'none'
        # 互斥轨道保持关闭
        assert cfg['ppbr']['enabled'] is False
        assert cfg['rfl']['enabled'] is False

    def test_ckks_efficient_profile_all_efficiency_on(self):
        cfg = load_config('configs/config_ckks_efficient.yaml')
        assert cfg['encryption']['enabled'] is True
        assert cfg['ckks']['slot_packing'] is True
        assert cfg['ckks']['sparsification']['strategy'] == 'topk'
        assert cfg['ckks']['ptopk']['enable'] is True
        assert cfg['global_mask_vote']['mode'] == 'topk'

    def test_fedavg_vs_efficient_same_crypto_params(self):
        """消融隔离：两组档案密码学参数必须一致，差异仅限效率开关"""
        a = load_config('configs/config_ckks_fedavg.yaml')
        b = load_config('configs/config_ckks_efficient.yaml')
        for key in ('poly_modulus_degree', 'coeff_mod_bit_sizes', 'global_scale'):
            assert a['ckks'][key] == b['ckks'][key], f"消融组密码学参数不一致: {key}"

    def test_plain_fedavg_profile(self):
        cfg = load_config('configs/config_plain_fedavg.yaml')
        assert cfg['encryption']['enabled'] is False
        assert cfg['defense']['aggregation_rule'] == 'fedavg'
        # 明文模式与密文轨道互斥的前提
        assert cfg['ppbr']['enabled'] is False
        assert cfg['threshold']['enabled'] is False

    def test_track_configs_resolve(self):
        """各轨道实验配置能正确解析出对应轨道开关"""
        assert load_config('configs/config_athfhe_twin.yaml')['athfhe_twin']['enabled'] is True
        assert load_config('configs/config_athfhe_hier.yaml')['athfhe_hier']['enabled'] is True
        assert load_config('configs/config_rfl.yaml')['rfl']['enabled'] is True
