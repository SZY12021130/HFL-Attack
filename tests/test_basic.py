"""
基础功能测试脚本
验证CKKS-Fed各模块的基本功能
"""

import torch
import numpy as np
import yaml
import sys
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def test_imports():
    """测试模块导入"""
    logger.info("测试模块导入...")
    try:
        from ckks_fed.crypto import CKKSContext, CKKSEncryptor, CKKSDecryptor, CKKSManager
        from ckks_fed.models import MLP, CNN, create_model
        from ckks_fed.data import get_data_transforms, load_dataset
        from ckks_fed.client import FederatedClient
        from ckks_fed.server import AggregationServer
        logger.info("✓ 所有模块导入成功")
        return True
    except Exception as e:
        logger.error(f"✗ 模块导入失败: {e}")
        return False


def test_ckks_basic():
    """测试CKKS基本功能"""
    logger.info("测试CKKS基本功能...")
    try:
        from ckks_fed.crypto import CKKSContext, CKKSEncryptor, CKKSDecryptor, CKKSEvaluator
        
        # 创建CKKS上下文
        context = CKKSContext(poly_modulus_degree=4096, 
                             coeff_mod_bit_sizes=[40, 20, 40],
                             global_scale=2**20)
        
        # 创建加密器、解密器、评估器
        encryptor = CKKSEncryptor(context)
        decryptor = CKKSDecryptor(context)
        evaluator = CKKSEvaluator(context)
        
        # 测试数据
        data1 = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        data2 = np.array([0.5, 1.0, 1.5, 2.0, 2.5])
        
        # 加密
        ct1 = encryptor.encrypt(data1)
        ct2 = encryptor.encrypt(data2)
        
        # 同态加法
        ct_add = evaluator.add(ct1, ct2)
        result_add = decryptor.decrypt(ct_add)
        
        # 验证
        expected_add = data1 + data2
        error_add = np.abs(result_add[:5] - expected_add).max()
        
        # 标量乘法
        ct_scalar = evaluator.multiply_scalar(ct1, 2.0)
        result_scalar = decryptor.decrypt(ct_scalar)
        expected_scalar = data1 * 2.0
        error_scalar = np.abs(result_scalar[:5] - expected_scalar).max()
        
        logger.info(f"  加法误差: {error_add:.6f}")
        logger.info(f"  标量乘法误差: {error_scalar:.6f}")
        
        if error_add < 0.5 and error_scalar < 0.5:
            logger.info("✓ CKKS基本功能测试通过")
            return True
        else:
            logger.warning("✗ CKKS精度误差较大")
            return False
            
    except Exception as e:
        logger.error(f"✗ CKKS测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_model_creation():
    """测试模型创建"""
    logger.info("测试模型创建...")
    try:
        from ckks_fed.models import MLP, CNN
        
        # 测试MLP
        mlp = MLP(input_dim=784, hidden_dims=[256, 128], num_classes=10)
        x = torch.randn(4, 784)
        y = mlp(x)
        assert y.shape == (4, 10), f"MLP输出形状错误: {y.shape}"
        logger.info(f"  MLP参数数量: {mlp.get_model_size()}")
        
        # 测试CNN
        cnn = CNN(input_channels=1, num_classes=10)
        x = torch.randn(4, 1, 28, 28)
        y = cnn(x)
        assert y.shape == (4, 10), f"CNN输出形状错误: {y.shape}"
        logger.info(f"  CNN参数数量: {cnn.get_model_size()}")
        
        logger.info("✓ 模型创建测试通过")
        return True
        
    except Exception as e:
        logger.error(f"✗ 模型创建测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_data_loading():
    """测试数据加载"""
    logger.info("测试数据加载...")
    try:
        from ckks_fed.data import load_dataset
        
        # 尝试加载MNIST（会下载）
        logger.info("  尝试加载MNIST数据集...")
        train_dataset, test_dataset = load_dataset("mnist", "./data")
        
        logger.info(f"  训练集大小: {len(train_dataset)}")
        logger.info(f"  测试集大小: {len(test_dataset)}")
        
        # 测试获取一个样本
        sample, label = train_dataset[0]
        logger.info(f"  样本形状: {sample.shape}, 标签: {label}")
        
        logger.info("✓ 数据加载测试通过")
        return True
        
    except Exception as e:
        logger.error(f"✗ 数据加载测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_config_loading():
    """测试配置加载"""
    logger.info("测试配置加载...")
    try:
        with open("configs/config.yaml", "r", encoding='utf-8') as f:
            config = yaml.safe_load(f)
        
        logger.info(f"  客户端数量: {config['system']['num_clients']}")
        logger.info(f"  训练轮数: {config['system']['num_rounds']}")
        logger.info(f"  数据集: {config['data']['dataset']}")
        logger.info(f"  模型: {config['model']['name']}")
        
        logger.info("✓ 配置加载测试通过")
        return True
        
    except Exception as e:
        logger.error(f"✗ 配置加载测试失败: {e}")
        return False


def test_ckks_manager():
    """测试CKKS管理器"""
    logger.info("测试CKKS管理器...")
    try:
        from ckks_fed.crypto import CKKSManager
        import torch
        
        config = {
            'poly_modulus_degree': 4096,
            'coeff_mod_bit_sizes': [40, 20, 40],
            'global_scale': 1048576
        }
        
        manager = CKKSManager(config)
        
        # 测试加密/解密一个小的模型更新
        model_update = {
            'fc1.weight': torch.randn(10, 5),
            'fc1.bias': torch.randn(10)
        }
        
        encrypted = manager.encrypt_model_update(model_update)
        shapes = {k: v.shape for k, v in model_update.items()}
        decrypted = manager.decrypt_model_update(encrypted, shapes)
        ct_size = manager.compute_encrypted_update_size(encrypted)
        pt_size = sum(p.numel() * 4 for p in model_update.values())
        
        logger.info(f"  加密参数数量: {len(encrypted)}")
        logger.info(f"  解密后参数数量: {len(decrypted)}")
        logger.info(f"  明文大小: {pt_size}B, 密文大小: {ct_size}B, 膨胀率: {ct_size/pt_size:.1f}x")
        
        logger.info("✓ CKKS管理器测试通过")
        return True
        
    except Exception as e:
        logger.error(f"✗ CKKS管理器测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_dataset_support():
    """测试多数据集支持"""
    logger.info("测试数据集支持...")
    try:
        from ckks_fed.data import get_dataset_num_classes, get_data_transforms
        from ckks_fed.models import create_model
        import yaml
        
        datasets = ["mnist", "fashion_mnist", "cifar10", "cifar100", "svhn", "emnist"]
        for ds in datasets:
            num_classes = get_dataset_num_classes(ds)
            train_tf, test_tf = get_data_transforms(ds)
            logger.info(f"  {ds}: 类别数={num_classes}, transform OK")
        
        with open("configs/config.yaml", "r", encoding='utf-8') as f:
            config = yaml.safe_load(f)
        
        # 测试CIFAR-100模型创建
        config['data']['dataset'] = "cifar100"
        config['data']['num_classes'] = 100
        cnn100 = create_model("cnn", config)
        x = torch.randn(2, 3, 32, 32)
        y = cnn100(x)
        assert y.shape == (2, 100), f"CIFAR-100 CNN输出错误: {y.shape}"
        logger.info(f"  CIFAR-100 CNN参数数量: {cnn100.get_model_size()}")
        
        logger.info("✓ 数据集支持测试通过")
        return True
        
    except Exception as e:
        logger.error(f"✗ 数据集支持测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def run_all_tests():
    """运行所有测试"""
    logger.info("=" * 60)
    logger.info("CKKS-Fed 基础功能测试")
    logger.info("=" * 60)
    
    tests = [
        ("配置加载", test_config_loading),
        ("模块导入", test_imports),
        ("CKKS基本功能", test_ckks_basic),
        ("模型创建", test_model_creation),
        ("数据加载", test_data_loading),
        ("CKKS管理器", test_ckks_manager),
        ("数据集支持", test_dataset_support),
    ]
    
    results = []
    for name, test_func in tests:
        logger.info(f"\n{'-' * 40}")
        result = test_func()
        results.append((name, result))
    
    # 汇总结果
    logger.info("\n" + "=" * 60)
    logger.info("测试结果汇总")
    logger.info("=" * 60)
    
    passed = sum(1 for _, r in results if r)
    total = len(results)
    
    for name, result in results:
        status = "✓ 通过" if result else "✗ 失败"
        logger.info(f"  {name}: {status}")
    
    logger.info(f"\n总计: {passed}/{total} 测试通过")
    
    return passed == total


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
