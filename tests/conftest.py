"""
pytest 全局配置：在任何测试模块（及 torch/tenseal）导入前设置
OMP 双 libiomp 兼容开关——MKL 与 TenSEAL 各带一份 libiomp5md.dll，
后初始化的运行库会触发 OMP Error #15 直接 abort 整个 pytest 进程。
"""

import os

os.environ.setdefault('KMP_DUPLICATE_LIB_OK', 'TRUE')
