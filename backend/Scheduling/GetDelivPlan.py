import numpy as np
import pandas as pd
import random
import copy
import math
import itertools
from collections import defaultdict
import pulp
import logging
import sys


def GetDelivPlan(Demands, LocationNum, TypeList, SubTypeList, DelivDay, VeUnitPrice, VeTypeNum, VNums, VeCap, DMAT):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s", stream=sys.stdout)

    '''1. 提取基础参数与箱数转换'''
    SubTypeNum = len(SubTypeList)
    DemandsBoxs = np.zeros((LocationNum, SubTypeNum))
    Demands_arr = Demands.values if isinstance(Demands, pd.DataFrame) else Demands

    for i in range(SubTypeNum):
        UnitPerBoxI = SubTypeList.loc[i, 'PACK_BOX_NUM'] if 'PACK_BOX_NUM' in SubTypeList.columns else 5
        DemandsBoxs[:, i] = np.ceil(Demands_arr[:, i] / UnitPerBoxI)

    DemandsBoxs = np.sum(DemandsBoxs, axis=1)

    unit_sum = {i + 1: int(DemandsBoxs[i]) for i in range(LocationNum) if DemandsBoxs[i] > 0}

    if not unit_sum:
        return pd.DataFrame(), pd.DataFrame(), np.zeros((0, DelivDay), dtype=int), []

    '''2. 全局配置'''
    COST_PER_BOX_KM = float(VeUnitPrice[0]) if len(VeUnitPrice) > 0 and float(VeUnitPrice[0]) > 0 else 0.0695

    VEHICLE_CONFIG = [
        {'type': i + 1, 'cap': VeCap[i], 'daily_max': VNums[i]} for i in range(VeTypeNum)
    ]

    DMAT_arr = DMAT.values if isinstance(DMAT, pd.DataFrame) else DMAT
    DMAT_arr = DMAT_arr + DMAT_arr.T

    def get_dist(id1, id2):
        return DMAT_arr[id1, id2]

    # 【按需修正】：严格执行你定义的成本计算逻辑
    def calc_route_cost(route):
        """
        逻辑：拉到某站点的物料箱数 * 上一站点到该站点距离 * 单价
        """
        total_cost = 0.0
        prev_node = 0  # 0 为省级总库 (CENTER)
        deliveries = route['deliveries']

        for cid, amt in deliveries:
            dist_segment = get_dist(prev_node, cid)
            total_cost += amt * dist_segment * COST_PER_BOX_KM
            prev_node = cid  # 下一个站点的起点就是当前站点

        return total_cost

    def eval_route_fitness(route):
        """优化目标：配送成本 + 车辆利用率均衡"""
        real_cost = calc_route_cost(route)
        if not route['deliveries']: return real_cost

        cap = next(v['cap'] for v in VEHICLE_CONFIG if v['type'] == route['vehicle_type'])
        load = sum(a for _, a in route['deliveries'])
        load_rate = load / cap if cap > 0 else 0

        # 平滑惩罚：鼓励装载率
        base_dispatch = 50.0
        fitness = (base_dispatch + real_cost) / (load_rate + 0.2)
        return fitness

    def optimize_route_sequence(route):
        """基于新计费逻辑的路径重排"""
        deliveries = route['deliveries']
        if len(deliveries) <= 1: return route

        best_cost = float('inf')
        best_seq = None

        for seq in itertools.permutations(deliveries):
            temp_route = {'deliveries': list(seq)}
            cost = calc_route_cost(temp_route)
            if cost < best_cost:
                best_cost = cost
                best_seq = list(seq)

        route['deliveries'] = best_seq
        return route

    def calc_total_cost(routes):
        return sum(calc_route_cost(r) for r in routes)

    def calc_total_fitness(routes):
        return sum(eval_route_fitness(r) for r in routes)

    def get_best_available_vehicle(used_vehicles_count, target_load):
        sorted_configs = sorted(VEHICLE_CONFIG, key=lambda x: x['cap'])
        for cfg in sorted_configs:
            if cfg['cap'] >= target_load and used_vehicles_count[cfg['type']] < cfg['daily_max'] * DelivDay:
                return cfg['type'], cfg['cap']
        for cfg in sorted_configs[::-1]:
            if used_vehicles_count[cfg['type']] < cfg['daily_max'] * DelivDay:
                return cfg['type'], cfg['cap']
        raise Exception("全局总运力不足！")

    def reassign_vehicles(routes):
        routes_sorted = sorted(routes, key=lambda r: sum(a for _, a in r['deliveries']), reverse=True)
        v_counts = {cfg['type']: 0 for cfg in VEHICLE_CONFIG}
        sorted_configs = sorted(VEHICLE_CONFIG, key=lambda x: x['cap'])

        for r in routes_sorted:
            load = sum(a for _, a in r['deliveries'])
            assigned_type = None
            for cfg in sorted_configs:
                if cfg['cap'] >= load and v_counts[cfg['type']] < cfg['daily_max'] * DelivDay:
                    assigned_type = cfg['type']
                    break
            if not assigned_type:
                for cfg in sorted_configs[::-1]:
                    if v_counts[cfg['type']] < cfg['daily_max'] * DelivDay:
                        assigned_type = cfg['type']
                        break
            r['vehicle_type'] = assigned_type
            v_counts[assigned_type] += 1
        return routes_sorted

    def generate_initial_solution(unassigned):
        routes = []
        pending = sorted(unassigned.items(), key=lambda x: x[1], reverse=True)
        used_v_count = {cfg['type']: 0 for cfg in VEHICLE_CONFIG}

        for cid, total_amt in pending:
            amt = total_amt
            while amt > 0:
                assigned = False
                for r in routes:
                    cap = next(v['cap'] for v in VEHICLE_CONFIG if v['type'] == r['vehicle_type'])
                    curr_load = sum(a for _, a in r['deliveries'])
                    space = cap - curr_load

                    is_existing = any(v[0] == cid for v in r['deliveries'])
                    current_node_count = len(set([c for c, _ in r['deliveries']]))

                    # 严格限定最多 3 个网点
                    if space > 0 and (current_node_count < 3 or is_existing):
                        load = min(amt, space)
                        existing_idx = next((i for i, v in enumerate(r['deliveries']) if v[0] == cid), -1)
                        if existing_idx >= 0:
                            old_amt = r['deliveries'][existing_idx][1]
                            r['deliveries'][existing_idx] = (cid, old_amt + load)
                        else:
                            r['deliveries'].append((cid, load))

                        r = optimize_route_sequence(r)
                        amt -= load
                        assigned = True
                        break

                if not assigned:
                    v_type, cap = get_best_available_vehicle(used_v_count, amt)
                    load = min(amt, cap)
                    routes.append({'vehicle_type': v_type, 'deliveries': [(cid, load)]})
                    used_v_count[v_type] += 1
                    amt -= load
        return routes, used_v_count

    def random_removal(solution, num_remove=3):
        sol_copy = copy.deepcopy(solution)
        unassigned = defaultdict(float)
        for _ in range(num_remove):
            if not sol_copy: break
            ri = random.randint(0, len(sol_copy) - 1)
            route = sol_copy[ri]
            if route['deliveries']:
                pi = random.randint(0, len(route['deliveries']) - 1)
                cid, amt = route['deliveries'].pop(pi)
                unassigned[cid] += amt
            if not route['deliveries']:
                sol_copy.pop(ri)
        return sol_copy, unassigned

    def greedy_insertion(routes, unassigned, current_v_count):
        pending = sorted(unassigned.items(), key=lambda x: x[1], reverse=True)
        for cid, amt in pending:
            while amt > 0:
                best_fitness_diff = float('inf')
                best_action = None

                for ri, route in enumerate(routes):
                    is_existing = any(v[0] == cid for v in route['deliveries'])
                    current_node_count = len(set([c for c, _ in route['deliveries']]))

                    # 严格限定最多 3 个网点
                    if current_node_count >= 3 and not is_existing:
                        continue

                    cap = next(v['cap'] for v in VEHICLE_CONFIG if v['type'] == route['vehicle_type'])
                    curr_load = sum(a for _, a in route['deliveries'])
                    space = cap - curr_load

                    if space > 0:
                        ins_amt = min(amt, space)
                        base_fit = eval_route_fitness(route)
                        temp_route = copy.deepcopy(route)

                        existing_idx = next((i for i, v in enumerate(temp_route['deliveries']) if v[0] == cid), -1)
                        if existing_idx >= 0:
                            old_amt = temp_route['deliveries'][existing_idx][1]
                            temp_route['deliveries'][existing_idx] = (cid, old_amt + ins_amt)
                        else:
                            temp_route['deliveries'].append((cid, ins_amt))

                        temp_route = optimize_route_sequence(temp_route)
                        new_fit = eval_route_fitness(temp_route)

                        fit_diff = new_fit - base_fit
                        if fit_diff < best_fitness_diff:
                            best_fitness_diff = fit_diff
                            best_action = ('insert', ri, ins_amt, temp_route['deliveries'])

                if best_action is None:
                    for cfg in VEHICLE_CONFIG:
                        vtype, cap = cfg['type'], cfg['cap']
                        if current_v_count[vtype] >= cfg['daily_max'] * DelivDay:
                            continue

                        ins_amt = min(amt, cap)
                        temp_route = {'vehicle_type': vtype, 'deliveries': [(cid, ins_amt)]}
                        new_fit = eval_route_fitness(temp_route)
                        base_bias = 1.0 if vtype == 1 else (1.1 if vtype == 2 else 1.2)

                        if new_fit * base_bias < best_fitness_diff:
                            best_fitness_diff = new_fit * base_bias
                            best_action = ('new', vtype, ins_amt, temp_route['deliveries'])

                if best_action[0] == 'insert':
                    ri, ins_amt, new_seq = best_action[1], best_action[2], best_action[3]
                    routes[ri]['deliveries'] = new_seq
                    amt -= ins_amt
                else:
                    vtype, ins_amt, new_seq = best_action[1], best_action[2], best_action[3]
                    routes.append({'vehicle_type': vtype, 'deliveries': new_seq})
                    current_v_count[vtype] += 1
                    amt -= ins_amt

        return [r for r in routes if r['deliveries']], current_v_count

    '''3. 执行启发式空间规划'''
    max_iter = 250
    best_sol, best_v_count = generate_initial_solution(unit_sum)
    best_sol = reassign_vehicles(best_sol)
    best_fitness = calc_total_fitness(best_sol)

    for i in range(max_iter):
        remove_cnt = max(2, int(len(unit_sum) * random.uniform(0.1, 0.4)))
        destroyed, unassigned = random_removal(best_sol, num_remove=remove_cnt)
        curr_v_count = {cfg['type']: 0 for cfg in VEHICLE_CONFIG}
        for r in destroyed: curr_v_count[r['vehicle_type']] += 1
        new_sol, new_v_count = greedy_insertion(destroyed, unassigned, curr_v_count)
        new_sol = reassign_vehicles(new_sol)
        new_fitness = calc_total_fitness(new_sol)

        if new_fitness < best_fitness or math.exp((best_fitness - new_fitness) / 100) > random.random():
            best_sol = new_sol
            best_fitness = new_fitness

    best_sol = reassign_vehicles(best_sol)

    '''4. 组装接口数据'''
    PlanPath, DeNum, VeType, PathInd, Price = [], [], [], [], []
    unique_paths = []

    for route in best_sol:
        v_type = route['vehicle_type']
        path_nodes = tuple(cid for cid, amt in route['deliveries'])
        boxes = [amt for cid, amt in route['deliveries']]

        if path_nodes not in unique_paths: unique_paths.append(path_nodes)
        path_idx = unique_paths.index(path_nodes) + 1
        PlanPath.append(list(path_nodes));
        DeNum.append(list(boxes));
        VeType.append(v_type);
        PathInd.append(path_idx);
        Price.append(calc_route_cost(route))

    DelivPlan = pd.DataFrame(
        {'PathInd': PathInd, 'VeType': VeType, 'Price': Price, 'PlanPath': PlanPath, 'DeNum': DeNum})
    PathInfo = pd.DataFrame({'Ind': range(1, len(unique_paths) + 1), 'Path': [list(p) for p in unique_paths],
                             'PathDis': [sum(get_dist(p[i], p[i + 1]) for i in range(len(p) - 1)) for p in
                                         unique_paths]})

    '''5. 运筹学 Stage 2: 日历排程'''
    Ls = np.unique(PathInd)
    NL = len(Ls)
    Ds = np.zeros((NL, VeTypeNum))
    RouteLoads = np.zeros(NL)

    for i in range(NL):
        sample_trip = DelivPlan[DelivPlan['PathInd'] == Ls[i]].iloc[0]
        RouteLoads[i] = sum(sample_trip['DeNum'])
        for j in range(VeTypeNum):
            Ds[i, j] = np.sum((np.array(PathInd) == Ls[i]) & (np.array(VeType) == j + 1))

    prob2 = pulp.LpProblem("Minimize_Peak_Daily_Volume", pulp.LpMinimize)
    xd = pulp.LpVariable.dicts("xd", [(l, v, d) for l in range(NL) for v in range(VeTypeNum) for d in range(DelivDay)],
                               lowBound=0, cat='Integer')
    Z = pulp.LpVariable("Peak_Volume", lowBound=0, cat='Continuous')

    prob2 += Z
    for l in range(NL):
        for v in range(VeTypeNum):
            if Ds[l, v] > 0: prob2 += pulp.lpSum(xd[l, v, d] for d in range(DelivDay)) == Ds[l, v]
    for d in range(DelivDay):
        for v in range(VeTypeNum):
            prob2 += pulp.lpSum(xd[l, v, d] for l in range(NL)) <= VNums[v]
    for d in range(DelivDay):
        prob2 += pulp.lpSum(xd[l, v, d] * RouteLoads[l] for l in range(NL) for v in range(VeTypeNum)) <= Z

    solver2 = pulp.PULP_CBC_CMD(msg=False, options=['sec=30', 'ratioGap=0.05'])
    prob2.solve(solver2)

    DelivCalendar = np.zeros((NL, DelivDay), dtype=int)
    for l in range(NL):
        for d in range(DelivDay):
            for v in range(VeTypeNum):
                if xd[l, v, d].varValue and xd[l, v, d].varValue >= 0.5: DelivCalendar[l, d] = v + 1

    return PathInfo, DelivPlan, DelivCalendar, Ls