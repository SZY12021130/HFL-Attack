"""
ThresholdDecryptor：门限解密仪式编排 + 通信开销统计
====================================================

把 threshold/ 的密码学组件接入 CKKS-Fed 训练循环（doc/threshold_ckks.md
§4 TTP / §5 DKG / §6 组合器三场景）：

- 初始化（实验编排方充当的可信时刻，对应 TTP 的 Dealer 角色）：
  - ttp 模式：把含私钥的 TenSEAL 上下文序列化字节以 Shamir (t,n) 分块
    分片（threshold/sharing.py）分发给 n 个客户端（仿真中为 list，索引 =
    client_id），随后销毁本地完整字节引用（置 None）——"Setup 后 Dealer
    销毁"语义（doc §4.1 第 4 步）；
  - dkg 模式：`run_dkg(num_clients, t, seed=42)` 缓存 DKG 结果。
    ⚠️ 理想模型假设：Pedersen DKG 生成的集体密钥与实验 CKKS 密钥并非
    同一把（真实化需 OpenFHE MultipartyKeyGen / Lattigo mpCKKS，见
    doc §9.5），故保留构造时传入的一次性"仪式上下文"仅用于理想解密，
    统计里标 ``ideal: True``。

- 每轮 decrypt_global_params（§6 消息流的协议层仿真）：
  - ttp + combiner=server（场景 A）：模拟 t 个被选客户端把份额明文发
    服务器、服务器 reconstruct 出一次性完整上下文并解密；
  - ttp + combiner=client（场景 B）：组合器客户端本地 reconstruct
    （仿真与场景 A 同走 recover_secret；⚠️ docstring 注明：真实协议
    是部分解密而非重构 sk——见 doc §6 场景 B 与 §4.3"工程上禁止"，
    本仿真为通信开销/功能评估服务）；
  - dkg（份额与 DKG 数学真实，解密理想）：走仪式上下文解密，ideal=True。
"""

import time
from typing import Dict, List, Optional, Tuple

import torch

from ckks_fed.crypto import CKKSContext, CKKSManager
from ckks_fed.log_utils import LogTag, tag_info
from threshold import ThresholdConfig
from threshold.sharing import split_secret, recover_secret
from threshold.dkg import run_dkg, DKGResult


class ThresholdDecryptor:
    """门限解密编排器：份额生命周期 + 每轮仪式解密 + 通信开销统计"""

    def __init__(self, cfg: ThresholdConfig, num_clients: int,
                 full_context_bytes: bytes,
                 poly_modulus_degree: int = 8192):
        """
        Args:
            cfg:                 ThresholdConfig（mode/combiner/t/smudging）
            num_clients:         客户端总数 n（份额按 client_id 索引分发）
            full_context_bytes:  含私钥的 TenSEAL 上下文字节（实验进程内
                                 本就持有——相当于"初始化可信时刻"）
            poly_modulus_degree: 反序列化上下文时需要的外部参数（TenSEAL
                                 不保留该值）

        Setup 后 Dealer 销毁语义：ttp 模式分片完成后，构造函数的
        full_context_bytes 参数引用被置 None（del 语义），本对象只保留
        分片后的份额 list。
        """
        if not (num_clients >= cfg.t >= 2):
            raise ValueError(
                f"非法门限参数：n={num_clients} 个客户端，t={cfg.t}（需 n≥t≥2）")
        self.cfg = cfg
        self.num_clients = num_clients
        self.poly_modulus_degree = poly_modulus_degree

        # 份额分发（模拟）：索引 = client_id
        self.shares: Optional[List[bytes]] = None
        self.dist_bytes_per_client: int = 0
        self._ceremony_context: Optional[bytes] = None
        self._dkg_result: Optional[DKGResult] = None

        if cfg.mode == "ttp":
            self.shares = split_secret(full_context_bytes, num_clients, cfg.t)
            self.dist_bytes_per_client = len(self.shares[0])
            # 分发后销毁完整字节引用（模拟"Setup 后 Dealer 销毁"；注：仿真
            # 单进程内客户端上下文此前已各自持有一份，这是既有统一密钥架构
            # 的简化，真实部署中客户端只会收到自己的份额）
            full_context_bytes = None
            tag_info(LogTag.INIT,
                     f"Threshold(TTP): sk 已 (t={cfg.t},n={num_clients}) 分片"
                     f"并分发（{self.dist_bytes_per_client} B/客户端），"
                     f"完整副本引用已销毁")
        elif cfg.mode == "dkg":
            self._dkg_result = run_dkg(num_clients, cfg.t, seed=42)
            # 理想模型：保留一次性完整上下文用于理想解密（见模块 docstring）
            self._ceremony_context = full_context_bytes
            # 份额+承诺字节估计：(n−1)×(66B 份额 + 66B 盲化) + t×66B 承诺
            self.dist_bytes_per_client = ((num_clients - 1) * (66 + 66)
                                          + cfg.t * 66)
            tag_info(LogTag.INIT,
                     f"Threshold(DKG): Pedersen DKG 完成 (t={cfg.t},"
                     f"n={num_clients})，理想解密模型（ideal=True）")
        else:  # 防御性分支（ThresholdConfig.from_config 已校验）
            raise ValueError(f"未知 threshold.mode={cfg.mode!r}"
                             f"（可选 'ttp' / 'dkg'）")

    # ------------------------------------------------------------------
    def _decrypt_with_context(self, encrypted_global_params: dict,
                              context_bytes: bytes) -> Dict[str, torch.Tensor]:
        """用一份（重构/理想）完整上下文走 __sparse__ 解密路径

        复用 CKKSManager.decrypt_model_update：shapes/offsets/mask 自带于
        密文字典（FedPHE 对齐格式），与 ckks_fed/client.py
        `_decrypt_global_params` 同一实现路径，返回值可直接喂给
        client.local_train(global_params=...)。
        """
        context = CKKSContext.from_full_bytes(
            context_bytes, poly_modulus_degree=self.poly_modulus_degree)
        manager = CKKSManager(
            {'poly_modulus_degree': self.poly_modulus_degree,
             'slot_packing': True}, context=context)
        return manager.decrypt_model_update(encrypted_global_params, None)

    @staticmethod
    def _c1_broadcast_bytes(encrypted_global_params: dict) -> int:
        """估计服务器广播 c1 的字节数：CKKS 密文 (c0,c1) 两分量等长，
        取密文字典中全部密文序列化字节的一半。"""
        cts = encrypted_global_params.get('ciphertexts') or []
        total = 0
        for c in cts:
            total += len(c) if isinstance(c, (bytes, bytearray)) \
                else len(c.serialize())
        return total // 2

    # ------------------------------------------------------------------
    def decrypt_global_params(
            self, encrypted_global_params: dict,
            participant_ids: List[int]
    ) -> Tuple[Dict[str, torch.Tensor], Dict]:
        """
        门限解密全局模型（每轮调用一次，结果可复用给所有被选客户端）。

        Args:
            encrypted_global_params: 服务器 get_encrypted_global_params()
            participant_ids:         本轮参与门限解密的客户端（前 t 个生效）

        Returns:
            (明文 params state_dict, 统计 dict)

        ⚠️ 安全语义（doc §6）：ttp 模式的 combiner=server（场景 A）与
        combiner=client（场景 B）在本仿真中都走 recover_secret 重构一次性
        上下文——真实协议里客户端应只做部分解密（threshold/toy_ckks.py
        的 partial_decrypt/combine 给出了真·部分解密数学），把份额凑齐
        恢复完整 sk 是 doc §4.3 明令禁止的退化用法；此处为协议层功能与
        通信开销仿真。dkg 模式标 ideal=True（理想解密模型）。
        """
        t0 = time.time()
        n_part = min(self.cfg.t, len(participant_ids))
        ids = list(participant_ids[:n_part])
        c1_bytes = self._c1_broadcast_bytes(encrypted_global_params)

        if self.cfg.mode == "ttp":
            # 场景 A：t 个客户端 → 服务器重构；场景 B：组合器客户端本地
            # 重构（协议层等价，见 docstring 安全语义说明）
            blobs = [self.shares[cid] for cid in ids]
            context_bytes = recover_secret(blobs)
            params = self._decrypt_with_context(encrypted_global_params,
                                                context_bytes)
            context_bytes = None  # 一次性上下文字节立即释放
            ideal = False
            # 仪式字节：t 份份额经服务器转发 + c1 广播
            ceremony_bytes = n_part * self.dist_bytes_per_client + c1_bytes
        else:  # dkg：份额与 DKG 数学真实，解密走理想路径
            params = self._decrypt_with_context(encrypted_global_params,
                                                self._ceremony_context)
            ideal = True
            # 仪式字节估计：t 份部分解密多项式（稀疏份额非零系数+索引，
            # int64）+ t 份承诺 + c1 广播。非零系数按 N/2 估计（稀疏私钥）
            nonzero = 4096 // 2
            ceremony_bytes = (n_part * (nonzero + 1) * 8
                              + n_part * 66 + c1_bytes)

        stats = {
            'mode': self.cfg.mode,
            'combiner': self.cfg.combiner,
            't': self.cfg.t,
            'num_participants': n_part,
            'participant_ids': ids,
            'dist_bytes_per_client': self.dist_bytes_per_client,
            'ceremony_bytes_per_round': ceremony_bytes,
            'ceremony_time_sec': time.time() - t0,
            'ideal': ideal,
            'smudging': bool(self.cfg.smudging),
        }
        return params, stats

    # ------------------------------------------------------------------
    def partial_decrypt_flow(self, encrypted_global_params: dict,
                             participant_ids: List[int],
                             combiner_public_key: Optional[bytes] = None
                             ) -> Dict[str, torch.Tensor]:
        """
        真实门限后端的纯接口桩（当前抛 NotImplementedError）。

        未来接入 OpenFHE（MultipartyKeyGen / MultipartyDecryptLead/
        Active）或 Lattigo（mpCKKS）时，在此实现 doc §6 场景 B 的完整
        消息流：服务器广播 c1 → 各参与方 partial_decrypt + smudging
        （公钥加密到组合器）→ 组合器 Lagrange 组合出明文。
        部分解密的数学语义见 threshold/toy_ckks.py 的
        partial_decrypt / combine（教科书 CKKS 真·门限演示）。
        """
        raise NotImplementedError(
            "真实门限后端（OpenFHE/Lattigo）尚未接入；当前 dkg 模式为理想"
            "解密模型。数学演示见 threshold/toy_ckks.py，协议流见 "
            "doc/threshold_ckks.md §6")
