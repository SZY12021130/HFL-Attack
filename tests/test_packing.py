"""
测试 CKKS 可插拔打包策略
验证 layerwise / merged 两种模式的正确性
"""

import torch
from ckks_fed.crypto import CKKSManager
from ckks_fed.packing import create_packing_strategy, MergedPacking


def make_dummy_update():
    """构造一个类似 LeNet 的多层模型更新"""
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


def count_ciphertexts(encrypted_update):
    """统计密文数量（兼容 __sparse__ 格式）"""
    if encrypted_update.get('__sparse__'):
        return len(encrypted_update.get('ciphertexts', []))
    return sum(len(v) for v in encrypted_update.values() if hasattr(v, '__len__'))


def run_strategy(strategy_name: str):
    print(f"\n{'='*60}")
    print(f"Testing strategy: {strategy_name}")
    print(f"{'='*60}")

    ckks_config = {
        'poly_modulus_degree': 8192,
        'coeff_mod_bit_sizes': [40, 20, 40],
        'global_scale': 2**20,
        'slot_packing': True,
        'packing': {'strategy': strategy_name},
    }

    manager = CKKSManager(ckks_config)
    model_update = make_dummy_update()
    original_shapes = {k: v.shape for k, v in model_update.items()}

    # 加密
    encrypted = manager.encrypt_model_update(model_update)
    num_ct = count_ciphertexts(encrypted)
    ct_size = manager.compute_encrypted_update_size(encrypted)

    print(f"  Ciphertext count: {num_ct}")
    print(f"  Ciphertext size:  {ct_size / 1024 / 1024:.2f} MB")

    # 解密
    decrypted = manager.decrypt_model_update(encrypted, original_shapes)

    # 验证精度
    max_err = 0.0
    for name in model_update:
        err = torch.abs(model_update[name] - decrypted[name]).max().item()
        max_err = max(max_err, err)

    print(f"  Max decryption error: {max_err:.6e}")

    # 聚合测试
    encrypted2 = manager.encrypt_model_update(model_update)
    aggregated = manager.aggregate_encrypted_updates([encrypted, encrypted2], [0.5, 0.5])
    decrypted_agg = manager.decrypt_model_update(aggregated, original_shapes)

    agg_err = 0.0
    for name in model_update:
        expected = model_update[name]  # (x + x) * 0.5 = x
        err = torch.abs(expected - decrypted_agg[name]).max().item()
        agg_err = max(agg_err, err)

    print(f"  Max aggregation error: {agg_err:.6e}")

    if max_err < 1e-1 and agg_err < 5e-1:
        print(f"  [PASS] {strategy_name}")
    else:
        print(f"  [FAIL] {strategy_name}")

    return num_ct


if __name__ == '__main__':
    print("CKKS Packing Strategy Test Suite")

    # 测试三种策略
    ct_layerwise = run_strategy('layerwise')
    ct_merged = run_strategy('merged')

    print(f"\n{'='*60}")
    print("Summary:")
    print(f"  layerwise: {ct_layerwise} ciphertexts")
    print(f"  merged:    {ct_merged} ciphertexts")
    print(f"  reduction: {(1 - ct_merged/ct_layerwise)*100:.1f}%")
