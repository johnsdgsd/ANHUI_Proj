"""
多套计划补库数量生成器
用于生成多套不同策略的补库计划
"""
import logging

import pandas as pd
import numpy as np
import datetime
from backend.global_optimization.logger import logger
from backend.inventory_optimization.RunOptimize import run_optimization_from_api
from backend.inventory_optimization.GetMonthlyOrder import GenerateMonthlyThresholdAndOrder
import time

def GenerateMutiOrderScheme(yearMonth:str):
    '''
    每月一日触发，输入的日期是当月1日的日期
    例如:'202605
    '''
    from backend.api.data_api.fetch_data import (
        query_adam_org_stock_sample_by_month,
        query_adam_pre_range_info,
        insert_into_adam_glob_strategy_scheme,
        insert_into_adam_glob_strategy_scheme_cost,
        insert_into_adam_glob_strategy_scheme_itt,
        insert_into_adam_glob_strategy_scheme_lps,
        deleteScheme)

    deleteScheme(yearMonth)
    year = yearMonth[:4]
    month = yearMonth[4:6]
    epsilons = [0.99,0.995,0.999]
    monthly_holding_rate = 0.01

    init_stock = query_adam_org_stock_sample_by_month(yearMonth)
    logger.info(f'查询月度库存快照成功，数据量{len(init_stock)}')
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

    for e in epsilons:

        Threshold, Order,Demand_Pre = GenerateMonthlyThresholdAndOrder(
            year=year,
            month=month,
            init_stock=init_stock,
            tag=tag,
            alpha = e
        )
        tag_epsilon_map[tag] = e
        OrderSchemes[tag] = Order
        ThresholdSchemes[tag] = Threshold
        detail = PrepareDetail(Order,Threshold,init_stock,item_cost,Demand_Pre,monthly_holding_rate = monthly_holding_rate)
        detail = GetRunDurDetail(detail)
        logger.info('准备开始计算全局主表明细')
        global_scheme_item,detail = GetGlobalSchemeItem(detail,tag,yearMonth)
        logger.info('准备计算周转明细')
        global_scheme_itt = GetGlobalSchemeITT(detail,tag,yearMonth)
        logger.info('准备计算明细汇总')
        global_shceme_lps = GetGlobalSchemeLPS(detail,tag)
        logger.info('准备计算成本明细')
        global_scheme_cost = GetGlobalSchemeCost(detail,tag,yearMonth)

        cond1 = global_shceme_lps['PRE_STAT_NUM'] != 0
        cond2 = global_scheme_cost['PRE_STAT_COST'] == 0
        condition = cond1 & cond2
        global_scheme_cost.loc[condition, 'PRE_STAT_COST'] = 50 + global_shceme_lps.loc[condition, 'PRE_STAT_NUM'] * 0.1
        #
        global_scheme_cost['PRE_SINGLE_COST'] = global_scheme_cost['PRE_STAT_COST'].div(
            global_shceme_lps['PRE_STAT_NUM'], fill_value=0).replace([float('inf'), -float('inf')], 0)

        global_scheme_item['PRE_STAT_COST'] = global_scheme_item['PRE_STAT_COST'].astype(float).round(2)
        GlobalSchemeItems[tag] = global_scheme_item
        GlobalSchemeCost[tag] = global_scheme_cost
        GlobalSchemeITTs[tag] = global_scheme_itt
        GlobalSchemeLPS[tag] = global_shceme_lps

        tag +=1

    GlobalSchemeItems = determine_scheme_focus(GlobalSchemeItems)

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
        insert_into_adam_glob_strategy_scheme_cost(GlobalSchemeCost[tag])
        insert_into_adam_glob_strategy_scheme_itt(GlobalSchemeITTs[tag])
        insert_into_adam_glob_strategy_scheme_lps(GlobalSchemeLPS[tag])
    
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
    detail['UNIT_PRICE'] = detail['UNIT_PRICE'].fillna(0)
    # 6. 计算日均库存
    detail['AVG_INV'] = (detail['I0'] + detail['I_END']) / 2.0

    # 增加需求预测结果
    if PreNum is not None and not PreNum.empty:
        pre_num_sub = PreNum[['ORG_NO', 'DEV_CODE', 'PRE_NUM']].copy()
        detail = detail.merge(pre_num_sub, on=['ORG_NO', 'DEV_CODE'], how='left')
        detail['PRE_NUM'] = detail['PRE_NUM'].fillna(0)  # 若无预测则填0
    else:
        detail['PRE_NUM'] = 0

    # 月周转次数
    detail['TURNOVER'] = detail.apply(
        lambda row: row['PRE_NUM'] / row['AVG_INV'] if row['AVG_INV'] > 0 else 0,
        axis=1
    )
    detail['TURNOVER'] = detail['TURNOVER'].round(8)
    # 9. 持有成本 = 日均库存 * 单价 * 月持有成本率 * 30天
    detail['HOLDING_COST'] = detail['AVG_INV'] * detail['UNIT_PRICE'] * monthly_holding_rate * 30
    # 10. 缺货成本（暂设为0，可根据业务扩展）
    detail['SHORTAGE_COST'] = 0.0


    return detail


def GetGlobalSchemeItem(detail: pd.DataFrame, scheme_no: str, yearMonth: str):
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

    total_holding_cost = detail['HOLDING_COST'].sum()
    logger.info('计算配送成本')
    total_deliver_cost,detail = GetDeliverCost(detail)
    logger.info('计算检定成本')
    total_verificaiton_cost,detail = GetVerifCost(detail)
    logger.info('计算到货成本')
    total_arr_cost,detail = GetArrCost(detail)
    logger.info('计算同比环比')
    pre_stat_cost = total_arr_cost + total_verificaiton_cost + total_deliver_cost + total_holding_cost
    pre_single_cost = pre_stat_cost / (total_demand + total_inv) if (total_demand + total_inv)> 0 else 0.0
    total_turnover = detail.groupby(['ORG_NO', 'DEV_CLS', 'DEV_CATEG'])['TURNOVER'].sum().mean()
    cur_itr = detail['ITR'].round(2).mean()  ##这里是均值
    # 成本周转次数同比环比计算（历史值为空或0时结果为0）
    cost_tr = 0.0
    if cost_last_month and cost_last_month != 0:
        cost_tr = (pre_single_cost - cost_last_month) / cost_last_month
        cost_tr = round(cost_tr,2)

    cost_yoy = 0.0
    if cost_last_year and cost_last_year != 0:
        cost_yoy = (pre_single_cost - cost_last_year) / cost_last_year
        cost_yoy = round(cost_yoy,2)

    itt_tr = 0.0
    if itt_last_month and itt_last_month != 0:
        itt_tr = (total_turnover - itt_last_month ) / itt_last_month
        itt_tr = round(itt_tr,2)

    itt_yoy = 0.0
    if itt_last_year and itt_last_year != 0:
        itt_yoy = (total_turnover - itt_last_year) / itt_last_year
        itt_yoy = round(itt_yoy,2)
    # 计算库存运行比
    itr_tr = 0.0
    if itr_last_month and itr_last_month != 0:
        itr_tr = (cur_itr - itr_last_month) / itr_last_month
        itr_tr = round(itr_tr,2)

    itr_yoy = 0.0
    if itr_last_year and itr_last_year != 0:
        itr_yoy = (cur_itr - itr_last_year) / itr_last_year
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
    pre_stat_cost = safe_num_scale(pre_stat_cost, NUM10_2_MAX)
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

def GetGlobalSchemeITT(detail: pd.DataFrame, scheme_id: str, yearMonth: str) -> pd.DataFrame:
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
        PRE_ITT=('TURNOVER', 'sum'),
        PRE_ITR = ('ITR','mean')
    )

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
    # 3. 计算同比环比（避免除零）
    def calc_rate(current, history):
        # 历史值为0时返回0.0
        value = ((current - history) / history ).where(history != 0, 0.0)
        return round(value,2)

    grouped['ITT_TR'] = calc_rate(grouped['PRE_ITT'], grouped['PRE_ITT_LAST_MONTH'])
    grouped['ITT_YOY'] = calc_rate(grouped['PRE_ITT'], grouped['PRE_ITT_LAST_YEAR'])

    grouped['ITR_TR'] = calc_rate(grouped['PRE_ITR'], grouped['PRE_ITR_LAST_MONTH'])
    grouped['ITR_YOY'] = calc_rate(grouped['PRE_ITR'], grouped['PRE_ITR_LAST_YEAR'])
    # 4. 生成主键
    base_ts = int(time.time() * 1000)  # 13位毫秒时间戳
    base_ts = int(str(base_ts)[:12])
    grouped['ITT_DET_ID'] = [base_ts + i for i in range(len(grouped))]

    # 5. 添加固定字段
    now = datetime.datetime.now().strftime('%Y-%m-%d')
    grouped['SCHEME_ID'] = int(scheme_id) if isinstance(scheme_id, str) else scheme_id
    grouped['MADE_DATE'] = now
    grouped['UPDATE_DATE'] = now

    # 6. 暂不计算的字段置为 None
    none_fields = ['INCUR_ITR', 'INCUR_ITT']
    for field in none_fields:
        grouped[field] = None

    # 7. 按目标表字段顺序返回
    columns = [
        'ITT_DET_ID', 'SCHEME_ID', 'ORG_NO', 'START_STOCK_NUM', 'END_STOCK_NUM',
        'DEV_CLS', 'DEV_CATEG', 'PRE_ITR', 'ITR_YOY', 'ITR_TR', 'INCUR_ITR',
        'PRE_ITT', 'ITT_YOY', 'ITT_TR', 'INCUR_ITT', 'MADE_DATE', 'UPDATE_DATE'
    ]
    return grouped[columns]

def GetArrCost(detail:pd.DataFrame):
    
    detail['ARR_COST'] = detail['DEMAND'] * detail['UNIT_PRICE']
    total_arr_cost = detail['ARR_COST'].sum()
    return total_arr_cost,detail

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
    mask = (cost_df['LINK_TYPE'] == '03') & (cost_df['COST_TYPE'] == '02') & (cost_df['BASE_COST_TYPE'] == '01')
    matched = cost_df[mask]

    if matched.empty :
        # detail['VERIF_COST'] = 0.0
        daily_wage = 200
    try:
        daily_wage = matched.iloc[0]['BASE_COST_VALUE']
    except Exception as e:
        daily_wage = 200

    hourly_wage = daily_wage / 8.0   # 时薪，元/小时
    verif_costs = []
    total_cost = 0.0
    for _, row in detail.iterrows():
        demand = row['DEMAND']
        hourly_rate = row['HOURLY_VERI_NUM']
        if hourly_rate > 0:
            hours = demand / hourly_rate
            cost = hours * hourly_wage
            cost = round(cost,1)
        else:
            cost = 0.0
        verif_costs.append(cost)
        total_cost += cost
    
    detail['VERIF_COST'] = verif_costs
    return total_cost,detail

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

    # 1. 按管理单位、设备分类、设备类别汇总总需求和日均库存
    grouped = detail.groupby(['ORG_NO', 'DEV_CLS', 'DEV_CATEG'], as_index=False).agg(
        DEMAND_SUM=('DEMAND', 'sum'),
        AVG_INV_SUM=('AVG_INV', 'sum')   # 仓储使用日均库存总和
    )

    # 2. 定义环节类型及对应的数量字段
    # 采购、到货、检定、配送 均使用 DEMAND_SUM；仓储使用 AVG_INV_SUM
    link_mapping = [
        ('01', 'DEMAND_SUM'),   # 采购
        ('02', 'DEMAND_SUM'),   # 到货
        ('03', 'DEMAND_SUM'),   # 检定
        ('04', 'AVG_INV_SUM'),  # 仓储
        ('05', 'DEMAND_SUM')    # 配送
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

    # 1. 按管理单位、设备分类、设备类别汇总需求总量及各环节总成本
    grouped = detail.groupby(['ORG_NO', 'DEV_CLS', 'DEV_CATEG'], as_index=False).agg(
        DEMAND_SUM=('DEMAND', 'sum'),
        COST_01=('ARR_COST', 'sum'),      # 采购
        COST_02=('ARR_COST', 'sum'),      # 到货
        COST_03=('VERIF_COST', 'sum'),    # 检定
        COST_04=('HOLDING_COST', 'sum'),  # 仓储
        COST_05=('DELIVER_COST', 'sum')   # 配送
    )

    # 2. 生成基础记录（无同比环比）
    link_mapping = [
        ('01', 'COST_01'),   # 采购
        ('02', 'COST_02'),   # 到货
        ('03', 'COST_03'),   # 检定
        ('04', 'COST_04'),   # 仓储
        ('05', 'COST_05')    # 配送
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
        demand_sum = row['DEMAND_SUM']
        for link_type, cost_col in link_mapping:
            total_cost = row[cost_col]
            single_cost = total_cost / demand_sum if demand_sum > 0 else 0.0
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
                'INCUR_COST_YOY': None,
                'INCUR_COST_TR': None,
                'MADE_DATE': now_str,
                'UPDATE_DATE': now_str
            })

    result = pd.DataFrame(records)
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

    # 填充可能缺失的同比环比
    result['PRE_COST_TR'] = result['PRE_COST_TR'].fillna(0.0)
    result['PRE_COST_YOY'] = result['PRE_COST_YOY'].fillna(0.0)

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

    # 按成本升序、周转降序排序
    items_by_cost = sorted(items, key=lambda x: x[1])
    items_by_itt = sorted(items, key=lambda x: x[2], reverse=True)

    focus_map = {}

    # 成本最低 → 01
    cost_tag = items_by_cost[0][0]
    focus_map[cost_tag] = '01'
    logger.info(f"方案 {cost_tag} 成本最低，设为成本优先(01)")

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
            scheme_name = f"{exec_ym}成本最低方案"
        elif focus == '02':
            scheme_name = f"{exec_ym}周转最优方案"
        else:
            scheme_name = f"{exec_ym}均衡方案"

        # 修复列类型问题：强制转换为 object
        if 'SCHEME_NAME' not in df.columns:
            df['SCHEME_NAME'] = None
        df['SCHEME_NAME'] = df['SCHEME_NAME'].astype(str)
        df.loc[0, 'SCHEME_NAME'] = scheme_name
        logger.info(f"已设置方案 {tag}: SCHEME_FOCUS={focus}, SCHEME_NAME={scheme_name}")

    # 输出最终分配
    logger.info("最终侧重分配:")
    for tag, focus in focus_map.items():
        focus_name = {'01': '成本优先', '02': '周转优先', '03': '均衡分布'}.get(focus, '未知')
        logger.info(f"  方案 {tag}: {focus_name} ({focus})")

    return scheme_items

