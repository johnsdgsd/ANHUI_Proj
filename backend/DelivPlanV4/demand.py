"""
需求处理模块

功能:
    1. compute_volume_boxes: 件数 → 体积箱转换
    2. build_vehicle_config: 构建车辆配置列表
    3. split_large_demand: 超容量网点拆分为 demand_unit
"""

import logging
import numpy as np
import pandas as pd


def compute_volume_boxes(demands, sub_type_list):
    """
    将需求件数矩阵转换为各网点的体积箱合计。

    转换公式（与 GetDelivPlan 保持一致）:
        boxes(loc, dev) = ceil(ceil(pieces / PACK_BOX_NUM) × vol_mult)
        vol_mult = 2.5 if DEV_CLS == '02' else 1.0
        unit_sum(loc) = Σ_i boxes(loc, i)

    Args:
        demands: (LocationNum × SubTypeNum) DataFrame 或 numpy array，需求件数
        sub_type_list: 设备规格 DataFrame，需含 PACK_BOX_NUM 和 DEV_CLS 列

    Returns:
        dict[int, float]: {node_id (1-indexed): total_volume_boxes}
    """
    location_num = demands.shape[0]
    sub_type_num = len(sub_type_list)
    demands_arr = demands.values if isinstance(demands, pd.DataFrame) else demands
    demands_boxes = np.zeros((location_num, sub_type_num))

    # 统计互感器设备
    pt_devices = 0
    for i in range(sub_type_num):
        cls_val = str(sub_type_list.iloc[i].get('DEV_CLS', '')).replace('.0', '').strip().zfill(2)
        if cls_val == '02':
            pt_devices += 1
    logging.info(f"[需求转换] {location_num}站点 × {sub_type_num}设备 (含{pt_devices}种互感器×2.5体积系数)")

    total_pieces = 0
    for i in range(sub_type_num):
        unit_per_box = float(sub_type_list.iloc[i]['PACK_BOX_NUM'])
        cls_val = str(sub_type_list.iloc[i].get('DEV_CLS', '')).replace('.0', '').strip().zfill(2)
        vol_mult = 2.5 if cls_val == '02' else 1.0
        pieces_i = demands_arr[:, i].sum()
        total_pieces += pieces_i
        demands_boxes[:, i] = np.ceil(np.ceil(demands_arr[:, i] / unit_per_box) * vol_mult)

    total_boxes_per_loc = np.sum(demands_boxes, axis=1)
    unit_sum = {
        i + 1: float(total_boxes_per_loc[i])
        for i in range(location_num)
        if total_boxes_per_loc[i] > 0
    }

    total_vol_boxes = sum(unit_sum.values())
    active_sites = len(unit_sum)
    logging.info(
        f"[需求转换] 总件数={int(total_pieces)}, 总体积箱={total_vol_boxes:.0f}, "
        f"有需求站点={active_sites}/{location_num}"
    )
    return unit_sum


def build_vehicle_config(ve_cap, v_nums, ve_type_num):
    """
    构建车辆配置列表，按容量升序排列。

    Args:
        ve_cap: 各车型容量列表
        v_nums: 各车型日配额列表
        ve_type_num: 车型数量

    Returns:
        list[dict]: 按 cap 升序排列的车辆配置，每项含 type/cap/daily_max
    """
    config = sorted([
        {'type': i + 1, 'cap': float(ve_cap[i]), 'daily_max': int(v_nums[i])}
        for i in range(ve_type_num)
    ], key=lambda x: x['cap'])

    total_cap = sum(c['cap'] * c['daily_max'] for c in config)
    logging.info(
        f"[车辆配置] {ve_type_num}种车型, 总运力={total_cap:.0f}箱, "
        + " | ".join(
            f"车型{t['type']}(容{t['cap']:.0f}×{t['daily_max']}辆)"
            for t in config
        )
    )
    return config


def split_large_demand(unit_sum, max_cap):
    """
    将超过 max_cap 的网点需求拆分为多个 demand_unit。
    每个 demand_unit 的 boxes ≤ max_cap，确保任何车型都能装载单个 unit。

    Args:
        unit_sum: {node_id: total_boxes} 字典
        max_cap: 最大单车容量

    Returns:
        list[tuple]: [(unit_id, node_id, boxes), ...]，unit_id 从 0 起连续编号
    """
    units = []
    uid = 0
    split_count = 0
    for nid, boxes in sorted(unit_sum.items()):
        remaining = boxes
        if boxes > max_cap:
            split_count += 1
            logging.info(
                f"[需求拆分] 站点{nid} 需求{boxes:.0f}箱 > 最大容量{max_cap:.0f}箱, 需拆分"
            )
        while remaining > 0.001:
            take = min(remaining, max_cap)
            units.append((uid, nid, take))
            uid += 1
            remaining -= take

    logging.info(
        f"[需求拆分] {len(unit_sum)}个站点 → {len(units)}个demand_unit "
        f"(其中{split_count}个站点超容拆分)"
    )
    return units
