"""
模型切分与 split learning 结构工具（论文 §V-B 式 6-12）
=========================================================

RFL 把全局模型逻辑切分为：
- 浅层 p̲（式 6 的 SL）：部署在资源受限的 EC 上，输出中间表示（smashed
  data），维度恒定 → EC 计算/通信开销与全局模型维度无关（Theorem 4）；
- 深层 p̄（式 10 的 DL）：部署在 ES 上，对加噪中间表示 p̂_u 继续前向。

本模块负责"结构"：从 ckks_fed/models.py 的全局模型切出浅/深子模块
（深拷贝，参数独立），并维护 <扁平向量, 全局参数名> 的双向映射，
供协议层做签名、哈希与聚合。支持的切分点：

- MLP：前 Ls 个隐藏层为浅层（默认 Ls=1，论文默认），其余隐藏层+分类
  头为深层；
- CNN：卷积栈（conv1-3+pool）为浅层，全连接栈（fc1-fc2）为深层
  （此时 num_shallow_layers 必须为 1）。
"""

import copy
from typing import Dict, List, Tuple

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

from base.models import CNN, MLP


class ShallowMLP(nn.Module):
    """MLP 浅层：输入展平 + 前 Ls 个 (Linear, ReLU, Dropout) 块。"""

    def __init__(self, layers: List[nn.Module]):
        super().__init__()
        self.net = nn.Sequential(*layers)

    def forward(self, x):
        if x.dim() > 2:
            x = x.view(x.size(0), -1)
        return self.net(x)


class DeepMLP(nn.Module):
    """MLP 深层：剩余隐藏层 + 分类头（输入为浅层输出的中间表示）。"""

    def __init__(self, layers: List[nn.Module], classifier: nn.Linear):
        super().__init__()
        self.net = nn.Sequential(*layers)
        self.classifier = classifier

    def forward(self, h):
        return self.classifier(self.net(h))


class ShallowCNN(nn.Module):
    """CNN 浅层：conv1-3 + 池化（输出展平前的特征图）。"""

    def __init__(self, conv1, conv2, conv3, pool):
        super().__init__()
        self.conv1, self.conv2, self.conv3, self.pool = conv1, conv2, conv3, pool

    def forward(self, x):
        x = self.pool(F.relu(self.conv1(x)))
        x = self.pool(F.relu(self.conv2(x)))
        x = self.pool(F.relu(self.conv3(x)))
        return x


class DeepCNN(nn.Module):
    """CNN 深层：展平 + fc1 + ReLU + Dropout + fc2。"""

    def __init__(self, fc1, fc2, dropout):
        super().__init__()
        self.fc1, self.fc2, self.dropout = fc1, fc2, dropout

    def forward(self, x):
        x = x.view(x.size(0), -1)
        x = F.relu(self.fc1(x))
        x = self.dropout(x)
        return self.fc2(x)


def build_split(model: nn.Module, num_shallow_layers: int
                ) -> Tuple[nn.Module, nn.Module, List[str], List[str]]:
    """
    把全局模型切分为 (shallow, deep)，并返回两侧参数对应的全局参数名列表
    （顺序与 ``flatten_module`` 的输出一致）。

    Returns:
        shallow: 部署到 EC 的浅层模块（深拷贝，参数独立）
        deep:    部署到 ES 的深层模块（深拷贝，参数独立）
        shallow_names / deep_names: 全局模型 named_parameters 的参数名
    """
    if isinstance(model, MLP):
        Ls = int(num_shallow_layers)
        n_hidden = len(model.hidden_dims)
        if not (1 <= Ls <= n_hidden):
            raise ValueError(
                f"MLP 切分点 Ls={Ls} 超出范围 [1, {n_hidden}]"
                f"（hidden_dims={model.hidden_dims}）")
        children = list(model.feature_extractor.children())
        per_block = 3  # (Linear, ReLU, Dropout)
        cut = per_block * Ls
        shallow = ShallowMLP([copy.deepcopy(m) for m in children[:cut]])
        deep = DeepMLP([copy.deepcopy(m) for m in children[cut:]],
                       copy.deepcopy(model.classifier))
        shallow_names = [f"feature_extractor.{j}.{s}"
                         for j in range(cut)
                         for s, _ in children[j].named_parameters()]
        deep_names = ([f"feature_extractor.{cut + j}.{s}"
                       for j, m in enumerate(children[cut:])
                       for s, _ in m.named_parameters()]
                      + [f"classifier.{s}"
                         for s, _ in model.classifier.named_parameters()])
        return shallow, deep, shallow_names, deep_names

    if isinstance(model, CNN):
        if int(num_shallow_layers) != 1:
            raise ValueError(
                "CNN 仅支持 conv/fc 边界切分（num_shallow_layers 必须为 1）")
        shallow = ShallowCNN(copy.deepcopy(model.conv1),
                             copy.deepcopy(model.conv2),
                             copy.deepcopy(model.conv3),
                             copy.deepcopy(model.pool))
        deep = DeepCNN(copy.deepcopy(model.fc1),
                       copy.deepcopy(model.fc2),
                       copy.deepcopy(model.dropout))
        shallow_names = [f"{c}.{s}" for c in ("conv1", "conv2", "conv3")
                         for s, _ in getattr(model, c).named_parameters()]
        deep_names = ([f"fc1.{s}" for s, _ in model.fc1.named_parameters()]
                      + [f"fc2.{s}" for s, _ in model.fc2.named_parameters()])
        return shallow, deep, shallow_names, deep_names

    raise ValueError(
        f"RFL 模型切分暂不支持 {type(model).__name__}"
        f"（支持：MLP（mlp）、CNN（cnn））")


# =====================================================================
# 扁平向量 <-> 命名参数 映射
# =====================================================================

def flatten_module(module: nn.Module, grad: bool = False) -> np.ndarray:
    """模块参数（或梯度）拼成一个 float64 向量，顺序 = named_parameters。"""
    parts = []
    for _, p in module.named_parameters():
        src = p.grad if grad else p
        if src is None:
            src = torch.zeros_like(p)
        parts.append(src.detach().cpu().double().numpy().ravel())
    return np.concatenate(parts) if parts else np.zeros(0, dtype=np.float64)


def flatten_named(named: Dict[str, torch.Tensor]) -> np.ndarray:
    return np.concatenate([v.detach().cpu().double().numpy().ravel()
                           for k, v in sorted(named.items())])


def unflatten_named(vec: np.ndarray,
                    template: Dict[str, torch.Tensor]
                    ) -> Dict[str, torch.Tensor]:
    """把扁平向量按 template 的形状/键名还原为命名参数字典。"""
    out: Dict[str, torch.Tensor] = {}
    offset = 0
    for k in sorted(template):
        n = template[k].numel()
        out[k] = torch.from_numpy(
            np.asarray(vec[offset:offset + n], dtype=np.float32)
            .reshape(template[k].shape)).clone()
        offset += n
    return out


# =====================================================================
# 轻量差分隐私（式 7 及灵敏度推导）
# =====================================================================

def dp_sigma(dim: int, dataset_size: int, epsilon: float,
             delta: float, clip: float) -> float:
    """
    式(7) 高斯机制噪声尺度：σ_u = c·d·S2/ε，
    其中 c = √(2ln(1.25/δ))，灵敏度 S2 = 2C/|D_u|。
    """
    c = float(np.sqrt(2.0 * np.log(1.25 / delta)))
    s2 = 2.0 * clip / max(1, int(dataset_size))
    return c * dim * s2 / epsilon
