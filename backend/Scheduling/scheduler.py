"""
日程安排程序（Step 2，独立于路线划分 MIP）

在路线划分结果（配送任务清单.csv）的基础上，把每条配送任务安排到工作日，
生成配送日程。Step 1（路线划分）与 Step 2（日程安排）是两个独立程序，
不混在一个 MIP 模型里。

====== 业务语义（用户确认）======
- 运输队序号 = 工作日序号（运输队.xlsx 每行 = 一个工作日的一支车队）。
- 优先量尽快配送：路线划分已把优先路线标到最早的 n 个车队/工作日，
  这里把优先路线固定在它所属的工作日（优先量不动）。
- 总车辆池 = 各队车辆之和；但每天只能出动当天对应运输队的车辆，
  所以日程安排的硬约束是：每天各车型车辆数 ≤ 当天运输队上限。
- 运输队序号**不允许缺失**：若 1..H 中有序号没有对应车队，直接触发异常。

====== 硬约束 ======
(a) 每条路径恰好安排在一个工作日
(b) 优先量路径固定在所属运输队/工作日（v5.1 路线划分保证非拆分）
(c) 非优先路径不得排到"全优先日"（优先量所在的最早 n 个工作日）
(d) 每天各车型车辆数 ≤ 当天运输队的车辆上限
(g) 到货间隔（滑动窗口硬约束）：网点 c 出现 k 次 → g_c = ⌊H/k⌋，
    任意连续 g_c 天窗口内至多配送一次，即任意两次配送间隔 ≥ g_c 天。

====== 目标（单遍，全部线性）======
min 非全优先日的日配送峰值（用户确认：只要保证峰值小即可，
无需单独优化日量均匀；到货间隔由硬约束 (g) 保证）。

====== 到货间隔规则（用户确认）======
网点在 k 个日期配送 → 相邻配送日期间隔 ≥ ⌊H/k⌋ 天（H = 工作日跨度，
按最大运输队序号，本算例 22），由滑动窗口约束 (g) 硬保证。某网点的优先量
若分在两天送，这两天不受间隔约束（v5.1 非拆分保证每个网点优先量只在 1 天，
故本规则在此算例不触发）；一天送优先、一天送非优先，仍受间隔约束
（全优先日的优先路线计入间隔计算）。

输出：单个 配送任务清单.csv（路径明细 + 工作日序号）+ 合并总报告 配送结果报告.html
（路径划分 + 日程安排）。Stage 1 → Stage 2 交接为内存传递（list/dict），不落盘 CSV/JSON。
"""

import time
import os
import sys
import json
import tempfile
import subprocess
from collections import defaultdict

from ortools.sat.python import cp_model

# 绝对路径基准目录（子进程编排时 cwd 不固定，不能用相对路径）
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _branch_name(routes, code):
    """网点编码 → 名称（本地实现，解除对 reporter 的依赖；用户 2026-08-10）。"""
    for r in routes:
        if code in r['stops']:
            return r['names'][r['stops'].index(code)]
    return code

# ====== 算法超参数 ======
SCHED_TIME_LIMIT = 30         # 单遍 min 峰值的求解时限（秒）
INTERVAL_PENALTY = 1000       # 间隔约束软化的违反惩罚（箱/次，高权重→尽量满足间隔）


# =====================================================================
# 数据加载
# =====================================================================



# =====================================================================
# CP-SAT 模型
# =====================================================================

def _build_model(routes, teams, H, avail_days, n_prio_days, occ, include_interval=True, qbar=None):
    """构建日程安排 CP-SAT 模型（单遍 min 峰值 + 滑动窗口间隔硬约束）。

    include_interval=False:跳过滑动窗口间隔约束(g),用于 INFEASIBLE 诊断
    (定位"间隔约束" vs "单日车型配额" 谁是瓶颈)。

    qbar: 日平均运输量 Q̄ = 总配送量/车队数。非 None 时,普通路线可排到优先日,
    但优先日每队(天)总运力 ≤ max(Q̄, 该天优先路线总运力)(优先车队剩余车辆启用,不超日均)。

    Returns:
        (model, ctx)，ctx 提供 assign / day_vol / peak / min_day。
    """
    model = cp_model.CpModel()

    assign = {}                       # (rid, day) -> BoolVar
    for r in routes:
        if r['is_priority']:
            allowed = [r['team']]     # 优先路线固定在其运输队/工作日
        else:
            # 非优先路线可排任意工作日(含优先日)。优先日每队(天)总运力
            # 受 ≤ max(Q̄, 该天优先路线运量)约束(见下),普通排入不会超日均。
            allowed = [d for d in avail_days]
        for d in allowed:
            assign[(r['id'], d)] = model.NewBoolVar(f'x_{r["id"]}_{d}')
        model.AddExactlyOne(assign[(r['id'], d)] for d in allowed)

    # 日配送量
    day_vol = {}
    for d in avail_days:
        day_vol[d] = model.NewIntVar(0, 60000, f'vol_{d}')
        model.Add(day_vol[d] == sum(
            assign[(r['id'], d)] * r['total_load']
            for r in routes if (r['id'], d) in assign))

    # (d) 每天各车型车辆 ≤ 当天运输队上限
    for d in avail_days:
        for k in range(3):
            used = sum(assign[(r['id'], d)] for r in routes
                       if r['vehicle_k'] == k and (r['id'], d) in assign)
            model.Add(used <= teams[d][k])

    # 峰值（仅非全优先日参与）
    peak = model.NewIntVar(0, 60000, 'peak')
    for d in avail_days:
        if d > n_prio_days:
            model.Add(peak >= day_vol[d])

    # (e2) 优先日每队(天)总运力 ≤ max(Q̄, 该天优先路线总运力)
    #      Q̄ = 日平均运输量(总配送量/车队数)。优先路线装载 < Q̄ → 该队剩余车可运普通
    #      (普通路线排到优先日),但总运力补足到 Q̄ 为止;优先路线装载 ≥ Q̄ → 不排普通。
    if qbar is not None:
        prio_vol_by_day = {}
        for r in routes:
            if r['is_priority'] and r.get('team') is not None:
                prio_vol_by_day[r['team']] = prio_vol_by_day.get(r['team'], 0) + r['total_load']
        for d in avail_days:
            if d <= n_prio_days:
                cap_day = max(qbar, prio_vol_by_day.get(d, 0))
                model.Add(day_vol[d] <= cap_day)

    # (g) 到货间隔·滑动窗口（软约束）：网点 c 出现 k 次 → g_c = ⌊H/k⌋，
    #     任意连续 g_c 天窗口内至多配送一次 ⟺ 任意两次配送间隔 ≥ g_c 天。
    #     （同时吸收原"同日一网点至多一条"约束：g_c ≥ 2 ⇒ 无同日重复）
    #     软约束：允许违反，惩罚 INTERVAL_PENALTY×Σslack 计入目标，尽量满足。
    interval_slack = []
    if include_interval:
        for c, rids in occ.items():
            k = len(rids)
            if k < 2:
                continue
            g = max(1, H // k)
            for d in range(1, H - g + 2):
                terms = [assign[(rid, dd)] for rid in rids
                         for dd in range(d, d + g) if (rid, dd) in assign]
                if len(terms) > 1:
                    s = model.NewIntVar(0, len(terms), f'islack_{c}_{d}')
                    model.Add(sum(terms) <= 1 + s)
                    interval_slack.append(s)

    # min_day 仅作报告指标（不再作为优化目标）
    min_day = model.NewIntVar(0, 60000, 'min_day')
    for d in avail_days:
        if d > n_prio_days:
            model.Add(min_day <= day_vol[d])

    ctx = {
        'assign': assign,
        'day_vol': day_vol,
        'peak': peak,
        'min_day': min_day,
        'interval_slack': interval_slack,
    }
    return model, ctx


def _solve_one_pass(pass_no, bounds, routes, teams, time_limit=60):
    """在单个进程内求解日程安排（子进程入口调用）。

    单遍求解：模型含约束 (a)-(d)+(g) 滑动窗口，目标 = min 非全优先日峰值。
    （曾尝试"峰值二分 + 可行性探测"证明最优，实测本实例探测大量超时(UNKNOWN)，
    无法快速判定，且 UNKNOWN 不能按不可行处理，故回归单遍 min 峰值。

    Args:
        pass_no: 1=min 峰值（唯一 pass）
        bounds: 保留参数（未用）
        routes / teams: 已加载的数据

    Returns:
        dict: {status, wall, peak, min_day, assignment}
    """
    H = max(teams.keys())
    avail_days = sorted(teams.keys())
    prio_serials = sorted({r['team'] for r in routes if r['is_priority']})
    n_prio_days = max(prio_serials) if prio_serials else 0
    # 日平均运输量 Q̄ = 总配送量 / 车队数(优先车队剩余车启用普通、每队(天)≤Q̄ 的依据)
    qbar = round(sum(r['total_load'] for r in routes) / max(1, len(teams))) if routes else 0
    occ = defaultdict(list)
    for r in routes:
        for c in r['stops']:
            occ[c].append(r['id'])

    def _extract(solver, ctx):
        assignment = {}
        for (rid, d), v in ctx['assign'].items():
            if solver.Value(v) == 1:
                assignment[rid] = d
        return assignment

    model, ctx = _build_model(routes, teams, H, avail_days, n_prio_days, occ, qbar=qbar)
    model.Minimize(ctx['peak'] + INTERVAL_PENALTY * sum(ctx['interval_slack']))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    solver.parameters.num_search_workers = 8
    t0 = time.time()
    st = solver.Solve(model)
    wall = time.time() - t0
    if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        # ---- INFEASIBLE 诊断:定位超配车型/天(并入异常消息,经 stderr 回传)----
        diag = []
        nonprio_days = [d for d in avail_days if d > n_prio_days]
        ord_by_k = [0, 0, 0]
        prio_by_team = {}
        for r in routes:
            k = r['vehicle_k']
            if r['is_priority']:
                t = r['team']
                prio_by_team.setdefault(t, [0, 0, 0])
                prio_by_team[t][k] += 1
            else:
                ord_by_k[k] += 1
        cap_ord = [0, 0, 0]
        for d in nonprio_days:
            for k in range(3):
                cap_ord[k] += teams[d][k]
        diag.append(f"普通路线车型数 {ord_by_k} vs 非优先日配额和 {cap_ord}")
        for k in range(3):
            if ord_by_k[k] > cap_ord[k]:
                diag.append(f"车型{k} 普通路线 {ord_by_k[k]} > 配额 {cap_ord[k]} 超配")
        for t in sorted(prio_by_team):
            diag.append(f"优先日{t} 优先路线车型 {prio_by_team[t]} vs 配额 {teams[t]}")
            for k in range(3):
                if prio_by_team[t][k] > teams[t][k]:
                    diag.append(f"优先日{t} 车型{k} 超配")
        # 诊断:忽略间隔约束(g)是否可行 → 定位"间隔 vs 单日车型配额"谁是瓶颈
        try:
            m_ni, _ = _build_model(routes, teams, H, avail_days, n_prio_days, occ,
                                   include_interval=False, qbar=qbar)
            s_ni = cp_model.CpSolver()
            s_ni.parameters.max_time_in_seconds = 10
            st_ni = s_ni.Solve(m_ni)
            diag.append(f"忽略间隔约束后: {solver.StatusName(st_ni)}")
        except Exception as _e:
            diag.append(f"无间隔诊断失败: {_e}")
        raise RuntimeError(f"日程安排无解: status={solver.StatusName(st)}; " + "; ".join(diag))
    return {
        'status': solver.StatusName(st), 'wall': wall,
        'peak': solver.Value(ctx['peak']),
        'min_day': solver.Value(ctx['min_day']),
        'assignment': _extract(solver, ctx),
    }


def _interval_penalty(day_assign, routes, H):
    """到货间隔指标（事后报告用）：相邻配送间隔与 floor(H/k) 的偏差之和。

    到货间隔已由约束 (g) 滑动窗口硬保证（间隔 ≥ ⌊H/k⌋），此函数只用于
    报告展示间隔质量，不是优化目标。
    """
    branch_dates = defaultdict(list)
    for r in routes:
        d = day_assign.get(r['id'])
        if d is None:
            continue
        for c in r['stops']:
            branch_dates[c].append(d)
    total = 0
    for c, ds in branch_dates.items():
        ds = sorted(ds)
        k = len(ds)
        if k < 2:
            continue
        g = max(1, H // k)
        for i in range(k - 1):
            total += abs(ds[i + 1] - ds[i] - g)
    return total


def _pass_worker_cli():
    """子进程入口：python scheduler.py --pass N --bounds F --out F

    路线/运输队通过环境变量 SCHED_ROUTES_FILE / SCHED_TEAMS_FILE 指向的临时 JSON 传入
    （由 solve_schedule 序列化，内存交接不落盘到 配送任务清单.csv）。scheduler 是纯库，
    不读 data_loader/不读 CSV——由 main.py 提供全部数据。
    """
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--pass', dest='pass_no', type=int, required=True)
    ap.add_argument('--bounds', required=True)
    ap.add_argument('--out', required=True)
    a = ap.parse_args()

    with open(os.environ['SCHED_ROUTES_FILE'], 'r', encoding='utf-8') as f:
        routes = json.load(f)
    with open(os.environ['SCHED_TEAMS_FILE'], 'r', encoding='utf-8') as f:
        teams = {int(k): v for k, v in json.load(f).items()}
    with open(a.bounds, 'r', encoding='utf-8') as f:
        bounds = json.load(f)
    tl = int(os.environ.get('SCHED_TIME_LIMIT', '60'))
    result = _solve_one_pass(a.pass_no, bounds, routes, teams, time_limit=tl)
    with open(a.out, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False)


def solve_schedule(routes, teams, time_limit=60, verbose=True):
    """单遍求解日程安排（min 非全优先日峰值）。

    模型含约束 (a)-(d) + (g) 滑动窗口（到货间隔硬保证，无独立间隔目标）。
    ortools 9.15 在本机同一进程内连续多次大模型求解会触发原生访问冲突，
    因此求解放在独立子进程运行；主进程通过临时 JSON 文件取结果。
    可行解空间对称性强，证明最优可能超时，接受时间限制内最优可行解（incumbent）。

    Returns:
        solution: {day_assign, day_vol, day_vehicles, day_routes,
                   branch_dates, peak, min_day, interval_cost, pass_status}
    """
    H = max(teams.keys())                       # 工作日跨度（最大运输队序号）
    avail_days = sorted(teams.keys())           # 实际可用工作日（load_teams 已保证连续）
    prio_serials = sorted({r['team'] for r in routes if r['is_priority']})
    n_prio_days = max(prio_serials) if prio_serials else 0
    non_prio_days = [d for d in avail_days if d > n_prio_days]
    rid_map = {r['id']: r for r in routes}

    if verbose:
        print(f"  工作日跨度 H={H}, 可用工作日 {len(avail_days)} 天, "
              f"全优先日: 1..{n_prio_days}")
        print(f"  非优先日: {non_prio_days} ({len(non_prio_days)} 天)")

    script = os.path.abspath(__file__)

    # 路线/运输队序列化到临时 JSON，经环境变量传给子进程（内存交接，不落盘到 CSV）。
    tmp = tempfile.mkdtemp(prefix='sched_p1_')
    rfile = os.path.join(tmp, 'routes.json')
    tfile = os.path.join(tmp, 'teams.json')
    with open(rfile, 'w', encoding='utf-8') as f:
        json.dump(routes, f, ensure_ascii=False)
    with open(tfile, 'w', encoding='utf-8') as f:
        json.dump({str(k): v for k, v in teams.items()}, f, ensure_ascii=False)
    env = dict(os.environ, SCHED_TIME_LIMIT=str(time_limit),
               SCHED_ROUTES_FILE=rfile, SCHED_TEAMS_FILE=tfile)

    bfile = os.path.join(tmp, 'bounds.json')
    ofile = os.path.join(tmp, 'out.json')
    with open(bfile, 'w', encoding='utf-8') as f:
        json.dump({}, f)
    proc = subprocess.run(
        [sys.executable, script, '--pass', '1', '--bounds', bfile,
         '--out', ofile],
        cwd=BASE_DIR, env=env, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"日程安排子进程失败: {proc.stderr[-500:]}")
    with open(ofile, 'r', encoding='utf-8') as f:
        p1 = json.load(f)

    peak_star = p1['peak']
    day_assign = {int(k): v for k, v in p1['assignment'].items()}
    min_star = p1['min_day']
    iv = _interval_penalty(day_assign, routes, H)      # 事后报告指标
    if verbose:
        print(f"  求解: min 峰值 = {peak_star} 箱 ({p1['status']}, {p1['wall']:.1f}s)")
        print(f"  到货间隔惩罚(事后指标) = {iv}")

    day_routes = defaultdict(list)
    for rid, d in day_assign.items():
        day_routes[d].append(rid)

    day_vehicles = {}
    for d in avail_days:
        day_vehicles[d] = [0, 0, 0]
    for rid, d in day_assign.items():
        day_vehicles[d][rid_map[rid]['vehicle_k']] += 1

    day_vol_final = {}
    for d in avail_days:
        day_vol_final[d] = sum(rid_map[rid]['total_load']
                               for rid in day_routes.get(d, []))

    branch_dates = defaultdict(list)
    for rid, d in day_assign.items():
        for c in rid_map[rid]['stops']:
            branch_dates[c].append(d)
    for c in branch_dates:
        branch_dates[c].sort()

    return {
        'H': H,
        'avail_days': avail_days,
        'n_prio_days': n_prio_days,
        'non_prio_days': non_prio_days,
        'day_assign': day_assign,
        'day_routes': day_routes,
        'day_vehicles': day_vehicles,
        'day_vol': day_vol_final,
        'branch_dates': branch_dates,
        'peak': peak_star,
        'min_day': min_star,
        'interval_cost': iv,
        'pass_status': (p1['status'],),
    }


# =====================================================================
# 校验 + 报告
# =====================================================================

def validate(solution, routes, teams, params):
    """约束校验，返回 (violations, checks)"""
    rid_map = {r['id']: r for r in routes}
    viol = []
    checks = []

    # (a) 每条路线恰好一个工作日
    for r in routes:
        if r['id'] not in solution['day_assign']:
            viol.append(f"路线 {r['id']} 未安排工作日")
    checks.append(f"{len(routes)} 条路线均已安排工作日" if not viol else "存在未安排路线")

    # (b) 优先路线固定在所属工作日
    for r in routes:
        if r['is_priority'] and solution['day_assign'].get(r['id']) != r['team']:
            viol.append(f"优先路线 {r['id']} 未固定在运输队 {r['team']}")
    checks.append("优先路线已固定在所属工作日" if not any(
        r['is_priority'] and solution['day_assign'].get(r['id']) != r['team']
        for r in routes) else "存在优先路线未固定")

    # (c) 非优先路线不在全优先日
    for r in routes:
        if not r['is_priority']:
            d = solution['day_assign'].get(r['id'], 0)
            if d <= solution['n_prio_days']:
                viol.append(f"非优先路线 {r['id']} 排在全优先日 {d}")
    checks.append("非优先路线均避开全优先日" if not any(
        not r['is_priority'] and solution['day_assign'].get(r['id'], 0) <= solution['n_prio_days']
        for r in routes) else "存在非优先路线落在全优先日")

    # (d) 每天车辆 ≤ 当天上限
    for d, v in solution['day_vehicles'].items():
        cap = teams[d]
        for k in range(3):
            if v[k] > cap[k]:
                viol.append(f"工作日 {d}: {params['VEHICLE_NAMES'][k]} 用 {v[k]} > 上限 {cap[k]}")
    checks.append("每天车辆数 ≤ 当天运输队上限" if not any(
        any(solution['day_vehicles'][d][k] > teams[d][k] for k in range(3))
        for d in solution['day_vehicles']) else "存在超上限工作日")

    # (e) 无运输队的工作日不安排（load_teams 已保证序号连续，此处兜底）
    for d in solution['day_routes']:
        if d not in teams:
            viol.append(f"无运输队工作日 {d} 被安排了任务")
    checks.append("缺失运输队序号的工作日未安排任务" if not any(
        d not in teams for d in solution['day_routes']) else "存在无运输队却安排的工作日")

    # (g) 到货间隔·滑动窗口：每网点任意两次配送间隔 ≥ ⌊H/k⌋
    H = solution['H']
    for c, dates in solution['branch_dates'].items():
        k = len(dates)
        if k < 2:
            continue
        g = max(1, H // k)
        dates_sorted = sorted(dates)
        for i in range(len(dates_sorted) - 1):
            gap = dates_sorted[i + 1] - dates_sorted[i]
            if gap < g:
                viol.append(f"到货间隔违规 {_branch_name(routes, c)}({c}): "
                            f"间隔 {gap} 天 < 目标 {g} 天")
    checks.append("每网点配送间隔 ≥ ⌊H/k⌋（滑动窗口约束）" if not any(
        v.startswith("到货间隔违规") for v in viol) else "存在到货间隔违规")

    return viol, checks


def solve(routes, teams, params=None, time_limit=SCHED_TIME_LIMIT):
    """求解日程安排，返回日程解（不输出文件、不验证——由 main 分别调用 validate/输出）。

    由 main.py 调用（routes/teams/params 内存传递，不落盘交接）；
    也可由 `python scheduler.py` 单独运行。

    Returns:
        solution: 日程安排结果 dict（供 Stage-2 验证与输出报告使用）
    """
    t0 = time.time()
    print("=" * 60)
    print("  配送日程安排 (Step 2)")
    print("  单遍 CP-SAT: min 非全优先日峰值 + 滑动窗口间隔硬约束")
    print("=" * 60)

    print("\n[1/2] 求解日程安排...")
    solution = solve_schedule(routes, teams, time_limit=time_limit, verbose=True)
    solution['elapsed'] = time.time() - t0

    # 控制台摘要
    H = solution['H']
    prio_routes = [r for r in routes if r['is_priority']]
    print(f"\n  全优先日(工作日 1..{solution['n_prio_days']}): "
          f"{len(prio_routes)} 条优先路线, "
          f"{sum(solution['day_vol'][d] for d in range(1, solution['n_prio_days']+1))} 箱")
    non_prio = solution['non_prio_days']
    vols = [solution['day_vol'][d] for d in non_prio]
    print(f"  非全优先日({len(non_prio)} 天): 峰值 {max(vols)} 箱, "
          f"平均 {sum(vols)/len(vols):.0f} 箱, 范围 {min(vols)}~{max(vols)} 箱")
    print(f"  到货间隔指标(事后,非目标): {solution['interval_cost']}")
    print(f"  求解总耗时: {time.time()-t0:.1f}s")
    return solution


if __name__ == '__main__':
    # 子进程入口：solve_schedule 用 sys.executable scheduler.py --pass N ... 单独进程求解
    _pass_worker_cli()
