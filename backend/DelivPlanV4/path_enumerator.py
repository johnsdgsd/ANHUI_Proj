"""
Stage 1: 候选路径枚举

以 demand_unit 为原子单元，枚举所有满足约束的候选配送路径。

约束条件（V4）:
    - 每路径 ≤ MAX_STOPS 个物理站点
    - 路径内任意两网点夹角 ≤ 45°（合肥四库房豁免）
    - 总装载量 ≤ max_cap
    - 闭环距离 ≤ max_route_dist
    - [约束1] 同城节点首站必须是最大箱数节点
    - [约束2] 合肥四库房不能与各自150km内非合肥节点同路

算法:
    1. 计算网点极角排序
    2. 滑动窗口构建极角兼容的网点组（注入合肥四库房）
    3. 预计算各合肥节点的排斥集（约束2）
    4. 窗口内枚举节点组合 → 枚举 demand_unit 子集 → 枚举节点排列
    5. 排列过滤：约束1（同城首站最大）+ 约束2（排斥集检查）
    6. 保留每个 demand_unit 组合的最优排列
    7. 单点单 unit 直达兜底
"""

import itertools
import logging
import time
from collections import defaultdict

from backend.DelivPlanV4.config import MAX_STOPS, HEFEI_EXCLUDE_DIST
from backend.DelivPlanV4.geometry import (
    compute_polar_angles,
    check_angle_constraint,
    compute_path_cost,
)


def enumerate_candidate_paths(demand_units, dmat_arr, max_cap, max_route_dist,
                               hefei_node_ids=None, node_to_group=None):
    """
    枚举所有满足约束的候选配送路径。

    约束条件（V4 新增约束见 Stage 1 内联注释）:
        - 每路径 ≤ MAX_STOPS 个物理站点
        - 路径内任意两网点夹角 ≤ 45°（合肥四库房豁免）
        - 总装载量 ≤ max_cap
        - 闭环距离 ≤ max_route_dist
        - [约束1] 同城节点首站必须最大箱数
        - [约束2] 合肥四库房不能与各自 150km 内非合肥节点同路

    Args:
        demand_units: list[tuple], [(unit_id, node_id, boxes), ...]
        dmat_arr: numpy 2D array, 距离矩阵
        max_cap: float, 最大单车容量
        max_route_dist: float, 最大闭环距离
        hefei_node_ids: set[int] | None, 合肥四库房 node_id 集合（合肥本部/肥东/肥西/长丰）
        node_to_group: dict[int, str] | None, node_id → 城市编码（ORG_NO 前5位），用于约束 1

    Returns:
        list[dict]: 候选路径列表，每项格式:
            {
                'id': int,
                'unit_ids': frozenset[int],   # 覆盖的 demand_unit ID 集合
                'sequence': tuple[int],        # 访问的物理节点序列（最优排列）
                'load': float,                 # 总装载量
                'distance': float,             # 闭环距离
                'base_cost': float,            # 运输成本基数（未乘单价）
                'has_hefei': bool,             # [V4新增] 是否含合肥四库房
                'has_far_node': bool,          # [V4新增] 是否含非合肥节点
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

    # ---- 2. 极角排序 + 识别合肥四库房 ----
    # 合肥四库房 = 合肥本部/肥东/肥西/长丰（由调用方通过 hefei_node_ids 传入）
    # 这些节点不受角度约束，可出现在任意滑动窗口中
    hefei_nodes = hefei_node_ids or set()
    polar = compute_polar_angles(unique_nodes, dmat_arr, hefei_nodes=hefei_nodes if hefei_nodes else None)
    n_polar = len(polar)

    if hefei_nodes:
        details = ", ".join(
            f"站点{n}(距省库{dmat_arr[0, n]:.0f})"
            for n in sorted(hefei_nodes)
        )
        logging.info(
            f"[Stage1] 合肥四库房({len(hefei_nodes)}个): {details}"
        )
        # ---- 2.5 约束2预计算：各合肥节点的排斥集 ----
        # 对每个合肥库房 h，排斥集 E(h) = {非合肥节点 j | 0 < d_{h,j} ≤ 150km}
        # 路径含 h 时，不能同时包含 E(h) 中的任何节点
        hefei_exclusion = {}  # {hefei_nid: set of excluded non-hefei nids}
        for h in sorted(hefei_nodes):
            excluded = set()
            for nid in unique_nodes:
                if nid in hefei_nodes:
                    continue  # 合肥内部不互斥
                d = dmat_arr[h, nid]
                if 0 < d <= HEFEI_EXCLUDE_DIST:
                    excluded.add(nid)
            hefei_exclusion[h] = excluded
            if excluded:
                logging.info(
                    f"  [约束2] 合肥站点{h}(距省库{dmat_arr[0, h]:.0f}km) "
                    f"排斥{len(excluded)}个非合肥节点: {sorted(excluded)[:10]}"
                    + ("..." if len(excluded) > 10 else "")
                )
    else:
        hefei_exclusion = {}
        logging.info("[Stage1] 无合肥四库房信息，约束1/2跳过")

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
            if all(check_angle_constraint(candidate_nid, wn, dmat_arr, hefei_nodes if hefei_nodes else None) for wn in window_nodes):
                window_nodes.append(candidate_nid)

        # 注入合肥四库房（不受角度约束，可出现在任意窗口）
        for nn in hefei_nodes:
            if nn not in window_nodes:
                window_nodes.append(nn)

        if not window_nodes:
            continue

        window_sizes.append(len(window_nodes))

        # 窗口内枚举节点组合 (1 ~ min(|window|, MAX_STOPS))
        max_k = min(len(window_nodes), MAX_STOPS)
        for k in range(1, max_k + 1):
            for node_subset in itertools.combinations(window_nodes, k):
                if not _all_pairs_angle_ok(node_subset, dmat_arr, hefei_nodes if hefei_nodes else None):
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

                        # ---- V4 约束2: 合肥四库房排斥各自150km内非合肥节点 ----
                        # 若 combo_nodes 含合肥库房 h，则不能同时包含 E(h) 中的任何节点
                        combo_has_hefei = combo_nodes & hefei_nodes
                        if combo_has_hefei:
                            constraint2_reject = False
                            for h in combo_has_hefei:
                                combo_other = combo_nodes - {h}
                                if combo_other & hefei_exclusion.get(h, set()):
                                    constraint2_reject = True
                                    break
                            if constraint2_reject:
                                continue  # 违反约束2，跳过该组合

                        node_load = defaultdict(float)
                        for uid in unit_combo:
                            nid, boxes = uid_to_info[uid]
                            node_load[nid] += boxes

                        best_seq, best_dist, best_cost = _find_best_sequence(
                            combo_nodes, node_load, total_load, dmat_arr, max_route_dist,
                            node_to_group=node_to_group if node_to_group else None
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
                # 兜底路径也需带标签，元组格式与标准路径一致
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
            # V4 新增标签: 供 Stage 2 约束3 使用
            'has_hefei': bool(set(seq) & hefei_nodes),
            'has_far_node': bool(set(seq) - hefei_nodes),
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


def _all_pairs_angle_ok(node_subset, dmat_arr, hefei_nodes=None):
    """检查节点集合内所有两两组合是否满足角度约束。
    Args:
        node_subset: 节点 ID 集合
        dmat_arr: 距离矩阵
        hefei_nodes: 合肥四库房 node_id 集合（None 时用距离阈值兜底）
    """
    nodes = list(node_subset)
    for i in range(len(nodes)):
        for j in range(i + 1, len(nodes)):
            if not check_angle_constraint(nodes[i], nodes[j], dmat_arr, hefei_nodes):
                return False
    return True


def _find_best_sequence(combo_nodes, node_load, total_load, dmat_arr, max_route_dist,
                        node_to_group=None):
    """
    对给定节点集合，枚举所有排列，找成本最低的。

    V4 新增约束1: 同城组内首站必须是最大箱数节点。

    Args:
        combo_nodes: 节点集合
        node_load: {node_id: boxes}，各节点在该路径中的装箱数
        total_load: 总装载量
        dmat_arr: 距离矩阵
        max_route_dist: 最大闭环距离
        node_to_group: dict | None, node_id → 城市编码（None 时跳过约束1）

    Returns:
        (best_sequence, best_distance, best_cost) 或 (None, 0, inf)
    """
    best_seq = None
    best_dist = float('inf')
    best_cost = float('inf')

    for perm in itertools.permutations(combo_nodes):
        # ---- V4 约束1: 同城组内首站必须是最大箱数节点 ----
        if node_to_group is not None and not _check_max_first(perm, node_load, node_to_group):
            continue

        load, dist, cost = compute_path_cost(perm, node_load, dmat_arr)
        if dist > max_route_dist + 0.01:
            continue
        if cost < best_cost - 0.01:
            best_cost = cost
            best_seq = perm
            best_dist = dist

    return best_seq, best_dist, best_cost


def _check_max_first(perm, node_load, node_to_group):
    """
    V4 约束1辅助函数：检查同城组内首站是否为最大箱数节点。

    对于排列中属于同一城市的节点组，该组中箱数最大的节点必须排在该组最前面。
    单节点组无需检查。

    Args:
        perm: 节点排列 tuple
        node_load: {node_id: boxes}，各节点装箱数
        node_to_group: {node_id: city_code} | None，城市编码映射（None 时跳过检查）

    Returns:
        bool: 满足约束返回 True
    """
    if node_to_group is None:
        return True
    # 按城市分组，记录各节点在排列中的位置
    groups = {}  # {city_code: [(position, node_id), ...]}
    for pos, node in enumerate(perm):
        g = node_to_group.get(node)
        if g is not None:
            if g not in groups:
                groups[g] = []
            groups[g].append((pos, node))

    for g, entries in groups.items():
        if len(entries) < 2:
            continue  # 单节点组无需检查
        # 找该组箱数最大的节点
        max_node = max(entries, key=lambda e: node_load.get(e[1], 0))
        # 最大箱数节点必须是该组在排列中最先出现的（即 pos 最小）
        first_in_group = min(entries, key=lambda e: e[0])
        if first_in_group[1] != max_node[1]:
            return False
    return True
