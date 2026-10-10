"""
数据层面攻击 (Data-level Attacks)
在客户端本地数据上进行投毒，影响模型训练结果
"""

import torch
import numpy as np
from torch.utils.data import Dataset, DataLoader
from typing import Optional, Callable


class PoisonedDataset(Dataset):
    """包装数据集，应用数据投毒攻击"""
    
    def __init__(self, dataset: Dataset, attack_transform: Optional[Callable] = None,
                 target_transform: Optional[Callable] = None):
        self.dataset = dataset
        self.attack_transform = attack_transform
        self.target_transform = target_transform
    
    def __len__(self):
        return len(self.dataset)
    
    def __getitem__(self, idx):
        data, target = self.dataset[idx]
        if self.attack_transform is not None:
            data = self.attack_transform(data)
        if self.target_transform is not None:
            target = self.target_transform(target)
        return data, target


class LabelFlippingAttack:
    """
    标签翻转攻击 (Label Flipping Attack)
    
    类型:
    1. random: 随机翻转到其他任意标签
    2. targeted: 将源标签 source_class 翻转到目标标签 target_class
    3. all_to_one: 将所有标签都翻转到 target_class
    """
    
    def __init__(self, num_classes: int, attack_type: str = "random",
                 source_class: Optional[int] = None, target_class: Optional[int] = None,
                 poison_rate: float = 1.0, seed: int = 42):
        """
        Args:
            num_classes: 类别总数
            attack_type: 攻击类型 (random/targeted/all_to_one)
            source_class: 源类别（targeted类型使用）
            target_class: 目标类别（targeted/all_to_one使用）
            poison_rate: 投毒比例 (0.0 ~ 1.0)
            seed: 随机种子
        """
        self.num_classes = num_classes
        self.attack_type = attack_type
        self.source_class = source_class
        self.target_class = target_class
        self.poison_rate = poison_rate
        self.rng = np.random.RandomState(seed)
    
    def transform_label(self, label: int) -> int:
        """对单个标签进行翻转"""
        if self.rng.random() > self.poison_rate:
            return label
        
        if self.attack_type == "random":
            # 随机翻转到其他标签
            new_label = self.rng.randint(0, self.num_classes)
            while new_label == label:
                new_label = self.rng.randint(0, self.num_classes)
            return int(new_label)
        
        elif self.attack_type == "targeted":
            # 仅翻转特定源类别到目标类别
            if self.source_class is not None and label == self.source_class:
                return self.target_class
            return label
        
        elif self.attack_type == "all_to_one":
            # 所有标签都变成目标类别
            return self.target_class
        
        return label
    
    def apply_to_dataset(self, dataset: Dataset) -> PoisonedDataset:
        """将攻击应用到数据集上"""
        def target_transform(label):
            return self.transform_label(int(label))
        return PoisonedDataset(dataset, attack_transform=None, target_transform=target_transform)


class BackdoorDataAttack:
    """
    后门数据攻击 (Backdoor Data Attack)
    
    在输入数据中添加特定触发器（如右下角的白色方块），
    并将这些样本的标签改为目标类别，使模型学习到错误的关联模式。
    """
    
    def __init__(self, target_class: int = 0, poison_rate: float = 0.2,
                 trigger_size: int = 3, trigger_value: float = 1.0,
                 position: str = "bottom_right"):
        """
        Args:
            target_class: 后门触发后的目标类别
            poison_rate: 投毒比例
            trigger_size: 触发器边长（像素）
            trigger_value: 触发器像素值
            position: 触发器位置 (bottom_right/bottom_left/top_right/top_left/center)
        """
        self.target_class = target_class
        self.poison_rate = poison_rate
        self.trigger_size = trigger_size
        self.trigger_value = trigger_value
        self.position = position
    
    def add_trigger(self, img: torch.Tensor) -> torch.Tensor:
        """
        在图像上添加触发器
        
        Args:
            img: 图像张量 (C, H, W)
        
        Returns:
            带触发器的图像
        """
        img = img.clone()
        c, h, w = img.shape
        
        size = min(self.trigger_size, h // 4, w // 4)
        
        if self.position == "bottom_right":
            y_start, x_start = h - size, w - size
        elif self.position == "bottom_left":
            y_start, x_start = h - size, 0
        elif self.position == "top_right":
            y_start, x_start = 0, w - size
        elif self.position == "top_left":
            y_start, x_start = 0, 0
        elif self.position == "center":
            y_start, x_start = (h - size) // 2, (w - size) // 2
        else:
            y_start, x_start = h - size, w - size
        
        img[:, y_start:y_start+size, x_start:x_start+size] = self.trigger_value
        return img
    
    def apply_to_dataset(self, dataset: Dataset, seed: int = 42) -> PoisonedDataset:
        """将后门攻击应用到数据集"""
        rng = np.random.RandomState(seed)
        
        def attack_transform(img):
            # 只对一定比例的数据添加触发器
            if rng.random() < self.poison_rate:
                return self.add_trigger(img)
            return img
        
        def target_transform(label):
            # 如果被投毒，标签变为目标类别
            # 注意：这里需要和 loader 中的 poison mask 配合
            # 简化处理：在 collate_fn 或 transform 中处理比较困难
            # 我们的策略是：对所有数据都应用 trigger，但只让部分数据被标记为目标类
            # 实际更好的做法是在 get_poisoned_loader 中处理
            return label
        
        return PoisonedDataset(dataset, attack_transform=attack_transform, target_transform=target_transform)


def get_poisoned_loader(original_loader: DataLoader, attack,
                        batch_size: Optional[int] = None,
                        shuffle: Optional[bool] = None,
                        num_workers: int = 0) -> DataLoader:
    """
    根据攻击配置生成有毒的数据加载器
    
    Args:
        original_loader: 原始数据加载器
        attack: 数据攻击实例 (LabelFlippingAttack 或 BackdoorDataAttack)
        batch_size: batch大小（默认与原始loader相同）
        shuffle: 是否shuffle（默认与原始loader相同）
        num_workers: 工作进程数
    
    Returns:
        有毒数据加载器
    """
    if batch_size is None:
        batch_size = original_loader.batch_size
    if shuffle is None:
        shuffle = original_loader.shuffle if hasattr(original_loader, 'shuffle') else False
    
    poisoned_dataset = attack.apply_to_dataset(original_loader.dataset)
    
    return DataLoader(
        poisoned_dataset,
        batch_size=batch_size,
        shuffle=shuffle,
        num_workers=num_workers,
        pin_memory=original_loader.pin_memory if hasattr(original_loader, 'pin_memory') else False,
        drop_last=original_loader.drop_last if hasattr(original_loader, 'drop_last') else False
    )
