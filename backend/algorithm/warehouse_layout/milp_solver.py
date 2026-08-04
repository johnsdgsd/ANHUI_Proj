"""
仓网布局优化 — MILP 求解器
纯数学建模与求解，不含业务逻辑。

三个求解函数:
  solve_distance_milp(data, pairs)       → min Z₂ (纯线性)
  solve_cost_milp(data, pairs)           → min Z₁ (SOS2 分段线性)
  solve_constrained_milp(data, pairs, D) → min Z₁ s.t. Z₂ ≤ D
"""
import logging
from typing import List, Tuple

import numpy as np
import pulp

from backend.algorithm.warehouse_layout.config import (
    R_w, L_w, z_w,
)

logger = logging.getLogger(__name__)

# SOS2 sqrt 分段数
N_BREAKPOINTS = 6


def _build_sos2_breakpoints(total_demand_per_k: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """为每个设备码构建 SOS2 sqrt 分段断点。

    Args:
        total_demand_per_k: shape (D,), 每个设备码总需求量

    Returns:
        breakpoints: shape (D, B), 每行是设备 k 的断点序列
        sqrt_vals:   shape (D, B), 对应的 sqrt 值
    """
    D = len(total_demand_per_k)
    B = N_BREAKPOINTS
    breakpoints = np.zeros((D, B))
    sqrt_vals = np.zeros((D, B))

    for k in range(D):
        u_max = total_demand_per_k[k]
        if u_max <= 0:
            u_max = 1.0
        # 在 [0, u_max] 均匀取 B 个断点
        breakpoints[k] = np.linspace(0, u_max, B)
        sqrt_vals[k] = np.sqrt(breakpoints[k])

    return breakpoints, sqrt_vals


def _reduce_pairs(
    data: dict, pairs: List[Tuple[int, int]]
) -> Tuple[List[Tuple[int, int]], dict, dict]:
    """预处理：固定唯一可达供���所和必须开启的库房，减少变量。

    Returns:
        active_pairs: 仍需决策的 (j,i) 对
        fixed_stations: {i: j} 只有一个可达库房的供电所，直接固定
        must_open: {j} 至少被一个固定供电所指向的库房，必须开启
    """
    S = len(data['station_codes'])
    reachable = {i: [] for i in range(S)}
    for j, i in pairs:
        reachable[i].append(j)

    fixed_stations = {}
    must_open = set()

    for i in range(S):
        if len(reachable[i]) == 1:
            # 只有一个可达库房，直接固定
            j = reachable[i][0]
            fixed_stations[i] = j
            must_open.add(j)
        elif len(reachable[i]) == 0:
            logger.warning(f"[MILP] 供电所 {data['station_codes'][i]} 无可达库房!")

    # 过滤出仍需决策的对
    active_pairs = [(j, i) for j, i in pairs if i not in fixed_stations]

    logger.info(
        f"[MILP] 预固定: {len(fixed_stations)} 个供电所(唯一可达), "
        f"{len(must_open)} 个库房必须开启, "
        f"剩余 {len(active_pairs)} 个可变对"
    )
    return active_pairs, fixed_stations, must_open


def _build_base_model(
    data: dict,
    active_pairs: List[Tuple[int, int]],
    fixed_stations: dict,
    must_open: set,
    breakpoints: np.ndarray,
    sqrt_vals: np.ndarray,
) -> Tuple[pulp.LpProblem, dict, dict, dict, np.ndarray]:
    """构建 MILP 基础模型（变量 + 通用约束），返回模型和变量字典。

    Returns:
        prob: PuLP problem
        x: {(j,i): LpVariable} 决策变量
        y: {j: LpVariable} 库房开启变量
        u: {(j,k): LpVariable} 仓库需求聚合变量
        station_demand: np.ndarray (S,), 每个供电所总需求
    """
    S = len(data['station_codes'])
    W = len(data['wh_codes'])
    D = len(data['dev_codes'])
    B = N_BREAKPOINTS
    demand = data['demand']
    dist = data['distance']

    prob = pulp.LpProblem("WarehouseLayout", pulp.LpMinimize)

    # ---- y_j: 库房是否开启 ----
    y = {}
    for j in range(W):
        y[j] = pulp.LpVariable(f"y_{j}", cat=pulp.LpBinary)

    # ---- x_ij: 供电所分配 ----
    x = {}
    for j, i in active_pairs:
        x[(j, i)] = pulp.LpVariable(f"x_{j}_{i}", cat=pulp.LpBinary)

    # ---- u_jk: 仓库需求聚合 ----
    u = {}
    for j in range(W):
        for k in range(D):
            u[(j, k)] = pulp.LpVariable(f"u_{j}_{k}", lowBound=0)

    # ---- v_jk: sqrt 近似 ----
    v = {}
    for j in range(W):
        for k in range(D):
            v[(j, k)] = pulp.LpVariable(f"v_{j}_{k}", lowBound=0)

    # ---- w_jkm: SOS2 权重 ----
    w = {}
    for j in range(W):
        for k in range(D):
            for m in range(B):
                w[(j, k, m)] = pulp.LpVariable(f"w_{j}_{k}_{m}", lowBound=0, upBound=1)

    # ---- z_jkm: SOS2 段选择 ----
    z = {}
    for j in range(W):
        for k in range(D):
            for m in range(B - 1):
                z[(j, k, m)] = pulp.LpVariable(f"z_{j}_{k}_{m}", cat=pulp.LpBinary)

    # ============ 约束 ============

    # C1: 每个供电所唯一分配
    station_demand = demand.sum(axis=1)  # shape (S,)
    for i in range(S):
        if i in fixed_stations:
            continue  # 固定供电所不需约束
        terms = [x[(j, i)] for j, _i in active_pairs if _i == i]
        if terms:
            prob += pulp.lpSum(terms) == 1, f"assign_{i}"

    # C2: x_ij ≤ y_j
    for j, i in active_pairs:
        prob += x[(j, i)] <= y[j], f"open_{j}_{i}"

    # C3: 必须开启的库房
    for j in must_open:
        prob += y[j] == 1, f"must_open_{j}"

    # C4: 需求聚合 u_jk = Σ_i λ_ik * x_ij
    for j in range(W):
        reachable_i = [i for _j, i in active_pairs if _j == j]
        for k in range(D):
            terms = []
            for i in reachable_i:
                if demand[i, k] > 0:
                    terms.append(demand[i, k] * x[(j, i)])
            # 加上固定供电所的需求
            for i, fj in fixed_stations.items():
                if fj == j and demand[i, k] > 0:
                    terms.append(demand[i, k])

            if terms:
                prob += u[(j, k)] == pulp.lpSum(terms), f"demand_{j}_{k}"
            else:
                prob += u[(j, k)] == 0, f"demand_zero_{j}_{k}"

    # C5: SOS2 约束 for sqrt
    for j in range(W):
        for k in range(D):
            # Σ_m w_jkm = 1
            prob += pulp.lpSum(w[(j, k, m)] for m in range(B)) == 1, f"sos2_sum_{j}_{k}"

            # u_jk = Σ_m w_jkm * bp[m]
            prob += u[(j, k)] == pulp.lpSum(
                w[(j, k, m)] * breakpoints[k, m] for m in range(B)
            ), f"sos2_u_{j}_{k}"

            # v_jk = Σ_m w_jkm * sqrt(bp[m])
            prob += v[(j, k)] == pulp.lpSum(
                w[(j, k, m)] * sqrt_vals[k, m] for m in range(B)
            ), f"sos2_v_{j}_{k}"

            # SOS2 adjacency: w_0 ≤ z_0, w_m ≤ z_{m-1} + z_m, w_{B-1} ≤ z_{B-2}
            prob += w[(j, k, 0)] <= z[(j, k, 0)], f"sos2_adj_{j}_{k}_0"
            for m in range(1, B - 1):
                prob += (w[(j, k, m)] <= z[(j, k, m - 1)] + z[(j, k, m)],
                         f"sos2_adj_{j}_{k}_{m}")
            prob += w[(j, k, B - 1)] <= z[(j, k, B - 2)], f"sos2_adj_{j}_{k}_{B-1}"

            # Σ z = 1
            prob += pulp.lpSum(z[(j, k, m)] for m in range(B - 1)) == 1, f"sos2_z_{j}_{k}"

    return prob, x, y, u, v, station_demand


def _extract_solution(
    prob: pulp.LpProblem, x: dict, y: dict, _u: dict, _v: dict,
    active_pairs: List, fixed_stations: dict,
    data: dict, station_demand: np.ndarray,
) -> dict:
    """从求解后的 PuLP 模型中提取解。"""
    status = pulp.LpStatus[prob.status]
    if status != 'Optimal':
        logger.warning(f"[MILP] 求解状态: {status}, 尝试提取当前最优解")

    S = len(data['station_codes'])
    W = len(data['wh_codes'])
    D = len(data['dev_codes'])
    demand = data['demand']
    dist = data['distance']
    fixed_cost = data['fixed_cost']
    trans_cost = data['trans_cost']
    holding_cost = data['holding_cost']

    # 提取 y_j
    opened = np.zeros(W, dtype=bool)
    for j in range(W):
        if j in y:
            opened[j] = pulp.value(y[j]) > 0.5

    # 提取 x_ij
    assignments = [-1] * S
    for i, j in fixed_stations.items():
        assignments[i] = j
    for (j, i), var in x.items():
        if pulp.value(var) > 0.5:
            assignments[i] = j

    # 校验：全部供电所已分配
    unassigned = [i for i in range(S) if assignments[i] < 0]
    if unassigned:
        codes = [data['station_codes'][i] for i in unassigned]
        raise RuntimeError(
            f"[MILP] {len(unassigned)} 个供电所未分配库房: {codes[:10]}"
            f"{'...' if len(unassigned) > 10 else ''}"
        )

    # 计算 Z₁ 和 Z₂
    from backend.algorithm.warehouse_layout.config import (
        TRANSPORT_UNIT_PRICE, R_w as _R_w, L_w as _L_w, z_w as _z_w,
    )

    wh_demand = np.zeros((W, D))
    for i in range(S):
        wi = assignments[i]
        if wi >= 0:
            wh_demand[wi] += demand[i]

    # Z₁
    z1 = 0.0
    for j in range(W):
        if opened[j]:
            z1 += fixed_cost[j]
    for j in range(W):
        total_d = wh_demand[j].sum()
        z1 += trans_cost[j] * total_d
    for j in range(W):
        for k in range(D):
            if wh_demand[j, k] > 0:
                z1 += holding_cost[k] * _R_w * wh_demand[j, k] / 2.0
                z1 += holding_cost[k] * _z_w * np.sqrt((_R_w + _L_w) * wh_demand[j, k])

    # Z₂
    total_demand = demand.sum()
    z2 = 0.0
    if total_demand > 0:
        for i in range(S):
            wi = assignments[i]
            if wi >= 0:
                z2 += station_demand[i] * dist[wi, i]
        z2 /= total_demand

    # 构建 mapping
    wh_codes = data['wh_codes']
    station_codes = data['station_codes']
    mapping = []
    for i in range(S):
        wi = assignments[i]
        if wi >= 0:
            mapping.append({
                'station_code': station_codes[i],
                'wh_code': wh_codes[wi],
            })

    return {
        'status': status,
        'Z1': float(z1),
        'Z2': float(z2),
        'opened_wh': [wh_codes[j] for j in range(W) if opened[j]],
        'n_opened': int(opened.sum()),
        'assignments': assignments,
        'opened': opened,
        'mapping': mapping,
    }


# ================================================================
# 公开接口
# ================================================================


def solve_distance_milp(data: dict, pairs: List[Tuple[int, int]]) -> dict:
    """求解纯距离最小化 MILP (Z₂, 纯线性)。

    目标: min Σ_{(j,i)∈P} D_i * d_ij * x_ij
    """
    logger.info("[MILP-距离] 构建模型...")
    active_pairs, fixed_stations, must_open = _reduce_pairs(data, pairs)

    S = len(data['station_codes'])
    W = len(data['wh_codes'])
    demand = data['demand']
    dist = data['distance']
    station_demand = demand.sum(axis=1)
    total_demand = station_demand.sum()

    prob = pulp.LpProblem("WL_Distance", pulp.LpMinimize)

    # 变量
    y = {}
    for j in range(W):
        y[j] = pulp.LpVariable(f"y_{j}", cat=pulp.LpBinary)
    x = {}
    for j, i in active_pairs:
        x[(j, i)] = pulp.LpVariable(f"x_{j}_{i}", cat=pulp.LpBinary)

    # C1: 分配
    for i in range(S):
        if i in fixed_stations:
            continue
        terms = [x[(j, _i)] for j, _i in active_pairs if _i == i]
        if terms:
            prob += pulp.lpSum(terms) == 1, f"assign_{i}"

    # C2: x ≤ y
    for j, i in active_pairs:
        prob += x[(j, i)] <= y[j], f"open_{j}_{i}"

    # C3: 必须开启
    for j in must_open:
        prob += y[j] == 1, f"must_open_{j}"

    # 目标: min Z₂
    obj_terms = []
    for j, i in active_pairs:
        obj_terms.append(station_demand[i] * dist[j, i] * x[(j, i)])
    # 固定供电所的距离贡献
    for i, j in fixed_stations.items():
        obj_terms.append(station_demand[i] * dist[j, i])

    prob += pulp.lpSum(obj_terms), "Z2_objective"

    logger.info(f"[MILP-距离] 变量: 二进制 {len(x) + len(y)}, 求解中...")
    prob.solve(pulp.PULP_CBC_CMD(msg=False, timeLimit=120, gapRel=0.001))

    result = _extract_solution(prob, x, y, {}, {},
                                active_pairs, fixed_stations, data, station_demand)
    result['label'] = 'DIST_ONLY'
    logger.info(f"[MILP-距离] {result['status']}: Z₁={result['Z1']:,.0f}, "
                f"Z₂={result['Z2']:.1f}km, 库房 {result['n_opened']}个")
    return result


def solve_cost_milp(data: dict, pairs: List[Tuple[int, int]]) -> dict:
    """求解纯成本最小化 MILP (Z₁, 含 SOS2 分段线性)。

    目标: min Σ_j f_j*y_j + Σ_j s_j*λ_j + Σ_j Σ_k h_k*R_w*λ_jk/2 + Σ_j Σ_k H_k*v_jk
    """
    logger.info("[MILP-成本] 构建模型(含 SOS2)...")
    active_pairs, fixed_stations, must_open = _reduce_pairs(data, pairs)

    D = len(data['dev_codes'])
    demand = data['demand']
    fixed_cost = data['fixed_cost']
    trans_cost = data['trans_cost']
    holding_cost = data['holding_cost']

    total_demand_per_k = demand.sum(axis=0)
    breakpoints, sqrt_vals = _build_sos2_breakpoints(total_demand_per_k)

    prob, x, y, u, v, station_demand = _build_base_model(
        data, active_pairs, fixed_stations, must_open, breakpoints, sqrt_vals,
    )

    W = len(data['wh_codes'])

    # 目标: min Z₁
    protection = R_w + L_w
    obj_terms = []

    # 固定成本
    for j in range(W):
        obj_terms.append(fixed_cost[j] * y[j])

    # 上游运输
    for j in range(W):
        obj_terms.append(trans_cost[j] * pulp.lpSum(u[(j, k)] for k in range(D)))

    # 周转库存: h_k * R_w * u_jk / 2
    # 安全库存: h_k * z_w * sqrt(protection) * v_jk
    for j in range(W):
        for k in range(D):
            h = holding_cost[k]
            if h > 0:
                obj_terms.append(h * R_w * u[(j, k)] / 2.0)
                obj_terms.append(h * z_w * np.sqrt(protection) * v[(j, k)])

    prob += pulp.lpSum(obj_terms), "Z1_objective"

    logger.info(f"[MILP-成本] 变量: 二进制 {len(x) + len(y)}, 求解中...")
    prob.solve(pulp.PULP_CBC_CMD(msg=False, timeLimit=120, gapRel=0.001))

    result = _extract_solution(prob, x, y, u, v,
                                active_pairs, fixed_stations, data, station_demand)
    result['label'] = 'COST_ONLY'
    logger.info(f"[MILP-成本] {result['status']}: Z₁={result['Z1']:,.0f}, "
                f"Z₂={result['Z2']:.1f}km, 库房 {result['n_opened']}个")
    return result


def solve_constrained_milp(
    data: dict, pairs: List[Tuple[int, int]], d_bound: float, label: str = ''
) -> dict:
    """求解带距离约束的成本最小化 MILP。

    min Z₁  s.t.  Z₂ ≤ d_bound
    """
    logger.info(f"[MILP-约束] 构建模型, D_max={d_bound:.1f}km...")
    active_pairs, fixed_stations, must_open = _reduce_pairs(data, pairs)

    S = len(data['station_codes'])
    D = len(data['dev_codes'])
    W = len(data['wh_codes'])
    demand = data['demand']
    dist = data['distance']
    fixed_cost = data['fixed_cost']
    trans_cost = data['trans_cost']
    holding_cost = data['holding_cost']
    station_demand = demand.sum(axis=1)
    total_demand = station_demand.sum()

    total_demand_per_k = demand.sum(axis=0)
    breakpoints, sqrt_vals = _build_sos2_breakpoints(total_demand_per_k)

    prob, x, y, u, v, _sd = _build_base_model(
        data, active_pairs, fixed_stations, must_open, breakpoints, sqrt_vals,
    )

    # 距离约束: Σ D_i * d_ij * x_ij ≤ d_bound * total_demand
    dist_terms = []
    for j, i in active_pairs:
        dist_terms.append(station_demand[i] * dist[j, i] * x[(j, i)])
    for i, j in fixed_stations.items():
        dist_terms.append(station_demand[i] * dist[j, i])

    prob += pulp.lpSum(dist_terms) <= d_bound * total_demand, "dist_constraint"

    # 目标: Z₁ (同 solve_cost_milp)
    protection = R_w + L_w
    obj_terms = []
    for j in range(W):
        obj_terms.append(fixed_cost[j] * y[j])
    for j in range(W):
        obj_terms.append(trans_cost[j] * pulp.lpSum(u[(j, k)] for k in range(D)))
    for j in range(W):
        for k in range(D):
            h = holding_cost[k]
            if h > 0:
                obj_terms.append(h * R_w * u[(j, k)] / 2.0)
                obj_terms.append(h * z_w * np.sqrt(protection) * v[(j, k)])

    prob += pulp.lpSum(obj_terms), "Z1_objective"

    logger.info(f"[MILP-约束] 求解中...")
    prob.solve(pulp.PULP_CBC_CMD(msg=False, timeLimit=120, gapRel=0.001))

    result = _extract_solution(prob, x, y, u, v,
                                active_pairs, fixed_stations, data, station_demand)
    result['label'] = label
    logger.info(f"[MILP-约束] {result['status']}: Z₁={result['Z1']:,.0f}, "
                f"Z₂={result['Z2']:.1f}km, 库房 {result['n_opened']}个")
    return result
