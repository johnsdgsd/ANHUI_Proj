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
        DemandsBoxs[:, i] = np.ceil(np.ceil(Demands_arr[:, i] / UnitPerBoxI) * vol_mult)

    DemandsBoxs = np.sum(DemandsBoxs, axis=1)
    unit_sum = {i + 1: float(DemandsBoxs[i]) for i in range(LocationNum) if DemandsBoxs[i] > 0}

    if not unit_sum:
        return []

    '''2. 全局配置'''
    VEHICLE_CONFIG = sorted([{'type': i + 1, 'cap': VeCap[i], 'daily_max': VNums[i]} for i in range(VeTypeNum)],
                            key=lambda x: x['cap'])
    MAX_CAP = VEHICLE_CONFIG[-1]['cap']

    # 【新增全局红线】：单条路线的闭环最大行驶里程
    MAX_ROUTE_DIST = 750

    DMAT_arr = DMAT.values if isinstance(DMAT, pd.DataFrame) else DMAT
    # 使用 np.maximum 完美兼容：
    # 如果是上三角矩阵，它能正确补齐；如果是完整对称矩阵，它保持原值绝对不会翻倍！
    DMAT_arr = np.maximum(DMAT_arr, DMAT_arr.T)

    def get_dist(id1, id2):
        return DMAT_arr[id1, id2]

    # ====================================================================
    # 【核心约束 1】：8方向切分（利用余弦定理计算夹角，小于45度）
    # ====================================================================
    def check_angle_constraint(cid1, cid2):
        if cid1 == cid2: return True
        d01 = get_dist(0, cid1)
        d02 = get_dist(0, cid2)
        d12 = get_dist(cid1, cid2)

        if d01 <= 0.001 or d02 <= 0.001: return True

        cos_theta = (d01 ** 2 + d02 ** 2 - d12 ** 2) / (2 * d01 * d02)
        cos_theta = max(-1.0, min(1.0, cos_theta))
        return cos_theta >= 0.707

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
        dist += get_dist(prev_node, 0)
        return dist

    def eval_route_fitness(route):
        real_cost = calc_route_cost(route)
        if not route['deliveries']: return real_cost

        load = sum(a for _, a in route['deliveries'])
        best_cap = MAX_CAP
        for cfg in VEHICLE_CONFIG:
            if cfg['cap'] >= load:
                best_cap = cfg['cap']
                break

        load_rate = load / best_cap if best_cap > 0 else 0
        dist_penalty = calc_route_distance(route['deliveries']) * 500.0

        penalty = 0
        if load_rate < 0.70:
            penalty = 12000 * ((0.70 - load_rate) ** 2)

        return real_cost + dist_penalty + penalty

    # ====================================================================
    # 【核心约束 2】：终极防绕路（强制由近及远顺路卸货）
    # ====================================================================
    def optimize_route_sequence(route):
        deliveries = route['deliveries']
        if len(deliveries) <= 1: return route
        route['deliveries'] = sorted(deliveries, key=lambda x: get_dist(0, x[0]))
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

        # 【借鉴main copy: 极角扫描排序】
        # 利用距离矩阵计算各网点相对于基准方向的cos角, 同方向网点聚在一起
        nodes = list(unassigned.keys())
        if len(nodes) >= 2:
            # 取距省库最远的网点作为极轴参考方向
            ref_node = max(nodes, key=lambda x: get_dist(0, x))
            d0_ref = get_dist(0, ref_node)
            polar_info = []
            for cid in nodes:
                d0_i = max(get_dist(0, cid), 0.001)
                d_ref_i = get_dist(ref_node, cid)
                cos_val = (d0_i**2 + d0_ref**2 - d_ref_i**2) / (2 * d0_i * d0_ref)
                cos_val = max(-1.0, min(1.0, cos_val))
                polar_info.append((cid, cos_val, unassigned[cid]))
            # 先按cos角分组(同方向), 组内按需求量降序
            polar_info.sort(key=lambda x: (-x[1], -x[2]))
            pending = [(cid, amt) for cid, _, amt in polar_info]
        else:
            pending = sorted(unassigned.items(), key=lambda x: x[1], reverse=True)

        for cid, total_amt in pending:
            amt = total_amt
            while amt > 0:
                # Phase 1: 优先找能完整装下的路线（防拆分）
                best_route = None
                for r in routes:
                    space = MAX_CAP - sum(a for _, a in r['deliveries'])
                    if space < amt: continue
                    has_cid = any(c == cid for c, _ in r['deliveries'])
                    if not has_cid and len(r['deliveries']) >= 3: continue
                    if not has_cid:
                        if not all(check_angle_constraint(cid, ec) for ec, _ in r['deliveries']): continue
                    temp = copy.deepcopy(r)
                    if has_cid:
                        for i, (c, a) in enumerate(temp['deliveries']):
                            if c == cid: temp['deliveries'][i] = (c, a + amt); break
                    else:
                        temp['deliveries'].append((cid, amt))
                    temp = optimize_route_sequence(temp)
                    if calc_route_distance(temp['deliveries']) <= MAX_ROUTE_DIST:
                        best_route = r
                        break
                if best_route is not None:
                    has_cid = any(c == cid for c, _ in best_route['deliveries'])
                    if has_cid:
                        for i, (c, a) in enumerate(best_route['deliveries']):
                            if c == cid: best_route['deliveries'][i] = (c, a + amt); break
                    else:
                        best_route['deliveries'].append((cid, amt))
                    best_route = optimize_route_sequence(best_route)
                    amt = 0
                    continue

                # Phase 2: 拆分插入 — 选能装最多的路线, 最小化拆分碎片
                best_space = 0
                best_route = None
                for r in routes:
                    space = MAX_CAP - sum(a for _, a in r['deliveries'])
                    if space <= 0: continue
                    has_cid = any(c == cid for c, _ in r['deliveries'])
                    if not has_cid and len(r['deliveries']) >= 3: continue
                    if not has_cid:
                        if not all(check_angle_constraint(cid, ec) for ec, _ in r['deliveries']): continue
                    take = min(amt, space)
                    temp = copy.deepcopy(r)
                    if has_cid:
                        for i, (c, a) in enumerate(temp['deliveries']):
                            if c == cid: temp['deliveries'][i] = (c, a + take); break
                    else:
                        temp['deliveries'].append((cid, take))
                    temp = optimize_route_sequence(temp)
                    if calc_route_distance(temp['deliveries']) <= MAX_ROUTE_DIST:
                        if space > best_space:
                            best_space = space
                            best_route = r

                if best_route is not None:
                    take = min(amt, best_space)
                    has_cid = any(c == cid for c, _ in best_route['deliveries'])
                    if has_cid:
                        for i, (c, a) in enumerate(best_route['deliveries']):
                            if c == cid: best_route['deliveries'][i] = (c, a + take); break
                    else:
                        best_route['deliveries'].append((cid, take))
                    best_route = optimize_route_sequence(best_route)
                    amt -= take
                    continue

                # Phase 3: 新建路线 — 优先大车, 留空间给后续合并
                cfg = VEHICLE_CONFIG[-1]
                load = min(amt, MAX_CAP)
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

                    if not has_cid and len(route['deliveries']) >= 3: continue

                    direction_ok = True
                    if not has_cid:
                        for exist_cid, _ in route['deliveries']:
                            if not check_angle_constraint(cid, exist_cid):
                                direction_ok = False
                                break
                    if not direction_ok: continue

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

                        # =======================================================
                        # 【核心约束 3】：路线总里程验证（贪心插入拦截器）
                        # 破坏重建时，尝试塞入新货，同样必须满足 <= 750Km 的安全底线
                        # =======================================================
                        if calc_route_distance(temp['deliveries']) <= MAX_ROUTE_DIST:
                            fit = eval_route_fitness(temp)
                            # 同网点优先: 防止需求被拆散到其他路线
                            if has_cid:
                                fit *= 0.5
                            # 拆分惩罚: 装不下全部时加罚, 最小化碎片
                            if ins_amt < (amt - 0.001):
                                fit *= 1.5
                            if fit < best_fit:
                                best_fit = fit
                                best_action = ('insert', ri, ins_amt, temp['deliveries'])

                if best_action is None:
                    # 优先大车: 留空间给后续合并
                    cfg = VEHICLE_CONFIG[-1]
                    ins_amt = min(amt, MAX_CAP)
                    temp = {'vehicle_type': cfg['type'], 'deliveries': [(cid, ins_amt)]}
                    best_action = ('new', cfg['type'], ins_amt, temp['deliveries'])

                if best_action[0] == 'insert':
                    routes[best_action[1]]['deliveries'] = best_action[3]
                    amt -= best_action[2]
                else:
                    routes.append({'vehicle_type': best_action[1], 'deliveries': best_action[3]})
                    amt -= best_action[2]
        return [r for r in routes if r['deliveries']]

    '''3. 启发式算法：空间最优解 (ALNS)'''
    max_iter = 600
    best_sol = generate_initial_solution(unit_sum)
    best_sol = reassign_vehicles(best_sol)
    best_fitness = sum(eval_route_fitness(r) for r in best_sol)

    for i in range(max_iter):
        remove_cnt = max(2, int(len(unit_sum) * 0.3))
        destroyed, unassigned = random_removal(best_sol, num_remove=remove_cnt)
        new_sol = greedy_insertion(destroyed, unassigned)

        new_sol = [r for r in new_sol if r['deliveries']]
        new_sol = reassign_vehicles(new_sol)
        new_fitness = sum(eval_route_fitness(r) for r in new_sol)

        if new_fitness < best_fitness or math.exp((best_fitness - new_fitness) / 100) > random.random():
            best_sol, best_fitness = new_sol, new_fitness

    best_sol = reassign_vehicles(best_sol)
    best_sol = [r for r in best_sol if r['deliveries']]

    '''4. 整数线性规划 Stage 2: 日期精确排程 (引入网点时间窗离散惩罚)'''
    num_routes = len(best_sol)
    if num_routes == 0: return []

    prob2 = pulp.LpProblem("Minimize_Peak_Daily_And_Clustering", pulp.LpMinimize)
    x = pulp.LpVariable.dicts("x", [(r, d) for r in range(num_routes) for d in range(DelivDay)], cat='Binary')
    Z = pulp.LpVariable("Peak_Volume", lowBound=0, cat='Continuous')

    ALPHA = 50000

    node_to_routes = defaultdict(list)
    for r in range(num_routes):
        for cid, _ in best_sol[r]['deliveries']:
            node_to_routes[cid].append(r)

    penalties = []
    for cid, r_list in node_to_routes.items():
        freq = len(r_list)
        if freq <= 1:
            continue

        window_size = min(max(1, (DelivDay // freq) - 1), 5)

        for d in range(DelivDay - window_size):
            routes_in_window = pulp.lpSum(x[r, d + i] for r in r_list for i in range(window_size + 1))
            p_var = pulp.LpVariable(f"Pen_Node_{cid}_Day_{d}", lowBound=0, cat='Continuous')
            prob2 += p_var >= routes_in_window - 1
            penalties.append(p_var)

    if penalties:
        prob2 += Z + ALPHA * pulp.lpSum(penalties)
    else:
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