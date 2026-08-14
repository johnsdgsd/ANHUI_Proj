"""
配送排程引擎（v5.5 MIP + CP-SAT 架构）

保持原 GetDelivPlan 函数签名不变，内部替换为 v5_5 router + scheduler 求解。
数据适配由 delivery_data_adapter 完成。
"""
import logging
import sys
import copy
import numpy as np
from collections import defaultdict

# v5_5 引擎模块
from backend.Scheduling.router import prepare_data, enumerate_routes, preprocess_ftl
from backend.Scheduling.router import filter_no_task_routes, solve as router_solve
from backend.Scheduling.scheduler import solve as scheduler_solve
from backend.Scheduling.delivery_data_adapter import get_params, build_data, build_teams_dict


def GetDelivPlan(Demands, LocationNum, TypeList, SubTypeList, DelivDay,
                 VeUnitPrice, VeTypeNum, VNums, VeCap, DMAT,
                 node_priority=None, daily_vehicle_limits=None,
                 vehicle_types=None, near_center_nodes=None,
                 work_days=None, node_org_map=None):
    """
    配送排程主函数（签名与旧版完全兼容）。

    返回: ScheduledRoutes (list of dict)
        [{'vehicle_type': int, 'deliveries': [(node_id, boxes), ...],
          'schedule_day_idx': int}, ...]
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s", stream=sys.stdout)
    logging.info(">>> [配送] v5.5 MIP 引擎启动...")

    # =========================================================================
    # 1. 准备 v5_5 参数和数据
    # =========================================================================
    params = get_params()
    data, branch_codes, code_to_idx = build_data(
        Demands, LocationNum, TypeList, SubTypeList, DMAT,
        VeCap=VeCap, VNums=VNums,
        daily_vehicle_limits=daily_vehicle_limits,
        work_days=work_days,
        node_priority=node_priority,
        vehicle_types=vehicle_types,
        node_org_map=node_org_map)

    transport_teams = data.get('transport_teams', [])
    priority_tasks = data.get('priority_tasks', {})

    # =========================================================================
    # 2. router: 准备数据
    # =========================================================================
    prepare_data(data)  # 原地修改 vehicles max_trips

    # =========================================================================
    # 3. router: 路径枚举 + FTL
    # =========================================================================
    logging.info(">>> [v5.5] 路径枚举...")
    routes = enumerate_routes(params, data)

    solve_data = copy.deepcopy(data)
    ftl_routes, _ = preprocess_ftl(params, solve_data, priority_tasks)

    routes, _ = filter_no_task_routes(routes, solve_data['branches'])

    logging.info(f"[v5.5] 枚举路线={len(routes)}条, FTL路线={len(ftl_routes)}条")

    # =========================================================================
    # 4. router: MIP 求解
    # =========================================================================
    logging.info(">>> [v5.5] Stage-1 MIP 路径划分...")
    route_solution = router_solve(
        params, solve_data, routes, ftl_routes,
        enforce_load_rate=True,
        priority_tasks=priority_tasks,
        transport_teams=transport_teams,
        verbose=False)

    if route_solution is None or not route_solution.get('routes'):
        logging.error("[v5.5] Stage-1 求解失败，无可行路线！")
        return []

    logging.info(f"[v5.5] Stage-1 完成: {len(route_solution['routes'])} 条路线, "
                 f"总成本={route_solution.get('total_cost', 0):.0f}元, "
                 f"总箱数={route_solution.get('total_boxes', 0)}")

    # =========================================================================
    # 5. scheduler: 日程安排
    # =========================================================================
    schedule_routes = route_solution['routes']
    teams = build_teams_dict(transport_teams)

    logging.info(f">>> [v5.5] Stage-2 CP-SAT 日程安排 ({len(teams)} 个工作日)...")
    schedule_solution = scheduler_solve(schedule_routes, teams, params, verbose=False)

    if schedule_solution is None:
        logging.warning("[v5.5] Stage-2 求解失败，所有路线放第0天")
        day_assign = {r['id']: 1 for r in schedule_routes}  # 1-based, 后面会减1变成0
    else:
        day_assign = schedule_solution.get('day_assign', {})
        if not day_assign:
            logging.warning("[v5.5] Stage-2 day_assign 为空！所有路线将静默放第0天，车辆配额/间隔约束均无效！")
        logging.info(f"[v5.5] Stage-2 完成: 峰值={schedule_solution.get('peak', 'N/A')}")

    # =========================================================================
    # 6. 转换回 ScheduledRoutes 格式
    # =========================================================================
    ScheduledRoutes = []
    for r in schedule_routes:
        route_id = r['id']
        # scheduler day_assign 是 1-based → 减 1 转为 0-based（work_days_list 索引）
        day_idx = day_assign.get(route_id, 1) - 1

        # v5_5 vehicle_k (0/1/2) → 生产代码 vehicle_type (1/2/3)
        ve_type = r.get('vehicle_k', 0) + 1

        # boxes: {branch_code: qty} → deliveries: [(node_id, boxes)]
        deliveries = []
        boxes_dict = r.get('boxes', {})
        for code, qty in boxes_dict.items():
            if qty > 0:
                node_id = code_to_idx.get(code)
                if node_id is not None and node_id > 0:
                    deliveries.append((node_id, int(qty)))

        if deliveries:
            ScheduledRoutes.append({
                'vehicle_type': int(ve_type),
                'deliveries': deliveries,
                'schedule_day_idx': int(day_idx),
            })

    logging.info(f">>> [配送] v5.5 完成: {len(ScheduledRoutes)} 条配送路线")
    return ScheduledRoutes
