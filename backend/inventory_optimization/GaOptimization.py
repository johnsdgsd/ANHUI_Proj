"""
遗传算法库存优化适配器

入参/出参与 GetMonthlyOrder.GenerateMonthlyThresholdAndOrder 完全一致，
内部复用 RunOptimize.run_optimization_from_api 的遗传算法逻辑。
"""

import pandas as pd
from datetime import datetime


def GenerateMonthlyThresholdAndOrderGA(
    year: str,
    month: str,
    init_stock: pd.DataFrame,
    tag: str,
    alpha: float,
    n_iter: int = 10,
    pop_size: int = 200,
    n_processor: int = 10,
    verbose: bool = False
):
    """
    使用遗传算法生成月度库存阈值和补货订单。

    参数（与 GenerateMonthlyThresholdAndOrder 一致）:
        year:       年份, 如 '2026'
        month:      月份, 如 '05'
        init_stock: 月初库存 DataFrame (ORG_NO, DEV_CODE, STOCK_NUM)
        tag:        全局方案标识 (GLOBAL_SCHEME_ID)
        alpha:      目标满足率 (epsilon)

    返回:
        (MonthlyThreshold, MonthlyOrder, DemandPre)
    """
    from backend.inventory_optimization.RunOptimize import run_optimization_from_api
    from backend.api.data_api.fetch_data import query_adam_yqm_dmd_pre_by_year_month

    init_stock_month = int(f"{year}{month}")

    # ---- 调用遗传算法（复用现有 API） ----
    InventoryThreshold, InventoryOrder = run_optimization_from_api(
        init_stock_month=init_stock_month,
        tag=str(tag),
        n_iter=n_iter,
        pop_size=pop_size,
        epsilon=alpha,
        n_processor=n_processor,
        verbose=verbose
    )

    # ---- 构建 DemandPre（与 GenerateMonthlyThresholdAndOrder 一致） ----
    df = query_adam_yqm_dmd_pre_by_year_month(year, month)
    df_grouped = df.rename(columns={'PRE_NUM': '预测数量'})
    grouped = df_grouped.groupby(['ORG_NO', 'DEV_CODE'])

    demand_pre_rows = []
    for (org_no, dev_code), group in grouped:
        demand_pre_rows.append({
            'ORG_NO': org_no,
            'DEV_CODE': dev_code,
            'PRE_NUM': group['预测数量'].iloc[0]
        })

    return InventoryThreshold, InventoryOrder, pd.DataFrame(demand_pre_rows)
