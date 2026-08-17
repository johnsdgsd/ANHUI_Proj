"""
调拨场景二 — 【独立】缺货判定模块

输入: 库存快照(stock_df) + 日需求预测(daily_demand_df) + 日补库计划(daily_plan_df)
输出: 缺货组合列表 [(org, dev, qty, recommend_date), ...]

**阶段二独立维护**：缺货检测逻辑自一阶段 `EmergReplenish.EmergReplenishV2.emergency_replenishment_check_v2`
**复制**而来（用户确认 2026-08-07：阶段2紧急补库与阶段1完全独立，相同逻辑复制一份）。
阶段2与阶段1解耦，后续修改互不影响；本模块内做了 Decimal 兼容加固（阶段1直接复用时对 dmPython Decimal 输入会报错）。

本模块与紧急补库核心(emergency)/调拨核心(transfer)完全解耦：
不关心省中心、不关心调拨，只回答「哪些 (单位, 设备码) 缺货、缺多少、建议哪天补」。
"""
import calendar
import logging
from collections import defaultdict
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from scipy.stats import poisson

from backend.algorithm.transfer_scenario2.config import (
    THRESHOLD_PERCENTILE,
    TARGET_PERCENTILE,
    MAX_LEAD_TIME,
)

logger = logging.getLogger(__name__)


def _stockout_check(
    stock_df,
    daily_plan_df,
    daily_demand_df,
    threshold_percentile,
    target_percentile,
    max_lead_time,
    today=None,
):
    """缺货检测核心（自阶段1 EmergReplenishV2 复制，阶段2独立维护）。

    以库存快照为驱动：逐日累加真实日需求预测 + 下次补库日期，判断缺货风险。
    对每个 (ORG_NO, DEV_CODE):
        - check_days = 距下次补库天数（无计划→max_lead 窗口）
        - 窗口内需求总和 total，阈值 = Poisson(total) 的 α 分位
        - 当前库存 < 阈值 → 触发
        - 建议量 Q = max(0, Poisson(窗口累计需求) 的 β 分位 − 当前库存)
        - 建议日期 = today + 当前库存能覆盖的最大天数

    Returns:
        pd.DataFrame: ORG_NO, DEV_CODE, CURRENT_STOCK, PERIOD_DEMAND,
                      NEXT_REPLENISH_DAYS, THRESHOLD, TARGET_STOCK,
                      RECOMMEND_QTY, RECOMMEND_DAYS, RECOMMEND_DATE, GLOBAL_SCHEME_ID
    """
    if today is None:
        today = datetime.now().date()
    _, last_day = calendar.monthrange(today.year, today.month)
    month_end = today.replace(day=last_day)
    # 提前期不超过当月剩余天数
    max_lead = min(max_lead_time, (month_end - today).days)
    logger.info(
        f"[场景2缺货判定] 日期={today} 月末={month_end} | "
        f"max_lead={max_lead_time}->{max_lead}天 | α={threshold_percentile} β={target_percentile}")

    # ---- 1. 以库存快照为驱动（仅新表）----
    stock = stock_df.rename(columns={
        'MGT_ORG_CODE': 'ORG_NO', 'DEV_CODE_NO': 'DEV_CODE', 'STOCK_NUM': 'CURRENT_STOCK'})
    if 'OLD_NEW_FLAG' in stock.columns:
        stock = stock[stock['OLD_NEW_FLAG'] == '01'].copy()

    # ---- 2. 下次补库日期（无计划 → NaT，按 max_lead 窗口）----
    daily = daily_plan_df.rename(columns={'REC_ORG_NO': 'ORG_NO'})
    future_plan = pd.DataFrame()
    if daily is not None and not daily.empty:
        daily['PRE_DATE'] = pd.to_datetime(daily['PRE_DATE']).dt.date
        future_plan = daily[daily['PRE_DATE'] >= today]

    if not future_plan.empty and 'GLOBAL_SCHEME_ID' in future_plan.columns:
        global_scheme_id = future_plan['GLOBAL_SCHEME_ID'].iloc[0]
    else:
        global_scheme_id = -999

    next_repl = pd.DataFrame(columns=['ORG_NO', 'DEV_CODE', 'NEXT_DATE'])
    if not future_plan.empty:
        next_repl = future_plan.groupby(
            ['ORG_NO', 'DEV_CODE'], as_index=False)['PRE_DATE'].min()
        next_repl.rename(columns={'PRE_DATE': 'NEXT_DATE'}, inplace=True)

    merged = stock.merge(next_repl, on=['ORG_NO', 'DEV_CODE'], how='left')
    merged['NEXT_DATE'] = pd.to_datetime(merged['NEXT_DATE'])
    today_ts = pd.Timestamp(today)
    merged['NEXT_REPLENISH_DAYS'] = (merged['NEXT_DATE'] - today_ts).dt.days
    no_plan = merged['NEXT_DATE'].isna()
    if no_plan.any():
        logger.info(f"[场景2缺货判定] 无未来补库计划的库存记录: {int(no_plan.sum())} 条 "
                    f"(按 max_lead={max_lead} 天窗口检查)")

    # ---- 3. 构建逐日需求查找表 (ORG_NO, DEV_CODE) -> {day_offset: demand} ----
    demand = daily_demand_df.rename(columns={'PRE_NUM': 'DAILY_DEMAND'})
    if demand is not None and not demand.empty:
        demand = demand.groupby(
            ['PRE_DATE', 'ORG_NO', 'DEV_CODE'], as_index=False)['DAILY_DEMAND'].sum()
        demand['PRE_DATE'] = pd.to_datetime(demand['PRE_DATE']).dt.date
        demand = demand[(demand['PRE_DATE'] >= today)
                        & (demand['PRE_DATE'] <= today + timedelta(days=max_lead))]
        demand['DAY_OFFSET'] = (demand['PRE_DATE'] - today).apply(lambda x: x.days)

    demand_lookup = defaultdict(lambda: defaultdict(float))
    for _, r in demand.iterrows():
        # 强制 float: 兼容 dmPython Decimal 输入（阶段1原实现此处会报错）
        demand_lookup[(r['ORG_NO'], r['DEV_CODE'])][r['DAY_OFFSET']] += float(r['DAILY_DEMAND'])

    # ---- 4. 逐条判断 ----
    results = []
    for idx, row in merged.iterrows():
        org = row['ORG_NO']
        dev = row['DEV_CODE']
        current = float(row['CURRENT_STOCK'])
        next_date = row['NEXT_DATE']
        days_to_next = row['NEXT_REPLENISH_DAYS']

        # 确定检查天数窗口
        if pd.isna(next_date):
            check_days = max_lead
        else:
            check_days = int(min(max(days_to_next, 0), max_lead))
            if check_days <= 0:
                continue

        demand_days = demand_lookup.get((org, dev), {})
        daily_demands = [demand_days.get(d, 0) for d in range(1, check_days + 1)]

        # 窗口内需求总和用于触发判断（触发与目标库存统一口径：均基于窗口累计需求）
        period_total = sum(daily_demands)
        if period_total <= 0:
            continue

        threshold = poisson.ppf(threshold_percentile, period_total)
        if current >= threshold:
            continue

        # 触发: 逐日累计需求
        cumulative = list(np.cumsum(daily_demands))
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
            'GLOBAL_SCHEME_ID': global_scheme_id,
        })
        logger.info(
            f"[场景2缺货判定] 触发 {org} 设备码 {dev} | 当前库存 {current:.0f} "
            f"窗口需求总和 {period_total:.0f} 目标库存 {target:.0f} "
            f"缺货量 {recommend_qty:.0f} 建议日期 {recommend_date}")

    result_df = pd.DataFrame(results)
    logger.info(
        f"[场景2缺货判定] 完成: 触发 {len(result_df)} 个缺货组合")
    return result_df


def detect_shortage(
    stock_df,
    daily_plan_df,
    daily_demand_df,
    threshold_percentile=THRESHOLD_PERCENTILE,
    target_percentile=TARGET_PERCENTILE,
    max_lead_time=MAX_LEAD_TIME,
    today=None,
):
    """缺货判定：返回 [(org, dev, qty, recommend_date), ...]。

    qty 为缺货量（与紧急补库建议量一致）；recommend_date 为建议补库日，
    紧急补库分支作为 PRE_DATE。`today` 可选（默认当天），便于测试/指定快照日。

    Raises:
        ValueError: 库存快照为空（无法判定）
    """
    if stock_df is None or stock_df.empty:
        raise ValueError("库存快照为空，无法进行缺货判定")

    emerg_df = _stockout_check(
        stock_df,
        daily_plan_df,
        daily_demand_df,
        threshold_percentile=threshold_percentile,
        target_percentile=target_percentile,
        max_lead_time=max_lead_time,
        today=today,
    )
    if emerg_df is None or emerg_df.empty:
        logger.info("缺货判定: 无缺货组合")
        return []

    shortages = []
    for _, r in emerg_df.iterrows():
        shortages.append((
            str(r['ORG_NO']).strip(),
            r['DEV_CODE'],
            float(r['RECOMMEND_QTY']),
            str(r['RECOMMEND_DATE']),
        ))
    total_qty = sum(s[2] for s in shortages)
    logger.info(
        f"缺货判定: {len(shortages)} 个缺货组合, 总缺货量 {total_qty:.0f}")
    return shortages
