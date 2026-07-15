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


def GetDelivPlan(Demands, LocationNum, TypeList, SubTypeList, DelivDay, VeUnitPrice, VeTypeNum, VNums, VeCap, DMAT, node_priority=None, daily_vehicle_limits=None, vehicle_types=None, near_center_nodes=None):
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
    if vehicle_types is None:
        vehicle_types = list(range(1, VeTypeNum + 1))
    VEHICLE_CONFIG = sorted([{'type': vehicle_types[i], 'cap': VeCap[i], 'daily_max': VNums[i]} for i in range(VeTypeNum)],
                            key=lambda x: x['cap'])
    MAX_CAP = VEHICLE_CONFIG[-1]['cap']

    def _route_cap(route):
        """获取路线实际分配的车型容量（被 reassign_vehicles 可能换了车型），未分配时兜底 MAX_CAP"""
        vt = route.get('vehicle_type')
        if vt is not None:
            v_idx = int(vt) - 1
            if 0 <= v_idx < len(VEHICLE_CONFIG):
                return VEHICLE_CONFIG[v_idx]['cap']
        return MAX_CAP

    # 【新增全局红线】：单条路线的闭环最大行驶里程
    MAX_ROUTE_DIST = 750

    def pick_best_vehicle(load, quotas):
        """选能装下load且还有配额的最小车型，无可用车型返回None"""
        for cfg in VEHICLE_CONFIG:
            if cfg['cap'] >= load and quotas[cfg['type']] > 0:
                return cfg
        return None

    def _get_monthly_quota():
        """根据每日车辆配额或固定配置计算各车型月总配额"""
        if daily_vehicle_limits:
            quota = {}
            for cfg in VEHICLE_CONFIG:
                vt = cfg['type']
                quota[vt] = sum(daily_vehicle_limits[d].get(vt, 0) for d in range(DelivDay))
            return quota
        return {cfg['type']: cfg['daily_max'] * DelivDay for cfg in VEHICLE_CONFIG}

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
        if near_center_nodes is not None and cid1 in near_center_nodes and cid2 in near_center_nodes:
            return True
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
        """按车型月度配额分配，超配额路线拆回需求供 ALNS 重塞。返回 (assigned_routes, dropped_demand)"""
        monthly_quota = _get_monthly_quota()
        total_quota = sum(monthly_quota.values())
        routes_sorted = sorted(routes, key=lambda r: sum(a for _, a in r['deliveries']), reverse=True)
        assigned = []
        dropped_demand = defaultdict(float)
        for r in routes_sorted:
            load = sum(a for _, a in r['deliveries'])
            # 第一优先：找能装下且有月度配额的车型
            best_cfg = None
            for cfg in VEHICLE_CONFIG:
                if cfg['cap'] >= load and monthly_quota[cfg['type']] > 0:
                    best_cfg = cfg
                    break
            # 降级：所有有配额车型都装不下 → 用仍有配额的最大车型
            if best_cfg is None:
                for cfg in reversed(VEHICLE_CONFIG):
                    if monthly_quota[cfg['type']] > 0:
                        best_cfg = cfg
                        break
            if best_cfg is not None:
                r['vehicle_type'] = best_cfg['type']
                monthly_quota[best_cfg['type']] -= 1
                # 车型容量不够：截断到容量，超出的拆回 dropped_demand 让 ALNS 重塞
                if best_cfg['cap'] < load:
                    excess = load - best_cfg['cap']
                    trim_remaining = excess
                    for i, (cid, amt) in enumerate(r['deliveries']):
                        if trim_remaining <= 0:
                            break
                        trim = min(amt, trim_remaining)
                        r['deliveries'][i] = (cid, amt - trim)
                        dropped_demand[cid] += trim
                        trim_remaining -= trim
                    r['deliveries'] = [(c, a) for c, a in r['deliveries'] if a > 0.001]
                assigned.append(r)
            else:
                # 配额用尽，拆回需求供 ALNS 重塞
                for cid, amt in r['deliveries']:
                    dropped_demand[cid] += amt
        return assigned, dict(dropped_demand)

    def _build_routes(pending, routes):
        """通用贪心构建：按 pending 顺序逐个网点插入已有路线或建新车"""
        for cid, total_amt in pending:
            amt = total_amt
            while amt > 0:
                # Phase 1: best-fit — 评估所有能完整装下的路线，选剩余空间最小的（最大化装载率）
                best_waste = float('inf')
                best_route = None
                for r in routes:
                    space = _route_cap(r) - sum(a for _, a in r['deliveries'])
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
                        waste = space - amt
                        # 同网点路线大幅优先（防止需求被拆散到其他路线）
                        if has_cid:
                            waste *= 0.3
                        if waste < best_waste:
                            best_waste = waste
                            best_route = r
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

                # Phase 2: 拆分插入 — 选能装最多且剩余空间最小的路线
                best_score = float('inf')  # 综合评分 = waste - 0.5*space, 空间大+浪费小
                best_action = None
                for r in routes:
                    space = _route_cap(r) - sum(a for _, a in r['deliveries'])
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
                        waste = space - take
                        score = waste - 0.3 * space  # 空间大也加分
                        if has_cid:
                            score *= 0.5
                        if score < best_score:
                            best_score = score
                            best_action = (r, take, temp['deliveries'])

                if best_action is not None:
                    r, take, new_dels = best_action
                    r['deliveries'] = new_dels
                    amt -= take
                    continue

                # Phase 3: 新建路线 — 选最小能装下的车型+留20%余量
                load = min(amt, MAX_CAP)
                cfg = VEHICLE_CONFIG[-1]
                for c in VEHICLE_CONFIG:
                    if c['cap'] >= load * 1.2:
                        cfg = c
                        break
                if cfg['cap'] < load:
                    for c in VEHICLE_CONFIG:
                        if c['cap'] >= load:
                            cfg = c
                            break
                routes.append({'vehicle_type': cfg['type'], 'deliveries': [(cid, load)]})
                amt -= load

    def generate_initial_solution(unassigned):
        """Multi-start: 3 种排序策略各建一个初解，返回最优的"""
        nodes = list(unassigned.keys())
        if len(nodes) <= 1:
            pending = sorted(unassigned.items(), key=lambda x: x[1], reverse=True)
            routes = []
            _build_routes(pending, routes)
            return _merge_small_routes(routes)

        # 预计算各网点极坐标
        ref_node = max(nodes, key=lambda x: get_dist(0, x))
        d0_ref = get_dist(0, ref_node)
        polar_info = []
        for cid in nodes:
            d0_i = max(get_dist(0, cid), 0.001)
            d_ref_i = get_dist(ref_node, cid)
            cos_val = (d0_i**2 + d0_ref**2 - d_ref_i**2) / (2 * d0_i * d0_ref)
            cos_val = max(-1.0, min(1.0, cos_val))
            polar_info.append((cid, cos_val, get_dist(0, cid), unassigned[cid]))

        # 策略定义: (名称, 排序键)
        strategies = [
            ('polar_demand',   lambda x: (-x[1], -x[3])),   # 同方向+需求量降序
            ('polar_dist',     lambda x: (-x[1], x[2])),     # 同方向+距省库升序(近及远)
            ('demand_desc',    lambda x: (-x[3], -x[1])),    # 纯需求量降序+同方向
        ]

        best_routes = None
        best_fit = float('inf')

        for name, sort_key in strategies:
            polar_info.sort(key=sort_key)
            pending = [(cid, amt) for cid, _, _, amt in polar_info]
            routes = []
            _build_routes(pending, routes)
            routes = _merge_small_routes(routes)

            # 快速 relocation 局部搜索
            routes = _relocate_improve(routes, max_passes=3)

            fit = sum(eval_route_fitness(r) for r in routes)
            if fit < best_fit:
                best_fit = fit
                best_routes = routes
                logging.info(f"[初解] 策略 '{name}': {len(routes)}条路线, cost={fit:,.0f} ✓最优")

        return best_routes

    def _relocate_improve(routes, max_passes=3):
        """局部搜索: 尝试把 delivery 从一条路线迁移到另一条路线，降低总成本"""
        for _ in range(max_passes):
            improved = False
            n = len(routes)
            for ri in range(n):
                if improved: break
                for pi, (cid, amt) in enumerate(routes[ri]['deliveries']):
                    if improved: break
                    best_saving = 0
                    best_move = None
                    old_cost_i = eval_route_fitness(routes[ri])

                    for rj in range(n):
                        if rj == ri: continue
                        has_cid = any(c == cid for c, _ in routes[rj]['deliveries'])
                        if not has_cid and len(routes[rj]['deliveries']) >= 3: continue
                        if not has_cid:
                            if not all(check_angle_constraint(cid, ec) for ec, _ in routes[rj]['deliveries']): continue

                        space = _route_cap(routes[rj]) - sum(a for _, a in routes[rj]['deliveries'])
                        if space < amt: continue

                        old_cost_j = eval_route_fitness(routes[rj])
                        temp_i = copy.deepcopy(routes[ri])
                        temp_j = copy.deepcopy(routes[rj])
                        temp_i['deliveries'].pop(pi)
                        if has_cid:
                            for k, (c, a) in enumerate(temp_j['deliveries']):
                                if c == cid:
                                    temp_j['deliveries'][k] = (c, a + amt)
                                    break
                        else:
                            temp_j['deliveries'].append((cid, amt))
                        temp_i = optimize_route_sequence(temp_i)
                        temp_j = optimize_route_sequence(temp_j)

                        if calc_route_distance(temp_i['deliveries']) <= MAX_ROUTE_DIST and \
                           calc_route_distance(temp_j['deliveries']) <= MAX_ROUTE_DIST:
                            new_cost = eval_route_fitness(temp_i) + eval_route_fitness(temp_j)
                            saving = (old_cost_i + old_cost_j) - new_cost
                            if saving > best_saving:
                                best_saving = saving
                                best_move = (ri, rj, temp_i['deliveries'], temp_j['deliveries'])

                    if best_move:
                        ri2, rj2, new_i_dels, new_j_dels = best_move
                        routes[ri2]['deliveries'] = new_i_dels
                        routes[rj2]['deliveries'] = new_j_dels
                        improved = True

            if not improved:
                break
        return [r for r in routes if r['deliveries']]

    def _merge_small_routes(routes):
        """贪心合并低装载率的兼容路线，减少车辆使用数"""
        improved = True
        while improved:
            improved = False
            n = len(routes)
            # 按装载量升序排列，优先从小路线合并
            idx_by_load = sorted(range(n), key=lambda i: sum(a for _, a in routes[i]['deliveries']))
            for ii in range(n):
                if improved:
                    break
                i = idx_by_load[ii]
                for jj in range(ii + 1, n):
                    j = idx_by_load[jj]
                    load_i = sum(a for _, a in routes[i]['deliveries'])
                    load_j = sum(a for _, a in routes[j]['deliveries'])
                    total_load = load_i + load_j

                    # 找出能装下合并量的最小车型
                    fit_cfg = None
                    for cfg in VEHICLE_CONFIG:
                        if cfg['cap'] >= total_load:
                            fit_cfg = cfg
                            break
                    if fit_cfg is None:
                        continue

                    combined = routes[i]['deliveries'] + routes[j]['deliveries']
                    unique_nodes = set(c for c, _ in combined)
                    if len(unique_nodes) > 3:
                        continue

                    # 角度约束
                    nodes_lst = list(unique_nodes)
                    angle_ok = True
                    for ni in range(len(nodes_lst)):
                        for nj in range(ni + 1, len(nodes_lst)):
                            if not check_angle_constraint(nodes_lst[ni], nodes_lst[nj]):
                                angle_ok = False
                                break
                        if not angle_ok:
                            break
                    if not angle_ok:
                        continue

                    # 距离约束
                    merged = {'vehicle_type': fit_cfg['type'], 'deliveries': combined}
                    merged = optimize_route_sequence(merged)
                    if calc_route_distance(merged['deliveries']) <= MAX_ROUTE_DIST:
                        old_cost = eval_route_fitness(routes[i]) + eval_route_fitness(routes[j])
                        new_cost = eval_route_fitness(merged)
                        if new_cost < old_cost:
                            routes[i] = merged
                            routes.pop(j)
                            improved = True
                            break
        return routes

    # ====================================================================
    # 【新增算子 1】批量组合合并算子：突破两两合并，支持最多3条路线重组
    # ====================================================================
    def batch_merge_routes(routes, max_combine=3):
        improved = True
        while improved:
            improved = False
            n = len(routes)
            if n < 2:
                break

            # 预计算每条路线的装载量、装载率
            route_info = []
            for idx, r in enumerate(routes):
                load = sum(a for _, a in r['deliveries'])
                fit_cap = MAX_CAP
                for cfg in VEHICLE_CONFIG:
                    if cfg['cap'] >= load:
                        fit_cap = cfg['cap']
                        break
                load_rate = load / fit_cap if fit_cap > 0 else 0
                route_info.append({
                    'idx': idx,
                    'load': load,
                    'load_rate': load_rate,
                    'nodes': set(c for c, _ in r['deliveries'])
                })

            # 优先处理低装载率路线
            route_info.sort(key=lambda x: x['load_rate'])
            candidate_idxs = [item['idx'] for item in route_info]

            best_gain = 0
            best_combo = None

            # 枚举2~max_combine条路线的组合
            for k in range(2, min(max_combine, len(candidate_idxs)) + 1):
                # 仅枚举前15条低载路线，控制计算量
                for combo in itertools.combinations(candidate_idxs[:min(15, len(candidate_idxs))], k):
                    all_deliveries = []
                    all_nodes = set()
                    total_load = 0
                    for ri in combo:
                        for cid, amt in routes[ri]['deliveries']:
                            all_deliveries.append((cid, amt))
                            all_nodes.add(cid)
                            total_load += amt

                    # 快速剪枝：总网点数超出拆分上限则跳过
                    if len(all_nodes) > 3 * (k - 1):
                        continue

                    old_cost = sum(eval_route_fitness(routes[ri]) for ri in combo)

                    # 按方向聚类贪心重组为k-1条路线
                    nodes_sorted = sorted(all_nodes, key=lambda c: node_cos.get(c, 1.0), reverse=True)
                    new_routes = []
                    current_route_deliveries = []
                    current_load = 0
                    current_nodes = set()

                    for cid in nodes_sorted:
                        amt = sum(a for c, a in all_deliveries if c == cid)
                        temp_nodes = current_nodes | {cid}
                        temp_load = current_load + amt
                        
                        if len(temp_nodes) <= 3 and temp_load <= MAX_CAP:
                            angle_ok = True
                            for exist_cid in current_nodes:
                                if not check_angle_constraint(cid, exist_cid):
                                    angle_ok = False
                                    break
                            if angle_ok:
                                current_route_deliveries.append((cid, amt))
                                current_nodes.add(cid)
                                current_load += amt
                                continue
                        
                        if current_route_deliveries:
                            new_routes.append(current_route_deliveries)
                        current_route_deliveries = [(cid, amt)]
                        current_nodes = {cid}
                        current_load = amt
                    if current_route_deliveries:
                        new_routes.append(current_route_deliveries)

                    # 重组后路线数必须减少才有意义
                    if len(new_routes) >= k:
                        continue

                    # 校验每条新路线的约束
                    valid = True
                    new_route_objs = []
                    for dels in new_routes:
                        load_r = sum(a for _, a in dels)
                        fit_cfg = None
                        for cfg in VEHICLE_CONFIG:
                            if cfg['cap'] >= load_r:
                                fit_cfg = cfg
                                break
                        if fit_cfg is None:
                            valid = False
                            break
                        r_obj = {'vehicle_type': fit_cfg['type'], 'deliveries': dels}
                        r_obj = optimize_route_sequence(r_obj)
                        if calc_route_distance(r_obj['deliveries']) > MAX_ROUTE_DIST:
                            valid = False
                            break
                        new_route_objs.append(r_obj)

                    if not valid:
                        continue

                    new_cost = sum(eval_route_fitness(r) for r in new_route_objs)
                    gain = old_cost - new_cost
                    if gain > best_gain:
                        best_gain = gain
                        best_combo = (combo, new_route_objs)

            # 执行收益最大的合并
            if best_combo is not None and best_gain > 0:
                combo, new_route_objs = best_combo
                for ri in sorted(combo, reverse=True):
                    routes.pop(ri)
                routes.extend(new_route_objs)
                improved = True

        return [r for r in routes if r['deliveries']]

    # ====================================================================
    # 【新增算子 2】交换-合并协同算子：先交换网点再尝试合并
    # ====================================================================
    def swap_merge_routes(routes):
        def _check_route_angle(nodes):
            lst = list(nodes)
            for a in range(len(lst)):
                for b in range(a + 1, len(lst)):
                    if not check_angle_constraint(lst[a], lst[b]):
                        return False
            return True

        improved = True
        while improved:
            improved = False
            n = len(routes)
            if n < 2:
                break

            best_gain = 0
            best_action = None

            for i in range(n):
                for j in range(i + 1, n):
                    ri = routes[i]
                    rj = routes[j]
                    old_cost = eval_route_fitness(ri) + eval_route_fitness(rj)

                    for pi, (cid_i, amt_i) in enumerate(ri['deliveries']):
                        for pj, (cid_j, amt_j) in enumerate(rj['deliveries']):
                            if cid_i == cid_j:
                                continue

                            # 构造交换后的两条路线
                            temp_i = copy.deepcopy(ri)
                            temp_j = copy.deepcopy(rj)
                            temp_i['deliveries'].pop(pi)
                            temp_j['deliveries'].pop(pj)
                            temp_i['deliveries'].append((cid_j, amt_j))
                            temp_j['deliveries'].append((cid_i, amt_i))

                            # 网点数约束
                            nodes_i = set(c for c, _ in temp_i['deliveries'])
                            nodes_j = set(c for c, _ in temp_j['deliveries'])
                            if len(nodes_i) > 3 or len(nodes_j) > 3:
                                continue

                            # 角度约束
                            if not _check_route_angle(nodes_i) or not _check_route_angle(nodes_j):
                                continue

                            # 排序+里程校验
                            temp_i = optimize_route_sequence(temp_i)
                            temp_j = optimize_route_sequence(temp_j)
                            if calc_route_distance(temp_i['deliveries']) > MAX_ROUTE_DIST:
                                continue
                            if calc_route_distance(temp_j['deliveries']) > MAX_ROUTE_DIST:
                                continue

                            # 方案1：仅交换的收益
                            new_cost_swap = eval_route_fitness(temp_i) + eval_route_fitness(temp_j)
                            gain_swap = old_cost - new_cost_swap

                            # 方案2：交换后合并两条路线
                            gain_merge = 0
                            merged_route = None
                            total_load = sum(a for _, a in temp_i['deliveries']) + sum(a for _, a in temp_j['deliveries'])
                            fit_cfg = None
                            for cfg in VEHICLE_CONFIG:
                                if cfg['cap'] >= total_load:
                                    fit_cfg = cfg
                                    break
                            if fit_cfg is not None:
                                combined_dels = temp_i['deliveries'] + temp_j['deliveries']
                                combined_nodes = set(c for c, _ in combined_dels)
                                if len(combined_nodes) <= 3 and _check_route_angle(combined_nodes):
                                    merged = {'vehicle_type': fit_cfg['type'], 'deliveries': combined_dels}
                                    merged = optimize_route_sequence(merged)
                                    if calc_route_distance(merged['deliveries']) <= MAX_ROUTE_DIST:
                                        new_cost_merge = eval_route_fitness(merged)
                                        gain_merge = old_cost - new_cost_merge
                                        merged_route = merged

                            max_gain = max(gain_swap, gain_merge)
                            if max_gain > best_gain:
                                best_gain = max_gain
                                if gain_merge >= gain_swap and merged_route is not None:
                                    best_action = ('merge', i, j, merged_route)
                                else:
                                    best_action = ('swap', i, j, temp_i['deliveries'], temp_j['deliveries'])

            if best_action is not None and best_gain > 0:
                action_type = best_action[0]
                i, j = best_action[1], best_action[2]
                if action_type == 'merge':
                    routes.pop(j)
                    routes.pop(i)
                    routes.append(best_action[3])
                else:
                    routes[i]['deliveries'] = best_action[3]
                    routes[j]['deliveries'] = best_action[4]
                improved = True

        return [r for r in routes if r['deliveries']]

    # ====================================================================
    # 【新增算子 3】低载路线拆解填充算子：彻底消除低效路线
    # ====================================================================
    def dismantle_low_load_routes(routes, min_load_rate=0.5):
        improved = True
        while improved:
            improved = False
            n = len(routes)
            if n <= 1:
                break

            # 计算每条路线的装载率，升序排列
            route_rates = []
            for idx, r in enumerate(routes):
                load = sum(a for _, a in r['deliveries'])
                fit_cap = MAX_CAP
                for cfg in VEHICLE_CONFIG:
                    if cfg['cap'] >= load:
                        fit_cap = cfg['cap']
                        break
                rate = load / fit_cap if fit_cap > 0 else 0
                route_rates.append((idx, rate, load))
            route_rates.sort(key=lambda x: x[1])

            # 从装载率最低的开始尝试拆解
            for idx, rate, load in route_rates:
                if rate >= min_load_rate:
                    break

                target_route = routes[idx]
                deliveries_to_move = list(target_route['deliveries'])
                move_success = [False] * len(deliveries_to_move)

                for di, (cid, amt) in enumerate(deliveries_to_move):
                    best_fit = float('inf')
                    best_rj = None
                    best_dels = None

                    for rj in range(n):
                        if rj == idx:
                            continue
                        other = routes[rj]
                        has_cid = any(c == cid for c, _ in other['deliveries'])
                        if not has_cid and len(other['deliveries']) >= 3:
                            continue
                        # 角度校验
                        if not has_cid:
                            if not all(check_angle_constraint(cid, ec) for ec, _ in other['deliveries']):
                                continue
                        # 容量校验
                        space = _route_cap(other) - sum(a for _, a in other['deliveries'])
                        if space < amt:
                            continue
                        # 构造临时路线并校验
                        temp = copy.deepcopy(other)
                        if has_cid:
                            for k, (c, a) in enumerate(temp['deliveries']):
                                if c == cid:
                                    temp['deliveries'][k] = (c, a + amt)
                                    break
                        else:
                            temp['deliveries'].append((cid, amt))
                        temp = optimize_route_sequence(temp)
                        if calc_route_distance(temp['deliveries']) <= MAX_ROUTE_DIST:
                            fit = eval_route_fitness(temp)
                            if fit < best_fit:
                                best_fit = fit
                                best_rj = rj
                                best_dels = temp['deliveries']

                    if best_rj is not None:
                        routes[best_rj]['deliveries'] = best_dels
                        move_success[di] = True

                # 所有网点都成功移走则删除原路线
                if all(move_success):
                    routes.pop(idx)
                    improved = True
                    break

        return [r for r in routes if r['deliveries']]

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

    def worst_removal(solution, num_remove=3):
        """移除单位成本最高的路线中的 delivery，优先优化低效拼车"""
        sol_copy = copy.deepcopy(solution)
        unassigned = defaultdict(float)

        delivery_costs = []
        for ri, route in enumerate(sol_copy):
            if not route['deliveries']:
                continue
            route_fitness = eval_route_fitness(route)
            route_load = sum(amt for _, amt in route['deliveries'])
            unit_cost = route_fitness / route_load if route_load > 0 else float('inf')
            for pi, (cid, amt) in enumerate(route['deliveries']):
                noise = random.uniform(0.8, 1.2)  # 随机扰动避免死板
                delivery_costs.append((unit_cost * noise, ri, pi, cid, amt))

        delivery_costs.sort(key=lambda x: x[0], reverse=True)
        removed = 0
        for _, ri, pi, cid, amt in delivery_costs:
            if removed >= num_remove:
                break
            if pi < len(sol_copy[ri]['deliveries']) and sol_copy[ri]['deliveries'][pi][0] == cid:
                sol_copy[ri]['deliveries'].pop(pi)
                unassigned[cid] += amt
                removed += 1

        sol_copy = [r for r in sol_copy if r['deliveries']]
        return sol_copy, unassigned

    def shaw_removal(solution, num_remove=3):
        """Shaw 破坏：移除方向/距离相似的 delivery 集合，让 repair 重新组合拼车"""
        sol_copy = copy.deepcopy(solution)
        unassigned = defaultdict(float)

        if not sol_copy:
            return sol_copy, unassigned

        all_deliveries = []
        for ri, route in enumerate(sol_copy):
            for pi, (cid, amt) in enumerate(route['deliveries']):
                all_deliveries.append((ri, pi, cid, amt))

        if len(all_deliveries) <= 1:
            return random_removal(solution, num_remove)

        # 随机选种子
        seed = random.choice(all_deliveries)
        seed_cid = seed[2]
        seed_cos = node_cos.get(seed_cid, 1.0)
        seed_d0 = get_dist(0, seed_cid)

        # 计算其他 delivery 与种子的相似度
        relatedness = []
        for ri, pi, cid, amt in all_deliveries:
            if ri == seed[0] and pi == seed[1]:
                continue
            d0_i = get_dist(0, cid)
            cos_i = node_cos.get(cid, 1.0)
            dir_diff = abs(cos_i - seed_cos)          # 方向差异
            dist_diff = abs(d0_i - seed_d0) / max_d0  # 距省库远近差异（归一化）
            rel = 0.6 * dir_diff + 0.4 * dist_diff
            rel *= random.uniform(0.9, 1.3)           # 随机扰动
            relatedness.append((rel, ri, pi, cid, amt))

        # 最相似的 num_remove-1 个 + 种子
        relatedness.sort(key=lambda x: x[0])
        to_remove = [(seed[0], seed[1], seed[2], seed[3])]
        to_remove += [(r[1], r[2], r[3], r[4]) for r in relatedness[:num_remove - 1]]

        # 按索引降序删除（避免错位）
        remove_set = {}
        for ri, pi, cid, amt in to_remove:
            remove_set[(ri, pi)] = (cid, amt)

        for (ri, pi), (cid, amt) in sorted(remove_set.items(), key=lambda x: (-x[0][0], -x[0][1])):
            if ri < len(sol_copy) and pi < len(sol_copy[ri]['deliveries']):
                if sol_copy[ri]['deliveries'][pi][0] == cid:
                    sol_copy[ri]['deliveries'].pop(pi)
                    unassigned[cid] += amt

        sol_copy = [r for r in sol_copy if r['deliveries']]
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

                    space = _route_cap(route) - sum(a for _, a in route['deliveries'])
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

                        if calc_route_distance(temp['deliveries']) <= MAX_ROUTE_DIST:
                            fit = eval_route_fitness(temp)
                            if has_cid:
                                fit *= 0.5
                            if ins_amt < (amt - 0.001):
                                fit *= 5000
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

    def regret2_insertion(routes, unassigned):
        """Regret-2 插入：优先处理 best 与 2nd-best 差距最大的 delivery，避免最后无路可走"""
        pending = dict(unassigned)

        while pending:
            best_regret = -1.0
            best_insertion = None  # (cid, action_tuple)

            for cid, amt in list(pending.items()):
                costs = []  # [(fitness, action_type, arg1, ins_amt, deliveries), ...]

                # 检查所有已有的路线
                for ri, route in enumerate(routes):
                    has_cid = any(c == cid for c, _ in route['deliveries'])
                    if not has_cid and len(route['deliveries']) >= 3:
                        continue
                    if not has_cid:
                        if not all(check_angle_constraint(cid, ec) for ec, _ in route['deliveries']):
                            continue

                    space = _route_cap(route) - sum(a for _, a in route['deliveries'])
                    if space <= 0:
                        continue

                    ins_amt = min(amt, space)
                    temp = copy.deepcopy(route)
                    if has_cid:
                        for k, (c, a) in enumerate(temp['deliveries']):
                            if c == cid:
                                temp['deliveries'][k] = (c, a + ins_amt)
                                break
                    else:
                        temp['deliveries'].append((cid, ins_amt))
                    temp = optimize_route_sequence(temp)

                    if calc_route_distance(temp['deliveries']) <= MAX_ROUTE_DIST:
                        fit = eval_route_fitness(temp)
                        if has_cid:
                            fit *= 0.5
                        if ins_amt < amt - 0.001:
                            fit *= 1.5
                        costs.append((fit, 'insert', ri, ins_amt, temp['deliveries']))

                # 新建路线作为兜底
                cfg = VEHICLE_CONFIG[-1]
                ins_amt = min(amt, MAX_CAP)
                temp = {'vehicle_type': cfg['type'], 'deliveries': [(cid, ins_amt)]}
                costs.append((eval_route_fitness(temp), 'new', cfg['type'], ins_amt, temp['deliveries']))

                if not costs:
                    continue

                costs.sort(key=lambda x: x[0])

                regret = (costs[1][0] - costs[0][0]) if len(costs) >= 2 else 1e9

                if regret > best_regret:
                    best_regret = regret
                    best_insertion = (cid, costs[0])

            if best_insertion is None:
                break

            cid, (_, action_type, arg1, ins_amt, deliveries) = best_insertion

            if action_type == 'insert':
                routes[arg1]['deliveries'] = deliveries
            else:
                routes.append({'vehicle_type': arg1, 'deliveries': deliveries})

            remaining = pending[cid] - ins_amt
            if remaining > 0.0001:
                pending[cid] = remaining
            else:
                del pending[cid]

        return [r for r in routes if r['deliveries']]

    def merge_repair(routes, unassigned):
        """合并修复算子：先贪心塞入未分配需求，再拆散低装载率路线并入其他路线"""
        # Step 1: 贪心处理未分配需求
        if unassigned:
            routes = greedy_insertion(routes, unassigned)

        MIN_LOAD_RATE = 0.40

        improved = True
        while improved:
            improved = False
            n = len(routes)

            # 计算每条路线的装载率，按升序排列
            route_data = []
            for ri, route in enumerate(routes):
                load = sum(a for _, a in route['deliveries'])
                best_cap = MAX_CAP
                for cfg in VEHICLE_CONFIG:
                    if cfg['cap'] >= load:
                        best_cap = cfg['cap']
                        break
                route_data.append((ri, load / best_cap if best_cap > 0 else 0, load, route))

            route_data.sort(key=lambda x: x[1])  # 装载率升序

            for ri, lr, load, route in route_data:
                if lr >= MIN_LOAD_RATE:
                    break  # 后面的都高于阈值，不用处理了

                # 尝试把这条路线的每个 delivery 塞进其他路线
                deliveries_to_move = list(route['deliveries'])
                all_moved = True

                for cid, amt in deliveries_to_move:
                    moved = False
                    for rj in range(n):
                        if rj == ri:
                            continue
                        other = routes[rj]

                        has_cid = any(c == cid for c, _ in other['deliveries'])
                        if not has_cid and len(other['deliveries']) >= 3:
                            continue

                        # 角度约束
                        if not has_cid:
                            if not all(check_angle_constraint(cid, ec) for ec, _ in other['deliveries']):
                                continue

                        space = _route_cap(other) - sum(a for _, a in other['deliveries'])
                        if space < amt:
                            continue

                        # 尝试插入
                        temp = copy.deepcopy(other)
                        if has_cid:
                            for k, (c, a) in enumerate(temp['deliveries']):
                                if c == cid:
                                    temp['deliveries'][k] = (c, a + amt)
                                    break
                        else:
                            temp['deliveries'].append((cid, amt))
                        temp = optimize_route_sequence(temp)

                        if calc_route_distance(temp['deliveries']) <= MAX_ROUTE_DIST:
                            routes[rj]['deliveries'] = temp['deliveries']
                            moved = True
                            break

                    if not moved:
                        all_moved = False
                        break

                if all_moved:
                    # 全部移走 → 删除空路线
                    routes.pop(ri)
                    improved = True
                    break  # 重头扫描

        return [r for r in routes if r['deliveries']]

    '''3. 自适应大邻域搜索算法 (Adaptive ALNS)'''

    # ---- 3.0 预计算：各网点方向角（供 Shaw Removal 使用） ----
    nodes_list = list(unit_sum.keys())
    if len(nodes_list) >= 2:
        ref_node = max(nodes_list, key=lambda x: get_dist(0, x))
        d0_ref = get_dist(0, ref_node)
        node_cos = {}
        for cid in nodes_list:
            d0_i = max(get_dist(0, cid), 0.001)
            d_ref_i = get_dist(ref_node, cid)
            cos_val = (d0_i**2 + d0_ref**2 - d_ref_i**2) / (2 * d0_i * d0_ref)
            node_cos[cid] = max(-1.0, min(1.0, cos_val))
    else:
        node_cos = {cid: 1.0 for cid in nodes_list}
    max_d0 = max(get_dist(0, cid) for cid in nodes_list) if nodes_list else 1.0

    # ---- 3.1 算子定义（新增 dismantle 修复算子） ----
    DESTROY_OPS = ['random', 'worst', 'shaw']
    REPAIR_OPS = ['greedy', 'regret2', 'merge', 'dismantle']

    # ---- 3.2 权重追踪 ----
    weights = {d: {r: 1.0 for r in REPAIR_OPS} for d in DESTROY_OPS}
    scores = {d: {r: 0.0 for r in REPAIR_OPS} for d in DESTROY_OPS}
    uses = {d: {r: 0 for r in REPAIR_OPS} for d in DESTROY_OPS}
    SCORE_NEW_BEST = 30
    SCORE_BETTER = 10
    SCORE_ACCEPTED = 5
    WEIGHT_UPDATE_INTERVAL = 50

    # ---- 3.3 模拟退火 ----
    T = 200.0
    COOLING_RATE = 0.9975
    T_MIN = 1.0

    # ---- 3.4 自适应破坏力度 ----
    base_remove_ratio = 0.20
    stuck_counter = 0
    STUCK_THRESHOLD = 50

    # ---- 3.5 算子选择（轮盘赌） ----
    def select_destroy_op():
        w = [sum(weights[d][r] for r in REPAIR_OPS) for d in DESTROY_OPS]
        total = sum(w)
        if total <= 0:
            return random.choice(DESTROY_OPS)
        rnd = random.random() * total
        cum = 0
        for i, d in enumerate(DESTROY_OPS):
            cum += w[i]
            if rnd <= cum:
                return d
        return DESTROY_OPS[-1]

    def select_repair_op(d_op):
        w = [weights[d_op][r] for r in REPAIR_OPS]
        total = sum(w)
        if total <= 0:
            return random.choice(REPAIR_OPS)
        rnd = random.random() * total
        cum = 0
        for i, r in enumerate(REPAIR_OPS):
            cum += w[i]
            if rnd <= cum:
                return r
        return REPAIR_OPS[-1]

    def update_weights():
        for d in DESTROY_OPS:
            for r in REPAIR_OPS:
                if uses[d][r] > 0:
                    weights[d][r] = 0.9 * weights[d][r] + 0.1 * (scores[d][r] / uses[d][r])
                scores[d][r] = 0.0
                uses[d][r] = 0

    def run_repair(routes, unassigned, r_op):
        if r_op == 'greedy':
            return greedy_insertion(routes, unassigned)
        elif r_op == 'regret2':
            return regret2_insertion(routes, unassigned)
        elif r_op == 'merge':
            return merge_repair(routes, unassigned)
        else:  # dismantle 修复：先插入再拆解低载路线
            routes = greedy_insertion(routes, unassigned)
            return dismantle_low_load_routes(routes, min_load_rate=0.5)

    def run_destroy(solution, num_remove, d_op):
        if d_op == 'random':
            return random_removal(solution, num_remove)
        elif d_op == 'worst':
            return worst_removal(solution, num_remove)
        else:
            return shaw_removal(solution, num_remove)

    # ---- 3.7 构建初解 ----
    # 运力校验：总需求 > 总运力 → 直接报错，避免无限重试
    total_demand_boxes = sum(unit_sum.values())
    mq_check = _get_monthly_quota()
    total_capacity_boxes = sum(mq_check[cfg['type']] * cfg['cap'] for cfg in VEHICLE_CONFIG)
    if total_demand_boxes > total_capacity_boxes:
        raise ValueError(
            f"【运力严重不足】总需求 {total_demand_boxes:,.0f} 箱 > "
            f"总运力 {total_capacity_boxes:,.0f} 箱 "
            f"(缺口 {total_demand_boxes - total_capacity_boxes:,.0f} 箱)，无法排程！"
        )
    max_iter = 40000
    best_sol = generate_initial_solution(unit_sum)
    best_sol, dropped = reassign_vehicles(best_sol)
    retry = 0
    while dropped:
        best_sol = greedy_insertion(best_sol, dropped)
        best_sol, dropped = reassign_vehicles(best_sol)
        retry += 1
        if retry % 500 == 0:
            logging.info(f"[初解重塞] 第{retry}轮, 仍有{len(dropped)}个网点, 总量{sum(dropped.values()):.0f}箱")
        if retry >= 5000:
            raise RuntimeError(
                f"[初解重塞] 重试{retry}轮仍未清空! "
                f"剩余{sum(dropped.values()):.0f}箱, 网点{list(dropped.keys())[:10]}"
            )
    best_fitness = sum(eval_route_fitness(r) for r in best_sol)
    global_best_fitness = best_fitness
    no_improve_counter = 0
    NO_IMPROVE_LIMIT = 4000
    logging.info(f"[ALNS初始] 路线={len(best_sol)}, 成本={best_fitness:,.0f}, "
                 f"T₀={T:.0f}, 力度={base_remove_ratio*100:.0f}%, 早停阈值={NO_IMPROVE_LIMIT}")

    # ---- 3.8 ALNS 主循环 ----
    for iteration in range(max_iter):
        # 自适应破坏力度：连续卡壳 → 加倍破坏
        if stuck_counter >= STUCK_THRESHOLD:
            remove_ratio = min(0.50, base_remove_ratio * 2)
        else:
            remove_ratio = base_remove_ratio
        remove_cnt = max(2, int(len(unit_sum) * remove_ratio))

        # 选择算子组合
        d_op = select_destroy_op()
        r_op = select_repair_op(d_op)
        uses[d_op][r_op] += 1

        # 执行 destroy + repair
        destroyed, unassigned = run_destroy(best_sol, remove_cnt, d_op)
        new_sol = run_repair(destroyed, unassigned, r_op)

        new_sol = [r for r in new_sol if r['deliveries']]
        new_sol, dropped = reassign_vehicles(new_sol)
        retry_inner = 0
        while dropped:
            new_sol = run_repair(new_sol, dropped, r_op)
            new_sol, dropped = reassign_vehicles(new_sol)
            retry_inner += 1
            if retry_inner >= 500:
                raise RuntimeError(
                    f"[ALNS内环] 重试{retry_inner}轮仍未清空! "
                    f"剩余{sum(dropped.values()):.0f}箱, 网点{list(dropped.keys())[:10]}"
                )
        new_fitness = sum(eval_route_fitness(r) for r in new_sol)

        # 接受判断
        accepted = False
        if new_fitness < best_fitness:
            best_sol, best_fitness = new_sol, new_fitness
            accepted = True
            stuck_counter = 0
            if new_fitness < global_best_fitness:
                global_best_fitness = new_fitness
                no_improve_counter = 0
                scores[d_op][r_op] += SCORE_NEW_BEST
            else:
                scores[d_op][r_op] += SCORE_BETTER
        elif T > 0 and math.exp((best_fitness - new_fitness) / T) > random.random():
            best_sol, best_fitness = new_sol, new_fitness
            accepted = True
            stuck_counter = 0
            scores[d_op][r_op] += SCORE_ACCEPTED
        else:
            stuck_counter += 1

        no_improve_counter += 1

        # 早停：连续 N 步全局最优未改进
        if no_improve_counter >= NO_IMPROVE_LIMIT:
            logging.info(f"[ALNS早停] 连续{NO_IMPROVE_LIMIT}轮全局最优未改进，第{iteration}轮退出")
            break

        # 降温
        T = max(T * COOLING_RATE, T_MIN)

        # 定期更新权重
        if (iteration + 1) % WEIGHT_UPDATE_INTERVAL == 0:
            update_weights()

        # 定期深度合并优化：抵消破坏算子带来的路线碎片化
        if (iteration + 1) % 200 == 0:
            best_sol = batch_merge_routes(best_sol)
            best_sol = swap_merge_routes(best_sol)
            best_sol = dismantle_low_load_routes(best_sol, min_load_rate=0.45)
            best_fitness = sum(eval_route_fitness(r) for r in best_sol)
            if best_fitness < global_best_fitness:
                global_best_fitness = best_fitness
                no_improve_counter = 0

        # 日志输出
        if iteration % 100 == 0 or accepted:
            flag = "✓" if accepted else " "
            stuck_mark = f" ⚠卡壳{stuck_counter}" if stuck_counter >= STUCK_THRESHOLD else ""
            logging.info(f"[ALNS {iteration:4d}/{max_iter}] {flag} {d_op}/{r_op} T={T:5.0f} "
                         f"当前={new_fitness:,.0f} 最佳={best_fitness:,.0f} "
                         f"路线={len(new_sol)} 力度={remove_ratio*100:.0f}%{stuck_mark}")

        # 每 500 轮打印算子权重
        if iteration % 500 == 0 and iteration > 0:
            w_parts = []
            for d in DESTROY_OPS:
                for r in REPAIR_OPS:
                    w_parts.append(f"{d}/{r}={weights[d][r]:.2f}")
            logging.info(f"[ALNS权重] {' | '.join(w_parts)}")

    # ---- 3.9 最终整理与强化优化 ----
    # 执行全量合并优化，确保最终输出方案装载率最优
    best_sol = batch_merge_routes(best_sol)
    best_sol = swap_merge_routes(best_sol)
    best_sol = dismantle_low_load_routes(best_sol, min_load_rate=0.5)
    best_sol = _relocate_improve(best_sol, max_passes=5)

    best_sol, dropped = reassign_vehicles(best_sol)
    retry = 0
    while dropped:
        best_sol = greedy_insertion(best_sol, dropped)
        best_sol, dropped = reassign_vehicles(best_sol)
        retry += 1
        if retry % 500 == 0:
            logging.info(f"[最终兜底] 第{retry}轮, 仍有{len(dropped)}个网点, 总量{sum(dropped.values()):.0f}箱")
        if retry >= 5000:
            raise RuntimeError(
                f"[最终兜底] 重试{retry}轮仍未清空! "
                f"剩余{sum(dropped.values()):.0f}箱, 网点{list(dropped.keys())[:10]}"
            )
    best_sol = [r for r in best_sol if r['deliveries']]
    best_fitness = sum(eval_route_fitness(r) for r in best_sol)
    logging.info(f"[ALNS完成] 最终成本={best_fitness:,.0f}, 路线={len(best_sol)}, 全局最优={global_best_fitness:,.0f}")

    # ---- 需求覆盖诊断：对比输入输出 ----
    delivered = defaultdict(float)
    for r in best_sol:
        for cid, amt in r['deliveries']:
            delivered[cid] += amt
    missing = {}
    for cid, need in unit_sum.items():
        got = delivered.get(cid, 0)
        if got < need - 0.01:
            missing[cid] = (need, got, need - got)
    if missing:
        logging.error(f"[ALNS缺口] 输出与输入不匹配! {len(missing)}个网点有缺口:")
        for cid, (need, got, gap) in sorted(missing.items(), key=lambda x: x[1][2], reverse=True)[:10]:
            logging.error(f"  网点{cid}: 需求{need:.0f}箱 → 实际{got:.0f}箱, 缺口{gap:.0f}箱")
    else:
        logging.info(f"[ALNS缺口] 全部满足! 输出={sum(delivered.values()):.0f}箱 = 输入={sum(unit_sum.values()):.0f}箱")

    # ---- 同节点交付合并：修复 Phase 2/repair 造成的需求拆分 ----
    # 对出现在多条路线中的节点，尝试把量合并到"该节点量最大"的那条路线
    merged_count = 0
    for cid in list(delivered.keys()):
        # 找到所有含此节点的路线及其量（同路线多条目合并求和）
        route_entries = []  # [(ridx, sum_amt_for_this_cid, total_load_of_route)]
        seen_routes = set()
        for ri, r in enumerate(best_sol):
            if ri in seen_routes:
                continue
            cid_amt = sum(a for c, a in r['deliveries'] if c == cid)
            if cid_amt > 0:
                total_load = sum(amt for _, amt in r['deliveries'])
                route_entries.append((ri, cid_amt, total_load))
                seen_routes.add(ri)
        if len(route_entries) <= 1:
            continue

        # 按该节点量降序：量最大的作为合并目标
        route_entries.sort(key=lambda x: x[1], reverse=True)
        main_ri, main_amt, main_total = route_entries[0]

        for other_ri, other_amt, other_total in route_entries[1:]:
            main_route = best_sol[main_ri]
            other_route = best_sol[other_ri]
            main_cap = _route_cap(main_route)
            new_main_total = sum(amt for _, amt in main_route['deliveries']) + other_amt

            if new_main_total <= main_cap:
                # 合并到主路线
                for i, (c, a) in enumerate(main_route['deliveries']):
                    if c == cid:
                        main_route['deliveries'][i] = (c, a + other_amt)
                        break
                # 从另一路线删除
                other_route['deliveries'] = [(c, a) for c, a in other_route['deliveries'] if c != cid]
                merged_count += 1
            else:
                # 容量不够，记录原因
                logging.warning(f"[交付合并] 网点{cid}: 路线{other_ri}({other_amt:.0f}箱) → 路线{main_ri} "
                               f"失败(容量={main_cap:.0f}, 合并后={new_main_total:.0f}, "
                               f"当前载量={new_main_total - other_amt:.0f})")

    if merged_count > 0:
        # 删除被掏空的路线 + 重新统计
        best_sol = [r for r in best_sol if r['deliveries']]
        delivered2 = defaultdict(float)
        for r in best_sol:
            for cid, amt in r['deliveries']:
                delivered2[cid] += amt
        split_nodes = sum(1 for cid in delivered2 if sum(1 for r in best_sol if any(c == cid for c, _ in r['deliveries'])) >= 2)
        logging.info(f"[交付合并] 成功合并{merged_count}条, 路线={len(best_sol)}, "
                     f"仍拆分={split_nodes}个网点")
    else:
        logging.info(f"[交付合并] ✓ 无拆分需求可合并")
    # ====================================================================

    '''4. 整数线性规划 Stage 2: 日期精确排程 (引入网点时间窗离散惩罚)'''
    num_routes = len(best_sol)
    if num_routes == 0:
        return []

    # ---- 诊断日志：各车型路线数 vs 日配额 ----
    type_count = defaultdict(int)
    for r in best_sol:
        type_count[int(r.get('vehicle_type', 1))] += 1
    log_parts = []
    for v in range(VeTypeNum):
        vt = v + 1
        cnt = type_count.get(vt, 0)
        if daily_vehicle_limits:
            daily = [daily_vehicle_limits[d].get(vt, 0) for d in range(DelivDay)]
            monthly = sum(daily)
            log_parts.append(f"车型{vt}: {cnt}条路线, 日配额={daily}, 月配额={monthly}")
        else:
            daily = VNums[v]
            monthly = daily * DelivDay
            log_parts.append(f"车型{vt}: {cnt}条路线, 日配额={daily}, 月配额={monthly}")
    logging.info(f"[ILP诊断] 共{num_routes}条路线, DelivDay={DelivDay}天 | " + " | ".join(log_parts))
    # ---- 诊断结束 ----

    prob2 = pulp.LpProblem("Minimize_Peak_Daily_And_Clustering", pulp.LpMinimize)
    x = pulp.LpVariable.dicts("x", [(r, d) for r in range(num_routes) for d in range(DelivDay)], cat='Binary')
    Z = pulp.LpVariable("Peak_Volume", lowBound=0, cat='Continuous')

    ALPHA = 1000000  # 聚类惩罚权重（拉开同网点配送间隔）
    BETA =500   # 缺货优先级惩罚权重（只罚代表路线，不绑架其余路线）

    # ---- 构建网点→路线映射 & 计算网点级数据 ----
    node_routes_ilp = defaultdict(list)  # {cid: [route_idx, ...]}
    node_load_ilp = defaultdict(float)   # {cid: 该网点在各路线中的总箱数}
    for r in range(num_routes):
        for cid, amt in best_sol[r]['deliveries']:
            node_routes_ilp[cid].append(r)
            node_load_ilp[cid] += amt

    # ---- 筛选有缺货优先级的网点 ----
    priority_nodes = {}  # {cid: priority}
    if node_priority is not None:
        for cid in node_routes_ilp:
            if cid < len(node_priority) and node_priority[cid] > 0:
                priority_nodes[cid] = node_priority[cid]

    has_priority = len(priority_nodes) > 0

    # ---- 每个缺货网点选一条"代表路线"（载量最小），只惩罚它的配送日 ----
    # 其余路线不参与优先级惩罚，由 ALPHA 聚类惩罚自然拉开
    priority_route_info = {}  # {cid: (rep_route_idx, rep_load)}
    if has_priority:
        for cid in priority_nodes:
            r_list = node_routes_ilp[cid]
            rep_r = min(r_list, key=lambda r: sum(a for _, a in best_sol[r]['deliveries']))
            rep_load = sum(a for _, a in best_sol[rep_r]['deliveries'])
            priority_route_info[cid] = (rep_r, rep_load)

    # ---- 同网点聚类惩罚（分层累进：路线越集中在同一窗口罚得越重） ----
    penalties = []
    for cid, r_list in node_routes_ilp.items():
        freq = len(r_list)
        if freq <= 1:
            continue

        window_size = min(max(0, (DelivDay // freq) - 1), 5)

        for d in range(DelivDay - window_size):
            routes_in_window = pulp.lpSum(x[r, d + i] for r in r_list for i in range(window_size + 1))
            # 分层惩罚：p2≥routes-1, p3≥routes-2, p4≥routes-3, ...
            # n条路线的总惩罚 = ALPHA × (1+2+...+(n-1)) = ALPHA × n(n-1)/2
            for tier in range(2, freq + 1):
                pk = pulp.LpVariable(f"Pen_N{cid}_D{d}_T{tier}", lowBound=0, cat='Continuous')
                prob2 += pk >= routes_in_window - (tier - 1)
                penalties.append(pk)

    # ---- 网点首次配送日变量与约束（只绑代表路线） ----
    # earliest[cid] = 代表路线的配送日（载量最小的那条）
    # BETA 只推这一条路线 → 其余路线靠 ALPHA 聚类惩罚自然拉开
    earliest = {}
    if has_priority:
        for cid in priority_nodes:
            earliest[cid] = pulp.LpVariable(f"Earliest_Node_{cid}",
                                            lowBound=0, upBound=DelivDay - 1, cat='Continuous')
            rep_r, _ = priority_route_info[cid]
            for d in range(DelivDay):
                prob2 += earliest[cid] >= d - DelivDay * (1 - x[rep_r, d])

    # ---- 目标函数：峰值最小化 + 聚类惩罚 + 优先级逆序惩罚 ----
    obj_terms = [Z]
    if penalties:
        obj_terms.append(ALPHA * pulp.lpSum(penalties))
    if has_priority:
        # 只惩罚代表路线（载量最小的那条），避免全量网点箱数绑架BETA
        # 缺货概率 × 首次配送日 × 代表路线载量
        priority_pen = pulp.lpSum(
            priority_nodes[cid] * earliest[cid] * priority_route_info[cid][1]
            for cid in priority_nodes
        )
        obj_terms.append(BETA * priority_pen)
        high_pri_nodes = sum(1 for cid in priority_nodes if priority_nodes[cid] > 0.5)
        max_pri = max(priority_nodes.values())
        avg_pri = sum(priority_nodes.values()) / len(priority_nodes)
        total_rep_load = sum(v[1] for v in priority_route_info.values())
        logging.info(f"[ILP首次配送] 缺货概率>0.5的网点={high_pri_nodes}/{len(priority_nodes)}, "
                     f"BETA={BETA}, 代表路线总载量={total_rep_load:.0f}箱, "
                     f"最大优先级={max_pri:.4f}, 平均={avg_pri:.4f}")
    prob2 += pulp.lpSum(obj_terms)

    for r in range(num_routes):
        prob2 += pulp.lpSum(x[r, d] for d in range(DelivDay)) == 1

    for d in range(DelivDay):
        for v in range(VeTypeNum):
            v_type = v + 1
            type_routes = [r for r in range(num_routes) if int(best_sol[r].get('vehicle_type', 1)) == v_type]
            if type_routes:
                daily_limit = daily_vehicle_limits.get(d, {}).get(v_type, VNums[v]) if daily_vehicle_limits else VNums[v]
                prob2 += pulp.lpSum(x[r, d] for r in type_routes) <= daily_limit

    # 硬约束：禁止同网点同日多路线（同一网点每天最多1条路线）
    for cid, r_list in node_routes_ilp.items():
        unique_routes = list(set(r_list))
        if len(unique_routes) > 1:
            for d in range(DelivDay):
                prob2 += pulp.lpSum(x[r, d] for r in unique_routes) <= 1

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

    # ---- ILP 后诊断：高优先级网点的首次配送日期 ----
    if has_priority:
        logging.info("=" * 70)
        logging.info(f"[首次配送诊断] 缺货优先级>0的网点共{len(priority_nodes)}个:")
        # 按优先级降序排列
        sorted_pri = sorted(priority_nodes.items(), key=lambda x: -x[1])
        logging.info(f"{'排名':<4} {'网点':>6} {'缺货概率':>8} {'网点总箱数':>10} {'首次配送日':>10} {'路线数':>6}")
        logging.info("-" * 70)
        day_dist = defaultdict(int)
        for rank, (cid, pri) in enumerate(sorted_pri, 1):
            e_val = earliest[cid].varValue if earliest[cid].varValue is not None else -1
            day_dist[int(e_val)] += 1
            n_routes = len(set(node_routes_ilp[cid]))  # 去重：同路线多条目只算一次
            logging.info(f"{rank:<4} {cid:>6} {pri:>8.4f} {node_load_ilp[cid]:>10.0f} "
                         f"{'D'+str(int(e_val)):>9} {n_routes:>6}")
        logging.info("-" * 70)
        early_nodes = sum(1 for cid in priority_nodes
                          if earliest[cid].varValue is not None and earliest[cid].varValue <= DelivDay * 0.25)
        late_nodes = sum(1 for cid in priority_nodes
                         if earliest[cid].varValue is not None and earliest[cid].varValue >= DelivDay * 0.75)
        logging.info(f"月初25%天内的首次配送: {early_nodes}个网点, "
                     f"月末25%天内的首次配送: {late_nodes}个网点")
        if priority_nodes:
            avg_first_day = sum(earliest[cid].varValue for cid in priority_nodes
                                if earliest[cid].varValue is not None) / len(priority_nodes)
            logging.info(f"加权平均首次配送日: {avg_first_day:.2f} / {DelivDay-1}天")
        logging.info("=" * 70)

    # ---- 每日车辆配额使用率诊断 ----
    logging.info("=" * 70)
    logging.info(f"[每日配额使用率] 共{DelivDay}天, {num_routes}条路线:")
    for d in range(DelivDay):
        parts = []
        for v in range(VeTypeNum):
            v_type = v + 1
            type_routes = [r for r in range(num_routes) if int(best_sol[r].get('vehicle_type', 1)) == v_type]
            daily_limit = daily_vehicle_limits.get(d, {}).get(v_type, VNums[v]) if daily_vehicle_limits else VNums[v]
            scheduled = int(sum(1 for r in type_routes if x[r, d].varValue and x[r, d].varValue >= 0.5))
            if daily_limit > 0:
                pct = scheduled / daily_limit * 100
                mark = " ←满!" if scheduled >= daily_limit else ""
                parts.append(f"车型{v_type}:{scheduled}/{daily_limit}({pct:.0f}%){mark}")
            elif scheduled > 0:
                parts.append(f"车型{v_type}:{scheduled}/0 ⚠超配额!")
        if parts:
            logging.info(f"  D{d:>2}: " + " | ".join(parts))
    logging.info("=" * 70)

    # ---- 小需求拆分诊断：箱数少却被拆成多路线的网点 ----
    fragmented = []
    for cid, r_list in node_routes_ilp.items():
        total = node_load_ilp[cid]
        unique_routes = list(set(r_list))  # 去重：同一条路线同一网点出现两次只算一次
        n = len(unique_routes)
        if total < 100 and n >= 2:
            route_details = []
            for r in unique_routes:
                day = best_sol[r]['schedule_day_idx']
                # 该路线中此网点的总箱数（可能跨多个delivery条目）
                load = sum(a for c, a in best_sol[r]['deliveries'] if c == cid)
                other_nodes = list(set(c for c, _ in best_sol[r]['deliveries'] if c != cid))
                route_details.append((r, day, load, other_nodes))
            fragmented.append((cid, total, n, route_details))
    if fragmented:
        logging.warning(f"[小需求拆分] {len(fragmented)}个网点(总需求<100箱)被拆成≥2条路线:")
        for cid, total, n, route_details in sorted(fragmented, key=lambda x: x[1]):
            logging.warning(f"  网点{cid}: 总{total:.0f}箱 → {n}条路线:")
            for r, day, load, others in route_details:
                other_str = f" 拼网点{others}" if others else " 独立"
                logging.warning(f"    路线{r} D{day}: {load:.0f}箱{other_str}")
    else:
        logging.info(f"[小需求拆分] ✓ 无小需求被拆分")
    # ========================================================================

    # ---- 同网点同日/连续日配送检查 ----
    bad_nodes = []
    for cid, r_list in node_routes_ilp.items():
        unique_routes = set(r_list)
        if len(unique_routes) <= 1:
            continue
        days = sorted(int(best_sol[r]['schedule_day_idx']) for r in unique_routes)
        # 检查同日多路线
        same_day = any(days.count(d) > 1 for d in set(days))
        # 检查连续日
        consecutive = any(days[i+1] - days[i] == 1 for i in range(len(days) - 1))
        if same_day or consecutive:
            bad_nodes.append((cid, days, len(unique_routes), same_day, consecutive))
    if bad_nodes:
        total_nodes = len(node_routes_ilp)
        logging.warning(f"[配送间隔] {len(bad_nodes)}/{total_nodes} 个网点存在同日或连续日配送:")
        same_count = sum(1 for _, _, _, s, _ in bad_nodes if s)
        cons_count = sum(1 for _, _, _, _, c in bad_nodes if c)
        logging.warning(f"  同日多路线: {same_count}个 | 连续工作日: {cons_count}个")
        bad_nodes.sort(key=lambda x: -x[2])
        for cid, days, n_routes, same, cons in bad_nodes[:10]:
            tags = []
            if same: tags.append("同日")
            if cons: tags.append("连续")
            logging.warning(f"  网点{cid}: {n_routes}条路线, 配送日={days} [{','.join(tags)}]")
        if len(bad_nodes) > 10:
            logging.warning(f"  ... 共{len(bad_nodes)}个网点")
    else:
        logging.info(f"[配送间隔] ✓ 所有网点配送日均已拉开，无同日或连续工作日配送")
    # ========================================================================

    return best_sol