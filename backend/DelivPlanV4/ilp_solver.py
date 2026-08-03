"""
Stage 2: 集合划分整数线性规划 (ILP)

将候选路径分配给车辆，最小化总运输成本，同时满足:
    - 每个 demand_unit 恰好被一条路径覆盖（集合划分）
    - 每车型使用数不超过日配额
    - 路径装载量不超过分配车型容量

求解器: PuLP + CBC
"""

import logging
import time
from collections import defaultdict

import pulp

from backend.DelivPlanV4.config import ILP_TIMEOUT_SEC


def solve_set_partition_ilp(candidates, demand_units, vehicle_config, ve_unit_price,
                            no_small_first=False, load_rate_penalty=0.0):
    """
    求解集合划分 ILP，返回 best_sol 格式的路线列表。

    Args:
        candidates: list[dict], Stage 1 输出的候选路径
        demand_units: list[tuple], [(unit_id, node_id, boxes), ...]
        vehicle_config: list[dict], 按 cap 升序的车辆配置 [{type, cap, daily_max}, ...]
        ve_unit_price: list, 各车型单价 (索引 = type - 1)
        no_small_first: bool, 是否跳过小车优先策略。True 时全车型单轮求解，
                        False 时保持三轮逐步放宽（默认）。
        load_rate_penalty: float, 低装载率惩罚系数。0=不惩罚（默认）。
                           >0 时 cost *= (1 + penalty×(1-load_rate)²)，推动合并低载路线。

    Returns:
        list[dict]: best_sol 格式:
            [{'vehicle_type': int, 'deliveries': [(node_id, boxes), ...]}, ...]

    Raises:
        ValueError: ILP 无可行解时
    """
    t_start = time.time()
    num_candidates = len(candidates)
    num_units = len(demand_units)
    num_types = len(vehicle_config)

    logging.info(
        f"[Stage2] 开始建ILP: {num_candidates}条候选路径 × {num_types}种车型, "
        f"覆盖{num_units}个需求单元"
    )

    # ---- 1. 查找表 ----
    uid_to_info = {uid: (nid, boxes) for uid, nid, boxes in demand_units}

    # ---- 2. 预计算覆盖矩阵 ----
    coverage = {}
    for r in range(num_candidates):
        unit_ids = candidates[r]['unit_ids']
        for uid in unit_ids:
            coverage[(r, uid)] = True

    # ---- 3. 预计算每条路线的最小可行车型 ----
    # 优先选用小车：先尝试只开放最小车型，配额不够时逐步放开大车型
    from backend.DelivPlanV4.config import HEFEI_DEDICATED_LOAD_RATE
    route_min_type = {}  # {r: min_t}，每条路线能装下的最小车型索引
    for r in range(num_candidates):
        load = candidates[r]['load']
        for t in range(num_types):
            if vehicle_config[t]['cap'] >= load - 0.01:
                route_min_type[r] = t
                break
        # 所有车型都装不下 → 不设 min_type，该路线不会进入 compat

    # ---- 4. 多轮求解：逐步放开车型限制 ----
    # extra_types=0: 只允许最小车型（最优装载率）
    # extra_types=1: 允许最小+次小车型（小型车配额不够的兜底）
    # extra_types=num_types-1: 允许全部车型（最终兜底，等价于原始行为）
    # no_small_first=True: 跳过前两轮，直接全车型单轮求解
    last_error = None
    last_error_detail = ""
    x, prob, status_str, obj_val = None, None, None, None

    extra_type_passes = [0, 1, num_types - 1]  # 默认：三轮逐步放宽
    if no_small_first:
        extra_type_passes = [num_types - 1]    # 跳过小车优先，直接全车型

    for pass_idx, extra_types in enumerate(extra_type_passes):
        # ---- 4a. 构建本轮兼容矩阵 ----
        compat = {}
        compat_count_per_type = defaultdict(int)
        constraint3_rejected = 0

        for r in range(num_candidates):
            min_t = route_min_type.get(r)
            if min_t is None:
                continue  # 所有车型都装不下，跳过
            # 本轮允许的车型范围: [min_t, min(min_t + extra_types, num_types - 1)]
            max_t = min(min_t + extra_types, num_types - 1)
            load = candidates[r]['load']

            for t in range(min_t, max_t + 1):
                cap = vehicle_config[t]['cap']
                if cap < load - 0.01:
                    continue

                # ---- V4 约束3: 合肥独占路线装载率 <70% 必须混装远距节点 ----
                # 含义：如果路线只含合肥四库房（无远距节点）且装不满 70%，则拒绝，
                # 强制要求搭配 >150km 的远距节点来提高装载率
                has_hefei = candidates[r].get('has_hefei', False)
                has_far = candidates[r].get('has_far_node', False)
                if has_hefei and not has_far and cap > 0:
                    load_rate = load / cap
                    if load_rate < HEFEI_DEDICATED_LOAD_RATE - 0.001:
                        constraint3_rejected += 1
                        continue

                compat[(r, t)] = True
                compat_count_per_type[t] += 1

        # 日志：本轮车型限制说明
        total_passes = len(extra_type_passes)
        if no_small_first:
            pass_desc = "全车型一次性求解"
        else:
            pass_desc = [
                f"仅最小车型",
                f"允许2种车型",
                f"允许全部车型"
            ][pass_idx]
        logging.info(
            f"[Stage2] Pass {pass_idx + 1}/{total_passes} ({pass_desc}): "
            f"兼容矩阵 {len(compat)} 个(r,t)组合, " +
            " | ".join(
                f"车型{vehicle_config[t]['type']}:{compat_count_per_type.get(t, 0)}条路径"
                for t in range(num_types)
            )
        )

        if constraint3_rejected > 0:
            logging.info(
                f"[Stage2] 约束3: 排除 {constraint3_rejected} 个合肥混合满载(r,t)组合 "
                f"(阈值={HEFEI_DEDICATED_LOAD_RATE*100:.0f}%)"
            )

        if not compat:
            last_error_detail = "无任何路径-车型兼容组合"
            continue

        # ---- 4b. 构建 ILP ----
        prob = pulp.LpProblem("DelivPlanV4_SetPartition", pulp.LpMinimize)

        # 决策变量: x[(r, t)] ∈ {0, 1}
        x = {}
        for (r, t), _ in compat.items():
            var_name = f"x_{r}_{t}"
            x[(r, t)] = pulp.LpVariable(var_name, cat=pulp.LpBinary)

        logging.info(f"[Stage2] 决策变量: {len(x)}个")

        # 目标函数: min Σ cost_r × price × (1 + ε × cap/max_cap) × penalty × x
        # ε 极小，仅用于打破平局：当 base_cost×price 相同时优先选小车
        # penalty: 低装载率惩罚 (1 + λ×(1-lr)²)，推动合并、减少低载路线
        obj_terms = []
        cost_samples = []
        max_cap = max(c['cap'] for c in vehicle_config)
        EPS = 1e-8  # 容量平局打破系数
        LRP = load_rate_penalty  # 缩写
        for (r, t) in compat:
            vt = vehicle_config[t]['type']
            price = float(ve_unit_price[vt - 1])
            cap = vehicle_config[t]['cap']
            load = candidates[r]['load']
            load_rate = load / cap if cap > 0 else 0
            penalty_factor = 1.0 + LRP * (1.0 - load_rate) ** 2
            cost_coef = (candidates[r]['base_cost'] * price *
                         (1 + EPS * cap / max_cap) * penalty_factor)
            obj_terms.append(cost_coef * x[(r, t)])
            if len(cost_samples) < 5:
                cost_samples.append((r, t, load, cap, cost_coef))
        prob += pulp.lpSum(obj_terms), "total_cost"

        logging.info(f"[Stage2] 目标系数样本 (前5个):")
        for r, t, load, cap, coeff in cost_samples:
            logging.info(f"  r={r} t={vehicle_config[t]['type']} load={load:.0f} cap={cap:.0f} coeff={coeff:.1f}")

        # 约束1: 每个 demand_unit 恰好覆盖一次
        coverable_units = 0
        coverage_error = None
        for u in range(num_units):
            covering = []
            for r in range(num_candidates):
                if coverage.get((r, u)):
                    for t in range(num_types):
                        if compat.get((r, t)):
                            covering.append(x[(r, t)])
            if covering:
                prob += pulp.lpSum(covering) == 1, f"cover_u{u}"
                coverable_units += 1
            else:
                uid, nid, boxes = demand_units[u]
                coverage_error = (
                    f"demand_unit {u} (节点{nid}, {boxes:.0f}箱) "
                    f"无任何候选路径覆盖，当前车辆数无法满足全部需求"
                )

        if coverage_error:
            last_error_detail = coverage_error
            logging.warning(f"[Stage2] Pass {pass_idx + 1}: {coverage_error}，扩大车型范围重试")
            continue

        # 约束2: 每车型使用数不超过日配额
        for t in range(num_types):
            type_vars = []
            for r in range(num_candidates):
                if compat.get((r, t)):
                    type_vars.append(x[(r, t)])
            if type_vars:
                quota = vehicle_config[t]['daily_max']
                prob += pulp.lpSum(type_vars) <= quota, f"quota_type{vehicle_config[t]['type']}"

        logging.info(
            f"[Stage2] ILP规模: 变量={len(x)}, "
            f"覆盖约束={coverable_units}/{num_units}, 配额约束={num_types}"
        )

        # ---- 4c. 求解 ----
        logging.info(f"[Stage2] 开始CBC求解 (超时{ILP_TIMEOUT_SEC}s, gap=1%)...")
        solver = pulp.PULP_CBC_CMD(
            msg=False,
            options=[f'sec={ILP_TIMEOUT_SEC}', 'ratioGap=0.01']
        )
        status = prob.solve(solver)
        status_str = pulp.LpStatus[status]

        obj_val = pulp.value(prob.objective) if status_str in ('Optimal', 'Feasible') else None
        if obj_val is not None:
            elapsed = time.time() - t_start
            logging.info(
                f"[Stage2] Pass {pass_idx + 1} 求解成功: {status_str}, "
                f"目标值={obj_val:.0f}, 耗时{elapsed:.1f}s"
            )
            break  # 求解成功，退出循环
        else:
            last_error_detail = f"ILP状态={status_str}"
            logging.warning(
                f"[Stage2] Pass {pass_idx + 1} 求解失败: {status_str}，扩大车型范围重试"
            )
            continue

    # ---- 5. 所有轮次均失败 → 报错 ----
    if status_str not in ('Optimal', 'Feasible'):
        total_dmd = sum(boxes for _, _, boxes in demand_units)
        total_veh_cap = sum(c['cap'] * c['daily_max'] for c in vehicle_config)
        if total_dmd > total_veh_cap:
            shortage = total_dmd - total_veh_cap
            raise ValueError(
                f"【运力不足，请增加车辆】总需求 {total_dmd:.0f} 箱 > "
                f"总运力 {total_veh_cap:.0f} 箱, 缺口 {shortage:.0f} 箱, "
                f"{last_error_detail}"
            )
        else:
            raise ValueError(
                f"【运力不足，请增加车辆】ILP无可行解 ({last_error_detail}), "
                f"总需求={total_dmd:.0f}箱 ≤ 总运力={total_veh_cap:.0f}箱, "
                f"但角度/距离/站点数约束导致无法覆盖全部需求, "
                f"请增加车辆或放宽约束后重试"
            )

    # ---- 6. 提取结果 → best_sol ----
    best_sol = []
    selected_routes = []
    total_delivered_boxes = 0.0
    type_usage = defaultdict(int)

    for (r, t), var in x.items():
        if pulp.value(var) < 0.5:
            continue

        cand = candidates[r]
        vehicle_type = vehicle_config[t]['type']

        node_deliv = defaultdict(float)
        for uid in cand['unit_ids']:
            nid, boxes = uid_to_info[uid]
            node_deliv[nid] += boxes

        deliveries = []
        for nid in cand['sequence']:
            if nid in node_deliv and node_deliv[nid] > 0.001:
                load_nid = round(node_deliv[nid], 6)
                deliveries.append((nid, load_nid))
                total_delivered_boxes += load_nid

        if deliveries:
            best_sol.append({
                'vehicle_type': vehicle_type,
                'deliveries': deliveries,
            })
            type_usage[vehicle_type] += 1
            selected_routes.append((r, t, vehicle_type, cand['load'], cand['distance'], cand['base_cost']))

    # 详细路线日志
    logging.info(f"[Stage2] 选中 {len(best_sol)} 条路线, 配送总量={total_delivered_boxes:.0f}箱:")
    used_demand_units = set()
    for i, (r_id, t_id, vt, load, dist, cost) in enumerate(selected_routes):
        cand = candidates[r_id]
        cap = vehicle_config[t_id]['cap']
        price = float(ve_unit_price[vt - 1])
        rate = load / cap * 100 if cap > 0 else 0
        stops = "→".join(str(n) for n in cand['sequence'])
        units_detail = []
        for uid in cand['unit_ids']:
            used_demand_units.add(uid)
            nid, boxes = uid_to_info[uid]
            units_detail.append(f"u{uid}(n{nid},{boxes:.0f}箱)")
        logging.info(
            f"  [{i+1}] 车型{vt}(容{cap:.0f}) 站点[{stops}] "
            f"装载{load:.0f}箱 满载率{rate:.1f}% 距离{dist:.0f} "
            f"成本={cost:.0f}×{price:.2f}={cost*price:.0f} "
            f"覆盖unit: {'+'.join(units_detail)}"
        )

    # 类型使用统计
    logging.info(
        f"[Stage2] 车型使用: " +
        " | ".join(
            f"车型{t['type']}:{type_usage[t['type']]}/{t['daily_max']}辆"
            for t in vehicle_config
        )
    )

    # 覆盖完整性验证
    uncovered = set(range(num_units)) - used_demand_units
    if uncovered:
        logging.error(f"[Stage2] 警告: {len(uncovered)}个demand_unit未被任何路线覆盖!")
    else:
        logging.info(f"[Stage2] 覆盖完整: {num_units}个demand_unit全部覆盖 ✓")

    return best_sol
