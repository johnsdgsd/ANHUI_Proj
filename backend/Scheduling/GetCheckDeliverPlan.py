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
                        locations, org_priority=None, dev_stock=None, dev_forecast=None):
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
    # 0.5 构建网点优先级数组（索引 → 缺货概率），供 ILP 排序使用
    # =========================================================================
    node_priority = np.zeros(LocationNum + 1)  # 索引 0 = 省级总库，优先级为 0
    if org_priority:
        for i in range(1, LocationNum + 1):
            org_no = str(locations.loc[i, 'ORG_NO']).strip()
            if org_no in org_priority:
                node_priority[i] = org_priority[org_no]
        high_pri_nodes = np.sum(node_priority > 0.5)
        logging.info(f"网点优先级已加载: {high_pri_nodes} 个网点缺货概率 > 0.5, "
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
        UnitPerBoxI = unit_arr[0] if len(unit_arr) > 0 else 5
        cls_val = str(SubTypeList.loc[i, 'DEV_CLS']).replace('.0', '').strip().zfill(2) if 'DEV_CLS' in SubTypeList.columns else '01'
        vol_mult = 2.5 if cls_val == '02' else 1.0
        demand_boxes_by_loc += np.ceil(np.ceil(Demands[:, i] / UnitPerBoxI) * vol_mult)
    total_demand_boxes = demand_boxes_by_loc.sum()
    active_loc_count = np.sum(demand_boxes_by_loc > 0)
    logging.info(f"[诊断-排程前] 总需求={total_demand_units:,.0f}只, 体积加权总箱数={total_demand_boxes:,.0f}箱, "
                 f"有需求网点数={active_loc_count}/{LocationNum}")
    # =========================================================================
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

    ScheduledRoutes = GetDelivPlan(Demands, LocationNum, TypeList, SubTypeList,
                                   actual_deliv_days, VeUnitPrice, VeTypeNum, VNums, VeCap, DMAT,
                                   node_priority, daily_vehicle_limits, vehicle_types=all_vehicle_types)

    # 【诊断】排程后装箱前：汇总 ALNS 配送结果
    sched_total_boxes = 0.0
    sched_loc_boxes = defaultdict(float)
    sched_route_count = len(ScheduledRoutes)
    for trip in ScheduledRoutes:
        for cid, amt in trip['deliveries']:
            sched_total_boxes += amt
            sched_loc_boxes[cid] += amt
    sched_loc_count = len(sched_loc_boxes)
    logging.info(f"[诊断-排程后] ALNS返回{len(ScheduledRoutes)}条路线, "
                 f"配送总体积箱数={sched_total_boxes:,.0f}箱, 覆盖网点数={sched_loc_count}, "
                 f"占原始总箱数={sched_total_boxes/total_demand_boxes*100:.1f}%"
                 f"{' ←缺口!' if sched_total_boxes < total_demand_boxes - 1 else ''}")

    # ---- 缺货概率 TOP5 网点 + 设备码，含配送日期 ----
    if org_priority or (dev_stock and dev_forecast):
        logging.info("=" * 60)
        logging.info(">>> 缺货概率 TOP5 及配送日期")
        # 构建网点索引 → 最早配送日期
        loc_earliest_day = {}  # loc_1based → date_str
        for trip in ScheduledRoutes:
            day_idx = trip.get('schedule_day_idx', 0)
            if day_idx < len(work_days_list):
                d_str = work_days_list[day_idx].strftime('%Y-%m-%d') if hasattr(work_days_list[day_idx], 'strftime') else str(work_days_list[day_idx])[:10]
                for cid, _ in trip['deliveries']:
                    if cid not in loc_earliest_day or d_str < loc_earliest_day[cid]:
                        loc_earliest_day[cid] = d_str

        if org_priority:
            top_orgs = sorted(org_priority.items(), key=lambda x: x[1], reverse=True)[:5]
            logging.info("--- TOP5 高缺货网点 ---")
            for org_no, prob in top_orgs:
                # 找该网点对应的 location index
                loc_idx = None
                for i in range(1, LocationNum + 1):
                    if str(locations.loc[i, 'ORG_NO']).strip() == str(org_no).strip():
                        loc_idx = i
                        break
                deliv_date = loc_earliest_day.get(loc_idx, '未配送')
                logging.info(f"  网点={org_no} 缺货概率={prob:.4f} 最早配送日期={deliv_date}")

        if dev_stock and dev_forecast:
            dev_probs = []
            for (org_no, dev_code), forecast in dev_forecast.items():
                stock = dev_stock.get((org_no, dev_code), 0)
                prob = max(0.0, 1.0 - stock / forecast) if forecast > 0 else 1.0
                dev_probs.append((org_no, dev_code, prob, stock, forecast))
            dev_probs.sort(key=lambda x: x[2], reverse=True)
            logging.info("--- TOP5 高缺货设备码 ---")
            for org_no, dev_code, prob, stock, forecast in dev_probs[:5]:
                loc_idx = None
                for i in range(1, LocationNum + 1):
                    if str(locations.loc[i, 'ORG_NO']).strip() == str(org_no).strip():
                        loc_idx = i
                        break
                deliv_date = loc_earliest_day.get(loc_idx, '未配送')
                logging.info(f"  {org_no}/{dev_code} 库存={stock:.0f} 14天预测={forecast:.0f} 缺货概率={prob:.4f} 最早配送日期={deliv_date}")
        logging.info("=" * 60)

    GlobalDelivPlan = []
    Sim_Demands = Demands.copy()

    Total_Delivery_Needed = np.zeros(SubTypeNum)
    Last_Delivery_Date = {}
    DevCodeToIndex = {str(SubTypeList.loc[i, 'DEV_CODE_NO']).strip(): i for i in range(SubTypeNum)}

    # ---- 装箱优先级初始化：设备码级有效库存 ----
    if dev_stock is None:
        dev_stock = {}
    if dev_forecast is None:
        dev_forecast = {}
    # effective_stock[(org_no, dev_code)] = 初始库存 + 累计已配送量
    effective_stock = {}

    # 按配送日期排序，确保逐日推算库存水平
    sorted_trips = sorted(ScheduledRoutes, key=lambda t: t.get('schedule_day_idx', 0))

    # 预计算设备码属性缓存（避免循环内重复查表）
    dev_vol_mult = {}   # dev_code → 体积系数
    dev_unit_box = {}   # dev_code → 每箱只数
    for sub_idx in range(SubTypeNum):
        dc = str(SubTypeList.loc[sub_idx, 'DEV_CODE_NO']).strip()
        dev_vol_mult[dc] = 2.5 if get_cls(dc) == '02' else 1.0
        unit_arr = TypeList.loc[TypeList['DEV_CODE_NO'] == dc, 'UnitPerBox'].values
        dev_unit_box[dc] = unit_arr[0] if len(unit_arr) > 0 else 5

    # 可装箱设备码列表（预过滤：存在需求且在 dev_forecast 中有记录）
    active_dev_indices = list(range(SubTypeNum))

    for trip in sorted_trips:
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
            vol_remaining = de_nums[step_idx]
            dist_segment = DMAT[prev_node, loc_1based] if isinstance(DMAT, np.ndarray) else DMAT.values[
                prev_node, loc_1based]
            prev_node = loc_1based
            org_no = str(locations.loc[loc_1based, 'ORG_NO']).strip()

            # ---- 贪心逐箱分配：每次选缺货概率最大的设备码装1箱 ----
            alloc_map = {}  # dev_code → 累计分配只数

            while vol_remaining > 0.0001:
                remaining_cap = max_cap_boxes - total_vol_used
                if remaining_cap <= 0.001:
                    break

                # 找缺货概率最大的设备码（需能装下至少1箱）
                # tiebreaker: 概率相同时选完成率最低的设备码（防止大数据量设备霸占所有箱数）
                best_dev_idx = -1
                best_prob = -1.0
                best_fulfilled_pct = 2.0  # 完成率(0~1), 越小越优先, 初始>1保证首次选中
                best_dev_code = None

                for sub_idx in active_dev_indices:
                    remaining_demand = Sim_Demands[loc_1based - 1, sub_idx]
                    if remaining_demand <= 0:
                        continue
                    dc = str(SubTypeList.loc[sub_idx, 'DEV_CODE_NO']).strip()
                    vm = dev_vol_mult.get(dc, 1.0)
                    if vm > remaining_cap + 0.001:
                        continue  # 装不下1箱

                    # 当前有效库存 = 初始库存 + 已分配量
                    key = (org_no, dc)
                    init_stock = dev_stock.get(key, 0)
                    cur_stock = effective_stock.get(key, init_stock)
                    forecast = dev_forecast.get(key, 0)

                    if forecast <= 0:
                        prob = 1.0
                    else:
                        prob = max(0.0, 1.0 - cur_stock / forecast)

                    # 完成率 = 已分配量 / 月计划量（越小越需要补充分配）
                    plan_qty = Demands[loc_1based - 1, sub_idx]
                    fulfilled_pct = (plan_qty - remaining_demand) / plan_qty if plan_qty > 0 else 1.0

                    # 概率优先；概率相同时完成率低的优先
                    if prob > best_prob or (abs(prob - best_prob) < 0.0001 and fulfilled_pct < best_fulfilled_pct):
                        best_prob = prob
                        best_fulfilled_pct = fulfilled_pct
                        best_dev_idx = sub_idx
                        best_dev_code = dc

                if best_dev_idx < 0:
                    break  # 所有设备码都装不下或需求已耗尽

                # 分配1箱
                unit_per_box = dev_unit_box.get(best_dev_code, 5)
                vm = dev_vol_mult.get(best_dev_code, 1.0)

                qty_per_box = min(Sim_Demands[loc_1based - 1, best_dev_idx], unit_per_box)
                used_boxes = math.ceil(qty_per_box / unit_per_box)
                vol_used = used_boxes * vm

                if total_vol_used + vol_used > max_cap_boxes + 0.001:
                    break  # 容量不够装完整箱

                total_vol_used += vol_used
                vol_remaining -= vol_used
                Sim_Demands[loc_1based - 1, best_dev_idx] -= qty_per_box

                # 更新有效库存 → 降低该设备码后续缺货概率
                key = (org_no, best_dev_code)
                init_stock = dev_stock.get(key, 0)
                effective_stock[key] = effective_stock.get(key, init_stock) + qty_per_box

                # 聚合到同设备码
                alloc_map[best_dev_code] = alloc_map.get(best_dev_code, 0) + qty_per_box

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

    # 【诊断】装箱后：汇总实际消耗
    boxing_consumed_units = Total_Delivery_Needed.sum()
    boxing_remaining_units = Sim_Demands.sum()
    boxing_total = boxing_consumed_units + boxing_remaining_units
    logging.info(f"[诊断-装箱后] 实际装箱={boxing_consumed_units:,.0f}只, "
                 f"剩余未装={boxing_remaining_units:,.0f}只, "
                 f"总计={boxing_total:,.0f}只, "
                 f"满足率={boxing_consumed_units/boxing_total*100:.2f}%"
                 f"{' ←缺口!' if boxing_remaining_units > 0.5 else ''}")

    # =========================================================================
    # 配送 vs 月补库计划对比
    # =========================================================================
    logging.info("=" * 60)
    logging.info(">>> 配送执行 vs 月补库计划 对比核验")
    total_demand = 0.0
    total_delivered = 0.0
    mismatch_count = 0
    mismatch_details = []

    location_org_map = {i + 1: str(locations.loc[i + 1, 'ORG_NO']).strip() for i in range(LocationNum)}

    for i in range(LocationNum):
        org_no = location_org_map.get(i + 1, f'LOC_{i+1}')
        for j in range(SubTypeNum):
            plan_qty = Demands[i, j]
            if plan_qty <= 0:
                continue
            remaining = Sim_Demands[i, j]
            delivered = plan_qty - remaining
            total_demand += plan_qty
            total_delivered += delivered
            if remaining > 0.001:
                dev_code = str(SubTypeList.loc[j, 'DEV_CODE_NO']).strip()
                mismatch_count += 1
                mismatch_details.append(f"  [{mismatch_count}] 网点={org_no} 设备码={dev_code} "
                                       f"月计划={plan_qty:.0f}只 实际配送={delivered:.0f}只 缺口={remaining:.0f}只")

    coverage_pct = (total_delivered / total_demand * 100) if total_demand > 0 else 100
    logging.info(f"总量: 月计划={total_demand:,.0f}只 → 实际配送={total_delivered:,.0f}只, "
                 f"满足率={coverage_pct:.2f}%")
    if mismatch_count > 0:
        logging.warning(f"[配送缺口] 共 {mismatch_count} 处网点-设备码未满足月补库计划:")
        for detail in mismatch_details:
            logging.warning(detail)
    else:
        logging.info("[配送核验] 所有网点-设备码月补库计划100%满足!")
    logging.info("=" * 60)

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
        earliest_bgn = max(arr_dt + timedelta(days=2), sim_start_dt)
        last_dist_date = Last_Delivery_Date.get(sub_idx, sim_start_dt)
        is_actually_needed = (Inspect_Target[sub_idx] > 0) and (earliest_bgn <= last_dist_date)

        # 混合备货策略：前中期全部兜底囤货，月末压线严格按需检定
        if is_realtime:
            take = rem
        elif days_to_end >= 2:
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
        actual_detected_num = int(lot['orig_take'])

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

    logging.info(f"LotObjects: {len(LotObjects)}个批次, 产出DetectPlan: {len(DetectPlanResult)}条")
    return pd.DataFrame(DetectPlanResult), GlobalDelivPlan, pd.DataFrame(WorkArrangeResult)