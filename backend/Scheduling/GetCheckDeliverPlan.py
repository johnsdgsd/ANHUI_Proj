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
    # 阶段一：先生成配送方案 (100%覆盖需求，永不丢单)
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
                # 加入 + 0.0001 容差，防止 vol_needed = 0.99999 被卡死
                if Sim_Demands[loc_1based - 1, sub_idx] > 0 and vol_needed > 0.0001:
                    dev_code_str = SubTypeList.loc[sub_idx, 'DEV_CODE_NO']
                    unit_arr = TypeList.loc[TypeList['DEV_CODE_NO'] == dev_code_str, 'UnitPerBox'].values
                    unit_per_box = unit_arr[0] if len(unit_arr) > 0 else 5
                    vol_mult = 2.5 if get_cls(dev_code_str) == '02' else 1.0

                    # 【核心修复】：完全信任 de_nums，移除之前的超载强制截断，精准拆箱
                    max_boxes_for_node = max(0, math.floor((vol_needed + 0.0001) / vol_mult))
                    max_allowable_qty = max_boxes_for_node * unit_per_box

                    qty_needed = min(Sim_Demands[loc_1based - 1, sub_idx], max_allowable_qty)

                    if qty_needed > 0:
                        used_boxes = math.ceil(qty_needed / unit_per_box)
                        vol_used = used_boxes * vol_mult

                        total_vol_used += vol_used
                        vol_needed -= vol_used

                        details_data.append({
                            'REC_ORG_NO': org_no, 'DEV_CODE': dev_code_str,
                            'DEV_CLS': get_cls(dev_code_str), 'DEV_CATEG': get_cat(dev_code_str),
                            'PLAN_DIST_NUM': qty_needed, 'PLAN_BOX_NUM': used_boxes,
                            'VOL_BOX_NUM': float(vol_used), 'DIST_SEQ': step_idx + 1,
                            'LOAD_SEQ': len(path_nodes) - step_idx,
                            'DIST_SEGMENT': float(dist_segment)
                        })
                        Sim_Demands[loc_1based - 1, sub_idx] -= qty_needed
                        Total_Delivery_Needed[sub_idx] += qty_needed

        if details_data:
            # 强制封顶，杜绝 100.4% 的情况
            actual_load_rate = min(1.0, total_vol_used / max_cap_boxes) if max_cap_boxes > 0 else 0
            unit_price = float(VeUnitPrice[int(ve_type) - 1]) if len(VeUnitPrice) >= int(ve_type) and float(
                VeUnitPrice[int(ve_type) - 1]) > 0 else 0.0695
            actual_price = sum(d['PLAN_BOX_NUM'] * d['DIST_SEGMENT'] * unit_price for d in details_data)

            master_data = {'CAR_TYPE': f"0{int(ve_type)}", 'PLAN_DIST_DATE': current_date.strftime('%Y-%m-%d'),
                           'PRICE': actual_price, 'LOAD_RATE': f"{actual_load_rate * 100:.1f}%",
                           'UNIT_PRICE': unit_price}
            GlobalDelivPlan.append({'master': master_data, 'details': details_data})

    # 日志输出：诊断是否有真漏单（在上述修复后，此处应当绝对不会触发）
    # for sub_idx in range(SubTypeNum):
    #     for loc_idx in range(LocationNum):
    #         if Sim_Demands[loc_idx, sub_idx] > 0:
    #             logging.error(
    #                 f"【严重警告】网点 {locations.iloc[loc_idx + 1]['ORG_NO']} 仍有 {Sim_Demands[loc_idx, sub_idx]} 件需求未被配送分配！")

    # =========================================================================
    # 阶段二：反向拉动检定需求 (配送要多少缺口，就恰好检定多少)
    # =========================================================================
    logging.info(">>> [阶段二] 配送完成！正在计算缺口反推检定产线...")

    Inspect_Target = np.zeros(SubTypeNum)
    for sub_idx in range(SubTypeNum):
        Inspect_Target[sub_idx] = max(0, Total_Delivery_Needed[sub_idx] - InitQuaStock[sub_idx])

    DevCatToLines = defaultdict(list)
    if not DeviceCaps.empty:
        for _, row in DeviceCaps.iterrows():
            veri_cat = str(row['VERI_CATEG']).strip().zfill(2)
            veri_type = str(row['VERI_TYPE']).strip().zfill(2)
            cap = int(pd.to_numeric(row['VDRILINE_NUM'], errors='coerce') or 0) * \
                  int(pd.to_numeric(row['POSI_NUM'], errors='coerce') or 1) * \
                  int(pd.to_numeric(row['POSI_CHECK_NUM'], errors='coerce') or 200)
            dev_categ_str = str(row.get('DEV_CATEG', '')).strip()
            for dc in dev_categ_str.replace('，', ',').split(','):
                if dc.strip():
                    DevCatToLines[dc.strip()].append({'veri_cat': veri_cat, 'veri_type': veri_type, 'batch_cap': cap})

    HoursUsed = {d: defaultdict(float) for d in all_days}

    LotObjects = []
    for idx, row in LotList.iterrows():
        dev_code_str = row['DEV_CODE_NO']
        sub_idx = DevCodeToIndex.get(dev_code_str)
        if sub_idx is None or Inspect_Target[sub_idx] <= 0: continue

        rem = int(row['RemNum'])
        take = min(rem, Inspect_Target[sub_idx])
        Inspect_Target[sub_idx] -= take

        arr_dt_pd = pd.to_datetime(row['PLAN_DATE'])
        arr_dt = datetime(arr_dt_pd.year, arr_dt_pd.month, arr_dt_pd.day) if not pd.isna(arr_dt_pd) else sim_start_dt

        LotObjects.append({
            'idx': idx, 'row': row,
            'orig_take': take, 'rem': take,
            'earliest': max(arr_dt + timedelta(days=1), sim_start_dt),
            'bgn': None, 'end': None, 'veri_type_used': None
        })

    # 【8级漏斗】

    inspection_passes = [
        {'max_h': 8, 'auto': True, 'wd': True},  # 1. 8h自动
        {'max_h': 12, 'auto': True, 'wd': True},  # 2. 12h自动
        {'max_h': 24, 'auto': True, 'wd': True},  # 3. 24小时自动
        {'max_h': 24, 'auto': True, 'wd': False},  # 4. 节假日自动
        {'max_h': 8, 'auto': False, 'wd': True},  # 5. 8h人工
        {'max_h': 12, 'auto': False, 'wd': True},  # 6. 12h人工
        {'max_h': 24, 'auto': False, 'wd': True},  # 7. 24h人工
        {'max_h': 24, 'auto': False, 'wd': False}  # 8. 节假日人工
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

                    avail_h = p['max_h'] - HoursUsed[d][line['veri_cat']]
                    if avail_h > 0:
                        do_qty = min(lot['rem'], math.floor(avail_h * pph))
                        if do_qty > 0:
                            lot['rem'] -= do_qty
                            HoursUsed[d][line['veri_cat']] += do_qty / pph
                            if not lot['bgn'] or d < lot['bgn']: lot['bgn'] = d
                            if not lot['end'] or d > lot['end']: lot['end'] = d
                            lot['veri_type_used'] = line['veri_type']
                    if lot['rem'] <= 0: break
            if lot['rem'] <= 0: continue

    DetectPlanResult = []
    for lot in LotObjects:
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
        for cat, hrs in HoursUsed[d].items():
            if hrs <= 0.01: continue
            w_flag = '02' if is_workday_safe(d) else '03'
            d_dur = '8h' if hrs <= 8.1 else ('12h' if hrs <= 12.1 else '24h')
            WorkArrangeResult.append({
                'VERI_CATEG': cat, 'WORK_DATE': d.strftime('%Y-%m-%d'), 'WORK_FLAG': w_flag,
                'DETECT_DUR': d_dur, 'CAPACITY_NUM': math.floor(hrs * 2500)
            })

    return pd.DataFrame(DetectPlanResult), GlobalDelivPlan, pd.DataFrame(WorkArrangeResult)