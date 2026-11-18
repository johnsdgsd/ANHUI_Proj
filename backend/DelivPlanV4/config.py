"""
DelivPlanV4 全局常量

约束说明:
    - MAX_ROUTE_DIST: 路径闭环距离上限（硬约束）
    - ANGLE_COS_THRESHOLD: 夹角余弦阈值，两网点从省库出发夹角 ≤45°（硬约束）
    - MAX_STOPS: 每条路径最大停靠站点数（硬约束）
    - ILP_TIMEOUT_SEC: ILP 求解超时时间
    - NEAR_DEPOT_DIST_THRESHOLD: 角度豁免兜底阈值（优先用 hefei_nodes 集合判定）
    - HEFEI_EXCLUDE_DIST: 约束2 — 合肥四库房排斥半径 (km)
    - HEFEI_DEDICATED_LOAD_RATE: 约束3 — 合肥混合路线满载率上限
"""

MAX_ROUTE_DIST = 750
ANGLE_COS_THRESHOLD = 0.707   # cos(45°)

# ---- 运行时参数（默认值仅兜底；实际值由 params.py 读取 ADAM_SYS_PARAM）----
DEFAULT_MAX_STOPS = 3
MAX_STOPS = DEFAULT_MAX_STOPS    # 兼容旧 import；运行时以参数表为准
MAX_STOPS_LIMIT = 4
DEFAULT_GK_DIST_MIST = 10.0      # 短腿计费阈值默认值（km）
PT_VOL_MULT = 2.0                # 互感器(DEV_CLS='02')体积系数（V4 专用）

ILP_TIMEOUT_SEC = 60
# 4 站档：候选池约为 3 站的数倍，且 V4 有三轮车型放宽，单轮 300s、总预算 900s
ILP_TIMEOUT_SEC_4STOP = 300
ILP_TOTAL_BUDGET_SEC_4STOP = 900
MAX_CANDIDATES_4STOP = 100000
STAGE1_TIME_LIMIT_4STOP = 900

NEAR_DEPOT_DIST_THRESHOLD = 80  # 角度豁免兜底阈值（hefei_nodes 未知时使用）

# ==== V4 新增约束参数 ====
HEFEI_EXCLUDE_DIST = 150          # 约束2: 合肥四库房排斥半径 (km)
HEFEI_DEDICATED_LOAD_RATE = 0.7   # 约束3: 合肥混合路线满载率上限阈值
