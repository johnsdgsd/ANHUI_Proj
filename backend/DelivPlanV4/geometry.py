"""
几何计算模块

功能:
    1. compute_polar_angles: 计算各网点以省库为原点的极角代理
    2. check_angle_constraint: 检查两网点夹角是否满足约束
    3. compute_path_cost: 计算给定节点序列的路径指标（装载量、距离、成本）

V4.1 新增: 支持基于 geodesic 直线距离的余弦定理角度约束（向后兼容）
    - 传入 depot_coord + node_coords → 用 geodesic 距离代入余弦定理（避免道路绕行扭曲）
    - 不传 → 用道路距离矩阵余弦定理（旧行为不变）
"""

import logging
import math

from backend.DelivPlanV4.config import ANGLE_COS_THRESHOLD, NEAR_DEPOT_DIST_THRESHOLD

# 地球半径 (km)
_EARTH_RADIUS_KM = 6371.0


def _haversine_km(coord1, coord2):
    """Haversine 公式计算两经纬度坐标间的球面直线距离 (km)。
    coord 格式: (lon, lat)，纯 Python 实现，无外部依赖。"""
    lon1, lat1 = math.radians(coord1[0]), math.radians(coord1[1])
    lon2, lat2 = math.radians(coord2[0]), math.radians(coord2[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2)
    return _EARTH_RADIUS_KM * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _geodesic_km(coord1, coord2):
    """计算两经纬度坐标间的球面直线距离 (km)。
    coord 格式: (lon, lat)"""
    return _haversine_km(coord1, coord2)


def _geodesic_cos_angle(depot_coord, coord1, coord2):
    """用 geodesic 直线距离 + 余弦定理计算两节点从省库看的夹角 cos 值。

    cosθ = (d01² + d02² - d12²) / (2 × d01 × d02)

    Args:
        depot_coord: (lon, lat) 省库坐标
        coord1, coord2: (lon, lat) 两节点坐标

    Returns:
        float: cos 值，已 clamp 到 [-1, 1]
    """
    d01 = max(_geodesic_km(depot_coord, coord1), 0.001)
    d02 = max(_geodesic_km(depot_coord, coord2), 0.001)
    d12 = _geodesic_km(coord1, coord2)
    cos_val = (d01 ** 2 + d02 ** 2 - d12 ** 2) / (2 * d01 * d02)
    return max(-1.0, min(1.0, cos_val))


def compute_polar_angles(node_ids, dmat_arr, hefei_nodes=None,
                         depot_coord=None, node_coords=None):
    """
    极角代理：计算每个网点相对于省库的角度排序。

    V4.1: 支持两种模式
    - 地理模式 (depot_coord 和 node_coords 均有效):
      用经纬度计算方位角，按 0-360° 排序
    - 距离模式 (默认，向后兼容):
      以最远网点为参考方向，用余弦定理计算 cos 代理值
      cos = (d0_i² + d0_ref² - d_ref_i²) / (2 × d0_i × d0_ref)

    Args:
        node_ids: 网点 ID 列表 (1-indexed)
        dmat_arr: 距离矩阵 (numpy 2D array), dmat[0, :] 为省库到各网点距离
        hefei_nodes: 合肥四库房 node_id 集合，用于日志输出（可选）
        depot_coord: (lon, lat) 省库经纬度，None 时用距离模式
        node_coords: {node_id: (lon, lat)}，None 时用距离模式

    Returns:
        list[tuple]: [(node_id, angle_value), ...]
                     地理模式按方位角升序，距离模式按 cos 降序
    """
    if not node_ids:
        return []

    # ---- V4.1: geodesic 余弦代理模式 ----
    if depot_coord is not None and node_coords is not None:
        dep_lon, dep_lat = depot_coord
        if dep_lon != 0 or dep_lat != 0:  # 坐标有效
            # 找 geodesic 最远节点为参考
            ref_node = None
            ref_dist = -1
            node_geodesic_d0 = {}
            for nid in node_ids:
                coord = node_coords.get(nid)
                if coord is None:
                    break
                d0 = _geodesic_km(depot_coord, coord)
                node_geodesic_d0[nid] = d0
                if d0 > ref_dist:
                    ref_dist = d0
                    ref_node = nid
            else:
                # 所有节点都有坐标 → geodesic 模式
                ref_coord = node_coords[ref_node]
                d0_ref = max(ref_dist, 0.001)
                polar = []
                for nid in node_ids:
                    coord = node_coords[nid]
                    cos_val = _geodesic_cos_angle(depot_coord, ref_coord, coord)
                    polar.append((nid, cos_val))
                polar.sort(key=lambda x: x[1], reverse=True)

                cos_vals = [c for _, c in polar]
                exempt_nodes = [nid for nid in node_ids
                                if hefei_nodes is not None and nid in hefei_nodes]
                msg = (
                    f"[极角-geodesic] {len(polar)}个网点, "
                    f"参考={ref_node}({ref_dist:.0f}km), "
                    f"cos范围=[{min(cos_vals):.3f}, {max(cos_vals):.3f}], "
                    f"中位数={cos_vals[len(cos_vals)//2]:.3f}"
                )
                if exempt_nodes:
                    details = ", ".join(
                        f"站点{n}(距省库{node_geodesic_d0.get(n, 0):.0f}km)" for n in sorted(exempt_nodes))
                    msg += f", 角度豁免({len(exempt_nodes)}个): {details}"
                logging.info(msg)
                return polar
            # 有节点缺坐标，回退到距离模式（兜底）

    # ---- 距离模式（默认，向后兼容） ----
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


def check_angle_constraint(n1, n2, dmat_arr, hefei_nodes=None,
                           depot_coord=None, node_coords=None):
    """
    检查两网点从省库出发的夹角是否满足约束。

    V4.1 支持两种模式:
        - 地理模式 (depot_coord 和 node_coords 均有效):
          用经纬度方位角差 ≤ GEO_ANGLE_THRESHOLD (45°)
        - 距离模式 (默认，向后兼容):
          余弦定理: cosθ = (d01² + d02² - d12²) / (2 × d01 × d02)
          约束: cosθ >= ANGLE_COS_THRESHOLD (0.707, 即 ≤45°)

    特殊处理:
        - 同节点: 始终通过
        - 合肥四库房（hefei_nodes 集合中的节点）: 不受夹角约束
        - hefei_nodes=None 时，回退距离阈值判定（向后兼容）
        - 距省库 ≤ 0.001: 距离数据缺失，始终通过

    Args:
        n1, n2: 网点 ID (1-indexed)
        dmat_arr: 距离矩阵
        hefei_nodes: 合肥四库房 node_id 集合，None 时用距离阈值兜底
        depot_coord: (lon, lat) 省库经纬度，None 时用距离模式
        node_coords: {node_id: (lon, lat)}，None 时用距离模式

    Returns:
        bool: 是否满足角度约束
    """
    if n1 == n2:
        return True

    # 角度豁免判定：优先使用合肥四库房集合，否则用距离阈值兜底
    if hefei_nodes is not None:
        # V4: 合肥四库房（合肥本部/肥东/肥西/长丰）不受夹角约束
        if n1 in hefei_nodes or n2 in hefei_nodes:
            return True
    else:
        # 向后兼容：无 hefei_nodes 信息时，距省库 ≤ 阈值的站点不受约束
        d01 = dmat_arr[0, n1]
        d02 = dmat_arr[0, n2]
        if d01 <= NEAR_DEPOT_DIST_THRESHOLD or d02 <= NEAR_DEPOT_DIST_THRESHOLD:
            return True

    # ---- V4.1: geodesic 余弦定理模式 ----
    if depot_coord is not None and node_coords is not None:
        dep_lon, dep_lat = depot_coord
        if dep_lon != 0 or dep_lat != 0:  # 坐标有效
            coord1 = node_coords.get(n1)
            coord2 = node_coords.get(n2)
            if coord1 is not None and coord2 is not None:
                cos_theta = _geodesic_cos_angle(depot_coord, coord1, coord2)
                return cos_theta >= ANGLE_COS_THRESHOLD
            # 坐标缺失，回退距离模式（兜底）

    # ---- 距离模式（默认，向后兼容） ----
    d01 = dmat_arr[0, n1]
    d02 = dmat_arr[0, n2]
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
