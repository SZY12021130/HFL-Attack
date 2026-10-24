"""
神经网络模型定义模块
支持MLP、CNN、LeNet、MobileNetV2、DenseNet等模型
"""

import torch
import torch.nn as nn
import torch.nn.functional as F
from typing import List


class MLP(nn.Module):
    """
    多层感知机 (MLP)
    适用于MNIST/Fashion-MNIST/EMNIST等灰度图数据集
    """
    
    def __init__(self, input_dim: int = 784,
                 hidden_dims: List[int] = [256, 128],
                 num_classes: int = 10,
                 dropout: float = 0.2):
        super(MLP, self).__init__()
        self.input_dim = input_dim
        self.hidden_dims = hidden_dims
        self.num_classes = num_classes
        
        layers = []
        prev_dim = input_dim
        for hidden_dim in hidden_dims:
            layers.append(nn.Linear(prev_dim, hidden_dim))
            layers.append(nn.ReLU())
            layers.append(nn.Dropout(dropout))
            prev_dim = hidden_dim
        
        self.feature_extractor = nn.Sequential(*layers)
        self.classifier = nn.Linear(prev_dim, num_classes)
    
    def forward(self, x):
        if x.dim() > 2:
            x = x.view(x.size(0), -1)
        features = self.feature_extractor(x)
        logits = self.classifier(features)
        return logits
    
    def get_model_size(self) -> int:
        return sum(p.numel() for p in self.parameters())


class CNN(nn.Module):
    """
    通用卷积神经网络 (3层CNN)
    适用于MNIST/Fashion-MNIST/CIFAR-10/SVHN
    """
    
    def __init__(self, input_channels: int = 1,
                 num_classes: int = 10,
                 dropout: float = 0.2):
        super(CNN, self).__init__()
        self.input_channels = input_channels
        self.num_classes = num_classes
        
        self.conv1 = nn.Conv2d(input_channels, 32, kernel_size=3, padding=1)
        self.conv2 = nn.Conv2d(32, 64, kernel_size=3, padding=1)
        self.conv3 = nn.Conv2d(64, 64, kernel_size=3, padding=1)
        self.pool = nn.MaxPool2d(2, 2)
        self.dropout = nn.Dropout(dropout)
        
        if input_channels == 1:  # 28x28 -> 14 -> 7 -> 3
            fc_input_dim = 64 * 3 * 3
        else:  # 32x32 -> 16 -> 8 -> 4
            fc_input_dim = 64 * 4 * 4
        
        self.fc1 = nn.Linear(fc_input_dim, 128)
        self.fc2 = nn.Linear(128, num_classes)
    
    def forward(self, x):
        x = self.pool(F.relu(self.conv1(x)))
        x = self.pool(F.relu(self.conv2(x)))
        x = self.pool(F.relu(self.conv3(x)))
        x = x.view(x.size(0), -1)
        x = F.relu(self.fc1(x))
        x = self.dropout(x)
        x = self.fc2(x)
        return x
    
    def get_model_size(self) -> int:
        return sum(p.numel() for p in self.parameters())


class DeepCNN(nn.Module):
    """
    更深的无BN卷积神经网络 (4层CNN)
    专为测试CKKS量化深度极限设计，所有层均无BatchNorm
    """
    
    def __init__(self, input_channels: int = 3,
                 num_classes: int = 10,
                 width: int = 32):
        super(DeepCNN, self).__init__()
        self.input_channels = input_channels
        self.num_classes = num_classes
        
        # 4层卷积，逐渐扩大通道但控制参数量
        self.conv1 = nn.Conv2d(input_channels, width, kernel_size=3, padding=1)
        self.conv2 = nn.Conv2d(width, width * 2, kernel_size=3, padding=1)
        self.conv3 = nn.Conv2d(width * 2, width * 2, kernel_size=3, padding=1)
        self.conv4 = nn.Conv2d(width * 2, width, kernel_size=3, padding=1)
        self.pool = nn.MaxPool2d(2, 2)
        
        if input_channels == 1:  # 28x28 -> 14 -> 7 -> 3 -> 1 (with pool after conv4)
            fc_input_dim = width * 1 * 1
        else:  # 32x32 -> 16 -> 8 -> 4 -> 2
            fc_input_dim = width * 2 * 2
        
        self.fc1 = nn.Linear(fc_input_dim, 128)
        self.fc2 = nn.Linear(128, num_classes)
    
    def forward(self, x):
        x = self.pool(F.relu(self.conv1(x)))  # /2
        x = self.pool(F.relu(self.conv2(x)))  # /2
        x = F.relu(self.conv3(x))
        x = self.pool(F.relu(self.conv4(x)))  # /2
        x = x.view(x.size(0), -1)
        x = F.relu(self.fc1(x))
        x = self.fc2(x)
        return x
    
    def get_model_size(self) -> int:
        return sum(p.numel() for p in self.parameters())


class VGGNoBN(nn.Module):
    """
    无BatchNorm的VGG风格网络 (VGG-11简化版)
    专为CKKS同态加密联邦学习设计——去除所有BN层避免运行时统计量同步问题
    
    结构: 64->128->256->256->512->512->512->512 + 3 FC
    CIFAR-10/100适配: 输入32x32, 输出num_classes
    """
    
    def __init__(self, input_channels: int = 3, num_classes: int = 10):
        super(VGGNoBN, self).__init__()
        
        # VGG-11风格卷积组 (无BN)
        self.features = nn.Sequential(
            # Block 1: 32x32
            nn.Conv2d(input_channels, 64, kernel_size=3, padding=1),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(kernel_size=2, stride=2),  # 16x16
            
            # Block 2
            nn.Conv2d(64, 128, kernel_size=3, padding=1),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(kernel_size=2, stride=2),  # 8x8
            
            # Block 3
            nn.Conv2d(128, 256, kernel_size=3, padding=1),
            nn.ReLU(inplace=True),
            nn.Conv2d(256, 256, kernel_size=3, padding=1),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(kernel_size=2, stride=2),  # 4x4
            
            # Block 4
            nn.Conv2d(256, 512, kernel_size=3, padding=1),
            nn.ReLU(inplace=True),
            nn.Conv2d(512, 512, kernel_size=3, padding=1),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(kernel_size=2, stride=2),  # 2x2
            
            # Block 5
            nn.Conv2d(512, 512, kernel_size=3, padding=1),
            nn.ReLU(inplace=True),
            nn.Conv2d(512, 512, kernel_size=3, padding=1),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(kernel_size=2, stride=2),  # 1x1
        )
        
        self.classifier = nn.Sequential(
            nn.Linear(512, 512),
            nn.ReLU(inplace=True),
            nn.Dropout(0.5),
            nn.Linear(512, 512),
            nn.ReLU(inplace=True),
            nn.Dropout(0.5),
            nn.Linear(512, num_classes),
        )
        
        # Kaiming初始化 (无BN时更重要)
        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                nn.init.kaiming_normal_(m.weight, mode='fan_out', nonlinearity='relu')
                if m.bias is not None:
                    nn.init.constant_(m.bias, 0)
            elif isinstance(m, nn.Linear):
                nn.init.normal_(m.weight, 0, 0.01)
                nn.init.constant_(m.bias, 0)
    
    def forward(self, x):
        x = self.features(x)
        x = x.view(x.size(0), -1)
        x = self.classifier(x)
        return x
    
    def get_model_size(self) -> int:
        return sum(p.numel() for p in self.parameters())


class LeNet(nn.Module):
    """
    LeNet-5 变体
    经典结构，特别适合MNIST/Fashion-MNIST
    参数量小、收敛快、联邦场景通信友好
    """
    
    def __init__(self, input_channels: int = 1, num_classes: int = 10):
        super(LeNet, self).__init__()
        self.feature_extractor = nn.Sequential(
            nn.Conv2d(input_channels, 6, kernel_size=5),
            nn.ReLU(),
            nn.MaxPool2d(2, 2),
            nn.Conv2d(6, 16, kernel_size=5),
            nn.ReLU(),
            nn.MaxPool2d(2, 2)
        )
        # MNIST 28x28: 28->24->12->8->4 => 16*4*4=256
        # CIFAR 32x32: 32->28->14->10->5 => 16*5*5=400
        fc_input = 16 * 4 * 4 if input_channels == 1 else 16 * 5 * 5
        self.classifier = nn.Sequential(
            nn.Flatten(),
            nn.Linear(fc_input, 120),
            nn.ReLU(),
            nn.Linear(120, 84),
            nn.ReLU(),
            nn.Linear(84, num_classes)
        )
    
    def forward(self, x):
        features = self.feature_extractor(x)
        logits = self.classifier(features)
        return logits
    
    def get_model_size(self) -> int:
        return sum(p.numel() for p in self.parameters())


class _InvertedResidual(nn.Module):
    """MobileNetV2 倒置残差块"""
    
    def __init__(self, in_ch: int, out_ch: int, stride: int, expand_ratio: int):
        super().__init__()
        hidden_dim = in_ch * expand_ratio
        self.use_residual = stride == 1 and in_ch == out_ch
        
        layers = []
        if expand_ratio != 1:
            layers.append(nn.Conv2d(in_ch, hidden_dim, 1, bias=False))
            layers.append(nn.BatchNorm2d(hidden_dim))
            layers.append(nn.ReLU6(inplace=True))
        layers.extend([
            nn.Conv2d(hidden_dim, hidden_dim, 3, stride, 1, groups=hidden_dim, bias=False),
            nn.BatchNorm2d(hidden_dim),
            nn.ReLU6(inplace=True),
            nn.Conv2d(hidden_dim, out_ch, 1, bias=False),
            nn.BatchNorm2d(out_ch),
        ])
        self.conv = nn.Sequential(*layers)
    
    def forward(self, x):
        if self.use_residual:
            return x + self.conv(x)
        return self.conv(x)


class MobileNetV2(nn.Module):
    """
    轻量版 MobileNetV2
    适合CIFAR-10/100/SVHN等彩色图像数据集
    在移动/边缘设备上联邦学习表现优异
    """
    
    def __init__(self, input_channels: int = 3, num_classes: int = 10, width_mult: float = 1.0):
        super(MobileNetV2, self).__init__()
        # CIFAR-10 配置 (比ImageNet更浅的初始层)
        config = [
            # t, c, n, s
            [1, 16, 1, 1],
            [6, 24, 2, 1],  # CIFAR 不需要大下采样
            [6, 32, 3, 2],
            [6, 64, 4, 2],
            [6, 96, 3, 1],
            [6, 160, 3, 2],
            [6, 320, 1, 1],
        ]
        
        in_ch = int(32 * width_mult)
        self.first_conv = nn.Sequential(
            nn.Conv2d(input_channels, in_ch, 3, 1, 1, bias=False),  # stride=1 for 32x32
            nn.BatchNorm2d(in_ch),
            nn.ReLU6(inplace=True)
        )
        
        layers = []
        for t, c, n, s in config:
            out_ch = int(c * width_mult)
            for i in range(n):
                stride = s if i == 0 else 1
                layers.append(_InvertedResidual(in_ch, out_ch, stride, t))
                in_ch = out_ch
        
        self.features = nn.Sequential(*layers)
        self.last_conv = nn.Sequential(
            nn.Conv2d(in_ch, int(1280 * width_mult), 1, bias=False),
            nn.BatchNorm2d(int(1280 * width_mult)),
            nn.ReLU6(inplace=True)
        )
        
        self.classifier = nn.Sequential(
            nn.Dropout(0.2),
            nn.Linear(int(1280 * width_mult), num_classes)
        )
    
    def forward(self, x):
        x = self.first_conv(x)
        x = self.features(x)
        x = self.last_conv(x)
        x = F.adaptive_avg_pool2d(x, 1)
        x = x.view(x.size(0), -1)
        x = self.classifier(x)
        return x
    
    def get_model_size(self) -> int:
        return sum(p.numel() for p in self.parameters())


class _DenseLayer(nn.Module):
    """DenseNet 密集连接层"""
    
    def __init__(self, in_ch: int, growth_rate: int, bn_size: int, drop_rate: float):
        super().__init__()
        self.norm1 = nn.BatchNorm2d(in_ch)
        self.relu1 = nn.ReLU(inplace=True)
        self.conv1 = nn.Conv2d(in_ch, bn_size * growth_rate, 1, bias=False)
        self.norm2 = nn.BatchNorm2d(bn_size * growth_rate)
        self.relu2 = nn.ReLU(inplace=True)
        self.conv2 = nn.Conv2d(bn_size * growth_rate, growth_rate, 3, padding=1, bias=False)
        self.drop_rate = drop_rate
    
    def forward(self, x):
        if isinstance(x, torch.Tensor):
            features = [x]
        else:
            features = x
        out = torch.cat(features, 1)
        out = self.conv1(self.relu1(self.norm1(out)))
        out = self.conv2(self.relu2(self.norm2(out)))
        if self.drop_rate > 0:
            out = F.dropout(out, p=self.drop_rate, training=self.training)
        return out


class _DenseBlock(nn.Module):
    def __init__(self, num_layers: int, in_ch: int, growth_rate: int, bn_size: int, drop_rate: float):
        super().__init__()
        layers = []
        for i in range(num_layers):
            layer = _DenseLayer(in_ch + i * growth_rate, growth_rate, bn_size, drop_rate)
            layers.append(layer)
        self.block = nn.ModuleList(layers)
    
    def forward(self, x):
        features = [x]
        for layer in self.block:
            out = layer(features)
            features.append(out)
        return torch.cat(features, 1)


class _Transition(nn.Module):
    def __init__(self, in_ch: int, out_ch: int):
        super().__init__()
        self.norm = nn.BatchNorm2d(in_ch)
        self.relu = nn.ReLU(inplace=True)
        self.conv = nn.Conv2d(in_ch, out_ch, 1, bias=False)
        self.pool = nn.AvgPool2d(2, 2)
    
    def forward(self, x):
        x = self.conv(self.relu(self.norm(x)))
        x = self.pool(x)
        return x


class DenseNet(nn.Module):
    """
    轻量版 DenseNet (类似 DenseNet-40)
    适合CIFAR-10/100/SVHN
    特征重用能力强，但参数量较大，联邦通信成本高
    """
    
    def __init__(self, input_channels: int = 3, num_classes: int = 10,
                 growth_rate: int = 12, block_config: List[int] = (6, 6, 6),
                 num_init_features: int = 24, bn_size: int = 4, drop_rate: float = 0.0):
        super(DenseNet, self).__init__()
        
        layers = []
        layers.extend([
            nn.Conv2d(input_channels, num_init_features, kernel_size=3, stride=1, padding=1, bias=False),
            nn.BatchNorm2d(num_init_features),
            nn.ReLU(inplace=True),
        ])
        
        num_features = num_init_features
        for i, num_layers_count in enumerate(block_config):
            layers.append(_DenseBlock(num_layers_count, num_features, growth_rate, bn_size, drop_rate))
            num_features += num_layers_count * growth_rate
            if i != len(block_config) - 1:
                layers.append(_Transition(num_features, num_features // 2))
                num_features //= 2
        
        self.features = nn.Sequential(*layers)
        self.norm_final = nn.BatchNorm2d(num_features)
        self.classifier = nn.Linear(num_features, num_classes)
    
    def forward(self, x):
        features = self.features(x)
        out = F.relu(self.norm_final(features), inplace=True)
        out = F.adaptive_avg_pool2d(out, 1)
        out = out.view(out.size(0), -1)
        out = self.classifier(out)
        return out
    
    def get_model_size(self) -> int:
        return sum(p.numel() for p in self.parameters())


class _BasicBlock(nn.Module):
    """ResNet-18 BasicBlock：两层3x3卷积 + shortcut"""
    expansion = 1
    
    def __init__(self, in_ch: int, out_ch: int, stride: int = 1):
        super().__init__()
        self.conv1 = nn.Conv2d(in_ch, out_ch, 3, stride, 1, bias=False)
        self.bn1 = nn.BatchNorm2d(out_ch)
        self.conv2 = nn.Conv2d(out_ch, out_ch, 3, 1, 1, bias=False)
        self.bn2 = nn.BatchNorm2d(out_ch)
        
        self.shortcut = nn.Sequential()
        if stride != 1 or in_ch != out_ch:
            self.shortcut = nn.Sequential(
                nn.Conv2d(in_ch, out_ch, 1, stride, bias=False),
                nn.BatchNorm2d(out_ch)
            )
    
    def forward(self, x):
        out = F.relu(self.bn1(self.conv1(x)))
        out = self.bn2(self.conv2(out))
        out += self.shortcut(x)
        out = F.relu(out)
        return out


class ResNet18(nn.Module):
    """
    ResNet-18 (CIFAR适配版)
    适合CIFAR-10/100/SVHN等32x32彩色图像
    与MobileNetV2精度相当，但结构更简单、特征更稳定
    """
    
    def __init__(self, input_channels: int = 3, num_classes: int = 10):
        super(ResNet18, self).__init__()
        # CIFAR适配：初始卷积用3x3 stride=1（ImageNet版用7x7 stride=2）
        self.conv1 = nn.Conv2d(input_channels, 64, kernel_size=3, stride=1, padding=1, bias=False)
        self.bn1 = nn.BatchNorm2d(64)
        
        # 4个stage，每个2个BasicBlock
        self.layer1 = self._make_layer(64, 64, 2, stride=1)
        self.layer2 = self._make_layer(64, 128, 2, stride=2)
        self.layer3 = self._make_layer(128, 256, 2, stride=2)
        self.layer4 = self._make_layer(256, 512, 2, stride=2)
        
        self.avgpool = nn.AdaptiveAvgPool2d((1, 1))
        self.fc = nn.Linear(512, num_classes)
    
    def _make_layer(self, in_ch: int, out_ch: int, num_blocks: int, stride: int):
        layers = []
        layers.append(_BasicBlock(in_ch, out_ch, stride))
        for _ in range(1, num_blocks):
            layers.append(_BasicBlock(out_ch, out_ch, 1))
        return nn.Sequential(*layers)
    
    def forward(self, x):
        x = F.relu(self.bn1(self.conv1(x)))
        x = self.layer1(x)
        x = self.layer2(x)
        x = self.layer3(x)
        x = self.layer4(x)
        x = self.avgpool(x)
        x = x.view(x.size(0), -1)
        x = self.fc(x)
        return x
    
    def get_model_size(self) -> int:
        return sum(p.numel() for p in self.parameters())


# ═══════════════════════════════════════════════════════════════════════
# FedPHE 对齐模型（用于公平对比实验）
# ═══════════════════════════════════════════════════════════════════════

class CNNCifar(nn.Module):
    """
    FedPHE 原版 CNN for CIFAR (LeNet 风格轻量 CNN)
    2层卷积 + 3层全连接，无 BatchNorm/Dropout
    参数量 ~60K (CIFAR-10)，与 FedPHE 完全一致
    """
    
    def __init__(self, input_channels: int = 3, num_classes: int = 10):
        super(CNNCifar, self).__init__()
        self.conv1 = nn.Conv2d(input_channels, 6, kernel_size=5)
        self.pool = nn.MaxPool2d(2, 2)
        self.conv2 = nn.Conv2d(6, 16, kernel_size=5)
        # CIFAR 32x32: 32->28->14->10->5 => 16*5*5=400
        self.fc1 = nn.Linear(16 * 5 * 5, 120)
        self.fc2 = nn.Linear(120, 84)
        self.fc3 = nn.Linear(84, num_classes)
    
    def forward(self, x):
        x = self.pool(F.relu(self.conv1(x)))
        x = self.pool(F.relu(self.conv2(x)))
        x = x.view(-1, 16 * 5 * 5)
        x = F.relu(self.fc1(x))
        x = F.relu(self.fc2(x))
        x = self.fc3(x)
        return x
    
    def get_model_size(self) -> int:
        return sum(p.numel() for p in self.parameters())


class CNNFashion(nn.Module):
    """
    FedPHE 原版 CNN for Fashion-MNIST
    3层卷积(含BN) + 1层全连接，专为 28x28 设计
    参数量 ~39K，与 FedPHE 完全一致
    """
    
    def __init__(self, input_channels: int = 1, num_classes: int = 10):
        super(CNNFashion, self).__init__()
        self.layer1 = nn.Sequential(
            nn.Conv2d(input_channels, 16, kernel_size=5, padding=2),
            nn.BatchNorm2d(16),
            nn.ReLU()
        )
        self.pool1 = nn.MaxPool2d(2)
        self.layer2 = nn.Sequential(
            nn.Conv2d(16, 32, kernel_size=3),
            nn.BatchNorm2d(32),
            nn.ReLU()
        )
        self.layer3 = nn.Sequential(
            nn.Conv2d(32, 64, kernel_size=3),
            nn.BatchNorm2d(64),
            nn.ReLU()
        )
        self.pool2 = nn.MaxPool2d(2)
        # 28x28: pad2->28->pool1->14->conv3->12->conv3->10->pool2->5 => 64*5*5
        self.fc = nn.Linear(64 * 5 * 5, num_classes)
    
    def forward(self, x):
        out = self.pool1(self.layer1(x))
        out = self.pool2(self.layer3(self.layer2(out)))
        out = out.view(out.size(0), -1)
        out = self.fc(out)
        return out
    
    def get_model_size(self) -> int:
        return sum(p.numel() for p in self.parameters())


class _FedPHEResBlock(nn.Module):
    """FedPHE ResNet BasicBlock (CIFAR 轻量版)"""
    expansion = 1
    
    def __init__(self, in_planes: int, planes: int, stride: int = 1, option: str = "B"):
        super().__init__()
        self.conv1 = nn.Conv2d(in_planes, planes, kernel_size=3, stride=stride, padding=1, bias=False)
        self.bn1 = nn.BatchNorm2d(planes)
        self.conv2 = nn.Conv2d(planes, planes, kernel_size=3, stride=1, padding=1, bias=False)
        self.bn2 = nn.BatchNorm2d(planes)
        
        self.shortcut = nn.Sequential()
        if stride != 1 or in_planes != planes:
            if option == "A":
                self.shortcut = nn.Sequential(
                    nn.Conv2d(in_planes, self.expansion * planes, kernel_size=1, stride=stride, bias=False),
                    nn.BatchNorm2d(self.expansion * planes)
                )
            elif option == "B":
                self.shortcut = nn.Sequential(
                    nn.Conv2d(in_planes, self.expansion * planes, kernel_size=1, stride=stride, bias=False),
                    nn.BatchNorm2d(self.expansion * planes)
                )
    
    def forward(self, x):
        out = F.relu(self.bn1(self.conv1(x)))
        out = self.bn2(self.conv2(out))
        out += self.shortcut(x)
        out = F.relu(out)
        return out


class ResNetCifar(nn.Module):
    """
    FedPHE 原版 ResNet for CIFAR (ResNet20/32)
    初始通道16，3个stage(16->32->64)，参数量 272K/470K
    与标准ResNet-18不同，专为CIFAR小图像优化
    """
    
    def __init__(self, input_channels: int = 3, num_classes: int = 10,
                 num_blocks: List[int] = [3, 3, 3]):
        super(ResNetCifar, self).__init__()
        self.in_planes = 16
        
        self.conv1 = nn.Conv2d(input_channels, 16, kernel_size=3, stride=1, padding=1, bias=False)
        self.bn1 = nn.BatchNorm2d(16)
        self.layer1 = self._make_layer(16, num_blocks[0], stride=1)
        self.layer2 = self._make_layer(32, num_blocks[1], stride=2)
        self.layer3 = self._make_layer(64, num_blocks[2], stride=2)
        self.linear = nn.Linear(64, num_classes)
        
        # Kaiming 初始化 (FedPHE 原版)
        for m in self.modules():
            if isinstance(m, (nn.Linear, nn.Conv2d)):
                nn.init.kaiming_normal_(m.weight)
    
    def _make_layer(self, planes: int, num_blocks: int, stride: int):
        strides = [stride] + [1] * (num_blocks - 1)
        layers = []
        for s in strides:
            layers.append(_FedPHEResBlock(self.in_planes, planes, s))
            self.in_planes = planes * _FedPHEResBlock.expansion
        return nn.Sequential(*layers)
    
    def forward(self, x):
        out = self.conv1(x)
        out = self.bn1(out)
        out = F.relu(out)
        out = self.layer1(out)
        out = self.layer2(out)
        out = self.layer3(out)
        out = F.avg_pool2d(out, out.size()[3])
        out = out.view(out.size(0), -1)
        out = self.linear(out)
        return out
    
    def get_model_size(self) -> int:
        return sum(p.numel() for p in self.parameters())


# ═══════════════════════════════════════════════════════════════════════
# 外部项目对齐模型（用于跨项目公平对比实验）
# ═══════════════════════════════════════════════════════════════════════

class Mnist2NN(nn.Module):
    """
    2层全连接网络 (Mnist_2NN)
    来源: FedDMC-main / fedavgpy-master
    经典MNIST基线模型，结构极简：input_dim->200->num_classes
    参数量约159K (input_dim=784)，适合作为非CNN架构的对比基线
    """
    
    def __init__(self, input_dim: int = 784, num_classes: int = 10):
        super(Mnist2NN, self).__init__()
        self.fc1 = nn.Linear(input_dim, 200)
        self.fc2 = nn.Linear(200, num_classes)
    
    def forward(self, x):
        x = x.view(x.size(0), -1)
        x = F.relu(self.fc1(x))
        x = self.fc2(x)
        return x
    
    def get_model_size(self) -> int:
        return sum(p.numel() for p in self.parameters())


class AlexNetCifar(nn.Module):
    """
    CIFAR-10 适配版 AlexNet
    来源: FedSHE (models/CNNModel.py)
    原始ImageNet版AlexNet的CIFAR适配：缩小卷积核和FC层以适应32x32输入
    结构: 3->64->192->384->256->256 (conv) + 4096->4096->10 (fc)
    参数量约2.3M，适合作为中等深度CNN的对比基线
    """
    
    def __init__(self, num_classes: int = 10):
        super(AlexNetCifar, self).__init__()
        self.features = nn.Sequential(
            nn.Conv2d(3, 64, kernel_size=3, stride=2, padding=1),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(kernel_size=2, stride=2),
            nn.Conv2d(64, 192, kernel_size=3, padding=1),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(kernel_size=2, stride=2),
            nn.Conv2d(192, 384, kernel_size=3, padding=1),
            nn.ReLU(inplace=True),
            nn.Conv2d(384, 256, kernel_size=3, padding=1),
            nn.ReLU(inplace=True),
            nn.Conv2d(256, 256, kernel_size=3, padding=1),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(kernel_size=2, stride=2),
        )
        self.classifier = nn.Sequential(
            nn.Dropout(0.5),
            nn.Linear(256 * 2 * 2, 4096),
            nn.ReLU(inplace=True),
            nn.Dropout(0.5),
            nn.Linear(4096, 4096),
            nn.ReLU(inplace=True),
            nn.Linear(4096, num_classes),
        )
    
    def forward(self, x):
        x = self.features(x)
        x = x.view(x.size(0), -1)
        x = self.classifier(x)
        return x
    
    def get_model_size(self) -> int:
        return sum(p.numel() for p in self.parameters())


class AlexNetMnist(nn.Module):
    """
    MNIST 适配版 AlexNet
    来源: FedSHE (models/CNNModel.py)
    专为28x28灰度图设计的AlexNet变体，缩小了通道数和FC层维度
    结构: 1->32->64->96->64->32 (conv) + 2048->1024->10 (fc)
    参数量约1.2M，适合MNIST上的深度CNN对比
    """
    
    def __init__(self, num_classes: int = 10):
        super(AlexNetMnist, self).__init__()
        self.features = nn.Sequential(
            nn.Conv2d(1, 32, kernel_size=5, stride=1, padding=1),
            nn.ReLU(inplace=True),
            nn.Conv2d(32, 64, kernel_size=3, padding=1),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(kernel_size=2, stride=2),
            nn.Conv2d(64, 96, kernel_size=3, padding=1),
            nn.ReLU(inplace=True),
            nn.Conv2d(96, 64, kernel_size=3, padding=1),
            nn.ReLU(inplace=True),
            nn.Conv2d(64, 32, kernel_size=3, padding=1),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(kernel_size=2, stride=1),
        )
        self.classifier = nn.Sequential(
            nn.Dropout(0.5),
            nn.Linear(32 * 12 * 12, 2048),
            nn.ReLU(inplace=True),
            nn.Dropout(0.5),
            nn.Linear(2048, 1024),
            nn.ReLU(inplace=True),
            nn.Linear(1024, num_classes),
        )
    
    def forward(self, x):
        x = self.features(x)
        x = x.view(x.size(0), -1)
        x = self.classifier(x)
        return x
    
    def get_model_size(self) -> int:
        return sum(p.numel() for p in self.parameters())


class CharLSTM(nn.Module):
    """
    字符级LSTM（用于文本/序列联邦学习）
    来源: Federated-Learning-master (models/Nets.py)
    原始用于Shakespeare数据集（80个字符），支持通用vocab_size和num_classes
    结构: Embedding -> 2-layer LSTM(256) -> Dropout -> Linear
    参数量约275K (vocab=80, classes=80)，是框架中唯一的序列模型
    可用于NLP联邦学习场景（如Shakespeare、sent140等）
    """
    
    def __init__(self, vocab_size: int = 80, embed_dim: int = 8,
                 hidden_dim: int = 256, num_layers: int = 2,
                 num_classes: int = 80, dropout: float = 0.5):
        super(CharLSTM, self).__init__()
        self.embed = nn.Embedding(vocab_size, embed_dim)
        self.lstm = nn.LSTM(embed_dim, hidden_dim, num_layers,
                            batch_first=True, dropout=dropout if num_layers > 1 else 0)
        self.dropout = nn.Dropout(dropout)
        self.fc = nn.Linear(hidden_dim, num_classes)
    
    def forward(self, x):
        # x: (batch, seq_len)
        x = self.embed(x)           # (batch, seq_len, embed_dim)
        x, _ = self.lstm(x)         # (batch, seq_len, hidden_dim)
        x = self.dropout(x)
        # 取最后一个时间步
        x = x[:, -1, :]             # (batch, hidden_dim)
        x = self.fc(x)              # (batch, num_classes)
        return x
    
    def get_model_size(self) -> int:
        return sum(p.numel() for p in self.parameters())


class CryptoNet(nn.Module):
    """
    CryptoNets 风格轻量CNN（专为同态加密推理设计）
    来源: ckks_cryptonet (modules/model.py)，原实现为TensorFlow/Keras
    核心特点：使用平方激活函数 (x^2) 替代ReLU，减少HE中的非线性开销
    结构: Conv2D(5, 3x3, s2) -> Flatten -> Dense(100, square) -> Dense(10, square) -> Dense(num_classes)
    原始输入为15x15x1（MNIST下采样），此处适配标准28x28和32x32
    参数量约85K，是非常轻量的HE友好模型
    """
    
    def __init__(self, input_channels: int = 1, num_classes: int = 10):
        super(CryptoNet, self).__init__()
        self.conv1 = nn.Conv2d(input_channels, 5, kernel_size=3, stride=2, padding=1)
        # 计算FC输入维度
        # 28x28 -> pool/stride2 -> 14x14; 32x32 -> 16x16
        # 由于padding=1, stride=2: H_out = ceil(H_in / 2)
        # 28 -> 14, 32 -> 16
        self.fc1 = nn.Linear(5 * 14 * 14 if input_channels == 1 else 5 * 16 * 16, 100)
        self.fc2 = nn.Linear(100, 10)
        self.fc3 = nn.Linear(10, num_classes)
    
    def _square(self, x):
        """平方激活函数，HE友好"""
        return x * x
    
    def forward(self, x):
        x = self.conv1(x)
        x = self._square(x)  # 第一层也用平方激活（对齐原TF实现）
        x = x.view(x.size(0), -1)
        x = self._square(self.fc1(x))
        x = self._square(self.fc2(x))
        x = self.fc3(x)
        return x
    
    def get_model_size(self) -> int:
        return sum(p.numel() for p in self.parameters())


# ============================================================================
# FedFetch 对齐模型 — ShuffleNet
# 来源: FedFetch-main/fedscale/utils/models/cv_models/shufflenet.py
# 原始论文: 'ShuffleNet: An Extremely Efficient Convolutional Neural Network
#           for Mobile Devices,' https://arxiv.org/abs/1707.01083
# ============================================================================

def channel_shuffle(x, groups):
    """Channel shuffle operation."""
    batch, channels, height, width = x.size()
    if channels % groups != 0:
        raise ValueError("channels must be divisible by groups")
    channels_per_group = channels // groups
    x = x.view(batch, groups, channels_per_group, height, width)
    x = torch.transpose(x, 1, 2).contiguous()
    x = x.view(batch, channels, height, width)
    return x


class ChannelShuffle(nn.Module):
    def __init__(self, channels, groups):
        super(ChannelShuffle, self).__init__()
        if channels % groups != 0:
            raise ValueError("channels must be divisible by groups")
        self.groups = groups

    def forward(self, x):
        return channel_shuffle(x, self.groups)


def conv1x1(in_channels, out_channels, stride=1, groups=1, bias=False):
    return nn.Conv2d(
        in_channels=in_channels,
        out_channels=out_channels,
        kernel_size=1,
        stride=stride,
        groups=groups,
        bias=bias)


def conv3x3(in_channels, out_channels, stride=1, padding=1, dilation=1, groups=1, bias=False):
    return nn.Conv2d(
        in_channels=in_channels,
        out_channels=out_channels,
        kernel_size=3,
        stride=stride,
        padding=padding,
        dilation=dilation,
        groups=groups,
        bias=bias)


def depthwise_conv3x3(channels, stride=1, padding=1, dilation=1, bias=False):
    return nn.Conv2d(
        in_channels=channels,
        out_channels=channels,
        kernel_size=3,
        stride=stride,
        padding=padding,
        dilation=dilation,
        groups=channels,
        bias=bias)


class ShuffleUnit(nn.Module):
    """ShuffleNet unit."""
    def __init__(self, in_channels, out_channels, groups, downsample, ignore_group):
        super(ShuffleUnit, self).__init__()
        self.downsample = downsample
        mid_channels = out_channels // 4
        if downsample:
            out_channels -= in_channels

        self.compress_conv1 = conv1x1(
            in_channels=in_channels,
            out_channels=mid_channels,
            groups=(1 if ignore_group else groups))
        self.compress_bn1 = nn.BatchNorm2d(num_features=mid_channels)
        self.c_shuffle = ChannelShuffle(channels=mid_channels, groups=groups)
        self.dw_conv2 = depthwise_conv3x3(
            channels=mid_channels,
            stride=(2 if self.downsample else 1))
        self.dw_bn2 = nn.BatchNorm2d(num_features=mid_channels)
        self.expand_conv3 = conv1x1(
            in_channels=mid_channels,
            out_channels=out_channels,
            groups=groups)
        self.expand_bn3 = nn.BatchNorm2d(num_features=out_channels)
        if downsample:
            self.avgpool = nn.AvgPool2d(kernel_size=3, stride=2, padding=1)
        self.activ = nn.ReLU(inplace=True)

    def forward(self, x):
        identity = x
        x = self.compress_conv1(x)
        x = self.compress_bn1(x)
        x = self.activ(x)
        x = self.c_shuffle(x)
        x = self.dw_conv2(x)
        x = self.dw_bn2(x)
        x = self.expand_conv3(x)
        x = self.expand_bn3(x)
        if self.downsample:
            identity = self.avgpool(identity)
            x = torch.cat((x, identity), dim=1)
        else:
            x = x + identity
        x = self.activ(x)
        return x


class ShuffleInitBlock(nn.Module):
    """ShuffleNet specific initial block."""
    def __init__(self, in_channels, out_channels):
        super(ShuffleInitBlock, self).__init__()
        self.conv = conv3x3(
            in_channels=in_channels,
            out_channels=out_channels,
            stride=2)
        self.bn = nn.BatchNorm2d(num_features=out_channels)
        self.activ = nn.ReLU(inplace=True)
        self.pool = nn.MaxPool2d(kernel_size=3, stride=2, padding=1)

    def forward(self, x):
        x = self.conv(x)
        x = self.bn(x)
        x = self.activ(x)
        x = self.pool(x)
        return x


class ShuffleNet(nn.Module):
    """
    ShuffleNet model.
    适配说明: 将原始固定 kernel_size=7 的 AvgPool2d 替换为 AdaptiveAvgPool2d((1,1))
            以支持 MNIST(28x28)、CIFAR(32x32) 等不同输入尺寸。
    """
    def __init__(self, channels, init_block_channels, groups, in_channels=3, num_classes=1000):
        super(ShuffleNet, self).__init__()
        self.num_classes = num_classes

        self.features = nn.Sequential()
        self.features.add_module("init_block", ShuffleInitBlock(
            in_channels=in_channels,
            out_channels=init_block_channels))
        in_channels = init_block_channels
        for i, channels_per_stage in enumerate(channels):
            stage = nn.Sequential()
            for j, out_channels in enumerate(channels_per_stage):
                downsample = (j == 0)
                ignore_group = (i == 0) and (j == 0)
                stage.add_module("unit{}".format(j + 1), ShuffleUnit(
                    in_channels=in_channels,
                    out_channels=out_channels,
                    groups=groups,
                    downsample=downsample,
                    ignore_group=ignore_group))
                in_channels = out_channels
            self.features.add_module("stage{}".format(i + 1), stage)
        self.features.add_module("final_pool", nn.AdaptiveAvgPool2d((1, 1)))

        self.output = nn.Linear(in_features=in_channels, out_features=num_classes)
        self._init_params()

    def _init_params(self):
        for name, module in self.named_modules():
            if isinstance(module, nn.Conv2d):
                nn.init.kaiming_uniform_(module.weight)
                if module.bias is not None:
                    nn.init.constant_(module.bias, 0)

    def forward(self, x):
        x = self.features(x)
        x = x.view(x.size(0), -1)
        x = self.output(x)
        return x

    def get_model_size(self) -> int:
        return sum(p.numel() for p in self.parameters())


def get_shufflenet(groups, width_scale, in_channels=3, num_classes=1000, **kwargs):
    """Create ShuffleNet model with specific parameters."""
    init_block_channels = 24
    layers = [4, 8, 4]

    if groups == 1:
        channels_per_layers = [144, 288, 576]
    elif groups == 2:
        channels_per_layers = [200, 400, 800]
    elif groups == 3:
        channels_per_layers = [240, 480, 960]
    elif groups == 4:
        channels_per_layers = [272, 544, 1088]
    elif groups == 8:
        channels_per_layers = [384, 768, 1536]
    else:
        raise ValueError("The {} of groups is not supported".format(groups))

    channels = [[ci] * li for (ci, li) in zip(channels_per_layers, layers)]

    if width_scale != 1.0:
        channels = [[int(cij * width_scale) for cij in ci] for ci in channels]
        init_block_channels = int(init_block_channels * width_scale)

    net = ShuffleNet(
        channels=channels,
        init_block_channels=init_block_channels,
        groups=groups,
        in_channels=in_channels,
        num_classes=num_classes,
        **kwargs)
    return net


def print_model_summary(model: nn.Module, input_size: tuple = (1, 1, 28, 28)):
    """
    打印模型结构和参数统计（类Keras表格形式）
    
    Args:
        model: PyTorch模型
        input_size: 输入张量形状 (batch, channels, height, width)
    """
    from collections import OrderedDict
    
    summary = OrderedDict()
    hooks = []
    
    def register_hook(module):
        def hook(module, input, output):
            class_name = str(module.__class__).split(".")[-1].split("'")[0]
            module_idx = len(summary)
            m_key = f"{class_name}-{module_idx + 1}"
            
            summary[m_key] = OrderedDict()
            
            # 记录输出形状，batch维度统一用-1表示
            if isinstance(output, (list, tuple)):
                summary[m_key]["output_shape"] = [
                    [-1] + list(o.size())[1:] for o in output
                ]
            else:
                summary[m_key]["output_shape"] = list(output.size())
                summary[m_key]["output_shape"][0] = -1
            
            # 该层所有参数数量（含weight/bias/running_mean/running_var等）
            params = sum(p.numel() for p in module.parameters())
            summary[m_key]["nb_params"] = params
        
        # 仅对叶子模块（无子模块）注册hook，避免容器模块重复统计
        if len(list(module.children())) == 0:
            hooks.append(module.register_forward_hook(hook))
    
    device = next(model.parameters()).device
    x = torch.rand(input_size).to(device)
    
    # 切换到 eval 模式，避免 BatchNorm 在 batch_size=1 时因无法计算统计量而报错
    was_training = model.training
    model.eval()
    with torch.no_grad():
        model.apply(register_hook)
        model(x)
    
    for h in hooks:
        h.remove()
    
    # 恢复原始训练状态
    if was_training:
        model.train()
    
    total_params = sum(p.numel() for p in model.parameters())
    trainable_params = sum(p.numel() for p in model.parameters() if p.requires_grad)
    
    print("\n" + "=" * 75)
    print(f"Model: {model.__class__.__name__}")
    print("=" * 75)
    
    header = f"{'Layer (type)':<30} {'Output Shape':<25} {'Param #':>15}"
    print(header)
    print("=" * 75)
    
    for key in summary:
        output_shape = str(summary[key]["output_shape"]).replace(" ", "")
        params = summary[key]["nb_params"]
        line = f"{key:<30} {output_shape:<25} {params:>15,}"
        print(line)
    
    print("=" * 75)
    print(f"Total params:         {total_params:,}")
    print(f"Trainable params:     {trainable_params:,}")
    print(f"Non-trainable params: {max(0, total_params - trainable_params):,}")
    print("-" * 75)
    print(f"Model size (float32): {total_params * 4 / 1024 / 1024:.2f} MB")
    print("=" * 75 + "\n")


def create_model(model_name: str, config: dict) -> nn.Module:
    """
    根据配置创建模型
    
    Args:
        model_name: 模型名称 (mlp, cnn, lenet, mobilenetv2, densenet)
        config: 配置字典
    
    Returns:
        模型实例
    """
    dataset = config['data']['dataset']
    num_classes = config['data']['num_classes']
    
    # 根据数据集推断输入通道数
    if dataset in ["mnist", "fashion_mnist", "emnist"]:
        input_channels = 1
    elif dataset in ["cifar10", "cifar100", "svhn"]:
        input_channels = 3
    else:
        input_channels = 1
    
    if model_name == "mlp":
        if dataset in ["mnist", "fashion_mnist", "emnist"]:
            input_dim = 784  # 28*28
        elif dataset in ["cifar10", "cifar100", "svhn"]:
            input_dim = 3072  # 32*32*3
        else:
            input_dim = 784
        
        return MLP(
            input_dim=input_dim,
            hidden_dims=config['model'].get('hidden_dims', [256, 128]),
            num_classes=num_classes,
            dropout=config['model'].get('dropout', 0.2)
        )
    
    elif model_name == "cnn":
        return CNN(
            input_channels=input_channels,
            num_classes=num_classes,
            dropout=config['model'].get('dropout', 0.2)
        )
    
    elif model_name == "lenet":
        return LeNet(
            input_channels=input_channels,
            num_classes=num_classes
        )
    
    elif model_name in ["mobilenet", "mobilenetv2"]:
        return MobileNetV2(
            input_channels=input_channels,
            num_classes=num_classes,
            width_mult=config['model'].get('width_mult', 1.0)
        )
    
    elif model_name in ["densenet", "densenet40"]:
        return DenseNet(
            input_channels=input_channels,
            num_classes=num_classes,
            growth_rate=config['model'].get('growth_rate', 12),
            block_config=config['model'].get('block_config', [6, 6, 6]),
            num_init_features=config['model'].get('num_init_features', 24),
            bn_size=config['model'].get('bn_size', 4),
            drop_rate=config['model'].get('drop_rate', 0.0)
        )
    
    elif model_name in ["resnet", "resnet18"]:
        return ResNet18(
            input_channels=input_channels,
            num_classes=num_classes
        )
    
    elif model_name == "deepcnn":
        return DeepCNN(
            input_channels=input_channels,
            num_classes=num_classes,
            width=config['model'].get('deepcnn_width', 32)
        )
    
    elif model_name == "vggnobn":
        return VGGNoBN(
            input_channels=input_channels,
            num_classes=num_classes
        )
    
    # --- FedPHE 对齐模型 ---
    elif model_name == "cnn_cifar":
        return CNNCifar(
            input_channels=input_channels,
            num_classes=num_classes
        )
    
    elif model_name in ["cnn_fmnist", "cnn_fashion"]:
        return CNNFashion(
            input_channels=input_channels,
            num_classes=num_classes
        )
    
    elif model_name == "resnet20":
        return ResNetCifar(
            input_channels=input_channels,
            num_classes=num_classes,
            num_blocks=[3, 3, 3]
        )
    
    elif model_name == "resnet32":
        return ResNetCifar(
            input_channels=input_channels,
            num_classes=num_classes,
            num_blocks=[5, 5, 5]
        )
    
    # --- 外部项目对齐模型 ---
    elif model_name == "mnist2nn":
        if dataset in ["cifar10", "cifar100", "svhn"]:
            input_dim = 3072  # 32*32*3
        else:
            input_dim = 784   # 28*28
        return Mnist2NN(input_dim=input_dim, num_classes=num_classes)
    
    elif model_name == "alexnet_cifar":
        return AlexNetCifar(num_classes=num_classes)
    
    elif model_name == "alexnet_mnist":
        return AlexNetMnist(num_classes=num_classes)
    
    elif model_name == "cryptonet":
        return CryptoNet(
            input_channels=input_channels,
            num_classes=num_classes
        )
    
    elif model_name == "lstm":
        return CharLSTM(
            vocab_size=config['model'].get('vocab_size', 80),
            embed_dim=config['model'].get('embed_dim', 8),
            hidden_dim=config['model'].get('hidden_dim', 256),
            num_layers=config['model'].get('num_layers', 2),
            num_classes=num_classes,
            dropout=config['model'].get('lstm_dropout', 0.5)
        )
    
    elif model_name == "shufflenet":
        groups = config['model'].get('shufflenet_groups', 3)
        width_scale = config['model'].get('shufflenet_width', 1.0)
        return get_shufflenet(
            groups=groups,
            width_scale=width_scale,
            in_channels=input_channels,
            num_classes=num_classes
        )
    
    else:
        raise ValueError(f"不支持的模型: {model_name}")


if __name__ == "__main__":
    # 快速测试各模型
    test_cases = [
        ("mlp", 1, 28, 10),
        ("cnn", 1, 28, 10),
        ("lenet", 1, 28, 10),
        ("mobilenetv2", 3, 32, 10),
        ("densenet", 3, 32, 10),
        ("resnet18", 3, 32, 10),
        # FedPHE 对齐模型
        ("cnn_cifar", 3, 32, 10),
        ("cnn_fmnist", 1, 28, 10),
        ("resnet20", 3, 32, 10),
        ("resnet32", 3, 32, 10),
        # 外部项目对齐模型
        ("mnist2nn", 1, 28, 10),
        ("alexnet_cifar", 3, 32, 10),
        ("alexnet_mnist", 1, 28, 10),
        ("cryptonet", 1, 28, 10),
        ("lstm", 1, 80, 80),  # 序列模型单独处理：seq_len=80
        ("shufflenet", 3, 32, 10),
    ]
    for name, channels, size, classes in test_cases:
        dummy_cfg = {
            'data': {'dataset': 'mnist' if channels == 1 else 'cifar10', 'num_classes': classes},
            'model': {'dropout': 0.2, 'hidden_dims': [256, 128], 'width_mult': 1.0}
        }
        model = create_model(name, dummy_cfg)
        if name == "lstm":
            x = torch.randint(0, classes, (2, size))  # (batch, seq_len)
        else:
            x = torch.randn(2, channels, size, size)
        y = model(x)
        print(f"{name:15s}: params={model.get_model_size():>8,}, input={x.shape}, output={y.shape}")
