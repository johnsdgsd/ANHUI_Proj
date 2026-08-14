"""
Stage 1 求解器：路径枚举 + 统一 MIP（两遍字典序）+ FTL 预处理 + 结果合并

业务参数不在本文件头部——由 main 通过 data_loader.get_params() 读取后以 params
参数传入各函数；本文件头部仅保留算法超参数。
"""
import copy
import math
import os
import time
from collections import defaultdict

try:
    from ortools.sat.python import cp_model
    HAS_ORTOOLS = True
except ImportError:
    HAS_ORTOOLS = False

# ====== 算法超参数 ======
# ---- Pass 2 时限分级(用户 2026-08-05):按 MIP 路线数自动分档,替换固定超参 ----
# 小算例(路线数 ≤ ROUTE_CAP_SMALL)≤60s;中算例(≤ ROUTE_CAP_MEDIUM)≤200s;
# 大算例(其余,如正式 87 网点 RPS=1 后 ~4500 条)400s(用户 2026-08-10: 600→400 提速)。
SOLVER_TIME_LIMIT_SMALL = 60
SOLVER_TIME_LIMIT_MEDIUM = 200
SOLVER_TIME_LIMIT_LARGE = 400
ROUTE_CAP_SMALL = 800          # MIP 路线数 ≤ 此值 → 小算例
ROUTE_CAP_MEDIUM = 3000        # MIP 路线数 ≤ 此值 → 中算例; 其余 → 大算例
CONSTRUCT_PASS2_TIME = 400     # 构造热启动生效时的 Pass 2 时间(与 LARGE 同步,用户 2026-08-10: 600→400)
COST_SCALE = 10000             # 目标函数整数化精度
DEMAND_FILTER_MIN_LOAD = 225   # 需求预过滤：路径停靠点总需求最低阈值（小车450×50%）
FEASIBILITY_CHECK_TIME = 120   # 装载率档位探测求解时限（秒）
MAX_ROUTES_PER_SET = 1         # reduce_routes：每无序停靠集最多保留路径数
MAX_ROUTES_AUTO_CAP = 2500     # 自适应 reduce：RPS=3 后路线数 ≤ 此值才采用 3(否则降回 1)
NUM_SEARCH_WORKERS = min(8, os.cpu_count() or 1)   # CP-SAT并行搜索线程数(用户 2026-08-09: 4→8,核数不足自动降)
RATE_EPSILON = 0.001           # 装载率/占比浮点比较容差（validate 用）
DIST_EPSILON = 0.1             # 距离浮点比较容差（validate 用）

def _grade_pass2_time(n_mip_routes, construction=False):
    """Pass 2 时限分级（用户 2026-08-05）：按 MIP 路线数自动分档，替换固定超参。

    路线数是 MIP 规模的主要决定因素（变量/约束数随路线数线性增长）：
    - 小算例（≤ ROUTE_CAP_SMALL 条）：60s —— 单日/小规模算例足够
    - 中算例（≤ ROUTE_CAP_MEDIUM 条）：200s
    - 大算例（其余，如正式 87 网点 RPS=1 后 ~4500 条）：400s（用户 2026-08-10: 600→400 提速）
    construction=True（构造热启动把优先路线硬固定后，Pass 2 仍需完成全网络解，
    1 车队紧凑拼装是大算例难点）：大算例在分档基础上直接给满 CONSTRUCT_PASS2_TIME（400s）。
    """
    if n_mip_routes <= ROUTE_CAP_SMALL:
        base = SOLVER_TIME_LIMIT_SMALL
    elif n_mip_routes <= ROUTE_CAP_MEDIUM:
        base = SOLVER_TIME_LIMIT_MEDIUM
    else:
        base = SOLVER_TIME_LIMIT_LARGE
    if construction and n_mip_routes > ROUTE_CAP_MEDIUM:
        base = max(base, CONSTRUCT_PASS2_TIME)
    return base

def validate(solution, data, params):
    """Stage-1 路径划分验证（main 调用）：打印摘要 + 全部约束合规校验。

    与 scheduler.validate（Stage-2）对应；返回的 (viol, checks) 供
    reporter.save_schedule 输出到合并 HTML 的校验段。

    Returns:
        (viol, checks): 违规明细列表 + 各约束检查项描述
    """
    print(f"\n{'='*60}")
    print(f"  配送优化结果摘要")
    print(f"{'='*60}")
    print(f"  总成本: {solution['total_cost']:,.2f} 元")
    print(f"  配送任务数: {len(solution['routes'])}")
    print(f"  求解状态: {solution['solver_status']}")
    print(f"  求解时间: {solution['solve_time']:.1f}s")

    # 运输队信息
    if solution.get('teams_activated', 0) > 0:
        print(f"  运输队: {solution['teams_activated']}/{solution.get('total_teams', '?')} activated")
        team_routes = {}
        for sr in solution['routes']:
            t = sr.get('team') or 0
            if t > 0:
                team_routes[t] = team_routes.get(t, 0) + 1
        for t in sorted(team_routes.keys()):
            print(f"    运输队 {t}: {team_routes[t]} routes")

    branches = data['branches']
    active = [c for c, b in branches.items() if b['has_demand']]
    total_demand = sum(branches[c]['demand'] for c in active)
    print(f"  需求满足: {solution['total_boxes']}/{total_demand} 箱")

    vehicles = data['params']['vehicles']
    for k_idx, v in enumerate(vehicles):
        used = solution['vehicle_usage'].get(k_idx, 0)
        print(f"  {v['name']}: {used}/{v['max_trips']} 次")

    viol = []          # 违规明细
    checks = []        # 各约束检查项描述（"..." 通过 / "存在..." 违规）
    priority_tasks = solution.get('priority_tasks', {})

    # 装载率 / A_B 比例 / 距离（所有路线统一校验）
    load_viol = 0
    for sr in solution['routes']:
        cat = sr['category']
        load_min = params['LOAD_RATE_MIN_A_ONLY'] if cat == 'A_ONLY' else params['LOAD_RATE_MIN_ANY']
        if sr['load_rate'] < load_min - RATE_EPSILON:
            load_viol += 1
            viol.append(f"route {sr['id']} ({cat}) 装载率 {sr['load_rate']:.2%} < {load_min:.0%}")
        if sr['category'] == 'A_B' and sr['a_rate'] > params['A_B_A_RATE_MAX'] + RATE_EPSILON:
            viol.append(f"route {sr['id']} A占比 {sr['a_rate']:.2%} > {params['A_B_A_RATE_MAX']:.0%}")
        if sr['dist_km'] > params['DIST_MAX_KM'] + DIST_EPSILON:
            viol.append(f"route {sr['id']} 距离 {sr['dist_km']:.1f}km > {params['DIST_MAX_KM']}")
    checks.append("全部路线装载率/A占比/距离符合约束" if load_viol == 0 else f"{load_viol} 处路线违规")

    # 每车队运量 ≤ 3000
    team_vol = defaultdict(int)
    for sr in solution['routes']:
        t = sr.get('team') or 0
        if t > 0:
            team_vol[t] += sr['total_load']
    team_viol = [t for t in sorted(team_vol) if team_vol[t] > params['TEAM_CAP_BOXES'] + 1]
    for t in team_viol:
        viol.append(f"车队 {t} 运量 {team_vol[t]} > {params['TEAM_CAP_BOXES']}")
    checks.append(f"每车队运量 ≤ {params['TEAM_CAP_BOXES']} 箱" if not team_viol else "存在超上限车队")

    # 车队饱和 + 标注（前 n-1 队）
    sat = solution.get('teams_saturated')
    n_t = solution.get('teams_activated', 0)
    if n_t >= 2:
        if sat is False:
            viol.append(f"前 {n_t-1} 车队未饱和(某队仍可再装优先路线)")
        checks.append(f"车队 1..{n_t-1} 饱和（无法再接受优先路线）" if sat is True else f"前 {n_t-1} 车队饱和性未满足")
        bad_team_route = [sr['id'] for sr in solution['routes']
                          if 1 <= (sr.get('team') or 0) <= n_t - 1 and sr.get('priority_boxes', 0) <= 0]
        for rid in bad_team_route:
            viol.append(f"车队路线 {rid} 不含优先量")
        checks.append(f"前 {n_t-1} 队所有路线均承载优先量" if not bad_team_route else "存在不含优先量的车队路线")

    # C11 优先覆盖（每个优先任务配送量足够、承运路线不超拆分上界）
    if priority_tasks:
        c11_viol = 0
        for pcode, pqty in priority_tasks.items():
            routes_with = [sr for sr in solution['routes'] if pcode in sr['stops']]
            name = branches.get(pcode, {}).get('name', pcode)
            total_delivered = sum(sr['boxes'].get(pcode, 0) for sr in routes_with)
            if total_delivered < pqty:
                c11_viol += 1
                viol.append(f"C11a: 优先 {pcode}({name}) 配送 {total_delivered} < 优先量 {pqty}")
            parts = [size for _t, _c, size in
                     expand_priority_tasks({pcode: pqty}, params['FTL_CAPACITY'])]
            min_part = min(parts)
            carriers = sum(1 for sr in solution['routes']
                           if sr['boxes'].get(pcode, 0) >= min_part)
            max_possible = math.ceil(total_delivered / min_part) if total_delivered > 0 else 0
            if carriers > max_possible:
                c11_viol += 1
                viol.append(f"C11b: 优先 {pcode}({name}) 承运路线 {carriers} > 上限 {max_possible}")
        checks.append("优先任务全部按量配送且未过度拆分(C11a/C11b)" if c11_viol == 0 else f"{c11_viol} 处优先覆盖违规")

    for v in viol:
        print(f"  VIOLATION: {v}")
    if not viol:
        print(f"  ✓ All constraints satisfied")
    print(f"{'='*60}")
    return viol, checks

def filter_by_demand_feasibility(routes, branches, min_load=225, protect_codes=None):
    """需求感知预过滤：剔除停靠点总需求不足的路径。

    如果一条路径服务的所有网点需求之和 < min_load，则该路径在任何车型下
    都无法满足最低装载要求，不可能被 MIP 选中，直接剔除。

    protect_codes: 受保护网点集合——含这些网点的路线一律保留（优先网点
    可能有很小需求，但其路线是优先子任务唯一的承载，滤除会导致 0==1 不可行）。
    """
    original = list(routes)   # 过滤前快照,供"无路线网点"恢复
    filtered = []
    removed = 0
    for r in routes:
        if protect_codes and any(s in protect_codes for s in r['stops']):
            filtered.append(r)
            continue
        total_demand = sum(branches[s]['demand'] for s in r['stops'])
        if total_demand >= min_load:
            filtered.append(r)
        else:
            removed += 1
    # 保护(2026-08-05):每个有需求网点至少保留一条候选路线。
    # 否则过滤可能删光某网点全部路线 → 需求约束 (a) 对无路线网点 continue 跳过
    # → 需求静默丢失但 Stage-1 仍 FEASIBLE(实验 2 霍山县 34 箱即此)。
    active = [c for c, b in branches.items() if b.get('has_demand') and b.get('demand', 0) > 0]
    for c in active:
        if any(c in r['stops'] for r in filtered):
            continue
        candidates = [r for r in original if c in r['stops']]
        if not candidates:
            print(f"  WARNING: 网点 {c} 无任何候选路线(枚举缺失),需求将无法配送")
            continue
        # 优先单停路线:拼车恢复可能引入组排序约束矛盾
        # (前站需求小却要求 ≥ 后站大需求,如霍山34拼车路线 presolve #10656)。
        single = [r for r in candidates if len(r['stops']) == 1]
        pool = single if single else candidates
        best = max(pool, key=lambda r: sum(branches[s]['demand'] for s in r['stops']))
        filtered.append(best)
        removed -= 1
    if removed > 0:
        print(f"  Demand filter: removed {removed} routes (total stop demand < {min_load} boxes)")
    return filtered

def reduce_routes(routes, max_per_set=MAX_ROUTES_PER_SET):
    """Keep only the cheapest max_per_set orderings per unordered stop set."""
    groups = defaultdict(list)
    for r in routes:
        key = tuple(sorted(r['stops']))
        cost_metric = sum(r['cost_coeffs'].values())
        groups[key].append((cost_metric, r))

    reduced = []
    for key, items in groups.items():
        items.sort(key=lambda x: x[0])
        for _, r in items[:max_per_set]:
            reduced.append(r)

    for idx, r in enumerate(reduced):
        r['route_id'] = idx

    return reduced

def expand_priority_tasks(priority_tasks, capacity):
    """将优先任务按车辆容量拆分为不可拆分的子任务。

    优先量 pqty >= capacity 时拆为 k 个满载部分(size == capacity) + 一个
    余量部分(< capacity),每个部分作为一个独立的 0-1 子任务。满载部分在
    容量上不可能与其他子任务拼车,因此它天然对应单站直达路线。

    Returns:
        list of (tid, code, size), tid = (code, part_index)
    """
    tasks = []
    for code, pqty in priority_tasks.items():
        n_full, rem = divmod(pqty, capacity)
        for p in range(n_full):
            tasks.append(((code, p), code, capacity))
        if rem > 0:
            tasks.append(((code, n_full), code, rem))
    return tasks

def _ensure_full_load_routes(routes, routes_before, priority_tasks, cap_big):
    """确保每个满载子任务(size == 大车容量)都有独立的单站直达路线。

    枚举器每网点只生成 1 条单站路线，而一个网点可能有多个满载子任务
    (如 pqty=2142 → 2 个满载部分)。从原始路线 deepcopy 克隆补足，
    使每个满载子任务都有容量可行的独立路线。克隆仅存在于本函数返回的
    routes 列表中，不修改共享的 routes_before。
    """
    if not priority_tasks:
        return routes
    full_counts = defaultdict(int)
    for _tid, code, size in expand_priority_tasks(priority_tasks, cap_big):
        if size == cap_big:
            full_counts[code] += 1
    if not full_counts:
        return routes

    next_id = max((r['route_id'] for r in routes), default=-1) + 1
    for code, k in full_counts.items():
        single = [r for r in routes if r['stops'] == [code]]
        need = max(0, k - len(single))
        if need == 0:
            continue
        orig = routes_before.get((code,))
        if orig is None and single:
            orig = single[0]
        for _ in range(need):
            if orig is None:
                print(f"  WARNING: full-load part for {code} has no single-stop "
                      f"route — priority guarantee may be dropped")
                break
            new_r = copy.deepcopy(orig)
            new_r['route_id'] = next_id
            routes.append(new_r)
            next_id += 1
    return routes

def _team_volume_lb(params, subtasks, routes, branches, vehicles, hard_target):
    """车队运量下界(快速预检):优先总量 + 无法与其它优先子任务拼装的子任务
    单独成路线所需的最少拼车普通量。

    用途:若 3000×N < 该下界则 N 车队必不可行,免去慢速 MIP 不可行证明。
    例:1 车队下 阜阳城郊 532 无法与任何优先子任务拼装(其余优先网点同向角不符
    无路线 / 利辛容量超限),单独路线需 ≥720(中车80%),下界 2841+188=3029 > 3000。
    保守(低估)构造:可拼装者记 0 普通,故结果是真值的下界,不会误判可行为不可行。
    """
    cap = [v['capacity'] for v in vehicles]
    cap_big = cap[0]
    pri_branches = {c for _t, c, _s in subtasks}
    # 优先网点对是否共现于某枚举路线(能拼装的前提)
    cooccur = {c: set() for c in pri_branches}
    for r in routes:
        stops = r['stops']
        for i in range(len(stops)):
            a = stops[i]
            if a not in pri_branches:
                continue
            for j in range(i + 1, len(stops)):
                b = stops[j]
                if b in pri_branches:
                    cooccur[a].add(b)
                    cooccur[b].add(a)
    hard = hard_target if hard_target is not None else params['LOAD_RATE_TARGET_ANY_HARD_80']
    extra = 0
    for _tid, c, size in subtasks:
        if size >= cap_big:
            continue  # 满载大车单独装
        can_pack = any(
            (c2 in cooccur[c] and size + size2 <= cap_big)
            for _t2, c2, size2 in subtasks if c2 != c)
        if not can_pack:
            fit = min(kc for kc in cap if kc >= size)   # 最小可装车型容量
            need = math.ceil(hard * fit)
            extra += max(0, need - size)
    total = sum(size for _t, _c, size in subtasks)
    return total + extra

def _team_lower_bound(params, subtasks, team_mix, vehicles, max_teams=18,
                      routes=None, branches=None, hard_target=None):
    """车队下界:满足"车队池可承载优先子任务"的最小车队数。

    size > 900 的子任务只能用大车(>中车容量),每个独占一辆大车;
    其余优先量需车队池总容量覆盖(每队车一车一趟);
    另受 params['TEAM_CAP_BOXES'](3000箱/队)日运量上限约束:车队数 ≥ ceil(优先总量/3000);
    有 routes 时再叠加运量结构下界(_team_volume_lb,含不可拼装子任务所需普通量),
    如当前实例 阜阳532 无法拼装 → 下界 ≥3029 → 车队数 ≥ 2,跳过 1 车队慢速探测。
    """
    cap = [v['capacity'] for v in vehicles]
    big_needed = sum(1 for _t, _c, size in subtasks if size > 900)
    total_boxes = sum(size for _t, _c, size in subtasks)
    cap_lb = max(1, math.ceil(total_boxes / params['TEAM_CAP_BOXES']))
    if routes is not None:
        vol_lb = _team_volume_lb(params, subtasks, routes, branches, vehicles, hard_target)
        cap_lb = max(cap_lb, math.ceil(vol_lb / params['TEAM_CAP_BOXES']))
    for N in range(cap_lb, max_teams + 1):
        pool = [team_mix[k] * N for k in range(3)]
        if pool[0] < big_needed:
            continue
        avail = pool[0] * cap[0] + pool[1] * cap[1] + pool[2] * cap[2]
        if avail >= total_boxes:
            return N
    return cap_lb

def _construct_priority_packing(params, subtasks, routes, branches, vehicles, team_mix,
                                n_teams, hard_target):
    """贪心构造 N 车队优先拼装方案(热启动),N≥1。

    解决"1 车队可行但 CP-SAT 在 4902 条路线里找不到紧凑拼装"的问题:
    1) 优先子任务按量降序,尽量共载到少数路线(路线须含该网点、容量够、停靠总需求够 80%);
    2) 回溯把路线分给 N 车队(先填前队),车型可选小→中→大(槽位不足时升级车型以保槽位),
       每队运量 ≤ params['TEAM_CAP_BOXES']。
    成功返回 (plan, team_vols):plan={rid: (vehicle_k, [(tid,br,size),...], team_idx)};
    失败返回 None(贪心未找到,仍需 MIP 探测)。
    """
    cap = [v['capacity'] for v in vehicles]
    cap_big = cap[0]
    pri_branches = {c for _t, c, _s in subtasks}
    route_by_id = {r['route_id']: r for r in routes}
    # 含各优先网点的候选路线
    cand = defaultdict(list)
    for r in routes:
        if any(s in pri_branches for s in r['stops']):
            for s in r['stops']:
                if s in pri_branches:
                    cand[s].append(r)
    remaining = set(subtasks)
    rid_sub = defaultdict(list)  # rid -> [(tid, branch, size)]

    # ---- 1) 贪心共载 ----
    # 评分:① 共载其它未放置优先网点最多;② 优先复用已选路线(避免摊成多条小路线);
    #     ③ 车型越小越好(装载越低)。
    for tid, br, size in sorted(subtasks, key=lambda x: -x[2]):
        best = None
        for r in cand.get(br, []):
            rid = r['route_id']
            # 满载子任务(尺寸==大车容量)MIP 限单站直达(h 约束),构造不得选多站路线
            if size == cap_big and len(r['stops']) != 1:
                continue
            cur = rid_sub.get(rid, [])
            total = sum(sz for _t, _b, sz in cur) + size
            if total > cap_big:
                continue
            # 上限检查(2026-08-05):激活路线每个停靠点强制 ≥ min(MIN_BOXES, 需求),
            # 优先量取 max。强制最低装载必须能塞进某车型,否则硬固定后必然无解
            # (例:900 优先 + 拼载网点 100 > 中车 900 → 2 车队被误判不可行)。
            prio = {s: 0 for s in r['stops']}
            for _t, b, sz in cur:
                prio[b] = prio.get(b, 0) + sz
            prio[br] = prio.get(br, 0) + size
            mb = params['MIN_BOXES_PER_STOP']
            mandatory = sum(max(prio[s], min(mb, branches[s]['demand']))
                            for s in r['stops'])
            if mandatory > cap_big:
                continue
            need_cap = min(c for c in cap if c >= max(total, mandatory))
            stop_demand = sum(branches[s]['demand'] for s in r['stops'])
            if stop_demand < max(math.ceil(hard_target * need_cap), total):
                continue
            n_copack = sum(1 for _t2, c2, _s2 in remaining
                           if c2 != br and c2 in r['stops'])
            used = 1 if rid in rid_sub else 0
            score = (n_copack, used, -need_cap)
            if best is None or score > best[0]:
                best = (score, rid)
        if best is None:
            return None
        rid_sub[best[1]].append((tid, br, size))
        remaining.discard((tid, br, size))

    # ---- 2) 回溯分配 N 车队(含车型升级) ----
    items = []
    for rid, subs in rid_sub.items():
        P = sum(sz for _t, _b, sz in subs)
        prio = {s: 0 for s in route_by_id[rid]['stops']}
        for _t, b, sz in subs:
            prio[b] = prio.get(b, 0) + sz
        mb = params['MIN_BOXES_PER_STOP']
        mandatory = sum(max(prio[s], min(mb, branches[s]['demand']))
                        for s in route_by_id[rid]['stops'])
        stop_demand = sum(branches[s]['demand'] for s in route_by_id[rid]['stops'])
        # 车型必须装得下"优先量 P 和 强制最低装载 mandatory"两者
        ks = [k for k, c in enumerate(cap)
              if c >= max(P, mandatory)
              and stop_demand >= max(math.ceil(hard_target * c), P)]
        items.append({'rid': rid, 'P': P, 'subs': subs, 'ks': ks})
    items.sort(key=lambda it: -it['P'])
    team_slots = [[team_mix[k] for k in range(len(vehicles))] for _ in range(n_teams)]
    team_vol = [0] * n_teams
    plan = {}
    _bt_cnt = [0]
    MAX_BT = 50000          # 回溯迭代上限,防止 N 大时指数爆炸;超限视为构造失败回退探测

    def bt(idx):
        _bt_cnt[0] += 1
        if _bt_cnt[0] > MAX_BT:
            return False
        if idx >= len(items):
            return True
        it = items[idx]
        for k in it['ks']:
            load = max(math.ceil(hard_target * cap[k]), it['P'])
            for t in range(n_teams):
                if team_slots[t][k] <= 0 or team_vol[t] + load > params['TEAM_CAP_BOXES']:
                    continue
                team_slots[t][k] -= 1
                team_vol[t] += load
                plan[it['rid']] = (k, it['subs'], t)
                if bt(idx + 1):
                    return True
                team_slots[t][k] += 1
                team_vol[t] -= load
                del plan[it['rid']]
        return False

    if not bt(0):
        return None
    return plan, team_vol

def _build_unified_model(params, data, routes, priority_tasks, n_teams_max, team_mix,
                         enforce_load_rate, load_rate_hard_target,
                         objective,  # 'feasibility' | 'min_teams' | 'min_cost'
                         fix_n_teams=None, max_n_teams=None,
                         effective_max_trips=None, transport_teams=None, qbar=None):
    """构建统一 MIP 模型。返回 (model, ctx)。

    ctx = {'x','route_active','y','z','p','w','n_teams',
           'priority_touching','eligible_by_route'}
    """
    branches = data['branches']
    active_codes = [c for c, b in branches.items() if b['has_demand']]
    vehicles = data['params']['vehicles']
    set_a = {c for c, b in branches.items() if b['set_A']}
    code_to_group = {c: b['group_code'] for c, b in branches.items()}
    cap_big = vehicles[0]['capacity']

    model = cp_model.CpModel()

    # ---- 变量 ----
    x = {}
    for r in routes:
        rid = r['route_id']
        for k_idx in range(len(vehicles)):
            x[(rid, k_idx)] = model.NewBoolVar(f'x_{rid}_{k_idx}')

    route_active = {}
    for r in routes:
        rid = r['route_id']
        route_active[rid] = model.NewBoolVar(f'a_{rid}')
        model.Add(sum(x[(rid, k_idx)] for k_idx in range(len(vehicles))) == route_active[rid])

    y = {}
    for r in routes:
        rid = r['route_id']
        for i in r['stops']:
            y[(rid, i)] = model.NewIntVar(0, branches[i]['demand'], f'y_{rid}_{i}')

    # 优先子任务变量：z_{r,t} 路线 r 整装子任务 t；p_r 承运优先；w_{r,k}=p∧x
    subtasks = []
    eligible = defaultdict(list)            # tid -> [(rid, size), ...]
    eligible_by_route = defaultdict(list)   # rid -> [(tid, size), ...]
    priority_touching = set()
    if priority_tasks:
        subtasks = expand_priority_tasks(priority_tasks, cap_big)
        for tid, code, size in subtasks:
            for r in routes:
                rid = r['route_id']
                if size == cap_big:
                    if r['stops'] == [code]:
                        eligible[tid].append((rid, size))
                        eligible_by_route[rid].append((tid, size))
                        priority_touching.add(rid)
                else:
                    if code in r['stops']:
                        eligible[tid].append((rid, size))
                        eligible_by_route[rid].append((tid, size))
                        priority_touching.add(rid)

    z = {}
    for tid in eligible:
        for rid, _s in eligible[tid]:
            z[(rid, tid)] = model.NewBoolVar(f'z_{rid}_{tid[0]}_{tid[1]}')

    p = {}
    w = {}
    for rid in priority_touching:
        p[rid] = model.NewBoolVar(f'p_{rid}')
        for k_idx in range(len(vehicles)):
            w[(rid, k_idx)] = model.NewBoolVar(f'w_{rid}_{k_idx}')

    # 车队变量
    has_teams = n_teams_max > 0
    n_teams = None
    if has_teams:
        n_teams = model.NewIntVar(0, n_teams_max, 'n_teams')
        if fix_n_teams is not None:
            model.Add(n_teams == fix_n_teams)
        if max_n_teams is not None:
            model.Add(n_teams <= max_n_teams)

    objective_terms = []

    # ---- 约束 ----
    # (a) 需求满足
    for i in active_codes:
        routes_with_i = [r for r in routes if i in r['stops']]
        if not routes_with_i:
            continue
        model.Add(sum(y[(r['route_id'], i)] for r in routes_with_i) == branches[i]['demand'])

    # (b)-(f) 每路线（所有路线统一，不再豁免）
    for r in routes:
        rid = r['route_id']
        active = route_active[rid]
        total_y = sum(y[(rid, i)] for i in r['stops'])

        # (b) 激活联动 + 每站最低
        for i in r['stops']:
            model.Add(y[(rid, i)] <= branches[i]['demand'] * active)
            min_per_stop = min(params['MIN_BOXES_PER_STOP'], branches[i]['demand'])
            model.Add(y[(rid, i)] >= min_per_stop * active)

        for k_idx, v in enumerate(vehicles):
            cap = v['capacity']
            sel = x[(rid, k_idx)]

            # (c) 容量
            model.Add(total_y <= cap).OnlyEnforceIf(sel)

            # (d) 装载率（统一生效）
            if enforce_load_rate:
                if r['category'] == 'ANY' and load_rate_hard_target is not None:
                    load_min = math.ceil(load_rate_hard_target * cap)
                    model.Add(total_y >= load_min).OnlyEnforceIf(sel)
                elif r['category'] == 'A_ONLY':
                    load_min = math.ceil(params['LOAD_RATE_TARGET_A_ONLY'] * cap)
                    model.Add(total_y >= load_min).OnlyEnforceIf(sel)
                else:
                    # 软惩罚（A_B 路线，或 ANY 软档）—— 多档分段线性(用户 2026-08-10):
                    #   ≥80% 无惩罚(LOAD_RATE_TARGET=0.80); [70,80] 100/箱; [50,70] +400/箱;
                    #   [30,50] +500/箱; [0,30] +2000/箱 (累计最高 3000/箱, 高于70%轻、低于30%重)
                    load_min_90 = math.ceil(params['LOAD_RATE_TARGET'] * cap)
                    load_min_70 = math.ceil(params['LOAD_RATE_MID'] * cap)
                    load_min_50 = math.ceil(params['LOAD_RATE_CRITICAL'] * cap)
                    load_min_30 = math.ceil(params['LOAD_RATE_SEVERE'] * cap)
                    slack = model.NewIntVar(0, load_min_90, f'slack_{rid}_{k_idx}')
                    below_70 = model.NewIntVar(0, load_min_70, f'below70_{rid}_{k_idx}')
                    below_50 = model.NewIntVar(0, load_min_50, f'below50_{rid}_{k_idx}')
                    below_30 = model.NewIntVar(0, load_min_30, f'below30_{rid}_{k_idx}')
                    model.Add(total_y + slack >= load_min_90).OnlyEnforceIf(sel)
                    model.Add(below_70 >= slack - (load_min_90 - load_min_70))
                    model.Add(below_50 >= below_70 - (load_min_70 - load_min_50))
                    model.Add(below_30 >= below_50 - (load_min_50 - load_min_30))
                    objective_terms.append(int(round(params['PENALTY'] * COST_SCALE)) * slack)
                    objective_terms.append(int(round(params['PENALTY_MID'] * COST_SCALE)) * below_70)
                    objective_terms.append(int(round(params['PENALTY_CRITICAL'] * COST_SCALE)) * below_50)
                    objective_terms.append(int(round(params['PENALTY_SEVERE'] * COST_SCALE)) * below_30)

        # (e) A_B 比例
        if r['category'] == 'A_B':
            a_in_route = [i for i in r['stops'] if i in set_a]
            if a_in_route:
                sum_a = sum(y[(rid, i)] for i in a_in_route)
                for k_idx, v in enumerate(vehicles):
                    a_max = int(params['A_B_A_RATE_MAX'] * v['capacity'])
                    sel = x[(rid, k_idx)]
                    model.Add(sum_a <= a_max).OnlyEnforceIf(sel)

        # (f) 组排序（同组前站 ≥ 后站）
        stops = r['stops']
        for pos_i in range(len(stops)):
            for pos_j in range(pos_i + 1, len(stops)):
                i, j = stops[pos_i], stops[pos_j]
                if code_to_group.get(i) == code_to_group.get(j):
                    model.Add(y[(rid, i)] >= y[(rid, j)]).OnlyEnforceIf(active)

    # (g1) 每型总路线 ≤ 一般池 max_trips(绝对上限;车队车从一般池出,不扩张运力)
    # (g2) 每型优先路线 ≤ 车队池(优先必须用车队车)
    trip_limits = effective_max_trips if effective_max_trips is not None else [v['max_trips'] for v in vehicles]
    for k_idx in range(len(vehicles)):
        model.Add(sum(x[(r['route_id'], k_idx)] for r in routes) <= trip_limits[k_idx])
        if has_teams:
            model.Add(sum(w[(rid, k_idx)] for rid in priority_touching)
                      <= n_teams * team_mix[k_idx])

    # (g1b 运力版见 (g3) 之后) 优先车队剩余车辆按日平均运输量 Q̄ 启用(运力口径)。

    # (g1c) 已移除(2026-08-05):原"每网点路线数 ≤ ceil(车队数/4)"在车队少时过紧
    #       (4 队 → 每网点 ≤1 条,需求超单路线容量即无解,实验 2 Stage-1 INFEASIBLE)。
    #       单网点拆分度交由 Stage-2 滑动窗口间隔约束(g)兜底,不再在 Stage-1 硬限制。

    # (g3) 每车队日运量上限:车队运量 = 承载优先子任务路线的总箱数(优先+拼车普通) ≤ 3000×n_teams
    #      v_r = p_r · total_y(r) 线性化(仅 priority_touching 路线)
    if has_teams:
        team_vol_terms = []
        for r in routes:
            rid = r['route_id']
            if rid not in priority_touching:
                continue
            pr = p[rid]
            total_y = sum(y[(rid, i)] for i in r['stops'])
            v = model.NewIntVar(0, cap_big, f'v_{rid}')
            model.Add(v <= total_y)
            model.Add(v <= cap_big * pr)
            model.Add(v >= total_y - cap_big * (1 - pr))
            team_vol_terms.append(v)
        model.Add(sum(team_vol_terms) <= params['TEAM_CAP_BOXES'] * n_teams)

    # (g1b) 普通路线运力约束(运力口径,替换旧"车型数全扣"):
    #       普通路线可运箱数 ≤ 非优先日车队容量 + 优先日可释放容量 max(0, m·Q̄ − R_cap)
    #       Q̄ = 总配送需求/车队数(日平均运输量);R_cap = 优先路线车辆总容量。
    #       语义:优先车队运优先的车总容量 < m·Q̄ → 可启用剩余车辆运普通,但该队(天)
    #       总运力 ≤ Q̄(普通量在 Stage-2 排到优先日)。优先车队车辆不再"全扣",避免
    #       优先量小时(如萧县 71 箱占 1 台大车)整队剩余车闲置/误伤。
    #       依赖 g3 的 team_vol_terms(v_r = p_r·total_y)求 R_vol,故置于 (g3) 之后。
    if has_teams and transport_teams and qbar is not None:
        n_max = n_teams_max
        cap_k = [v['capacity'] for v in vehicles]
        # 容量前缀:prefix_cap[m] = 前 m 队车辆总容量
        prefix_cap = [0] * (n_max + 1)
        for m in range(1, n_max + 1):
            vt = transport_teams[m - 1]['vehicles']
            prefix_cap[m] = prefix_cap[m - 1] + sum(vt[k] * cap_k[k] for k in range(len(vehicles)))
        pool_cap_total = prefix_cap[n_max]
        # n_teams 线性化(收紧,用户 2026-08-09):u_m = [n_teams ≥ m](累积,单调),
        #   n_teams = Σ_m u_m。替换旧 δ_m=(n_teams==m) 1-hot + 大M 析取,
        #   消除根节点 LP 析取松弛,分支剪枝更快。
        u = {}
        for m in range(1, n_max + 1):
            u[m] = model.NewBoolVar(f'nteams_ge_{m}')
            if m > 1:
                model.Add(u[m - 1] >= u[m])
        model.Add(n_teams == sum(u[m] for m in range(1, n_max + 1)))
        # ---- 车型数约束(无大M精确式):普通(k) + 前 n_teams 队车型(k) ≤ 池(k) ----
        #       普通车型(k) ≤ 池(k) − 前 n_teams 队车型(k)。运力约束允许普通排到优先日,
        #       但车型数必须能在"非优先日 + 优先日槽位"内放下;全扣优先车队车型是
        #       保守上界(优先量小不启用时,优先日不排普通,普通车型只能占非优先日)。
        #       否则如实验 1:普通小车 21 > 非优先日 20,Stage-2 无解。
        for k_idx in range(len(vehicles)):
            ordinary_cnt = model.NewIntVar(0, trip_limits[k_idx], f'ordinary_cnt_{k_idx}')
            model.Add(ordinary_cnt == sum(x[(r['route_id'], k_idx)] for r in routes)
                                       - sum(w[(rid, k_idx)] for rid in priority_touching))
            # prefix_veh[k][n_teams] = Σ_m u_m·team_veh[k][m](按队车型分段累加)
            team_veh = sum(u[m] * transport_teams[m - 1]['vehicles'][k_idx]
                           for m in range(1, n_max + 1))
            model.Add(ordinary_cnt + team_veh <= trip_limits[k_idx])
        # R_cap = 优先路线车辆总容量(Σ w_{r,k}·cap_k)
        R_cap = model.NewIntVar(0, pool_cap_total, 'rcap')
        model.Add(R_cap == sum(w[(rid, k_idx)] * cap_k[k_idx]
                               for rid in priority_touching
                               for k_idx in range(len(vehicles))))
        # 普通路线运力 = D_total(待配需求)− R_vol(优先路线装载量,复用 g3 的 v_r)
        # 总配送要么走优先路线要么普通路线,故普通路线运力 = D_total − Σv_r
        D_total = sum(b['demand'] for b in branches.values())
        ordinary_vol = model.NewIntVar(0, D_total, 'ordinary_vol')
        model.Add(ordinary_vol == D_total - sum(team_vol_terms))
        # release = max(0, n_teams·Q̄ − R_cap):优先车队运优先容量 < n_teams·Q̄ 时剩余车
        #   可运普通(该队天总运力 ≤ Q̄),否则不释放。符号指示精确线性化(替换旧 per-m
        #   release 大M——旧版 release 未被目标下压、实际可能偏松)。
        diff = qbar * sum(u[m] for m in range(1, n_max + 1)) - R_cap
        release = model.NewIntVar(0, max(0, n_max * qbar), 'release')
        model.Add(release >= diff)
        b_rel = model.NewBoolVar('release_pos')
        model.Add(diff >= 0).OnlyEnforceIf(b_rel)
        model.Add(diff <= -1).OnlyEnforceIf(b_rel.Not())
        model.Add(release <= diff).OnlyEnforceIf(b_rel)
        model.Add(release <= 0).OnlyEnforceIf(b_rel.Not())
        team_cap = sum(u[m] * (prefix_cap[m] - prefix_cap[m - 1])
                       for m in range(1, n_max + 1))   # prefix_cap[n_teams]
        model.Add(ordinary_vol <= (pool_cap_total - team_cap) + release)

    # (h) 优先子任务（非拆分）
    if priority_tasks:
        # 每子任务恰 1 条路线
        for tid in eligible:
            model.Add(sum(z[(rid, tid)] for rid, _s in eligible[tid]) == 1)
        # z ≤ a（承运 → 激活）
        for (rid, tid) in z:
            model.Add(z[(rid, tid)] <= route_active[rid])
        # y ≥ Σ size·z（按网点汇总）
        for rid, tasks in eligible_by_route.items():
            tasks_by_code = defaultdict(list)
            for tid, size in tasks:
                tasks_by_code[tid[0]].append((tid, size))
            for code, ts in tasks_by_code.items():
                expr = sum(size * z[(rid, tid)] for tid, size in ts)
                model.Add(y[(rid, code)] >= expr)
        # p 指示（承运任一优先子任务）
        for rid in priority_touching:
            ts = eligible_by_route.get(rid, [])
            model.Add(p[rid] <= sum(z[(rid, tid)] for tid, _s in ts))
            for tid, _s in ts:
                model.Add(p[rid] >= z[(rid, tid)])
        # w = p ∧ x
        for rid in priority_touching:
            for k_idx in range(len(vehicles)):
                model.Add(w[(rid, k_idx)] >= p[rid] + x[(rid, k_idx)] - 1)
                model.Add(w[(rid, k_idx)] <= p[rid])
                model.Add(w[(rid, k_idx)] <= x[(rid, k_idx)])

    # ---- 目标 ----
    if objective == 'feasibility':
        objective_terms = [route_active[rid] for rid in route_active]
    elif objective == 'min_teams':
        objective_terms = [n_teams] if n_teams is not None else [0]
    elif objective == 'min_team_volume':
        # 车队探测目标:最小化车队总运量 Σv_r —— 直接驱动搜索向"紧凑拼装"的可行域
        # (g3 硬约束下,任何可行解的 Σv_r 已 ≤ 3000·n_teams;该目标加速找到该可行域)
        objective_terms = list(team_vol_terms) if has_teams else [0]
    else:  # min_cost
        # 运输成本（无车辆数量目标；软装载率惩罚项已在上方约束循环中加入 objective_terms）
        for r in routes:
            rid = r['route_id']
            for i, coeff in r['cost_coeffs'].items():
                objective_terms.append(int(round(coeff * COST_SCALE)) * y[(rid, i)])

    model.Minimize(sum(objective_terms))

    return model, {
        'x': x, 'route_active': route_active, 'y': y,
        'z': z, 'p': p, 'w': w, 'n_teams': n_teams,
        'priority_touching': priority_touching,
        'eligible_by_route': eligible_by_route,
    }

def _probe_feasibility(params, data, routes, load_rate_target_any, n_teams_max, team_mix,
                       priority_tasks, effective_max_trips, fix_n_teams=None,
                       objective='feasibility', stop_after_first=True,
                       transport_teams=None, qbar=None, hint=None):
    """可行性探测：统一模型（含优先+车队），n_teams 固定。

    fix_n_teams: 车队搜索时逐 N 探测传 N；None 时 n_teams 自由（装载率档位探测：
        不固定车队数，让 g1b/g3 联合决定——固定 n_teams_max 会让 g1b 把普通路线
        压成 0 而误判装载率不可行）。
    load_rate_target_any: ANY 路线硬装载率档位（0.9/0.8）；None 表示软档。
    objective: 'feasibility'(min 路线) 或 'min_team_volume'(min 车队运量,车队探测用,
        直接驱动搜索向满足 3000 箱/队上限的紧凑拼装可行域)。
    stop_after_first: True 时找到第一个解即停(可解性早停,用户 2026-08-05);False 时用
        优化模式(LNS)驱动目标向可行域搜索(车队探测:min 车队运量会把搜索带向低运量
        紧凑拼装,更易命中"4902 路线里的针")。
    hint: (solver, ctx) 外部可行解,作 AddHint 软热启动(探测热启动共享,用户 2026-08-05)。
        装载率档位探测的可行解直接喂给后续车队探测,避免重复搜索;hint 赋值若与
        fix_n_teams=N 冲突(如 hint 用了更多车队),由 CP-SAT 忽略,不误判可解性。

    Returns:
        (result, solver, ctx): result ∈ {'feasible','infeasible','timeout'};
        有解时返回 solver/ctx 供 Pass 2 热启动(AddHint)。
    """
    model, ctx = _build_unified_model(params,
        data, routes, priority_tasks, n_teams_max, team_mix,
        enforce_load_rate=True, load_rate_hard_target=load_rate_target_any,
        objective=objective,
        fix_n_teams=fix_n_teams,
        effective_max_trips=effective_max_trips,
        transport_teams=transport_teams,
        qbar=qbar)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = FEASIBILITY_CHECK_TIME
    solver.parameters.num_search_workers = NUM_SEARCH_WORKERS
    solver.parameters.linearization_level = 2
    solver.parameters.symmetry_level = 2
    solver.parameters.log_search_progress = False
    # 档位探测只需判定"是否存在可行解",找到第一个解即停,无需最小化路线数
    solver.parameters.stop_after_first_solution = stop_after_first

    # 探测热启动共享:外部可行解直接作本模型 AddHint(软约束,与 fix_n_teams 冲突时忽略)。
    # x/z/y 的 key 都是 (route_id, ...) 形式,两模型共用同一 routes 列表,key 一一对应。
    if hint is not None:
        s_hint, ctx_hint = hint
        for key, var in ctx_hint['x'].items():
            model.AddHint(ctx['x'][key], s_hint.Value(var))
        for key, var in ctx_hint['z'].items():
            model.AddHint(ctx['z'][key], s_hint.Value(var))
        for key, var in ctx_hint['y'].items():
            model.AddHint(ctx['y'][key], s_hint.Value(var))

    status = solver.Solve(model)
    if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
        return 'feasible', solver, ctx
    elif status == cp_model.INFEASIBLE:
        return 'infeasible', None, None
    else:
        return 'timeout', solver, ctx

def solve(params, data, routes, ftl_routes, verbose=True, enforce_load_rate=True,
          effective_max_trips=None, priority_tasks=None,
          transport_teams=None, external_hints=None, pass2_time_limit=None):
    """统一 MIP 求解 + 合并：装载率档位探测 → Pass 1 最少车队 → Pass 2 最低成本。

    external_hints（可选）：外部解（如 ALNS）热启动提示，list of dict：
      {'route_id': int, 'vehicle_k': int, 'boxes': {code: qty}, 'subtask_ids': [(code, part), ...]}
    在 Pass 2 对 x/y/z 变量加 AddHint（软约束），引导 CP-SAT 从外部解起步。
    pass2_time_limit（可选）：覆盖 Pass 2 分级时限（测试/对比用）。

    求解后直接与 FTL 路线合并（merge_solutions），补车队/优先任务字段，
    返回**合并后的完整结果 merged**（供 main 验证与日程安排使用）。

    Returns:
        merged: 合并结果 dict（含 'routes'/'teams_activated'/'teams_saturated' 等）
    """
    if not HAS_ORTOOLS:
        raise RuntimeError("MIP 求解失败且无 OR-Tools，无法生成结果")

    branches = data['branches']
    vehicles = data['params']['vehicles']
    set_a = {c for c, b in branches.items() if b['set_A']}
    cap_big = vehicles[0]['capacity']

    if verbose:
        print(f"Routes before reduction: {len(routes)}")

    routes_before = {tuple(r['stops']): r for r in routes}

    protect_codes = set(priority_tasks) if priority_tasks else None
    routes = filter_by_demand_feasibility(routes, branches, min_load=DEMAND_FILTER_MIN_LOAD,
                                          protect_codes=protect_codes)
    # 自适应 MAX_ROUTES_PER_SET(用户 2026-08-05):先试 RPS=3,若 reduce 后路线数 ≤
    # MAX_ROUTES_AUTO_CAP(2500)则采用 3(更多拼车候选 → 装载率更优,实验2 其它路线
    # 27%~73%→63%~79%);否则降回 1(正式数据 RPS=3 路线 13435,模型20×、600s Pass2
    # 不足且 Stage-2 峰值 3589 明显变差)。reduce_routes 不改输入,可安全重试。
    routes_filt = routes
    routes3 = reduce_routes(routes_filt, max_per_set=3)
    if len(routes3) <= MAX_ROUTES_AUTO_CAP:
        routes = routes3
        if verbose:
            print(f"  Auto MAX_ROUTES_PER_SET: 3 (reduce → {len(routes3)} routes ≤ {MAX_ROUTES_AUTO_CAP})")
    else:
        routes = reduce_routes(routes_filt, max_per_set=1)
        if verbose:
            print(f"  Auto MAX_ROUTES_PER_SET: 1 (RPS=3 后 {len(routes3)} routes > {MAX_ROUTES_AUTO_CAP}, "
                  f"降回 RPS=1 → {len(routes)} routes)")
    routes = _ensure_full_load_routes(routes, routes_before, priority_tasks, cap_big)
    if verbose:
        print(f"Routes after reduction + full-load cloning: {len(routes)}")

    # 车队：优先任务必须用车队车（g2）；普通量可占车队+一般池（g1）
    has_teams = bool(transport_teams and priority_tasks)
    n_teams_max = len(transport_teams) if has_teams else 0
    team_mix = transport_teams[0]['vehicles'] if has_teams else [0, 0, 0]
    # 日平均运输量 Q̄ = 总配送需求 / 车队数(用户确认)。总配送需求 = FTL 后待配需求 + FTL 已运量。
    # 用于 g1b:优先车队运优先的车容量 < m·Q̄ 时可启用剩余车运普通,但该队总运力 ≤ Q̄。
    qbar = None
    if has_teams:
        total_demand = sum(b['demand'] for b in branches.values())
        ftl_load = sum(f.get('total_load', 0) for f in ftl_routes)
        qbar = (total_demand + ftl_load) // n_teams_max

    # ---- 装载率档位探测 ----
    load_rate_hard_target = None
    load_rate_probe = None        # (solver, ctx):档位探测的可行解,供车队探测/Pass 2 热启动共享
    if enforce_load_rate:
        if verbose:
            print("Checking load-rate feasibility (unified model, all teams)...")
        for target, name in [(params['LOAD_RATE_TARGET_ANY_HARD_90'], "90%"),
                             (params['LOAD_RATE_TARGET_ANY_HARD_80'], "80%")]:
            t0 = time.time()
            result, s_probe, ctx_probe = _probe_feasibility(params, data, routes, target, n_teams_max, team_mix,
                                                priority_tasks, effective_max_trips,
                                                transport_teams=transport_teams, qbar=qbar)
            el = time.time() - t0
            if result == 'feasible':
                load_rate_hard_target = target
                load_rate_probe = (s_probe, ctx_probe)   # 探测解共享:车队搜索/Pass 2 直接复用
                if verbose:
                    print(f"  ✓ {name} hard feasible ({el:.1f}s) → using hard {name} for ANY")
                break
            elif result == 'timeout':
                load_rate_hard_target = target
                if verbose:
                    print(f"  ⏱ {name} hard timeout ({el:.1f}s) → will try hard {name} in full solve")
                break
            else:
                if verbose:
                    print(f"  ✗ {name} hard infeasible ({el:.1f}s)")
        if load_rate_hard_target is None and verbose:
            print("  → fallback to soft load rate")

    # ---- 车队搜索:递增尝试法(下界 → 逐 N 先贪心构造、再 MIP 探测) ----
    n_teams_opt = 0
    # warm_start = ('construct', plan, team_vols) 或 ('probe', solver, ctx)
    warm_start = None
    if has_teams:
        subtasks = expand_priority_tasks(priority_tasks, vehicles[0]['capacity'])
        lb = _team_lower_bound(params, subtasks, team_mix, vehicles, n_teams_max,
                               routes=routes, branches=branches,
                               hard_target=load_rate_hard_target)
        if verbose:
            print(f"Team search: capacity lower bound N≥{lb} "
                  f"({len(subtasks)} priority subtasks)")
        hard_any = (load_rate_hard_target if load_rate_hard_target is not None
                    else params['LOAD_RATE_TARGET_ANY_HARD_80'])
        # 装载率档位探测可行解隐含的车队数:≤ N 时该解对 n_teams==N 模型仍合法,
        # 可作探测热启动 hint 并早停;> N 时 hint 与 g2/g3 冲突,由 CP-SAT 忽略。
        probe_implied = None
        if load_rate_probe is not None:
            s_hint, ctx_hint = load_rate_probe
            if ctx_hint.get('n_teams') is not None:
                probe_implied = s_hint.Value(ctx_hint['n_teams'])
        for N in range(lb, n_teams_max + 1):
            # 先尝试贪心构造(快速得到可行解 + 热启动)。
            # 关键:1 车队可行但"紧凑拼装"是 4902 路线里的针,CP-SAT 纯搜索难命中;
            #     贪心共载优先子任务到少数路线,直接给出可行拼装,跳过慢速 MIP 探测。
            plan = _construct_priority_packing(params,
                subtasks, routes, branches, vehicles, team_mix, N, hard_any)
            if plan is not None:
                p_plan, p_vols = plan
                n_teams_opt = N
                warm_start = ('construct', p_plan, p_vols)
                if verbose:
                    print(f"  ✓ {N} team(s) via constructive packing "
                          f"(team vol {p_vols}, {len(p_plan)} routes) → min teams = {N}")
                break
            # 构造失败 → MIP 探测(目标 min 车队运量)。
            # 可解性探测 early stop(用户 2026-08-05):探测只判定"N 队是否可行",
            # 有合法 hint 时找到首个可行解即停(stop_after_first=True);无 hint 时维持
            # 优化模式(LNS)驱动搜索向紧凑拼装可行域(1 车队"针"场景)。
            hint_valid = (load_rate_probe is not None
                          and (probe_implied is None or probe_implied <= N))
            t0 = time.time()
            result, s_probe, ctx_probe = _probe_feasibility(params,
                data, routes, load_rate_hard_target,
                n_teams_max, team_mix, priority_tasks,
                effective_max_trips, fix_n_teams=N,
                objective='min_team_volume',
                stop_after_first=hint_valid,
                transport_teams=transport_teams, qbar=qbar,
                hint=(load_rate_probe if hint_valid else None))
            el = time.time() - t0
            if result == 'feasible':
                n_teams_opt = N
                warm_start = ('probe', s_probe, ctx_probe)
                if verbose:
                    print(f"  ✓ {N} team(s) feasible ({el:.1f}s) → min teams = {N}")
                break
            elif result == 'timeout':
                n_teams_opt = N
                if verbose:
                    print(f"  ⏱ {N} team(s) timeout ({el:.1f}s) → assume feasible, "
                          f"will verify in Pass 2")
                break
            else:
                if verbose:
                    print(f"  ✗ {N} team(s) infeasible ({el:.1f}s)")
        if n_teams_opt == 0:
            n_teams_opt = n_teams_max
            if verbose:
                print(f"  Fallback: n_teams = {n_teams_max}")

    # ---- Pass 2: 最低成本(含软装载率惩罚;无车辆数量目标) ----
    # 车队数递增回退:若探测假设的 N* 实际不可行(探测超时误判,或热启动未命中),
    # 逐步放大 max_n_teams 重试,得到合法解后再退出;避免整条流水线坠入贪心回退。
    solution = None
    for trial_N in range(n_teams_opt, n_teams_max + 1):
        t0 = time.time()
        model2, ctx2 = _build_unified_model(params,
            data, routes, priority_tasks, n_teams_max, team_mix,
            enforce_load_rate, load_rate_hard_target,
            objective='min_cost',
            max_n_teams=(trial_N if has_teams else None),
            effective_max_trips=effective_max_trips,
            transport_teams=transport_teams,
            qbar=qbar)

        # 热启动:首个尝试用探测/构造解做 hint,引导 CP-SAT 直接进入满足
        # 3000 箱/队上限的紧凑拼装可行域(否则 min-成本 会优先探索"宽松但超上限"的解)
        if trial_N == n_teams_opt and warm_start is not None:
            src = warm_start[0]
            if src == 'construct':
                # 构造已证明这些优先路线可行(容量+80%+每队≤3000)。
                # 用硬约束固定(而非软 hint):引导 Pass 2 从干净拼装完成,避免偏离
                # 到"低装载烂路线"(实测软 hint 600s 会偏离出 A_B 11.2% 的烂路线,
                # 硬固定 400s 得到无 <70% 路线的干净解)。
                _plan = warm_start[1]
                n_fix = 0
                for rid, (k, subs, _t) in _plan.items():
                    model2.Add(ctx2['x'][(rid, k)] == 1)
                    n_fix += 1
                    for tid, _br, _sz in subs:
                        if (rid, tid) in ctx2['z']:
                            model2.Add(ctx2['z'][(rid, tid)] == 1)
                            n_fix += 1
                if ctx2['n_teams'] is not None:
                    model2.Add(ctx2['n_teams'] <= trial_N)
                if verbose:
                    print(f"  Fix {len(_plan)} priority routes from constructive "
                          f"packing ({n_fix} constraints)")
            else:  # 'probe'
                s_probe, ctx_probe = warm_start[1], warm_start[2]
                for key, var in ctx_probe['x'].items():
                    model2.AddHint(ctx2['x'][key], s_probe.Value(var))
                for key, var in ctx_probe['z'].items():
                    model2.AddHint(ctx2['z'][key], s_probe.Value(var))
                for key, var in ctx_probe['y'].items():
                    model2.AddHint(ctx2['y'][key], s_probe.Value(var))
                if ctx_probe['n_teams'] is not None and ctx2['n_teams'] is not None:
                    model2.AddHint(ctx2['n_teams'], s_probe.Value(ctx_probe['n_teams']))
                if verbose:
                    print(f"  Warm start from team-probe solution ({len(ctx2['x'])} hints)")

        # 兜底热启动:无构造/探测 hint 时的两种情形(均软 hint,不与硬固定冲突)。
        elif trial_N == n_teams_opt and warm_start is None and load_rate_probe is not None:
            # ① 装载率档位探测已给出合法可行解(探测热启动共享):直接作 Pass 2 hint,
            #    避免从零搜索。覆盖"车队搜索全失败(超时)"与"无优先量"两种路径。
            s_hint, ctx_hint = load_rate_probe
            _nh = 0
            for key, var in ctx_hint['x'].items():
                model2.AddHint(ctx2['x'][key], s_hint.Value(var)); _nh += 1
            for key, var in ctx_hint['z'].items():
                model2.AddHint(ctx2['z'][key], s_hint.Value(var))
            for key, var in ctx_hint['y'].items():
                model2.AddHint(ctx2['y'][key], s_hint.Value(var))
            if ctx_hint['n_teams'] is not None and ctx2['n_teams'] is not None:
                model2.AddHint(ctx2['n_teams'], s_hint.Value(ctx_hint['n_teams']))
            if verbose:
                print(f"  Warm start from load-rate probe solution ({_nh} hints)")
        elif trial_N == n_teams_opt and warm_start is None and not has_teams:
            # ② 无优先量(has_teams=False)且档位探测超时/软档:用贪心解做 Pass 2 首解 hint,
            #    让 CP-SAT 从可行解起步、FEASIBLE 提前到来,再由内置 LNS 改善。
            #    贪心解满足需求/容量/装载率(≥90% ≥ 任意硬档),是无优先情形的合法解。
            greedy = greedy_warm_start(params, data, routes, verbose=False)
            if greedy:
                for g in greedy:
                    model2.AddHint(ctx2['x'][(g['id'], g['vehicle_k'])], 1)
                    for i, qty in g['boxes'].items():
                        model2.AddHint(ctx2['y'][(g['id'], i)], qty)
                if verbose:
                    print(f"  Warm start from greedy solution ({len(greedy)} routes)")

        # 外部解热启动（如 ALNS）：只在首次尝试加 x/y/z AddHint（软），引导 CP-SAT 从外部解起步
        if trial_N == n_teams_opt and external_hints:
            _nh = 0
            for h in external_hints:
                rid = h['route_id']
                if (rid, h['vehicle_k']) in ctx2['x']:
                    model2.AddHint(ctx2['x'][(rid, h['vehicle_k'])], 1)
                    _nh += 1
                for c, q in h['boxes'].items():
                    if (rid, c) in ctx2['y']:
                        model2.AddHint(ctx2['y'][(rid, c)], int(q))
                for (cc, pp) in h.get('subtask_ids', []):
                    if (rid, (cc, pp)) in ctx2['z']:
                        model2.AddHint(ctx2['z'][(rid, (cc, pp))], 1)
            if verbose and _nh:
                print(f"  Warm start from external solution ({_nh} route hints)")

        # Pass 2 时限按算例规模自动分档(小 60s / 中 200s / 大 600s),替换固定超参。
        # 构造热启动(大算例 1 队紧凑拼装难完成)已由大算例档(600s)覆盖,无需额外放宽。
        time_limit = _grade_pass2_time(
            len(routes),
            construction=(trial_N == n_teams_opt and warm_start is not None
                          and warm_start[0] == 'construct'))
        if pass2_time_limit is not None:
            time_limit = pass2_time_limit

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = time_limit
        solver.parameters.num_search_workers = NUM_SEARCH_WORKERS
        solver.parameters.linearization_level = 2
        solver.parameters.symmetry_level = 2
        solver.parameters.log_search_progress = verbose

        if verbose:
            print(f"Pass 2 (n_teams ≤ {trial_N}): optimizing cost "
                  f"(limit {time_limit}s)...")
        status = solver.Solve(model2)
        elapsed = time.time() - t0

        if verbose:
            print(f"Status: {solver.StatusName(status)}, time: {elapsed:.1f}s")
            print(f"Objective: {solver.ObjectiveValue()/COST_SCALE:.2f} yuan")

        # 兜底(2026-08-05):构造 hard-fix 把"装不下的多站路线"钉死后 ≤N 会伪不可行
        # (例:优先量4500 2队→误升3队,诊断 free 模式证明 2 队可行)。此时去掉硬固定、
        # 用构造解作软 hint 重试同 N,确认真不可行再升级 N——避免"最少车队"目标被
        # 构造选路错误放大。仅首次尝试(N == n_teams_opt)的构造路径需要此兜底。
        if (status not in [cp_model.OPTIMAL, cp_model.FEASIBLE]
                and trial_N == n_teams_opt and warm_start is not None
                and warm_start[0] == 'construct'):
            if verbose:
                print(f"  ↻ construct hard-fix {solver.StatusName(status)} at ≤{trial_N}; "
                      f"retry same N with soft hints...")
            model2b, ctx2b = _build_unified_model(params,
                data, routes, priority_tasks, n_teams_max, team_mix,
                enforce_load_rate, load_rate_hard_target,
                objective='min_cost',
                max_n_teams=(trial_N if has_teams else None),
                effective_max_trips=effective_max_trips,
                transport_teams=transport_teams,
                qbar=qbar)
            _plan = warm_start[1]
            for rid, (k, subs, _t) in _plan.items():
                model2b.AddHint(ctx2b['x'][(rid, k)], 1)
                for tid, _br, _sz in subs:
                    if (rid, tid) in ctx2b['z']:
                        model2b.AddHint(ctx2b['z'][(rid, tid)], 1)
            t1 = time.time()
            solver2 = cp_model.CpSolver()
            solver2.parameters.max_time_in_seconds = time_limit
            solver2.parameters.num_search_workers = NUM_SEARCH_WORKERS
            solver2.parameters.linearization_level = 2
            solver2.parameters.symmetry_level = 2
            solver2.parameters.log_search_progress = verbose
            if verbose:
                print(f"  Pass 2 retry (n_teams ≤ {trial_N}, soft hints): "
                      f"optimizing cost (limit {time_limit}s)...")
            status = solver2.Solve(model2b)
            elapsed = time.time() - t1
            solver, model2, ctx2 = solver2, model2b, ctx2b
            if verbose:
                print(f"Status: {solver.StatusName(status)}, time: {elapsed:.1f}s")
                print(f"Objective: {solver.ObjectiveValue()/COST_SCALE:.2f} yuan")

        if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
            n_teams_actual = trial_N
            if has_teams and ctx2['n_teams'] is not None:
                n_teams_actual = solver.Value(ctx2['n_teams'])
            solution = _extract_solution(params, 
                solver, routes, vehicles, branches, set_a,
                ctx2['x'], ctx2['y'], elapsed,
                solver.StatusName(status),
                solver.ObjectiveValue() / COST_SCALE,
                p=ctx2['p'], n_teams=n_teams_actual,
                n_teams_max=n_teams_max, team_mix=team_mix,
                z=ctx2['z'], eligible_by_route=ctx2['eligible_by_route'])
            break
        else:
            if verbose:
                print(f"  ✗ Pass 2 (≤{trial_N}) {solver.StatusName(status)} → "
                      + ("retry with more teams" if trial_N < n_teams_max else "give up"))
            if trial_N >= n_teams_max:
                break

    if solution is None:
        print("ERROR: MIP solver failed to find a solution.")
        print("Falling back to greedy solution...")
        vehicles_w = data['params']['vehicles']
        greedy_sol = greedy_warm_start(params, data, routes)
        solution = {
            'routes': greedy_sol,
            'total_cost': sum(s['cost'] for s in greedy_sol),
            'total_boxes': sum(s['total_load'] for s in greedy_sol),
            'vehicle_usage': {},
            'delivered': {},
            'solver_status': 'GREEDY_FALLBACK',
            'solve_time': 0,
            'teams_activated': 0,
        }
        for k_idx in range(len(vehicles_w)):
            solution['vehicle_usage'][k_idx] = sum(
                1 for s in greedy_sol if s.get('vehicle_k') == k_idx)

    # 合并 FTL + MIP 路线，补车队/优先任务字段，返回 merged（供 main 验证与日程安排）
    merged = merge_solutions(ftl_routes, solution)
    merged['teams_activated'] = solution.get('teams_activated', 0)
    merged['total_teams'] = len(transport_teams) if transport_teams else 0
    merged['priority_tasks'] = priority_tasks or {}
    merged['teams_saturated'] = solution.get('teams_saturated')
    return merged

def _extract_solution(params, solver, routes, vehicles, branches, set_a, x, y, solve_time,
                      status_name, solver_obj, p=None, n_teams=0, n_teams_max=0,
                      team_mix=None, z=None, eligible_by_route=None):
    solution_routes = []
    total_cost = 0
    total_boxes = 0
    vehicle_usage = defaultdict(int)
    delivered = defaultdict(int)
    priority_carriers = []

    for r in routes:
        rid = r['route_id']
        selected_k = None
        for k_idx in range(len(vehicles)):
            if solver.Value(x[(rid, k_idx)]) == 1:
                selected_k = k_idx
                vehicle_usage[k_idx] += 1
                break
        if selected_k is None:
            continue

        boxes = {}
        total_load = 0
        for i in r['stops']:
            qty = solver.Value(y[(rid, i)])
            if qty > 0:
                boxes[i] = qty
                total_load += qty
                delivered[i] += qty
        if total_load == 0:
            continue

        cap = vehicles[selected_k]['capacity']
        load_rate = total_load / cap if cap > 0 else 0
        cost = sum(r['cost_coeffs'].get(i, 0) * boxes.get(i, 0) for i in boxes)
        total_cost += cost
        total_boxes += total_load

        a_load = sum(boxes.get(i, 0) for i in r['stops'] if i in set_a)
        a_rate = a_load / cap if cap > 0 else 0

        is_priority = p is not None and solver.Value(p.get(rid, 0)) == 1

        # 该路线承载的优先子任务量(非拆分优先量,来自 z 变量)
        priority_boxes = 0
        if z is not None and eligible_by_route is not None:
            for tid, size in eligible_by_route.get(rid, []):
                if solver.Value(z.get((rid, tid), 0)) == 1:
                    priority_boxes += size

        sr = {
            'id': rid,
            'stops': r['stops'],
            'names': [branches[c]['name'] for c in r['stops']],
            'category': r['category'],
            'vehicle_type': vehicles[selected_k]['name'],
            'vehicle_k': selected_k,
            'capacity': cap,
            'boxes': boxes,
            'total_load': total_load,
            'load_rate': round(load_rate, 4),
            'a_load': a_load,
            'a_rate': round(a_rate, 4),
            'dist_km': r['total_dist_km'],
            'cost': round(cost, 2),
            'team': 0,
            'is_priority': is_priority,
            'priority_boxes': priority_boxes,
        }
        solution_routes.append(sr)
        if is_priority:
            priority_carriers.append(sr)

    # 优先路线标注车队号:先填满前 n-1 个车队(饱和),使"用了 n 个车队"在装载上也成立——
    # 前 n-1 队应已无法再接受任何优先路线。尊重每队车型槽位 + 每队运量 ≤ params['TEAM_CAP_BOXES']。
    # 排序按"优先量降序"(同优先量再按总运量降序):让优先量最多的路线优先装进早车队,
    # 契合"前面车队优先量装得满"的业务目标——若按总运量排序,优先量少但普通拼车多的路线
    # 会先占满早车队容量,挤占优先量。g2/g3 聚合约束保证存在可行标注;
    # 此处精确分配并校验每队运量 ≤ 3000 及饱和性。
    teams_saturated = None
    if p is not None and n_teams_max > 0 and team_mix and priority_carriers:
        n_types = len(vehicles)
        ordered = sorted(priority_carriers,
                         key=lambda s: (-s.get('priority_boxes', 0), -s['total_load']))
        n_ord = len(ordered)
        teams_state = [{'slots': [team_mix[k] for k in range(n_types)], 'volume': 0}
                       for _ in range(n_teams)]
        assign_res = [None] * n_ord

        def _assign(idx):
            if idx >= n_ord:
                return True
            sr = ordered[idx]
            k = sr['vehicle_k']
            load = sr['total_load']
            # 按队号升序尝试(先填满车队1,再车队2…) → 前 n-1 队优先饱和
            for t in range(len(teams_state)):
                st = teams_state[t]
                if st['slots'][k] <= 0 or st['volume'] + load > params['TEAM_CAP_BOXES']:
                    continue
                st['slots'][k] -= 1
                st['volume'] += load
                assign_res[idx] = t
                if _assign(idx + 1):
                    return True
                st['slots'][k] += 1
                st['volume'] -= load
                assign_res[idx] = None
            return False

        if _assign(0):
            for idx, sr in enumerate(ordered):
                sr['team'] = assign_res[idx] + 1
            team_vol = [ts['volume'] for ts in teams_state]
            for t in range(n_teams):
                if team_vol[t] > params['TEAM_CAP_BOXES']:
                    print(f"  WARNING: team {t+1} volume {team_vol[t]} > {params['TEAM_CAP_BOXES']}")
            print(f"  Team volume: "
                  + " | ".join(f"team{t+1}:{team_vol[t]}箱" for t in range(n_teams))
                  + f" (cap {params['TEAM_CAP_BOXES']}/team)")

            # 饱和校验:前 n-1 队是否已无法再接受任何更低编号队的优先路线(槽位+容量)
            sat_ok = True
            for t in range(n_teams - 1):
                for idx, sr in enumerate(ordered):
                    if assign_res[idx] <= t:
                        continue  # 该路线已在 t 队或更早队
                    k = sr['vehicle_k']
                    if (teams_state[t]['slots'][k] > 0
                            and teams_state[t]['volume'] + sr['total_load'] <= params['TEAM_CAP_BOXES']):
                        print(f"  ⚠ team {t+1} NOT saturated: could take route "
                              f"{sr['id']} ({sr['total_load']}箱) from team "
                              f"{assign_res[idx]+1}")
                        sat_ok = False
            teams_saturated = sat_ok
            if sat_ok:
                print(f"  ✓ teams 1..{n_teams-1} saturated: no team can accept "
                      f"more priority routes")
        else:
            # 兜底:按车型槽位贪心(极端情况下聚合可行但回溯未找到,打印警告)
            print(f"  WARNING: no team assignment fits {params['TEAM_CAP_BOXES']}/team; "
                  f"falling back to greedy type-slot labeling")
            teams_saturated = False
            remaining = {}
            for t in range(n_teams_max):
                for k in range(len(vehicles)):
                    remaining[(t, k)] = team_mix[k]
            for sr in sorted(priority_carriers, key=lambda s: -s['vehicle_k']):
                k = sr['vehicle_k']
                for t in range(n_teams):
                    if remaining.get((t, k), 0) > 0:
                        sr['team'] = t + 1
                        remaining[(t, k)] -= 1
                        break

    print(f"\nSelected routes: {len(solution_routes)} ({len(priority_carriers)} priority)")
    print(f"Total cost: {total_cost:.2f} yuan (solver obj: {solver_obj:.2f})")
    print(f"Total delivered: {total_boxes} boxes")
    for k_idx, v in enumerate(vehicles):
        used = vehicle_usage[k_idx]
        print(f"  {v['name']}: {used}/{v['max_trips']}")

    return {
        'routes': solution_routes,
        'total_cost': round(total_cost, 2),
        'total_boxes': total_boxes,
        'vehicle_usage': dict(vehicle_usage),
        'delivered': dict(delivered),
        'solver_status': status_name,
        'solve_time': solve_time,
        'teams_activated': n_teams,
        'teams_saturated': teams_saturated,
    }

def greedy_warm_start(params, data, routes, verbose=True):
    """贪心初始解（作为 Pass 2 无解时的兜底）。"""
    branches = data['branches']
    vehicles = data['params']['vehicles']
    active_codes = [c for c, b in branches.items() if b['has_demand']]
    set_a = {c for c, b in branches.items() if b['set_A']}

    route_by_branch = defaultdict(list)
    for r in routes:
        for s in r['stops']:
            route_by_branch[s].append(r)

    sorted_codes = sorted(active_codes, key=lambda c: branches[c]['dist_to_center_km'], reverse=True)
    remaining = {c: branches[c]['demand'] for c in active_codes}
    selected = []
    veh_remaining = [v['max_trips'] for v in vehicles]

    for code in sorted_codes:
        if remaining[code] <= 0:
            continue
        candidates = route_by_branch.get(code, [])
        candidates = sorted(candidates, key=lambda r: sum(r['cost_coeffs'].values()) / r['n_stops'])

        for r in candidates:
            if remaining[code] <= 0:
                break
            needs = any(remaining.get(s, 0) > 0 for s in r['stops'])
            if not needs:
                continue

            best_k = None
            for k_idx, v in enumerate(vehicles):
                if veh_remaining[k_idx] > 0:
                    est = sum(min(remaining.get(s, 0), branches[s]['demand']) for s in r['stops'])
                    if est / v['capacity'] >= params['LOAD_RATE_TARGET']:
                        best_k = k_idx
                        break
            if best_k is None:
                continue

            boxes = {}
            total = 0
            cap = vehicles[best_k]['capacity']
            for s in r['stops']:
                need = remaining.get(s, 0)
                if need > 0:
                    alloc = min(need, cap - total)
                    if alloc > 0:
                        boxes[s] = alloc
                        total += alloc
                        remaining[s] -= alloc

            if total > 0 and total / cap >= params['LOAD_RATE_TARGET']:
                veh_remaining[best_k] -= 1
                cost = sum(r['cost_coeffs'].get(s, 0) * boxes.get(s, 0) for s in boxes)
                a_load = sum(boxes.get(s, 0) for s in r['stops'] if s in set_a)
                a_rate = a_load / cap if cap > 0 else 0
                selected.append({
                    'id': r['route_id'],
                    'stops': r['stops'],
                    'names': [branches[c]['name'] for c in r['stops']],
                    'category': r['category'],
                    'vehicle_k': best_k,
                    'vehicle_type': vehicles[best_k]['name'],
                    'capacity': cap,
                    'boxes': boxes,
                    'total_load': total,
                    'load_rate': total / cap,
                    'a_load': a_load,
                    'a_rate': a_rate,
                    'cost': round(cost, 2),
                    'dist_km': r['total_dist_km'],
                    'team': 0,  # greedy 阶段无运输队分配
                    'is_priority': False,
                    'priority_boxes': 0,
                })

    total_del = sum(s['total_load'] for s in selected)
    total_cost = sum(s['cost'] for s in selected)
    if verbose:
        print(f"Greedy: {len(selected)} routes, {total_del} boxes, {total_cost:.0f} yuan")
    return selected

def filter_no_task_routes(routes, branches):
    """结构过滤：取掉包含"无配送任务网点"(has_demand=False)的路径。

    枚举本身只从有需求的网点构建路径，正常不会有此类网点；但 FTL 预处理
    会把需求被整车清零的网点置为 has_demand=False，此时已枚举路径中仍可能
    含有这些"死停靠点"。调用方应在 FTL 之后用本函数做二次过滤。
    """
    kept = []
    dropped = 0
    for r in routes:
        if any(not branches[s].get('has_demand', False) for s in r['stops']):
            dropped += 1
        else:
            kept.append(r)
    return kept, dropped

def enumerate_routes(params, data, verbose=True):
    """
    枚举所有可行路径结构
    返回: routes列表，每条route包含:
        - route_id: int
        - stops: [code1, code2, ...]  有序网点序列
        - category: "ANY" | "A_ONLY" | "A_B"
        - distances: {leg lengths}
        - cost_coeffs: {branch_code: unit_cost_coefficient}
        - total_dist_km: float
        - n_stops: int
    """
    t0 = time.time()

    branches = data['branches']
    compatible = data['compatible']
    d_center = data['d_center']
    d_ij = data['full_d_ij']  # branch-branch distances from xlsx
    center_code = data['center_code']
    set_a = {c for c, b in branches.items() if b['set_A']}
    set_b = {c for c, b in branches.items() if b['set_B']}

    # 有需求的网点
    active_codes = [c for c, b in branches.items() if b['has_demand']]
    n_active = len(active_codes)
    if verbose:
        print(f"Active branches (with demand): {n_active}")
        print(f"Set A: {len(set_a)}, Set B: {len(set_b)}")
        print(f"Other (not A or B): {n_active - len(set_a & set(active_codes)) - len(set_b & set(active_codes))}")

    # 预计算距离查询函数
    def get_dist_center_to(code):
        return d_center.get(('center_to', code), branches[code]['dist_to_center_km'])

    def get_dist_to_center(code):
        return d_center.get((code, 'to_center'), get_dist_center_to(code))

    def get_dist_ij(i, j):
        key = (i, j)
        if key in d_ij:
            return d_ij[key]
        # fallback: try reversed or from angle CSV
        key_rev = (j, i)
        if key_rev in d_ij:
            return d_ij[key_rev]
        # last resort: approximate from angle data
        return data['d_ij'].get(key, data['d_ij'].get(key_rev, params['MISSING_DIST_SENTINEL']))

    # 预计算每个网点的"邻居"（同方向兼容的网点）
    if verbose:
        print("Building neighbor lists (angle buckets)...")
    neighbors = defaultdict(list)
    for i in active_codes:
        for j in active_codes:
            if i == j:
                continue
            if compatible.get((i, j), False):
                neighbors[i].append(j)
    avg_neighbors = sum(len(v) for v in neighbors.values()) / len(neighbors)
    if verbose:
        print(f"Average neighbors per branch: {avg_neighbors:.1f}")

    # 路径分类函数
    def classify(stops):
        has_a = any(s in set_a for s in stops)
        if not has_a:
            return "ANY"
        has_b = any(s in set_b for s in stops)
        has_other = any(s not in set_a and s not in set_b for s in stops)
        if has_other:
            return "INVALID"
        if has_b:
            return "A_B"
        return "A_ONLY"

    routes = []
    route_id = 0
    stats = {'1stop': 0, '2stop': 0, '3stop': 0, 'invalid': 0, 'dist_fail': 0, 'angle_fail': 0}

    # ---- 1站路径 ----
    if verbose:
        print("Enumerating 1-stop routes...")
    for i in active_codes:
        d_out = get_dist_center_to(i)
        d_back = get_dist_to_center(i)
        total_d = d_out + d_back
        if total_d > params['L_MAX']:
            stats['dist_fail'] += 1
            continue

        cat = classify([i])
        if cat == "INVALID":
            stats['invalid'] += 1
            continue

        routes.append({
            'route_id': route_id,
            'stops': [i],
            'category': cat,
            'total_dist_km': round(total_d, 3),
            'n_stops': 1,
            'cost_coeffs': {i: params['C0'] * d_out},
            'leg_distances': [d_out],
        })
        route_id += 1
        stats['1stop'] += 1

    if verbose:
        print(f"  1-stop routes: {stats['1stop']}")

    # ---- 2站路径 ----
    if verbose:
        print("Enumerating 2-stop routes...")
    for i in active_codes:
        d_out_i = get_dist_center_to(i)
        for j in active_codes:
            if i == j:
                continue
            # 角度检查
            if not compatible.get((i, j), False):
                stats['angle_fail'] += 1
                continue

            d_ij_val = get_dist_ij(i, j)
            d_back_j = get_dist_to_center(j)
            total_d = d_out_i + d_ij_val + d_back_j
            if total_d > params['L_MAX']:
                stats['dist_fail'] += 1
                continue

            cat = classify([i, j])
            if cat == "INVALID":
                stats['invalid'] += 1
                continue

            routes.append({
                'route_id': route_id,
                'stops': [i, j],
                'category': cat,
                'total_dist_km': round(total_d, 3),
                'n_stops': 2,
                'cost_coeffs': {i: params['C0'] * d_out_i, j: params['C0'] * d_ij_val},
                'leg_distances': [d_out_i, d_ij_val],
            })
            route_id += 1
            stats['2stop'] += 1

    if verbose:
        print(f"  2-stop routes: {stats['2stop']}")

    # ---- 3站路径 (角度桶加速) ----
    if verbose:
        print("Enumerating 3-stop routes (angle bucket accelerated)...")
    three_count = 0
    processed_triples = set()

    for i in active_codes:
        d_out_i = get_dist_center_to(i)
        # 只考虑i的邻居范围内的3站组合
        nbrs = neighbors[i]
        nbr_set = set(nbrs)

        for j in nbrs:
            if j == i:
                continue
            d_ij_val = get_dist_ij(i, j)

            # j也必须与i兼容（已满足），但j、l之间也需要兼容
            for l in nbrs:
                if l == i or l == j:
                    continue
                # 检查j和l是否兼容(即l也在j的邻居内)
                if not compatible.get((j, l), False):
                    continue

                # 去重：triple (i,j,l)
                triple_key = (i, j, l)
                if triple_key in processed_triples:
                    continue
                processed_triples.add(triple_key)

                d_jl_val = get_dist_ij(j, l)
                d_back_l = get_dist_to_center(l)
                total_d = d_out_i + d_ij_val + d_jl_val + d_back_l
                if total_d > params['L_MAX']:
                    stats['dist_fail'] += 1
                    continue

                cat = classify([i, j, l])
                if cat == "INVALID":
                    stats['invalid'] += 1
                    continue

                routes.append({
                    'route_id': route_id,
                    'stops': [i, j, l],
                    'category': cat,
                    'total_dist_km': round(total_d, 3),
                    'n_stops': 3,
                    'cost_coeffs': {i: params['C0'] * d_out_i, j: params['C0'] * d_ij_val, l: params['C0'] * d_jl_val},
                    'leg_distances': [d_out_i, d_ij_val, d_jl_val],
                })
                route_id += 1
                three_count += 1

    stats['3stop'] = three_count
    if verbose:
        print(f"  3-stop routes: {three_count}")

    # ---- 组排序重排(用户 2026-08-10): 同组网点按需求降序,保证 MIP 组排序约束 (f) 恒可满足 ----
    # 旧枚举按腿距排顺序,可能把同组小需求网点排在大需求前面(如 全椒13 → 滁州城郊939),
    # MIP 组排序要求 y[前站]≥y[后站],小在前大在后 → 路线死 → 可能整体无解(202605 案例)。
    # 重排后大需求在前, y[大]≥y[小] 恒可满足。重排后重建 cost 字段并校验相邻兼容/距离。
    def _group_desc_order(stops):
        """同组网点按需求降序重排(稳定,组间相对顺序不变)。"""
        if len(stops) < 2:
            return stops
        groups = {}
        g_order = []
        for s in stops:
            g = branches[s].get('group_code') or s
            if g not in groups:
                groups[g] = []
                g_order.append(g)
            groups[g].append(s)
        out = []
        for g in g_order:
            out.extend(sorted(groups[g], key=lambda s: -branches[s]['demand']))
        return out

    def _rebuild_route_cost(r, new_stops):
        """按新顺序重建 cost 字段,校验相邻兼容 + 距离 ≤ L_MAX。校验失败返回原路线。"""
        n = len(new_stops)
        for a in range(n - 1):
            if not compatible.get((new_stops[a], new_stops[a + 1]), False) and \
               not compatible.get((new_stops[a + 1], new_stops[a]), False):
                return r
        legs = [get_dist_center_to(new_stops[0])]
        for a in range(n - 1):
            legs.append(get_dist_ij(new_stops[a], new_stops[a + 1]))
        total_d = sum(legs) + get_dist_to_center(new_stops[-1])
        if total_d > params['L_MAX']:
            return r
        r2 = dict(r)
        r2['stops'] = new_stops
        r2['total_dist_km'] = round(total_d, 3)
        r2['cost_coeffs'] = {s: params['C0'] * legs[a] for a, s in enumerate(new_stops)}
        r2['leg_distances'] = legs
        return r2

    n_reorder = 0
    for idx, r in enumerate(routes):
        new_stops = _group_desc_order(r['stops'])
        if new_stops != r['stops']:
            r2 = _rebuild_route_cost(r, new_stops)
            if r2 is not r:
                routes[idx] = r2
                n_reorder += 1
    if n_reorder > 0 and verbose:
        print(f"  Group-order reorder: {n_reorder} routes")

    # ---- 结构过滤:取掉包含"无配送任务网点"(has_demand=False)的路径 ----
    # 枚举只从有需求的网点构建,此处防御性校验保证不变量;FTL 清零后的
    # 二次过滤由 main.py 在 preprocess_ftl 之后调用 filter_no_task_routes 完成。
    routes, dropped_no_task = filter_no_task_routes(routes, branches)
    if dropped_no_task:
        stats['no_task'] = dropped_no_task
        if verbose:
            print(f"  Removed {dropped_no_task} routes containing no-task branches")

    elapsed = time.time() - t0
    if verbose:
        print(f"\n=== Route Enumeration Summary ===")
        print(f"Total feasible routes: {len(routes)}")
        cat_counts = defaultdict(int)
        for r in routes:
            cat_counts[r['category']] += 1
        for cat in ['ANY', 'A_ONLY', 'A_B']:
            print(f"  {cat}: {cat_counts[cat]}")
        print(f"Filtered: angle_fail={stats['angle_fail']}, dist_fail={stats['dist_fail']}, invalid_structure={stats['invalid']}")
        print(f"Time: {elapsed:.1f}s")

    return routes

def preprocess_ftl(params, data, priority_tasks=None):
    """整车直配预处理

    对需求 >= params['FTL_DEMAND_THRESHOLD'] 的网点，分配大车整车直达配送。
    优先任务网点跳过 FTL，留给统一 MIP 处理以保证非拆分。
    修改 data 中的 demand、has_demand 和 大车 max_trips（一般池扣减）。

    Args:
        data: 原始数据字典（会被原地修改）
        priority_tasks: 优先任务字典，这些网点不触发 FTL

    Returns:
        ftl_routes: FTL 路线列表
        ftl_stats: [(code, name, num_ftl, remaining_demand), ...]
    """
    branches = data['branches']
    vehicles = data['params']['vehicles']
    d_center = data['d_center']
    set_a = {c for c, b in branches.items() if b['set_A']}

    # 收集 FTL 候选网点，按需求降序排列（高需求优先分配车辆）
    candidates = []
    for code, b in branches.items():
        if b['has_demand'] and b['demand'] >= params['FTL_DEMAND_THRESHOLD']:
            num_ftl = b['demand'] // params['FTL_CAPACITY']
            candidates.append((code, b, num_ftl))

    candidates.sort(key=lambda x: -x[1]['demand'])

    ftl_routes = []
    ftl_stats = []
    remaining_big_trips = vehicles[params['FTL_VEHICLE_K']]['max_trips']

    for code, b, num_potential in candidates:
        # 跳过优先任务网点：留给统一 MIP 处理以保证非拆分
        if priority_tasks and code in priority_tasks:
            continue
        # 不超过大车剩余趟数
        num_ftl = min(num_potential, remaining_big_trips)
        if num_ftl == 0:
            continue

        original_demand = b['demand']
        remaining = original_demand - num_ftl * params['FTL_CAPACITY']

        # 获取距离（去程和回程）
        d_out = d_center.get(('center_to', code), b['dist_to_center_km'])
        d_back = d_center.get((code, 'to_center'), d_out)
        total_dist = round(d_out + d_back, 3)

        # 距离安全检查：FTL 直发路径也不能超 750km
        if total_dist > params['L_MAX']:
            print(f"  SKIP {b['name']}({code}): FTL round-trip {total_dist:.1f}km > {params['L_MAX']}km")
            continue

        is_set_a = code in set_a
        # 单站路径分类：Set A → A_ONLY，其余 → ANY
        category = 'A_ONLY' if is_set_a else 'ANY'

        for _ in range(num_ftl):
            ftl_routes.append({
                'id': -1,                           # 合并时重新编号
                'stops': [code],
                'names': [b['name']],
                'category': category,
                'vehicle_type': vehicles[params['FTL_VEHICLE_K']]['name'],
                'vehicle_k': params['FTL_VEHICLE_K'],
                'capacity': params['FTL_CAPACITY'],
                'boxes': {code: params['FTL_CAPACITY']},
                'total_load': params['FTL_CAPACITY'],
                'load_rate': 1.0,
                'a_load': params['FTL_CAPACITY'] if is_set_a else 0,
                'a_rate': 1.0 if is_set_a else 0.0,
                'dist_km': total_dist,
                'cost': round(d_out * params['FTL_CAPACITY'] * params['C0'], 2),
                'team': None, 'is_priority': False, 'priority_boxes': 0,
            })

        # 更新网点需求
        b['demand'] = remaining
        if remaining == 0:
            b['has_demand'] = False

        remaining_big_trips -= num_ftl
        ftl_stats.append((code, b['name'], num_ftl, original_demand, remaining))

    # 更新大车可用趟数（一般池扣减）
    vehicles[params['FTL_VEHICLE_K']]['max_trips'] = remaining_big_trips

    return ftl_routes, ftl_stats

def merge_solutions(ftl_routes, mip_solution):
    """合并 FTL 路线和 MIP 求解结果"""
    mip_routes = mip_solution.get('routes', [])

    # 合并路线列表，统一重新编号（统一 schema：id/names/dist_km/cost）
    all_routes = []
    for idx, r in enumerate(ftl_routes + mip_routes):
        r['id'] = idx
        all_routes.append(r)

    # 汇总统计
    ftl_cost = sum(r['cost'] for r in ftl_routes)
    ftl_boxes = sum(r['total_load'] for r in ftl_routes)
    mip_cost = mip_solution.get('total_cost', 0)
    mip_boxes = mip_solution.get('total_boxes', 0)

    # 合并车辆使用统计
    vehicle_usage = {}
    for k_idx in range(3):
        vehicle_usage[k_idx] = sum(1 for r in all_routes if r.get('vehicle_k') == k_idx)

    # 合并各网点配送量
    delivered = {}
    for r in all_routes:
        for code, qty in r['boxes'].items():
            delivered[code] = delivered.get(code, 0) + qty

    return {
        'routes': all_routes,
        'total_cost': round(ftl_cost + mip_cost, 2),
        'total_boxes': ftl_boxes + mip_boxes,
        'vehicle_usage': vehicle_usage,
        'delivered': delivered,
        'solver_status': f"FTL({len(ftl_routes)})+{mip_solution.get('solver_status', 'UNKNOWN')}",
        'solve_time': mip_solution.get('solve_time', 0),
    }

def prepare_data(data):
    """车辆池（运输队.xlsx 各队车辆之和 → 每车 max_trips）+ 合并优先需求（取较大值）。

    就地修改 data（main 调用，作为求解前的数据准备）。
    """
    transport_teams = data.get('transport_teams', [])
    priority_tasks = data.get('priority_tasks', {})

    # 车辆池 = 运输队.xlsx 各队车辆之和（旧 max_trips 大22/中66/小44 作废）
    if transport_teams:
        team_pool = [0, 0, 0]
        for t in transport_teams:
            for k, cnt in enumerate(t.get('vehicles', [])):
                team_pool[k] += cnt
        for k in range(3):
            data['params']['vehicles'][k]['max_trips'] = team_pool[k]

    # 合并优先需求：取较大值
    if priority_tasks:
        for code, pqty in priority_tasks.items():
            b = data['branches'].get(code)
            if b:
                b['demand'] = max(b['demand'], pqty)
                b['has_demand'] = True
    return data
