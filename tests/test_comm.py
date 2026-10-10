import math
import pandas as pd


class xMKCKKS_Comm_Simulator:
    def __init__(self, num_clients, total_params, sparsity_ratio, slot_size=8192, poly_size_mb=0.955):
        self.M = num_clients
        self.total_params = total_params
        self.sparsity = sparsity_ratio
        self.slot_size = slot_size
        self.poly_size = poly_size_mb  # 1个多项式的大小（单位：MB）

    def simulate_communication(self):
        # 提取的重要参数数量
        important_params = self.total_params * self.sparsity
        # 密集打包需要的基础密文数量 C
        num_ciphertexts = math.ceil(important_params / self.slot_size)

        # --- Round 1: 掩码生成 ---
        r1_down_mb = (self.total_params * 4) / (1024 ** 2)  # 明文模型 (Float32)
        r1_up_mb = (self.total_params / 8) / (1024 ** 2)  # 1-bit 掩码

        # --- Round 2: 密文上传 ---
        r2_down_mb = r1_up_mb  # 全局掩码下发
        # 客户端初始密文：2个多项式
        r2_up_mb = num_ciphertexts * 2 * self.poly_size

        # --- Round 3: 部分解密 ---
        # 多密钥核心膨胀：聚合密文包含 (M + 1) 个多项式
        r3_down_mb = num_ciphertexts * (self.M + 1) * self.poly_size
        # 理论最优的部分解密份额：1个多项式
        r3_up_optimized_mb = num_ciphertexts * 1 * self.poly_size

        # 汇总单个客户端的总通信量
        client_total_down = r1_down_mb + r2_down_mb + r3_down_mb
        client_total_up = r1_up_mb + r2_up_mb + r3_up_optimized_mb

        # 汇总服务端 (Server) 的总网络吞吐量 (广播给 M 个人，接收 M 个人的数据)
        server_total_transmit = client_total_down * self.M
        server_total_receive = client_total_up * self.M

        return {
            "Num_Clients": self.M,
            "Ciphertexts_per_Client": num_ciphertexts,
            "Client_Down_MB": round(client_total_down, 2),
            "Client_Up_MB": round(client_total_up, 2),
            "Server_Transmit_MB": round(server_total_transmit, 2),
            "Server_Receive_MB": round(server_total_receive, 2)
        }


# 运行模拟：测试客户端规模扩张对 xMKCKKS 通信量的影响
if __name__ == "__main__":
    # 以类似 ResNet 的小型模型为例 (100万参数)，保留10%参数
    total_parameters = 1_000_000

    results = []
    for clients in [5, 10, 20, 50, 100]:
        sim = xMKCKKS_Comm_Simulator(
            num_clients=clients,
            total_params=total_parameters,
            sparsity_ratio=0.1
        )
        results.append(sim.simulate_communication())

    df = pd.DataFrame(results)
    print(df.to_string(index=False))