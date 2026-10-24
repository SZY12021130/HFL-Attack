"""
RFL 测试（Zhou et al. IEEE TDSC 2026 的算法 2，RFL 轨道）
==========================================================

运行方式：
    python -m pytest tests/test_rfl.py -v

覆盖：
  1. 模型切分：MLP Ls=1/2 切分前后前向等价、CNN conv/fc 切分、不支持模型报错；
  2. 端到端：3 轮完整 RFL 协议跑通，式(16)(17) 验证通过且 loss 下降；
  3. 可验证性：恶意 CS drop_edge / scale_edge 篡改 → 式(17) 校验失败、
     全体 ES 中止本轮（返回零更新）；
  4. IFGIA 防御：CS 伪造的虚拟客户端无 EC→ES 直连注册身份，式(9) 验签
     被拒，不影响诚实客户端聚合；
  5. 维度无关性（Theorem 4）：客户端线上字节数只取决于浅层输出维度，
     不随深层维度增长。

密码原语测试（签名 / 同态哈希 / DP 尺度）见 tests/test_rfl_crypto.py。
"""

import numpy as np
import pytest
import torch
from torch.utils.data import DataLoader, TensorDataset


def _make_loaders(num_clients=6, per_client=40, batch=20, seed=0):
    """合成玩具数据：均值符号决定标签，可分。"""
    g = torch.Generator().manual_seed(seed)
    X = torch.randn(num_clients * per_client, 1, 28, 28, generator=g)
    y = (X.mean(dim=(1, 2, 3)) > 0).long()
    loaders = {}
    for c in range(num_clients):
        ds = TensorDataset(X[c * per_client:(c + 1) * per_client],
                           y[c * per_client:(c + 1) * per_client])
        loaders[c] = DataLoader(ds, batch_size=batch, shuffle=True)
    return loaders


def _make_model(hidden=(16, 8), dropout=0.0):
    from base.models import MLP
    torch.manual_seed(0)
    return MLP(input_dim=784, hidden_dims=list(hidden), num_classes=10,
               dropout=dropout)


GROUPS = {0: [0, 1], 1: [2, 3], 2: [4, 5]}
BASE_CFG = {'rfl': {'enabled': True, 'hash_bits': 128, 'epsilon': 5.0}}


# =====================================================================
# 1. 模型切分
# =====================================================================

def test_split_forward_equivalence_mlp():
    """切分等价性：deep(shallow(x)) ≡ 原模型输出（Ls=1 与 Ls=2）。"""
    from rfl import build_split
    for ls in (1, 2):
        model = _make_model(hidden=(16, 8))
        shallow, deep, sn, dn = build_split(model, ls)
        x = torch.randn(4, 1, 28, 28)
        with torch.no_grad():
            ref, out = model(x), deep(shallow(x))
        assert torch.allclose(ref, out, atol=1e-6), f"Ls={ls} 切分不等价"
        # 参数名映射覆盖全局模型全部参数且无重复
        all_names = sn + dn
        assert len(all_names) == len(set(all_names))
        assert set(all_names) == {k for k, _ in model.named_parameters()}


def test_split_forward_equivalence_cnn():
    from rfl import build_split
    from base.models import CNN
    torch.manual_seed(0)
    model = CNN(input_channels=1, num_classes=10, dropout=0.0)
    shallow, deep, sn, dn = build_split(model, 1)
    x = torch.randn(4, 1, 28, 28)
    with torch.no_grad():
        assert torch.allclose(model(x), deep(shallow(x)), atol=1e-6)


def test_split_rejects_unsupported_and_bad_cut():
    from rfl import build_split
    from base.models import LeNet
    model = _make_model(hidden=(16, 8))
    with pytest.raises(ValueError):
        build_split(model, 3)          # Ls 超过隐藏层数
    with pytest.raises(ValueError):
        build_split(LeNet(1, 10), 1)   # 暂不支持 LeNet


# =====================================================================
# 2. 端到端协议
# =====================================================================

def test_end_to_end_rounds_verify_and_converge():
    """3 轮完整协议：式(16)(17) 通过，训练 loss 下降，模型参数被更新。"""
    from rfl import RFLProtocol
    loaders = _make_loaders()
    model = _make_model()
    proto = RFLProtocol(BASE_CFG, seed=42)
    losses = []
    for r in range(3):
        grad, aborted = proto.run_round(r, loaders, GROUPS, model)
        assert not aborted and proto.last_stats['verified']
        losses.append(proto.last_stats['mean_loss'])
        with torch.no_grad():
            for n, p in model.named_parameters():
                p.add_(grad[n], alpha=-0.5)
    assert losses[-1] < losses[0], f"loss 未下降: {losses}"
    # 参数确实被更新（未中止、非零更新）
    assert any(v.abs().sum() > 0 for v in grad.values())


def test_new_client_integration_registers_identity():
    """新客户端集成：首轮注册 pk，注册表记录身份；后续轮复用。"""
    from rfl import RFLProtocol
    loaders = _make_loaders()
    model = _make_model()
    proto = RFLProtocol(BASE_CFG, seed=42)
    proto.run_round(0, loaders, GROUPS, model)
    for eid, cids in GROUPS.items():
        registered = proto.registered_clients(eid)
        assert set(cids) <= set(registered)
    n_after_first = sum(len(proto.registered_clients(e)) for e in GROUPS)
    proto.run_round(1, loaders, GROUPS, model)
    n_after_second = sum(len(proto.registered_clients(e)) for e in GROUPS)
    assert n_after_second == n_after_first, "注册应只发生一次（身份长期有效）"


# =====================================================================
# 3. 可验证性：恶意 CS 篡改聚合（式 16/17）
# =====================================================================

@pytest.mark.parametrize("tamper", ["drop_edge", "scale_edge"])
def test_cs_tamper_triggers_abort(tamper):
    """云丢弃/缩放某边缘更新 → 式(17) 校验失败 → 全体 ES 中止（零更新）。"""
    from rfl import RFLProtocol
    loaders = _make_loaders()
    model = _make_model()
    cfg = {'rfl': dict(BASE_CFG['rfl'], cs_tamper=tamper)}
    proto = RFLProtocol(cfg, seed=42)
    grad, aborted = proto.run_round(0, loaders, GROUPS, model)
    assert aborted
    assert not proto.last_stats['verified']
    assert proto.last_stats['tampered'] is not None
    assert all(v.abs().sum() == 0 for v in grad.values()), \
        "中止轮必须返回零更新（ES 退出系统，不接受被篡改的聚合）"


# =====================================================================
# 4. IFGIA 防御（式 9）
# =====================================================================

def test_ifgia_fabricated_clients_rejected():
    """CS 伪造的虚拟客户端（无注册身份）被式(9)验签拒绝，诚实聚合不受影响。"""
    from rfl import RFLProtocol
    loaders = _make_loaders()
    model = _make_model()
    cfg = {'rfl': dict(BASE_CFG['rfl'], simulate_ifgia=True,
                       ifgia_per_edge=2)}
    proto = RFLProtocol(cfg, seed=42)
    grad, aborted = proto.run_round(0, loaders, GROUPS, model)
    st = proto.last_stats
    assert not aborted and st['verified']
    assert st['rejected_fabricated'] == 2 * len(GROUPS)
    # 伪造客户端未进入注册表
    for eid in GROUPS:
        assert all(cid >= 0 for cid in proto.registered_clients(eid))


# =====================================================================
# 5. 维度无关性（Theorem 4）
# =====================================================================

def test_client_wire_independent_of_deep_dimension():
    """EC 通信量只取决于浅层输出维度（hidden[0] 相同、深层宽度不同）。"""
    from rfl import RFLProtocol
    loaders = _make_loaders()

    def client_wire(hidden):
        proto = RFLProtocol(BASE_CFG, seed=42)
        proto.run_round(0, loaders, GROUPS, _make_model(hidden=hidden))
        w = proto.last_wire
        return (w['client_smashed'] + w['client_sig']
                + w['client_outgrad'] + w['client_gradback']
                + w['client_shallow'])

    w_small = client_wire((16, 8))
    w_large = client_wire((16, 128))   # 深层维度 ×16，浅层不变
    assert w_small == w_large, \
        f"EC 通信量应维度无关: small={w_small} vs large={w_large}"
