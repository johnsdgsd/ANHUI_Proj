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
    """
    【核心运筹引擎：检定与配送联合排程 (Two-Phase Scheduling)】

    算法核心机制拆解：
    1. 空间配送（正向推导）：基于各地市局的缺口，调用 ALNS 算法排车。
    2. 检定排产（逆向推导）：
       - 【Phase 1 (宏观定班)】：剥离批次概念，纯算全月总量。强制优先月初加班，按 8h->12h->24h 的梯队，从每月1号开始填平算力差值。
       - 【Phase 2 (微观落盘)】：拿着定死的日班次表，将具体批次按 FIFO 灌入。保证批次处于连续的作业时段内，绝不撕裂！
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s", stream=sys.stdout)

    # =========================================================================
    # 0. 基础时间轴与辅助函数初始化
    # =========================================================================
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
    # 阶段一：调用空间运筹模型 (ALNS)，生成车辆配送明细方案
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
    # 阶段二：解析产线结构，生成虚拟物理并发实例
    # =========================================================================
    logging.info(">>> [阶段二] 正在计算缺口与当月到货清库存计划...")

    Inspect_Target = np.zeros(SubTypeNum)
    for sub_idx in range(SubTypeNum):
        Inspect_Target[sub_idx] = max(0, Total_Delivery_Needed[sub_idx] - InitQuaStock[sub_idx])

    DevCatToLines = defaultdict(list)
    line_idx = 0

    if not DeviceCaps.empty:
        for _, row in DeviceCaps.iterrows():
            veri_cat = str(row['VERI_CATEG']).strip().zfill(2)
            veri_type = str(row['VERI_TYPE']).strip().zfill(2)

            p_num = int(pd.to_numeric(row['POSI_NUM'], errors='coerce') or 1)
            pc_num = int(pd.to_numeric(row['POSI_CHECK_NUM'], errors='coerce') or 200)
            cap_per_line = p_num * pc_num  # POSI_NUM × POSI_CHECK_NUM = 单线总容量
            if cap_per_line <= 0: cap_per_line = 200

            dev_categ_str = str(row.get('DEV_CATEG', '')).strip()
            cats = [dc.strip() for dc in dev_categ_str.replace('，', ',').split(',') if dc.strip()]

            line_idx += 1
            unique_line_id = f"L_{line_idx}"
            for dc in cats:
                DevCatToLines[dc].append({
                    'line_id': unique_line_id,
                    'veri_cat': veri_cat,
                    'veri_type': veri_type,
                    'batch_cap': cap_per_line
                })

    # =========================================================================
    # 构建待检批次队列 (FIFO)
    # =========================================================================
    LotObjects = []
    for idx, row in LotList.iterrows():
        dev_code_str = row['DEV_CODE_NO']
        sub_idx = DevCodeToIndex.get(dev_code_str)
        if sub_idx is None: continue

        arr_dt_pd = pd.to_datetime(row['PLAN_DATE'])
        arr_dt = datetime(arr_dt_pd.year, arr_dt_pd.month, arr_dt_pd.day) if not pd.isna(arr_dt_pd) else sim_start_dt

        rem = int(row['RemNum'])
        if rem <= 0: continue

        is_realtime = (str(row.get('SOURCE_TYPE', '')).upper() == 'REALTIME')
        days_to_end = (month_end_dt - arr_dt).days
        earliest_bgn = max(arr_dt + timedelta(days=1), sim_start_dt)
        last_dist_date = Last_Delivery_Date.get(sub_idx, sim_start_dt)
        is_actually_needed = (Inspect_Target[sub_idx] > 0) and (earliest_bgn <= last_dist_date)

        # 混合备货策略：前中期全部兜底囤货，月末压线严格按需检定
        if is_realtime:
            take = rem
        elif days_to_end >= 5:
            take = rem
        else:
            take = rem if is_actually_needed else 0

        if take <= 0: continue

        if Inspect_Target[sub_idx] > 0 and is_actually_needed:
            Inspect_Target[sub_idx] -= take

        LotObjects.append({
            'idx': idx, 'row': row, 'earliest': earliest_bgn,
            'orig_take': take, 'rem': take, 'processed': 0,
            'bgn': None, 'end': None, 'veri_types_used': set()
        })

    # =========================================================================
    # 【核心：Phase 1 宏观产能规划 (时间顺序优先月初)】
    # 不看具体批次到达时间，纯算账！如果全月需要加班 2 天 12H，雷打不动铺在 1号、2号！
    # =========================================================================

    # 记录每天分配的宏观工时上限
    cat_auto_h_dict = defaultdict(lambda: np.zeros(total_sim_days))
    cat_manual_h_dict = defaultdict(lambda: np.zeros(total_sim_days))

    # 定义 8 级阶梯。注意：is_base=True 代表基础工作日产能，无条件全月铺满 8H。
    # 其余阶梯都是加班增量，只有全月总任务吃不消时，才会从每月 1 号开始依次触发！
    passes = [
        {'is_auto': True, 'target': 'wd', 'add_h': 8, 'is_base': True},  # 自动线 基础8H
        {'is_auto': True, 'target': 'wd', 'add_h': 4, 'is_base': False},  # 自动线 +4H (变12H)
        {'is_auto': True, 'target': 'wd', 'add_h': 12, 'is_base': False},  # 自动线 +12H (变24H)
        {'is_auto': True, 'target': 'we', 'add_h': 24, 'is_base': False},  # 自动线 周末24H
        {'is_auto': False, 'target': 'wd', 'add_h': 8, 'is_base': False},  # 人工线 开始启动 8H
        {'is_auto': False, 'target': 'wd', 'add_h': 4, 'is_base': False},  # 人工线 +4H (变12H)
        {'is_auto': False, 'target': 'wd', 'add_h': 12, 'is_base': False},  # 人工线 +12H (变24H)
        {'is_auto': False, 'target': 'we', 'add_h': 24, 'is_base': False},  # 人工线 周末24H
    ]

    all_cats = set(get_cat(lot['row']['DEV_CODE_NO']) for lot in LotObjects)

    for cat in all_cats:
        lots_cat = [lot for lot in LotObjects if get_cat(lot['row']['DEV_CODE_NO']) == cat]

        auto_lines = [l for l in DevCatToLines.get(cat, []) if l['veri_type'] == '02']
        manual_lines = [l for l in DevCatToLines.get(cat, []) if l['veri_type'] == '01']
        num_a, num_m = len(auto_lines), len(manual_lines)
        if num_a == 0 and num_m == 0: continue

        # 换算流速 PPH
        sample_code = lots_cat[0]['row']['DEV_CODE_NO']
        a_dur = get_detect_time(sample_code, '02')
        m_dur = get_detect_time(sample_code, '01')
        a_pph = (auto_lines[0]['batch_cap'] if num_a > 0 else 200) / a_dur if a_dur > 0 else 9999
        m_pph = (manual_lines[0]['batch_cap'] if num_m > 0 else 200) / m_dur if m_dur > 0 else 9999

        # 该品类全月总共要消化的数量
        rem_items = sum(lot['rem'] for lot in lots_cat)

        # 按优先级阶梯，依次将任务量折算为时间铺设到日历上
        for p in passes:
            # 如果不是基础班，且任务已经全部分配完了，停止后续更高级别的加班运算！
            if not p.get('is_base', False) and rem_items <= 0.0001: break

            is_auto = p['is_auto']
            if is_auto and num_a == 0: continue
            if not is_auto and num_m == 0: continue

            pph = a_pph if is_auto else m_pph
            num_l = num_a if is_auto else num_m

            # 【完美月初优先】：永远从 day 0 (1号) 开始循环寻找可以垫高工时的日子！
            for d_idx in range(total_sim_days):
                if not p.get('is_base', False) and rem_items <= 0.0001: break

                d = all_days[d_idx]
                is_wd = is_workday_safe(d)

                # 'wd' 仅工作日生效，'we' 仅周末生效
                if p['target'] == 'wd' and not is_wd: continue
                if p['target'] == 'we' and is_wd: continue

                cap_items = p['add_h'] * num_l * pph  # 这个班次格子能吃多少货

                if p.get('is_base', False):
                    # 基础班次无条件全月铺设 8H，即使导致 rem_items 变成负数（产能溢出）也没关系
                    actual_items = cap_items
                    need_h = p['add_h']
                else:
                    # 加班班次极度克制：只吃掉剩余的缺口量，缺口吃完立马停止，绝不多排一天！
                    actual_items = min(rem_items, cap_items)
                    need_h = actual_items / (num_l * pph)

                if is_auto:
                    cat_auto_h_dict[cat][d_idx] += need_h
                else:
                    cat_manual_h_dict[cat][d_idx] += need_h

                rem_items -= actual_items

    # =========================================================================
    # 【核心：Phase 2 微观批次物理落盘 (绝不撕裂)】
    # Phase 1 已经把每天的框子（8H或12H）定死了。
    # 批次来了，直接往当天的框子里灌，如果 12H 框子大，批次就在 12H 内一口气干完！
    # =========================================================================
    HoursUsed = {d: defaultdict(float) for d in all_days}
    QtyUsed = {d: defaultdict(float) for d in all_days}

    # 修复关键点：追踪物理产线大类（VERI_CATEG，如'01'或'02'）所承载的宏观最高排班时长
    MaxAlloc = {d: defaultdict(float) for d in all_days}

    for lot in LotObjects:
        if lot['rem'] <= 0.0001: continue

        cat = get_cat(lot['row']['DEV_CODE_NO'])
        dev_code = lot['row']['DEV_CODE_NO']

        lines = sorted(DevCatToLines.get(cat, []), key=lambda x: str(x['veri_type']), reverse=True)
        curr_idx = max(0, (lot['earliest'] - sim_start_dt).days)

        while lot['rem'] > 0.0001 and curr_idx < len(all_days):
            d = all_days[curr_idx]

            for line in lines:
                if lot['rem'] <= 0.0001: break

                is_auto = (line['veri_type'] == '02')
                # 调取 Phase 1 算好的当天这条线的“宏观最大工时”
                allowed_h = cat_auto_h_dict[cat][curr_idx] if is_auto else cat_manual_h_dict[cat][curr_idx]

                # 减去被前面的批次占用的时间，剩下的就是当前可分配的连续空隙
                avail_h = allowed_h - HoursUsed[d][line['line_id']]

                if avail_h > 0:
                    dur = get_detect_time(dev_code, line['veri_type'])
                    pph = line['batch_cap'] / dur if dur > 0 else 9999

                    do_qty = min(lot['rem'], avail_h * pph)
                    if do_qty > 0.0001:
                        used_h = do_qty / pph
                        lot['rem'] -= do_qty
                        lot['processed'] += do_qty

                        # 落盘统计
                        HoursUsed[d][line['line_id']] += used_h
                        QtyUsed[d][line['veri_cat']] += do_qty

                        # 同步记录这条物理线（veri_cat，如大类'01'）今天被分配了多大的宏观班次框
                        MaxAlloc[d][line['veri_cat']] = max(MaxAlloc[d][line['veri_cat']], allowed_h)

                        lot['veri_types_used'].add(line['veri_type'])

                        if not lot['bgn'] or d < lot['bgn']: lot['bgn'] = d
                        if not lot['end'] or d > lot['end']: lot['end'] = d

            curr_idx += 1

    # =========================================================================
    # 落库结果组装输出
    # =========================================================================
    DetectPlanResult = []
    for lot in LotObjects:
        if lot['rem'] > 0.0001:
            logging.warning(
                f"⚠️ [产能极度高压] 批次 {lot['row'].get('ARR_BATCH_NO', 'N/A')} 经全月极限排产仍剩 {lot['rem']} 只，将顺延！")

        if lot['bgn'] is None: continue
        actual_detected_num = int(lot['processed'])

        # 只要沾了自动线就显示 02
        primary_veri_type = '02' if '02' in lot['veri_types_used'] else (
            '01' if '01' in lot['veri_types_used'] else '01')

        if actual_detected_num > 0:
            DetectPlanResult.append({
                'ARR_BATCH_NO': safe_val(lot['row'].get('ARR_BATCH_NO')),
                'BATCH_PLAN_ARR_ID': safe_val(lot['row'].get('BATCH_PLAN_ARR_ID')),
                'DEV_CODE': lot['row']['DEV_CODE_NO'], 'DEV_CODE_DESC': get_desc(lot['row']['DEV_CODE_NO']),
                'DEV_CLS': get_cls(lot['row']['DEV_CODE_NO']), 'DEV_CATEG': get_cat(lot['row']['DEV_CODE_NO']),
                'DETECT_PLAN_NUM': actual_detected_num,
                'DETECT_BGN_DATE': lot['bgn'].strftime('%Y-%m-%d'), 'DETECT_END_DATE': lot['end'].strftime('%Y-%m-%d'),
                'PLAN_STAT': '01', 'DAY_DETECT_PLAN_PRE_ID': lot['row'].get('DAY_DETECT_PLAN_PRE_ID'),
                'VERI_TYPE': primary_veri_type
            })

    WorkArrangeResult = []
    for d in all_days:
        # 修复：遍历真正在当天干过活的产线大类（VERI_CATEG）
        for veri_cat, qty in QtyUsed[d].items():
            if qty <= 0: continue

            # 获取这条大类线在当天的最高宏观班次配置（由 Phase 1 定死，Phase 2 透传）
            max_alloc = MaxAlloc[d][veri_cat]

            # 【精准打标】：完美反映阶梯溢出的结果。只要加班量溢出到了 12H 的区间，就定性为 12h 班。
            if max_alloc <= 8.1:
                d_dur = '8h'
            elif max_alloc <= 12.1:
                d_dur = '12h'
            else:
                d_dur = '24h'

            w_flag = '02'  # 强制工作日状态兜底

            WorkArrangeResult.append({
                'VERI_CATEG': veri_cat, 'WORK_DATE': d.strftime('%Y-%m-%d'), 'WORK_FLAG': w_flag,
                'DETECT_DUR': d_dur, 'CAPACITY_NUM': int(qty)
            })

    return pd.DataFrame(DetectPlanResult), GlobalDelivPlan, pd.DataFrame(WorkArrangeResult)

