"""
训练工具函数模块
包含数据加载、训练、评估等工具函数
"""

import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader, Dataset, Subset
import torchvision
import torchvision.transforms as transforms
import numpy as np
from typing import Tuple, List, Dict, Optional
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def get_data_transforms(dataset_name: str):
    if dataset_name in ["mnist", "fashion_mnist", "emnist"]:
        transform = transforms.Compose([
            transforms.ToTensor(),
            transforms.Normalize((0.5,), (0.5,))
        ])
        return transform, transform
    elif dataset_name in ["cifar10", "cifar100"]:
        train_transform = transforms.Compose([
            transforms.RandomCrop(32, padding=4),
            transforms.RandomHorizontalFlip(),
            transforms.ToTensor(),
            transforms.Normalize((0.4914, 0.4822, 0.4465), (0.2023, 0.1994, 0.2010))
        ])
        test_transform = transforms.Compose([
            transforms.ToTensor(),
            transforms.Normalize((0.4914, 0.4822, 0.4465), (0.2023, 0.1994, 0.2010))
        ])
        return train_transform, test_transform
    elif dataset_name == "svhn":
        train_transform = transforms.Compose([
            transforms.RandomCrop(32, padding=4),
            transforms.ToTensor(),
            transforms.Normalize((0.4377, 0.4438, 0.4728), (0.1980, 0.2010, 0.1970))
        ])
        test_transform = transforms.Compose([
            transforms.ToTensor(),
            transforms.Normalize((0.4377, 0.4438, 0.4728), (0.1980, 0.2010, 0.1970))
        ])
        return train_transform, test_transform
    else:
        raise ValueError(f"不支持的数据集: {dataset_name}")


def get_dataset_num_classes(dataset_name: str) -> int:
    mapping = {
        "mnist": 10, "fashion_mnist": 10, "cifar10": 10,
        "cifar100": 100, "svhn": 10, "emnist": 47,
        "femnist": 62,  # LEAF FEMNIST: 10 digits + 26 upper + 26 lower
    }
    return mapping.get(dataset_name, 10)


def load_dataset(dataset_name: str, data_dir: str = "./data"):
    train_transform, test_transform = get_data_transforms(dataset_name)
    
    if dataset_name == "mnist":
        train_dataset = torchvision.datasets.MNIST(root=data_dir, train=True, download=True, transform=train_transform)
        test_dataset = torchvision.datasets.MNIST(root=data_dir, train=False, download=True, transform=test_transform)
    elif dataset_name == "fashion_mnist":
        train_dataset = torchvision.datasets.FashionMNIST(root=data_dir, train=True, download=True, transform=train_transform)
        test_dataset = torchvision.datasets.FashionMNIST(root=data_dir, train=False, download=True, transform=test_transform)
    elif dataset_name == "cifar10":
        train_dataset = torchvision.datasets.CIFAR10(root=data_dir, train=True, download=True, transform=train_transform)
        test_dataset = torchvision.datasets.CIFAR10(root=data_dir, train=False, download=True, transform=test_transform)
    elif dataset_name == "cifar100":
        train_dataset = torchvision.datasets.CIFAR100(root=data_dir, train=True, download=True, transform=train_transform)
        test_dataset = torchvision.datasets.CIFAR100(root=data_dir, train=False, download=True, transform=test_transform)
    elif dataset_name == "svhn":
        train_dataset = torchvision.datasets.SVHN(root=data_dir, split='train', download=True, transform=train_transform)
        test_dataset = torchvision.datasets.SVHN(root=data_dir, split='test', download=True, transform=test_transform)
    elif dataset_name == "emnist":
        train_dataset = torchvision.datasets.EMNIST(root=data_dir, split='balanced', train=True, download=True, transform=train_transform)
        test_dataset = torchvision.datasets.EMNIST(root=data_dir, split='balanced', train=False, download=True, transform=test_transform)
    elif dataset_name == "femnist":
        # FEMNIST (LEAF benchmark): 使用 EMNIST byclass 作为数据源
        # 62 classes: 10 digits + 26 uppercase + 26 lowercase
        train_dataset = torchvision.datasets.EMNIST(root=data_dir, split='byclass', train=True, download=True, transform=train_transform)
        test_dataset = torchvision.datasets.EMNIST(root=data_dir, split='byclass', train=False, download=True, transform=test_transform)
    else:
        raise ValueError(f"不支持的数据集: {dataset_name}")
    
    from ckks_fed.log_utils import tag_info, LogTag
    tag_info(LogTag.INIT, f"Dataset loaded: {dataset_name}  |  train={len(train_dataset)}  |  test={len(test_dataset)}")
    return train_dataset, test_dataset


def log_client_distributions(client_datasets: List[Subset], num_classes: int):
    lines = []
    lines.append("=" * 70)
    lines.append("Client Data Distribution")
    lines.append("=" * 70)
    overall_counts = np.zeros(num_classes, dtype=int)
    for cid, ds in enumerate(client_datasets):
        if hasattr(ds, 'dataset') and hasattr(ds, 'indices'):
            underlying = ds.dataset
            indices = ds.indices
            if hasattr(underlying, 'targets'):
                labels = np.array(underlying.targets)[indices]
            elif hasattr(underlying, 'labels'):
                labels = np.array(underlying.labels)[indices]
            else:
                labels = np.array([underlying[i][1] for i in indices])
        else:
            labels = np.array([ds[i][1] for i in range(len(ds))])
        counts = np.bincount(labels, minlength=num_classes)
        overall_counts += counts
        total = int(len(labels))
        if num_classes <= 20:
            dist_str = "  ".join([f"{i:2d}:{counts[i]:4d}" for i in range(num_classes)])
        else:
            top5 = np.argsort(counts)[-5:][::-1]
            dist_str = "  ".join([f"cls{cls}:{counts[cls]:4d}({counts[cls]/total*100:.1f}%)" for cls in top5])
        lines.append(f"Client {cid:2d}: total={total:5d} | {dist_str}")
    lines.append("-" * 70)
    if num_classes <= 20:
        class_total_str = "  ".join([f"{i:2d}:{overall_counts[i]:5d}" for i in range(num_classes)])
    else:
        top10 = np.argsort(overall_counts)[-10:][::-1]
        class_total_str = "  ".join([f"cls{cls}:{overall_counts[cls]:5d}" for cls in top10])
    lines.append(f"Per-class total: {class_total_str}")
    lines.append(f"Overall total samples: {overall_counts.sum()}")
    lines.append("=" * 70)
    logger.info("\n" + "\n".join(lines))


def partition_data_iid(dataset: Dataset, num_clients: int) -> List[Subset]:
    num_items = len(dataset)
    items_per_client = num_items // num_clients
    client_datasets = []
    all_indices = np.random.permutation(num_items)
    for i in range(num_clients):
        start_idx = i * items_per_client
        end_idx = (i + 1) * items_per_client if i < num_clients - 1 else num_items
        client_indices = all_indices[start_idx:end_idx]
        client_datasets.append(Subset(dataset, client_indices))
    from ckks_fed.log_utils import tag_info, LogTag
    tag_info(LogTag.INIT, f"IID partition: ~{items_per_client} samples/client")
    return client_datasets


def partition_data_noniid(dataset: Dataset, num_clients: int, alpha: float = 0.5) -> List[Subset]:
    if hasattr(dataset, 'targets'):
        labels = np.array(dataset.targets)
    elif hasattr(dataset, 'labels'):
        labels = np.array(dataset.labels)
    else:
        labels = np.array([dataset[i][1] for i in range(len(dataset))])
    num_classes = len(np.unique(labels))
    class_indices = [np.where(labels == i)[0] for i in range(num_classes)]
    client_indices = [[] for _ in range(num_clients)]
    for class_idx in class_indices:
        np.random.shuffle(class_idx)
        proportions = np.random.dirichlet(np.repeat(alpha, num_clients))
        proportions = proportions / proportions.sum()
        splits = (np.cumsum(proportions) * len(class_idx)).astype(int)[:-1]
        for i, idx_set in enumerate(np.split(class_idx, splits)):
            client_indices[i].extend(idx_set.tolist())
    # Ensure no client has empty dataset (Dirichlet α→0 can produce zeros)
    for i in range(num_clients):
        if len(client_indices[i]) == 0:
            richest = max(range(num_clients), key=lambda j: len(client_indices[j]))
            client_indices[i].append(client_indices[richest].pop())

    client_datasets = [Subset(dataset, indices) for indices in client_indices]
    from ckks_fed.log_utils import tag_info, LogTag
    tag_info(LogTag.INIT, f"Non-IID partition (α={alpha}): samples/client={[len(ds) for ds in client_datasets]}")
    return client_datasets


def create_client_dataloaders(dataset_name: str, num_clients: int, batch_size: int,
                              data_dir: str = "./data", iid: bool = True, 
                              alpha: float = 0.5) -> Tuple[List[DataLoader], DataLoader]:
    train_dataset, test_dataset = load_dataset(dataset_name, data_dir)
    if iid:
        client_datasets = partition_data_iid(train_dataset, num_clients)
    else:
        client_datasets = partition_data_noniid(train_dataset, num_clients, alpha)
    num_classes = get_dataset_num_classes(dataset_name)
    log_client_distributions(client_datasets, num_classes)
    client_loaders = [DataLoader(ds, batch_size=batch_size, shuffle=True) for ds in client_datasets]
    test_loader = DataLoader(test_dataset, batch_size=batch_size, shuffle=False)
    return client_loaders, test_loader


def train_epoch(model: nn.Module, dataloader: DataLoader, criterion: nn.Module,
                optimizer: optim.Optimizer, device: str) -> Tuple[float, float]:
    model.train()
    total_loss = 0.0
    correct = 0
    total = 0
    for batch_idx, (data, target) in enumerate(dataloader):
        data, target = data.to(device), target.to(device)
        optimizer.zero_grad()
        output = model(data)
        loss = criterion(output, target)
        loss.backward()
        optimizer.step()
        total_loss += loss.item()
        _, predicted = output.max(1)
        total += target.size(0)
        correct += predicted.eq(target).sum().item()
    avg_loss = total_loss / len(dataloader)
    accuracy = 100. * correct / total
    return avg_loss, accuracy


def evaluate(model: nn.Module, dataloader: DataLoader, criterion: nn.Module,
             device: str) -> Tuple[float, float]:
    model.eval()
    total_loss = 0.0
    correct = 0
    total = 0
    with torch.no_grad():
        for data, target in dataloader:
            data, target = data.to(device), target.to(device)
            output = model(data)
            loss = criterion(output, target)
            total_loss += loss.item()
            _, predicted = output.max(1)
            total += target.size(0)
            correct += predicted.eq(target).sum().item()
    avg_loss = total_loss / len(dataloader)
    accuracy = 100. * correct / total
    return avg_loss, accuracy


def get_model_params(model: nn.Module) -> Dict[str, torch.Tensor]:
    return {name: param.data.clone() for name, param in model.named_parameters()}


def set_model_params(model: nn.Module, params: Dict[str, torch.Tensor]):
    with torch.no_grad():
        for name, param in model.named_parameters():
            if name in params:
                param.copy_(params[name])


def compute_model_update(old_params: Dict[str, torch.Tensor],
                         new_params: Dict[str, torch.Tensor]) -> Dict[str, torch.Tensor]:
    update = {}
    for name in old_params:
        update[name] = new_params[name] - old_params[name]
    return update


def apply_model_update(model: nn.Module, update: Dict[str, torch.Tensor],
                       learning_rate: float = 1.0):
    with torch.no_grad():
        for name, param in model.named_parameters():
            if name in update:
                param.add_(update[name], alpha=learning_rate)
