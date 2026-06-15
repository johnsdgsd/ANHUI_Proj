import pandas as pd
import numpy as np
from scipy.stats import poisson
from datetime import datetime, timedelta
import logging
import time
from collections import defaultdict

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
    紧急补库监控 V2：以库存快照为驱动，逐日累加真实日需求预测。

    参数:
        stock_df:       当前库存快照，列: MGT_ORG_CODE, DEV_CODE_NO, STOCK_NUM
        daily_plan_df:  日补库计划表，列: PRE_DATE, REC_ORG_NO, DEV_CODE, PLAN_IAS_NUM
        daily_demand_df:日需求预测表，列: PRE_DATE, ORG_NO, DEV_CODE, PRE_NUM
        threshold_percentile: 触发阈值分位数，默认 0.5
        target_percentile:    目标服务水平分位数，默认 0.9
        max_lead_time:        最大提前天数，默认 7
    """
    today = datetime.now().date()
    import calendar
    _, last_day = calendar.monthrange(today.year, today.month)
    month_end = today.replace(day=last_day)
    # 提前期不超过当月剩余天数
    max_lead = min(max_lead_time, (month_end - today).days)
    logger.info(f"[V2] 开始 | 日期={today} 月末={month_end.strftime('%Y-%m-%d')} | "
                f"max_lead={max_lead_time} -> 有效={max_lead}天 | α={threshold_percentile} β={target_percentile}")

    # ================================================================
    # 1. 以库存快照为驱动
    # ================================================================
    stock = stock_df.rename(columns={
        'MGT_ORG_CODE': 'ORG_NO',
        'DEV_CODE_NO': 'DEV_CODE',
        'STOCK_NUM': 'CURRENT_STOCK'
    })
    before_stock = len(stock)
    if 'OLD_NEW_FLAG' in stock.columns:
        stock = stock[stock['OLD_NEW_FLAG'] == '01'].copy()
        logger.info(f"[V2] 库存数据: {before_stock} -> {len(stock)} (仅新表)")
    else:
        logger.warning("[V2] 库存表缺少 OLD_NEW_FLAG 列")
    logger.info(f"[V2] 库存快照明细(前10):\n{stock[['ORG_NO', 'DEV_CODE', 'CURRENT_STOCK']].head(10).to_string()}")

    # ================================================================
    # 2. 下次补库日期查找（无计划 → NaT，默认视作无约束）
    # ================================================================
    daily = daily_plan_df.rename(columns={'REC_ORG_NO': 'ORG_NO'})
    future_plan = pd.DataFrame()
    if not daily.empty:
        daily['PRE_DATE'] = pd.to_datetime(daily['PRE_DATE']).dt.date
        future_plan = daily[daily['PRE_DATE'] >= today]
    logger.info(f"[V2] 未来日补库计划: {len(future_plan)} 条")

    if not future_plan.empty and 'GLOBAL_SCHEME_ID' in future_plan.columns:
        global_scheme_id = future_plan['GLOBAL_SCHEME_ID'].iloc[0]
    else:
        global_scheme_id = -999

    next_repl = pd.DataFrame(columns=['ORG_NO', 'DEV_CODE', 'NEXT_DATE'])
    if not future_plan.empty:
        next_repl = future_plan.groupby(['ORG_NO', 'DEV_CODE'], as_index=False)['PRE_DATE'].min()
        next_repl.rename(columns={'PRE_DATE': 'NEXT_DATE'}, inplace=True)
    logger.info(f"[V2] 有下次补库日期的组合数: {len(next_repl)}")

    # left join: 保留所有库存记录
    merged = stock.merge(next_repl, on=['ORG_NO', 'DEV_CODE'], how='left')
    merged['NEXT_DATE'] = pd.to_datetime(merged['NEXT_DATE'])
    today_ts = pd.Timestamp(today)
    merged['NEXT_REPLENISH_DAYS'] = (merged['NEXT_DATE'] - today_ts).dt.days

    no_plan = merged['NEXT_DATE'].isna()
    logger.info(f"[V2] 无未来补库计划的库存记录: {no_plan.sum()} 条 (将按 max_lead={max_lead} 天窗口检查)")

    logger.info(f"[V2] 下次补库明细(前10):\n{merged[['ORG_NO', 'DEV_CODE', 'NEXT_DATE', 'NEXT_REPLENISH_DAYS']].head(10).to_string()}")

    # ================================================================
    # 3. 构建逐日需求查找表 (ORG_NO, DEV_CODE) -> {day_offset: demand}
    # ================================================================
    demand = daily_demand_df.rename(columns={'PRE_NUM': 'DAILY_DEMAND'})
    if not demand.empty:
        # 折叠 BUS_TYPE
        demand = demand.groupby(['PRE_DATE', 'ORG_NO', 'DEV_CODE'], as_index=False)['DAILY_DEMAND'].sum()
        demand['PRE_DATE'] = pd.to_datetime(demand['PRE_DATE']).dt.date
        # 只保留未来 max_lead 天内的需求
        demand = demand[(demand['PRE_DATE'] >= today) & (demand['PRE_DATE'] <= today + timedelta(days=max_lead))]
        demand['DAY_OFFSET'] = (demand['PRE_DATE'] - today).apply(lambda x: x.days)

    logger.info(f"[V2] 日需求预测(聚合后): {len(demand)} 条 | 日期范围: {today} ~ {today + timedelta(days=max_lead)}")

    # 构建嵌套字典: (org, dev) -> {day_offset: demand}
    demand_lookup = defaultdict(lambda: defaultdict(float))
    for _, r in demand.iterrows():
        demand_lookup[(r['ORG_NO'], r['DEV_CODE'])][r['DAY_OFFSET']] += r['DAILY_DEMAND']

    if not demand.empty:
        logger.info(f"[V2] 日需求明细(前10):\n{demand[['ORG_NO', 'DEV_CODE', 'PRE_DATE', 'DAY_OFFSET', 'DAILY_DEMAND']].head(10).to_string()}")
        # 诊断: 日需求中的 (ORG_NO, DEV_CODE) 在库存中是否存在
        demand_keys = set(zip(demand['ORG_NO'], demand['DEV_CODE']))
        stock_keys = set(zip(merged['ORG_NO'], merged['DEV_CODE']))
        match_keys = demand_keys & stock_keys
        logger.info(f"[V2] 需求(ORG_NO,DEV_CODE)数: {len(demand_keys)} | 库存(ORG_NO,DEV_CODE)数: {len(stock_keys)} | 匹配数: {len(match_keys)}")
        if not match_keys:
            logger.warning(f"[V2] 需求和库存无任何匹配! 需求样例: {list(demand_keys)[:5]}")

    # ================================================================
    # 4. 逐条判断
    # ================================================================
    skipped_no_demand = 0
    skipped_stock_ok = 0
    triggered = 0
    results = []

    for idx, row in merged.iterrows():
        org = row['ORG_NO']
        dev = row['DEV_CODE']
        current = row['CURRENT_STOCK']
        next_date = row['NEXT_DATE']
        days_to_next = row['NEXT_REPLENISH_DAYS']

        # 确定检查天数窗口
        if pd.isna(next_date):
            check_days = max_lead
        else:
            check_days = int(min(max(days_to_next, 0), max_lead))
            if check_days <= 0:
                skipped_no_demand += 1
                continue

        # 逐日取需求
        demand_days = demand_lookup.get((org, dev), {})
        daily_demands = [demand_days.get(d, 0) for d in range(1, check_days + 1)]

        # 窗口内最大单日需求用于触发判断
        peak_demand = max(daily_demands) if daily_demands else 0
        if peak_demand <= 0:
            skipped_no_demand += 1
            continue

        threshold = poisson.ppf(threshold_percentile, peak_demand)

        if current >= threshold:
            skipped_stock_ok += 1
            continue

        # 触发：逐日累计需求
        triggered += 1
        cumulative = list(np.cumsum(daily_demands))
        period_total = cumulative[-1]
        target = poisson.ppf(target_percentile, period_total)
        recommend_qty = max(0, target - current)

        best_days = 1
        for d in range(check_days):
            if current >= poisson.ppf(target_percentile, cumulative[d]):
                best_days = d + 1
            else:
                break
        best_days = min(best_days, max_lead)
        recommend_date = (today + timedelta(days=best_days)).strftime('%Y-%m-%d')

        results.append({
            'ORG_NO': org,
            'DEV_CODE': dev,
            'CURRENT_STOCK': current,
            'PERIOD_DEMAND': period_total,
            'NEXT_REPLENISH_DAYS': check_days,
            'THRESHOLD': threshold,
            'TARGET_STOCK': target,
            'RECOMMEND_QTY': recommend_qty,
            'RECOMMEND_DAYS': best_days,
            'RECOMMEND_DATE': recommend_date,
            'GLOBAL_SCHEME_ID': global_scheme_id
        })

        logger.info(f"[V2] 触发 {org}-{dev} | 库存={current} | "
                    f"峰值需求={peak_demand:.0f} 目标库存={target:.0f} | "
                    f"建议补库={recommend_qty} | 建议日期={recommend_date} (d={best_days})")

    # ================================================================
    # 5. 汇总
    # ================================================================
    result_df = pd.DataFrame(results)
    total = skipped_no_demand + skipped_stock_ok + triggered
    logger.info(f"[V2] 判断完成: 总计 {total} | 触发 {triggered} | 库存充足 {skipped_stock_ok} | 无需求 {skipped_no_demand}")
    logger.info(f"[V2] 生成紧急补库建议 {len(result_df)} 条")
    return result_df


def run_emergency_replenishment_v2(
    snapshot_date: str = None,
    year_month: str = None
):
    """运行紧急补库 V2 完整流程。"""
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

    logger.info(f"[V2] 流程启动 | 快照日期={snapshot_date} | 计划年月={year_month}")

    stock_df = query_adam_stock_count_sample_all()
    if stock_df.empty:
        return {'error': '未找到库存快照'}

    daily_plan_df = query_adam_plan_day_ias_pre_by_month(year_month)
    if daily_plan_df.empty:
        logger.warning("[V2] 当月无日补库计划数据，有补库计划的记录的期间需求视为 0")
    else:
        logger.info(f"[V2] 日补库计划: {len(daily_plan_df)} 条")

    daily_demand_df = query_adam_wd_dmd_pre_by_year_month_and_pretype(year, month, '05')
    if daily_demand_df.empty:
        logger.warning("[V2] 当月无日需求预测数据，缺失日期的需求视为 0")
    else:
        logger.info(f"[V2] 日需求预测: {len(daily_demand_df)} 条")

    emergency_df = emergency_replenishment_check_v2(
        stock_df=stock_df,
        daily_plan_df=daily_plan_df,
        daily_demand_df=daily_demand_df,
        threshold_percentile=0.5,
        target_percentile=0.9,
        max_lead_time=7
    )

    if emergency_df.empty:
        logger.info("[V2] 流程结束: 无紧急补库建议")
        return {'success': True, 'message': '无需紧急补库'}

    spec_df = query_adam_spec_code_config()
    spec_map = spec_df[['DEV_CODE', 'DEV_CLS', 'DEV_CATEG']].drop_duplicates(subset=['DEV_CODE'])

    insert_records = []
    base_timestamp = int(time.time() * 1000)
    for idx, row in emergency_df.iterrows():
        dev_code = row['DEV_CODE']
        spec = spec_map[spec_map['DEV_CODE'] == dev_code]
        if spec.empty:
            continue
        insert_records.append({
            'PLAN_MONTH_IAS_PRE_ID': base_timestamp + idx,
            'PRE_DATE': row['RECOMMEND_DATE'],
            'REC_ORG_NO': row['ORG_NO'],
            'DEV_CLS': spec.iloc[0]['DEV_CLS'],
            'DEV_CATEG': spec.iloc[0]['DEV_CATEG'],
            'DEV_CODE': dev_code,
            'PLAN_IAS_NUM': row['RECOMMEND_QTY'],
            'EST_STOCK_NUM': None,
            'GLOBAL_SCHEME_ID': row['GLOBAL_SCHEME_ID'],
            'DAILY_PLAN_STATUS': '01',
            'REPLE_TASK_TYPE': '02',
            'TASK_SOURCE': '03'
        })

    if not insert_records:
        return {'success': False, 'message': '无有效记录可插入'}

    insert_df = pd.DataFrame(insert_records)
    from backend.api.data_api.fetch_data import query_pk_next
    insert_df['PLAN_MONTH_IAS_PRE_ID'] = [int(x) for x in query_pk_next("SEQ_ADAM_PLAN_DAY_IAS_PRE_EMERGENCY", len(insert_df))]

    try:
        result = insert_into_adam_plan_day_ias_pre(insert_df)
        logger.info(f"[V2] 插入成功: {len(insert_df)} 条")
        return result
    except Exception as e:
        logger.error(f"[V2] 插入失败: {e}")
        return {'success': False, 'message': str(e)}
