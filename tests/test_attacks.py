"""
攻击库单元测试：逐个验证 24 个攻击是否生效
============================================

运行方式：
    cd HFL-Attack
    python -m pytest tests/test_attacks.py -v

覆盖内容（不依赖 CKKS / 数据集下载，秒级完成）：
  1. 注册表完整性：24 个攻击全部注册；
  2. 模型投毒（9）：篡改行为符合预期（符号翻转 / 缩放 / 噪声 / 常数 / 后门 /
     mimic-ALIE / ALIE / Min-Max / Min-Sum）；
  3. 系统攻击（6）：全零 / 上一轮全局模型 / 伪装范数 / 女巫合谋 / 掉线与 DoS 参与模式；
  4. 数据投毒（5）：毒化后标签与图像变化符合各自攻击定义；
  5. 隐私推断（4）：在小模型 + 合成数据上能跑通并返回指标。
"""

import math

import numpy as np
import pytest
import torch
from torch.utils.data import DataLoader, TensorDataset

import hfl_attacks  # noqa: F401  触发全部 @register_attack
from hfl_attacks.base import ATTACK_REGISTRY, AttackContext


# =====================================================================
# 通用夹具
# =====================================================================

def _make_update(seed: int = 0) -> dict:
    g = torch.Generator().manual_seed(seed)
    return {
        "fc1.weight": torch.randn(8, 6, generator=g),
        "fc1.bias": torch.randn(8, generator=g),
        "fc2.weight": torch.randn(4, 8, generator=g),
        "fc2.bias": torch.randn(4, generator=g),
    }


def _flat_l2(update: dict) -> float:
    return math.sqrt(sum(float(v.pow(2).sum()) for v in update.values()))


def _make_honest_updates(n: int = 4, base_seed: int = 100) -> list:
    return [_make_update(base_seed + i) for i in range(n)]


def _collusion_ctx(self_update, honest, malicious_positions):
    """构造合谋上下文：all_updates = 诚实更新 + 合谋者更新交错排列"""
    all_updates = []
    honest_iter = iter(honest)
    colluding = {}  # position -> update
    for pos, upd in malicious_positions.items():
        colluding[pos] = upd
    h_idx = 0
    for pos in range(len(honest) + len(colluding)):
        if pos in colluding:
            all_updates.append(colluding[pos])
        else:
            all_updates.append(next(honest_iter))
    return AttackContext(
        global_params=None,
        self_update=self_update,
        all_updates=all_updates,
        self_index=list(colluding.keys())[0],
        malicious_indices=sorted(colluding.keys()),
        num_classes=10,
        round_num=0,
    )


HONEST = None  # lazy init（torch RNG 在 import 时不可控）


@pytest.fixture()
def honest():
    return _make_honest_updates(4)


# =====================================================================
# 1. 注册表完整性
# =====================================================================

EXPECTED_ATTACKS = [
    # 数据投毒
    "data:label_flipping_random",
    "data:label_flipping_targeted",
    "data:backdoor_badnets",
    "data:backdoor_blend",
    "data:backdoor_edgecase",
    # 模型投毒
    "model:sign_flipping",
    "model:scaling",
    "model:gaussian_noise",
    "model:constant",
    "model:backdoor_model",
    "model:mimic_alie",
    "model:alie",
    "model:min_max",
    "model:min_sum",
    "model:ipm",
    # 系统攻击
    "system:free_riding_zeros",
    "system:free_riding_previous",
    "system:free_riding_masked",
    "system:sybil_collusion",
    "system:random_dropout",
    "system:persistent_dos",
    # 隐私推断
    "privacy:dlg",
    "privacy:idlg",
    "privacy:inverting_gradients",
    "privacy:membership_inference",
]


def test_registry_completeness():
    missing = [n for n in EXPECTED_ATTACKS if n not in ATTACK_REGISTRY]
    assert not missing, f"未注册的攻击: {missing}"
    assert len(ATTACK_REGISTRY) == len(EXPECTED_ATTACKS), \
        f"注册数量不符: {len(ATTACK_REGISTRY)} != {len(EXPECTED_ATTACKS)}"


def test_registry_instantiable():
    for name in EXPECTED_ATTACKS:
        cls = ATTACK_REGISTRY[name]
        try:
            cls()
        except TypeError as e:
            pytest.fail(f"{name} 无参实例化失败: {e}")


# =====================================================================
# 2. 模型投毒攻击
# =====================================================================

def test_sign_flipping():
    atk = ATTACK_REGISTRY["model:sign_flipping"]()
    upd = _make_update(0)
    out = atk.attack(upd, AttackContext(self_update=upd))
    for k in upd:
        assert torch.allclose(out[k], -upd[k]), f"{k} 未被符号翻转"


def test_scaling():
    atk = ATTACK_REGISTRY["model:scaling"](scale_factor=10.0)
    upd = _make_update(0)
    out = atk.attack(upd, AttackContext(self_update=upd))
    for k in upd:
        assert torch.allclose(out[k], 10.0 * upd[k]), f"{k} 未被缩放 10 倍"


def test_gaussian_noise():
    atk = ATTACK_REGISTRY["model:gaussian_noise"](std=1.0)
    upd = _make_update(0)
    out = atk.attack(upd, AttackContext(self_update=upd))
    for k in upd:
        assert not torch.allclose(out[k], upd[k]), f"{k} 未被噪声修改"
        assert torch.isfinite(out[k]).all()


def test_constant():
    atk = ATTACK_REGISTRY["model:constant"](constant_value=3.0)
    upd = _make_update(0)
    out = atk.attack(upd, AttackContext(self_update=upd))
    for k in upd:
        assert torch.allclose(out[k], torch.full_like(upd[k], 3.0)), \
            f"{k} 未被替换为常数"


def test_backdoor_model():
    atk = ATTACK_REGISTRY["model:backdoor_model"](scale_factor=10.0)
    upd = _make_update(0)
    out = atk.attack(upd, AttackContext(self_update=upd))
    for k in upd:
        assert set(out.keys()) == set(upd.keys())
        assert not torch.allclose(out[k], upd[k]), f"{k} 后门攻击未改变更新"
        assert torch.isfinite(out[k]).all()


def test_mimic_alie():
    atk = ATTACK_REGISTRY["model:mimic_alie"]()
    upd = _make_update(0)
    out = atk.attack(upd, AttackContext(self_update=upd))
    for k in upd:
        assert torch.isfinite(out[k]).all()


@pytest.mark.parametrize("name", ["model:alie", "model:min_max", "model:min_sum"])
def test_collusion_update_attacks(name, honest):
    """合谋攻击：需要 all_updates 上下文，输出应为合法的张量更新"""
    atk = ATTACK_REGISTRY[name]()
    self_upd = _make_update(7)
    # 恶意客户端位于位置 4（最后一个）
    ctx = AttackContext(
        global_params=None,
        self_update=self_upd,
        all_updates=honest + [self_upd],
        self_index=4,
        malicious_indices=[4],
        num_classes=10,
        round_num=0,
    )
    out = atk.attack(self_upd, ctx)
    assert set(out.keys()) == set(self_upd.keys())
    for k in self_upd:
        assert out[k].shape == self_upd[k].shape
        assert torch.isfinite(out[k]).all()
    # 缺少合谋上下文时必须显式报错
    with pytest.raises(Exception):
        atk.attack(self_upd, AttackContext(self_update=self_upd))


# =====================================================================
# 3. 系统攻击
# =====================================================================

def test_free_riding_zeros():
    atk = ATTACK_REGISTRY["system:free_riding_zeros"]()
    upd = _make_update(0)
    out = atk.attack(upd, AttackContext(self_update=upd))
    for k in upd:
        assert torch.all(out[k] == 0), f"{k} 不是全零更新"


def test_free_riding_previous():
    atk = ATTACK_REGISTRY["system:free_riding_previous"]()
    upd = _make_update(0)
    gp = _make_update(50)
    # 有全局模型时上传全局模型本身
    out = atk.attack(upd, AttackContext(global_params=gp, self_update=upd))
    for k in upd:
        assert torch.equal(out[k], gp[k])
    # 无全局模型（本框架密钥分离）时退化为 δ0 全零
    out = atk.attack(upd, AttackContext(global_params=None, self_update=upd))
    for k in upd:
        assert torch.all(out[k] == 0)


def test_free_riding_masked(honest):
    atk = ATTACK_REGISTRY["system:free_riding_masked"](seed=0)
    upd = _make_update(7)
    ctx = AttackContext(
        global_params=None, self_update=upd,
        all_updates=honest + [upd], self_index=4,
        malicious_indices=[4],
    )
    out = atk.attack(upd, ctx)
    honest_avg = sum(_flat_l2(u) for u in honest) / len(honest)
    assert abs(_flat_l2(out) - honest_avg) / honest_avg < 0.5, \
        "伪装更新范数应接近诚实更新平均范数"


def test_sybil_collusion(honest):
    atk = ATTACK_REGISTRY["system:sybil_collusion"]()
    m1, m2 = _make_update(20), _make_update(21)
    expected_mean = {
        k: (m1[k] + m2[k]) / 2 for k in m1
    }
    # 恶意客户端位于位置 2 和 4
    for self_pos, self_upd in ((2, m1), (4, m2)):
        ctx = _collusion_ctx(self_upd, honest, {2: m1, 4: m2})
        out = atk.attack(self_upd, ctx)
        for k in self_upd:
            assert torch.allclose(out[k], expected_mean[k], atol=1e-6), \
                "女巫合谋者应上传所有合谋者更新的均值"


def test_random_dropout_participation():
    always_off = ATTACK_REGISTRY["system:random_dropout"](drop_rate=1.0, seed=0)
    always_on = ATTACK_REGISTRY["system:random_dropout"](drop_rate=0.0, seed=0)
    assert not always_off.participates(0)
    assert always_on.participates(0)
    # drop_rate=0.5 时参与率应接近 50%
    half = ATTACK_REGISTRY["system:random_dropout"](drop_rate=0.5, seed=0)
    rate = np.mean([half.participates(r) for r in range(200)])
    assert 0.35 < rate < 0.65


def test_persistent_dos():
    atk = ATTACK_REGISTRY["system:persistent_dos"](active_every=3)
    pattern = [atk.participates(r) for r in range(7)]
    assert pattern == [True, False, False, True, False, False, True]


# =====================================================================
# 4. 数据投毒攻击
# =====================================================================

def _make_labeled_loader(labels, n_channels=1, size=8, seed=0):
    g = torch.Generator().manual_seed(seed)
    xs = torch.rand(len(labels), n_channels, size, size, generator=g)
    ys = torch.tensor(labels, dtype=torch.long)
    return DataLoader(TensorDataset(xs, ys), batch_size=len(labels), shuffle=False)


def _loader_labels(loader):
    return torch.cat([y for _, y in loader]).tolist()


def _loader_images(loader):
    return torch.cat([x for x, _ in loader])


@pytest.mark.parametrize("name", [
    "data:label_flipping_random",
    "data:label_flipping_targeted",
    "data:backdoor_badnets",
    "data:backdoor_blend",
    "data:backdoor_edgecase",
])
def test_data_poisoners_run_and_change_labels(name):
    atk = ATTACK_REGISTRY[name]()
    loader = _make_labeled_loader([0, 1, 2, 3, 4, 5, 6, 7])
    poisoned = atk.poison_loader(loader, num_classes=10)
    new_labels = _loader_labels(poisoned)
    assert new_labels != [0, 1, 2, 3, 4, 5, 6, 7], f"{name} 没有改动任何标签"


def test_label_flipping_random_stats():
    atk = ATTACK_REGISTRY["data:label_flipping_random"](poison_rate=1.0)
    labels = [0, 0, 1, 1, 2, 2, 3, 3]
    poisoned = atk.poison_loader(_make_labeled_loader(labels), num_classes=10)
    changed = sum(1 for a, b in zip(labels, _loader_labels(poisoned)) if a != b)
    assert changed == len(labels), "随机翻转应翻转全部标签（poison_rate=1.0）"


def test_label_flipping_targeted_only_source_class():
    atk = ATTACK_REGISTRY["data:label_flipping_targeted"](
        source_class=2, target_class=9)
    labels = [0, 2, 2, 3, 2, 5]
    poisoned = atk.poison_loader(_make_labeled_loader(labels), num_classes=10)
    new = _loader_labels(poisoned)
    for orig, new_l in zip(labels, new):
        if orig == 2:
            assert new_l == 9, "源类样本应被翻转到目标类"
        else:
            assert new_l == orig, "非源类样本不应被改动"


def test_backdoor_badnets_trigger_and_labels():
    atk = ATTACK_REGISTRY["data:backdoor_badnets"](
        target_class=5, poison_rate=0.5)
    labels = [1, 2, 3, 4, 6, 7, 8, 9]
    loader = _make_labeled_loader(labels)
    poisoned = atk.poison_loader(loader, num_classes=10)
    new = _loader_labels(poisoned)
    flipped = [i for i, (a, b) in enumerate(zip(labels, new)) if b == 5]
    assert 0 < len(flipped) <= len(labels), "部分样本标签应变为目标类"
    # apply_trigger 应改变像素且不改变标签语义接口
    x = torch.rand(2, 1, 8, 8)
    x_trig = atk.apply_trigger(x)
    assert x_trig.shape == x.shape
    assert not torch.equal(x_trig, x)


def test_backdoor_blend_trigger():
    atk = ATTACK_REGISTRY["data:backdoor_blend"](target_class=3, poison_rate=1.0)
    labels = [0, 1, 2, 4]
    poisoned = atk.poison_loader(_make_labeled_loader(labels), num_classes=10)
    assert all(l == 3 for l in _loader_labels(poisoned))
    x = torch.rand(2, 1, 8, 8)
    x_trig = atk.apply_trigger(x)
    assert not torch.equal(x_trig, x)


def test_backdoor_edgecase_images_unchanged():
    atk = ATTACK_REGISTRY["data:backdoor_edgecase"](
        source_class=7, target_class=0)
    labels = [7, 7, 1, 7, 2]
    loader = _make_labeled_loader(labels)
    before = _loader_images(loader)
    poisoned = atk.poison_loader(loader, num_classes=10)
    after = _loader_images(poisoned)
    assert torch.equal(before, after), "edge-case 攻击不应改动图像"
    new = _loader_labels(poisoned)
    for orig, new_l in zip(labels, new):
        assert new_l == (0 if orig == 7 else orig)


# =====================================================================
# 5. 隐私推断攻击
# =====================================================================

@pytest.fixture(scope="module")
def privacy_setup():
    """合成 MNIST 形状的 batch + 小 MLP 模型 + 真实梯度"""
    from hfl_attacks.privacy_inference._utils import (
        make_test_model, make_victim_gradient)
    torch.manual_seed(0)
    model = make_test_model("mlp")
    x = torch.rand(2, 1, 28, 28)
    y = torch.tensor([3, 7])
    victim = make_victim_gradient(model, x, y)
    return model, x, y, victim


@pytest.mark.parametrize("name,iters", [
    ("privacy:dlg", 30),
    ("privacy:idlg", 30),
    ("privacy:inverting_gradients", 30),
])
def test_gradient_inversion_attacks(name, iters, privacy_setup):
    model, x, y, victim = privacy_setup
    atk = ATTACK_REGISTRY[name](iters=iters)
    res = atk.run(model, device="cpu",
                  victim_gradient=victim,
                  ground_truth=x,
                  ground_truth_labels=y)
    for key in ("mse", "psnr", "cosine_similarity", "label_recovery_acc"):
        assert key in res, f"{name} 缺少指标 {key}"
        assert np.isfinite(res[key]), f"{name} 指标 {key} 非有限值"
    assert res["reconstructed"].shape == x.shape


def test_membership_inference():
    from hfl_attacks.privacy_inference._utils import make_test_model
    torch.manual_seed(0)
    model = make_test_model("mlp")
    xs = torch.rand(16, 1, 28, 28)
    ys = torch.randint(0, 10, (16,))
    loader = DataLoader(TensorDataset(xs, ys), batch_size=8, shuffle=False)
    atk = ATTACK_REGISTRY["privacy:membership_inference"](score="loss")
    res = atk.run(model, member_loader=loader, nonmember_loader=loader)
    for key in ("membership_auc", "membership_acc", "membership_advantage"):
        assert key in res, f"缺少指标 {key}"
        assert np.isfinite(res[key])
    assert 0.0 <= res["membership_auc"] <= 1.0
