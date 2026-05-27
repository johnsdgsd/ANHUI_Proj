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

    '''1. 提取基础参数与箱数转换 (引入 2.5 倍体积折算)'''
    SubTypeNum = len(SubTypeList)
    DemandsBoxs = np.zeros((LocationNum, SubTypeNum))
    Demands_arr = Demands.values if isinstance(Demands, pd.DataFrame) else Demands

    for i in range(SubTypeNum):
        UnitPerBoxI = SubTypeList.loc[i, 'PACK_BOX_NUM'] if 'PACK_BOX_NUM' in SubTypeList.columns else 5
        cls_val = '01'
        if 'DEV_CLS' in SubTypeList.columns:
            cls_val = str(SubTypeList.loc[i, 'DEV_CLS']).replace('.0', '').strip().zfill(2)
        vol_mult = 2.5 if cls_val == '02' else 1.0
        DemandsBoxs[:, i] = np.ceil(Demands_arr[:, i] / UnitPerBoxI) * vol_mult

    DemandsBoxs = np.sum(DemandsBoxs, axis=1)
    unit_sum = {i + 1: float(DemandsBoxs[i]) for i in range(LocationNum) if DemandsBoxs[i] > 0}

    if not unit_sum:
        return []

    '''2. 全局配置'''
    # 按容量从小到大排序
    VEHICLE_CONFIG = sorted([{'type': i + 1, 'cap': VeCap[i], 'daily_max': VNums[i]} for i in range(VeTypeNum)],
                            key=lambda x: x['cap'])
    DMAT_arr = DMAT.values if isinstance(DMAT, pd.DataFrame) else DMAT
    DMAT_arr = DMAT_arr + DMAT_arr.T

    def get_dist(id1, id2):
        return DMAT_arr[id1, id2]

    def calc_route_cost(route):
        total_cost = 0.0;
        prev_node = 0
        v_idx = int(route.get('vehicle_type', 1)) - 1
        unit_price = float(VeUnitPrice[v_idx]) if len(VeUnitPrice) > v_idx else 0.0695
        for cid, amt in route['deliveries']:
            total_cost += amt * get_dist(prev_node, cid) * unit_price
            prev_node = cid
        return total_cost

    def eval_route_fitness(route):
        real_cost = calc_route_cost(route)
        if not route['deliveries']: return real_cost
        cap = next(v['cap'] for v in VEHICLE_CONFIG if v['type'] == route.get('vehicle_type', 1))
        load = sum(a for _, a in route['deliveries'])
        load_rate = load / cap if cap > 0 else 0

        # 装载率目标：低于80%施加严厉惩罚
        penalty = 0
        if load_rate < 0.8:
            penalty = (10000 + real_cost * 100) * ((0.8 - load_rate) ** 2)

        return (500.0 + real_cost + penalty) / (load_rate + 0.1)

    def optimize_route_sequence(route):
        deliveries = route['deliveries']
        if len(deliveries) <= 1: return route
        best_cost = float('inf');
        best_seq = None
        for seq in itertools.permutations(deliveries):
            temp_route = {'vehicle_type': route.get('vehicle_type', 1), 'deliveries': list(seq)}
            cost = calc_route_cost(temp_route)
            if cost < best_cost: best_cost = cost; best_seq = list(seq)
        route['deliveries'] = best_seq
        return route

    def reassign_vehicles(routes):
        routes_sorted = sorted(routes, key=lambda r: sum(a for _, a in r['deliveries']), reverse=True)
        for r in routes_sorted:
            load = sum(a for _, a in r['deliveries'])
            assigned = False
            # 【核心修复】：永远只分配能装下 load 的最小车型，绝对不允许强行分配小车导致丢单！
            for cfg in VEHICLE_CONFIG:
                if cfg['cap'] >= load:
                    r['vehicle_type'] = cfg['type']
                    assigned = True
                    break
            if not assigned:
                # 如果超出了所有车的最大容量，分配给最大的车
                r['vehicle_type'] = VEHICLE_CONFIG[-1]['type']
        return routes_sorted

    def generate_initial_solution(unassigned):
        routes = [];
        pending = sorted(unassigned.items(), key=lambda x: x[1], reverse=True)
        for cid, total_amt in pending:
            amt = total_amt
            while amt > 0:
                assigned = False
                for r in routes:
                    cap = next(v['cap'] for v in VEHICLE_CONFIG if v['type'] == r.get('vehicle_type', 1))
                    space = cap - sum(a for _, a in r['deliveries'])
                    # 路径配送网点数量不超过 5 个
                    if space > 0 and len(r['deliveries']) < 5:
                        load = min(amt, space);
                        r['deliveries'].append((cid, load));
                        r = optimize_route_sequence(r);
                        amt -= load;
                        assigned = True;
                        break
                if not assigned:
                    cfg = VEHICLE_CONFIG[-1]
                    for c in VEHICLE_CONFIG:
                        if c['cap'] >= amt: cfg = c; break
                    load = min(amt, cfg['cap']);
                    routes.append({'vehicle_type': cfg['type'], 'deliveries': [(cid, load)]})
                    amt -= load
        return routes

    def random_removal(solution, num_remove=3):
        sol_copy = copy.deepcopy(solution);
        unassigned = defaultdict(float)
        for _ in range(num_remove):
            if not sol_copy: break
            ri = random.randint(0, len(sol_copy) - 1);
            route = sol_copy[ri]
            if route['deliveries']:
                pi = random.randint(0, len(route['deliveries']) - 1);
                cid, amt = route['deliveries'].pop(pi);
                unassigned[cid] += amt
            if not route['deliveries']: sol_copy.pop(ri)
        return sol_copy, unassigned

    def greedy_insertion(routes, unassigned):
        pending = sorted(unassigned.items(), key=lambda x: x[1], reverse=True)
        for cid, amt in pending:
            while amt > 0:
                best_fit = float('inf');
                best_action = None
                for ri, route in enumerate(routes):
                    if len(route['deliveries']) >= 5: continue
                    cap = next(v['cap'] for v in VEHICLE_CONFIG if v['type'] == route.get('vehicle_type', 1))
                    # 【核心修复】：将原本拼写错误的 ['deliveries'] 修正为 route['deliveries']
                    space = cap - sum(a for _, a in route['deliveries'])
                    if space > 0:
                        ins_amt = min(amt, space);
                        temp = copy.deepcopy(route);
                        temp['deliveries'].append((cid, ins_amt))
                        temp = optimize_route_sequence(temp);
                        fit = eval_route_fitness(temp)
                        if fit < best_fit: best_fit = fit; best_action = ('insert', ri, ins_amt, temp['deliveries'])
                if best_action is None:
                    cfg = VEHICLE_CONFIG[-1]
                    for c in VEHICLE_CONFIG:
                        if c['cap'] >= amt: cfg = c; break
                    ins_amt = min(amt, cfg['cap']);
                    temp = {'vehicle_type': cfg['type'], 'deliveries': [(cid, ins_amt)]}
                    best_action = ('new', cfg['type'], ins_amt, temp['deliveries'])
                if best_action[0] == 'insert':
                    routes[best_action[1]]['deliveries'] = best_action[3];
                    amt -= best_action[2]
                else:
                    routes.append({'vehicle_type': best_action[1], 'deliveries': best_action[3]});
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
        new_sol = reassign_vehicles(new_sol)
        new_fitness = sum(eval_route_fitness(r) for r in new_sol)

        if new_fitness < best_fitness or math.exp((best_fitness - new_fitness) / 100) > random.random():
            best_sol, best_fitness = new_sol, new_fitness

    best_sol = reassign_vehicles(best_sol)

    '''4. 整数线性规划 Stage 2: 日期精确排程 (100%硬约束，辅以弹性防死锁)'''
    num_routes = len(best_sol)
    prob2 = pulp.LpProblem("Minimize_Peak_Daily_Volume", pulp.LpMinimize)
    x = pulp.LpVariable.dicts("x", [(r, d) for r in range(num_routes) for d in range(DelivDay)], cat='Binary')

    # 引入弹性变量。首选遵守 VNums 限额，万不得已时允许破额(施加巨额罚金)，绝对不抛弃需求
    slack_vars = pulp.LpVariable.dicts("slack", [(v, d) for v in range(VeTypeNum) for d in range(DelivDay)], lowBound=0,
                                       cat='Integer')
    Z = pulp.LpVariable("Peak_Volume", lowBound=0, cat='Continuous')

    prob2 += Z + 100000 * pulp.lpSum(slack_vars[v, d] for v in range(VeTypeNum) for d in range(DelivDay))

    # 铁律：每趟车必须且只能发车一天
    for r in range(num_routes):
        prob2 += pulp.lpSum(x[r, d] for d in range(DelivDay)) == 1

    # 每日车型限额
    for d in range(DelivDay):
        for v in range(VeTypeNum):
            v_type = v + 1
            type_routes = [r for r in range(num_routes) if int(best_sol[r].get('vehicle_type', 1)) == v_type]
            if type_routes:
                prob2 += pulp.lpSum(x[r, d] for r in type_routes) <= VNums[v] + slack_vars[v, d]

    # 削峰填谷
    for d in range(DelivDay):
        prob2 += pulp.lpSum(x[r, d] * sum(amt for _, amt in best_sol[r]['deliveries']) for r in range(num_routes)) <= Z

    solver2 = pulp.PULP_CBC_CMD(msg=False, options=['sec=60', 'ratioGap=0.05'])
    prob2.solve(solver2)

    # 挂载排期
    for r in range(num_routes):
        best_sol[r]['schedule_day_idx'] = 0
        for d in range(DelivDay):
            if x[r, d].varValue and x[r, d].varValue >= 0.5:
                best_sol[r]['schedule_day_idx'] = d
                break

    return best_sol