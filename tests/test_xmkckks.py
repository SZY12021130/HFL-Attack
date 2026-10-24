# -*- coding: utf-8 -*-
"""
测试 xMKCKKS_Go2Py 库（GitHub: sustainable-computing/xMKCKKS_Go2Py）

用途：验证唯一可 Python 调用的 MK-CKKS 多密钥同态加密库在本机可用。
测试内容（双人 / 三人 MK-CKKS 全流程）：
  1. 每个参与方独立生成自己的密钥对 (sk_i, pk_i) —— 无 TTP，各自持私钥
  2. 服务器聚合公钥 P 环（xMKCKKS 特色，构造联合公钥）
  3. 各客户端用聚合后公钥加密自己的梯度向量
  4. 服务器对密文做同态加法 + 密文×常数（模拟 FedAvg 聚合）
  5. 各客户端用各自私钥做部分解密 partialDecrypt，上传部分解密份额
  6. 服务器 ringQAddLvl 聚合部分解密份额，decodeAfterPartialDecrypt 恢复明文和
  7. 负例：只聚合部分（而非全部）参与方的解密份额 → 解密失败
     （确认 xMKCKKS 是 n-out-of-n 语义，不具备阈值/掉线容忍能力）

前置：需要用 Go 工具链构建共享库（已在 E:/tmp/xMKCKKS_Go2Py 完成）：
    cd xMKCKKS_Go2Py
    go build -o xmkckks.dll -buildmode=c-shared export.go   # Windows

运行：python tests/test_xmkckks.py   （或 pytest tests/test_xmkckks.py -v）
"""

import copy
import os
import time
from ctypes import (
    CDLL, POINTER, Structure, byref, c_bool, c_double, c_int, c_size_t,
    c_ulonglong,
)

import numpy as np

DLL_PATH = os.environ.get(
    "XMKCKKS_DLL",
    r"E:\tmp\xMKCKKS_Go2Py\xmkckks.dll",
)

# ---------------------------------------------------------------------------
# ctypes 结构体定义（与 xmkckks.h 对齐，取自官方 tutorial.ipynb）
# ---------------------------------------------------------------------------


class _Ldouble(Structure):
    _fields_ = [("data", POINTER(c_double)), ("size", c_size_t)]


class _Luint64(Structure):
    _fields_ = [("data", POINTER(c_ulonglong)), ("size", c_size_t)]


class _ParametersLiteral(Structure):
    _fields_ = [
        ("qi", _Luint64), ("pi", _Luint64),
        ("logN", c_int), ("logSlots", c_int),
        ("scale", c_double), ("sigma", c_double),
    ]


class _Poly(Structure):
    _fields_ = [
        ("coeffs", POINTER(_Luint64)),
        ("IsNTT", c_bool), ("IsMForm", c_bool),
        ("size", c_size_t),
    ]


class _PolyQP(Structure):
    _fields_ = [("Q", POINTER(_Poly)), ("P", POINTER(_Poly))]


class _PolyQPPair(Structure):
    _fields_ = [("qp0", _PolyQP), ("qp1", _PolyQP)]


class _Ciphertext(Structure):
    _fields_ = [
        ("data", POINTER(_Poly)),
        ("size", c_size_t),
        ("idxs", POINTER(c_int)),
        ("scale", c_double),
    ]


class _Data(Structure):
    _fields_ = [("data", POINTER(_Ciphertext)), ("size", c_size_t)]


class _MPHEServer(Structure):
    _fields_ = [
        ("paramsLiteral", _ParametersLiteral),
        ("crs", _Poly),
        ("sk", _PolyQP),
        ("pk", _PolyQPPair),
        ("data", _Data),
        ("idx", c_int),
    ]


_so = None
if os.path.exists(DLL_PATH):
    _so = CDLL(DLL_PATH)

    _newMPHEServer = _so.newMPHEServer
    _newMPHEServer.restype = POINTER(_MPHEServer)

    _encryptFromPk = _so.encryptFromPk
    _encryptFromPk.argtypes = [POINTER(_PolyQPPair), POINTER(c_double), c_size_t, c_int]
    _encryptFromPk.restype = POINTER(_Ciphertext)

    _partialDecrypt = _so.partialDecrypt
    _partialDecrypt.argtypes = [POINTER(_PolyQP), POINTER(_Ciphertext), c_int]
    _partialDecrypt.restype = POINTER(_Ciphertext)

    _ringQAddLvl = _so.ringQAddLvl
    _ringQAddLvl.argtypes = [POINTER(_Ciphertext), c_int, POINTER(_Ciphertext), c_int]
    _ringQAddLvl.restype = POINTER(_Ciphertext)

    _decodeAfterPartialDecrypt = _so.decodeAfterPartialDecrypt
    _decodeAfterPartialDecrypt.argtypes = [POINTER(_Ciphertext)]
    _decodeAfterPartialDecrypt.restype = POINTER(_Ldouble)

    _addCTs = _so.addCTs
    _addCTs.argtypes = [POINTER(_Ciphertext), POINTER(_Ciphertext)]
    _addCTs.restype = POINTER(_Ciphertext)

    _multiplyCTConst = _so.multiplyCTConst
    _multiplyCTConst.argtypes = [POINTER(_Ciphertext), c_double]
    _multiplyCTConst.restype = POINTER(_Ciphertext)

    _addRingPs = _so.addRingPs
    _addRingPs.argtypes = [POINTER(_Poly), POINTER(_Poly)]
    _addRingPs.restype = POINTER(_Poly)


# ---------------------------------------------------------------------------
# pickle 友好的包装类（简化自官方 tutorial）
# ---------------------------------------------------------------------------


class ParametersLiteral:
    def __init__(self, p):
        self.logN, self.logSlots = p.logN, p.logSlots
        self.scale, self.sigma = p.scale, p.sigma
        self.qi = [p.qi.data[i] for i in range(p.qi.size)]
        self.pi = [p.pi.data[i] for i in range(p.pi.size)]


class Poly:
    def __init__(self, p):
        self.coeffs = [[p.coeffs[i].data[j] for j in range(p.coeffs[i].size)]
                       for i in range(p.size)]
        self.IsNTT, self.IsMForm = p.IsNTT, p.IsMForm

    def make_structure(self):
        arr = (_Luint64 * len(self.coeffs))()
        for i, c in enumerate(self.coeffs):
            arr[i].size = len(c)
            arr[i].data = (c_ulonglong * len(c))(*c)
        p = _Poly()
        p.size = len(self.coeffs)
        p.coeffs = arr
        p.IsNTT, p.IsMForm = self.IsNTT, self.IsMForm
        return p


class PolyQP:
    def __init__(self, qp):
        self.Q, self.P = Poly(qp.Q.contents), Poly(qp.P.contents)

    def make_structure(self):
        qp = _PolyQP()
        q, pp = self.Q.make_structure(), self.P.make_structure()
        qp.Q = POINTER(_Poly)(q)
        qp.P = POINTER(_Poly)(pp)
        return qp


class Ciphertext:
    def __init__(self, ct):
        self.data = [Poly(ct.data[i]) for i in range(ct.size)]
        self.idxs = [ct.idxs[i] for i in range(ct.size)]
        self.scale = ct.scale

    def make_structure(self):
        ct = _Ciphertext()
        polys = [p.make_structure() for p in self.data]
        ct.size = len(polys)
        ct.data = (_Poly * ct.size)(*polys)
        ct.idxs = (c_int * ct.size)(*self.idxs)
        ct.scale = self.scale
        return ct

    def approx_bytes(self):
        """粗略估计密文字节数（序列化大小）。"""
        n = 0
        for p in self.data:
            for c in p.coeffs:
                n += len(c) * 8
        return n


class Party:
    """MK-CKKS 参与方（服务器 id=0，客户端 id>=1）。"""

    def __init__(self, idx):
        ptr = _newMPHEServer(idx)
        s = ptr.contents
        self.idx = s.idx
        self.params = ParametersLiteral(s.paramsLiteral)
        self.sk = PolyQP(s.sk)
        self.pk = [PolyQP(s.pk.qp0), PolyQP(s.pk.qp1)]

    def encrypt(self, vec):
        pk = _PolyQPPair(self.pk[0].make_structure(), self.pk[1].make_structure())
        arr = (c_double * len(vec))(*vec)
        ct = _encryptFromPk(byref(pk), arr, len(vec), self.idx)
        return Ciphertext(ct.contents)

    def partial_decrypt(self, ct):
        sk = self.sk.make_structure()
        _ct = ct.make_structure()
        pd = _partialDecrypt(byref(sk), byref(_ct), self.idx)
        return Ciphertext(pd.contents)


def aggregate_ringP(server, ringP_list):
    """xMKCKKS 公钥聚合：sum P_i（修复 tutorial 中未赋值的 bug）。"""
    s = server.pk[0].P.make_structure()
    for rp in ringP_list:
        s = _addRingPs(s, rp.make_structure())
    return Poly(s.contents)


def add_cts(server, a, b):
    res = _addCTs(a.make_structure(), b.make_structure())
    return Ciphertext(res.contents)


def mul_ct_const(server, a, const):
    res = _multiplyCTConst(a.make_structure(), c_double(const))
    return Ciphertext(res.contents)


def aggregate_pds(server, pd_list, client_ids):
    agg = server_ring_add(pd_list[0], 0, pd_list[0], client_ids[0])
    for pd, cid in zip(pd_list[1:], client_ids[1:]):
        agg = server_ring_add(agg, 0, pd, cid)
    return agg


def server_ring_add(ct1, i1, ct2, i2):
    res = _ringQAddLvl(ct1.make_structure(), i1, ct2.make_structure(), i2)
    return Ciphertext(res.contents)


def decode(ct):
    res = _decodeAfterPartialDecrypt(ct.make_structure())
    return [res.contents.data[i] for i in range(res.contents.size)]


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------

N_CLIENTS = 3
VEC = 8192  # 2^13 slots，与库默认参数匹配


def _setup():
    server = Party(0)
    clients = [Party(i + 1) for i in range(N_CLIENTS)]
    # xMKCKKS：聚合所有客户端公钥的 P 环，构造联合公钥
    aggP = aggregate_ringP(server, [c.pk[0].P for c in clients])
    for c in clients:
        c.pk[0].P = copy.deepcopy(aggP)
    return server, clients


def test_library_available():
    assert _so is not None, f"xmkckks.dll 不存在: {DLL_PATH}（需先用 Go 构建共享库）"


def test_mkckks_full_pipeline():
    if _so is None:
        import pytest
        pytest.skip("xmkckks.dll 未构建")

    server, clients = _setup()
    rng = np.random.default_rng(42)
    grads = [rng.standard_normal(VEC) * 0.01 for _ in clients]  # 模拟梯度向量

    # 各客户端加密上传
    cts = [c.encrypt(g) for c, g in zip(clients, grads)]
    print(f"\n单个密文 ≈ {cts[0].approx_bytes() / 1e6:.1f} MB（{VEC} 槽）")

    # 服务器密态聚合（加法 + ×常数，模拟加权 FedAvg）
    agg = cts[0]
    for ct in cts[1:]:
        agg = add_cts(server, agg, ct)
    agg = mul_ct_const(server, agg, 1.0 / N_CLIENTS)

    # 全体客户端部分解密 → 服务器聚合份额 → 解码
    pds = [c.partial_decrypt(agg) for c in clients]
    pd_agg = aggregate_pds(server, pds, [c.idx for c in clients])
    dec = np.array(decode(pd_agg))
    expected = np.sum(grads, axis=0) / N_CLIENTS

    err = np.max(np.abs(dec - expected))
    print(f"解密最大误差: {err:.3e}")
    assert err < 1e-4, f"MK-CKKS 解密误差过大: {err}"


def test_partial_set_fails():
    """负例：只聚合部分客户端的解密份额（模拟 1 人掉线），应解密失败。
    证明 xMKCKKS 是 n-out-of-n 语义，不具备阈值/掉线容忍。"""
    if _so is None:
        import pytest
        pytest.skip("xmkckks.dll 未构建")

    server, clients = _setup()
    rng = np.random.default_rng(7)
    grads = [rng.standard_normal(VEC) * 0.01 for _ in clients]
    cts = [c.encrypt(g) for c, g in zip(clients, grads)]
    agg = cts[0]
    for ct in cts[1:]:
        agg = add_cts(server, agg, ct)

    # 只有 client_1 和 client_2 提供份额，client_3 掉线
    pds = [clients[0].partial_decrypt(agg), clients[1].partial_decrypt(agg)]
    pd_agg = aggregate_pds(server, pds, [clients[0].idx, clients[1].idx])
    dec = np.array(decode(pd_agg))
    expected = np.sum(grads, axis=0)

    rel = np.linalg.norm(dec - expected) / (np.linalg.norm(expected) + 1e-12)
    print(f"\n部分份额解密相对误差: {rel:.3f}（应 >> 阈值，证明无法解密）")
    assert rel > 0.1, "部分份额竟能解密成功？与 n-out-of-n 语义不符"


if __name__ == "__main__":
    t0 = time.time()
    test_library_available()
    print(f"[1/3] 库加载成功: {DLL_PATH}")
    test_mkckks_full_pipeline()
    print("[2/3] 三人 MK-CKKS 全流程（加密→同态聚合→部分解密→联合解码）通过")
    test_partial_set_fails()
    print("[3/3] 负例验证（n-out-of-n，无阈值容忍）通过")
    print(f"\n全部测试通过，耗时 {time.time() - t0:.1f}s")
