"""
仓网布局优化 — 可配置参数
模块加载时自动从 ADAM_SYS_PARAM 读取配置，失败则用默认值。
"""
import logging

logger = logging.getLogger(__name__)

# ==================== 默认值 ====================

_R_w_default = 1.0 / 12.0       # 区县库房盘点周期（年），1 个月
_L_w_default = 0.5 / 12.0       # 区县库房补货提前期（年），0.5 个月
_z_w_default = 1.645             # 区县库房服务水平系数（95%）
_ANNUAL_INTEREST_RATE_default = 0.05     # 年持有成本利率
_TRANSPORT_UNIT_PRICE_default = 0.0695   # 上游运输单价（元/公里/件）
_N_PARETO_SOLUTIONS_default = 10         # 帕累托前沿解数量

# ==================== 模块级可配置变量 ====================

R_w = _R_w_default
L_w = _L_w_default
z_w = _z_w_default
ANNUAL_INTEREST_RATE = _ANNUAL_INTEREST_RATE_default
TRANSPORT_UNIT_PRICE = _TRANSPORT_UNIT_PRICE_default
N_PARETO_SOLUTIONS = _N_PARETO_SOLUTIONS_default


def _load_from_db():
    """从 ADAM_SYS_PARAM 读取配置并更新模块级变量。"""
    global R_w, L_w, z_w, ANNUAL_INTEREST_RATE, TRANSPORT_UNIT_PRICE, N_PARETO_SOLUTIONS
    try:
        from backend.api.data_api.fetch_data import query_adam_sys_param
        db_params = query_adam_sys_param()
        if db_params:
            R_w = float(db_params.get('WL_R_W', _R_w_default))
            L_w = float(db_params.get('WL_L_W', _L_w_default))
            z_w = float(db_params.get('WL_Z_W', _z_w_default))
            ANNUAL_INTEREST_RATE = float(
                db_params.get('WL_ANNUAL_INTEREST_RATE', _ANNUAL_INTEREST_RATE_default))
            TRANSPORT_UNIT_PRICE = float(
                db_params.get('WL_TRANSPORT_UNIT_PRICE', _TRANSPORT_UNIT_PRICE_default))
            N_PARETO_SOLUTIONS = int(float(
                db_params.get('WL_N_PARETO_SOLUTIONS', _N_PARETO_SOLUTIONS_default)))
            logger.info(f"[仓网配置] 已从数据库加载: "
                        f"R_w={R_w:.4f}, L_w={L_w:.4f}, z_w={z_w}, "
                        f"rate={ANNUAL_INTEREST_RATE}, "
                        f"trans_price={TRANSPORT_UNIT_PRICE}, "
                        f"n_pareto={N_PARETO_SOLUTIONS}")
            return
    except Exception as e:
        logger.warning(f"[仓网配置] 数据库读取失败，使用默认值: {e}")
    # 读取失败，回退默认值
    R_w = _R_w_default
    L_w = _L_w_default
    z_w = _z_w_default
    ANNUAL_INTEREST_RATE = _ANNUAL_INTEREST_RATE_default
    TRANSPORT_UNIT_PRICE = _TRANSPORT_UNIT_PRICE_default
    N_PARETO_SOLUTIONS = _N_PARETO_SOLUTIONS_default


# 模块加载时自动读取数据库配置
_load_from_db()
