"""
多套计划补库数量生成器
用于生成多套不同策略的补库计划
"""
import pandas as pd
import numpy as np
import datetime
from global_optimization.logger import logger
from inventory_optimization.RunOptimize import run_optimization_from_api
import time

def GenerateMutiOrderScheme(yearMonth:str):
    '每月一日触发，输入的日期是当月1日的日期'
    epsilons = [0.99,0.995,0.999]
    OrderSchemes = {}
    ThresholdSchemes = {}
    for e in epsilons:
        tag = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
        Threshold, Order = run_optimization_from_api(
            init_stock_month=yearMonth,
            epsilon=e,
            tag=tag
        )
        OrderSchemes[tag] = Order
        ThresholdSchemes[tag] = Threshold

    
    return schemes


def PrepareDetail(Order: pd.DataFrame, Threshold: pd.DataFrame, init_stock: pd.DataFrame,
                  price_df: pd.DataFrame, monthly_holding_rate: float = 0.01) -> pd.DataFrame:
    """
    将补货计划表、月末库存阈值表、月初库存表、单价表合并为一张明细表，
    计算每个 (ORG_NO, DEV_CODE) 的：
        - 月需求 (DEMAND)
        - 月初库存 (I0)
        - 月末阈值 (I_END)
        - 日均库存 (AVG_INV)
        - 月度周转次数 (TURNOVER)
        - 持有成本 (HOLDING_COST)
        - 缺货成本 (SHORTAGE_COST，默认为0)
    
    参数:
        Order: 补货计划表，至少包含列 REC_ORG_NO, DEV_CODE, PLAN_IAS_NUM
        Threshold: 月末库存阈值表，至少包含列 ORG_NO, DEV_CODE, BASE_LIMIT
        init_stock: 月初库存表（已按目标月份过滤），至少包含列 ORG_NO, DEV_CODE, STOCK_NUM
        price_df: 单价表，至少包含列 DEV_CODE, TAX_UP (单价)
        monthly_holding_rate: 月持有成本率，默认0.01 (即1%每月)
    
    返回:
        DataFrame 包含列: ORG_NO, DEV_CODE, DEMAND, I0, I_END, AVG_INV, TURNOVER,
                         HOLDING_COST, SHORTAGE_COST
    """
    # 1. 汇总补货计划得到月需求（补货总量 = 需求总量）
    Order = Order[['REC_ORG_NO','DEV_CODE','PLAN_IAS_NUM','DEV_CLS','DEV_CATEG']]
    demand_agg = Order.groupby(['REC_ORG_NO', 'DEV_CODE','DEV_CLS','DEV_CATEG'], as_index=False)['PLAN_IAS_NUM'].sum()
    demand_agg.rename(columns={'REC_ORG_NO': 'ORG_NO', 'PLAN_IAS_NUM': 'DEMAND'}, inplace=True)

    # 2. 月初库存（直接使用传入的 init_stock，假定已为当前月份）
    init_f = init_stock[['ORG_NO', 'DEV_CODE', 'STOCK_NUM']].copy()
    init_f.rename(columns={'STOCK_NUM': 'I0'}, inplace=True)

    # 3. 月末阈值
    thresh_f = Threshold[['ORG_NO', 'DEV_CODE', 'BASE_LIMIT']].copy()
    thresh_f.rename(columns={'BASE_LIMIT': 'I_END'}, inplace=True)
    price_f = price_df[['DEV_CODE', 'TAX_UP']].copy()
    price_f.rename(columns={'TAX_UP': 'UNIT_PRICE'}, inplace=True)

    # 4. 左连接（以需求表为准）
    detail = demand_agg.merge(init_f, on=['ORG_NO', 'DEV_CODE'], how='left')
    detail = detail.merge(thresh_f, on=['ORG_NO', 'DEV_CODE'], how='left')
    detail = detail.merge(price_f, on=['DEV_CODE'], how='left')
    # 5. 填充缺失值（若无月初或月末库存，则填0）
    detail['I0'] = detail['I0'].fillna(0)
    detail['I_END'] = detail['I_END'].fillna(0)

    # 6. 计算日均库存
    detail['AVG_INV'] = (detail['I0'] + detail['I_END']) / 2.0
    # 月周转次数
    detail['TURNOVER'] = detail.apply(
        lambda row: row['DEMAND'] / row['AVG_INV'] if row['AVG_INV'] > 0 else 0,
        axis=1
    )
    # 9. 持有成本 = 日均库存 * 单价 * 月持有成本率 * 30天
    detail['HOLDING_COST'] = detail['AVG_INV'] * detail['UNIT_PRICE'] * monthly_holding_rate * 30
    # 10. 缺货成本（暂设为0，可根据业务扩展）
    detail['SHORTAGE_COST'] = 0.0
    return detail

def GetGlobalSchemeItem(detail: pd.DataFrame, scheme_no: str, yearMonth: str):
    from backend.api.data_api.fetch_data import query_adam_glob_strategy_scheme_by_month

    year = int(yearMonth[:4])
    month = int(yearMonth[4:])
    if month == 1:
        last_month = f"{year-1}12"
    else:
        last_month = f"{year}{month-1:02d}"
    last_year = f"{year-1}{month:02d}"

    # 获取历史数据（DataFrame，可能为空）
    df_last_month = query_adam_glob_strategy_scheme_by_month(last_month)
    df_last_year = query_adam_glob_strategy_scheme_by_month(last_year)

    # 提取历史值（取第一条记录的对应字段，若无数据则 None）
    def get_value(df, field):
        if df is not None and not df.empty:
            return df.iloc[0].get(field)
        return None

    cost_last_month = get_value(df_last_month, 'PRE_STAT_COST')
    cost_last_year = get_value(df_last_year, 'PRE_STAT_COST')
    itt_last_month = get_value(df_last_month, 'PRE_ITT')
    itt_last_year = get_value(df_last_year, 'PRE_ITT')

    # 计算当前指标
    focus = '03'
    total_demand = detail['DEMAND'].sum()
    total_holding_cost = detail['HOLDING_COST'].sum()
    total_deliver_cost = GetDeliverCost(detail)
    total_verificaiton_cost = GetVerifCost(detail)
    total_arr_cost = GetArrCost(detail)
    pre_stat_cost = total_arr_cost + total_verificaiton_cost + total_deliver_cost + total_holding_cost
    pre_single_cost = pre_stat_cost / total_demand if total_demand > 0 else 0.0
    total_turnover = detail['TURNOVER'].sum()

    # 同比环比计算（历史值为空或0时结果为0）
    cost_tr = 0.0
    if cost_last_month and cost_last_month != 0:
        cost_tr = (pre_stat_cost - cost_last_month) / cost_last_month * 100

    cost_yoy = 0.0
    if cost_last_year and cost_last_year != 0:
        cost_yoy = (pre_stat_cost - cost_last_year) / cost_last_year * 100

    itt_tr = 0.0
    if itt_last_month and itt_last_month != 0:
        itt_tr = (total_turnover - itt_last_month) / itt_last_month * 100

    itt_yoy = 0.0
    if itt_last_year and itt_last_year != 0:
        itt_yoy = (total_turnover - itt_last_year) / itt_last_year * 100

    record = {
        'SCHEME_ID': int(scheme_no),
        'SCHEME_NO': scheme_no,
        'SCHEMENAME': scheme_no,
        'SCHEME_FOCUS': focus,
        'EXEC_YM': yearMonth,                     # 填充执行年月
        'PRE_STAT_COST': round(pre_stat_cost, 2),
        'PRE_SINGLE_COST': round(pre_single_cost, 4),
        'COST_YOY': round(cost_yoy, 2),
        'COST_TR': round(cost_tr, 2),
        'PRE_ITR': None,
        'ITR_YOY': None,
        'ITR_TR': None,
        'PRE_ITT': round(total_turnover, 4),
        'ITT_YOY': round(itt_yoy, 2),
        'ITT_TR': round(itt_tr, 2),
        'MADE_DATE': datetime.datetime.now(),
        'COM_INDEX': None,
        'SCHEME_DESC': f"自动生成-{scheme_no}",    # 修正变量名
        'APPR_DATE': None,
        'APPR_RSLT': '00',
        'APPR_REMARK': None,
        'APPROUSER': None,
        'APPRO_ORG': None,
    }
    return record


def GetGlobalSchemeITT(detail: pd.DataFrame, scheme_id: str, yearMonth: str) -> pd.DataFrame:
    """
    全局策略方案周转明细
    根据明细表聚合到 (ORG_NO, DEV_CLS, DEV_CATEG) 粒度，
    生成 ADAM_GLOB_STRATEGY_SCHEME_ITT 表的记录，并计算周转次数的同比/环比。

    参数:
        detail: PrepareDetail 返回的 DataFrame，需包含字段:
            ORG_NO, DEV_CLS, DEV_CATEG, I0, I_END, TURNOVER
        scheme_id: 方案标识（字符串或整数）
        yearMonth: 执行年月，如 '202605'

    返回:
        DataFrame 包含周转明细表所需字段
    """
    from backend.api.data_api.fetch_data import (
        query_adam_glob_strategy_scheme_by_month,
        query_adam_glob_strategy_scheme_itt_by_schemeid
    )

    year = int(yearMonth[:4])
    month = int(yearMonth[4:])
    if month == 1:
        last_month = f"{year-1}12"
    else:
        last_month = f"{year}{month-1:02d}"
    last_year = f"{year-1}{month:02d}"

    # 获取上月和去年同月的全局策略方案记录
    df_last_month = query_adam_glob_strategy_scheme_by_month(last_month)
    df_last_year = query_adam_glob_strategy_scheme_by_month(last_year)

    # 获取历史方案的 SCHEME_ID
    scheme_id_last_month = int(df_last_month.iloc[0]['SCHEME_ID'])
    scheme_id_last_year = int(df_last_year.iloc[0]['SCHEME_ID'])

    # 获取历史周转明细表
    itt_last_month_df = query_adam_glob_strategy_scheme_itt_by_schemeid(scheme_id_last_month)
    itt_last_year_df = query_adam_glob_strategy_scheme_itt_by_schemeid(scheme_id_last_year)

    # 1. 按管理单位和设备分类/类别聚合当前明细
    grouped = detail.groupby(['ORG_NO', 'DEV_CLS', 'DEV_CATEG'], as_index=False).agg(
        START_STOCK_NUM=('I0', 'sum'),
        END_STOCK_NUM=('I_END', 'sum'),
        PRE_ITT=('TURNOVER', 'sum')
    )

    # 2. 合并历史数据（使用左连接）
    # 重命名历史表中的 PRE_ITT 列
    if not itt_last_month_df.empty:
        itt_last_month_df = itt_last_month_df.rename(columns={'PRE_ITT': 'PRE_ITT_LAST_MONTH'})
        grouped = grouped.merge(
            itt_last_month_df[['ORG_NO', 'DEV_CLS', 'DEV_CATEG', 'PRE_ITT_LAST_MONTH']],
            on=['ORG_NO', 'DEV_CLS', 'DEV_CATEG'],
            how='left'
        )
    else:
        grouped['PRE_ITT_LAST_MONTH'] = 0.0

    if not itt_last_year_df.empty:
        itt_last_year_df = itt_last_year_df.rename(columns={'PRE_ITT': 'PRE_ITT_LAST_YEAR'})
        grouped = grouped.merge(
            itt_last_year_df[['ORG_NO', 'DEV_CLS', 'DEV_CATEG', 'PRE_ITT_LAST_YEAR']],
            on=['ORG_NO', 'DEV_CLS', 'DEV_CATEG'],
            how='left'
        )
    else:
        grouped['PRE_ITT_LAST_YEAR'] = 0.0

    # 填充缺失历史值为 0
    grouped['PRE_ITT_LAST_MONTH'] = grouped['PRE_ITT_LAST_MONTH'].fillna(0)
    grouped['PRE_ITT_LAST_YEAR'] = grouped['PRE_ITT_LAST_YEAR'].fillna(0)

    # 3. 计算同比环比（避免除零）
    def calc_rate(current, history):
        # 历史值为0时返回0.0
        value = ((current - history) / history ).where(history != 0, 0.0)
        return round(value,2)

    grouped['ITT_TR'] = calc_rate(grouped['PRE_ITT'], grouped['PRE_ITT_LAST_MONTH'])
    grouped['ITT_YOY'] = calc_rate(grouped['PRE_ITT'], grouped['PRE_ITT_LAST_YEAR'])

    # 4. 生成主键
    base_ts = int(time.time() * 1_000_000)
    grouped['ITT_DET_ID'] = [base_ts + i for i in range(len(grouped))]

    # 5. 添加固定字段
    now = datetime.datetime.now()
    grouped['SCHEME_ID'] = int(scheme_id) if isinstance(scheme_id, str) else scheme_id
    grouped['MADE_DATE'] = now
    grouped['UPDATE_DATE'] = now

    # 6. 暂不计算的字段置为 None
    none_fields = ['PRE_ITR', 'ITR_YOY', 'ITR_TR', 'INCUR_ITR', 'INCUR_ITT']
    for field in none_fields:
        grouped[field] = None

    # 7. 按目标表字段顺序返回
    columns = [
        'ITT_DET_ID', 'SCHEME_ID', 'ORG_NO', 'START_STOCK_NUM', 'END_STOCK_NUM',
        'DEV_CLS', 'DEV_CATEG', 'PRE_ITR', 'ITR_YOY', 'ITR_TR', 'INCUR_ITR',
        'PRE_ITT', 'ITT_YOY', 'ITT_TR', 'INCUR_ITT', 'MADE_DATE', 'UPDATE_DATE'
    ]
    return grouped[columns]
    