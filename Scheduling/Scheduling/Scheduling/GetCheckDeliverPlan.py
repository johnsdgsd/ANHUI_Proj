import numpy as np
import pandas as pd
import logging
import sys
from datetime import datetime, timedelta
import math

try:
    import chinese_calendar

    HAS_CALENDAR = True
except ImportError:
    HAS_CALENDAR = False

from backend.Scheduling.GetDelivPlan import GetDelivPlan


def GetCheckDeliverPlan(Demands, InitQuaStock, LotList, DeviceCaps, SubTypeList, TypeList, DMAT, LocationNum, VeCap,
                        VNums, VeUnitPrice, VeTypeNum, sim_start_date_str, total_sim_days, record_start_date_str,
                        locations):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s", stream=sys.stdout)

    sim_start_dt = datetime.strptime(sim_start_date_str, '%Y-%m-%d')
    month_end_dt = sim_start_dt + timedelta(days=total_sim_days - 1)
    SubTypeNum = len(SubTypeList)

    def get_cls(code):
        m = SubTypeList[SubTypeList['DEV_CODE_NO'] == code]
        return str(m['DEV_CLS'].iloc[0]).replace('.0', '').strip().zfill(2) if not m.empty else '01'

    def get_cat(code):
        m = SubTypeList[SubTypeList['DEV_CODE_NO'] == code]
        if not m.empty and pd.notnull(m['DEV_CATEG'].iloc[0]):
            return str(m['DEV_CATEG'].iloc[0]).strip()
        return '01_01'

    def get_desc(code):
        m = SubTypeList[SubTypeList['DEV_CODE_NO'] == code]
        return str(m['DEV_CODE_DESC'].iloc[0]).strip() if not m.empty and 'DEV_CODE_DESC' in m.columns else ''

    def get_detect_time(code, veri_type):
        m = SubTypeList[SubTypeList['DEV_CODE_NO'] == code]
        if not m.empty:
            col = 'LAB_DUR' if str(veri_type) == '01' else 'AUTO_DUR'
            if col in m.columns and pd.notnull(m[col].iloc[0]):
                try:
                    val = float(m[col].iloc[0])
                    if val > 0: return val / 60.0
                except:
                    pass
        return 1.5

    def is_workday_safe(date_obj):
        if HAS_CALENDAR:
            try:
                return chinese_calendar.is_workday(date_obj)
            except NotImplementedError:
                return date_obj.weekday() < 5
        return date_obj.weekday() < 5

    last_workday = month_end_dt
    while not is_workday_safe(last_workday) and last_workday > sim_start_dt:
        last_workday -= timedelta(days=1)

    def safe_val(val):
        if pd.isna(val): return None
        s = str(val).replace('.0', '').strip()
        return s if s not in ('nan', 'None', '') else None

    logging.info(">>> [Phase 1] 正在执行批次检定推演...")

    BatchCap = {}
    DevCatToVeriCat = {}
    VeriCatToType = {}

    if not DeviceCaps.empty:
        for _, row in DeviceCaps.iterrows():
            veri_cat = str(row['VERI_CATEG']).strip().zfill(2)
            veri_type = str(row['VERI_TYPE']).strip().zfill(2)
            VeriCatToType[veri_cat] = veri_type

            lines = int(pd.to_numeric(row['VDRILINE_NUM'], errors='coerce') or 0)
            posi_num = int(pd.to_numeric(row['POSI_NUM'], errors='coerce') or 1)
            chk_num = int(pd.to_numeric(row['POSI_CHECK_NUM'], errors='coerce') or 200)

            BatchCap[veri_cat] = BatchCap.get(veri_cat, 0) + (lines * posi_num * chk_num)

            dev_categ_str = str(row.get('DEV_CATEG', '')).strip()
            if dev_categ_str and dev_categ_str not in ('nan', 'None'):
                for dc in dev_categ_str.replace('，', ',').split(','):
                    dc = dc.strip()
                    if dc: DevCatToVeriCat[dc] = veri_cat

    def get_veri_line_cat(dev_cat):
        return DevCatToVeriCat.get(dev_cat, dev_cat)

    LotList = LotList.sort_values(by=['PLAN_DATE', 'ARR_BATCH_NO'])
    DetectPlanResult = []
    StockReadyTimeline = {}

    DailyTimeLoadMap = {}
    DailyCapMap = {}

    for idx, row in LotList.iterrows():
        dev_code = row['DEV_CODE_NO']
        qty = int(row['RemNum'])
        if qty <= 0: continue

        dev_cat = get_cat(dev_code)
        veri_cat = get_veri_line_cat(dev_cat)

        batch_cap = BatchCap.get(veri_cat, 5000)
        veri_type = VeriCatToType.get(veri_cat, '01')
        detect_time = get_detect_time(dev_code, veri_type)

        pieces_per_hour = batch_cap / detect_time if detect_time > 0 else 9999
        cap_8h = math.floor(pieces_per_hour * 8.0)
        if cap_8h < 1: cap_8h = 1

        arr_dt_pd = pd.to_datetime(row['PLAN_DATE'])
        arr_dt = datetime(arr_dt_pd.year, arr_dt_pd.month, arr_dt_pd.day) if not pd.isna(arr_dt_pd) else sim_start_dt

        earliest_start_dt = max(arr_dt + timedelta(days=1), sim_start_dt)

        if earliest_start_dt > last_workday:
            earliest_start_dt = last_workday
        while not is_workday_safe(earliest_start_dt) and earliest_start_dt < last_workday:
            earliest_start_dt += timedelta(days=1)

        qty_rem = qty
        curr_load_dt = earliest_start_dt
        start_dt = None
        end_dt = None

        while qty_rem > 0:
            if curr_load_dt > last_workday:
                curr_load_dt = last_workday

            curr_str = curr_load_dt.strftime('%Y-%m-%d')
            if curr_str not in DailyTimeLoadMap: DailyTimeLoadMap[curr_str] = {}
            if curr_str not in DailyCapMap: DailyCapMap[curr_str] = {}

            already_used = DailyTimeLoadMap[curr_str].get(veri_cat, 0.0)

            if curr_load_dt == last_workday:
                alloc_qty = qty_rem
            else:
                avail_hours = max(0.0, 24.0 - already_used)
                qty_can_do = math.floor(avail_hours * pieces_per_hour)
                alloc_qty = min(qty_rem, qty_can_do)

            if alloc_qty > 0:
                if start_dt is None: start_dt = curr_load_dt
                end_dt = curr_load_dt

                hours_added = alloc_qty / pieces_per_hour
                DailyTimeLoadMap[curr_str][veri_cat] = already_used + hours_added
                DailyCapMap[curr_str][veri_cat] = cap_8h
                qty_rem -= alloc_qty

            if qty_rem > 0:
                curr_load_dt += timedelta(days=1)
                while not is_workday_safe(curr_load_dt) and curr_load_dt <= last_workday:
                    curr_load_dt += timedelta(days=1)

        if start_dt is None: start_dt = earliest_start_dt
        if end_dt is None: end_dt = earliest_start_dt

        DetectPlanResult.append({
            'ARR_BATCH_NO': safe_val(row.get('ARR_BATCH_NO')),
            'BATCH_PLAN_ARR_ID': safe_val(row.get('BATCH_PLAN_ARR_ID')),
            'DEV_CODE': dev_code, 'DEV_CODE_DESC': get_desc(dev_code), 'DEV_CLS': get_cls(dev_code),
            'DEV_CATEG': dev_cat, 'DETECT_PLAN_NUM': qty,
            'DETECT_BGN_DATE': start_dt.strftime('%Y-%m-%d'), 'DETECT_END_DATE': end_dt.strftime('%Y-%m-%d'),
            'PLAN_STAT': '01', 'DAY_DETECT_PLAN_PRE_ID': row.get('DAY_DETECT_PLAN_PRE_ID'),
            'VERI_TYPE': str(veri_type).zfill(2)
        })

        clean_date = datetime(end_dt.year, end_dt.month, end_dt.day)
        if clean_date not in StockReadyTimeline: StockReadyTimeline[clean_date] = []
        StockReadyTimeline[clean_date].append({'dev_code': dev_code, 'qty': qty})

    WorkArrangeResult = []
    for i in range(total_sim_days):
        curr_date = sim_start_dt + timedelta(days=i)
        curr_str = curr_date.strftime('%Y-%m-%d')
        for v_cat, b_cap in BatchCap.items():
            hours_used = DailyTimeLoadMap.get(curr_str, {}).get(v_cat, 0.0)

            day_cap_8h = DailyCapMap.get(curr_str, {}).get(v_cat, int(b_cap * (8.0 / 1.5)))

            if hours_used <= 0.01:
                w_flag, d_dur, final_cap = '01', '0h', 0
            else:
                w_flag = '02'
                if hours_used <= 8.5:
                    d_dur, final_cap = '8h', day_cap_8h
                elif hours_used <= 12.5:
                    d_dur, final_cap = '12h', int(day_cap_8h * 1.5)
                else:
                    d_dur, final_cap = '24h', int(day_cap_8h * 3)

            WorkArrangeResult.append({
                'VERI_CATEG': v_cat, 'WORK_DATE': curr_str, 'WORK_FLAG': w_flag,
                'DETECT_DUR': d_dur, 'CAPACITY_NUM': final_cap
            })

    # ==================== 配送模块 ====================
    work_days_list = [sim_start_dt + timedelta(days=i) for i in range(total_sim_days) if
                      is_workday_safe(sim_start_dt + timedelta(days=i))]
    actual_deliv_days = len(work_days_list) or 1
    if not work_days_list: work_days_list = [sim_start_dt]

    PathInfo, DelivPlan, DelivCalendar, Ls = GetDelivPlan(Demands, LocationNum, TypeList, SubTypeList,
                                                          actual_deliv_days, VeUnitPrice, VeTypeNum, VNums, VeCap, DMAT)

    GlobalDelivPlan = []
    CurrentStock, RemDemands = InitQuaStock.copy(), Demands.copy()
    DevCodeToIndex = {SubTypeList.loc[i, 'DEV_CODE_NO']: i for i in range(SubTypeNum)}

    for wd_idx, current_date in enumerate(work_days_list):
        if current_date in StockReadyTimeline:
            for item in StockReadyTimeline[current_date]:
                idx = DevCodeToIndex.get(item['dev_code'])
                if idx is not None: CurrentStock[idx] += item['qty']

        for l in range(len(Ls)):
            ve_type = DelivCalendar[l, wd_idx]
            if ve_type > 0:
                path_ind = Ls[l]
                trip = DelivPlan[(DelivPlan['PathInd'] == path_ind) & (DelivPlan['VeType'] == ve_type)].iloc[0]
                path_nodes, de_nums = trip['PlanPath'], trip['DeNum']
                simulate_stock, simulate_rem_demands, total_boxes_actual, details_data = CurrentStock.copy(), RemDemands.copy(), 0, []

                prev_node = 0
                for step_idx, loc_1based in enumerate(path_nodes):
                    dist_segment = DMAT[prev_node, loc_1based] if isinstance(DMAT, np.ndarray) else DMAT.values[
                        prev_node, loc_1based]
                    prev_node = loc_1based

                    # 此时 de_nums 里面装的是体积配额
                    vol_needed = de_nums[step_idx]

                    boxes_needed, org_no = de_nums[step_idx], locations.loc[loc_1based, 'ORG_NO']
                    for sub_idx in range(SubTypeNum):
                        if simulate_rem_demands[loc_1based - 1, sub_idx] > 0 and simulate_stock[sub_idx] > 0:
                            dev_code_str = SubTypeList.loc[sub_idx, 'DEV_CODE_NO']
                            unit_arr = TypeList.loc[TypeList['DEV_CODE_NO'] == dev_code_str, 'UnitPerBox'].values
                            unit_per_box = unit_arr[0] if len(unit_arr) > 0 else 5

                            # 获取体积系数
                            vol_mult = 2.5 if get_cls(dev_code_str) == '02' else 1.0

                            # 计算当前剩余空间最多能装多少数量
                            max_allowable_boxes = math.floor(vol_needed / vol_mult)
                            if max_allowable_boxes == 0 and vol_needed > 0.1:
                                max_allowable_boxes = 1  # 兜底：只要还有空间，至少允许塞一箱
                            max_allowable_qty = max_allowable_boxes * unit_per_box

                            qty_needed = min(simulate_rem_demands[loc_1based - 1, sub_idx], simulate_stock[sub_idx])
                            if qty_needed > max_allowable_qty:
                                qty_needed = max_allowable_qty

                            if qty_needed > 0:
                                used_boxes = math.ceil(qty_needed / unit_per_box)
                                vol_used = used_boxes * vol_mult  # 计算扣减体积

                                total_boxes_actual += vol_used  # 在车辆容量中，按2.5倍累加消耗
                                vol_needed -= vol_used

                                details_data.append({
                                    'REC_ORG_NO': org_no, 'DEV_CODE': dev_code_str,
                                    'DEV_CLS': get_cls(dev_code_str),
                                    'DEV_CATEG': get_cat(dev_code_str),
                                    'PLAN_DIST_NUM': qty_needed,
                                    'PLAN_BOX_NUM': used_boxes,  # 给到数据库的：绝对真实的物理箱数 (1箱)
                                    'VOL_BOX_NUM': float(vol_used),  # 给到计费模块的：体积折算箱数 (2.5箱)
                                    'DIST_SEQ': step_idx + 1,
                                    'LOAD_SEQ': len(path_nodes) - step_idx,
                                    'DIST_SEGMENT': float(dist_segment)
                                })
                                simulate_stock[sub_idx] -= qty_needed
                                simulate_rem_demands[loc_1based - 1, sub_idx] -= qty_needed

                        if vol_needed <= 0: break

                # 获取这趟车原计划要拉多少体积的货
                planned_vol = sum(de_nums)
                # 计算“满足率”（实际拉的货 / 计划拉的货）
                fulfillment_rate = total_boxes_actual / planned_vol if planned_vol > 0 else 1.0

                # total_boxes_actual 已经是包含了折算后的体积箱数了，可以直接与容量约束比对
                max_cap_boxes = VeCap[int(ve_type) - 1]
                actual_load_rate = total_boxes_actual / max_cap_boxes if max_cap_boxes > 0 else 0

                # 【核心修复点：切断“无脑往月末顺延”的死循环】
                # 只有当：实际装载率不足 80% 【且】 是因为没货（库存满足率 < 80%）导致的空车，才顺延！
                # 那些本来就是小尾货、但是货已经齐了的单子，今天立刻发走，绝不允许推到 27 号！
                if actual_load_rate < 0.8 and fulfillment_rate < 0.8 and wd_idx < actual_deliv_days - 3:
                    if wd_idx + 1 < actual_deliv_days: DelivCalendar[l, wd_idx + 1] = ve_type
                    continue

                if details_data:
                    CurrentStock, RemDemands = simulate_stock, simulate_rem_demands

                    unit_price = float(VeUnitPrice[int(ve_type) - 1]) if len(VeUnitPrice) >= int(ve_type) and float(
                        VeUnitPrice[int(ve_type) - 1]) > 0 else 0.0695

                    master_data = {'CAR_TYPE': f"0{int(ve_type)}", 'PLAN_DIST_DATE': current_date.strftime('%Y-%m-%d'),
                                   'PRICE': trip['Price'], 'LOAD_RATE': f"{actual_load_rate * 100:.1f}%",
                                   'UNIT_PRICE': unit_price}
                    GlobalDelivPlan.append({'master': master_data, 'details': details_data})

    return pd.DataFrame(DetectPlanResult), GlobalDelivPlan, pd.DataFrame(WorkArrangeResult)