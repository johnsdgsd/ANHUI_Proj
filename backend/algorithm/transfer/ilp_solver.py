"""
调拨 ILP 求解器 — 纯数学建模与求解，不含业务逻辑。

运输问题：供应点(可调出富余) → 需求点(缺货缺口)，最小化总运输距离。

模型（《调拨算法设计方案最新版.md》场景一）:
    集合: S 供应点 (n1), D 需求点 (n2)
    参数: s_i 可调出富余量; d_j 缺货缺口; c_ij 运输距离
    变量: x_ij ∈ Z≥0, 从供应点 i 调拨至需求点 j 的件数
    目标: min Σ_{i∈S} Σ_{j∈D} c_ij · x_ij
    情形1 (Σs ≥ Σd): 富余足够, 需求全满足, 调出不超富余
        Σ_j x_ij ≤ s_i, ∀i;   Σ_i x_ij = d_j, ∀j
    情形2 (Σs < Σd): 富余不足, 富余全调出, 调入不超缺口
        Σ_j x_ij = s_i, ∀i;   Σ_i x_ij ≤ d_j, ∀j

求解器: PuLP + CBC（参考 DelivPlanV4/ilp_solver.py 风格）。
距离数据在数据准备阶段已处理，理论上无不可达对；若出现 NaN/Inf 直接报错。

纯函数单问题：每次调用求解一个设备码的调拨，业务层按设备码循环调用。
"""
import logging
import time

import numpy as np
import pulp

from backend.algorithm.transfer.config import ILP_TIMEOUT_SEC, ILP_GAP

logger = logging.getLogger(__name__)


def solve_transfer(
    supply,
    demand,
    cost,
    supply_ids=None,
    demand_ids=None,
    timeout_sec=None,
    gap=None,
):
    """求解单个设备码的调拨运输问题。

    Args:
        supply: array-like (n1,), 供应点可调出富余量 s_i (>0 参与)
        demand: array-like (n2,), 需求点缺货缺口 d_j (>0 参与)
        cost: array-like (n1, n2), 运输距离 c_ij；不允许 NaN/Inf（数据准备阶段已保证可达）
        supply_ids: list (n1,), 供应点标签（如组织编码），默认 0..n1-1
        demand_ids: list (n2,), 需求点标签，默认 0..n2-1
        timeout_sec: CBC 求解超时秒数，默认 ILP_TIMEOUT_SEC
        gap: CBC 相对最优性 gap，默认 ILP_GAP

    Returns:
        dict:
            status: 'Optimal' / 'Infeasible' / 'Unbounded' / 其他 pulp 状态
            mode:   'S>=D' 富余≥缺口(需求全满足) | 'S<D' 富余<缺口(富余全调出)
            x:      [(src_id, tgt_id, qty, dist), ...] 非零调拨
            total_dist: 总运输距离
            total_supply: Σs_i (活跃供应点)
            total_demand: Σd_j (活跃需求点)
            supply_used: {src_id: 实际调出量}
            demand_satisfied: {tgt_id: 实际调入量}
            leftover_supply: 情形1 未调出富余
            unmet_demand:    情形2 未满足缺口
            solve_time: 求解耗时(秒)

    Raises:
        ValueError: 距离矩阵含 NaN/Inf（不可达对），或输入维度不匹配
    """
    t_start = time.time()

    supply = np.asarray(supply, dtype=float).reshape(-1)
    demand = np.asarray(demand, dtype=float).reshape(-1)
    cost = np.asarray(cost, dtype=float)

    n1, n2 = len(supply), len(demand)
    if cost.shape != (n1, n2):
        raise ValueError(
            f"cost 维度 ({cost.shape}) 与 supply({n1})×demand({n2}) 不匹配")

    if supply_ids is None:
        supply_ids = list(range(n1))
    if demand_ids is None:
        demand_ids = list(range(n2))
    if len(supply_ids) != n1 or len(demand_ids) != n2:
        raise ValueError("supply_ids/demand_ids 长度必须与 supply/demand 一致")

    # ---- 1. 距离质量检查：不可达对直接报错（数据准备阶段应已处理） ----
    if np.isnan(cost).any() or np.isinf(cost).any():
        bad = np.argwhere(np.isnan(cost) | np.isinf(cost))
        sample = [(int(i), int(j)) for i, j in bad[:5]]
        raise ValueError(
            f"距离矩阵含 NaN/Inf（不可达对），共 {len(bad)} 个，示例 {sample}。"
            f"不可达应在数据准备阶段处理，不允许进入求解。")

    # ---- 2. 活跃点过滤（非正不参与） ----
    act_s = [i for i in range(n1) if supply[i] > 0]
    act_d = [j for j in range(n2) if demand[j] > 0]

    if not act_s or not act_d:
        logger.warning(
            f"[调拨ILP] 无活跃供应点({len(act_s)})或无需求点({len(act_d)})，返回空方案")
        return {
            'status': 'Optimal', 'mode': None,
            'x': [], 'total_dist': 0.0,
            'total_supply': 0.0, 'total_demand': 0.0,
            'supply_used': {}, 'demand_satisfied': {},
            'leftover_supply': float(supply[act_s].sum()) if act_s else 0.0,
            'unmet_demand': float(demand[act_d].sum()) if act_d else 0.0,
            'solve_time': time.time() - t_start,
        }

    total_supply = float(sum(supply[i] for i in act_s))
    total_demand = float(sum(demand[j] for j in act_d))
    mode = 'S>=D' if total_supply >= total_demand else 'S<D'

    logger.info(
        f"[调拨ILP] 求解 {len(act_s)} 供应点 × {len(act_d)} 需求点, "
        f"Σs={total_supply:.0f}, Σd={total_demand:.0f}, 情形{mode}")

    # ---- 3. 建模 ----
    prob = pulp.LpProblem("Transfer", pulp.LpMinimize)
    x = {}
    for i in act_s:
        for j in act_d:
            x[(i, j)] = pulp.LpVariable(f"x_{i}_{j}", lowBound=0, cat=pulp.LpInteger)

    # 目标: min ΣΣ c_ij·x_ij
    prob += pulp.lpSum(cost[i, j] * x[(i, j)] for i in act_s for j in act_d), "total_dist"

    # 约束
    if mode == 'S>=D':
        # 情形1: 需求全满足, 调出不超富余
        for i in act_s:
            prob += pulp.lpSum(x[(i, j)] for j in act_d) <= supply[i], f"sup_{i}"
        for j in act_d:
            prob += pulp.lpSum(x[(i, j)] for i in act_s) == demand[j], f"dmd_{j}"
    else:
        # 情形2: 富余全调出, 调入不超缺口
        for i in act_s:
            prob += pulp.lpSum(x[(i, j)] for j in act_d) == supply[i], f"sup_{i}"
        for j in act_d:
            prob += pulp.lpSum(x[(i, j)] for i in act_s) <= demand[j], f"dmd_{j}"

    # ---- 4. 求解 ----
    timeout = timeout_sec or ILP_TIMEOUT_SEC
    gap_val = gap if gap is not None else ILP_GAP
    solver = pulp.PULP_CBC_CMD(
        msg=False,
        options=[f'sec={timeout}', f'ratioGap={gap_val}']
    )
    status = prob.solve(solver)
    status_str = pulp.LpStatus[status]
    logger.info(f"[调拨ILP] 求解完成: {status_str}, 目标值="
                f"{pulp.value(prob.objective):.0f}, 耗时{time.time() - t_start:.2f}s")

    if status_str not in ('Optimal', 'Feasible'):
        logger.error(f"[调拨ILP] 求解失败: {status_str}")
        return {
            'status': status_str, 'mode': mode,
            'x': [], 'total_dist': None,
            'total_supply': total_supply, 'total_demand': total_demand,
            'supply_used': {}, 'demand_satisfied': {},
            'leftover_supply': None, 'unmet_demand': None,
            'solve_time': time.time() - t_start,
        }

    # ---- 5. 提取结果 ----
    x_list = []
    supply_used = {}
    demand_satisfied = {}
    total_dist = 0.0
    for i in act_s:
        used = 0.0
        for j in act_d:
            qty = float(pulp.value(x[(i, j)]))
            if qty > 0.001:
                x_list.append((supply_ids[i], demand_ids[j], qty, float(cost[i, j])))
                total_dist += qty * float(cost[i, j])
                used += qty
                demand_satisfied[demand_ids[j]] = demand_satisfied.get(demand_ids[j], 0.0) + qty
        supply_used[supply_ids[i]] = used

    result = {
        'status': status_str,
        'mode': mode,
        'x': x_list,
        'total_dist': total_dist,
        'total_supply': total_supply,
        'total_demand': total_demand,
        'supply_used': supply_used,
        'demand_satisfied': demand_satisfied,
        'leftover_supply': total_supply - sum(supply_used.values()) if mode == 'S>=D' else 0.0,
        'unmet_demand': total_demand - sum(demand_satisfied.values()) if mode == 'S<D' else 0.0,
        'solve_time': time.time() - t_start,
    }

    logger.info(
        f"[调拨ILP] 方案: {len(x_list)} 条调拨, 总量 {sum(q for *_, q, _ in x_list):.0f} 件, "
        f"总距离 {total_dist:.0f}, "
        f"剩余富余 {result['leftover_supply']:.0f} / 未满足缺口 {result['unmet_demand']:.0f}")
    return result


if __name__ == '__main__':
    # 独立自测：两种情形的结果校验
    import io
    import sys
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s',
                        stream=io.StringIO())
    logger = logging.getLogger(__name__)

    def _assert_close(actual, expected, msg):
        assert abs(actual - expected) < 1e-6, f"{msg}: {actual} != {expected}"

    print("=" * 50)
    print("自测 情形1: 富余≥缺口 → 需求全满足")
    # supply=[10,5], demand=[4,6]
    # 最优: A→C=4(4×1), A→D=1(1×3), B→D=5(5×2) → dist=17, A剩5, C/D全满足
    r1 = solve_transfer([10, 5], [4, 6],
                        [[1, 3], [4, 2]],
                        supply_ids=['A', 'B'], demand_ids=['C', 'D'])
    assert r1['status'] == 'Optimal', r1['status']
    assert r1['mode'] == 'S>=D', r1['mode']
    _assert_close(sum(q for *_, q, _ in r1['x']), 10, "情形1 总调拨量")
    _assert_close(r1['demand_satisfied']['C'], 4, "C 满")
    _assert_close(r1['demand_satisfied']['D'], 6, "D 满")
    _assert_close(r1['total_dist'], 17, "情形1 总距离")
    _assert_close(r1['leftover_supply'], 5, "情形1 剩余富余")
    assert r1['unmet_demand'] == 0.0
    print(f"  OK: x={r1['x']}, total_dist={r1['total_dist']}, leftover={r1['leftover_supply']}")

    print("=" * 50)
    print("自测 情形2: 富余<缺口 → 富余全调出")
    # supply=[3,2], demand=[4,5], 最优 x[0,0]=3,x[1,1]=2, dist=3*1+2*1=5
    r2 = solve_transfer([3, 2], [4, 5],
                        [[1, 2], [3, 1]],
                        supply_ids=['A', 'B'], demand_ids=['C', 'D'])
    assert r2['status'] == 'Optimal', r2['status']
    assert r2['mode'] == 'S<D', r2['mode']
    _assert_close(sum(q for *_, q, _ in r2['x']), 5, "情形2 总调拨量")
    _assert_close(r2['supply_used']['A'], 3, "A 全调出")
    _assert_close(r2['supply_used']['B'], 2, "B 全调出")
    _assert_close(r2['total_dist'], 5, "情形2 总距离")
    _assert_close(r2['unmet_demand'], 4, "情形2 未满足缺口")
    assert r2['leftover_supply'] == 0.0
    print(f"  OK: x={r2['x']}, total_dist={r2['total_dist']}, unmet={r2['unmet_demand']}")

    print("=" * 50)
    print("自测 不可达对 → 应抛 ValueError")
    try:
        solve_transfer([3, 2], [4, 5], [[1, 2], [float('inf'), 1]])
        raise AssertionError("应抛出 ValueError")
    except ValueError as e:
        print(f"  OK: {e}")

    print("=" * 50)
    print("自测 无活跃点 → 返回空方案")
    r4 = solve_transfer([0, 0], [4, 5], [[1, 2], [3, 1]])
    assert r4['x'] == [] and r4['status'] == 'Optimal'
    print("  OK: 空方案返回")
    print("\n全部自测通过 [OK]")
