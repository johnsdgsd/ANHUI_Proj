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
        return str(m['DEV_CATEG'].iloc[0]).replace('.0', '').strip().zfill(2) if not m.empty else '01'

    def get_veri_line_cat(dev_cat):
        mapping = {'01': '01', '02': '02', '03': '02', '04': '02', '05': '05', '06': '06', '07': '07'}
        return mapping.get(dev_cat, dev_cat)

    def is_workday_safe(date_obj):
        if HAS_CALENDAR:
            try: return chinese_calendar.is_workday(date_obj)
            except NotImplementedError: return date_obj.weekday() < 5
        return date_obj.weekday() < 5

    def advance_workdays(start_date, days_to_add):
        curr = start_date
        added = 0
        while added < days_to_add:
            curr += timedelta(days=1)
            if is_workday_safe(curr): added += 1
        return curr

    logging.info(">>> [Phase 1] 正在执行批次检定推演...")

    DailyCap = {}
    if not DeviceCaps.empty:
        for _, row in DeviceCaps.iterrows():
            veri_cat = str(row['VERI_CATEG']).strip().zfill(2)
            lines = int(pd.to_numeric(row['VDRILINE_NUM'], errors='coerce') or 0)
            posi_num = int(pd.to_numeric(row['POSI_NUM'], errors='coerce') or 1)
            chk_num = int(pd.to_numeric(row['POSI_CHECK_NUM'], errors='coerce') or 200)
            DailyCap[veri_cat] = DailyCap.get(veri_cat, 0) + (lines * posi_num * chk_num)

    LotList = LotList.sort_values(by=['PLAN_DATE', 'ARR_BATCH_NO'])
    CategoryAvailDate = {}
    DetectPlanResult = []
    StockReadyTimeline = []

    for idx, row in LotList.iterrows():
        dev_code = row['DEV_CODE_NO']
        qty = int(row['RemNum'])
        if qty <= 0: continue

        dev_cat = get_cat(dev_code)
        veri_cat = get_veri_line_cat(dev_cat)
        cap = DailyCap.get(veri_cat, 5000)
        days_needed = math.ceil(qty / cap)
        if days_needed < 1: days_needed = 1

        arr_dt_pd = pd.to_datetime(row['PLAN_DATE'])
        arr_dt = datetime(arr_dt_pd.year, arr_dt_pd.month, arr_dt_pd.day) if not pd.isna(arr_dt_pd) else sim_start_dt
        earliest_start_dt = arr_dt + timedelta(days=1)
        base_start = max(earliest_start_dt, CategoryAvailDate.get(veri_cat, sim_start_dt))

        while not is_workday_safe(base_start): base_start += timedelta(days=1)
        if base_start > month_end_dt:
            base_start = month_end_dt
            while not is_workday_safe(base_start) and base_start > sim_start_dt: base_start -= timedelta(days=1)

        start_dt = base_start
        end_dt = advance_workdays(start_dt, days_needed)

        if end_dt > month_end_dt:
            end_dt = month_end_dt
            while not is_workday_safe(end_dt) and end_dt > start_dt: end_dt -= timedelta(days=1)
            if end_dt <= start_dt: end_dt = start_dt

        CategoryAvailDate[veri_cat] = end_dt

        DetectPlanResult.append({
            'ARR_BATCH_NO': row['ARR_BATCH_NO'],
            'DEV_CODE': dev_code,
            'DEV_CLS': get_cls(dev_code),
            'DEV_CATEG': dev_cat,
            'DETECT_PLAN_NUM': qty,
            'DETECT_BGN_DATE': start_dt.strftime('%Y-%m-%d'),
            'DETECT_END_DATE': end_dt.strftime('%Y-%m-%d'),
            'PLAN_STATUS': '0'  # <--- 【新增】：明确设置状态为0
        })

        clean_date = datetime(end_dt.year, end_dt.month, end_dt.day)
        StockReadyTimeline.append({'date': clean_date, 'dev_code': dev_code, 'qty': qty})

    work_days_list = [sim_start_dt + timedelta(days=i) for i in range(total_sim_days) if is_workday_safe(sim_start_dt + timedelta(days=i))]
    actual_deliv_days = len(work_days_list) or 1
    if not work_days_list: work_days_list = [sim_start_dt]

    PathInfo, DelivPlan, DelivCalendar, Ls = GetDelivPlan(Demands, LocationNum, TypeList, SubTypeList, actual_deliv_days, VeUnitPrice, VeTypeNum, VNums, VeCap, DMAT)

    GlobalDelivPlan = []
    CurrentStock, RemDemands = InitQuaStock.copy(), Demands.copy()
    DevCodeToIndex = {SubTypeList.loc[i, 'DEV_CODE_NO']: i for i in range(SubTypeNum)}

    for wd_idx, current_date in enumerate(work_days_list):
        for item in StockReadyTimeline:
            if item['date'] == current_date:
                idx = DevCodeToIndex.get(item['dev_code'])
                if idx is not None: CurrentStock[idx] += item['qty']

        for l in range(len(Ls)):
            ve_type = DelivCalendar[l, wd_idx]
            if ve_type > 0:
                path_ind = Ls[l]
                trip = DelivPlan[(DelivPlan['PathInd'] == path_ind) & (DelivPlan['VeType'] == ve_type)].iloc[0]
                path_nodes, de_nums = trip['PlanPath'], trip['DeNum']
                simulate_stock, simulate_rem_demands, total_boxes_actual, details_data = CurrentStock.copy(), RemDemands.copy(), 0, []

                for step_idx, loc_1based in enumerate(path_nodes):
                    boxes_needed, org_no = de_nums[step_idx], locations.loc[loc_1based, 'ORG_NO']
                    for sub_idx in range(SubTypeNum):
                        if simulate_rem_demands[loc_1based - 1, sub_idx] > 0 and simulate_stock[sub_idx] > 0:
                            unit_arr = TypeList.loc[TypeList['DEV_CODE_NO'] == SubTypeList.loc[sub_idx, 'DEV_CODE_NO'], 'UnitPerBox'].values
                            unit_per_box = unit_arr[0] if len(unit_arr) > 0 else 5
                            qty_needed = min(boxes_needed * unit_per_box, simulate_rem_demands[loc_1based - 1, sub_idx], simulate_stock[sub_idx])
                            if qty_needed > 0:
                                used_boxes = math.ceil(qty_needed / unit_per_box)
                                total_boxes_actual += used_boxes
                                boxes_needed -= used_boxes
                                details_data.append({
                                    'REC_ORG_NO': org_no, 'DEV_CODE': SubTypeList.loc[sub_idx, 'DEV_CODE_NO'],
                                    'DEV_CLS': get_cls(SubTypeList.loc[sub_idx, 'DEV_CODE_NO']), 'DEV_CATEG': get_cat(SubTypeList.loc[sub_idx, 'DEV_CODE_NO']),
                                    'PLAN_DIST_NUM': qty_needed, 'PLAN_BOX_NUM': used_boxes, 'DIST_SEQ': step_idx + 1, 'LOAD_SEQ': len(path_nodes) - step_idx
                                })
                                simulate_stock[sub_idx] -= qty_needed
                                simulate_rem_demands[loc_1based - 1, sub_idx] -= qty_needed
                            if boxes_needed <= 0: break

                max_cap_boxes = VeCap[int(ve_type) - 1]
                actual_load_rate = total_boxes_actual / max_cap_boxes if max_cap_boxes > 0 else 0
                if actual_load_rate < 0.6 and wd_idx < actual_deliv_days - 3:
                    if wd_idx + 1 < actual_deliv_days: DelivCalendar[l, wd_idx + 1] = ve_type
                    continue
                if details_data:
                    CurrentStock, RemDemands = simulate_stock, simulate_rem_demands
                    master_data = {'CAR_TYPE': f"0{int(ve_type)}", 'PLAN_DIST_DATE': current_date.strftime('%Y-%m-%d'), 'PRICE': trip['Price'], 'LOAD_RATE': f"{actual_load_rate * 100:.1f}%"}
                    GlobalDelivPlan.append({'master': master_data, 'details': details_data})

    return pd.DataFrame(DetectPlanResult), GlobalDelivPlan