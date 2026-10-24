"""
集成测试：验证 server.run_global_mask_vote 的边界对齐。
模拟完整的投票流程，确认 num_batches 与 packing 策略一致。
"""
import torch
import numpy as np
from unittest.mock import MagicMock

from ckks.server import AggregationServer
from ckks.packing import MergedPacking
from ckks.global_mask_vote import GlobalMaskVote


def make_lenet_update():
    return {
        'conv1.weight': torch.randn(6, 1, 5, 5),
        'conv1.bias': torch.randn(6),
        'conv2.weight': torch.randn(16, 6, 5, 5),
        'conv2.bias': torch.randn(16),
        'fc1.weight': torch.randn(120, 256),
        'fc1.bias': torch.randn(120),
        'fc2.weight': torch.randn(84, 120),
        'fc2.bias': torch.randn(84),
        'fc3.weight': torch.randn(10, 84),
        'fc3.bias': torch.randn(10),
    }


def test_server_gmv_with_merged():
    """测试 Merged 场景下 server.run_global_mask_vote 的 num_batches 计算"""

    class MockModel(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.conv1 = torch.nn.Conv2d(1, 6, 5)
            self.conv2 = torch.nn.Conv2d(6, 16, 5)
            self.fc1 = torch.nn.Linear(256, 120)
            self.fc2 = torch.nn.Linear(120, 84)
            self.fc3 = torch.nn.Linear(84, 10)

    model = MockModel()

    config = {
        'global_mask_vote': {
            'mode': 'union',
            'sparsity_rate': 0.3,
            'scoring_method': 'avg_abs',
        },
        'ckks': {'sparsification': {'strategy': 'none'}},
        'system': {'upload_difference': True},
        'defense': {'aggregation_rule': 'fedavg'},
    }

    ckks_manager = MagicMock()
    ckks_manager.batch_size = 4096

    packing = MergedPacking(4096)
    ckks_manager.packing_strategy = packing

    server = AggregationServer(model, config, device='cpu', ckks_manager=ckks_manager)

    # 客户端本地计算 mask 后上传（0/1 向量），服务器只接收 mask
    gmv = GlobalMaskVote(mode="union")
    values_per_chunk = packing.get_values_per_chunk()  # 4096
    client_masks = []
    for _ in range(3):
        update = make_lenet_update()
        mask = gmv.client_compute_mask_from_update(
            update, batch_size=values_per_chunk, sparsity_rate=0.3
        )
        client_masks.append(mask)

    chunks, _ = packing.pack(make_lenet_update())
    num_batches = len(chunks)

    global_mask = server.run_global_mask_vote(
        client_masks=client_masks,
        num_batches=num_batches,
        round_num=0,
    )

    total_params = sum(p.numel() for p in model.parameters())
    expected_batches = (total_params + values_per_chunk - 1) // values_per_chunk  # 11

    print(f"[Merged Server Integration]")
    print(f"  total_params={total_params}, values_per_chunk={values_per_chunk}")
    print(f"  packing_chunks={len(chunks)}, expected_batches={expected_batches}")
    print(f"  global_mask_len={len(global_mask)}, active={global_mask.sum()}")

    assert len(global_mask) == len(chunks), \
        f"mask_len({len(global_mask)}) != packing_chunks({len(chunks)})"
    assert len(global_mask) == expected_batches, \
        f"mask_len({len(global_mask)}) != expected_batches({expected_batches})"

    print("  PASS: server GMV 边界对齐\n")


if __name__ == '__main__':
    test_server_gmv_with_merged()
    print("=" * 60)
    print("All server integration tests passed!")
