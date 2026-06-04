import pandas as pd
import numpy as np
from scipy.stats import poisson
from datetime import datetime, timedelta
import logging
import time

logger = logging.getLogger(__name__)


def emergency_replenishment_check_v2(
        stock_df: pd.DataFrame,
        daily_plan_df: pd.DataFrame,
        daily_demand_df: pd.DataFrame,
        threshold_percentile: float = 0.5,
        target_percentile: float = 0.9,
        max_lead_time: int = 7
) -> pd.DataFrame:
    """
    紧急补库监控 V2：基于日需求预测计算日均需求率（替代 V1 的月度需求/30）。

    参数:
        stock_df:       当前库存快照，列: MGT_ORG_CODE, DEV_CODE_NO, STOCK_NUM
        daily_plan_df:  日补库计划表，列: PRE_DATE, REC_ORG_NO, DEV_CODE, PLAN_IAS_NUM
        daily_demand_df:日需求预测表，列: PRE_DATE, ORG_NO, DEV_CODE, PRE_NUM
        threshold_percentile: 触发阈值分位数，默认 0.5
        target_percentile:    目标服务水平分位数，默认 0.9
        max_lead_time:        最大提前天数，默认 7

    返回:
        DataFrame 列: ORG_NO, DEV_CODE, CURRENT_STOCK, PERIOD_DEMAND,
                      NEXT_REPLENISH_DAYS, THRESHOLD, TARGET_STOCK,
                      RECOMMEND_QTY, RECOMMEND_DAYS, RECOMMEND_DATE, GLOBAL_SCHEME_ID
    """
    today = datetime.now().date()
    logger.info(f"[V2] 开始紧急补库检查，日期: {today}")

    # 1. 列映射 & 仅保留新表
    stock = stock_df.rename(columns={
        'MGT_ORG_CODE': 'ORG_NO',
        'DEV_CODE_NO': 'DEV_CODE',
        'STOCK_NUM': 'CURRENT_STOCK'
    })
    before_stock = len(stock_df)
    if 'OLD_NEW_FLAG' in stock.columns:
        stock = stock[stock['OLD_NEW_FLAG'] == '01'].copy()
        logger.info(f"[V2] 库存过滤新旧表: {before_stock} -> {len(stock)} (仅保留新表 OLD_NEW_FLAG='01')")
    else:
        logger.warning("[V2] 库存表缺少 OLD_NEW_FLAG 列，未过滤新旧表")
    daily = daily_plan_df.rename(columns={'REC_ORG_NO': 'ORG_NO'})
    before_demand = len(daily_demand_df)
    demand = daily_demand_df.rename(columns={'PRE_NUM': 'DAILY_DEMAND'})
    # 按 (日期, 单位, 设备码) 汇总，折叠 BUS_TYPE 分业务类型维度
    demand = demand.groupby(['PRE_DATE', 'ORG_NO', 'DEV_CODE'], as_index=False)['DAILY_DEMAND'].sum()
    logger.info(f"[V2] 日需求预测按 BUS_TYPE 聚合: {before_demand} -> {len(demand)} 条 (已折叠业务类型维度)")

    logger.info(f"[V2] 库存记录数: {len(stock)}")
    logger.info(f"[V2] 日补库计划记录数: {len(daily)}")

    # 2. 提取下次补库日期
    daily['PRE_DATE'] = pd.to_datetime(daily['PRE_DATE']).dt.date
    future_plan = daily[daily['PRE_DATE'] >= today]
    logger.info(f"[V2] 未来日补库计划记录数: {len(future_plan)}")
    if future_plan.empty:
        logger.warning("[V2] 没有未来的日补库计划，流程终止")
        return pd.DataFrame()

    if 'GLOBAL_SCHEME_ID' in future_plan.columns:
        global_scheme_id = future_plan['GLOBAL_SCHEME_ID'].iloc[0]
    else:
        global_scheme_id = -999

    next_replenish = future_plan.groupby(['ORG_NO', 'DEV_CODE'], as_index=False)['PRE_DATE'].min()
    next_replenish.rename(columns={'PRE_DATE': 'NEXT_DATE'}, inplace=True)
    logger.info(f"[V2] 获取到下次补库日期的组合数: {len(next_replenish)}")

    # 3. 合并库存与下次补库日期
    merged = stock.merge(next_replenish, on=['ORG_NO', 'DEV_CODE'], how='inner')
    if merged.empty:
        logger.warning("[V2] 库存和下次补库日期无匹配记录，流程终止")
        return pd.DataFrame()

    merged['NEXT_DATE'] = pd.to_datetime(merged['NEXT_DATE'])
    today_ts = pd.Timestamp(today)
    merged['NEXT_REPLENISH_DAYS'] = (merged['NEXT_DATE'] - today_ts).dt.days

    before_filter = len(merged)
    merged = merged[merged['NEXT_REPLENISH_DAYS'] > 0].copy()
    after_day0 = len(merged)
    merged = merged[merged['NEXT_REPLENISH_DAYS'] <= max_lead_time].copy()
    after_lead = len(merged)
    logger.info(f"[V2] 补库天数过滤: 原始 {before_filter} -> 排除当天(NEXT_DAYS<=0) {after_day0} -> 排除超过{max_lead_time}天 {after_lead}")
    if merged.empty:
        logger.info("[V2] 过滤后无有效记录")
        return pd.DataFrame()

    logger.info(f"[V2] 合并后有效记录数: {len(merged)}")

    # 4. 从日需求预测计算期间需求
    demand['PRE_DATE'] = pd.to_datetime(demand['PRE_DATE']).dt.date
    demand_period = demand.merge(
        merged[['ORG_NO', 'DEV_CODE', 'NEXT_DATE']].drop_duplicates(),
        on=['ORG_NO', 'DEV_CODE'], how='inner'
    )
    demand_period = demand_period[
        (demand_period['PRE_DATE'] >= today) &
        (demand_period['PRE_DATE'] < demand_period['NEXT_DATE'])
    ]
    period_sum = demand_period.groupby(['ORG_NO', 'DEV_CODE'], as_index=False)['DAILY_DEMAND'].sum()
    period_sum.rename(columns={'DAILY_DEMAND': 'PERIOD_DEMAND'}, inplace=True)

    merged = merged.merge(period_sum, on=['ORG_NO', 'DEV_CODE'], how='left')
    merged['PERIOD_DEMAND'] = merged['PERIOD_DEMAND'].fillna(0)
    merged['DAILY_AVG'] = merged['PERIOD_DEMAND'] / merged['NEXT_REPLENISH_DAYS']

    zero_demand = (merged['PERIOD_DEMAND'] == 0).sum()
    if zero_demand > 0:
        logger.warning(f"[V2] {zero_demand} 条记录在 [today, NEXT_DATE) 区间内日需求预测为空 (PERIOD_DEMAND=0)，将跳过触发")
    logger.info(f"[V2] 期间需求统计: 均值={merged['PERIOD_DEMAND'].mean():.2f}, "
                f"日均需求均值={merged['DAILY_AVG'].mean():.4f}")

    # 5. 遍历判断
    skipped_demand_zero = 0
    skipped_stock_ok = 0
    triggered = 0
    results = []
    for idx, row in merged.iterrows():
        org = row['ORG_NO']
        dev = row['DEV_CODE']
        current = row['CURRENT_STOCK']
        daily_avg = row['DAILY_AVG']
        days_to_next = row['NEXT_REPLENISH_DAYS']

        if days_to_next <= 0:
            continue

        if daily_avg <= 0:
            skipped_demand_zero += 1
            logger.debug(f"[V2] 跳过 {org}-{dev}: 日均需求={daily_avg:.4f} <= 0，期间需求为0")
            continue

        threshold = poisson.ppf(threshold_percentile, daily_avg)

        if current < threshold:
            triggered += 1
            total_mean = daily_avg * days_to_next
            target = poisson.ppf(target_percentile, total_mean)
            recommend_qty = max(0, target - current)

            best_days = days_to_next
            for i in range(1, int(days_to_next) + 1):
                demand_i = daily_avg * i
                if current >= poisson.ppf(target_percentile, demand_i):
                    best_days = i
                    break

            best_days = min(best_days, max_lead_time)
            recommend_date = (today + timedelta(days=best_days)).strftime('%Y-%m-%d')

            results.append({
                'ORG_NO': org,
                'DEV_CODE': dev,
                'CURRENT_STOCK': current,
                'PERIOD_DEMAND': row['PERIOD_DEMAND'],
                'NEXT_REPLENISH_DAYS': days_to_next,
                'THRESHOLD': threshold,
                'TARGET_STOCK': target,
                'RECOMMEND_QTY': recommend_qty,
                'RECOMMEND_DAYS': best_days,
                'RECOMMEND_DATE': recommend_date,
                'GLOBAL_SCHEME_ID': global_scheme_id
            })
            logger.info(f"[V2] 紧急补库建议: {org}-{dev} | 当前库存={current}, 日均需求={daily_avg:.2f}, "
                        f"门限={threshold:.2f}, 目标={target:.2f}, 建议数量={recommend_qty}, "
                        f"建议日期={recommend_date}, 提前天数={best_days}")
        else:
            skipped_stock_ok += 1
            logger.debug(f"[V2] 未触发紧急补库: {org}-{dev} | 当前库存={current}, 门限={threshold:.2f}")

    result_df = pd.DataFrame(results)
    total_checked = skipped_demand_zero + skipped_stock_ok + triggered
    logger.info(f"[V2] 遍历汇总: 共检查 {total_checked} 条 | "
                f"触发紧急补库 {triggered} 条 | "
                f"库存充足跳过 {skipped_stock_ok} 条 | "
                f"需求为0跳过 {skipped_demand_zero} 条")
    logger.info(f"[V2] 紧急补库检查完成，共生成 {len(result_df)} 条建议")
    return result_df


def run_emergency_replenishment_v2(
    snapshot_date: str = None,
    year_month: str = None
):
    """
    运行紧急补库 V2 完整流程。

    参数:
        snapshot_date: 库存快照日期，格式 'YYYY-MM-DD'，默认当天
        year_month:    补库计划年月，格式 'YYYYMM'，默认当前年月

    返回:
        dict: 执行结果
    """
    from backend.api.data_api.fetch_data import (
        query_adam_stock_count_sample_all,
        query_adam_plan_day_ias_pre_by_month,
        query_adam_wd_dmd_pre_by_year_month_and_pretype,
        query_adam_spec_code_config,
        insert_into_adam_plan_day_ias_pre
    )

    if snapshot_date is None:
        snapshot_date = datetime.now().strftime('%Y-%m-%d')
    if year_month is None:
        year_month = datetime.now().strftime('%Y%m')

    year = year_month[:4]
    month = year_month[4:6]

    logger.info(f"[V2] 开始紧急补库流程 | 快照日期: {snapshot_date}, 计划年月: {year_month}")

    # 1. 获取当前库存快照
    stock_df = query_adam_stock_count_sample_all()
    if stock_df.empty:
        logger.warning(f"[V2] 未找到库存快照，流程终止")
        return {'error': '未找到库存快照，流程终止'}

    logger.info(f"[V2] 库存快照记录数: {len(stock_df)}")

    # 2. 获取日补库计划
    daily_plan_df = query_adam_plan_day_ias_pre_by_month(year_month)
    if daily_plan_df.empty:
        logger.info(f"[V2] 未找到 {year_month} 的日补库计划，无需紧急补库")
        return {'message': f'当月无日补库计划数据，无需紧急补库', 'success': True}
    logger.info(f"[V2] 日补库计划记录数: {len(daily_plan_df)}")

    # 3. 获取日需求预测 (PRE_TYPE='05')
    daily_demand_df = query_adam_wd_dmd_pre_by_year_month_and_pretype(year, month, '05')
    if daily_demand_df.empty:
        logger.info(f"[V2] 未找到 {year_month} 的日需求预测，无需紧急补库")
        return {'message': f'当月无日需求预测数据，无需紧急补库', 'success': True}
    logger.info(f"[V2] 日需求预测记录数: {len(daily_demand_df)}")

    # 4. 执行紧急补库检查
    emergency_df = emergency_replenishment_check_v2(
        stock_df=stock_df,
        daily_plan_df=daily_plan_df,
        daily_demand_df=daily_demand_df,
        threshold_percentile=0.5,
        target_percentile=0.9,
        max_lead_time=7
    )

    if emergency_df.empty:
        logger.info("[V2] 无紧急补库建议，流程结束")
        return {'message': '当前库存充足，无需紧急补库', 'success': True}

    # 5. 获取设备规格映射
    spec_df = query_adam_spec_code_config()
    spec_map = spec_df[['DEV_CODE', 'DEV_CLS', 'DEV_CATEG']].drop_duplicates(subset=['DEV_CODE'])
    logger.info(f"[V2] 设备规格映射记录数: {len(spec_map)}")

    # 6. 构造待插入记录
    insert_records = []
    base_timestamp = int(time.time() * 1000)

    for idx, row in emergency_df.iterrows():
        dev_code = row['DEV_CODE']
        spec = spec_map[spec_map['DEV_CODE'] == dev_code]
        if spec.empty:
            logger.warning(f"[V2] 设备码 {dev_code} 无规格映射，跳过")
            continue
        dev_cls = spec.iloc[0]['DEV_CLS']
        dev_categ = spec.iloc[0]['DEV_CATEG']

        insert_records.append({
            'PLAN_MONTH_IAS_PRE_ID': base_timestamp + idx,
            'PRE_DATE': row['RECOMMEND_DATE'],
            'REC_ORG_NO': row['ORG_NO'],
            'DEV_CLS': dev_cls,
            'DEV_CATEG': dev_categ,
            'DEV_CODE': dev_code,
            'PLAN_IAS_NUM': row['RECOMMEND_QTY'],
            'EST_STOCK_NUM': None,
            'GLOBAL_SCHEME_ID': row['GLOBAL_SCHEME_ID'],
            'DAILY_PLAN_STATUS': '01',
            'REPLE_TASK_TYPE': '02',
            'TASK_SOURCE': '03'
        })

    if not insert_records:
        logger.warning("[V2] 无有效插入记录（可能因规格缺失）")
        return {'message': '紧急补库建议生成后无有效记录可插入', 'success': False}

    insert_df = pd.DataFrame(insert_records)
    logger.info(f"[V2] 准备插入 {len(insert_df)} 条紧急补库记录")
    logger.info(f"[V2] 表结构：{insert_df.columns}")

    result = {}
    try:
        result = insert_into_adam_plan_day_ias_pre(insert_df)
        logger.info(f"[V2] 插入结果: {result}")
    except Exception as e:
        logger.error(f"[V2] 插入失败: {e}", exc_info=True)

    return result
