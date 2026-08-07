"""
调拨场景二（缺货调拨）— 公共配置

触发/目标参数对齐一阶段紧急补库（EmergReplenishV2）默认值。
"""
# ---- 缺货检测（stockout_detect）----
THRESHOLD_PERCENTILE = 0.5   # 触发阈值分位数 α（对齐一阶段）
TARGET_PERCENTILE = 0.9      # 目标服务水平分位数 β（对齐一阶段）
MAX_LEAD_TIME = 7            # 最大提前期（天）

# ---- 库存上限（supplier_select 调出判定标准）----
UPPER_WINDOW_DAYS = 14       # 未来两周
UPPER_Z = 1.645              # 0.95 正态分位（Poisson 近似）

# ---- 输出字段 ----
SEND_REASON = '缺货调拨'      # ADAM_ALLOT_DAY_PLAN_PRE.SEND_REASON（与场景一 '高库龄' 区分）
REPLE_TASK_TYPE = '02'       # 紧急补库类型（ADAM_PLAN_DAY_IAS_PRE）
TASK_SOURCE = '03'           # 任务来源：算法生成
DAILY_PLAN_STATUS = '01'     # 计划状态：未确认

# ---- 主键序列 ----
SEQ_ALLOT_DAY_PLAN_PRE = 'SEQ_ADAM_ALLOT_DAY_PLAN_PRE'
SEQ_PLAN_DAY_IAS_PRE = 'SEQ_ADAM_PLAN_DAY_IAS_PRE'

# ---- 设备规格缺失默认值 ----
DEFAULT_DEV_CLS = '00'
DEFAULT_DEV_CATEG = '00'
