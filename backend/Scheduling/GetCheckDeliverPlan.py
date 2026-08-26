import numpy as np
import pandas as pd
import logging
import sys

from datetime import datetime, timedelta
import math
from collections import defaultdict

# 尝试导入中国法定节假日库，用于精准判断周末、法定节假日以及调休补班
try:
    import chinese_calendar

    HAS_CALENDAR = True
except ImportError:
    HAS_CALENDAR = False

from backend.Scheduling.GetDelivPlan import GetDelivPlan


def GetCheckDeliverPlan(Demands, InitQuaStock, LotList, DeviceCaps, SubTypeList, TypeList, DMAT, LocationNum, VeCap,
                        VNums, VeUnitPrice, VeTypeNum, sim_start_date_str, total_sim_days, record_start_date_str,
                        locations, org_priority=None, dev_stock=None, dev_forecast=None, maint_posi_df=None):
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
    # 0.5 构建网点优先级数组（索引 → 优先级），供 ILP 排序使用
    # =========================================================================
    node_priority = np.zeros(LocationNum + 1)  # 索引 0 = 省级总库，优先级为 0
    if org_priority:
        for i in range(1, LocationNum + 1):
            org_no = str(locations.loc[i, 'ORG_NO']).strip()
            if org_no in org_priority:
                node_priority[i] = org_priority[org_no]
        high_pri_nodes = np.sum(node_priority > 0.5)
        logging.info(f"网点优先级已加载: {high_pri_nodes} 个高优先级网点, "
                     f"最大={node_priority.max():.4f}, 平均={node_priority[node_priority > 0].mean():.4f}")
    else:
        logging.info("网点优先级未传入，所有路线优先级为 0（无优先级惩罚）")

    # =========================================================================
    # 【诊断】排程前：打印总需求与总箱数
    # =========================================================================
    total_demand_units = Demands.sum()
    # 用与 ALNS 完全一致的方式计算体积加权箱数
    demand_boxes_by_loc = np.zeros(LocationNum)
    for i in range(SubTypeNum):
        dc = str(SubTypeList.loc[i, 'DEV_CODE_NO']).strip()
        unit_arr = TypeList.loc[TypeList['DEV_CODE_NO'] == dc, 'UnitPerBox'].values
        UnitPerBoxI = pd.to_numeric(unit_arr[0], errors='coerce') if len(unit_arr) > 0 else 5
        UnitPerBoxI = UnitPerBoxI if (pd.notna(UnitPerBoxI) and UnitPerBoxI > 0) else 5
        cls_val = str(SubTypeList.loc[i, 'DEV_CLS']).replace('.0', '').strip().zfill(2) if 'DEV_CLS' in SubTypeList.columns else '01'
        vol_mult = 2.5 if cls_val == '02' else 1.0
        demand_boxes_by_loc += np.ceil(np.ceil(Demands[:, i] / UnitPerBoxI) * vol_mult)
    total_demand_boxes = demand_boxes_by_loc.sum()
    active_loc_count = np.sum(demand_boxes_by_loc > 0)
    logging.info(f"[诊断-排程前] 总需求={total_demand_units:,.0f}只, 体积加权总箱数={total_demand_boxes:,.0f}箱, "
                 f"有需求网点数={active_loc_count}/{LocationNum}")
    logging.info(">>> [阶段一] 生成精确到每一辆车的配送路线与日历分配...")
    work_days_list = [d for d in all_days if is_workday_safe(d)]
    actual_deliv_days = len(work_days_list) or 1
    if not work_days_list: work_days_list = [sim_start_dt]

    # =========================================================================
    # 0.6 逐日查询物流承运商车辆配置，构建每日各车型可用数量
    # =========================================================================
    from backend.Scheduling.Service_CheckDeliver import fetch_data
    logging.info(">>> [阶段一] 查询每日物流承运商及车辆配置...")
    daily_vehicle_limits = {}  # {day_idx: {vehicle_type: count}}
    # ---- 从承运商数据动态构建车型配置（替代旧的 VeCap/VNums/VeUnitPrice） ----
    type_info = {}  # {car_type: {'cap': ..., 'carri': ..., 'max_daily': ...}}
    for day_idx, work_date in enumerate(work_days_list):
        date_str = work_date.strftime('%Y-%m-%d') if hasattr(work_date, 'strftime') else str(work_date)[:10]
        day_counts = {}
        try:
            carriers = fetch_data("gk-adam_query_log_carrier_by_curr_date", {"query_date": date_str})
            if not carriers.empty:
                lcc_ids = carriers['LCC_ID'].dropna().unique()
                for lcc_id in lcc_ids:
                    vans = fetch_data("gk-adam_query_log_car_van_conf_by_lccid", {"lcc_id": int(lcc_id)})
                    if not vans.empty:
                        for _, vr in vans.iterrows():
                            ct_str = str(vr['CAR_TYPE']).strip().replace('.0', '')
                            ct = int(ct_str) if ct_str.isdigit() else 0
                            if ct <= 0:
                                continue
                            day_counts[ct] = day_counts.get(ct, 0) + int(vr['VEHICLE_NUM'])
                            # 记录车型容量和单价（首次遇到时记录）
                            if ct not in type_info:
                                cap = int(vr['VEHICLE_CAP']) if 'VEHICLE_CAP' in vr else 0
                                carri = float(vr['VEHICLE_CARRI']) if 'VEHICLE_CARRI' in vr else 0.0695
                                type_info[ct] = {'cap': cap, 'carri': carri}
            daily_vehicle_limits[day_idx] = day_counts
        except Exception as e:
            logging.warning(f"查询{date_str}车辆配置失败({e})")
    # ---- 用承运商数据覆盖 VeCap/VNums/VeUnitPrice/VeTypeNum ----
    if type_info:
        sorted_types = sorted(type_info.keys())
        VeTypeNum = len(sorted_types)
        VeCap = np.array([type_info[vt]['cap'] for vt in sorted_types])
        VeUnitPrice = np.array([type_info[vt]['carri'] for vt in sorted_types])
        # VNums 取所有日期中该车型的最大日配额
        VNums = np.array([
            max((daily_vehicle_limits[d].get(vt, 0) for d in range(actual_deliv_days)), default=0)
            for vt in sorted_types
        ])
        all_vehicle_types = sorted_types
        logging.info(f"✅ 从承运商接口拉取 {VeTypeNum} 种车型: {dict(zip(sorted_types, VeCap))}")
    else:
        all_vehicle_types = list(range(1, VeTypeNum + 1))
        logging.warning("⚠️ 承运商查询为空，使用旧接口默认车型配置")
    # 日志汇总
    for vt in all_vehicle_types:
        daily_list = [daily_vehicle_limits[d].get(vt, 0) for d in range(actual_deliv_days)]
        logging.info(f"[每日运力] 车型{vt}: 日配额={daily_list}, 月总={sum(daily_list)}")
    # =========================================================================

    # ---- 近中心网点：离省库很近的网点之间免角度约束 ----
    NEAR_CENTER_ORGS = {'3440101', '3440102', '3440103', '3440105', '34401'}
    near_center_nodes = set()
    for i in range(LocationNum):
        org_no = str(locations.loc[i + 1, 'ORG_NO'])
        if org_no in NEAR_CENTER_ORGS:
            near_center_nodes.add(i + 1)  # 1-based node ID
    if near_center_nodes:
        logging.info(f"[近中心豁免] 识别到{len(near_center_nodes)}个近中心网点: node={sorted(near_center_nodes)}")
    # ====================================================================

    # 构造 node_org_map → 供 GetDelivPlan 合肥约束使用
    node_org_map = {i + 1: str(locations.loc[i + 1, 'ORG_NO']).strip() for i in range(LocationNum)}

    ScheduledRoutes = GetDelivPlan(Demands, LocationNum, TypeList, SubTypeList,
                                   actual_deliv_days, VeUnitPrice, VeTypeNum, VNums, VeCap, DMAT,
                                   node_priority, daily_vehicle_limits, vehicle_types=all_vehicle_types,
                                   near_center_nodes=near_center_nodes if near_center_nodes else None,
                                   work_days=work_days_list, node_org_map=node_org_map)

    # 【诊断】排程后装箱前：汇总 ALNS 配送结果
    sched_total_boxes = 0.0
    sched_loc_boxes = defaultdict(float)
    sched_route_count = len(ScheduledRoutes)
    for trip in ScheduledRoutes:
        for cid, amt in trip['deliveries']:
            sched_total_boxes += amt
            sched_loc_boxes[cid] += amt
    sched_loc_count = len(sched_loc_boxes)
    demand_box_pct = (sched_total_boxes / total_demand_boxes * 100) if total_demand_boxes > 0 else 0.0
    logging.info(f"[诊断-排程后] v5.5 MIP返回{len(ScheduledRoutes)}条路线, "
                 f"配送总体积箱数={sched_total_boxes:,.0f}箱, 覆盖网点数={sched_loc_count}, "
                 f"占原始总箱数={demand_box_pct:.1f}%"
                 f"{' ←缺口!' if sched_total_boxes < total_demand_boxes - 1 else ''}")

    # ---- 排程月每周配送车辆数统计 ----
    week_veh = defaultdict(lambda: defaultdict(int))  # {week: {vehicle_type: count}}
    week_boxes = defaultdict(float)                     # {week: total_boxes}
    week_routes = defaultdict(int)                      # {week: route_count}
    for trip in ScheduledRoutes:
        day_idx = trip.get('schedule_day_idx', 0)
        if day_idx < len(work_days_list):
            d = work_days_list[day_idx]
            week = (d.day - 1) // 7 + 1
            vt = int(trip.get('vehicle_type', 1))
            week_veh[week][vt] += 1
            week_routes[week] += 1
            week_boxes[week] += sum(amt for _, amt in trip['deliveries'])
    if week_veh:
        logging.info("=" * 70)
        logging.info(f">>> 排程月每周配送车辆统计 (共{len(work_days_list)}个工作日)")
        logging.info(f"{'周次':<6} {'工作日范围':<16} {'路线数':<8} {'配送箱数':<10} {'各车型车辆数'}")
        logging.info("-" * 70)
        for week in sorted(week_veh.keys()):
            # 找该周的工作日范围
            week_dates = [work_days_list[trip.get('schedule_day_idx', 0)]
                         for trip in ScheduledRoutes
                         if trip.get('schedule_day_idx', 0) < len(work_days_list)
                         and (work_days_list[trip.get('schedule_day_idx', 0)].day - 1) // 7 + 1 == week]
            if week_dates:
                date_range = f"{min(week_dates).strftime('%m/%d')}-{max(week_dates).strftime('%m/%d')}"
            else:
                date_range = "-"
            veh_detail = " | ".join(f"车型{vt}×{cnt}" for vt, cnt in sorted(week_veh[week].items()))
            logging.info(f"第{week}周   {date_range:<16} {week_routes[week]:<8} "
                        f"{week_boxes[week]:<10.0f} {veh_detail}")
        logging.info("=" * 70)

    # 装箱前先跑检定规划
    DevCodeToIndex = {str(SubTypeList.loc[i, 'DEV_CODE_NO']).strip(): i for i in range(SubTypeNum)}
    Last_Delivery_Date = {}


    # 装箱统计变量
    GlobalDelivPlan = []
    Sim_Demands = Demands.copy()
    Total_Delivery_Needed = np.zeros(SubTypeNum)
    daily_allocated = np.zeros(SubTypeNum)  # 当天已分配量（库存约束用）
    # =========================================================================
    # 阶段二：解析产线结构，生成虚拟物理并发实例
    # =========================================================================
    logging.info(">>> [阶段二] 正在计算缺口与当月到货清库存计划...")

    DevCatToLines = defaultdict(list)
    line_idx = 0

    if not DeviceCaps.empty:
        for _, row in DeviceCaps.iterrows():
            veri_cat = str(row['VERI_CATEG']).strip().zfill(2)
            veri_type = str(row['VERI_TYPE']).strip().zfill(2)

            total_posi = int(pd.to_numeric(row['POSI_NUM'], errors='coerce') or 1)
            posi_check_num = int(pd.to_numeric(row['POSI_CHECK_NUM'], errors='coerce') or 200)
            if total_posi <= 0: total_posi = 1
            if posi_check_num <= 0: posi_check_num = 200

            dev_categ_str = str(row.get('DEV_CATEG', '')).strip()
            cats = [dc.strip() for dc in dev_categ_str.replace('，', ',').split(',') if dc.strip()]

            line_idx += 1
            # 【逐日产能】改用真实主键 DETECT_LINE_CONFIG_ID 作为 line_id，便于联动检修仓明细表 ADAM_LINE_DTL
            unique_line_id = str(row.get('DETECT_LINE_CONFIG_ID', '')).replace('.0', '').strip()
            if not unique_line_id or unique_line_id in ('nan', 'None'):
                unique_line_id = f"L_{line_idx}"  # 空值兜底回退自增序号
            for dc in cats:
                DevCatToLines[dc].append({
                    'line_id': unique_line_id,
                    'veri_cat': veri_cat,
                    'veri_type': veri_type,
                    'total_posi': total_posi,
                    'posi_check_num': posi_check_num
                })

    # =========================================================================
    # 构建待检批次队列 (FIFO)
    # =========================================================================
    LotObjects = []
    for idx, row in LotList.iterrows():
        dev_code_str = str(row['DEV_CODE_NO']).strip()
        sub_idx = DevCodeToIndex.get(dev_code_str)
        if sub_idx is None: continue

        arr_dt_pd = pd.to_datetime(row['PLAN_DATE'])
        arr_dt = datetime(arr_dt_pd.year, arr_dt_pd.month, arr_dt_pd.day) if not pd.isna(arr_dt_pd) else sim_start_dt

        rem = int(row['RemNum'])
        if rem <= 0: continue

        is_realtime = (str(row.get('SOURCE_TYPE', '')).upper() == 'REALTIME')
        days_to_end = (month_end_dt - arr_dt).days
        earliest_bgn = max(arr_dt + timedelta(days=2), sim_start_dt)
        # 全部待检批次都要检定完，不设需求上限
        if is_realtime:
            take = rem
        elif days_to_end >= 2:
            take = rem
        else:
            take = rem  # 月末也不压线，全部检定

        if take <= 0: continue

        LotObjects.append({
            'idx': idx, 'row': row, 'earliest': earliest_bgn,
            'orig_take': take, 'rem': take, 'processed': 0,
            'bgn': None, 'end': None, 'veri_types_used': set()
        })

    # =========================================================================
    # 【核心：Phase 1 宏观产能规划 (时间顺序优先月初)】
    # 不看具体批次到达时间，纯算账！如果全月需要加班 2 天 12H，雷打不动铺在 1号、2号！
    # =========================================================================

    # 记录每天分配的宏观工时上限（线级，不再按品类拆分）
    line_auto_h_dict = defaultdict(lambda: np.zeros(total_sim_days))    # {line_id: [day_hours]}
    line_manual_h_dict = defaultdict(lambda: np.zeros(total_sim_days))  # {line_id: [day_hours]}

    # 定义 6 级阶梯。注意：is_base=True 代表基础工作日产能，无条件全月铺满 12H。
    # 其余阶梯都是加班增量，只有全月总任务吃不消时，才会从每月 1 号开始依次触发！
    passes = [
        {'is_auto': True, 'target': 'wd', 'add_h': 12, 'is_base': True},   # 自动线 基础12H
        {'is_auto': True, 'target': 'wd', 'add_h': 12, 'is_base': False},  # 自动线 +12H (变24H)
        {'is_auto': True, 'target': 'we', 'add_h': 24, 'is_base': False},  # 自动线 周末24H
        {'is_auto': False, 'target': 'wd', 'add_h': 12, 'is_base': False}, # 人工线 开始启动 12H
        {'is_auto': False, 'target': 'wd', 'add_h': 12, 'is_base': False}, # 人工线 +12H (变24H)
        {'is_auto': False, 'target': 'we', 'add_h': 24, 'is_base': False}, # 人工线 周末24H
    ]

    all_cats = set(get_cat(lot['row']['DEV_CODE_NO']) for lot in LotObjects)

    # =========================================================================
    # 【修复】构建唯一物理产线映射，防止共享产线被多品类重复计数
    # =========================================================================
    unique_lines = {}  # {line_id: {'line': {...}, 'cats': {cat1, cat2, ...}}}
    for cat in all_cats:
        for l in DevCatToLines.get(cat, []):
            lid = l['line_id']
            if lid not in unique_lines:
                unique_lines[lid] = {'line': l, 'cats': set()}
            unique_lines[lid]['cats'].add(cat)

    # =========================================================================
    # 【逐日产能】构建每条物理产线的逐日批容量 cap_by_day[line_id][d_idx]
    # 满产能 = POSI_NUM × POSI_CHECK_NUM；检修仓在检修时间段内每天扣减 1 个仓位（= 减 POSI_CHECK_NUM）
    # =========================================================================
    cap_by_day = {}  # {line_id: np.array(total_sim_days)}
    for lid in unique_lines:
        line = unique_lines[lid]['line']
        cap_by_day[lid] = np.full(total_sim_days,
                                  float(line['total_posi']) * float(line['posi_check_num']))

    if maint_posi_df is not None and not maint_posi_df.empty:
        for _, mr in maint_posi_df.iterrows():
            lid = str(mr['DETECT_LINE_CONFIG_ID']).replace('.0', '').strip()
            if lid not in cap_by_day:
                continue
            try:
                m_start = pd.to_datetime(mr['MAINT_START_TIME']).date()
                m_end = pd.to_datetime(mr['MAINT_END_TIME']).date()
            except Exception:
                continue
            pc = unique_lines[lid]['line']['posi_check_num']  # 扣 1 仓位 = 减每仓检定数
            for d_idx, d in enumerate(all_days):
                if m_start <= d.date() <= m_end:
                    cap_by_day[lid][d_idx] -= pc
        for lid in cap_by_day:
            cap_by_day[lid] = np.clip(cap_by_day[lid], 0, None)  # 兜底不为负
        logging.info(f"[逐日产能] 已按检修仓明细扣减 {len(maint_posi_df)} 条检修记录")

    # =========================================================================
    # Phase 1: 以物理产线为单位进行宏观产能规划
    # =========================================================================
    for lid, info in unique_lines.items():
        line = info['line']
        is_auto = (line['veri_type'] == '02')
        veri_type_str = '02' if is_auto else '01'
        cats = info['cats']

        # ----- 需求只数按品类 + 加权平均检定时长（多品类共享一条线） -----
        total_units = 0.0
        weighted_num = 0.0
        for cat in cats:
            lots_cat = [lot for lot in LotObjects if get_cat(lot['row']['DEV_CODE_NO']) == cat]
            rem = sum(lot['rem'] for lot in lots_cat)
            if rem <= 0:
                continue
            sample_code = lots_cat[0]['row']['DEV_CODE_NO']
            dur = get_detect_time(sample_code, veri_type_str)
            total_units += rem
            weighted_num += rem * dur

        if total_units <= 0.0001:
            continue

        weighted_dur = weighted_num / total_units          # 加权平均单只检定时长（小时）
        daily_pph = cap_by_day[lid] / weighted_dur if weighted_dur > 0 else np.full(total_sim_days, 9999.0)  # 逐日 pph（只/小时）

        remaining = total_units

        # 按优先级阶梯，依次将产能（只数）铺设到日历上，逐日真实产能
        for p in passes:
            if p['is_auto'] != is_auto:
                continue  # 自动线/人工线只走各自对应的 pass

            # 非基础班 → 产能缺口已填满则停止
            if not p.get('is_base', False) and remaining <= 0.0001:
                break

            # 【完美月初优先】：永远从 day 0 (1号) 开始循环
            for d_idx in range(total_sim_days):
                if not p.get('is_base', False) and remaining <= 0.0001:
                    break

                d = all_days[d_idx]
                is_wd = is_workday_safe(d)

                if p['target'] == 'wd' and not is_wd:
                    continue
                if p['target'] == 'we' and is_wd:
                    continue

                pph_d = daily_pph[d_idx]
                add_units = pph_d * p['add_h']  # 该班次当天满产只数

                if p.get('is_base', False):
                    # 基础班次无条件全月铺设，即使需求已满也铺（产能溢出无所谓）
                    need_h = float(p['add_h'])
                    remaining -= add_units
                else:
                    # 加班班次极度克制：只吃掉剩余缺口，工时按实际所需折算
                    need_units = min(remaining, add_units)
                    need_h = (need_units / pph_d) if pph_d > 0 else 0.0
                    remaining -= need_units

                # 整块工时直接挂到产线上，不按品类拆分（品类互斥在 Phase 2 由批次锁保证）
                if is_auto:
                    line_auto_h_dict[lid][d_idx] += need_h
                else:
                    line_manual_h_dict[lid][d_idx] += need_h

    # =========================================================================
    # 【核心：Phase 2 微观批次物理落盘 (绝不撕裂)】
    # Phase 1 已经把每天的框子（8H或12H）定死了。
    # 批次来了，直接往当天的框子里灌，如果 12H 框子大，批次就在 12H 内一口气干完！
    # =========================================================================
    HoursUsed = {d: defaultdict(float) for d in all_days}
    QtyUsed = {d: defaultdict(float) for d in all_days}
    DevDailyDone = {d: defaultdict(float) for d in all_days}  # 日级设备码完工量

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
                # 调取 Phase 1 算好的当天这条线的宏观最大工时（线级整块）
                allowed_h = line_auto_h_dict[line['line_id']][curr_idx] if is_auto else line_manual_h_dict[line['line_id']][curr_idx]
                if allowed_h <= 0.0001:
                    continue

                # 减去已被前面批次占用的时间，剩余即当前可分配的空隙
                avail_h = allowed_h - HoursUsed[d][line['line_id']]

                if avail_h > 0:
                    dur = get_detect_time(dev_code, line['veri_type'])
                    pph = cap_by_day[line['line_id']][curr_idx] / dur if dur > 0 else 9999

                    do_qty = min(lot['rem'], avail_h * pph)
                    if do_qty > 0.0001:
                        used_h = do_qty / pph
                        lot['rem'] -= do_qty
                        lot['processed'] += do_qty

                        # 落盘统计
                        HoursUsed[d][line['line_id']] += used_h
                        QtyUsed[d][line['veri_cat']] += do_qty
                        DevDailyDone[d][dev_code] += do_qty  # 日级设备码完工量

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
        # 遍历真正在当天干过活的产线大类（VERI_CATEG）
        for veri_cat, qty in QtyUsed[d].items():
            if qty <= 0: continue

            # 获取这条大类线在当天的最高宏观班次配置（由 Phase 1 定死，Phase 2 透传）
            max_alloc = MaxAlloc[d][veri_cat]

            # 【精准打标】：基础 12H，加班到 24H
            if max_alloc <= 12.1:
                d_dur = '12h'
            else:
                d_dur = '24h'

            w_flag = '02'  # 强制工作日状态兜底

            WorkArrangeResult.append({
                'VERI_CATEG': veri_cat, 'WORK_DATE': d.strftime('%Y-%m-%d'), 'WORK_FLAG': w_flag,
                'DETECT_DUR': d_dur, 'CAPACITY_NUM': int(qty)
            })

    # =========================================================================
    # 装箱前：计算每天累计合格品库存（逐天累加检定完工量）
    # =========================================================================
    daily_cum_stock = {}  # {datetime: np.array[SubTypeNum]}
    cur_stock = InitQuaStock.copy()
    for d in all_days:
        cur_stock = cur_stock.copy()
        for dev_code, qty in DevDailyDone.get(d, {}).items():
            j = DevCodeToIndex.get(str(dev_code).strip())
            if j is not None:
                cur_stock[j] += qty
        daily_cum_stock[d] = cur_stock
    logging.info(f'[装箱] 日合格品库存计算完成, {len(all_days)}天')
    # =========================================================================

    logging.info(f"LotObjects: {len(LotObjects)}个批次, 产出DetectPlan: {len(DetectPlanResult)}条")
    # ---- 装箱优先级初始化：设备码级有效库存 ----
    if dev_stock is None:
        dev_stock = {}
    if dev_forecast is None:
        dev_forecast = {}
    # effective_stock[(org_no, dev_code)] = 初始库存 + 累计已配送量
    effective_stock = {}

    # 新缺货检测：建立 (ORG, DEV_CODE) → 应补库箱数 优先级表
    stockout_priority = {}  # {(org, dev): add_boxes}
    try:
        from backend.Scheduling.stockout_detector import detect_stockout
        date_str = work_days_list[0].strftime('%Y-%m-%d') if work_days_list else sim_start_date_str
        df_s = detect_stockout(date_str)
        if not df_s.empty:
            for _, r in df_s.iterrows():
                stockout_priority[(str(r['ORG']).strip(), str(r['DEV_CODE']).strip())] = int(r['原始箱数'])
        logging.info(f'[装箱] 缺货优先级表: {len(stockout_priority)} 个 (ORG,DEV) 组合')
    except Exception as e:
        logging.warning(f'[装箱] 缺货检测失败({e})，回退旧公式')

    # 按配送日期排序，确保逐日推算库存水平
    sorted_trips = sorted(ScheduledRoutes, key=lambda t: t.get('schedule_day_idx', 0))

    # 预计算设备码属性缓存（避免循环内重复查表）
    dev_vol_mult = {}   # dev_code → 体积系数
    dev_unit_box = {}   # dev_code → 每箱只数
    for sub_idx in range(SubTypeNum):
        dc = str(SubTypeList.loc[sub_idx, 'DEV_CODE_NO']).strip()
        dev_vol_mult[dc] = 2.5 if get_cls(dc) == '02' else 1.0
        unit_arr = TypeList.loc[TypeList['DEV_CODE_NO'] == dc, 'UnitPerBox'].values
        _upb = pd.to_numeric(unit_arr[0], errors='coerce') if len(unit_arr) > 0 else 5
        dev_unit_box[dc] = _upb if (pd.notna(_upb) and _upb > 0) else 5

    # 可装箱设备码列表（预过滤：存在需求且在 dev_forecast 中有记录）
    active_dev_indices = list(range(SubTypeNum))

    # 【车型容量映射】ve_type 来自 v5_5 的 vehicle_k+1，按容量降序语义（1=大车/容量最大，
    # 2=中车，3=小车/容量最小）。承运商接口下 CAR_TYPE=01 即大车（容量最大），VeCap 按
    # CAR_TYPE 升序 = 容量降序（大→中→小）。但为稳妥仍按容量降序显式重排（不依赖 CAR_TYPE
    # 编号顺序），得到 VeCap_desc / VeCarType_desc，供 ve_type-1 直接索引：大车→[0]，小车→[2]。
    _order_desc = sorted(range(len(VeCap)), key=lambda i: float(VeCap[i]), reverse=True)
    VeCap_desc = [float(VeCap[i]) for i in _order_desc]
    VeUnitPrice_desc = [float(VeUnitPrice[i]) if i < len(VeUnitPrice) else 0.0695 for i in _order_desc]
    VeCarType_desc = [all_vehicle_types[i] if i < len(all_vehicle_types) else (i + 1) for i in _order_desc]

    cumulative_shipped = np.zeros(SubTypeNum)  # 跨日累计已发货量，永不清零
    prev_date = None
    for trip in sorted_trips:
        wd_idx = trip.get('schedule_day_idx', 0)
        current_date = work_days_list[wd_idx]
        if current_date != prev_date:
            daily_allocated = np.zeros(SubTypeNum)
            prev_date = current_date
        ve_type = trip.get('vehicle_type', 1)
        path_nodes = [cid for cid, _ in trip['deliveries']]
        de_nums = [amt for _, amt in trip['deliveries']]

        total_vol_used = 0.0
        _vt = int(ve_type)
        max_cap_boxes = VeCap_desc[_vt - 1] if 1 <= _vt <= len(VeCap_desc) else (VeCap_desc[-1] if VeCap_desc else 0)
        details_data = []
        prev_node = 0

        for step_idx, loc_1based in enumerate(path_nodes):
            vol_remaining = de_nums[step_idx]
            dist_segment = DMAT[prev_node, loc_1based] if isinstance(DMAT, np.ndarray) else DMAT.values[
                prev_node, loc_1based]
            prev_node = loc_1based
            org_no = str(locations.loc[loc_1based, 'ORG_NO']).strip()

            # ---- 逐个设备码尽量装满：排序一次，同一设备码不拆散 ----
            alloc_map = {}  # dev_code → 累计分配只数

            # 1. 一次性计算所有设备码的缺货概率并排序
            dev_ranking = []  # [(prob, fulfilled_pct, sub_idx, dc), ...]
            remaining_cap0 = max_cap_boxes - total_vol_used
            for sub_idx in active_dev_indices:
                remaining_demand = Sim_Demands[loc_1based - 1, sub_idx]
                if remaining_demand <= 0:
                    continue
                dc = str(SubTypeList.loc[sub_idx, 'DEV_CODE_NO']).strip()
                vm = dev_vol_mult.get(dc, 1.0)
                if vm > remaining_cap0 + 0.001:
                    continue  # 至少能装下1箱

                key = (org_no, dc)
                # 用新缺货检测的应补库箱数作为优先级（越大越优先）
                prob = stockout_priority.get(key, 0)

                plan_qty = Demands[loc_1based - 1, sub_idx]
                fulfilled_pct = (plan_qty - remaining_demand) / plan_qty if plan_qty > 0 else 1.0

                dev_ranking.append((prob, fulfilled_pct, sub_idx, dc))

            # 概率降序；概率相同时完成率低的优先
            dev_ranking.sort(key=lambda x: (-x[0], x[1]))

            # 2. 缺货优先 + 逐个装满：同一设备码尽量一次性装完，不拆散
            day_stock = daily_cum_stock.get(current_date, InitQuaStock)
            for prob, fulfilled_pct, sub_idx, dc in dev_ranking:
                remaining_cap = max_cap_boxes - total_vol_used
                vm = dev_vol_mult.get(dc, 1.0)
                unit_per_box = dev_unit_box.get(dc, 5)
                if remaining_cap < vm + 0.001:
                    continue  # 剩余容量连1箱都装不下
                if vol_remaining <= 0.001:
                    break
                demand = Sim_Demands[loc_1based - 1, sub_idx]
                if demand <= 0:
                    continue
                # 检查合格品库存：当天累计可用 = 累计库存 - 跨日累计已发货
                avail = day_stock[sub_idx] - cumulative_shipped[sub_idx]
                if avail <= 0:
                    continue  # 库存不足，顺延到下个配送日
                # 能装多少装多少：需求、库存、整车容量、本网点本车配送箱数 四重上限
                max_can_fit = math.floor(remaining_cap / vm)  # 按体积最多装几箱
                # 关键修复：本网点本车的配送箱数(vol_remaining)也必须是上限，
                # 否则排序靠前的缺货设备码会超装、提前吃满整车容量，挤占排序靠后的库存充足设备码
                max_by_vol_remaining = math.floor(vol_remaining / vm) * unit_per_box
                max_qty = min(demand, avail, max_can_fit * unit_per_box, max_by_vol_remaining)  # 最多装多少只
                if max_qty <= 0:
                    continue
                # 一次性装完（同一设备码不拆散）
                qty = max_qty
                boxes_used = math.ceil(qty / unit_per_box)
                vol_used = boxes_used * vm
                total_vol_used += vol_used
                vol_remaining -= vol_used
                Sim_Demands[loc_1based - 1, sub_idx] -= qty
                cumulative_shipped[sub_idx] += qty
                alloc_map[dc] = alloc_map.get(dc, 0) + qty
                key = (org_no, dc)
                init_stock = dev_stock.get(key, 0)
                effective_stock[key] = effective_stock.get(key, init_stock) + qty

            # 将聚合结果转为 details_data 行
            for dev_code_str, qty_needed in alloc_map.items():
                sub_idx = DevCodeToIndex.get(dev_code_str)
                if sub_idx is None:
                    continue

                vm = dev_vol_mult.get(dev_code_str, 1.0)
                unit_per_box = dev_unit_box.get(dev_code_str, 5)
                used_boxes = math.ceil(qty_needed / unit_per_box)
                vol_used = used_boxes * vm

                details_data.append({
                    'REC_ORG_NO': org_no, 'DEV_CODE': dev_code_str,
                    'DEV_CLS': get_cls(dev_code_str), 'DEV_CATEG': get_cat(dev_code_str),
                    'PLAN_DIST_NUM': qty_needed, 'PLAN_BOX_NUM': used_boxes,
                    'VOL_BOX_NUM': float(vol_used), 'DIST_SEQ': step_idx + 1,
                    'LOAD_SEQ': len(path_nodes) - step_idx,
                    'DIST_SEGMENT': float(dist_segment)
                })
                Total_Delivery_Needed[sub_idx] += qty_needed
                daily_allocated[sub_idx] += qty_needed

                if sub_idx not in Last_Delivery_Date or current_date > Last_Delivery_Date[sub_idx]:
                    Last_Delivery_Date[sub_idx] = current_date

        if details_data:
            actual_load_rate = min(1.0, total_vol_used / max_cap_boxes) if max_cap_boxes > 0 else 0
            unit_price = VeUnitPrice_desc[int(ve_type) - 1] if len(VeUnitPrice_desc) >= int(ve_type) and float(
                VeUnitPrice_desc[int(ve_type) - 1]) > 0 else 0.0695
            actual_price = sum(d['PLAN_BOX_NUM'] * d['DIST_SEGMENT'] * unit_price for d in details_data)

            # CAR_TYPE 回写也必须按容量降序还原真实车型号（ve_type 是容量降序语义）
            car_type = int(VeCarType_desc[int(ve_type) - 1]) if int(ve_type) - 1 < len(VeCarType_desc) else int(ve_type)
            master_data = {'CAR_TYPE': f"0{car_type}", 'PLAN_DIST_DATE': current_date.strftime('%Y-%m-%d'),
                           'PRICE': actual_price, 'LOAD_RATE': f"{actual_load_rate * 100:.1f}%",
                           'UNIT_PRICE': unit_price}
            GlobalDelivPlan.append({'master': master_data, 'details': details_data})

    # 【诊断】装箱后：汇总实际消耗
    boxing_consumed_units = Total_Delivery_Needed.sum()
    boxing_remaining_units = Sim_Demands.sum()
    boxing_total = boxing_consumed_units + boxing_remaining_units
    boxing_satisfy = (boxing_consumed_units / boxing_total * 100) if boxing_total > 0 else 100.0
    logging.info(f"[诊断-装箱后] 实际装箱={boxing_consumed_units:,.0f}只, "
                 f"剩余未装={boxing_remaining_units:,.0f}只, "
                 f"总计={boxing_total:,.0f}只, "
                 f"满足率={boxing_satisfy:.2f}%"
                 f"{' ←缺口!' if boxing_remaining_units > 0.5 else ''}")

    # =========================================================================
    # 装箱后：按设备码汇总缺口（根因：检定合格品库存不足，配送运力已覆盖）
    # =========================================================================
    logging.info("=" * 60)
    logging.info(">>> 装箱后设备码缺口汇总（根因：检定合格品库存不足）")
    dev_gap_list = []
    for j in range(SubTypeNum):
        plan_qty = Demands[:, j].sum()
        gap = Sim_Demands[:, j].sum()
        if gap > 0.001:
            dev_code = str(SubTypeList.loc[j, 'DEV_CODE_NO']).strip()
            dev_gap_list.append((dev_code, plan_qty, plan_qty - gap, gap))

    total_demand = float(Demands.sum())
    total_delivered = total_demand - float(Sim_Demands.sum())
    coverage_pct = (total_delivered / total_demand * 100) if total_demand > 0 else 100.0
    logging.info(f"总量: 月计划={total_demand:,.0f}只 → 实际装箱={total_delivered:,.0f}只, "
                 f"满足率={coverage_pct:.2f}%")
    if dev_gap_list:
        dev_gap_list.sort(key=lambda x: -x[3])
        logging.warning(f"[检定缺口] 共 {len(dev_gap_list)} 个设备码因检定合格品库存不足未装满:")
        for dev_code, plan_qty, delivered, gap in dev_gap_list:
            logging.warning(f"  设备码={dev_code} 月计划={plan_qty:,.0f}只 "
                            f"实际装箱={delivered:,.0f}只 缺口={gap:,.0f}只")
    else:
        logging.info("[装箱后] 所有设备码100%装箱完成，无缺口")
    logging.info("=" * 60)

    return pd.DataFrame(DetectPlanResult), GlobalDelivPlan, pd.DataFrame(WorkArrangeResult)