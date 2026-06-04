import pandas as pd
import numpy as np
from scipy.stats import poisson
from datetime import datetime, timedelta
import logging
import time  # 新增：用于生成唯一时间戳ID

logger = logging.getLogger(__name__)


def emergency_replenishment_check(
        stock_df: pd.DataFrame,  # 当前库存快照，列: MGT_ORG_CODE, DEV_CODE_NO, STOCK_NUM
        daily_plan_df: pd.DataFrame,  # 日补库计划表，列: PRE_DATE, REC_ORG_NO, DEV_CODE, PLAN_IAS_NUM
        monthly_demand_df: pd.DataFrame,  # 月度总需求量，列: REC_ORG_NO, DEV_CODE, PLAN_IAS_NUM
        days_in_month: int = 30,
        threshold_percentile: float = 0.5,
        target_percentile: float = 0.9,
        max_lead_time: int = 7
) -> pd.DataFrame:
    """
    紧急补库监控：基于当前库存、日补库计划（获取下次补库日期）和月度总需求，返回需要紧急补库的记录。
    输出列: ORG_NO, DEV_CODE, CURRENT_STOCK, MONTHLY_DEMAND, NEXT_REPLENISH_DAYS,
           THRESHOLD, TARGET_STOCK, RECOMMEND_QTY, RECOMMEND_DAYS, RECOMMEND_DATE
    """
    today = datetime.now().date()
    logger.info(f"开始紧急补库检查，日期: {today}")

    # 统一列名映射（根据实际表结构调整）
    # 库存表：MGT_ORG_CODE -> ORG_NO, DEV_CODE_NO -> DEV_CODE, STOCK_NUM -> CURRENT_STOCK
    stock = stock_df.rename(columns={
        'MGT_ORG_CODE': 'ORG_NO',
        'DEV_CODE_NO': 'DEV_CODE',
        'STOCK_NUM': 'CURRENT_STOCK'
    })
    # 月度需求表：REC_ORG_NO -> ORG_NO, PLAN_IAS_NUM -> MONTHLY_DEMAND
    monthly = monthly_demand_df.rename(columns={
        'REC_ORG_NO': 'ORG_NO',
        'PLAN_IAS_NUM': 'MONTHLY_DEMAND'
    })[['ORG_NO', 'DEV_CODE', 'MONTHLY_DEMAND']]  # 保留 DEV_CODE 原列名
    # 日补库计划表：REC_ORG_NO -> ORG_NO
    daily = daily_plan_df.rename(columns={'REC_ORG_NO': 'ORG_NO'})

    # 检查必要的列是否存在
    required_stock_cols = ['ORG_NO', 'DEV_CODE', 'CURRENT_STOCK']
    required_monthly_cols = ['ORG_NO', 'DEV_CODE', 'MONTHLY_DEMAND']
    for col in required_stock_cols:
        if col not in stock.columns:
            raise KeyError(f"库存表缺少列: {col}")
    for col in required_monthly_cols:
        if col not in monthly.columns:
            raise KeyError(f"月度需求表缺少列: {col}")

    logger.info(f"库存记录数: {len(stock)}")
    logger.info(f"月度需求记录数: {len(monthly)}")
    logger.info(f"日补库计划记录数: {len(daily)}")

    # 1. 从日补库计划中提取每个 (ORG_NO, DEV_CODE) 的下一次补库日期（大于等于今天的最小PRE_DATE）
    daily['PRE_DATE'] = pd.to_datetime(daily['PRE_DATE']).dt.date
    future_plan = daily[daily['PRE_DATE'] >= today]
    logger.info(f"未来日补库计划记录数: {len(future_plan)}")
    if future_plan.empty:
        logger.warning("没有未来的日补库计划，无法计算下次补库日期，流程终止")
        return pd.DataFrame()

    # ===================== 只提取一次全局ID =====================
    if 'GLOBAL_SCHEME_ID' in future_plan.columns:
        global_scheme_id = future_plan['GLOBAL_SCHEME_ID'].iloc[0]  # 全表统一用一个
    else:
        global_scheme_id = -999  # 兜底
    # ===========================================================

    next_replenish = future_plan.groupby(['ORG_NO', 'DEV_CODE'], as_index=False)['PRE_DATE'].min()
    next_replenish.rename(columns={'PRE_DATE': 'NEXT_DATE'}, inplace=True)
    logger.info(f"获取到下次补库日期的组合数: {len(next_replenish)}")

    # 2. 合并数据
    merged = stock.merge(monthly, on=['ORG_NO', 'DEV_CODE'], how='inner')
    logger.info(f"合并库存与月度需求后记录数: {len(merged)}")
    if merged.empty:
        logger.warning("库存和月度需求无匹配记录，流程终止")
        return pd.DataFrame()
    merged = merged.merge(next_replenish, on=['ORG_NO', 'DEV_CODE'], how='left')
    # 如果没有未来补库计划，直接过滤（不再默认设为30天）
    merged = merged[~merged['NEXT_DATE'].isna()].copy()
    if merged.empty:
        logger.warning("无匹配的未来补库计划记录，流程终止")
        return pd.DataFrame()
    # 确保 NEXT_DATE 是 datetime 类型
    merged['NEXT_DATE'] = pd.to_datetime(merged['NEXT_DATE'])
    # 将 today 转换为 pandas Timestamp
    today_ts = pd.Timestamp(today)
    merged['NEXT_REPLENISH_DAYS'] = (merged['NEXT_DATE'] - today_ts).dt.days.clip(lower=0)
    merged['DAILY_AVG'] = merged['MONTHLY_DEMAND'] / days_in_month

    logger.info(f"合并全部数据后记录数: {len(merged)}")

    results = []
    for idx, row in merged.iterrows():
        org = row['ORG_NO']
        dev = row['DEV_CODE']
        current = row['CURRENT_STOCK']
        daily_avg = row['DAILY_AVG']
        days_to_next = row['NEXT_REPLENISH_DAYS']

        # 严格规则：只有下次正常补库 ≤ max_lead_time 天才允许紧急补库
        if days_to_next > max_lead_time:
            logger.debug(
                f"跳过 {org}-{dev}: 下次补库 {days_to_next} 天 > 最大提前天数 {max_lead_time}，不触发紧急补库"
            )
            continue

        if days_to_next <= 0:
            logger.debug(f"跳过 {org}-{dev}: 下次补库天数={days_to_next} <= 0")
            continue

        # 计算库存安全阈值
        threshold = poisson.ppf(threshold_percentile, daily_avg * 1)

        # 库存低于阈值才需要紧急补库
        if current < threshold:
            total_mean = daily_avg * days_to_next
            target = poisson.ppf(target_percentile, total_mean)
            recommend_qty = max(0, target - current)

            # 建议补库天数 强制 ≤ max_lead_time（最多提前7天）
            best_days = days_to_next
            for i in range(1, min(days_to_next, max_lead_time) + 1):
                demand_i = daily_avg * i
                if current >= poisson.ppf(target_percentile, demand_i):
                    best_days = i
                    break

            # 最终建议天数绝对不超过 max_lead_time
            best_days = min(best_days, max_lead_time)

            recommend_date = (today + timedelta(days=best_days)).strftime('%Y-%m-%d')
            results.append({
                'ORG_NO': org,
                'DEV_CODE': dev,
                'CURRENT_STOCK': current,
                'MONTHLY_DEMAND': row['MONTHLY_DEMAND'],
                'NEXT_REPLENISH_DAYS': days_to_next,
                'THRESHOLD': threshold,
                'TARGET_STOCK': target,
                'RECOMMEND_QTY': recommend_qty,
                'RECOMMEND_DAYS': best_days,
                'RECOMMEND_DATE': recommend_date,
                'GLOBAL_SCHEME_ID': global_scheme_id  # 使用统一全局ID
            })
            logger.info(f"紧急补库建议: {org}-{dev} | 当前库存={current}, 日均需求={daily_avg:.2f}, "
                        f"门限={threshold:.2f}, 目标={target:.2f}, 建议数量={recommend_qty}, "
                        f"建议日期={recommend_date}, 提前天数={best_days}")
        else:
            logger.debug(f"未触发紧急补库: {org}-{dev} | 当前库存={current}, 门限={threshold:.2f}")

    result_df = pd.DataFrame(results)
    logger.info(f"紧急补库检查完成，共生成 {len(result_df)} 条建议")
    return result_df


def run_emergency_replenishment(
    snapshot_date: str = None,
    year_month: str = None
) :
    """
    运行紧急补库完整流程：获取数据、检查、插入建议。

    参数:
        snapshot_date: 库存快照日期，格式 'YYYY-MM-DD'，默认当天
        year_month: 补库计划年月，格式 'YYYYMM'，默认当前年月

    返回:
        DataFrame: 紧急补库建议（未插入前的原始建议）
    """
    from backend.api.data_api.fetch_data import (
        query_adam_stock_count_sample_all,   #TODO 这里库存信息表可能是需要按照单位层级汇总到市县---不用汇总了
        query_adam_plan_day_ias_pre_by_month,
        query_adam_plan_month_ias_pre,
        query_adam_spec_code_config,
        insert_into_adam_plan_day_ias_pre   # 假设存在此插入函数
    )

    # 1. 参数默认值
    if snapshot_date is None:
        snapshot_date = datetime.now().strftime('%Y-%m-%d')
    if year_month is None:
        year_month = datetime.now().strftime('%Y%m')

    logger.info(f"开始紧急补库流程 | 快照日期: {snapshot_date}, 计划年月: {year_month}")

    # 2. 获取当前库存快照（过滤到指定日期）
    stock_df = query_adam_stock_count_sample_all()
    # stock_all['UPDATE_DATE'] = pd.to_datetime(stock_all['UPDATE_TIME']).dt.date
    # stock_df = stock_all[stock_all['UPDATE_DATE'] == pd.to_datetime(snapshot_date).date()]
    if stock_df.empty:
        logger.warning(f"未找到 {snapshot_date} 的库存快照，流程终止")
        return {'error': f'未找到 {snapshot_date} 的库存快照，流程终止'}

    logger.info(f"库存快照记录数: {len(stock_df)}")

    # 3. 获取日补库计划
    daily_plan_df = query_adam_plan_day_ias_pre_by_month(year_month)
    if daily_plan_df.empty:
        logger.info(f"未找到 {year_month} 的日补库计划，无需紧急补库")
        return {'message': f'当月无日补库计划数据，无需紧急补库', 'success': True}
    logger.info(f"日补库计划记录数: {len(daily_plan_df)}")

    # 4. 获取月度总需求量
    monthly_demand_df = query_adam_plan_month_ias_pre(pre_year=year_month[:4],pre_month=year_month[4:6])
    if monthly_demand_df.empty:
        logger.info(f"未找到 {year_month} 的月度需求量，无需紧急补库")
        return {'message': f'当月无月度需求数据，无需紧急补库', 'success': True}
    logger.info(f"月度需求量记录数: {len(monthly_demand_df)}")

    # 5. 执行紧急补库检查
    emergency_df = emergency_replenishment_check(
        stock_df=stock_df,
        daily_plan_df=daily_plan_df,
        monthly_demand_df=monthly_demand_df,
        days_in_month=30,
        threshold_percentile=0.5,
        target_percentile=0.9,
        max_lead_time=5
    )

    if emergency_df.empty:
        logger.info("无紧急补库建议，流程结束")
        return {'message': '当前库存充足，无需紧急补库', 'success': True}

    # 6. 获取设备规格映射（补充 DEV_CLS, DEV_CATEG）
    spec_df = query_adam_spec_code_config()
    spec_map = spec_df[['DEV_CODE', 'DEV_CLS', 'DEV_CATEG']].drop_duplicates(subset=['DEV_CODE'])
    logger.info(f"设备规格映射记录数: {len(spec_map)}")

    # 7. 构造待插入记录（完全对齐 ADAM_PLAN_DAY_IAS_PRE 表结构）
    insert_records = []
    # 用当前时间戳作为基础，保证ID唯一
    base_timestamp = int(time.time() * 1000)  # 毫秒级时间戳，避免重复

    for idx, row in emergency_df.iterrows():
        dev_code = row['DEV_CODE']
        spec = spec_map[spec_map['DEV_CODE'] == dev_code]
        if spec.empty:
            logger.warning(f"设备码 {dev_code} 无规格映射，跳过")
            continue
        dev_cls = spec.iloc[0]['DEV_CLS']
        dev_categ = spec.iloc[0]['DEV_CATEG']

        # 按表结构生成字段
        insert_records.append({
            'PLAN_MONTH_IAS_PRE_ID': base_timestamp + idx,  # 时间戳自增ID
            'PRE_DATE': row['RECOMMEND_DATE'],
            'REC_ORG_NO': row['ORG_NO'],
            'DEV_CLS': dev_cls,
            'DEV_CATEG': dev_categ,
            'DEV_CODE': dev_code,
            'PLAN_IAS_NUM': row['RECOMMEND_QTY'],
            'EST_STOCK_NUM': None,                # 预计库存不填
            'GLOBAL_SCHEME_ID': row['GLOBAL_SCHEME_ID'],  # 统一全局ID
            'DAILY_PLAN_STATUS': '01',           # 未确认
            'REPLE_TASK_TYPE': '02',              # 紧急补库类型
            'TASK_SOURCE': '03'                   # 算法生成
        })

    if not insert_records:
        logger.warning("无有效插入记录（可能因规格缺失）")
        return {'message': '紧急补库建议生成后无有效记录可插入', 'success': False}


    insert_df = pd.DataFrame(insert_records)
    logger.info(f"准备插入 {len(insert_df)} 条紧急补库记录，字段完全对齐 ADAM_PLAN_DAY_IAS_PRE 表")
    logger.info(f'表结构：{insert_df.columns}')
    # 8. 插入数据库
    result = {}
    try:
        result = insert_into_adam_plan_day_ias_pre(insert_df)
        print()  # 测试用，可注释
        logger.info(f"插入结果: {result}")
    except Exception as e:
        logger.error(f"插入失败: {e}", exc_info=True)
        # 可在此处降级处理（如逐条插入或记录到错误表）

    return result
