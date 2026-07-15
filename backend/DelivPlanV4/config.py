"""
DelivPlanV4 全局常量

约束说明:
    - MAX_ROUTE_DIST: 路径闭环距离上限（硬约束）
    - ANGLE_COS_THRESHOLD: 夹角余弦阈值，两网点从省库出发夹角 ≤45°（硬约束）
    - MAX_STOPS: 每条路径最大停靠站点数（硬约束）
    - ILP_TIMEOUT_SEC: ILP 求解超时时间
"""

MAX_ROUTE_DIST = 750
ANGLE_COS_THRESHOLD = 0.707   # cos(45°)
MAX_STOPS = 3
ILP_TIMEOUT_SEC = 60
NEAR_DEPOT_DIST_THRESHOLD = 80  # 距省库 ≤ 此距离的站点不受夹角约束
