import numpy as np
import pandas as pd
import pulp
from pulp import lpSum
import datetime
import calendar
import math
import logging

try:
    import chinese_calendar

    HAS_CALENDAR = True
except ImportError:
    HAS_CALENDAR = False


def get_week_of_month(date):
    first_day = date.replace(day=1)
    dom = date.day
    adjusted_dom = dom + first_day.weekday()
    return math.ceil(adjusted_dom / 7)


def GetArrPlan(LotList, TypeList, WorkDay_unused):
    """
    核心排程求解器
    (单日绝对不超 2500 箱，优先填满工作日，超额自动溢出至节假日)
    """
    LotNum = len(LotList)
    if LotNum == 0:
        return LotList

    # 1. 动态解析当月所有自然日
    ref_date_str = LotList['PLAN_ARR_DATE'].iloc[0]
    if isinstance(ref_date_str, str):
        ref_dt = datetime.datetime.strptime(ref_date_str, '%Y-%m-%d %H:%M:%S')
    else:
        ref_dt = ref_date_str

    year = ref_dt.year
    month = ref_dt.month
    _, num_days = calendar.monthrange(year, month)

    all_dates = [datetime.date(year, month, d) for d in range(1, num_days + 1)]

    # 2. 标记每一天是否为工作日
    is_workday = []
    for d in all_dates:
        # 五一、十一、元旦强硬限制兜底
        if month == 5 and d.day in [1, 2, 3]:
            is_workday.append(False)
        elif month == 10 and d.day in [1, 2, 3, 4, 5, 6, 7]:
            is_workday.append(False)
        elif month == 1 and d.day == 1:
            is_workday.append(False)
        elif HAS_CALENDAR:
            try:
                is_workday.append(chinese_calendar.is_workday(d))
            except:
                is_workday.append(d.weekday() < 5)
        else:
            is_workday.append(d.weekday() < 5)

    NumDays = num_days
    BoxCls = np.zeros(LotNum)

    # 3. 批次转换箱数
    for i in range(LotNum):
        LotSize = LotList['PLAN_ARR_NUM'].iloc[i]
        dev_code = LotList['DEV_CODE_NO'].iloc[i]

        match_col = 'DEV_CODE_NO' if 'DEV_CODE_NO' in TypeList.columns else 'DEV_CODE'
        val_col = 'PACK_BOX_NUM' if 'PACK_BOX_NUM' in TypeList.columns else 'UNITPERBOX'

        matches = TypeList.loc[TypeList[match_col] == dev_code, val_col].values
        if len(matches) > 0 and pd.notnull(matches[0]) and matches[0] > 0:
            BoxCap = matches[0]
        else:
            BoxCap = 5

        BoxCls[i] = math.ceil(LotSize / float(BoxCap))

    # 4. 创建线性规划问题 (全自然日映射)
    prob = pulp.LpProblem("arriveplan_boxes_mixed", sense=pulp.LpMinimize)

    # x[i, j] 二元变量：表示第 i 个批次是否在当月第 j 天到货
    x = pulp.LpVariable.dicts("x", ((i, j) for i in range(LotNum) for j in range(NumDays)), cat='Binary')

    peak_1day = pulp.LpVariable("peak_1day", lowBound=0, cat='Continuous')
    peak_2days = pulp.LpVariable("peak_2days", lowBound=0, cat='Continuous')

    # 【物理硬红线】：无论是工作日还是节假日，单日卸货极限死死锁在 2500 箱！
    prob += peak_1day <= 2500

    # 约束：每个批次必须被安排在某一天
    for i in range(LotNum):
        prob += lpSum([x[i, j] for j in range(NumDays)]) == 1

    # 每日箱数统计变量
    day_sums = [lpSum([BoxCls[i] * x[i, j] for i in range(LotNum)]) for j in range(NumDays)]

    # 峰值约束
    for j in range(NumDays):
        prob += day_sums[j] <= peak_1day

    for j in range(NumDays - 1):
        prob += day_sums[j] + day_sums[j + 1] <= peak_2days

    # 平滑防扎堆
    workday_count = sum(is_workday)
    if workday_count == 0: workday_count = NumDays
    avg_boxes = sum(BoxCls) / workday_count

    y_dev = pulp.LpVariable.dicts("y_dev", range(NumDays), lowBound=0, cat='Continuous')
    for j in range(NumDays):
        prob += day_sums[j] - avg_boxes <= y_dev[j]
        prob += avg_boxes - day_sums[j] <= y_dev[j]

    # 【核心新增】：节假日惩罚金机制
    # 计算所有被分配在非工作日(假日)的箱数总量
    holiday_penalty_boxes = lpSum(
        [BoxCls[i] * x[i, j] for i in range(LotNum) for j in range(NumDays) if not is_workday[j]])

    # 目标函数：压低峰值 + 防扎堆 + 【极力避免占用节假日(每箱10万罚金)】
    prob += peak_2days + peak_1day + 0.5 * lpSum([y_dev[j] for j in range(NumDays)]) + 100000 * holiday_penalty_boxes

    # 5. 求解
    solver = pulp.PULP_CBC_CMD(msg=False, options=['ratioGap=0.01', 'sec=30'])
    status = prob.solve(solver)

    if status != pulp.LpStatusOptimal:
        logging.warning(
            f"⚠️ [到货排程] 求解状态: {pulp.LpStatus[status]}。本月工作日与节假日总产能均已逼近 2500 箱/天的物理极限！")

    # 6. 组装结果映射回真实日期
    result_dates = []
    for i in range(LotNum):
        assigned_day_idx = 0
        for j in range(NumDays):
            if x[i, j].varValue and x[i, j].varValue >= 0.5:
                assigned_day_idx = j
                break
        result_dates.append(all_dates[assigned_day_idx])

    LotList['PLAN_ARR_DATE'] = result_dates
    LotList['PLAN_ARR_DATE'] = pd.to_datetime(LotList['PLAN_ARR_DATE'], format='%Y-%m-%d')
    LotList['WEEK_SEQ'] = LotList['PLAN_ARR_DATE'].apply(get_week_of_month)

    return LotList