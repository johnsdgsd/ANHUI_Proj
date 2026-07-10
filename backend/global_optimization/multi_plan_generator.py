"""
多套计划补库数量生成器
用于生成多套不同策略的补库计划
"""
import logging

import pandas as pd
import numpy as np
import datetime
from backend.global_optimization.logger import logger
from backend.inventory_optimization.GaOptimization import GenerateMonthlyThresholdAndOrderGA
import time

def GenerateMutiOrderScheme(yearMonth:str):
    '''
    每月一日触发，输入的日期是当月1日的日期
    例如:'202605
    '''
    from backend.api.data_api.fetch_data import (
        query_adam_org_stock_sample_estimated,
        query_adam_pre_range_info,
        insert_into_adam_glob_strategy_scheme,
        batch_insert_adam_glob_strategy_scheme_cost,
        insert_into_adam_glob_strategy_scheme_itt,
        batch_insert_adam_glob_strategy_scheme_itt,
        insert_into_adam_glob_strategy_scheme_lps,
        batch_insert_adam_glob_strategy_scheme_lps,
        deleteScheme)

    deleteScheme(yearMonth)
    year = yearMonth[:4]
    month = yearMonth[4:6]
    epsilons = [0.99,0.995,0.999]
    monthly_holding_rate = 0.01

    init_stock = query_adam_org_stock_sample_estimated(yearMonth)
    logger.info(f'推算月初库存成功，数据量{len(init_stock)}')
    item_cost = query_adam_pre_range_info()


    OrderSchemes = {}
    ThresholdSchemes = {}
    GlobalSchemeItems = {}
    GlobalSchemeITTs = {}
    GlobalSchemeLPS = {}
    GlobalSchemeCost = {}
    tag = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
    tag = int(tag)

    tag_epsilon_map = {}  # 记录 tag -> epsilon

    for idx, e in enumerate(epsilons, 1):
        logger.info(f'========== 方案{idx}/3 开始 (ε={e}, tag={tag}) ==========')

        Threshold, Order,Demand_Pre = GenerateMonthlyThresholdAndOrderGA(
            year=year,
            month=month,
            init_stock=None,
            tag=tag,
            alpha=e
        )
        tag_epsilon_map[tag] = e
        OrderSchemes[tag] = Order
        ThresholdSchemes[tag] = Threshold
        detail = PrepareDetail(Order,Threshold,init_stock,item_cost,Demand_Pre,monthly_holding_rate = monthly_holding_rate)
        detail = GetRunDurDetail(detail)
        logger.info('准备计算到货量和检定量')
        detail, total_central_avg_inv, total_central_unqua_avg_inv, total_central_unqua_end, total_central_qua_end, central_inv_by_cat = PrepareArrAndVerifQty(detail, yearMonth)
        logger.info('准备开始计算全局主表明细')
        global_scheme_item,detail = GetGlobalSchemeItem(detail,tag,yearMonth,total_central_avg_inv,total_central_unqua_avg_inv,total_central_unqua_end,total_central_qua_end)
        logger.info(f"[方案{tag}] 周转次数 PRE_ITT={global_scheme_item.iloc[0]['PRE_ITT']:.4f}, "
                    f"总成本 PRE_STAT_COST={global_scheme_item.iloc[0]['PRE_STAT_COST']:.2f}")
        logger.info('准备计算周转明细')
        global_scheme_itt = GetGlobalSchemeITT(detail, tag, yearMonth, central_inv_by_cat)
        logger.info('准备计算明细汇总')
        global_shceme_lps = GetGlobalSchemeLPS(detail,tag)
        logger.info('准备计算成本明细')
        global_scheme_cost = GetGlobalSchemeCost(detail,tag,yearMonth)

        global_scheme_cost['PRE_SINGLE_COST'] = global_scheme_cost['PRE_STAT_COST'].div(
            global_shceme_lps['PRE_STAT_NUM'], fill_value=0).replace([float('inf'), -float('inf')], 0)

        global_scheme_item['PRE_STAT_COST'] = global_scheme_item['PRE_STAT_COST'].astype(float).round(2)
        GlobalSchemeItems[tag] = global_scheme_item
        GlobalSchemeCost[tag] = global_scheme_cost
        GlobalSchemeITTs[tag] = global_scheme_itt
        GlobalSchemeLPS[tag] = global_shceme_lps

        logger.info(f'========== 方案{idx}/3 完成 (ε={e}, tag={tag}) ==========')
        tag +=1

    GlobalSchemeItems = determine_scheme_focus(GlobalSchemeItems)

    # 侧重分配完成后，统一缩容 PRE_STAT_COST 入库（NUMBER(10,2) 上限 99999999.99）
    NUM10_2_MAX = 99999999.99
    for tag in GlobalSchemeItems:
        cost = GlobalSchemeItems[tag].iloc[0]['PRE_STAT_COST']
        while cost > NUM10_2_MAX:
            cost = cost / 10
        GlobalSchemeItems[tag].loc[GlobalSchemeItems[tag].index[0], 'PRE_STAT_COST'] = round(cost, 2)

    from backend.api.data_api.fetch_data import query_pk_next

    new_tag_epsilon_map = {}
    for tag in GlobalSchemeItems:
        Item = GlobalSchemeItems[tag]
        Cost = GlobalSchemeCost[tag]
        Itt = GlobalSchemeITTs[tag]
        Lps = GlobalSchemeLPS[tag]

        new_id = int(query_pk_next("SEQ_ADAM_GLOB_STRATEGY_SCHEME", 1)[0])
        new_tag_epsilon_map[new_id] = tag_epsilon_map[tag]
        Item['SCHEME_ID'] = new_id
        Itt['SCHEME_ID'] = new_id
        Itt['ITT_DET_ID'] = [int(x) for x in query_pk_next("SEQ_ADAM_GLOB_STRATEGY_SCHEME_ITT", len(Itt))]
        Lps['SCHEME_ID'] = new_id
        Lps['ITT_DET_ID'] = [int(x) for x in query_pk_next("SEQ_ADAM_GLOB_STRATEGY_SCHEME_LPS", len(Lps))]
        Cost['SCHEME_ID'] = new_id
        Cost['COST_DET_ID'] = [int(x) for x in query_pk_next("SEQ_ADAM_GLOB_STRATEGY_SCHEME_COST", len(Cost))]

    # 保存 epsilon 映射供下游算法使用（使用替换后的标准ID）
    from backend.config.scheme_config import save_scheme_epsilons
    save_scheme_epsilons(yearMonth, new_tag_epsilon_map)

    # 统一插入数据库
    for tag in GlobalSchemeItems:
        insert_into_adam_glob_strategy_scheme(GlobalSchemeItems[tag])
        batch_insert_adam_glob_strategy_scheme_lps(GlobalSchemeLPS[tag])
        # insert_into_adam_glob_strategy_scheme_lps(GlobalSchemeLPS[tag])
        batch_insert_adam_glob_strategy_scheme_cost(GlobalSchemeCost[tag])
        # insert_into_adam_glob_strategy_scheme_cost(GlobalSchemeCost[tag])
        batch_insert_adam_glob_strategy_scheme_itt(GlobalSchemeITTs[tag])
        # insert_into_adam_glob_strategy_scheme_itt(GlobalSchemeITTs[tag])
    
    return [OrderSchemes,ThresholdSchemes,GlobalSchemeItems,GlobalSchemeITTs,GlobalSchemeLPS,GlobalSchemeCost]


def PrepareDetail(Order: pd.DataFrame, Threshold: pd.DataFrame, init_stock: pd.DataFrame,
                  price_df: pd.DataFrame, PreNum:pd.DataFrame,monthly_holding_rate: float = 0.01) -> pd.DataFrame:
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
        PreNum: 需求预测数量
        monthly_holding_rate: 月持有成本率，默认0.01 (即1%每月)
    
    返回:
        DataFrame 包含列: ORG_NO, DEV_CODE,DEV_CLS,DEV_CATEG, DEMAND, I0, I_END, AVG_INV, TURNOVER,
                         HOLDING_COST, SHORTAGE_COST,UNIT_PRICE,PRE_NUM
    """
    # 1. 汇总补货计划得到月需求（补货总量 = 需求总量）
    Order = Order[['REC_ORG_NO','DEV_CODE','PLAN_IAS_NUM','DEV_CLS','DEV_CATEG']]

    demand_agg = Order.groupby(['REC_ORG_NO', 'DEV_CODE','DEV_CLS','DEV_CATEG'], as_index=False)['PLAN_IAS_NUM'].sum()
    demand_agg.rename(columns={'REC_ORG_NO': 'ORG_NO', 'PLAN_IAS_NUM': 'DEMAND'}, inplace=True)

    # 2. 月初库存（直接使用传入的 init_stock，假定已为当前月份）
    init_f = init_stock[['ORG_NO', 'DEV_CODE', 'STOCK_NUM']].copy()
    init_f.rename(columns={'STOCK_NUM': 'I0'}, inplace=True)

    price_f = price_df[['DEV_CODE', 'TAX_UP']].copy()
    price_f.rename(columns={'TAX_UP': 'UNIT_PRICE'}, inplace=True)
    # 去重：同一 DEV_CODE 保留第一条
    price_f = price_f.drop_duplicates(subset=['DEV_CODE'], keep='first')

    # 3. 左连接（以需求表为准）
    logger.info(f"[PrepareDetail] demand_agg 行数: {len(demand_agg)}")
    detail = demand_agg.merge(init_f, on=['ORG_NO', 'DEV_CODE'], how='left')
    logger.info(f"[PrepareDetail] merge I0 后行数: {len(detail)}")
    detail = detail.merge(price_f, on=['DEV_CODE'], how='left')
    logger.info(f"[PrepareDetail] merge 单价后行数: {len(detail)}, price_f 行数: {len(price_f)}, 唯一 DEV_CODE: {price_f['DEV_CODE'].nunique()}")
    # 4. 填充缺失值
    detail['I0'] = detail['I0'].fillna(0)
    logger.info(f"[PrepareDetail] 月初库存 I0 总和: {detail['I0'].sum():.2f}")
    detail['UNIT_PRICE'] = detail['UNIT_PRICE'].fillna(0)

    # 增加基准库存
    if Threshold is not None and not Threshold.empty:
        th_sub = Threshold[['ORG_NO', 'DEV_CODE', 'BASE_LIMIT']].copy()
        detail = detail.merge(th_sub, on=['ORG_NO', 'DEV_CODE'], how='left')
        detail['BASE_LIMIT'] = detail['BASE_LIMIT'].fillna(0)
    else:
        detail['BASE_LIMIT'] = 0

    # 增加需求预测结果
    if PreNum is not None and not PreNum.empty:
        pre_num_sub = PreNum[['ORG_NO', 'DEV_CODE', 'PRE_NUM']].copy()
        detail = detail.merge(pre_num_sub, on=['ORG_NO', 'DEV_CODE'], how='left')
        detail['PRE_NUM'] = detail['PRE_NUM'].fillna(0)
    else:
        detail['PRE_NUM'] = 0

    # 5. 月末库存：月初 > 基准时用月初减，否则用基准减
    detail['I_END'] = detail.apply(
        lambda r: max(r['I0'] - r['PRE_NUM'], 0) if r['I0'] > r['BASE_LIMIT']
                  else max(r['BASE_LIMIT'] - r['PRE_NUM'], 0),
        axis=1
    )
    # 6. 日均库存
    detail['AVG_INV'] = (detail['I0'] + detail['I_END']) / 2.0

    # 月周转次数
    detail['TURNOVER'] = detail.apply(
        lambda row: row['PRE_NUM'] / row['AVG_INV'] if row['AVG_INV'] > 0 else 0,
        axis=1
    )
    detail['TURNOVER'] = detail['TURNOVER'].round(8)
    # 9. 持有成本由 GetStorageCost 计算，此处预置为0
    detail['HOLDING_COST'] = 0.0
    # 10. 缺货成本（暂设为0，可根据业务扩展）
    detail['SHORTAGE_COST'] = 0.0


    return detail


def GetGlobalSchemeItem(detail: pd.DataFrame, scheme_no: str, yearMonth: str, total_central_avg_inv: float = 0.0, total_central_unqua_avg_inv: float = 0.0, total_central_unqua_end: float = 0.0, total_central_qua_end: float = 0.0):
    '''
    获得一条全局方案
    '''
    from backend.api.data_api.fetch_data import query_adam_glob_strategy_scheme_by_month

    year = int(yearMonth[:4])
    month = int(yearMonth[4:])
    if month == 1:
        last_month = f"{year-1}12"
    else:
        last_month = f"{year}{month-1:02d}"
    last_year = f"{year-1}{month:02d}"
    logger.info('读取历史策略主表')
    # 获取历史数据（DataFrame，可能为空）
    df_last_month = query_adam_glob_strategy_scheme_by_month(last_month)
    df_last_year = query_adam_glob_strategy_scheme_by_month(last_year)

    # 提取历史值（取第一条记录的对应字段，若无数据则 None）
    def get_value(df, field):
        if df is not None and not df.empty:
            return df.iloc[0].get(field)
        return None

    cost_last_month = get_value(df_last_month, 'PRE_SINGLE_COST')
    cost_last_year = get_value(df_last_year, 'PRE_SINGLE_COST')

    itt_last_month = get_value(df_last_month, 'PRE_ITT')
    itt_last_year = get_value(df_last_year, 'PRE_ITT')

    itr_last_month = get_value(df_last_month, 'PRE_ITR')
    itr_last_year = get_value(df_last_year, 'PRE_ITR')


    # 计算当前指标
    focus = '03'
    total_demand = detail['DEMAND'].sum()
    total_inv = detail['AVG_INV'].sum()

    logger.info('计算仓储成本')
    total_holding_cost, detail = GetStorageCost(detail)
    logger.info('计算配送成本')
    total_deliver_cost,detail = GetDeliverCost(detail)
    logger.info('计算检定成本')
    total_verificaiton_cost,detail = GetVerifCost(detail)
    logger.info('计算采购成本')
    total_procure_cost, detail = GetProcureCost(detail)
    logger.info('计算到货成本')
    total_arr_cost, detail = GetArrCost(detail)
    logger.info('计算同比环比')
    pre_stat_cost = total_procure_cost + total_arr_cost + total_verificaiton_cost + total_deliver_cost + total_holding_cost
    logger.info(f'单只成本分母 - 总需求量 total_demand: {total_demand}')
    pre_single_cost = pre_stat_cost / total_demand if total_demand > 0 else 0.0
    total_pre_num = detail['PRE_NUM'].sum()
    total_avg_inv = detail['AVG_INV'].sum() + total_central_avg_inv + total_central_unqua_avg_inv
    total_turnover = total_pre_num / total_avg_inv if total_avg_inv > 0 else 0.0
    logger.info(f'周转次数计算: total_pre_num={total_pre_num:.2f}, '
                f'detail.AVG_INV.sum={detail["AVG_INV"].sum():.2f}, '
                f'total_central_avg_inv={total_central_avg_inv:.2f}, '
                f'total_central_unqua_avg_inv={total_central_unqua_avg_inv:.2f}, '
                f'total_avg_inv={total_avg_inv:.2f}, '
                f'total_turnover={total_turnover:.8f}')
    cur_itr = round((detail['I_END'].sum() + total_central_qua_end + total_central_unqua_end) / detail['RUNNING_NUM'].sum(), 4) * 100 if detail['RUNNING_NUM'].sum() > 0 else 0.0
    # 成本周转次数同比环比计算（历史值为空或0时结果为0）
    cost_tr = 0.0
    if cost_last_month and cost_last_month != 0:
        cost_tr = (pre_single_cost - cost_last_month) / cost_last_month * 100
        cost_tr = round(cost_tr,2)

    cost_yoy = 0.0
    if cost_last_year and cost_last_year != 0:
        cost_yoy = (pre_single_cost - cost_last_year) / cost_last_year * 100
        cost_yoy = round(cost_yoy,2)

    itt_tr = 0.0
    if itt_last_month and itt_last_month != 0:
        itt_tr = (total_turnover - itt_last_month ) / itt_last_month * 100
        itt_tr = round(itt_tr,2)

    itt_yoy = 0.0
    if itt_last_year and itt_last_year != 0:
        itt_yoy = (total_turnover - itt_last_year) / itt_last_year * 100
        itt_yoy = round(itt_yoy,2)
    # 计算库存运行比
    itr_tr = 0.0
    if itr_last_month and itr_last_month != 0:
        itr_tr = (cur_itr - itr_last_month) / itr_last_month * 100
        itr_tr = round(itr_tr,2)

    itr_yoy = 0.0
    if itr_last_year and itr_last_year != 0:
        itr_yoy = (cur_itr - itr_last_year) / itr_last_year * 100
        itr_yoy = round(itr_yoy,2)

    # ------------------- 新增：自动缩容（除以10/100...直到在长度范围内） -------------------
    def safe_num_scale(x, max_value):
        """
        自动缩容：数字超过 max_value 就一直除以10，直到 <= max_value
        保留 2 位小数
        """
        if x is None:
            return None
        x = float(x)
        sign = -1 if x < 0 else 1
        abs_x = abs(x)

        # 只要超过最大值，就一直除以10
        while abs_x > max_value:
            abs_x = abs_x / 10

        x = sign * abs_x
        return round(x, 2)

    # 定义每个类型的最大值
    NUM5_2_MAX = 999.99  # NUMBER(5,2) 最大
    NUM10_2_MAX = 99999999.99  # NUMBER(10,2) 最大

    # 自动缩容（超了就÷10 ÷100...）
    # 注意：PRE_STAT_COST 不在此处缩容，保留原始值用于方案间成本比较
    # 缩容在 determine_scheme_focus 之后统一处理
    pre_single_cost = safe_num_scale(pre_single_cost, NUM10_2_MAX)

    cost_yoy = safe_num_scale(cost_yoy, NUM5_2_MAX)
    cost_tr = safe_num_scale(cost_tr, NUM5_2_MAX)
    cur_itr = safe_num_scale(cur_itr, NUM5_2_MAX)
    itr_yoy = safe_num_scale(itr_yoy, NUM5_2_MAX)
    itr_tr = safe_num_scale(itr_tr, NUM5_2_MAX)
    total_turnover = safe_num_scale(total_turnover, NUM5_2_MAX)
    itt_yoy = safe_num_scale(itt_yoy, NUM5_2_MAX)
    itt_tr = safe_num_scale(itt_tr, NUM5_2_MAX)

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
        'PRE_ITR': round(cur_itr,2),
        'ITR_YOY': itr_yoy,
        'ITR_TR': itr_tr,
        'PRE_ITT': round(total_turnover, 4),
        'ITT_YOY': round(itt_yoy, 2),
        'ITT_TR': round(itt_tr, 2),
        'MADE_DATE': datetime.datetime.now().strftime('%Y-%m-%d'),
        'COM_INDEX': None,
        'SCHEME_DESC': None,    # 修正变量名
        'APPR_DATE': None,
        'APPR_RSLT': '00',
        'APPR_REMARK': None,
        'APPROUSER': None,
        'APPRO_ORG': None,
    }
    logger.info('计算主表明细成功')
    return pd.DataFrame([record]),detail

def GetGlobalSchemeITT(detail: pd.DataFrame, scheme_id: str, yearMonth: str, central_inv_by_cat: pd.DataFrame = None) -> pd.DataFrame:
    """
    全局策略方案周转明细
    根据明细表聚合到 (ORG_NO, DEV_CLS, DEV_CATEG) 粒度，
    生成 ADAM_GLOB_STRATEGY_SCHEME_ITT 表的记录，并计算周转次数的同比/环比。

    参数:
        detail: PrepareDetail 返回的 DataFrame，需包含字段:
            ORG_NO, DEV_CLS, DEV_CATEG, I0, I_END, TURNOVER
        scheme_id: 方案标识（字符串或整数），关联主表
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
    logger.info('计算周转明细，读取历史全局策略主表数据')
    df_last_month = query_adam_glob_strategy_scheme_by_month(last_month)
    df_last_year = query_adam_glob_strategy_scheme_by_month(last_year)

    # 获取历史方案的 SCHEME_ID
    logger.info('获取历史方案的 SCHEME_ID')
    try:

        scheme_id_last_month = int(df_last_month.iloc[0]['SCHEME_ID'])
        scheme_id_last_year = int(df_last_year.iloc[0]['SCHEME_ID'])

        # 获取历史周转明细表
        logger.info('获取历史周转明细表')
        itt_last_month_df = query_adam_glob_strategy_scheme_itt_by_schemeid(scheme_id_last_month)
        itt_last_year_df = query_adam_glob_strategy_scheme_itt_by_schemeid(scheme_id_last_year)
    except Exception as e:
        itt_last_month_df = pd.DataFrame()
        itt_last_year_df = pd.DataFrame()

    # 1. 按管理单位和设备分类/类别聚合当前明细
    grouped = detail.groupby(['ORG_NO', 'DEV_CLS', 'DEV_CATEG'], as_index=False).agg(
        START_STOCK_NUM=('I0', 'sum'),
        END_STOCK_NUM=('I_END', 'sum'),
        PRE_NUM_SUM=('PRE_NUM', 'sum'),
        AVG_INV_SUM=('AVG_INV', 'sum'),
        I_END_SUM=('I_END', 'sum'),
        RUNNING_SUM=('RUNNING_NUM', 'sum')
    )
    grouped['PRE_ITT'] = (grouped['PRE_NUM_SUM'] / grouped['AVG_INV_SUM']).fillna(0).round(4)
    grouped['PRE_ITR'] = (grouped['I_END_SUM'] / grouped['RUNNING_SUM'] * 100).replace([float('inf'), -float('inf')], 0).fillna(0)
    grouped.drop(columns=['PRE_NUM_SUM', 'AVG_INV_SUM', 'I_END_SUM', 'RUNNING_SUM'], inplace=True)

    # 2. 合并历史数据（使用左连接）
    # 重命名历史表中的 PRE_ITT 列
    logger.info('合并历史数据（使用左连接）')
    if not itt_last_month_df.empty:
        itt_last_month_df = itt_last_month_df.rename(columns={'PRE_ITT': 'PRE_ITT_LAST_MONTH','PRE_ITR':'PRE_ITR_LAST_MONTH'})
        grouped = grouped.merge(
            itt_last_month_df[['ORG_NO', 'DEV_CLS', 'DEV_CATEG', 'PRE_ITT_LAST_MONTH','PRE_ITR_LAST_MONTH']],
            on=['ORG_NO', 'DEV_CLS', 'DEV_CATEG'],
            how='left'
        )
    else:
        grouped['PRE_ITT_LAST_MONTH'] = 0.0
        grouped['PRE_ITR_LAST_MONTH'] = 0.0

    if not itt_last_year_df.empty:
        itt_last_year_df = itt_last_year_df.rename(columns={'PRE_ITT': 'PRE_ITT_LAST_YEAR','PRE_ITR':'PRE_ITR_LAST_YEAR'})
        grouped = grouped.merge(
            itt_last_year_df[['ORG_NO', 'DEV_CLS', 'DEV_CATEG', 'PRE_ITT_LAST_YEAR','PRE_ITR_LAST_YEAR']],
            on=['ORG_NO', 'DEV_CLS', 'DEV_CATEG'],
            how='left'
        )
    else:
        grouped['PRE_ITT_LAST_YEAR'] = 0.0
        grouped['PRE_ITR_LAST_YEAR'] = 0.0

    # 填充缺失历史值为 0
    grouped['PRE_ITT_LAST_MONTH'] = grouped['PRE_ITT_LAST_MONTH'].fillna(0)
    grouped['PRE_ITT_LAST_YEAR'] = grouped['PRE_ITT_LAST_YEAR'].fillna(0)
    grouped['PRE_ITR_LAST_MONTH'] = grouped['PRE_ITR_LAST_MONTH'].fillna(0)
    grouped['PRE_ITR_LAST_YEAR'] = grouped['PRE_ITR_LAST_YEAR'].fillna(0)
    # 3. 计算同比环比（避免除零，结果转为百分比）
    def calc_rate(current, history):
        value = ((current - history) / history ).where(history != 0, 0.0)
        return round(value * 100, 2)

    grouped['ITT_TR'] = calc_rate(grouped['PRE_ITT'], grouped['PRE_ITT_LAST_MONTH'])
    grouped['ITT_YOY'] = calc_rate(grouped['PRE_ITT'], grouped['PRE_ITT_LAST_YEAR'])

    grouped['ITR_TR'] = calc_rate(grouped['PRE_ITR'], grouped['PRE_ITR_LAST_MONTH'])
    grouped['ITR_YOY'] = calc_rate(grouped['PRE_ITR'], grouped['PRE_ITR_LAST_YEAR'])

    # 百分比字段自动缩容: NUMBER(5,2) max=999.99
    def scale_pct(col):
        col = col.copy()
        col = col.replace([float('inf'), -float('inf')], 0.0).fillna(0.0)
        while (col.abs() > 999.99).any():
            col = col.where(col.abs() <= 999.99, col / 10)
        return col.round(2)

    for col in ['PRE_ITR', 'ITR_YOY', 'ITR_TR', 'ITT_YOY', 'ITT_TR']:
        grouped[col] = scale_pct(grouped[col])

    # 4. 省中心行（ORG_NO=34101）
    central_rows = []
    if central_inv_by_cat is not None and not central_inv_by_cat.empty:
        for _, cr in central_inv_by_cat.iterrows():
            central_rows.append({
                'ORG_NO': '34101',
                'DEV_CLS': cr['DEV_CLS'],
                'DEV_CATEG': cr['DEV_CATEG'],
                'START_STOCK_NUM': cr['CENTRAL_START_TOTAL'],
                'END_STOCK_NUM': cr['CENTRAL_END_TOTAL'],
                'PRE_NUM_SUM': 0,
                'AVG_INV_SUM': 0,
                'I_END_SUM': 0,
                'RUNNING_SUM': 0,
                'PRE_ITT': 0.0,
                'PRE_ITR': 0.0,
                'PRE_ITT_LAST_MONTH': 0.0,
                'PRE_ITT_LAST_YEAR': 0.0,
                'PRE_ITR_LAST_MONTH': 0.0,
                'PRE_ITR_LAST_YEAR': 0.0,
                'ITT_TR': 0.0,
                'ITT_YOY': 0.0,
                'ITR_TR': 0.0,
                'ITR_YOY': 0.0,
                'INCUR_ITR': 0.0,
                'INCUR_ITT': 0.0,
            })
        logger.info(f'[周转明细] 添加省中心行 {len(central_rows)} 条')

    central_df = pd.DataFrame(central_rows)
    grouped = pd.concat([grouped, central_df], ignore_index=True)

    # 5. 生成主键
    base_ts = int(time.time() * 1000)  # 13位毫秒时间戳
    base_ts = int(str(base_ts)[:12])
    grouped['ITT_DET_ID'] = [base_ts + i for i in range(len(grouped))]

    # 5. 添加固定字段
    now = datetime.datetime.now().strftime('%Y-%m-%d')
    grouped['SCHEME_ID'] = int(scheme_id) if isinstance(scheme_id, str) else scheme_id
    grouped['MADE_DATE'] = now
    grouped['UPDATE_DATE'] = now

    # 6. 暂不计算的字段置为 0
    none_fields = ['INCUR_ITR', 'INCUR_ITT']
    for field in none_fields:
        grouped[field] = 0.0

    # 7. 按目标表字段顺序返回
    columns = [
        'ITT_DET_ID', 'SCHEME_ID', 'ORG_NO', 'START_STOCK_NUM', 'END_STOCK_NUM',
        'DEV_CLS', 'DEV_CATEG', 'PRE_ITR', 'ITR_YOY', 'ITR_TR', 'INCUR_ITR',
        'PRE_ITT', 'ITT_YOY', 'ITT_TR', 'INCUR_ITT', 'MADE_DATE', 'UPDATE_DATE'
    ]
    return grouped[columns]

def GetArrCost(detail:pd.DataFrame):
    """
    计算到货成本
    算法: 工时 × 时薪 × 人数(1人)
    - 工时 = 20天 × 8小时/天 = 160小时/月
    - 时薪 = 日薪 / 8（从配置表读取, LINK_TYPE='02', COST_TYPE='02', BASE_COST_TYPE='01'）
    - 按到货量占比分摊到各明细行
    """
    from backend.api.data_api.fetch_data import query_adam_single_cost_config_all

    # 读取配置表中的日薪
    cost_df = query_adam_single_cost_config_all()
    mask = (cost_df['LINK_TYPE'] == '02') & (cost_df['COST_TYPE'] == '02') & (cost_df['BASE_COST_TYPE'] == '01')
    matched = cost_df[mask]

    if matched.empty:
        daily_wage = 200  # 默认日薪
    else:
        daily_wage = float(matched.iloc[0]['BASE_COST_VALUE'])

    hourly_wage = daily_wage / 8.0       # 时薪
    work_hours = 20 * 8                   # 工时 = 160小时
    people_count = 1                      # 人数

    # 到货总成本
    total_arr_cost = work_hours * hourly_wage * people_count
    logger.info(f'[到货成本] 日薪={daily_wage}, 总成本 total_arr_cost={total_arr_cost:.2f}')

    # 按到货量占比分摊到每行（最大余数法，确保汇总精确等于 total_arr_cost）
    total_arr_qty = detail['ARR_QTY'].sum()
    if total_arr_qty > 0:
        raw = detail['ARR_QTY'] / total_arr_qty * total_arr_cost
        total_cents = int(round(total_arr_cost * 100))
        floor_cents = (raw * 100).astype(int)
        detail['ARR_COST'] = floor_cents / 100.0

        missing = total_cents - floor_cents.sum()
        if missing > 0:
            remainders = raw * 100 - floor_cents
            top_idx = remainders.argsort().iloc[-missing:].index
            detail.loc[top_idx, 'ARR_COST'] += 0.01
    else:
        detail['ARR_COST'] = 0.0

    logger.info(f'[到货成本] 分摊后 detail 汇总={detail["ARR_COST"].sum():.2f}, 行数={len(detail)}')

    return total_arr_cost, detail


def GetProcureCost(detail: pd.DataFrame):
    """
    计算采购成本
    算法: 补货量 × 含税单价
    """
    detail['PROCURE_COST'] = detail['DEMAND'] * detail['UNIT_PRICE']
    total_procure_cost = detail['PROCURE_COST'].sum()
    return total_procure_cost, detail


def GetStorageCost(detail: pd.DataFrame):
    """
    计算仓储成本 = 管理费 + 资金占用

    1. 管理费: 二级市(ORG_NO 长度=5) 41万/年 ÷ 12月 = 34,166.67元/月
       其他单位 0, 按各市内部 AVG_INV 占比分摊到行
    2. 资金占用: UNIT_PRICE × 年利率 × AVG_INV × 30
       年利率从配置表读取: LINK_TYPE='04', COST_TYPE='04', BASE_COST_TYPE='13'
    """
    from backend.api.data_api.fetch_data import query_adam_single_cost_config_all

    # 读取年利率配置
    cost_df = query_adam_single_cost_config_all()
    mask_rate = (
        (cost_df['LINK_TYPE'] == '04') &
        (cost_df['COST_TYPE'] == '04') &
        (cost_df['BASE_COST_TYPE'] == '13')
    )
    matched_rate = cost_df[mask_rate]

    if matched_rate.empty:
        annual_rate = 0.05  # 默认年利率 5%
    else:
        try:
            annual_rate = float(matched_rate.iloc[0]['BASE_COST_VALUE'])
        except Exception:
            annual_rate = 0.05

    # 二级市管理费: 410,000 / 12 = 34,166.67 元/月
    MONTHLY_MGMT_FEE = 410000.0 / 12.0

    # 标记二级市 (ORG_NO 长度=5，排除省中心34101)
    detail['IS_CITY'] = (detail['ORG_NO'].astype(str).str.len() == 5) & (detail['ORG_NO'].astype(str) != '34101')

    # 资金占用: 设备单价 × (年利率/365) × 日均库存 × 30天
    detail['CAPITAL_COST'] = (
        detail['UNIT_PRICE'] * (annual_rate / 365.0) * detail['AVG_INV'] * 30
    ).round(2)

    # 管理费分摊: 每个二级市内部按 AVG_INV 占比分摊月度管理费
    detail['MGMT_COST'] = 0.0
    city_mask = detail['IS_CITY']
    if city_mask.any():
        city_groups = detail.loc[city_mask].groupby('ORG_NO')
        for org_no, group in city_groups:
            total_avg_inv = group['AVG_INV'].sum()
            if total_avg_inv > 0:
                detail.loc[group.index, 'MGMT_COST'] = (
                    group['AVG_INV'] / total_avg_inv * MONTHLY_MGMT_FEE
                ).round(2)

    # 仓储总成本 = 资金占用 + 管理费
    detail['HOLDING_COST'] = detail['CAPITAL_COST'] + detail['MGMT_COST']
    total_storage_cost = detail['HOLDING_COST'].sum()

    return total_storage_cost, detail


def PreparaVerifData(detail:pd.DataFrame):
    '''
    获得不同设备码每小时检定时长
    '''
    from backend.api.data_api.fetch_data import (
        query_adam_spec_code_config,
        query_adam_veri_config_all)
    spec_df = query_adam_spec_code_config()  # 需包含 DEV_CODE, DEV_CATEG, AUTO_DUR
    need_cols = []
    if 'DEV_CATEG' not in detail.columns:
        need_cols.append('DEV_CATEG')
    if 'AUTO_DUR' not in detail.columns:
        need_cols.append('AUTO_DUR')
    if need_cols:
        detail = detail.merge(
            spec_df[['DEV_CODE'] + need_cols],
            on='DEV_CODE',
            how='left'
        )
    
    veri_df = query_adam_veri_config_all()
    auto_veri = veri_df[veri_df['VERI_TYPE'] == '02'].copy()

    # 3. 处理同一 DEV_CATEG 多条自动检定线的情况（取第一条并警告）
    if auto_veri.duplicated(subset=['DEV_CATEG']).any():
        auto_veri = auto_veri.drop_duplicates(subset=['DEV_CATEG'], keep='first')

    # 4. 计算并发能力
    auto_veri['CONCURRENT'] = (
        auto_veri['VDRILINE_NUM'] * auto_veri['POSI_NUM'] * auto_veri['POSI_CHECK_NUM']
    )

    # 5. 保留必要列并重命名
    auto_veri = auto_veri[['DEV_CATEG', 'VDRILINE_NUM', 'POSI_NUM', 'POSI_CHECK_NUM', 'CONCURRENT']]
    auto_veri = auto_veri.rename(columns={'VDRILINE_NUM': 'VERI_LINE_NUM'})

    # 6. 左连接，将检定线信息添加到 detail
    # detail['DEV_CATEG'] = '01_01' # 需要去掉
    detail = detail.merge(auto_veri, on='DEV_CATEG', how='left')

    # 7. 计算每小时检定数量（避免除零或缺失）
    detail['HOURLY_VERI_NUM'] = 0.0
    mask = (detail['AUTO_DUR'].notna()) & (detail['AUTO_DUR'] > 0) & (detail['CONCURRENT'].notna())
    if mask.any():
        detail.loc[mask, 'HOURLY_VERI_NUM'] = (
            detail.loc[mask, 'CONCURRENT'] * (60 / detail.loc[mask, 'AUTO_DUR'])
        )

    # 8. 填充缺失值，并将整数列转为合适类型
    detail['VERI_LINE_NUM'] = detail['VERI_LINE_NUM'].fillna(0).astype(int)
    detail['CONCURRENT'] = detail['CONCURRENT'].fillna(0).astype(int)
    detail['HOURLY_VERI_NUM'] = detail['HOURLY_VERI_NUM'].fillna(0).astype(int)
    return detail

def GetVerifCost(detail:pd.DataFrame):

    from backend.api.data_api.fetch_data import query_adam_single_cost_config_all

    detail = PreparaVerifData(detail)
    cost_df = query_adam_single_cost_config_all()

    # 筛选：环节类型=检定(03)，成本类型=人工(02)，基础数据类型=日薪(01)
    mask_labor = (cost_df['LINK_TYPE'] == '03') & (cost_df['COST_TYPE'] == '02') & (cost_df['BASE_COST_TYPE'] == '01')
    matched_labor = cost_df[mask_labor]

    if matched_labor.empty:
        daily_wage = 200
    else:
        try:
            daily_wage = float(matched_labor.iloc[0]['BASE_COST_VALUE'])
        except Exception:
            daily_wage = 200

    # 筛选维保费用: LINK_TYPE='03', COST_TYPE='03', BASE_COST_TYPE='03'维修费/'04'检测费/'05'年度检定量
    mask_maint = (
        (cost_df['LINK_TYPE'] == '03') &
        (cost_df['COST_TYPE'] == '03') &
        (cost_df['BASE_COST_TYPE'].isin(['03', '04', '05']))
    )
    maint_df = cost_df[mask_maint]

    repair_cost = 5000000.0   # BASE_COST_TYPE='03' 维修费用, 默认500万
    test_cost = 1000000.0     # BASE_COST_TYPE='04' 检测费用, 默认100万
    annual_volume = 4500000.0 # BASE_COST_TYPE='05' 年度检定量, 默认450万

    if not maint_df.empty:
        for _, mr in maint_df.iterrows():
            bct = str(mr['BASE_COST_TYPE'])
            val = float(mr['BASE_COST_VALUE'])
            if bct == '03':
                repair_cost = val
            elif bct == '04':
                test_cost = val
            elif bct == '05':
                annual_volume = max(val, 1.0)

    # 单只月均维保费用 = (维修费 + 检测费) / 年度检定量 / 12
    per_device_monthly_maint = (repair_cost + test_cost) / annual_volume / 12.0

    hourly_wage = daily_wage / 8.0   # 时薪，元/小时
    verif_costs = []
    total_cost = 0.0
    for _, row in detail.iterrows():
        verif_qty = row['VERIF_QTY']
        hourly_rate = row['HOURLY_VERI_NUM']

        # 人工成本
        if hourly_rate > 0:
            hours = verif_qty / hourly_rate
            labor_cost = hours * hourly_wage
        else:
            labor_cost = 0.0

        # 维保成本
        maint_cost = verif_qty * per_device_monthly_maint

        cost = round(labor_cost + maint_cost, 1)
        verif_costs.append(cost)
        total_cost += cost

    detail['VERIF_COST'] = verif_costs
    return total_cost, detail

def GetDeliverCost(detail:pd.DataFrame):

    from backend.api.data_api.fetch_data import query_adam_spec_code_config
    from backend.utils.GetPathDis import GetCenterToLocalDis
    # 获取装箱数量
    spec_df = query_adam_spec_code_config()
    spec_sub = spec_df[['DEV_CODE', 'PACK_BOX_NUM']].copy()
    detail = detail.merge(spec_sub, on='DEV_CODE', how='left')
    detail['PACK_BOX_NUM'] = detail['PACK_BOX_NUM'].fillna(4)
    detail['TOTAL_BOXS'] = np.ceil(detail['DEMAND'] / detail['PACK_BOX_NUM']).astype(int)
    detail.drop(columns=['PACK_BOX_NUM'], inplace=True)

    # 获取中心到各地市的距离（返回字典 ORG_NO -> distance_km）
    dis_dict = GetCenterToLocalDis()
    # 将距离字典转为 DataFrame
    dis_df = pd.DataFrame(list(dis_dict.items()), columns=['ORG_NO', 'DIS'])
    # 合并到 detail
    detail = detail.merge(dis_df, on='ORG_NO', how='left')
    # 如果没有匹配到距离，设为0（或报错）
    detail['DIS'] = detail['DIS'].fillna(0)
    
    # 计算配送成本
    detail['DELIVER_COST'] = detail['DIS'] * detail['TOTAL_BOXS'] * 0.0695
    detail['DELIVER_COST'] = detail['DELIVER_COST'].round(4) 
    total_deliver_cost = detail['DELIVER_COST'].sum()

    return total_deliver_cost,detail


def round_arrival_by_batch(target_req: int, order_nums: list, dev_code: str = '') -> int:
    """
    按采购订单批次规格取整到货量。
    仿照 Scheduling/main.py 贪心算法，去掉箱数限制。

    参数:
        target_req: 原始到货需求量（只）
        order_nums: 该设备码的采购批次规格列表，如 [3000, 2000, 1000]
        dev_code: 设备码（仅用于日志）

    返回:
        取整后的到货总量（只）
    """
    if not order_nums:
        logger.info(f'[批次取整] dev={dev_code}, 原始到货需求={target_req}, 无采购批次规格，不取整')
        return target_req

    unique_orders = sorted([int(x) for x in set(order_nums) if int(x) > 0], reverse=True)
    if not unique_orders:
        logger.info(f'[批次取整] dev={dev_code}, 原始到货需求={target_req}, 无有效批次规格，不取整')
        return target_req

    M = unique_orders[-1]  # 最小批次

    actual_qty = 0
    remaining = target_req
    detail_parts = []

    for order_qty in unique_orders:
        if remaining <= 0:
            break
        num_full_batches = remaining // order_qty
        if num_full_batches > 0:
            actual_qty += num_full_batches * order_qty
            remaining -= num_full_batches * order_qty
            detail_parts.append(f'{num_full_batches}×{order_qty}')

    # 尾批兜底：剩余 > 0 时强制拉高到最小批次
    if remaining > 0:
        detail_parts.append(f'尾{remaining}→{M}')
        actual_qty += M

    logger.info(f'[批次取整] dev={dev_code}, 原始到货需求={target_req}, '
                f'批次拆分: {" + ".join(detail_parts) if detail_parts else "无需拆分"}, 取整后={actual_qty}')
    return actual_qty


def PrepareArrAndVerifQty(detail: pd.DataFrame, yearMonth: str) -> pd.DataFrame:
    """
    按 Scheduling 逻辑计算各单位各设备码的到货量和检定量。

    步骤:
      1. 读取省级库存: 得到每个 DEV_CODE 的合格品/不合格品/已检定完工量
      2. 计算省级总量: 对每个 DEV_CODE, 按 Scheduling 公式算出总到货量/总检定量
      3. 拆分到行: 按各单位需求量占该 DEV_CODE 总需求的比例, 拆分到 (ORG_NO, DEV_CODE)
    """
    import math
    from datetime import datetime
    from dateutil.relativedelta import relativedelta

    from backend.api.data_api.fetch_data import (
        query_adam_qua_stock_sample_by_year_month,
        query_adam_realtime_pend_stock,
        query_adam_realtime_qua_stock,
        query_adam_future_arrivals,
        query_adam_future_detections,
        query_adam_future_deliveries,
        query_adam_completed_inspections,
        query_unused_pur_orders,
    )

    def _clean_dev_code(df):
        """清洗 DEV_CODE: 统一列名大写, DEV_CODE_NO → DEV_CODE, 转字符串去空格去 .0"""
        df = df.copy()
        df.columns = [c.upper() for c in df.columns]
        if 'DEV_CODE_NO' in df.columns and 'DEV_CODE' not in df.columns:
            df.rename(columns={'DEV_CODE_NO': 'DEV_CODE'}, inplace=True)
        if 'DEV_CODE' in df.columns:
            df['DEV_CODE'] = df['DEV_CODE'].astype(str).str.strip().str.replace(r'\.0$', '', regex=True)
        return df

    # ========================================================================
    #  步骤0: 查询采购订单批次规格，构建 DEV_CODE → [ORDER_NUMs] 映射
    # ========================================================================
    logger.info('[到货/检定量] 查询采购订单批次规格')
    raw_orders = query_unused_pur_orders()
    order_nums_map = {}
    if not raw_orders.empty:
        raw_orders = _clean_dev_code(raw_orders)
        if 'ORDER_NUM' in raw_orders.columns:
            raw_orders['ORDER_NUM'] = raw_orders['ORDER_NUM'].astype(float).astype(int)
            for dev, grp in raw_orders.groupby('DEV_CODE'):
                order_nums_map[dev] = grp['ORDER_NUM'].tolist()
    logger.info(f'[到货/检定量] 已加载 {len(order_nums_map)} 个设备码的采购批次规格')

    # ========================================================================
    #  步骤1: 读取省级库存，汇总得到 DF_stock[DEV_CODE, 合格品, 不合格品, 已检定]
    # ========================================================================

    logger.info(f'[到货/检定量] 开始读取省级库存数据, 目标月份: {yearMonth}')

    raw_pend = query_adam_realtime_pend_stock()

    target_dt = datetime.strptime(yearMonth, '%Y%m')
    target_start = target_dt.strftime('%Y-%m-%d 00:00:00')
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    logger.info(f'[到货/检定量] 区间范围: {now_str} → {target_start}')

    raw_arr = query_adam_future_arrivals(now_str, target_start)
    raw_det = query_adam_future_detections(now_str, target_start)
    raw_deliv = query_adam_future_deliveries(now_str, target_start)
    raw_rt_qua = query_adam_realtime_qua_stock()
    raw_insp = query_adam_completed_inspections(yearMonth)

    # ---- 统一清洗: 列名大写, DEV_CODE_NO → DEV_CODE, 按 DEV_CODE 汇总 ----

    def _clean_and_sum(raw, val_col, output_col=None):
        """清洗 → 按 DEV_CODE 汇总 → 重命名"""
        if raw.empty:
            return pd.DataFrame(columns=['DEV_CODE', output_col or val_col])
        raw = _clean_dev_code(raw)
        result = raw.groupby('DEV_CODE', as_index=False)[val_col.upper()].sum()
        if output_col:
            result.rename(columns={val_col.upper(): output_col}, inplace=True)
        return result

    df_pend = _clean_and_sum(raw_pend, 'NOW_PEND_NUM')
    logger.info(f'[到货/检定量] 实时待检: {len(df_pend)} 个设备码, 合计 {int(df_pend["NOW_PEND_NUM"].sum())} 只')

    df_arr = _clean_and_sum(raw_arr, 'ARR_NUM')
    logger.info(f'[到货/检定量] 区间到货: {len(df_arr)} 个设备码, 合计 {int(df_arr["ARR_NUM"].sum())} 只')

    df_det = _clean_and_sum(raw_det, 'DETECT_NUM')
    logger.info(f'[到货/检定量] 区间检定: {len(df_det)} 个设备码, 合计 {int(df_det["DETECT_NUM"].sum())} 只')

    df_deliv = _clean_and_sum(raw_deliv, 'DELIVERED_NUM')
    logger.info(f'[到货/检定量] 区间配送: {len(df_deliv)} 个设备码, 合计 {int(df_deliv["DELIVERED_NUM"].sum())} 只')

    df_rt_qua = _clean_and_sum(raw_rt_qua, 'QUA_STOCK_NUM', 'QUA_STOCK_RT')
    logger.info(f'[到货/检定量] 实时合格品(当前库存): {len(df_rt_qua)} 个设备码, 合计 {int(df_rt_qua["QUA_STOCK_RT"].sum())} 只')

    df_insp = _clean_and_sum(raw_insp, 'INSPECTED_NUM')
    logger.info(f'[到货/检定量] 已检定完工: {len(df_insp)} 个设备码, 合计 {int(df_insp["INSPECTED_NUM"].sum())} 只')

    # 推算本月末下月初不合格品: 实时待检 + 区间到货 - 区间检定
    df_unqua = df_pend.merge(df_arr, on='DEV_CODE', how='outer').merge(df_det, on='DEV_CODE', how='outer')
    df_unqua.fillna(0, inplace=True)
    df_unqua['UNQUA_STOCK'] = (df_unqua['NOW_PEND_NUM'] + df_unqua['ARR_NUM'] - df_unqua['DETECT_NUM']).clip(lower=0)
    df_unqua = df_unqua[['DEV_CODE', 'UNQUA_STOCK']]
    logger.info(f'[到货/检定量] 推算不合格品: {len(df_unqua)} 个设备码, 合计 {int(df_unqua["UNQUA_STOCK"].sum())} 只')

    # 合并为库存总表 DF_stock: 每个 DEV_CODE 一行（以实时合格品为基准）
    DF_stock = df_rt_qua.merge(df_unqua, on='DEV_CODE', how='outer') \
                        .merge(df_det, on='DEV_CODE', how='outer') \
                        .merge(df_deliv, on='DEV_CODE', how='outer') \
                        .merge(df_insp, on='DEV_CODE', how='outer')
    DF_stock.fillna(0, inplace=True)
    logger.info(f'[到货/检定量] 库存总表合并完成: {len(DF_stock)} 个设备码')
    # DF_stock 列: DEV_CODE, QUA_STOCK_RT, UNQUA_STOCK, DETECT_NUM, DELIVERED_NUM, INSPECTED_NUM

    # ========================================================================
    #  步骤2: 计算省级总量 DF_dev[D DEV_CODE, TOTAL_ARR, TOTAL_VERIF]
    # ========================================================================

    # 清洗 detail 的 DEV_CODE
    detail = _clean_dev_code(detail)

    # 汇总每个 DEV_CODE 的总需求量（保留 DEV_CLS, DEV_CATEG）
    dev_demand = detail.groupby(['DEV_CODE', 'DEV_CLS', 'DEV_CATEG'], as_index=False)['DEMAND'].sum()
    dev_demand.rename(columns={'DEMAND': 'TOTAL_DEMAND'}, inplace=True)
    logger.info(f'[到货/检定量] detail 中 {len(dev_demand)} 个设备码, 总需求量 {int(dev_demand["TOTAL_DEMAND"].sum())}')

    # 合并库存
    DF_dev = dev_demand.merge(DF_stock, on='DEV_CODE', how='left')
    DF_dev.fillna(0, inplace=True)

    # 推算目标月初合格品 = 实时合格品 + 期间检定 − 期间配送（对齐 Scheduling forward 模式）
    DF_dev['QUA_STOCK_ADJ'] = (DF_dev['QUA_STOCK_RT'] + DF_dev['DETECT_NUM']
                               - DF_dev['DELIVERED_NUM']).clip(lower=0)

    # 到货公式: ceil(1.25 × (1.25 × DEMAND − 合格品_ADJ) − 不合格品, 0)
    DF_dev['TOTAL_ARR'] = (1.25 * (1.25 * DF_dev['TOTAL_DEMAND'] - DF_dev['QUA_STOCK_ADJ'])
                           - DF_dev['UNQUA_STOCK']).clip(lower=0).apply(math.ceil)

    # 打印每个设备码的原始需求与原始到货量
    for _, r in DF_dev.iterrows():
        logger.info(f'[到货/检定量] dev={r["DEV_CODE"]}, cat={r["DEV_CATEG"]}, 补库需求={int(r["TOTAL_DEMAND"])}, '
                    f'合格品={int(r["QUA_STOCK_ADJ"])}, 不合格品={int(r["UNQUA_STOCK"])}, '
                    f'原始到货需求={int(r["TOTAL_ARR"])}')
        if str(r['DEV_CATEG']) == '01_04':
            logger.info(f'[到货/检定量] [01_04] dev={r["DEV_CODE"]}, 本月补货量={int(r["TOTAL_DEMAND"])}, '
                        f'合格品={int(r["QUA_STOCK_ADJ"])}, 不合格品={int(r["UNQUA_STOCK"])}, '
                        f'原始到货需求={int(r["TOTAL_ARR"])}')

    # 按采购订单批次规格取整（仅对需取整的类别：01_01, 01_02, DEV_CLS=09）
    NEED_BATCH_CATEGS = {'01_01', '01_02'}
    NEED_BATCH_CLS = {'09'}
    DF_dev['TOTAL_ARR'] = DF_dev.apply(
        lambda r: round_arrival_by_batch(int(r['TOTAL_ARR']), order_nums_map.get(r['DEV_CODE'], []), r['DEV_CODE'])
        if str(r.get('DEV_CATEG', '')) in NEED_BATCH_CATEGS or str(r.get('DEV_CLS', '')).zfill(2) in NEED_BATCH_CLS
        else int(r['TOTAL_ARR']),
        axis=1
    )

    # 检定公式: (到货量 + 不合格品) × 0.83，基于取整后的到货量
    DF_dev['TOTAL_VERIF'] = ((DF_dev['TOTAL_ARR'] + DF_dev['UNQUA_STOCK']) * 0.83).clip(lower=0).apply(math.ceil)

    logger.info(f'[到货/检定量] 省级总量(批次取整后): 到货 {int(DF_dev["TOTAL_ARR"].sum())} 只, 检定 {int(DF_dev["TOTAL_VERIF"].sum())} 只')

    # 省中心日均合格品 = (月初 + 月末) / 2（使用实时推算的月初合格品）
    # 月末 = 月初 + 检定新增 - 配送出库
    DF_dev['END_QUA'] = (DF_dev['QUA_STOCK_ADJ'] + DF_dev['TOTAL_VERIF'] - DF_dev['TOTAL_DEMAND']).clip(lower=0)
    DF_dev['CENTRAL_AVG_INV'] = ((DF_dev['QUA_STOCK_ADJ'] + DF_dev['END_QUA']) / 2.0).clip(lower=0)
    total_central_avg_inv = DF_dev['CENTRAL_AVG_INV'].sum()
    logger.info(f'[到货/检定量] 省中心总日均合格品: {total_central_avg_inv:.1f}')

    # 省中心月末合格品（已在上面 END_QUA 计算）
    total_central_qua_end = DF_dev['END_QUA'].sum()

    # 省中心非合格品库存: 月初 = UNQUA_STOCK, 月末 = 月初 + 到货 - 检定
    DF_dev['UNQUA_STOCK_END'] = (DF_dev['UNQUA_STOCK'] + DF_dev['TOTAL_ARR'] - DF_dev['TOTAL_VERIF']).clip(lower=0)
    DF_dev['CENTRAL_AVG_UNQUA'] = ((DF_dev['UNQUA_STOCK'] + DF_dev['UNQUA_STOCK_END']) / 2.0).clip(lower=0)
    total_central_unqua_avg_inv = DF_dev['CENTRAL_AVG_UNQUA'].sum()
    total_central_unqua_end = DF_dev['UNQUA_STOCK_END'].sum()
    logger.info(f'[到货/检定量] 省中心合格品月末={int(total_central_qua_end)}, '
                f'非合格品: 月初={int(DF_dev["UNQUA_STOCK"].sum())}, '
                f'月末={int(total_central_unqua_end)}, 日均={total_central_unqua_avg_inv:.1f}')

    # 省中心按 DEV_CATEG 汇总月初月末库存
    central_inv_by_cat = DF_dev.groupby(['DEV_CLS', 'DEV_CATEG'], as_index=False).agg(
        CENTRAL_START_QUA=('QUA_STOCK_ADJ', 'sum'),
        CENTRAL_START_UNQUA=('UNQUA_STOCK', 'sum'),
        CENTRAL_END_QUA=('END_QUA', 'sum'),
        CENTRAL_END_UNQUA=('UNQUA_STOCK_END', 'sum')
    )
    # 月初总库存 = 合格品 + 不合格品
    central_inv_by_cat['CENTRAL_START_TOTAL'] = central_inv_by_cat['CENTRAL_START_QUA'] + central_inv_by_cat['CENTRAL_START_UNQUA']
    central_inv_by_cat['CENTRAL_END_TOTAL'] = central_inv_by_cat['CENTRAL_END_QUA'] + central_inv_by_cat['CENTRAL_END_UNQUA']

    # DF_dev 列: DEV_CODE, TOTAL_DEMAND, TOTAL_ARR, TOTAL_VERIF (plus 库存中间列)
    # 保存完整 DF_dev（含省中心库存）用于后续追加省中心行
    central_dev_df = DF_dev[['DEV_CODE', 'DEV_CLS', 'DEV_CATEG',
                              'QUA_STOCK_ADJ', 'UNQUA_STOCK', 'END_QUA', 'UNQUA_STOCK_END']].copy()
    DF_dev = DF_dev[['DEV_CODE', 'TOTAL_DEMAND', 'TOTAL_ARR', 'TOTAL_VERIF']]

    # ========================================================================
    #  步骤3: 按各单位需求量占比拆分到 (ORG_NO, DEV_CODE)
    # ========================================================================

    detail = detail.merge(DF_dev, on='DEV_CODE', how='left')
    detail['TOTAL_DEMAND'] = detail['TOTAL_DEMAND'].fillna(0)

    # 占比 = 该行 DEMAND / 该 DEV_CODE 总 DEMAND
    detail['RATIO'] = detail.apply(
        lambda r: r['DEMAND'] / r['TOTAL_DEMAND'] if r['TOTAL_DEMAND'] > 0 else 0,
        axis=1
    )

    detail['ARR_QTY'] = (detail['TOTAL_ARR'] * detail['RATIO']).round(0).astype(int)
    detail['VERIF_QTY'] = (detail['TOTAL_VERIF'] * detail['RATIO']).round(0).astype(int)

    logger.info(f'[到货/检定量] 拆分完成: ARR_QTY 合计 {int(detail["ARR_QTY"].sum())}, VERIF_QTY 合计 {int(detail["VERIF_QTY"].sum())}')

    # 清理
    detail.drop(columns=['TOTAL_DEMAND', 'TOTAL_ARR', 'TOTAL_VERIF', 'RATIO'], inplace=True)

    # ========================================================================
    #  步骤4: 追加省中心行（ORG_NO='34101'）到明细表，供后续计算仓储成本
    # ========================================================================
    unit_price_map = detail.drop_duplicates(subset=['DEV_CODE']).set_index('DEV_CODE')['UNIT_PRICE']
    central_rows = []
    for _, r in central_dev_df.iterrows():
        dev_code = r['DEV_CODE']
        i0 = int(r['QUA_STOCK_ADJ'] + r['UNQUA_STOCK'])
        i_end = int(r['END_QUA'] + r['UNQUA_STOCK_END'])
        avg_inv = (i0 + i_end) / 2.0 if (i0 + i_end) > 0 else 0.0
        unit_price = unit_price_map.get(dev_code, 0)
        central_rows.append({
            'ORG_NO': '34101',
            'DEV_CODE': dev_code,
            'DEV_CLS': r['DEV_CLS'],
            'DEV_CATEG': r['DEV_CATEG'],
            'DEMAND': 0,
            'I0': i0,
            'BASE_LIMIT': 0,
            'PRE_NUM': 0,
            'I_END': i_end,
            'AVG_INV': avg_inv,
            'UNIT_PRICE': unit_price,
            'TURNOVER': 0.0,
            'HOLDING_COST': 0.0,
            'SHORTAGE_COST': 0.0,
            'ARR_QTY': 0,
            'VERIF_QTY': 0,
            'RUNNING_NUM': 0,
        })
    if central_rows:
        central_detail = pd.DataFrame(central_rows)
        detail = pd.concat([detail, central_detail], ignore_index=True)
        logger.info(f'[到货/检定量] 追加省中心行 {len(central_rows)} 条, '
                    f'省中心月初库存合计={sum(r["I0"] for r in central_rows)}, '
                    f'省中心日均库存合计={sum(r["AVG_INV"] for r in central_rows):.1f}')

    return detail, total_central_avg_inv, total_central_unqua_avg_inv, total_central_unqua_end, total_central_qua_end, central_inv_by_cat


def GetGlobalSchemeLPS(detail: pd.DataFrame, scheme_id: str) -> pd.DataFrame:
    """
    生成全局策略方案环节计划汇总明细表 (ADAM_GLOB_STRATEGY_SCHEME_LPS)

    参数:
        detail: 已经过 GetDeliverCost、GetVerifCost、GetArrCost 等函数处理的 DataFrame，
                至少包含字段: ORG_NO, DEV_CLS, DEV_CATEG, DEMAND, AVG_INV
        scheme_id: 方案标识（字符串或整数）

    返回:
        DataFrame 包含 LPS 表所需字段
    """
    import time
    import datetime

    # 1. 按管理单位、设备分类、设备类别汇总各环节数量
    grouped = detail.groupby(['ORG_NO', 'DEV_CLS', 'DEV_CATEG'], as_index=False).agg(
        DEMAND_SUM=('DEMAND', 'sum'),
        ARR_QTY_SUM=('ARR_QTY', 'sum'),
        VERIF_QTY_SUM=('VERIF_QTY', 'sum'),
        AVG_INV_SUM=('AVG_INV', 'sum')
    )

    # 2. 定义环节类型及对应的数量字段
    link_mapping = [
        ('01', 'DEMAND_SUM'),      # 采购 = 需求量
        ('02', 'ARR_QTY_SUM'),     # 到货 = ceil(1.25×(1.25×DEMAND-合格)-不合格)
        ('03', 'VERIF_QTY_SUM'),   # 检定 = max(0, DEMAND-实时合格)
        ('04', 'AVG_INV_SUM'),     # 仓储 = 日均库存
        ('05', 'DEMAND_SUM')       # 配送 = 需求量
    ]

    # 3. 生成记录
    base_ts = int(time.time() * 1000)  # 13位毫秒时间戳
    base_ts = int(str(base_ts)[:12])
    now = datetime.datetime.now().strftime('%Y-%m-%d')
    records = []
    idx = 0

    for _, row in grouped.iterrows():
        org = row['ORG_NO']
        dev_cls = row['DEV_CLS']
        dev_cat = row['DEV_CATEG']
        for link_type, value_col in link_mapping:
            quantity = row[value_col]
            det_id = base_ts + idx
            idx += 1
            records.append({
                'ITT_DET_ID': det_id,
                'SCHEME_ID': int(scheme_id),
                'LINK_TYPE': link_type,
                'ORG_NO': org,
                'DEV_CLS': dev_cls,
                'DEV_CATEG': dev_cat,
                'PRE_STAT_NUM': quantity,
                'INCUR_STAT_NUM': 0,      # 当前已完成总量，暂无数据填0
                'MADE_DATE': now,
                'UPDATE_DATE': now
            })

    # 4. 组装 DataFrame
    result = pd.DataFrame(records)
    columns = [
        'ITT_DET_ID', 'SCHEME_ID', 'LINK_TYPE', 'ORG_NO', 'DEV_CLS', 'DEV_CATEG',
        'PRE_STAT_NUM', 'INCUR_STAT_NUM', 'MADE_DATE', 'UPDATE_DATE'
    ]
    result = result[columns]
    return result

def GetGlobalSchemeCost(detail: pd.DataFrame, scheme_id: str, yearMonth: str) -> pd.DataFrame:
    """
    生成全局策略方案成本明细表 (ADAM_GLOB_STRATEGY_SCHEME_COST)

    参数:
        detail: 已包含 ARR_COST, VERIF_COST, DELIVER_COST, HOLDING_COST 等列的 DataFrame
        scheme_id: 方案标识
        yearMonth: 执行年月 'YYYYMM'

    返回:
        DataFrame 包含成本明细表所需字段
    """
    from backend.api.data_api.fetch_data import (
        query_adam_glob_strategy_scheme_by_month,
        query_adam_glob_strategy_scheme_cost_by_schemeid
    )
    import time
    import datetime

    #处理detail
    target_cols = [
        'VERI_LINE_NUM',
        'POSI_NUM',
        'POSI_CHECK_NUM',
        'CONCURRENT',
        'HOURLY_VERI_NUM'
    ]
    fill_map = {
        'VERI_LINE_NUM': 1,
        'POSI_NUM': 5,
        'POSI_CHECK_NUM': 12,
        'CONCURRENT': 60,
        'HOURLY_VERI_NUM': 120
    }
    for col in target_cols:
        detail[col] = detail[col].replace({0: fill_map[col], np.nan: fill_map[col]})
    detail['AUTO_DUR'] = detail['AUTO_DUR'].fillna(300)

    # 1. 按管理单位、设备分类、设备类别汇总需求量及各环节总成本
    grouped = detail.groupby(['ORG_NO', 'DEV_CLS', 'DEV_CATEG'], as_index=False).agg(
        DEMAND_SUM=('DEMAND', 'sum'),
        ARR_QTY_SUM=('ARR_QTY', 'sum'),
        VERIF_QTY_SUM=('VERIF_QTY', 'sum'),
        AVG_INV_SUM=('AVG_INV', 'sum'),
        COST_01=('PROCURE_COST', 'sum'),   # 采购
        COST_02=('ARR_COST', 'sum'),        # 到货
        COST_03=('VERIF_COST', 'sum'),      # 检定
        COST_04=('HOLDING_COST', 'sum'),    # 仓储
        COST_05=('DELIVER_COST', 'sum')     # 配送
    )
    logger.info(f'[成本明细] 分组后 COST_02 汇总={grouped["COST_02"].sum():.2f}, '
                f'分组数={len(grouped)}, COST_02>0 的分组数={(grouped["COST_02"] > 0).sum()}')

    # 2. 生成基础记录（无同比环比）
    link_mapping = [
        ('01', 'COST_01', 'DEMAND_SUM'),    # 采购: 单只成本 = 采购成本 / 补货量
        ('02', 'COST_02', 'ARR_QTY_SUM'),   # 到货: 单只成本 = 到货成本 / 到货量
        ('03', 'COST_03', 'VERIF_QTY_SUM'), # 检定: 单只成本 = 检定成本 / 检定量
        ('04', 'COST_04', 'AVG_INV_SUM'),   # 仓储: 单只成本 = 仓储成本 / 日均库存
        ('05', 'COST_05', 'DEMAND_SUM')     # 配送: 单只成本 = 配送成本 / 补货量
    ]

    base_ts = int(time.time() * 1000)  # 13位毫秒时间戳
    base_ts = int(str(base_ts)[:12])
    now_str = datetime.datetime.now().strftime('%Y-%m-%d')
    records = []
    idx = 0

    for _, row in grouped.iterrows():
        org = row['ORG_NO']
        dev_cls = row['DEV_CLS']
        dev_cat = row['DEV_CATEG']
        for link_type, cost_col, qty_col in link_mapping:
            # 省中心只保留仓储环节(04)，跳过采购/到货/检定/配送
            if str(org) == '34101' and link_type != '04':
                continue
            total_cost = row[cost_col]
            qty = row[qty_col]
            single_cost = total_cost / qty if qty > 0 else 0.0
            det_id = base_ts + idx
            idx += 1
            records.append({
                'COST_DET_ID': det_id,
                'SCHEME_ID': int(scheme_id),
                'LINK_TYPE': link_type,
                'COST_TYPE': link_type,          # 默认主要成本类型
                'ORG_NO': org,
                'DEV_CLS': dev_cls,
                'DEV_CATEG': dev_cat,
                'PRE_STAT_COST': round(total_cost, 2),
                'PRE_SINGLE_COST': round(single_cost, 4),
                'PRE_COST_YOY': None,
                'PRE_COST_TR': None,
                'INCUR_STAT_COST': 0.0,
                'INCUR_SINGLE_COST': 0.0,
                'INCUR_COST_YOY': 0.0,
                'INCUR_COST_TR': 0.0,
                'MADE_DATE': now_str,
                'UPDATE_DATE': now_str
            })

    result = pd.DataFrame(records)
    link02_sum = result[result['LINK_TYPE'] == '02']['PRE_STAT_COST'].sum()
    logger.info(f'[成本明细] 生成记录后 LINK_TYPE=02 的 PRE_STAT_COST 汇总={link02_sum:.2f}, 总记录数={len(result)}')
    logger.info('计算成本明细同比环比')
    # 3. 计算同比环比（基于历史成本明细）
    year = int(yearMonth[:4])
    month = int(yearMonth[4:])
    if month == 1:
        last_month = f"{year-1}12"
    else:
        last_month = f"{year}{month-1:02d}"
    last_year = f"{year-1}{month:02d}"

    df_last_month = query_adam_glob_strategy_scheme_by_month(last_month)
    df_last_year = query_adam_glob_strategy_scheme_by_month(last_year)

    scheme_id_last_month = int(df_last_month.iloc[0]['SCHEME_ID']) if not df_last_month.empty else None
    scheme_id_last_year = int(df_last_year.iloc[0]['SCHEME_ID']) if not df_last_year.empty else None

    cost_last_month_df = pd.DataFrame()
    cost_last_year_df = pd.DataFrame()
    logger.info('读取历史成本明细数据')
    if scheme_id_last_month:
        cost_last_month_df = query_adam_glob_strategy_scheme_cost_by_schemeid(scheme_id_last_month)
    if scheme_id_last_year:
        cost_last_year_df = query_adam_glob_strategy_scheme_cost_by_schemeid(scheme_id_last_year)

    def calc_rate(current, history):
        if history is None or history == 0:
            return 0.0
        return round((current - history) / history * 100, 2)

    # 合并上月数据计算环比
    if not cost_last_month_df.empty:
        cost_last_month_df = cost_last_month_df.rename(columns={'PRE_STAT_COST': 'COST_LAST_MONTH'})
        result = result.merge(
            cost_last_month_df[['LINK_TYPE', 'ORG_NO', 'DEV_CLS', 'DEV_CATEG', 'COST_LAST_MONTH']],
            on=['LINK_TYPE', 'ORG_NO', 'DEV_CLS', 'DEV_CATEG'],
            how='left'
        )
        result['PRE_COST_TR'] = result.apply(
            lambda r: calc_rate(r['PRE_STAT_COST'], r['COST_LAST_MONTH']), axis=1
        )
        result.drop(columns=['COST_LAST_MONTH'], inplace=True)
    else:
        result['PRE_COST_TR'] = 0.0

    # 合并去年数据计算同比
    if not cost_last_year_df.empty:
        cost_last_year_df = cost_last_year_df.rename(columns={'PRE_STAT_COST': 'COST_LAST_YEAR'})
        result = result.merge(
            cost_last_year_df[['LINK_TYPE', 'ORG_NO', 'DEV_CLS', 'DEV_CATEG', 'COST_LAST_YEAR']],
            on=['LINK_TYPE', 'ORG_NO', 'DEV_CLS', 'DEV_CATEG'],
            how='left'
        )
        result['PRE_COST_YOY'] = result.apply(
            lambda r: calc_rate(r['PRE_STAT_COST'], r['COST_LAST_YEAR']), axis=1
        )
        result.drop(columns=['COST_LAST_YEAR'], inplace=True)
    else:
        result['PRE_COST_YOY'] = 0.0

    # 填充可能缺失的同比环比，并防止数据溢出
    for col in ['PRE_COST_TR', 'PRE_COST_YOY']:
        result[col] = result[col].fillna(0.0).replace([float('inf'), -float('inf')], 0.0)
        result[col] = result[col].clip(-999.99, 999.99)
    # PRE_STAT_COST / PRE_SINGLE_COST → NUMBER(10,2)，max=99,999,999.99
    for col in ['PRE_STAT_COST', 'PRE_SINGLE_COST']:
        result[col] = result[col].fillna(0.0).replace([float('inf'), -float('inf')], 0.0)
        result[col] = result[col].clip(-99999999.99, 99999999.99)

    # 4. 按目标字段顺序返回
    columns = [
        'COST_DET_ID', 'SCHEME_ID', 'LINK_TYPE', 'COST_TYPE', 'ORG_NO', 'DEV_CLS', 'DEV_CATEG',
        'PRE_STAT_COST', 'PRE_SINGLE_COST', 'PRE_COST_YOY', 'PRE_COST_TR',
        'INCUR_STAT_COST', 'INCUR_SINGLE_COST', 'INCUR_COST_YOY', 'INCUR_COST_TR',
        'MADE_DATE', 'UPDATE_DATE'
    ]
    return result[columns]

def GetRunDurDetail(detail:pd.DataFrame):
    from backend.api.data_api.fetch_data import (
        query_adam_del_site_conf,
        query_adam_spec_code_config,
        query_adam_run_dur_sample_all)  # TODO 这里库存运行比数据也需要按照单位层级汇总到市县---需要汇总，直接改sql

    # 1. 获取有效站点（排除营销服务中心）
    site_df = query_adam_del_site_conf()
    site_df = site_df[site_df['STAT_NAME'] != '营销服务中心'].copy()
    org_list = site_df['ORG_NO'].unique().tolist()
    logger.info(f"有效站点数量（排除营销中心）: {len(org_list)}")

    # 2. 按站点获取运行时长明细
    combined_df = query_adam_run_dur_sample_all()
    # all_dfs = []
    # for org in org_list:
    #     try:
    #         df = query_adam_run_dur_sample_by_org_no(org)
    #         if not df.empty:
    #             all_dfs.append(df)
    #     except Exception as e:
    #         logger.warning(f"获取站点 {org} 运行年限数据失败: {e}")
    #         continue
    #
    # if not all_dfs:
    #     logger.warning("未获取到任何运行年限数据")
    #     return pd.DataFrame()

    # combined_df = pd.concat(all_dfs, ignore_index=True)
    logger.info(f"合并后运行年限记录数: {len(combined_df)}")

    # 3. 获取有效设备码
    spec_df = query_adam_spec_code_config()
    valid_dev_codes = set(spec_df['DEV_CODE'].unique())
    logger.info(f"有效设备码数量: {len(valid_dev_codes)}")

    # 4. 筛选 DEV_CODE 有效的数据
    filtered_df = combined_df[combined_df['DEV_CODE'].isin(valid_dev_codes)].copy()
    logger.info(f"筛选后运行年限记录数: {len(filtered_df)}")
    # 5. 重命名 DEV_NUM 列为 RUNNING_NUM，并选取需要的三列
    if 'DEV_NUM' in filtered_df.columns:
        filtered_df = filtered_df.rename(columns={'DEV_NUM': 'RUNNING_NUM'})
    else:
        logger.warning("运行时长表中未找到 DEV_NUM 列，将无法获取运行时长")
        detail['RUNNING_NUM'] = 0

    run_cols = ['ORG_NO', 'DEV_CODE', 'RUNNING_NUM']
    run_sub = filtered_df[run_cols]
    # 按 ORG_NO + DEV_CODE 聚合去重
    run_sub = run_sub.groupby(['ORG_NO', 'DEV_CODE'], as_index=False)['RUNNING_NUM'].sum()
    logger.info(f"merge 前 detail 记录数: {len(detail)}")
    detail = detail.merge(run_sub, on=['ORG_NO', 'DEV_CODE'], how='left')
    detail['RUNNING_NUM'] = detail['RUNNING_NUM'].fillna(0)  # 未匹配到则填0

    logger.info(f"左连接后 detail 记录数: {len(detail)}")
    detail['RUNNING_NUM'] = detail['PRE_NUM'] + detail['RUNNING_NUM']
    if 'I_END' in detail.columns:
        # 避免除零，RUNNING_NUM 为0时 ITR 设为 0
        detail['ITR'] = detail.apply(
            lambda row: row['I_END'] / row['RUNNING_NUM'] if row['RUNNING_NUM'] != 0 else 0,
            axis=1
        )
        # 可选：保留两位小数
        detail['ITR'] = detail['ITR'].round(4)
        logger.info("已计算库存运行比 ITR")
    else:
        logger.warning("detail 中缺少 I_END 列，无法计算 ITR")
        detail['ITR'] = 0

    logger.info(f"处理后 detail 记录数: {len(detail)}")

    return detail

def determine_scheme_focus(scheme_items: dict) -> dict:

    # 提取信息列表，每个元素为 (tag, cost, itt, exec_ym)
    items = []
    for tag, df in scheme_items.items():
        cost = df.iloc[0]['PRE_STAT_COST']
        itt = df.iloc[0]['PRE_ITT']
        exec_ym = df.iloc[0].get('EXEC_YM', '')
        items.append((tag, cost, itt, exec_ym))
        logger.info(f"方案 {tag}: 成本={cost}, 周转={itt:.4f}, 年月={exec_ym}")

    # 按成本降序、周转降序排序
    items_by_cost = sorted(items, key=lambda x: x[1], reverse=True)
    items_by_itt = sorted(items, key=lambda x: x[2], reverse=True)

    focus_map = {}

    # 成本最高 → 01
    cost_tag = items_by_cost[0][0]
    focus_map[cost_tag] = '01'
    logger.info(f"方案 {cost_tag} 成本最高，设为资金入账优先(01)")

    # 剩余两个中周转最高 → 02
    remaining = [it for it in items if it[0] != cost_tag]
    if remaining:
        rem_by_itt = sorted(remaining, key=lambda x: x[2], reverse=True)
        itt_tag = rem_by_itt[0][0]
        focus_map[itt_tag] = '02'
        logger.info(f"方案 {itt_tag} 周转最高（剩余中），设为周转优先(02)")
        # 最后一个 → 03
        for it in remaining:
            if it[0] != itt_tag:
                focus_map[it[0]] = '03'
                logger.info(f"方案 {it[0]} 设为均衡(03)")

    # 修改 DataFrame
    for tag, df in scheme_items.items():
        focus = focus_map[tag]
        # 设置 SCHEME_FOCUS
        if 'SCHEME_FOCUS' not in df.columns:
            df['SCHEME_FOCUS'] = None
        df.loc[0, 'SCHEME_FOCUS'] = focus

        # 获取年月
        exec_ym = df.iloc[0].get('EXEC_YM', '')
        if not exec_ym:
            exec_ym = "未知年月"
            logger.warning(f"方案 {tag} 缺少 EXEC_YM 列，使用默认")

        # 生成方案名
        if focus == '01':
            scheme_name = f"{exec_ym}资金入账优先方案"
        elif focus == '02':
            scheme_name = f"{exec_ym}周转优先方案"
        else:
            scheme_name = f"{exec_ym}均衡方案"

        # 修复列类型问题：强制转换为 object
        if 'SCHEME_NAME' not in df.columns:
            df['SCHEME_NAME'] = None
        df['SCHEME_NAME'] = df['SCHEME_NAME'].astype(str)
        df.loc[0, 'SCHEME_NAME'] = scheme_name
        logger.info(f"已设置方案 {tag}: SCHEME_FOCUS={focus}, SCHEME_NAME={scheme_name}")

    # ---- PRE_ITT 后处理：保留两位小数，重复时按侧重微调 ----
    for tag, df in scheme_items.items():
        df.loc[0, 'PRE_ITT'] = round(df.iloc[0]['PRE_ITT'], 2)
    itt_vals = {tag: df.iloc[0]['PRE_ITT'] for tag, df in scheme_items.items()}
    logger.info(f"PRE_ITT round后: {itt_vals}")

    if len(set(itt_vals.values())) < len(scheme_items):
        logger.info("PRE_ITT 存在重复，按成本微调 (成本低→周转高, 成本高→周转低)")
        base = min(itt_vals.values())
        # 按成本升序排列: [最低成本, 中, 最高成本]
        tags_by_cost = sorted(scheme_items.keys(),
                              key=lambda t: scheme_items[t].iloc[0]['PRE_STAT_COST'])
        offsets = [0.02, 0.01, 0.0]  # 成本低→周转高, 成本高→周转低
        for i, tag in enumerate(tags_by_cost):
            df = scheme_items[tag]
            df.loc[0, 'PRE_ITT'] = max(0, base + offsets[min(i, len(offsets) - 1)])
        logger.info(f"PRE_ITT 微调后: { {tag: df.iloc[0]['PRE_ITT'] for tag, df in scheme_items.items()} }")
    # -------------------------------------------------------------------

    # 输出最终分配
    logger.info("最终侧重分配:")
    for tag, focus in focus_map.items():
        focus_name = {'01': '资金入账优先', '02': '周转优先', '03': '均衡分布'}.get(focus, '未知')
        logger.info(f"  方案 {tag}: {focus_name} ({focus})")

    return scheme_items

