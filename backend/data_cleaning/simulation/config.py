"""
仿真模块全局常量

数据路径、类别映射、GA 参数等。
"""

import os

# ==================== 路径 ====================

DATA_DIR = r"D:\WYJ\库存优化与检定排程\二阶段"
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")

INVENTORY_EXCEL = os.path.join(DATA_DIR, "库存统计表1月1日.xlsx")
DEVICE_MAPPING_EXCEL = os.path.join(DATA_DIR, "设备码归类映射.xlsx")
SMCP_EXCEL = os.path.join(DATA_DIR, "SMCP_V2_DEV_ADAM_YQM_DMD_PRE_EXPORT.xlsx")
INSTALL_EXCEL = os.path.join(DATA_DIR, "ADAM_HIS_DAY_INSTAL_SAMPLE.xlsx")

# ==================== 仿真参数 ====================

SIM_START_MONTH = 1   # 1月
SIM_END_MONTH = 6     # 6月
SIM_YEAR = 2026

# ==================== 6 设备类别 ====================

CATEGORIES = ["A级表", "B级表", "C级表", "D级表", "集中器", "专变终端"]

# 类别 → 典型设备码（GA 的 Item 以 DEV_CODE 为粒度）
CATEGORY_TO_DEVCODE = {
    "A级表":    "34000196",
    "B级表":    "34000202",
    "C级表":    "34000209",
    "D级表":    "34000213",
    "集中器":   "34000214",
    "专变终端": "34000269",
}

# 设备码 → 类别（反向映射）
DEVCODE_TO_CATEGORY = {v: k for k, v in CATEGORY_TO_DEVCODE.items()}

# 库存表 中文列名 → 标准类别名
INVENTORY_COL_TO_CATEGORY = {
    "A级表": "A级表",
    "B级表": "B级表",
    "C级表": "C级表",
    "D级表": "D级表",
    "集中器": "集中器",
    "专变终端": "专变终端",
}

# 设备码归类映射表 归类列 → 标准类别 的匹配规则
MAP_TABLE_CLASS_TO_CATEGORY = {
    "A级单相电能表": "A级表",
    "B级三相电能表": "B级表",
    "C级三相电能表": "C级表",
    "D级三相电能表": "D级表",
    "集中器":       "集中器",
    "专变采集终端":  "专变终端",
}

# ==================== GA 参数 ====================

GA_EPSILON = 0.95       # 目标满足率下限
GA_N_ITER = 10          # 遗传代数
GA_POP_SIZE = 200       # 种群大小
GA_N_PROCESSOR = 8      # 并行进程数

# Poisson 分布的 rate 参数（与 item.py 一致: T=1, tn=0.5 → rate=1.5）
POISSON_RATE = 1.5

# ==================== 成本参数 ====================

HOLDING_COST_RATE = 0.1   # 持有成本 = 单价 × 10%
SHORTAGE_COST_RATE = 0.5  # 缺货成本 = 单价 × 50%

# ==================== DEV_CLS 覆写（实现按类别独立 alpha）====================
# 6 个典型设备码在 spec 表中 DEV_CLS 全为 '01'，GA 会给它们同一个 alpha。
# 通过覆写不同的 DEV_CLS 值，让 GA 按类别分组优化 alpha —— 不改代码，只动数据。
DEV_CLS_OVERRIDE = {
    "34000196": "01_A",   # A级表
    "34000202": "01_B",   # B级表
    "34000209": "01_C",   # C级表
    "34000213": "01_D",   # D级表
    "34000214": "09_J",   # 集中器
    "34000269": "09_Z",   # 专变终端
}
