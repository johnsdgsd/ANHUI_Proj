"""
Stage 1: 候选路径枚举

以 demand_unit 为原子单元，枚举所有满足约束的候选配送路径。

约束条件:
    - 每路径 ≤ MAX_STOPS 个物理站点
    - 路径内任意两网点夹角 ≤ 45°
    - 总装载量 ≤ max_cap
    - 闭环距离 ≤ max_route_dist

算法:
    1. 计算网点极角排序
    2. 滑动窗口构建极角兼容的网点组
    3. 窗口内枚举节点组合 → 枚举 demand_unit 子集 → 枚举节点排列
    4. 保留每个 demand_unit 组合的最优排列
    5. 单点单 unit 直达兜底
"""

import itertools
import logging
import time
from collections import defaultdict

from backend.DelivPlanV4.config import MAX_STOPS, NEAR_DEPOT_DIST_THRESHOLD
from backend.DelivPlanV4.geometry import (
    compute_polar_angles,
    check_angle_constraint,
    compute_path_cost,
)


def enumerate_candidate_paths(demand_units, dmat_arr, max_cap, max_route_dist):
    """
    枚举所有满足约束的候选配送路径。

    Args:
        demand_units: list[tuple], [(unit_id, node_id, boxes), ...]
        dmat_arr: numpy 2D array, 距离矩阵
        max_cap: float, 最大单车容量
        max_route_dist: float, 最大闭环距离

    Returns:
        list[dict]: 候选路径列表，每项格式:
            {
                'id': int,
                'unit_ids': frozenset[int],   # 覆盖的 demand_unit ID 集合
                'sequence': tuple[int],        # 访问的物理节点序列（最优排列）
                'load': float,                 # 总装载量
                'distance': float,             # 闭环距离
                'base_cost': float,            # 运输成本基数（未乘单价）
            }
    """
    t_start = time.time()

    # ---- 1. 构建查找表 ----
    uid_to_info = {uid: (nid, boxes) for uid, nid, boxes in demand_units}
    node_to_uids = defaultdict(list)
    for uid, nid, _ in demand_units:
        node_to_uids[nid].append(uid)

    unique_nodes = sorted(set(nid for _, nid, _ in demand_units))
    logging.info(f"[Stage1] 开始枚举: {len(unique_nodes)}个物理节点, {len(demand_units)}个demand_unit")

    # ---- 2. 极角排序 + 识别库边网点 ----
    polar = compute_polar_angles(unique_nodes, dmat_arr)
    n_polar = len(polar)

    # 库边网点：距离省库 ≤ 阈值的网点，不受夹角约束，应出现在所有窗口中
    near_depot_nodes = [
        nid for nid in unique_nodes
        if dmat_arr[0, nid] <= NEAR_DEPOT_DIST_THRESHOLD
    ]
    if near_depot_nodes:
        details = ", ".join(
            f"站点{n}(距省库{dmat_arr[0, n]:.0f})"
            for n in sorted(near_depot_nodes)
        )
        logging.info(
            f"[Stage1] 库边网点({len(near_depot_nodes)}个, 省库距离≤{NEAR_DEPOT_DIST_THRESHOLD}): {details}"
        )

    # ---- 3. 滑动窗口枚举 ----
    best_paths = {}
    total_enumerated = 0       # 检查的 unit 组合总数
    total_cap_rejected = 0     # 超容量拒绝
    total_dist_rejected = 0    # 超距离拒绝
    total_accepted = 0         # 实际保留
    total_replaced = 0         # 替换已有（更好的排列）
    window_sizes = []          # 各窗口大小统计

    for start in range(n_polar):
        # 构建极角兼容的最大窗口
        window_nodes = [polar[start][0]]
        for end in range(start + 1, n_polar):
            candidate_nid = polar[end][0]
            if all(check_angle_constraint(candidate_nid, wn, dmat_arr) for wn in window_nodes):
                window_nodes.append(candidate_nid)

        # 注入库边网点（不受夹角约束，可出现在任意窗口）
        for nn in near_depot_nodes:
            if nn not in window_nodes:
                window_nodes.append(nn)

        if not window_nodes:
            continue

        window_sizes.append(len(window_nodes))

        # 窗口内枚举节点组合 (1 ~ min(|window|, MAX_STOPS))
        max_k = min(len(window_nodes), MAX_STOPS)
        for k in range(1, max_k + 1):
            for node_subset in itertools.combinations(window_nodes, k):
                if not _all_pairs_angle_ok(node_subset, dmat_arr):
                    continue

                subset_uids = []
                for nid in node_subset:
                    subset_uids.extend(node_to_uids.get(nid, []))

                if not subset_uids:
                    continue

                max_r = len(subset_uids)
                for r in range(1, max_r + 1):
                    for unit_combo in itertools.combinations(subset_uids, r):
                        total_enumerated += 1
                        total_load = sum(uid_to_info[uid][1] for uid in unit_combo)
                        if total_load > max_cap + 0.01:
                            total_cap_rejected += 1
                            continue

                        combo_nodes = set(uid_to_info[uid][0] for uid in unit_combo)

                        node_load = defaultdict(float)
                        for uid in unit_combo:
                            nid, boxes = uid_to_info[uid]
                            node_load[nid] += boxes

                        best_seq, best_dist, best_cost = _find_best_sequence(
                            combo_nodes, node_load, total_load, dmat_arr, max_route_dist
                        )

                        if best_seq is not None:
                            key = frozenset(unit_combo)
                            if key not in best_paths:
                                best_paths[key] = (best_seq, total_load, best_dist, best_cost)
                                total_accepted += 1
                            elif best_cost < best_paths[key][2]:
                                best_paths[key] = (best_seq, total_load, best_dist, best_cost)
                                total_replaced += 1
                        else:
                            total_dist_rejected += 1

    # ---- 4. 单点单 unit 直达兜底 ----
    fallback_count = 0
    for uid, nid, boxes in demand_units:
        if boxes > max_cap:
            continue
        dist = dmat_arr[0, nid] + dmat_arr[nid, 0]
        if dist <= max_route_dist:
            cost = dmat_arr[0, nid] * boxes
            key = frozenset([uid])
            if key not in best_paths:
                best_paths[key] = ((nid,), boxes, dist, cost)
                fallback_count += 1

    # ---- 5. 转换为候选列表 ----
    candidates = []
    load_dist = defaultdict(int)  # 装载量分布统计
    stop_dist = defaultdict(int)  # 站点数分布
    for i, (key, (seq, load, dist, cost)) in enumerate(best_paths.items()):
        candidates.append({
            'id': i,
            'unit_ids': frozenset(key),
            'sequence': tuple(seq),
            'load': load,
            'distance': dist,
            'base_cost': cost,
        })
        # 统计
        load_bucket = int(load // 100) * 100
        load_dist[load_bucket] += 1
        stop_dist[len(seq)] += 1

    elapsed = time.time() - t_start

    # 汇总日志
    avg_window = sum(window_sizes) / len(window_sizes) if window_sizes else 0
    logging.info(
        f"[Stage1] 枚举完成: {len(candidates)}条候选路径 "
        f"({len(unique_nodes)}节点, {len(demand_units)}需求单元, 耗时{elapsed:.1f}s)"
    )
    logging.info(
        f"[Stage1] 枚举统计: 检查{total_enumerated}个组合, "
        f"超容拒绝{total_cap_rejected}, 超距拒绝{total_dist_rejected}, "
        f"新增{total_accepted}, 替换{total_replaced}, 兜底{fallback_count}"
    )
    logging.info(
        f"[Stage1] 窗口统计: 极角窗口均值={avg_window:.1f}节点, "
        f"范围=[{min(window_sizes) if window_sizes else 0}, {max(window_sizes) if window_sizes else 0}]"
    )
    logging.info(
        f"[Stage1] 站点分布: " +
        " | ".join(f"{k}站:{v}条" for k, v in sorted(stop_dist.items()))
    )
    logging.info(
        f"[Stage1] 装载分布: " +
        " | ".join(f"{lb}-{lb+100}箱:{v}条" for lb, v in sorted(load_dist.items())[:8])
        + (f" ...共{len(load_dist)}档" if len(load_dist) > 8 else "")
    )

    return candidates


def _all_pairs_angle_ok(node_subset, dmat_arr):
    """检查节点集合内所有两两组合是否满足角度约束。"""
    nodes = list(node_subset)
    for i in range(len(nodes)):
        for j in range(i + 1, len(nodes)):
            if not check_angle_constraint(nodes[i], nodes[j], dmat_arr):
                return False
    return True


def _find_best_sequence(combo_nodes, node_load, total_load, dmat_arr, max_route_dist):
    """
    对给定节点集合，枚举所有排列，找成本最低的。

    Returns:
        (best_sequence, best_distance, best_cost) 或 (None, 0, inf)
    """
    best_seq = None
    best_dist = float('inf')
    best_cost = float('inf')

    for perm in itertools.permutations(combo_nodes):
        load, dist, cost = compute_path_cost(perm, node_load, dmat_arr)
        if dist > max_route_dist + 0.01:
            continue
        if cost < best_cost - 0.01:
            best_cost = cost
            best_seq = perm
            best_dist = dist

    return best_seq, best_dist, best_cost
