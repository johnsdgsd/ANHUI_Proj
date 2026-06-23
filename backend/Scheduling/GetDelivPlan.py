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
        cls_val = '01'
        if 'DEV_CLS' in SubTypeList.columns:
            cls_val = str(SubTypeList.loc[i, 'DEV_CLS']).replace('.0', '').strip().zfill(2)
        vol_mult = 2.5 if cls_val == '02' else 1.0
        # 互感器向上取整，消灭碎片
        DemandsBoxs[:, i] = np.ceil(np.ceil(Demands_arr[:, i] / UnitPerBoxI) * vol_mult)

    DemandsBoxs = np.sum(DemandsBoxs, axis=1)
    unit_sum = {i + 1: float(DemandsBoxs[i]) for i in range(LocationNum) if DemandsBoxs[i] > 0}

    if not unit_sum:
        return []

    '''2. 全局配置'''
    VEHICLE_CONFIG = sorted([{'type': i + 1, 'cap': VeCap[i], 'daily_max': VNums[i]} for i in range(VeTypeNum)],
                            key=lambda x: x['cap'])
    MAX_CAP = VEHICLE_CONFIG[-1]['cap']

    DMAT_arr = DMAT.values if isinstance(DMAT, pd.DataFrame) else DMAT
    DMAT_arr = DMAT_arr + DMAT_arr.T

    def get_dist(id1, id2):
        return DMAT_arr[id1, id2]

    def calc_route_cost(route):
        total_cost = 0.0
        prev_node = 0
        v_idx = int(route.get('vehicle_type', 1)) - 1
        unit_price = float(VeUnitPrice[v_idx]) if len(VeUnitPrice) > v_idx else 0.0695
        for cid, amt in route['deliveries']:
            total_cost += amt * get_dist(prev_node, cid) * unit_price
            prev_node = cid
        return total_cost

    def calc_route_distance(deliveries):
        if not deliveries: return 0.0
        dist = 0.0
        prev_node = 0
        for cid, _ in deliveries:
            dist += get_dist(prev_node, cid)
            prev_node = cid
        dist += get_dist(prev_node, 0)  # 回到省库
        return dist

    def eval_route_fitness(route):
        real_cost = calc_route_cost(route)
        if not route['deliveries']: return real_cost

        # 动态评估这趟车配什么车型最合理，计算真实装载率
        load = sum(a for _, a in route['deliveries'])
        best_cap = MAX_CAP
        for cfg in VEHICLE_CONFIG:
            if cfg['cap'] >= load:
                best_cap = cfg['cap']
                break

        load_rate = load / best_cap if best_cap > 0 else 0

        # 【惩罚 1】：物理绕路成本。由于拆除了硬隔离红线，这里全靠数学打压过度绕路
        dist_penalty = calc_route_distance(route['deliveries']) * 5.0

        # 【惩罚 2】：70% 柔性装载率。达到 70% 就没有惩罚了
        penalty = 0
        if load_rate < 0.70:
            penalty = 12000 * ((0.70 - load_rate) ** 2)

        return real_cost + dist_penalty + penalty

    def optimize_route_sequence(route):
        deliveries = route['deliveries']
        if len(deliveries) <= 1: return route
        best_dist = float('inf')
        best_seq = None
        for seq in itertools.permutations(deliveries):
            dist = calc_route_distance(list(seq))
            if dist < best_dist:
                best_dist = dist
                best_seq = list(seq)
        route['deliveries'] = best_seq
        return route

    def reassign_vehicles(routes):
        monthly_quota = {cfg['type']: cfg['daily_max'] * DelivDay for cfg in VEHICLE_CONFIG}

        routes_sorted = sorted(routes, key=lambda r: sum(a for _, a in r['deliveries']), reverse=True)
        for r in routes_sorted:
            load = sum(a for _, a in r['deliveries'])
            assigned = False

            for cfg in VEHICLE_CONFIG:
                if cfg['cap'] >= load and monthly_quota[cfg['type']] > 0:
                    r['vehicle_type'] = cfg['type']
                    monthly_quota[cfg['type']] -= 1
                    assigned = True
                    break

            if not assigned:
                for cfg in VEHICLE_CONFIG:
                    if cfg['cap'] >= load:
                        r['vehicle_type'] = cfg['type']
                        monthly_quota[cfg['type']] -= 1
                        assigned = True
                        break

            if not assigned:
                r['vehicle_type'] = VEHICLE_CONFIG[-1]['type']
                monthly_quota[VEHICLE_CONFIG[-1]['type']] -= 1

        return routes_sorted

    def generate_initial_solution(unassigned):
        routes = []
        pending = sorted(unassigned.items(), key=lambda x: x[1], reverse=True)
        for cid, total_amt in pending:
            amt = total_amt
            while amt > 0:
                assigned = False
                for r in routes:
                    space = MAX_CAP - sum(a for _, a in r['deliveries'])
                    has_cid = any(c == cid for c, _ in r['deliveries'])

                    # 【修改点1】：仅保留：网点合并不超3 的业务红线
                    if space > 0 and (has_cid or len(r['deliveries']) < 3):
                        load = min(amt, space)
                        temp_route = copy.deepcopy(r)
                        if has_cid:
                            for i, (c, a) in enumerate(temp_route['deliveries']):
                                if c == cid:
                                    temp_route['deliveries'][i] = (c, a + load)
                                    break
                        else:
                            temp_route['deliveries'].append((cid, load))

                        temp_route = optimize_route_sequence(temp_route)

                        r['deliveries'] = temp_route['deliveries']
                        amt -= load
                        assigned = True
                        break
                if not assigned:
                    cfg = VEHICLE_CONFIG[-1]
                    for c in VEHICLE_CONFIG:
                        if c['cap'] >= amt: cfg = c; break
                    load = min(amt, cfg['cap'])
                    routes.append({'vehicle_type': cfg['type'], 'deliveries': [(cid, load)]})
                    amt -= load
        return routes

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
            if not route['deliveries']: sol_copy.pop(ri)
        return sol_copy, unassigned

    def greedy_insertion(routes, unassigned):
        pending = sorted(unassigned.items(), key=lambda x: x[1], reverse=True)
        for cid, amt in pending:
            while amt > 0:
                best_fit = float('inf')
                best_action = None
                for ri, route in enumerate(routes):
                    has_cid = any(c == cid for c, _ in route['deliveries'])
                    # 【修改点2】：单车3网点红线，达到或超过3个则跳过不插入新网点
                    if not has_cid and len(route['deliveries']) >= 3: continue

                    space = MAX_CAP - sum(a for _, a in route['deliveries'])
                    if space > 0:
                        ins_amt = min(amt, space)
                        temp = copy.deepcopy(route)
                        if has_cid:
                            for i, (c, a) in enumerate(temp['deliveries']):
                                if c == cid:
                                    temp['deliveries'][i] = (c, a + ins_amt)
                                    break
                        else:
                            temp['deliveries'].append((cid, ins_amt))

                        temp = optimize_route_sequence(temp)

                        fit = eval_route_fitness(temp)
                        if fit < best_fit:
                            best_fit = fit
                            best_action = ('insert', ri, ins_amt, temp['deliveries'])
                if best_action is None:
                    cfg = VEHICLE_CONFIG[-1]
                    for c in VEHICLE_CONFIG:
                        if c['cap'] >= amt: cfg = c; break
                    ins_amt = min(amt, cfg['cap'])
                    temp = {'vehicle_type': cfg['type'], 'deliveries': [(cid, ins_amt)]}
                    best_action = ('new', cfg['type'], ins_amt, temp['deliveries'])
                if best_action[0] == 'insert':
                    routes[best_action[1]]['deliveries'] = best_action[3]
                    amt -= best_action[2]
                else:
                    routes.append({'vehicle_type': best_action[1], 'deliveries': best_action[3]})
                    amt -= best_action[2]
        return [r for r in routes if r['deliveries']]

    '''3. 启发式算法：空间最优解'''
    max_iter = 600
    best_sol = generate_initial_solution(unit_sum)
    best_sol = reassign_vehicles(best_sol)
    best_fitness = sum(eval_route_fitness(r) for r in best_sol)

    for i in range(max_iter):
        remove_cnt = max(2, int(len(unit_sum) * 0.3))
        destroyed, unassigned = random_removal(best_sol, num_remove=remove_cnt)
        new_sol = greedy_insertion(destroyed, unassigned)

        # 随时清理空车
        new_sol = [r for r in new_sol if r['deliveries']]

        new_sol = reassign_vehicles(new_sol)
        new_fitness = sum(eval_route_fitness(r) for r in new_sol)

        if new_fitness < best_fitness or math.exp((best_fitness - new_fitness) / 100) > random.random():
            best_sol, best_fitness = new_sol, new_fitness

    # 最终强制收紧约束
    best_sol = reassign_vehicles(best_sol)
    best_sol = [r for r in best_sol if r['deliveries']]

    '''4. 整数线性规划 Stage 2: 日期精确排程 (绝对硬约束)'''
    num_routes = len(best_sol)
    if num_routes == 0: return []
    prob2 = pulp.LpProblem("Minimize_Peak_Daily_Volume", pulp.LpMinimize)
    x = pulp.LpVariable.dicts("x", [(r, d) for r in range(num_routes) for d in range(DelivDay)], cat='Binary')

    Z = pulp.LpVariable("Peak_Volume", lowBound=0, cat='Continuous')

    prob2 += Z

    for r in range(num_routes):
        prob2 += pulp.lpSum(x[r, d] for d in range(DelivDay)) == 1

    for d in range(DelivDay):
        for v in range(VeTypeNum):
            v_type = v + 1
            type_routes = [r for r in range(num_routes) if int(best_sol[r].get('vehicle_type', 1)) == v_type]
            if type_routes:
                prob2 += pulp.lpSum(x[r, d] for r in type_routes) <= VNums[v]

    for d in range(DelivDay):
        prob2 += pulp.lpSum(x[r, d] * sum(amt for _, amt in best_sol[r]['deliveries']) for r in range(num_routes)) <= Z

    solver2 = pulp.PULP_CBC_CMD(msg=False, options=['sec=60', 'ratioGap=0.01'])
    status = prob2.solve(solver2)

    if pulp.LpStatus[status] != 'Optimal':
        raise Exception(f"【运力严重不足】在不超每日配额的前提下无法排开所有路线！状态: {pulp.LpStatus[status]}")

    for r in range(num_routes):
        best_sol[r]['schedule_day_idx'] = 0
        for d in range(DelivDay):
            if x[r, d].varValue and x[r, d].varValue >= 0.5:
                best_sol[r]['schedule_day_idx'] = d
                break

    return best_sol