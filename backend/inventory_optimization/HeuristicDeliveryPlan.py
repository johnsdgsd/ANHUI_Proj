"""
启发式日配送调整算法 V2
参考 Scheduling/GetDelivPlan.py 的启发式框架，支持可配置站点数，优化满载率。
与 DailyReplenishmentPlan.AdjustDaliyDelivery 输出格式兼容。
"""
import numpy as np
import pandas as pd
import random
import copy
import math
import itertools
import logging
import sys
import time
import datetime
from collections import defaultdict

from backend.inventory_optimization.DailyReplenishmentPlan import (
    LoadDelivData, GenerateDelivPlan, GenerateSchemeTables
)


# ==================== 启发式核心函数（模块级） ====================

def _calc_route_cost(route, DMAT_arr, VeUnitPrice):
    """计算路径运输成本（省中心 → 各站点）"""
    total = 0.0
    prev = 0
    v_idx = int(route.get('vehicle_type', 1)) - 1
    price = float(VeUnitPrice[v_idx])
    for cid, amt in route['deliveries']:
        total += amt * DMAT_arr[prev, cid] * price
        prev = cid
    return total


def _calc_round_trip_dist(route, DMAT_arr):
    """计算路径往返总里程: 省中心 → 各站 → 省中心"""
    deliveries = route['deliveries']
    if not deliveries:
        return 0.0
    dist = DMAT_arr[0, deliveries[0][0]]  # depot → 首站
    for i in range(len(deliveries) - 1):
        dist += DMAT_arr[deliveries[i][0], deliveries[i + 1][0]]
    dist += DMAT_arr[deliveries[-1][0], 0]  # 末站 → depot
    return dist


def _max_sector_angle(station_indices, lons, lats):
    """以省中心(索引0)为顶点，计算覆盖给定站点的最小扇形角度"""
    if len(station_indices) <= 1:
        return 0.0
    depot_lat, depot_lon = math.radians(lats[0]), math.radians(lons[0])
    bearings = []
    for i in station_indices:
        lat_i = math.radians(lats[i])
        lon_i = math.radians(lons[i])
        dlon = lon_i - depot_lon
        y = math.sin(dlon) * math.cos(lat_i)
        x = (math.cos(depot_lat) * math.sin(lat_i)
             - math.sin(depot_lat) * math.cos(lat_i) * math.cos(dlon))
        b = math.degrees(math.atan2(y, x))
        bearings.append((b + 360) % 360)
    bearings.sort()
    max_gap = max(
        (bearings[i + 1] - bearings[i] for i in range(len(bearings) - 1)),
        default=0
    )
    max_gap = max(max_gap, 360 - (bearings[-1] - bearings[0]))
    return 360 - max_gap


def _eval_route_fitness(route, DMAT_arr, VEHICLE_CONFIG, VeUnitPrice, lons=None, lats=None):
    """评估路径适应度（成本 + 满载率惩罚 + 超距软约束，越低越好）"""
    cost = _calc_route_cost(route, DMAT_arr, VeUnitPrice)
    if not route['deliveries']:
        return cost
    cap = next(v['cap'] for v in VEHICLE_CONFIG if v['type'] == route.get('vehicle_type', 1))
    load = sum(a for _, a in route['deliveries'])
    rate = load / cap if cap > 0 else 0

    penalty = 0.0
    if rate < 0.8:
        penalty = (10000 + cost * 100) * ((0.8 - rate) ** 2)

    round_trip = _calc_round_trip_dist(route, DMAT_arr)
    if round_trip > 750:
        penalty += 1e10 * (round_trip - 750)

    if lons is not None and lats is not None:
        station_indices = [cid for cid, _ in route['deliveries']]
        angle = _max_sector_angle(station_indices, lons, lats)
        if angle > 45:
            penalty += 1e10 * (angle - 45)
            logging.info(f"[角度约束] 适应度惩罚: 站点={station_indices}, 夹角={angle:.1f}° > 45°, "
                          f"惩罚={1e10 * (angle - 45):.0f}")

    # 单位载重率综合成本: (固定基数 + 运输成本 + 惩罚) / (满载率 + 防除零)
    # 固定基数 500 避免 cost→0 时分母效应放大; 防除零 0.1 保证满载率为 0 时不会除零
    return (500.0 + cost + penalty) / (rate + 0.1)


def _optimize_route_sequence(route, DMAT_arr, VeUnitPrice):
    """排列路径站点顺序，最短路径优先"""
    deliveries = route['deliveries']
    if len(deliveries) <= 1:
        return route
    best_cost = float('inf')
    best_seq = None
    for seq in itertools.permutations(deliveries):
        tmp = {'vehicle_type': route.get('vehicle_type', 1), 'deliveries': list(seq)}
        c = _calc_route_cost(tmp, DMAT_arr, VeUnitPrice)
        if c < best_cost:
            best_cost = c
            best_seq = list(seq)
    route['deliveries'] = best_seq
    return route


def _reassign_vehicles(routes, VEHICLE_CONFIG):
    """车辆重分配：将路线分配给最小的能装下的车型"""
    quotas = {cfg['type']: cfg['daily_max'] for cfg in VEHICLE_CONFIG}
    sorted_routes = sorted(routes, key=lambda r: sum(a for _, a in r['deliveries']), reverse=True)
    for r in sorted_routes:
        load = sum(a for _, a in r['deliveries'])
        assigned = False
        for cfg in VEHICLE_CONFIG:
            if cfg['cap'] >= load and quotas[cfg['type']] > 0:
                r['vehicle_type'] = cfg['type']
                quotas[cfg['type']] -= 1
                assigned = True
                break
        if not assigned:
            for cfg in VEHICLE_CONFIG:
                if cfg['cap'] >= load:
                    r['vehicle_type'] = cfg['type']
                    quotas[cfg['type']] -= 1
                    assigned = True
                    break
        if not assigned:
            r['vehicle_type'] = VEHICLE_CONFIG[-1]['type']
            quotas[VEHICLE_CONFIG[-1]['type']] -= 1
    return sorted_routes


def _generate_initial_solution(unassigned, VEHICLE_CONFIG, max_stops, DMAT_arr, VeUnitPrice,
                               lons=None, lats=None):
    """贪心构造初始解：需求大的优先，尽量合并到已有路径"""
    routes = []
    angle_checks, angle_blocks = 0, 0
    pending = sorted(unassigned.items(), key=lambda x: x[1], reverse=True)
    for cid, total_amt in pending:
        amt = total_amt
        while amt > 0:
            found = False
            for r in routes:
                cap = next(v['cap'] for v in VEHICLE_CONFIG if v['type'] == r.get('vehicle_type', 1))
                space = cap - sum(a for _, a in r['deliveries'])
                has_cid = any(c == cid for c, _ in r['deliveries'])
                # 新增站点时检查 45° 夹角硬约束
                angle_ok = True
                if not has_cid and lons is not None and lats is not None:
                    test_indices = [c for c, _ in r['deliveries']] + [cid]
                    sector = _max_sector_angle(test_indices, lons, lats)
                    angle_checks += 1
                    angle_ok = sector <= 45
                    if not angle_ok:
                        angle_blocks += 1
                        logging.debug(f"[角度约束] 初始构造: 站点{cid}无法加入路线 "
                                      f"(当前站点={[c for c, _ in r['deliveries']]}, 夹角={sector:.1f}° > 45°)")
                if space > 0 and (has_cid or (len(r['deliveries']) < max_stops and angle_ok)):
                    load = min(amt, space)
                    if has_cid:
                        for i, (c, a) in enumerate(r['deliveries']):
                            if c == cid:
                                r['deliveries'][i] = (c, a + load)
                                break
                    else:
                        r['deliveries'].append((cid, load))
                    r = _optimize_route_sequence(r, DMAT_arr, VeUnitPrice)
                    amt -= load
                    found = True
                    break
            if not found:
                cfg = VEHICLE_CONFIG[-1]
                for c in VEHICLE_CONFIG:
                    if c['cap'] >= amt:
                        cfg = c
                        break
                load = min(amt, cfg['cap'])
                routes.append({'vehicle_type': cfg['type'], 'deliveries': [(cid, load)]})
                amt -= load
    if angle_checks > 0:
        logging.info(f"[角度约束] 初始构造完成: 检查{angle_checks}次, 拦截{angle_blocks}次")
    return routes


def _random_removal(solution, num_remove):
    """随机破坏：从解中移除若干配送任务"""
    sol = copy.deepcopy(solution)
    unassigned = defaultdict(float)
    for _ in range(num_remove):
        if not sol:
            break
        ri = random.randint(0, len(sol) - 1)
        route = sol[ri]
        if route['deliveries']:
            pi = random.randint(0, len(route['deliveries']) - 1)
            cid, amt = route['deliveries'].pop(pi)
            unassigned[cid] += amt
        if not route['deliveries']:
            sol.pop(ri)
    return sol, unassigned


def _greedy_insertion(routes, unassigned, VEHICLE_CONFIG, max_stops, DMAT_arr, VeUnitPrice,
                      lons=None, lats=None):
    """贪心修复：将移除的需求重新插入最优位置"""
    pending = sorted(unassigned.items(), key=lambda x: x[1], reverse=True)
    for cid, amt in pending:
        while amt > 0:
            best_fit = float('inf')
            best_action = None
            for ri, route in enumerate(routes):
                has_cid = any(c == cid for c, _ in route['deliveries'])
                if not has_cid and len(route['deliveries']) >= max_stops:
                    continue
                # 新增站点时检查 45° 夹角硬约束
                if not has_cid and lons is not None and lats is not None:
                    test_indices = [c for c, _ in route['deliveries']] + [cid]
                    sector = _max_sector_angle(test_indices, lons, lats)
                    if sector > 45:
                        logging.debug(f"[角度约束] 贪心插入: 站点{cid}无法插入路线 "
                                      f"(当前站点={[c for c, _ in route['deliveries']]}, 夹角={sector:.1f}° > 45°)")
                        continue
                cap = next(v['cap'] for v in VEHICLE_CONFIG if v['type'] == route.get('vehicle_type', 1))
                space = cap - sum(a for _, a in route['deliveries'])
                if space > 0:
                    ins_amt = min(amt, space)
                    tmp = copy.deepcopy(route)
                    if has_cid:
                        for i, (c, a) in enumerate(tmp['deliveries']):
                            if c == cid:
                                tmp['deliveries'][i] = (c, a + ins_amt)
                                break
                    else:
                        tmp['deliveries'].append((cid, ins_amt))
                    tmp = _optimize_route_sequence(tmp, DMAT_arr, VeUnitPrice)
                    fit = _eval_route_fitness(tmp, DMAT_arr, VEHICLE_CONFIG, VeUnitPrice, lons, lats)
                    if fit < best_fit:
                        best_fit = fit
                        best_action = ('insert', ri, ins_amt, tmp['deliveries'])
            if best_action is None:
                cfg = VEHICLE_CONFIG[-1]
                for c in VEHICLE_CONFIG:
                    if c['cap'] >= amt:
                        cfg = c
                        break
                ins_amt = min(amt, cfg['cap'])
                tmp = {'vehicle_type': cfg['type'], 'deliveries': [(cid, ins_amt)]}
                best_action = ('new', cfg['type'], ins_amt, tmp['deliveries'])
            if best_action[0] == 'insert':
                routes[best_action[1]]['deliveries'] = best_action[3]
                amt -= best_action[2]
            else:
                routes.append({'vehicle_type': best_action[1], 'deliveries': best_action[3]})
                amt -= best_action[2]
    return [r for r in routes if r['deliveries']]


def _build_delivery_plan(best_sol, Demands, SubTypeList, VeCap, VeUnitPrice,
                          CarTypeStrList, date, DMAT_arr, org_labels):
    """将启发式解转换为 DelivPlan DataFrame，再生成主表和明细表"""
    # org_labels = ["中心", org1, org2, ...] — 来自 DMAT 列标签，与 DMAT_arr 行列顺序一致
    org_lookup = {i: org_labels[i] for i in range(1, len(org_labels))}

    records = []
    for idx, route in enumerate(best_sol):
        deliveries = route['deliveries']
        node_ids = [cid for cid, _ in deliveries]
        amounts = [amt for _, amt in deliveries]
        v_type = route.get('vehicle_type', 1)
        v_idx = v_type - 1

        # 计算路径总距离：省中心→第一站→...→最后一站
        total_dist = DMAT_arr[0, node_ids[0]]
        seg_dists = [total_dist]  # 第一站的分段距离
        for j in range(len(node_ids) - 1):
            seg = DMAT_arr[node_ids[j], node_ids[j + 1]]
            total_dist += seg
            seg_dists.append(seg)


        # debug: segment distances (node_ids 映射为 ORG_NO)
        seg_orgs = "->".join(str(org_lookup.get(n, n)) for n in node_ids)
        seg_km = ",".join(f"{d:.1f}" for d in seg_dists)
        logging.info(f"[V2 seg] depo->" + seg_orgs + f", seg_km=[" + seg_km + f"], total={total_dist:.1f}km")
        records.append({
            'PathInd': 1,
            'VeType': v_type,
            'Price': 0.0,  # 后补
            'PlanPath': node_ids,
            'DeNum': amounts,
            'SegDis': seg_dists,
            'PathDis': total_dist
        })

    DelivPlan = pd.DataFrame(records)

    # 计算价格：里程逐站累进
    for i, row in DelivPlan.iterrows():
        DeNum = row['DeNum']
        PlanPath = row['PlanPath']
        total_boxes = sum(DeNum)
        v_idx = int(row['VeType']) - 1
        # loaded_cost = Σ (到达该站的里程 × 该站箱数 × 单价)
        loaded_cost = DMAT_arr[0, PlanPath[0]] * DeNum[0] * VeUnitPrice[v_idx]
        for j in range(1, len(PlanPath)):
            loaded_cost += DMAT_arr[PlanPath[j-1], PlanPath[j]] * DeNum[j] * VeUnitPrice[v_idx]
        empty_cost = VeUnitPrice[v_idx] * 0.5 * (VeCap[v_idx] - total_boxes) * row['PathDis']
        DelivPlan.at[i, 'Price'] = loaded_cost + empty_cost

    # PathNo 映射
    Path_no = []
    for planpath in DelivPlan['PlanPath']:
        p = [org_lookup.get(n, str(n)) for n in planpath]
        Path_no.append(p)
    DelivPlan['PathNo'] = Path_no

    DelivPlan = GenerateDelivPlan(DelivPlan, Demands, SubTypeList)

    # 校验：日补库需求总量 vs 配送计划总量
    total_demand_pieces_v2 = Demands.values.sum()
    total_deliv_pieces_v2 = sum(
        sum(sp) for _, row in DelivPlan.iterrows() for sp in row['DevicePieces']
    )
    total_deliv_boxes_v2 = sum(sum(row['DeNum']) for _, row in DelivPlan.iterrows())
    logging.info(f"[V2校验] 日补库需求总件数: {int(total_demand_pieces_v2)}, 配送计划总件数: {int(total_deliv_pieces_v2)}, 差异: {int(total_demand_pieces_v2 - total_deliv_pieces_v2)}")
    logging.info(f"[V2校验] 配送计划总箱数: {int(total_deliv_boxes_v2)}")

    MainScheme, DetailScheme = GenerateSchemeTables(
        DelivPlan, date, SubTypeList, VeCap, CarTypeStrList
    )
    return MainScheme, DetailScheme


def _merge_scheme_tables(MainDf, DetailDf, VeCap, CarTypeStrList, max_stops, DMAT_arr, org_labels):
    """归并低满载率配送方案（基于重算后的真实装载率）"""
    if len(MainDf) <= 1:
        logging.info("无需归并")
        return MainDf, DetailDf

    org_to_idx = {str(org): i for i, org in enumerate(org_labels)}
    car_type_to_idx = {ct: i for i, ct in enumerate(CarTypeStrList)}

    main_rows = MainDf.to_dict('records')
    detail_groups = {}
    for _, row in DetailDf.iterrows():
        sid = row['DIST_SCHEME_ID']
        detail_groups.setdefault(sid, []).append(row.to_dict())

    total_merged = 0

    while True:
        best_pair = None
        best_combined_rate = -1.0

        for i in range(len(main_rows)):
            for j in range(i + 1, len(main_rows)):
                if main_rows[i]['CAR_TYPE'] != main_rows[j]['CAR_TYPE']:
                    continue
                ri = float(str(main_rows[i]['LOAD_RATE']).rstrip('%'))
                rj = float(str(main_rows[j]['LOAD_RATE']).rstrip('%'))
                if ri + rj >= 100:
                    continue

                sid_i = main_rows[i]['DIST_SCHEME_ID']
                sid_j = main_rows[j]['DIST_SCHEME_ID']
                stops_i = {str(d['REC_ORG_NO']) for d in detail_groups.get(sid_i, [])}
                stops_j = {str(d['REC_ORG_NO']) for d in detail_groups.get(sid_j, [])}
                if len(stops_i | stops_j) > max_stops:
                    continue

                if ri + rj > best_combined_rate:
                    best_combined_rate = ri + rj
                    best_pair = (i, j)

        if best_pair is None:
            break

        i, j = best_pair
        sid_i = main_rows[i]['DIST_SCHEME_ID']
        sid_j = main_rows[j]['DIST_SCHEME_ID']
        car_type = main_rows[i]['CAR_TYPE']
        rate_i = float(str(main_rows[i]['LOAD_RATE']).rstrip('%'))
        rate_j = float(str(main_rows[j]['LOAD_RATE']).rstrip('%'))

        # 合并明细行
        combined_details = detail_groups.pop(sid_i, []) + detail_groups.pop(sid_j, [])

        # 按站点汇总箱数，确定最优访问顺序
        stop_boxes = defaultdict(float)
        for d in combined_details:
            stop_boxes[str(d['REC_ORG_NO'])] += float(d['PLAN_BOX_NUM'])

        unique_stops = list(stop_boxes.keys())
        best_order = unique_stops
        best_path_dist = float('inf')
        for perm in itertools.permutations(unique_stops):
            dist = DMAT_arr[0, org_to_idx[perm[0]]]
            for k in range(len(perm) - 1):
                dist += DMAT_arr[org_to_idx[perm[k]], org_to_idx[perm[k + 1]]]
            if dist < best_path_dist:
                best_path_dist = dist
                best_order = list(perm)

        # 重建明细行（保留原始 DIST_SCHEME_DET_ID，DIST_SCHEME_ID 沿用 sid_i）
        total_merged += 1
        new_details = []
        for pos, org in enumerate(best_order):
            dist_seq = pos + 1
            load_seq = len(best_order) - pos
            org_details = [d for d in combined_details if str(d['REC_ORG_NO']) == org]
            for d in org_details:
                nd = d.copy()
                nd['DIST_SCHEME_ID'] = sid_i
                nd['DIST_SEQ'] = dist_seq
                nd['LOAD_SEQ'] = load_seq
                # 重新计算分段里程
                if pos == 0:
                    nd['EST_TOT_DIST_MIST'] = round(DMAT_arr[0, org_to_idx[org]], 4)
                else:
                    prev_org = best_order[pos - 1]
                    nd['EST_TOT_DIST_MIST'] = round(DMAT_arr[org_to_idx[prev_org], org_to_idx[org]], 4)
                new_details.append(nd)

        # 计算新装载率（互感器 ×2.5）
        real_boxes = 0.0
        for d in new_details:
            boxes = float(d['PLAN_BOX_NUM'])
            if d.get('DEV_CLS') == '02':
                boxes *= 2.5
            real_boxes += boxes
        ve_idx = car_type_to_idx.get(car_type, -1)
        ve_cap = VeCap[ve_idx] if 0 <= ve_idx < len(VeCap) else 1
        new_load_rate = f"{min(real_boxes / ve_cap * 100, 100.0):.1f}%"

        # 更新主表行（保留 sid_i，更新装载率，移除 sid_j）
        main_rows[i]['LOAD_RATE'] = new_load_rate
        main_rows[i]['UPDATE_DATE'] = datetime.datetime.now().strftime('%Y-%m-%d')
        main_rows = [r for idx, r in enumerate(main_rows) if idx != j]
        detail_groups[sid_i] = new_details

        logging.info(f"[归并] {car_type}: 合并2条方案, "
                     f"原满载率={rate_i:.1f}%+{rate_j:.1f}%→{new_load_rate}, 站点数={len(best_order)}")

    if total_merged > 0:
        # 重建 DataFrame
        all_details = [d for details in detail_groups.values() for d in details]
        new_MainDf = pd.DataFrame(main_rows)
        new_DetailDf = pd.DataFrame(all_details) if all_details else pd.DataFrame(columns=DetailDf.columns)
        logging.info(f"[归并] 共归并 {total_merged} 对方案, 最终 {len(new_MainDf)} 条主表记录")
        return new_MainDf, new_DetailDf
    else:
        logging.info("无需归并")
        return MainDf, DetailDf


# ==================== 主入口 ====================

def AdjustDaliyDeliveryV2(date: str, max_stops: int = 3, max_iter: int = 600):
    """
    启发式日配送调整算法 V2。

    参数:
        date:       配送日期，格式 'YYYY-MM-DD'
        max_stops:  每条路径最多经过的站点数（默认 5）
        max_iter:   启发式迭代次数（默认 600）

    返回:
        (MainScheme, DetailScheme) — 与 AdjustDaliyDelivery 格式一致
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        stream=sys.stdout
    )

    try:
        return _adjust_daily_delivery_v2_impl(date, max_stops, max_iter)
    except Exception:
        logging.exception(f"AdjustDaliyDeliveryV2 异常: date={date}, max_stops={max_stops}, max_iter={max_iter}")
        raise


def _adjust_daily_delivery_v2_impl(date: str, max_stops: int, max_iter: int):
    """V2 算法实现体"""

    from backend.api.data_api.fetch_data import (
        query_adam_dist_scheme_by_date_range,
        delete_adam_dist_scheme_det_by_scheme_id,
        delete_adam_dist_scheme_by_id
    )

    # 删除当天未确认(DIST_FLAG!='02')的配送方案，保留已确认的
    try:
        existing = query_adam_dist_scheme_by_date_range(date, date)
        if not existing.empty:
            unconfirmed = existing[existing['DIST_FLAG'] != '02']
            for sid in unconfirmed['DIST_SCHEME_ID'].tolist():
                delete_adam_dist_scheme_det_by_scheme_id(sid)
                delete_adam_dist_scheme_by_id(sid)
            kept = len(existing) - len(unconfirmed)
            logging.info(f"已删除 {len(unconfirmed)} 条未确认方案" + (f"，保留 {kept} 条已确认方案" if kept > 0 else ""))
    except ValueError:
        logging.info(f"当天 ({date}) 无旧配送方案，跳过删除")

    # ---- 1. 载入数据 ----
    logging.info("=" * 60)
    logging.info(f"V2 启发式日配送开始: date={date}, max_stops={max_stops}, max_iter={max_iter}")
    t_start = time.time()

    Demands, LocationNum, SubTypeList, VeUnitPrice, VeTypeNum, VNums, VeCap, DMAT, _, CarTypeStrList, lons, lats = LoadDelivData(date)
    lons = [float(x) for x in lons]
    lats = [float(x) for x in lats]
    logging.info(f"[角度约束] 已启用，省中心=({lons[0]:.4f},{lats[0]:.4f})，配送站点={len(lons)-1}个")
    org_labels = DMAT.columns.tolist()  # ["中心", org1, org2, ...]
    SubTypeNum = len(SubTypeList)
    logging.info(f"数据载入完成: {LocationNum} 个配送站点, {SubTypeNum} 种设备, {VeTypeNum} 种车型")
    logging.info(f"车型容量: {[int(c) for c in VeCap]}, 各车型数量: {[int(n) for n in VNums]}")

    # 箱数转换
    DemandsBoxs = np.zeros((LocationNum, SubTypeNum))
    total_pieces = 0
    for i in range(SubTypeNum):
        UnitPerBoxI = SubTypeList.loc[i, 'PACK_BOX_NUM']
        pieces = Demands.loc[:, i].values
        total_pieces += np.sum(pieces)
        DemandsBoxs[:, i] = np.ceil(pieces / UnitPerBoxI)
    DemandsBoxs = np.sum(DemandsBoxs, axis=1)
    total_boxes = np.sum(DemandsBoxs)
    unit_sum = {i + 1: float(DemandsBoxs[i]) for i in range(LocationNum) if DemandsBoxs[i] > 0}
    logging.info(f"箱数转换完成: 总件数={int(total_pieces)}, 总箱数={int(total_boxes)}, 有需求站点={len(unit_sum)}")
    if unit_sum:
        logging.info(f"站点需求分布: min={min(unit_sum.values()):.0f}, max={max(unit_sum.values()):.0f}, avg={total_boxes/len(unit_sum):.1f}")

    empty_cols = {
        'main': ['DIST_SCHEME_ID', 'CAR_TYPE', 'PLAN_DIST_DATE', 'DIST_FLAG', 'LATE_FLAG',
                 'LOAD_RATE', 'CREATE_DATE', 'UPDATE_DATE', 'GLOBAL_SCHEME_ID'],
        'detail': ['DIST_SCHEME_DET_ID', 'DIST_SCHEME_ID', 'REC_ORG_NO', 'DEV_CODE',
                   'DEV_CLS', 'DEV_CATEG', 'DIST_SEQ', 'LOAD_SEQ', 'PLAN_DIST_NUM',
                   'PLAN_BOX_NUM', 'EST_TOT_DIST_MIST', 'DIST_EXP', 'GLOBAL_SCHEME_ID']
    }
    if not unit_sum:
        logging.warning("无配送需求，返回空方案")
        return pd.DataFrame(columns=empty_cols['main']), pd.DataFrame(columns=empty_cols['detail'])
    if sum(VNums) == 0:
        logging.warning("无可用车辆（全部被已确认方案占用），返回空方案")
        return pd.DataFrame(columns=empty_cols['main']), pd.DataFrame(columns=empty_cols['detail'])

    # 距离矩阵（LoadDelivData 已构建对称矩阵）
    DMAT_arr = DMAT.values

    # 车型配置（按容量升序）
    VEHICLE_CONFIG = sorted(
        [{'type': i + 1, 'cap': int(VeCap[i]), 'daily_max': int(VNums[i])} for i in range(VeTypeNum)],
        key=lambda x: x['cap']
    )
    total_vehicle_capacity = sum(c['cap'] * c['daily_max'] for c in VEHICLE_CONFIG)
    logging.info(f"总运力: {total_vehicle_capacity} 箱, 需求: {int(total_boxes)} 箱, 运力利用率预估: {total_boxes/total_vehicle_capacity*100:.1f}%")

    # ---- 2. 启发式求解 ----
    logging.info("-" * 40)
    logging.info(f"开始启发式求解: {len(unit_sum)} 站点, {VeTypeNum} 车型, 每车最多 {max_stops} 站")

    t_solve = time.time()
    best_sol = _generate_initial_solution(unit_sum, VEHICLE_CONFIG, max_stops, DMAT_arr, VeUnitPrice,
                                          lons, lats)
    logging.info(f"初始构造完成: {len(best_sol)} 条路径, 平均每车 {len(unit_sum)/max(1,len(best_sol)):.1f} 站")

    best_sol = _reassign_vehicles(best_sol, VEHICLE_CONFIG)
    best_fitness = sum(_eval_route_fitness(r, DMAT_arr, VEHICLE_CONFIG, VeUnitPrice, lons, lats) for r in best_sol)
    init_load_rates = []
    for r in best_sol:
        cap = next(v['cap'] for v in VEHICLE_CONFIG if v['type'] == r.get('vehicle_type', 1))
        load = sum(a for _, a in r['deliveries'])
        init_load_rates.append(load / cap * 100 if cap > 0 else 0)
    logging.info(f"初始解: {len(best_sol)} 条路径, fitness={best_fitness:.2f}, "
                 f"满载率 avg={np.mean(init_load_rates):.1f}%, min={np.min(init_load_rates):.1f}%")

    improved = 0
    accepted_worse = 0
    for i in range(max_iter):
        remove_cnt = max(2, int(len(unit_sum) * 0.3))
        destroyed, unassigned = _random_removal(best_sol, num_remove=remove_cnt)
        new_sol = _greedy_insertion(destroyed, unassigned, VEHICLE_CONFIG, max_stops, DMAT_arr, VeUnitPrice,
                                     lons, lats)
        new_sol = _reassign_vehicles(new_sol, VEHICLE_CONFIG)
        new_fitness = sum(_eval_route_fitness(r, DMAT_arr, VEHICLE_CONFIG, VeUnitPrice, lons, lats) for r in new_sol)

        if new_fitness < best_fitness:
            best_sol, best_fitness = new_sol, new_fitness
            improved += 1
        elif math.exp((best_fitness - new_fitness) / 100) > random.random():
            best_sol, best_fitness = new_sol, new_fitness
            accepted_worse += 1

        if (i + 1) % 100 == 0:
            routes_now = len(best_sol)
            rates_now = []
            for r in best_sol:
                cap = next(v['cap'] for v in VEHICLE_CONFIG if v['type'] == r.get('vehicle_type', 1))
                load = sum(a for _, a in r['deliveries'])
                rates_now.append(load / cap * 100 if cap > 0 else 0)
            logging.info(f"迭代 {i+1}/{max_iter}: {routes_now} 条路径, fitness={best_fitness:.2f}, "
                         f"满载率 avg={np.mean(rates_now):.1f}%, 改进={improved}, 退火接受={accepted_worse}")

    best_sol = _reassign_vehicles(best_sol, VEHICLE_CONFIG)
    t_solve_end = time.time()
    logging.info(f"求解耗时: {t_solve_end - t_solve:.1f}s, 改进次数={improved}, 退火接受={accepted_worse}")

    # ---- 3. 最终结果统计 ----
    logging.info("-" * 40)
    logging.info(f"最终解: {len(best_sol)} 条路径")
    final_rates = []
    type_counts = defaultdict(int)
    type_loads = defaultdict(list)
    stop_counts = []
    for r in best_sol:
        cap = next(v['cap'] for v in VEHICLE_CONFIG if v['type'] == r.get('vehicle_type', 1))
        load = sum(a for _, a in r['deliveries'])
        rate = load / cap * 100 if cap > 0 else 0
        final_rates.append(rate)
        type_counts[r['vehicle_type']] += 1
        type_loads[r['vehicle_type']].append(rate)
        stop_counts.append(len(r['deliveries']))
        logging.info(f"  车型={r['vehicle_type']}, 站点数={len(r['deliveries'])}, 箱数={load}/{cap}, 满载率={rate:.1f}%")

    logging.info(f"满载率统计: avg={np.mean(final_rates):.1f}%, min={np.min(final_rates):.1f}%, max={np.max(final_rates):.1f}%")
    logging.info(f"站点数统计: avg={np.mean(stop_counts):.1f}, min={np.min(stop_counts)}, max={np.max(stop_counts)}")
    for vt in sorted(type_counts.keys()):
        rates = type_loads[vt]
        cap = next(v['cap'] for v in VEHICLE_CONFIG if v['type'] == vt)
        logging.info(f"  车型{vt} (容量{cap}): {type_counts[vt]} 辆, 满载率 avg={np.mean(rates):.1f}%")

    # ---- 4. 输出 ----
    logging.info("-" * 40)
    logging.info("构建输出表...")

    MainScheme, DetailScheme = _build_delivery_plan(
        best_sol, Demands, SubTypeList, VeCap, VeUnitPrice,
        CarTypeStrList, date, DMAT_arr, org_labels
    )

    # ---- 5. 归并低满载率方案（基于重算后的真实装载率） ----
    logging.info("-" * 40)
    logging.info("归并低满载率方案...")
    MainScheme, DetailScheme = _merge_scheme_tables(
        MainScheme, DetailScheme, VeCap, CarTypeStrList, max_stops, DMAT_arr, org_labels
    )

    t_end = time.time()
    logging.info(f"输出完成: MainScheme={len(MainScheme)}行, DetailScheme={len(DetailScheme)}行")
    logging.info(f"V2 总耗时: {t_end - t_start:.1f}s")
    logging.info("=" * 60)
    return MainScheme, DetailScheme
