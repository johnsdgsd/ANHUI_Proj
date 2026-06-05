import numpy as np
import pandas as pd
import logging
import sys
from datetime import datetime, timedelta
import math
from collections import defaultdict
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
    all_days = [sim_start_dt + timedelta(days=i) for i in range(total_sim_days)]

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
        if date_obj.month == 5 and date_obj.day in [1, 2, 3]: return False
        if date_obj.month == 10 and date_obj.day in [1, 2, 3, 4, 5, 6, 7]: return False
        if date_obj.month == 1 and date_obj.day == 1: return False
        if HAS_CALENDAR:
            try:
                return chinese_calendar.is_workday(date_obj)
            except NotImplementedError:
                return date_obj.weekday() < 5
        return date_obj.weekday() < 5

    def safe_val(val):
        if pd.isna(val): return None
        return str(val).replace('.0', '').strip() if str(val) not in ('nan', 'None', '') else None

    # =========================================================================
    # 阶段一：先生成配送方案
    # =========================================================================
    logging.info(">>> [阶段一] 生成精确到每一辆车的配送路线与日历分配...")
    work_days_list = [d for d in all_days if is_workday_safe(d)]
    actual_deliv_days = len(work_days_list) or 1
    if not work_days_list: work_days_list = [sim_start_dt]

    ScheduledRoutes = GetDelivPlan(Demands, LocationNum, TypeList, SubTypeList,
                                   actual_deliv_days, VeUnitPrice, VeTypeNum, VNums, VeCap, DMAT)

    GlobalDelivPlan = []
    Sim_Demands = Demands.copy()

    Total_Delivery_Needed = np.zeros(SubTypeNum)
    Last_Delivery_Date = {}
    DevCodeToIndex = {SubTypeList.loc[i, 'DEV_CODE_NO']: i for i in range(SubTypeNum)}

    for trip in ScheduledRoutes:
        wd_idx = trip.get('schedule_day_idx', 0)
        current_date = work_days_list[wd_idx]
        ve_type = trip.get('vehicle_type', 1)
        path_nodes = [cid for cid, _ in trip['deliveries']]
        de_nums = [amt for _, amt in trip['deliveries']]

        total_vol_used = 0.0
        max_cap_boxes = float(VeCap[int(ve_type) - 1])
        details_data = []
        prev_node = 0

        for step_idx, loc_1based in enumerate(path_nodes):
            vol_needed = de_nums[step_idx]
            dist_segment = DMAT[prev_node, loc_1based] if isinstance(DMAT, np.ndarray) else DMAT.values[
                prev_node, loc_1based]
            prev_node = loc_1based
            org_no = locations.loc[loc_1based, 'ORG_NO']

            for sub_idx in range(SubTypeNum):
                if Sim_Demands[loc_1based - 1, sub_idx] > 0 and vol_needed > 0.0001:
                    dev_code_str = SubTypeList.loc[sub_idx, 'DEV_CODE_NO']
                    unit_arr = TypeList.loc[TypeList['DEV_CODE_NO'] == dev_code_str, 'UnitPerBox'].values
                    unit_per_box = unit_arr[0] if len(unit_arr) > 0 else 5
                    vol_mult = 2.5 if get_cls(dev_code_str) == '02' else 1.0

                    remaining_cap = max_cap_boxes - total_vol_used
                    max_boxes_phys = math.floor((remaining_cap + 0.0001) / vol_mult)
                    max_boxes_quota = math.ceil((vol_needed - 0.0001) / vol_mult)

                    allowable_boxes = max(0, min(max_boxes_quota, max_boxes_phys))
                    qty_needed = min(Sim_Demands[loc_1based - 1, sub_idx], allowable_boxes * unit_per_box)

                    if qty_needed > 0:
                        used_boxes = math.ceil(qty_needed / unit_per_box)
                        vol_used = used_boxes * vol_mult

                        if total_vol_used + vol_used <= max_cap_boxes + 0.0001:
                            total_vol_used += vol_used
                            vol_needed -= vol_used
                            Sim_Demands[loc_1based - 1, sub_idx] -= qty_needed

                            details_data.append({
                                'REC_ORG_NO': org_no, 'DEV_CODE': dev_code_str,
                                'DEV_CLS': get_cls(dev_code_str), 'DEV_CATEG': get_cat(dev_code_str),
                                'PLAN_DIST_NUM': qty_needed, 'PLAN_BOX_NUM': used_boxes,
                                'VOL_BOX_NUM': float(vol_used), 'DIST_SEQ': step_idx + 1,
                                'LOAD_SEQ': len(path_nodes) - step_idx,
                                'DIST_SEGMENT': float(dist_segment)
                            })
                            Total_Delivery_Needed[sub_idx] += qty_needed

                            if sub_idx not in Last_Delivery_Date or current_date > Last_Delivery_Date[sub_idx]:
                                Last_Delivery_Date[sub_idx] = current_date

        if details_data:
            actual_load_rate = min(1.0, total_vol_used / max_cap_boxes) if max_cap_boxes > 0 else 0
            unit_price = float(VeUnitPrice[int(ve_type) - 1]) if len(VeUnitPrice) >= int(ve_type) and float(
                VeUnitPrice[int(ve_type) - 1]) > 0 else 0.0695
            actual_price = sum(d['PLAN_BOX_NUM'] * d['DIST_SEGMENT'] * unit_price for d in details_data)

            master_data = {'CAR_TYPE': f"0{int(ve_type)}", 'PLAN_DIST_DATE': current_date.strftime('%Y-%m-%d'),
                           'PRICE': actual_price, 'LOAD_RATE': f"{actual_load_rate * 100:.1f}%",
                           'UNIT_PRICE': unit_price}
            GlobalDelivPlan.append({'master': master_data, 'details': details_data})

    # =========================================================================
    # 阶段二：计算缺口与当月排产计划
    # =========================================================================
    logging.info(">>> [阶段二] 正在计算缺口与当月到货清库存计划...")

    Inspect_Target = np.zeros(SubTypeNum)
    for sub_idx in range(SubTypeNum):
        Inspect_Target[sub_idx] = max(0, Total_Delivery_Needed[sub_idx] - InitQuaStock[sub_idx])

    DevCatToLines = defaultdict(list)
    line_idx = 0
    if not DeviceCaps.empty:
        for _, row in DeviceCaps.iterrows():
            line_idx += 1
            veri_cat = str(row['VERI_CATEG']).strip().zfill(2)
            veri_type = str(row['VERI_TYPE']).strip().zfill(2)

            v_num = int(pd.to_numeric(row['VDRILINE_NUM'], errors='coerce') or 0)
            p_num = int(pd.to_numeric(row['POSI_NUM'], errors='coerce') or 1)
            pc_num = int(pd.to_numeric(row['POSI_CHECK_NUM'], errors='coerce') or 200)
            cap = v_num * p_num * pc_num
            if cap <= 0: cap = 200

            dev_categ_str = str(row.get('DEV_CATEG', '')).strip()
            for dc in dev_categ_str.replace('，', ',').split(','):
                if dc.strip():
                    DevCatToLines[dc.strip()].append({
                        'line_id': f"L_{line_idx}",
                        'veri_cat': veri_cat,
                        'veri_type': veri_type,
                        'batch_cap': cap
                    })

    HoursUsed = {d: defaultdict(float) for d in all_days}
    QtyUsed = {d: defaultdict(int) for d in all_days}
    CatMaxHrs = {d: defaultdict(float) for d in all_days}

    LotObjects = []

    for idx, row in LotList.iterrows():
        dev_code_str = row['DEV_CODE_NO']
        sub_idx = DevCodeToIndex.get(dev_code_str)
        if sub_idx is None:
            continue

        arr_dt_pd = pd.to_datetime(row['PLAN_DATE'])
        arr_dt = datetime(arr_dt_pd.year, arr_dt_pd.month, arr_dt_pd.day) if not pd.isna(arr_dt_pd) else sim_start_dt

        rem = int(row['RemNum'])
        if rem <= 0:
            continue

        is_realtime = (str(row.get('SOURCE_TYPE', '')).upper() == 'REALTIME')
        days_to_end = (month_end_dt - arr_dt).days

        # 卸载掉补丁：因为到货时间已经是100%绝对真实的，直接遵循“次日开工”的物理铁律即可！
        earliest_bgn = max(arr_dt + timedelta(days=1), sim_start_dt)

        last_dist_date = Last_Delivery_Date.get(sub_idx, sim_start_dt)
        is_actually_needed = (Inspect_Target[sub_idx] > 0) and (earliest_bgn <= last_dist_date)

        if is_realtime:
            take = rem
        elif days_to_end >= 5:
            take = rem
        else:
            take = rem if is_actually_needed else 0

        if take <= 0:
            continue

        if Inspect_Target[sub_idx] > 0 and is_actually_needed:
            Inspect_Target[sub_idx] -= take

        LotObjects.append({
            'idx': idx, 'row': row,
            'orig_take': take, 'rem': take,
            'earliest': earliest_bgn,
            'bgn': None, 'end': None, 'veri_type_used': None
        })

    # 【8级漏斗：极致压榨产线】
    inspection_passes = [
        {'max_h': 8, 'auto': True, 'wd': True},
        {'max_h': 12, 'auto': True, 'wd': True},
        {'max_h': 24, 'auto': True, 'wd': True},
        {'max_h': 24, 'auto': True, 'wd': False},
        {'max_h': 8, 'auto': False, 'wd': True},
        {'max_h': 12, 'auto': False, 'wd': True},
        {'max_h': 24, 'auto': False, 'wd': True},
        {'max_h': 24, 'auto': False, 'wd': False}
    ]

    for p in inspection_passes:
        for lot in LotObjects:
            if lot['rem'] <= 0: continue
            dev_code_str = lot['row']['DEV_CODE_NO']
            dev_cat = get_cat(dev_code_str)
            lines = DevCatToLines.get(dev_cat, [])

            for line in lines:
                if p['auto'] and line['veri_type'] != '02': continue
                if not p['auto'] and line['veri_type'] == '02': continue

                detect_time_hours = get_detect_time(dev_code_str, line['veri_type'])
                pph = line['batch_cap'] / detect_time_hours if detect_time_hours > 0 else 9999

                for d in all_days:
                    if d < lot['earliest']: continue
                    is_wd = is_workday_safe(d)
                    if p['wd'] and not is_wd: continue
                    if not p['wd'] and is_wd: continue

                    avail_h = p['max_h'] - HoursUsed[d][line['line_id']]
                    if avail_h > 0:
                        do_qty = min(lot['rem'], math.floor(avail_h * pph))
                        if do_qty > 0:
                            lot['rem'] -= do_qty
                            used_h = do_qty / pph
                            HoursUsed[d][line['line_id']] += used_h
                            QtyUsed[d][line['veri_cat']] += do_qty
                            CatMaxHrs[d][line['veri_cat']] = max(CatMaxHrs[d][line['veri_cat']],
                                                                 HoursUsed[d][line['line_id']])

                            if not lot['bgn'] or d < lot['bgn']: lot['bgn'] = d
                            if not lot['end'] or d > lot['end']: lot['end'] = d
                            lot['veri_type_used'] = line['veri_type']
                    if lot['rem'] <= 0: break
            if lot['rem'] <= 0: continue

    DetectPlanResult = []
    for lot in LotObjects:
        if lot['rem'] > 0:
            logging.warning(
                f"⚠️ [产能告急] 批次 {lot['row'].get('ARR_BATCH_NO', 'N/A')} (设备 {lot['row']['DEV_CODE_NO']}) 仍有 {lot['rem']} 只未能在本月排产，顺延至下月！")

        if lot['bgn'] is None: continue
        actual_detected_num = lot['orig_take'] - lot['rem']

        if actual_detected_num > 0:
            DetectPlanResult.append({
                'ARR_BATCH_NO': safe_val(lot['row'].get('ARR_BATCH_NO')),
                'BATCH_PLAN_ARR_ID': safe_val(lot['row'].get('BATCH_PLAN_ARR_ID')),
                'DEV_CODE': lot['row']['DEV_CODE_NO'], 'DEV_CODE_DESC': get_desc(lot['row']['DEV_CODE_NO']),
                'DEV_CLS': get_cls(lot['row']['DEV_CODE_NO']), 'DEV_CATEG': get_cat(lot['row']['DEV_CODE_NO']),
                'DETECT_PLAN_NUM': actual_detected_num,
                'DETECT_BGN_DATE': lot['bgn'].strftime('%Y-%m-%d'), 'DETECT_END_DATE': lot['end'].strftime('%Y-%m-%d'),
                'PLAN_STAT': '01', 'DAY_DETECT_PLAN_PRE_ID': lot['row'].get('DAY_DETECT_PLAN_PRE_ID'),
                'VERI_TYPE': lot['veri_type_used'] or '01'
            })

    WorkArrangeResult = []
    for d in all_days:
        for cat, qty in QtyUsed[d].items():
            if qty <= 0: continue

            hrs = CatMaxHrs[d][cat]
            w_flag = '02' if is_workday_safe(d) else '03'
            d_dur = '8h' if hrs <= 8.1 else ('12h' if hrs <= 12.1 else '24h')
            WorkArrangeResult.append({
                'VERI_CATEG': cat, 'WORK_DATE': d.strftime('%Y-%m-%d'), 'WORK_FLAG': w_flag,
                'DETECT_DUR': d_dur,
                'CAPACITY_NUM': int(qty)
            })

    return pd.DataFrame(DetectPlanResult), GlobalDelivPlan, pd.DataFrame(WorkArrangeResult)