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


def _eval_route_fitness(route, DMAT_arr, VEHICLE_CONFIG, VeUnitPrice):
    """评估路径适应度（成本 + 满载率惩罚，越低越好）"""
    cost = _calc_route_cost(route, DMAT_arr, VeUnitPrice)
    if not route['deliveries']:
        return cost
    cap = next(v['cap'] for v in VEHICLE_CONFIG if v['type'] == route.get('vehicle_type', 1))
    load = sum(a for _, a in route['deliveries'])
    rate = load / cap if cap > 0 else 0

    penalty = 0.0
    if rate < 0.8:
        penalty = (10000 + cost * 100) * ((0.8 - rate) ** 2)

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


def _generate_initial_solution(unassigned, VEHICLE_CONFIG, max_stops, DMAT_arr, VeUnitPrice):
    """贪心构造初始解：需求大的优先，尽量合并到已有路径"""
    routes = []
    pending = sorted(unassigned.items(), key=lambda x: x[1], reverse=True)
    for cid, total_amt in pending:
        amt = total_amt
        while amt > 0:
            found = False
            for r in routes:
                cap = next(v['cap'] for v in VEHICLE_CONFIG if v['type'] == r.get('vehicle_type', 1))
                space = cap - sum(a for _, a in r['deliveries'])
                has_cid = any(c == cid for c, _ in r['deliveries'])
                if space > 0 and (has_cid or len(r['deliveries']) < max_stops):
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


def _greedy_insertion(routes, unassigned, VEHICLE_CONFIG, max_stops, DMAT_arr, VeUnitPrice):
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
                    fit = _eval_route_fitness(tmp, DMAT_arr, VEHICLE_CONFIG, VeUnitPrice)
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
                          CarTypeStrList, date, DMAT_arr, site_info):
    """将启发式解转换为 DelivPlan DataFrame，再生成主表和明细表"""
    node_to_org = site_info.reset_index(drop=True)
    # site_info 索引从 0 开始，网点编号从 1 开始
    org_lookup = {}
    for i, row in node_to_org.iterrows():
        org_lookup[i + 1] = row['ORG_NO']

    records = []
    for idx, route in enumerate(best_sol):
        deliveries = route['deliveries']
        node_ids = [cid for cid, _ in deliveries]
        amounts = [amt for _, amt in deliveries]
        v_type = route.get('vehicle_type', 1)
        v_idx = v_type - 1

        # 计算路径总距离：省中心→第一站→...→最后一站
        total_dist = DMAT_arr[0, node_ids[0]]
        for j in range(len(node_ids) - 1):
            total_dist += DMAT_arr[node_ids[j], node_ids[j + 1]]

        records.append({
            'PathInd': 1,
            'VeType': v_type,
            'Price': 0.0,  # 后补
            'PlanPath': node_ids,
            'DeNum': amounts,
            'PathDis': total_dist
        })

    DelivPlan = pd.DataFrame(records)

    # 计算价格
    for i, row in DelivPlan.iterrows():
        total_boxes = sum(row['DeNum'])
        v_idx = int(row['VeType']) - 1
        loaded_cost = VeUnitPrice[v_idx] * total_boxes
        empty_cost = VeUnitPrice[v_idx] * 0.5 * (VeCap[v_idx] - total_boxes)
        DelivPlan.at[i, 'Price'] = (loaded_cost + empty_cost) * row['PathDis']

    # PathNo 映射
    Path_no = []
    for planpath in DelivPlan['PlanPath']:
        p = [org_lookup.get(n, str(n)) for n in planpath]
        Path_no.append(p)
    DelivPlan['PathNo'] = Path_no

    DelivPlan = GenerateDelivPlan(DelivPlan, Demands, SubTypeList)
    MainScheme, DetailScheme = GenerateSchemeTables(
        DelivPlan, date, SubTypeList, VeCap, CarTypeStrList
    )
    return MainScheme, DetailScheme


# ==================== 主入口 ====================

def AdjustDaliyDeliveryV2(date: str, max_stops: int = 5, max_iter: int = 600):
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
        query_adam_del_site_conf,
        query_adam_dist_scheme_by_date_range,
        delete_adam_dist_scheme_det_by_scheme_id,
        delete_adam_dist_scheme_by_id
    )

    # 删除当天已有配送方案
    try:
        existing = query_adam_dist_scheme_by_date_range(date, date)
        if not existing.empty:
            for sid in existing['DIST_SCHEME_ID'].tolist():
                delete_adam_dist_scheme_det_by_scheme_id(sid)
                delete_adam_dist_scheme_by_id(sid)
            logging.info(f"已删除当天 {len(existing)} 条旧配送方案")
    except ValueError:
        logging.info(f"当天 ({date}) 无旧配送方案，跳过删除")

    # ---- 1. 载入数据 ----
    logging.info("=" * 60)
    logging.info(f"V2 启发式日配送开始: date={date}, max_stops={max_stops}, max_iter={max_iter}")
    t_start = time.time()

    Demands, LocationNum, SubTypeList, VeUnitPrice, VeTypeNum, VNums, VeCap, DMAT, _, CarTypeStrList = LoadDelivData(date)
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

    # 距离矩阵
    DMAT_arr = DMAT.values if isinstance(DMAT, pd.DataFrame) else DMAT
    DMAT_arr = DMAT_arr + DMAT_arr.T

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
    best_sol = _generate_initial_solution(unit_sum, VEHICLE_CONFIG, max_stops, DMAT_arr, VeUnitPrice)
    logging.info(f"初始构造完成: {len(best_sol)} 条路径, 平均每车 {len(unit_sum)/max(1,len(best_sol)):.1f} 站")

    best_sol = _reassign_vehicles(best_sol, VEHICLE_CONFIG)
    best_fitness = sum(_eval_route_fitness(r, DMAT_arr, VEHICLE_CONFIG, VeUnitPrice) for r in best_sol)
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
        new_sol = _greedy_insertion(destroyed, unassigned, VEHICLE_CONFIG, max_stops, DMAT_arr, VeUnitPrice)
        new_sol = _reassign_vehicles(new_sol, VEHICLE_CONFIG)
        new_fitness = sum(_eval_route_fitness(r, DMAT_arr, VEHICLE_CONFIG, VeUnitPrice) for r in new_sol)

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
    site_info = query_adam_del_site_conf()
    site_info = site_info[site_info['STAT_NAME'] != '营销服务中心']
    logging.info(f"站点信息: {len(site_info)} 个配送站点")

    MainScheme, DetailScheme = _build_delivery_plan(
        best_sol, Demands, SubTypeList, VeCap, VeUnitPrice,
        CarTypeStrList, date, DMAT_arr, site_info
    )

    t_end = time.time()
    logging.info(f"输出完成: MainScheme={len(MainScheme)}行, DetailScheme={len(DetailScheme)}行")
    logging.info(f"V2 总耗时: {t_end - t_start:.1f}s")
    logging.info("=" * 60)
    return MainScheme, DetailScheme
