"""
测试 GlobalMaskVote 和 PackingStrategy 的边界对齐。
验证投票切分大小和加密 chunk 大小完全一致。
"""
import torch
from ckks.packing import MergedPacking
from ckks.global_mask_vote import GlobalMaskVote


def make_lenet_update():
    """构造 LeNet 风格的模型更新（44,426 参数）"""
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


def test_merged_alignment():
    """MergedPacking: batch_size = slot_count = 4096"""
    slot_count = 4096
    packing = MergedPacking(slot_count)
    update = make_lenet_update()

    # packing 切分
    chunks, metadata = packing.pack(update)
    packing_batches = len(chunks)

    # 投票切分
    gmv = GlobalMaskVote(mode="union")
    total_params = sum(p.numel() for p in update.values())
    values_per_chunk = packing.get_values_per_chunk()  # = 4096
    expected_batches = (total_params + values_per_chunk - 1) // values_per_chunk

    mask = gmv.client_compute_mask_from_update(
        update, batch_size=values_per_chunk, sparsity_rate=0.3
    )

    print(f"[Merged] total_params={total_params}, values_per_chunk={values_per_chunk}")
    print(f"[Merged] packing_batches={packing_batches}, expected_batches={expected_batches}, mask_len={len(mask)}")

    assert packing_batches == expected_batches, \
        f"Merged: packing_batches({packing_batches}) != expected_batches({expected_batches})"
    assert len(mask) == packing_batches, \
        f"Merged: mask_len({len(mask)}) != packing_batches({packing_batches})"
    print("[Merged] PASS: 边界对齐\n")


if __name__ == '__main__':
    test_merged_alignment()
    print("=" * 60)
    print("All GlobalMaskVote alignment tests passed!")
