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


def get_month_workday_flags(year, month):
    """当月每一天是否为工作日（口径与排程一致，含五一/十一/元旦兜底）。返回长度=当月天数的 bool 列表。"""
    _, num_days = calendar.monthrange(year, month)
    flags = []
    for d in range(1, num_days + 1):
        day = datetime.date(year, month, d)
        # 五一、十一、元旦强硬限制兜底
        if month == 5 and d in [1, 2, 3]:
            flags.append(False)
        elif month == 10 and d in [1, 2, 3, 4, 5, 6, 7]:
            flags.append(False)
        elif month == 1 and d == 1:
            flags.append(False)
        elif HAS_CALENDAR:
            try:
                flags.append(chinese_calendar.is_workday(day))
            except:
                flags.append(day.weekday() < 5)
        else:
            flags.append(day.weekday() < 5)
    return flags


def GetArrPlan(LotList, TypeList, WorkDay_unused,
               end_index=None, start_index=None, pre_occupied=None, max_cap=2500):
    """
    核心排程求解器（两阶段通用，向后兼容）。

    参数：
        end_index:   一阶段截止自然日索引(0-based，含)。批次只能落 <= end_index；None=不限。
        start_index: 二阶段起始自然日索引(0-based，含)。批次只能落 >= start_index；None=不限。
        pre_occupied: {自然日索引(0-based): 已占箱数}，二阶段每日能力需扣减一阶段已占；None=全0。
        max_cap:      折扣后单日入库能力上限（箱）。

    硬约束：
        - 每个批次必须被安排在某一天，且落在允许的自然日范围内（时间窗口）。
        - 每日箱数 day_sums[j] + pre_occupied[j] <= max_cap（能力上限，含一阶段扣减）。
        - 目标函数沿用：压低峰值 + 平滑防扎堆 + 节假日每箱 10 万罚金。
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
    is_workday = get_month_workday_flags(year, month)

    NumDays = num_days
    BoxCls = np.zeros(LotNum)

    # 2. 批次转换箱数
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

    # 3. 时间窗口：自然日索引范围（工作日/节假日靠罚金软约束区分，装不下才溢出节假日）
    allowed_days = set(range(NumDays))

    if end_index is not None:
        allowed_days &= {j for j in range(NumDays) if j <= end_index}

    if start_index is not None:
        allowed_days &= {j for j in range(NumDays) if j >= start_index}

    workday_count = sum(is_workday)

    # 4. 每日能力上限（含一阶段已占扣减）
    pre_occupied = pre_occupied or {}
    day_caps = [max(0, max_cap - pre_occupied.get(j, 0)) for j in range(NumDays)]

    # 5. 创建线性规划问题
    prob = pulp.LpProblem("arriveplan_boxes_mixed", sense=pulp.LpMinimize)

    x = pulp.LpVariable.dicts("x", ((i, j) for i in range(LotNum) for j in range(NumDays)), cat='Binary')
    peak_1day = pulp.LpVariable("peak_1day", lowBound=0, cat='Continuous')
    peak_2days = pulp.LpVariable("peak_2days", lowBound=0, cat='Continuous')

    # 峰值物理上限（折扣后）
    prob += peak_1day <= max_cap

    # 约束：每个批次必须被安排在某一天（且落在允许日期内）
    for i in range(LotNum):
        for j in range(NumDays):
            if j not in allowed_days:
                prob += x[i, j] == 0
        prob += lpSum([x[i, j] for j in range(NumDays)]) == 1

    # 每日箱数统计变量
    day_sums = [lpSum([BoxCls[i] * x[i, j] for i in range(LotNum)]) for j in range(NumDays)]

    # 峰值约束 + 每日能力（扣减一阶段已占）
    for j in range(NumDays):
        prob += day_sums[j] <= peak_1day
        prob += day_sums[j] <= day_caps[j]

    for j in range(NumDays - 1):
        prob += day_sums[j] + day_sums[j + 1] <= peak_2days

    # 平滑防扎堆
    if workday_count == 0:
        workday_count = NumDays
    avg_boxes = sum(BoxCls) / workday_count

    y_dev = pulp.LpVariable.dicts("y_dev", range(NumDays), lowBound=0, cat='Continuous')
    for j in range(NumDays):
        prob += day_sums[j] - avg_boxes <= y_dev[j]
        prob += avg_boxes - day_sums[j] <= y_dev[j]

    # 节假日惩罚金机制：非工作日分配的箱数总量
    holiday_penalty_boxes = lpSum(
        [BoxCls[i] * x[i, j] for i in range(LotNum) for j in range(NumDays) if not is_workday[j]])

    # 目标函数：压低峰值 + 防扎堆 + 极力避免占用节假日(每箱10万罚金)
    prob += peak_2days + peak_1day + 0.5 * lpSum([y_dev[j] for j in range(NumDays)]) + 100000 * holiday_penalty_boxes

    # 6. 求解
    solver = pulp.PULP_CBC_CMD(msg=False, options=['ratioGap=0.01', 'sec=30'])
    status = prob.solve(solver)

    if status != pulp.LpStatusOptimal:
        logging.warning(
            f"⚠️ [到货排程] 求解状态: {pulp.LpStatus[status]}。本月工作日与节假日总产能均已逼近物理极限！")

    # 7. 组装结果映射回真实日期
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
