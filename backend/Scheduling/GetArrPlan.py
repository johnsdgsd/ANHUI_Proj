import numpy as np
import pandas as pd
import pulp
from pulp import lpSum
import datetime
import chinese_calendar
import math
import logging

def get_nth_workday(n, date_str):
    try:
        target_datetime = datetime.datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S')
        year = target_datetime.year
        month = target_datetime.month
        first_day = datetime.date(year, month, 1)

        if month == 12:
            last_day = datetime.date(year + 1, 1, 1) - datetime.timedelta(days=1)
        else:
            last_day = datetime.date(year, month + 1, 1) - datetime.timedelta(days=1)

        workday_count = 0
        current_day = first_day
        while current_day <= last_day:
            if chinese_calendar.is_workday(current_day):
                workday_count += 1
                if workday_count == n:
                    return current_day
            current_day += datetime.timedelta(days=1)
        return None
    except Exception as e:
        logging.error(f"日期推算错误: {e}")
        return None

def get_week_of_month(date):
    first_day = date.replace(day=1)
    dom = date.day
    adjusted_dom = dom + first_day.weekday()
    return math.ceil(adjusted_dom / 7)

def GetArrPlan(LotList, TypeList, WorkDay):
    """
    核心排程求解器 (单日绝对不超 2500 箱，且强制平滑防止扎堆)
    """
    LotNum = len(LotList)
    Ds = np.ones(LotNum) * WorkDay
    Es = np.ones(LotNum)
    BoxCls = np.zeros(LotNum)

    # 1. 将批次只数转换为箱数
    for i in range(1, len(LotList) + 1):
        LotSize = LotList['PLAN_ARR_NUM'].iloc[i - 1]
        dev_code = LotList['DEV_CODE_NO'].iloc[i - 1]

        match_col = 'DEV_CODE_NO' if 'DEV_CODE_NO' in TypeList.columns else 'DEV_CODE'
        val_col = 'PACK_BOX_NUM' if 'PACK_BOX_NUM' in TypeList.columns else 'UNITPERBOX'

        matches = TypeList.loc[TypeList[match_col] == dev_code, val_col].values
        if len(matches) > 0 and pd.notnull(matches[0]) and matches[0] > 0:
            BoxCap = matches[0]
        else:
            BoxCap = 5

        BoxCls[i - 1] = math.ceil(LotSize / float(BoxCap))

    # 2. 创建线性规划问题
    prob = pulp.LpProblem("arriveplan_boxes", sense=pulp.LpMinimize)

    XNum = LotNum * WorkDay
    x = pulp.LpVariable.dicts("x", range(1, XNum + 3), lowBound=0, cat='Integer')

    lb = np.zeros(XNum + 2)
    ub = np.ones(XNum + 2)
    ub[XNum] = sum(BoxCls)
    ub[XNum + 1] = sum(BoxCls)

    for i in range(1, XNum + 3):
        x[i].lowBound = lb[i - 1]
        x[i].upBound = ub[i - 1]

    # 【还原硬红线】：单日极限死死锁在 2500 箱！没有任何商量余地！
    max_allowed_daily_boxes = 2500
    prob += x[XNum + 1] <= max_allowed_daily_boxes

    # 约束：每个批次必须安排在某一天
    for i in range(LotNum):
        prob += lpSum([x[i * WorkDay + j] for j in range(int(Es[i]), int(Ds[i]) + 1)]) == 1

    # 约束：每天分配的批次累加箱数 <= 每日最大箱数变量
    for i in range(WorkDay):
        prob += lpSum([BoxCls[j] * x[i + j * WorkDay + 1] for j in range(LotNum)]) <= x[XNum + 1]

    # 约束：连续两天分配的批次累加箱数 <= 连续两日最大箱数变量
    for i in range(WorkDay - 1):
        prob += lpSum([BoxCls[j] * x[i + j * WorkDay + 1] for j in range(LotNum)]) + \
                lpSum([BoxCls[j] * x[i + 1 + j * WorkDay + 1] for j in range(LotNum)]) <= x[XNum + 2]

    # 【平滑防扎堆逻辑保留】：引入偏差惩罚变量，逼迫小单子散开
    avg_boxes = sum(BoxCls) / max(1, WorkDay)
    y_dev = pulp.LpVariable.dicts("y_dev", range(WorkDay), lowBound=0, cat='Continuous')

    for i in range(WorkDay):
        day_sum = lpSum([BoxCls[j] * x[i + j * WorkDay + 1] for j in range(LotNum)])
        prob += day_sum - avg_boxes <= y_dev[i]
        prob += avg_boxes - day_sum <= y_dev[i]

    # 目标函数：在遵守 2500 极限的前提下，压低峰值，惩罚扎堆 (权重 0.5)
    prob += x[XNum + 2] + x[XNum + 1] + 0.5 * lpSum([y_dev[i] for i in range(WorkDay)])

    # 3. 求解
    solver = pulp.PULP_CBC_CMD(msg=False, options=['ratioGap=0.01', 'sec=15'])
    status = prob.solve(solver)

    if status != pulp.LpStatusOptimal:
        logging.error(f"❌ [求解器报错] 排程失败！状态: {pulp.LpStatus[status]}。可能原因：当月总到货量远超 2500箱×工作天数 的极限！")

    # 4. 组装结果矩阵
    table = pd.DataFrame(index=range(LotNum), columns=range(WorkDay))

    for i in range(1, XNum + 1):
        row = (i - 1) // WorkDay
        col = (i - 1) % WorkDay
        table.iloc[row, col] = x[i].varValue

    result = pd.DataFrame(columns=range(3), index=range(LotNum))

    for i in range(LotNum):
        indices = table.columns[table.iloc[i] == 1].tolist()
        result.iloc[i, 0] = LotList['PLAN_ARR_NUM'].iloc[i]

        if len(indices) > 0:
            result.iloc[i, 1] = indices[0] + 1
        else:
            result.iloc[i, 1] = WorkDay

    reference_date_str = LotList['PLAN_ARR_DATE'].iloc[0]
    result[2] = result[1].apply(lambda nth: get_nth_workday(int(nth), reference_date_str) if pd.notnull(nth) else None)

    LotList['PLAN_ARR_DATE'] = result[2].values
    LotList['PLAN_ARR_DATE'] = pd.to_datetime(LotList['PLAN_ARR_DATE'], format='%Y-%m-%d')
    LotList['WEEK_SEQ'] = LotList['PLAN_ARR_DATE'].apply(get_week_of_month)

    return LotList