import tenseal as ts

def main():
    # ==================== 明文聚合（不加密）====================
    plain1 = [1.1, 2.2, 3.3]
    plain2 = [4.4, 5.5, 6.6]
    plain_sum = [a + b for a, b in zip(plain1, plain2)]

    print("===== 明文聚合大小 =====")
    print(f"明文1 长度: {len(plain1)} 个数值")
    print(f"明文2 长度: {len(plain2)} 个数值")
    print(f"聚合后明文: {len(plain_sum)} 个数值")
    print(f"✅ 明文聚合：大小不变\n")

    # ==================== CKKS 密文聚合 ====================
    context = ts.Context(
        ts.SCHEME_TYPE.CKKS,
        poly_modulus_degree=8192,
        coeff_mod_bit_sizes=[60, 40, 40, 60]
    )
    context.global_scale = 2 ** 40

    enc1 = ts.ckks_vector(context, plain1)
    enc2 = ts.ckks_vector(context, plain2)
    enc_sum = enc1 + enc2

    print("===== CKKS 密文聚合大小 =====")
    print(f"密文1 多项式数量: {enc1.data.size()}")
    print(f"密文2 多项式数量: {enc2.data.size()}")
    print(f"聚合后密文多项式数量: {enc_sum.data.size()}")
    print(f"✅ CKKS 密文聚合：大小不变")

if __name__ == "__main__":
    main()