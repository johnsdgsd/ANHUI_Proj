"""
几何计算模块

功能:
    1. compute_polar_angles: 计算各网点以省库为原点的极角代理
    2. check_angle_constraint: 检查两网点夹角是否满足约束
    3. compute_path_cost: 计算给定节点序列的路径指标（装载量、距离、成本）
"""

import logging
from backend.DelivPlanV4.config import ANGLE_COS_THRESHOLD, NEAR_DEPOT_DIST_THRESHOLD


def compute_polar_angles(node_ids, dmat_arr, hefei_nodes=None):
    """
    极角代理：以最远网点为参考方向，计算每个网点相对于省库的 cos 值。

    原理:
        以距离省库最远的网点作为参考方向，对其他网点用余弦定理计算 cos 值。
        cos = (d0_i² + d0_ref² - d_ref_i²) / (2 × d0_i × d0_ref)

    Args:
        node_ids: 网点 ID 列表 (1-indexed)
        dmat_arr: 距离矩阵 (numpy 2D array), dmat[0, :] 为省库到各网点距离
        hefei_nodes: 合肥四库房 node_id 集合，用于日志输出（可选）

    Returns:
        list[tuple]: [(node_id, cos_val), ...]，按 cos 降序（角度升序）排列
    """
    if not node_ids:
        return []
    ref_node = max(node_ids, key=lambda x: dmat_arr[0, x])
    d0_ref = max(dmat_arr[0, ref_node], 0.001)
    logging.info(f"[极角] 参考网点={ref_node}, 省库距离={dmat_arr[0, ref_node]:.0f}")
    polar = []
    for nid in node_ids:
        d0_i = max(dmat_arr[0, nid], 0.001)
        d_ref_i = dmat_arr[ref_node, nid]
        cos_val = (d0_i ** 2 + d0_ref ** 2 - d_ref_i ** 2) / (2 * d0_i * d0_ref)
        cos_val = max(-1.0, min(1.0, cos_val))
        polar.append((nid, cos_val))
    polar.sort(key=lambda x: x[1], reverse=True)

    # 角度分布摘要
    cos_vals = [c for _, c in polar]
    # 角度豁免节点：优先使用 hefei_nodes（合肥四库房），否则用距离阈值兜底
    if hefei_nodes is not None:
        exempt_nodes = [nid for nid in node_ids if nid in hefei_nodes]
    else:
        exempt_nodes = [nid for nid in node_ids if dmat_arr[0, nid] <= NEAR_DEPOT_DIST_THRESHOLD]
    msg = (
        f"[极角] {len(polar)}个网点, "
        f"cos范围=[{min(cos_vals):.3f}, {max(cos_vals):.3f}], "
        f"cos中位数={cos_vals[len(cos_vals)//2]:.3f}"
    )
    if exempt_nodes:
        details = ", ".join(f"站点{n}(距省库{dmat_arr[0, n]:.0f})" for n in sorted(exempt_nodes))
        msg += f", 角度豁免({len(exempt_nodes)}个): {details}"
    logging.info(msg)
    return polar


def check_angle_constraint(n1, n2, dmat_arr, hefei_nodes=None):
    """
    检查两网点从省库出发的夹角是否满足约束。

    余弦定理: cosθ = (d01² + d02² - d12²) / (2 × d01 × d02)
    约束: cosθ >= ANGLE_COS_THRESHOLD (即夹角 ≤ 45°)

    特殊处理:
        - 同节点: 始终通过
        - 合肥四库房（hefei_nodes 集合中的节点）: 不受夹角约束
        - hefei_nodes=None 时，回退距离阈值判定（向后兼容）
        - 距省库 ≤ 0.001: 距离数据缺失，始终通过

    Args:
        n1, n2: 网点 ID (1-indexed)
        dmat_arr: 距离矩阵
        hefei_nodes: 合肥四库房 node_id 集合，None 时用距离阈值兜底

    Returns:
        bool: 是否满足角度约束
    """
    if n1 == n2:
        return True
    d01 = dmat_arr[0, n1]
    d02 = dmat_arr[0, n2]
    # 角度豁免判定：优先使用合肥四库房集合，否则用距离阈值兜底
    if hefei_nodes is not None:
        # V4: 合肥四库房（合肥本部/肥东/肥西/长丰）不受夹角约束
        if n1 in hefei_nodes or n2 in hefei_nodes:
            return True
    else:
        # 向后兼容：无 hefei_nodes 信息时，距省库 ≤ 阈值的站点不受约束
        if d01 <= NEAR_DEPOT_DIST_THRESHOLD or d02 <= NEAR_DEPOT_DIST_THRESHOLD:
            return True
    if d01 <= 0.001 or d02 <= 0.001:
        return True
    d12 = dmat_arr[n1, n2]
    cos_theta = (d01 ** 2 + d02 ** 2 - d12 ** 2) / (2 * d01 * d02)
    cos_theta = max(-1.0, min(1.0, cos_theta))
    return cos_theta >= ANGLE_COS_THRESHOLD


def compute_path_cost(node_sequence, node_load_map, dmat_arr):
    """
    计算给定节点排列的路径指标。

    运输成本基数公式（与 V3 calc_route_cost 一致，不含单价）:
        base_cost = Σ_i amt_i × dist(prev_node_i, node_i)
        即：每段的成本只算该段终点卸货量，不是 cum_load

    对比 V3 源码（GetDelivPlan.calc_route_cost）:
        for cid, amt in route['deliveries']:
            total_cost += amt * get_dist(prev_node, cid) * unit_price
            prev_node = cid

    Args:
        node_sequence: 节点访问顺序，如 (3, 7, 12)
        node_load_map: dict[int, float], 各节点在该路径中的装载量（只含该路径覆盖的 demand_units）
        dmat_arr: 距离矩阵

    Returns:
        tuple: (total_load, distance, base_cost)
            - total_load: float, 总装载量
            - distance: float, 闭环距离（省库→节点1→...→节点k→省库）
            - base_cost: float, 运输成本基数（未乘单价）
    """
    k = len(node_sequence)
    if k == 0:
        return 0.0, 0.0, 0.0

    total_load = sum(node_load_map.get(nid, 0.0) for nid in node_sequence)

    # 闭环距离
    distance = dmat_arr[0, node_sequence[0]]
    for i in range(k - 1):
        distance += dmat_arr[node_sequence[i], node_sequence[i + 1]]
    distance += dmat_arr[node_sequence[-1], 0]

    # 运输成本基数（与 V3 一致）: Σ amt_i × dist(prev, i)，不含单价
    base_cost = 0.0
    prev_node = 0
    for nid in node_sequence:
        amt = node_load_map.get(nid, 0.0)
        base_cost += amt * dmat_arr[prev_node, nid]
        prev_node = nid
    # 回程空载不计成本

    return total_load, distance, base_cost
