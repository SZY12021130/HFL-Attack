import torch
import numpy as np
import sys


def generate_sparse_delta(shape, sparsity=0.999):
    """
    生成FL场景下的稀疏delta（绝大多数元素为0，仅少数非零）
    :param shape: 张量形状（模拟完整模型参数形状）
    :param sparsity: 稀疏度（0.999表示99.9%的元素为0）
    :return: 稀疏delta张量
    """
    # 生成基础delta（小数值变化量）
    delta = torch.randn(shape) * 0.01  # delta数值远小于W的数值（模拟参数增量）
    # 生成掩码，仅保留(1-sparsity)的非零元素
    mask = torch.bernoulli(torch.ones(shape) * (1 - sparsity))
    sparse_delta = delta * mask
    return sparse_delta


def serialize_sparse_tensor(tensor):
    """
    稀疏张量的序列化（仅存储非零元素的索引+数值，模拟FL真实传输）
    :param tensor: 输入张量
    :return: 序列化后的字节流、字节大小
    """
    # 提取非零元素的索引和数值
    non_zero_indices = torch.nonzero(tensor, as_tuple=True)
    non_zero_values = tensor[non_zero_indices]

    # 序列化（索引+数值）
    # 索引：转为numpy数组后序列化
    indices_bytes = b''
    for idx in non_zero_indices:
        indices_bytes += idx.numpy().tobytes()
    # 数值：转为numpy数组后序列化
    values_bytes = non_zero_values.numpy().tobytes()

    # 总字节流（索引+数值）
    serialized = indices_bytes + values_bytes
    return serialized, len(serialized)


def serialize_dense_tensor(tensor):
    """
    稠密张量的序列化（存储所有元素，模拟完整模型W的传输）
    :param tensor: 输入张量
    :return: 序列化后的字节流、字节大小
    """
    serialized = tensor.numpy().tobytes()
    return serialized, len(serialized)


def calculate_transfer_time(size_bytes, bandwidth_mbps=10):
    """
    计算传输耗时（模拟真实带宽）
    :param size_bytes: 传输字节大小
    :param bandwidth_mbps: 带宽（单位：MB/s，模拟移动端10MB/s）
    :return: 传输耗时（秒）
    """
    size_mb = size_bytes / (1024 * 1024)  # 字节转MB
    time_seconds = size_mb / bandwidth_mbps
    return time_seconds


if __name__ == "__main__":
    # ===================== Step 1: 模拟真实FL场景的模型参数 =====================
    # 模拟ResNet-18级别的模型参数形状（简化为1100万元素，对应真实44MB的float32模型）
    model_shape = (11000000,)  # 1维张量，模拟扁平化后的模型参数
    bandwidth = 10  # 移动端典型带宽：10MB/s

    # 生成初始全局模型W_t（稠密张量，数值范围[-10,10]）
    W_t = torch.randn(model_shape) * 10
    print(f"=== 初始全局模型 W_t 信息 ===")
    print(f"张量形状: {W_t.shape}")
    print(f"张量数值范围: [{W_t.min().item():.4f}, {W_t.max().item():.4f}]")
    dense_Wt_bytes, dense_Wt_size = serialize_dense_tensor(W_t)
    print(f"稠密序列化大小: {dense_Wt_size / (1024 * 1024):.2f} MB")
    print(f"传输耗时: {calculate_transfer_time(dense_Wt_size, bandwidth):.4f} 秒\n")

    # ===================== Step 2: 生成多轮稀疏delta =====================
    R = 3  # 预取轮数
    delta_list = []
    for i in range(R):
        delta = generate_sparse_delta(model_shape, sparsity=0.999)
        delta_list.append(delta)
        # 打印单轮delta信息
        sparse_delta_bytes, sparse_delta_size = serialize_sparse_tensor(delta)
        print(f"=== 第{i + 1}轮 delta 信息 ===")
        print(f"张量形状: {delta.shape}")
        print(f"非零元素占比: {torch.count_nonzero(delta) / delta.numel():.6f}")
        print(f"稀疏序列化大小: {sparse_delta_size / 1024:.2f} KB")
        print(f"传输耗时: {calculate_transfer_time(sparse_delta_size, bandwidth):.6f} 秒\n")

    # ===================== Step 3: 验证张量数值等式 =====================
    # 计算W_t + sum(delta_list) = W_{t+R}
    sum_delta = torch.sum(torch.stack(delta_list), dim=0)
    W_t_plus_R = W_t + sum_delta

    # 验证逐轮叠加 vs 直接求和的数值一致性
    W_t_iter = W_t.clone()
    for delta in delta_list:
        W_t_iter += delta
    numerical_equal = torch.allclose(W_t_plus_R, W_t_iter, atol=1e-6)
    print(f"=== 张量数值等式验证 ===")
    print(f"W_t + sum(delta_1~delta_{R}) 与逐轮叠加结果是否一致: {numerical_equal}")

    # ===================== Step 4: 对比序列化大小 =====================
    # W_{t+R}的稠密序列化大小
    dense_WtR_bytes, dense_WtR_size = serialize_dense_tensor(W_t_plus_R)
    # sum_delta的稀疏序列化大小
    sparse_sum_delta_bytes, sparse_sum_delta_size = serialize_sparse_tensor(sum_delta)

    print(f"\n=== 序列化大小对比 ===")
    print(
        f"W_{t + R} 稠密序列化大小: {dense_WtR_size / (1024 * 1024):.2f} MB (传输耗时: {calculate_transfer_time(dense_WtR_size, bandwidth):.4f} 秒)")
    print(
        f"sum(delta_1~delta_{R}) 稀疏序列化大小: {sparse_sum_delta_size / 1024:.2f} KB (传输耗时: {calculate_transfer_time(sparse_sum_delta_size, bandwidth):.6f} 秒)")
    print(
        f"单轮delta 稀疏序列化大小: {sparse_delta_size / 1024:.2f} KB (传输耗时: {calculate_transfer_time(sparse_delta_size, bandwidth):.6f} 秒)")

    # ===================== Step 5: 模拟“未预取完delta”场景 =====================
    # 假设预取了R-1轮，剩余1轮未预取，需补取
    remaining_delta = delta_list[-1]
    remaining_delta_bytes, remaining_delta_size = serialize_sparse_tensor(remaining_delta)
    print(f"\n=== 未预取完delta场景验证 ===")
    print(f"需补取的单轮delta大小: {remaining_delta_size / 1024:.2f} KB")
    print(f"补取delta耗时: {calculate_transfer_time(remaining_delta_size, bandwidth):.6f} 秒")
    print(f"下载完整W_{t + R}耗时: {calculate_transfer_time(dense_WtR_size, bandwidth):.4f} 秒")
    print(
        f"补取delta比下载完整W快: {calculate_transfer_time(dense_WtR_size, bandwidth) / calculate_transfer_time(remaining_delta_size, bandwidth):.0f} 倍")