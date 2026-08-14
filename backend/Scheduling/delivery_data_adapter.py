"""
数据适配器：将生产代码数据库数据转换为 v5_5 router/scheduler 所需格式。

生产代码已有数据 → v5_5 格式映射:
  Demands矩阵 + SubTypeList → branches (网点箱数需求)
  DMAT距离矩阵 → d_center + full_d_ij
  实时计算 → angle_matrix + compatible (余弦定理)
  VeCap/VNums → vehicles + transport_teams
  node_priority → priority_tasks
"""
import math
import logging
import numpy as np
from collections import defaultdict

# ============================================================
# 业务参数（从 v5_5 data_loader 复制，唯一事实来源）
# ============================================================
VEHICLE_DEFS = [
    {'name': '大车', 'capacity': 1071},
    {'name': '中车', 'capacity': 900},
    {'name': '小车', 'capacity': 450},
]
VEHICLE_NAMES = ['大车', '中车', '小车']
VEHICLE_K = {'大车': 0, '中车': 1, '小车': 2}

METERS_PER_KM = 1000.0
EXPECTED_BRANCH_COUNT = 87

C0 = 0.07
L_MAX = 750
THETA_MAX = 45
MISSING_DIST_SENTINEL = 999

FTL_VEHICLE_K = 0
FTL_CAPACITY = 1071
FTL_DEMAND_THRESHOLD = 1071
NON_TEAM_VEHICLES = [4, 3, 8]

LOAD_RATE_TARGET = 0.80  # 软目标装载率（v5_61 用户 2026-08-10: 0.90→0.80，与硬80档一致，≥80% 无惩罚）
PENALTY = 100.0
LOAD_RATE_MID = 0.70
PENALTY_MID = 400.0
LOAD_RATE_CRITICAL = 0.50
PENALTY_CRITICAL = 500.0
LOAD_RATE_SEVERE = 0.30
PENALTY_SEVERE = 2000.0
A_B_A_RATE_MAX = 0.70
MIN_BOXES_PER_STOP = 100
LOAD_RATE_TARGET_ANY_HARD_90 = 0.90
LOAD_RATE_TARGET_ANY_HARD_80 = 0.80
LOAD_RATE_TARGET_A_ONLY = 0.70
TEAM_CAP_BOXES = 3000

LOAD_RATE_MIN_ANY = 0.80
LOAD_RATE_MIN_A_ONLY = 0.70
DIST_MAX_KM = 750


def get_params():
    """返回 v5_5 全部业务参数字典"""
    return {
        'VEHICLE_DEFS': VEHICLE_DEFS,
        'VEHICLE_NAMES': VEHICLE_NAMES,
        'VEHICLE_K': VEHICLE_K,
        'METERS_PER_KM': METERS_PER_KM,
        'EXPECTED_BRANCH_COUNT': EXPECTED_BRANCH_COUNT,
        'C0': C0,
        'L_MAX': L_MAX,
        'THETA_MAX': THETA_MAX,
        'MISSING_DIST_SENTINEL': MISSING_DIST_SENTINEL,
        'FTL_VEHICLE_K': FTL_VEHICLE_K,
        'FTL_CAPACITY': FTL_CAPACITY,
        'FTL_DEMAND_THRESHOLD': FTL_DEMAND_THRESHOLD,
        'NON_TEAM_VEHICLES': NON_TEAM_VEHICLES,
        'LOAD_RATE_TARGET': LOAD_RATE_TARGET,
        'PENALTY': PENALTY,
        'LOAD_RATE_MID': LOAD_RATE_MID,
        'PENALTY_MID': PENALTY_MID,
        'LOAD_RATE_CRITICAL': LOAD_RATE_CRITICAL,
        'PENALTY_CRITICAL': PENALTY_CRITICAL,
        'LOAD_RATE_SEVERE': LOAD_RATE_SEVERE,
        'PENALTY_SEVERE': PENALTY_SEVERE,
        'A_B_A_RATE_MAX': A_B_A_RATE_MAX,
        'MIN_BOXES_PER_STOP': MIN_BOXES_PER_STOP,
        'LOAD_RATE_TARGET_ANY_HARD_90': LOAD_RATE_TARGET_ANY_HARD_90,
        'LOAD_RATE_TARGET_ANY_HARD_80': LOAD_RATE_TARGET_ANY_HARD_80,
        'LOAD_RATE_TARGET_A_ONLY': LOAD_RATE_TARGET_A_ONLY,
        'TEAM_CAP_BOXES': TEAM_CAP_BOXES,
        'LOAD_RATE_MIN_ANY': LOAD_RATE_MIN_ANY,
        'LOAD_RATE_MIN_A_ONLY': LOAD_RATE_MIN_A_ONLY,
        'DIST_MAX_KM': DIST_MAX_KM,
    }


def build_data(Demands, LocationNum, TypeList, SubTypeList, DMAT,
               VeCap, VNums, daily_vehicle_limits, work_days, node_priority,
               vehicle_types=None, node_org_map=None):
    """
    将生产代码数据转为 v5_5 统一 data dict。

    参数均为生产代码已有数据，与 GetDelivPlan 原参数一致。
    （不含 locations，通过 node_org_map + DMAT 推断网点信息）
    """
    # ---- 1. 计算各网点体积加权箱数需求 ----
    demand_boxes = _compute_demand_boxes(Demands, LocationNum, TypeList, SubTypeList)

    # ---- 2. 构建 branches（从 node_org_map + DMAT 推断） ----
    branches, branch_codes, code_to_idx = _build_branches(
        node_org_map, demand_boxes, LocationNum, DMAT)

    # ---- 3. 构建距离数据 (d_center, full_d_ij) ----
    center_code = branch_codes[0]  # 省级总库编码
    d_center, full_d_ij = _build_distance_data(
        DMAT, LocationNum, branch_codes, code_to_idx, center_code)

    # ---- 4. 计算夹角矩阵 (angle_matrix, compatible) ----
    angle_matrix, compatible = _build_angle_data(
        DMAT, LocationNum, branch_codes, code_to_idx, center_code, d_center)

    # ---- 5. 构建运输队 (transport_teams) ----
    transport_teams, team_vehicles_total = _build_transport_teams(
        daily_vehicle_limits, work_days, VNums, vehicle_types)

    # ---- 6. 构建车辆参数 (vehicles in data['params']) ----
    vehicles = _build_vehicles(VeCap, team_vehicles_total, VNums, vehicle_types)

    # ---- 7. 构建优先配送任务（新缺货检测算法） ----
    cur_date = work_days[0] if work_days is not None and len(work_days) > 0 else None
    priority_tasks = _build_priority_tasks_v2(
        node_priority, branch_codes, demand_boxes, LocationNum, cur_date, code_to_idx)

    # ---- 8. 组装 data dict ----
    data = {
        'branches': branches,
        'params': {'vehicles': vehicles},
        'angle_matrix': angle_matrix,
        'compatible': compatible,
        'd_ij': full_d_ij,          # v5_5 router 用 d_ij 查分支间距离
        'd_center': d_center,
        'full_d_ij': full_d_ij,
        'center_code': center_code,
        'branch_codes': branch_codes[1:],  # 不含中心仓库
        'priority_tasks': priority_tasks,
        'transport_teams': transport_teams,
    }
    return data, branch_codes, code_to_idx


def _compute_demand_boxes(Demands, LocationNum, TypeList, SubTypeList):
    """体积加权箱数计算（与 GetCheckDeliverPlan 诊断代码一致）"""
    SubTypeNum = len(SubTypeList)
    demand_boxes = np.zeros(LocationNum)

    # 兼容 ndarray 和 DataFrame
    if hasattr(Demands, 'iloc'):
        dm = Demands
    else:
        import pandas as pd
        dm = pd.DataFrame(Demands)

    for j in range(SubTypeNum):
        dc = str(SubTypeList.iloc[j]['DEV_CODE_NO']).strip()
        unit_arr = TypeList.loc[TypeList['DEV_CODE_NO'] == dc, 'UnitPerBox'].values
        UnitPerBox = unit_arr[0] if len(unit_arr) > 0 else 5
        cls_val = str(SubTypeList.iloc[j].get('DEV_CLS', '01')).replace('.0', '').strip().zfill(2)
        vol_mult = 2.5 if cls_val == '02' else 1.0
        for i in range(LocationNum):
            val = dm.iloc[i, j] if hasattr(dm, 'iloc') else dm[i, j]
            if val > 0:
                boxes = math.ceil(math.ceil(val / UnitPerBox) * vol_mult)
                demand_boxes[i] += boxes
    return demand_boxes


def _build_branches(node_org_map, demand_boxes, LocationNum, DMAT):
    """从 node_org_map + DMAT 构建 v5_5 branches 字典"""
    branches = {}
    branch_codes = []
    code_to_idx = {}

    if isinstance(DMAT, np.ndarray):
        dmat = DMAT
    else:
        dmat = DMAT.values

    # 索引 0 = 中心仓库（用 '34101' 兜底）
    center_code = '34101'
    branches[center_code] = {
        'name': '省级总库',
        'code_len': len(center_code),
        'group_code': center_code[:5],
        'dist_to_center_km': 0.0,
        'demand': 0,
        'has_demand': False,
        'set_A': False,
        'set_B': False,
    }
    branch_codes.append(center_code)
    code_to_idx[center_code] = 0

    for i in range(1, LocationNum + 1):
        # 用 node_org_map 或兜底编码
        if node_org_map and i in node_org_map:
            org_no = str(node_org_map[i]).strip()
        else:
            org_no = f"{34101 + i:07d}"

        # 到中心距离从 DMAT 取
        dist_center = float(dmat[0, i]) if not np.isnan(dmat[0, i]) else 0.0

        boxes = int(demand_boxes[i - 1])  # demand_boxes 是 0-based
        branches[org_no] = {
            'name': org_no,
            'code_len': len(org_no),
            'group_code': org_no[:5] if len(org_no) >= 5 else org_no,
            'dist_to_center_km': dist_center,
            'demand': boxes,
            'has_demand': boxes > 0,
            'set_A': boxes > 0,    # 有需求的网点归类为 Set A
            'set_B': False,
        }
        branch_codes.append(org_no)
        code_to_idx[org_no] = i

    logging.info(f"[adapter] branches 构建完成: {len(branches)} 个网点, "
                 f"有需求={sum(1 for b in branches.values() if b['has_demand'])}")
    return branches, branch_codes, code_to_idx


def _build_distance_data(DMAT, LocationNum, branch_codes, code_to_idx, center_code):
    """从 DMAT 矩阵构建 d_center 和 full_d_ij"""
    d_center = {}
    full_d_ij = {}

    if isinstance(DMAT, np.ndarray):
        dmat = DMAT
    else:
        dmat = DMAT.values

    for i in range(LocationNum + 1):
        code_i = branch_codes[i] if i < len(branch_codes) else str(i)
        for j in range(LocationNum + 1):
            code_j = branch_codes[j] if j < len(branch_codes) else str(j)
            dist_km = float(dmat[i, j]) if not np.isnan(dmat[i, j]) else MISSING_DIST_SENTINEL

            if i == 0:  # center → branch
                d_center[('center_to', code_j)] = dist_km
            if j == 0:  # branch → center
                d_center[(code_i, 'to_center')] = dist_km

            if i != j:
                full_d_ij[(code_i, code_j)] = dist_km

    logging.info(f"[adapter] 距离数据构建完成: d_center={len(d_center)}条, "
                 f"full_d_ij={len(full_d_ij)}条")
    return d_center, full_d_ij


def _build_angle_data(DMAT, LocationNum, branch_codes, code_to_idx, center_code, d_center):
    """用余弦定理计算网点间夹角和同方向兼容性"""
    angle_matrix = {}
    compatible = {}

    if isinstance(DMAT, np.ndarray):
        dmat = DMAT
    else:
        dmat = DMAT.values

    for i in range(1, LocationNum + 1):
        code_i = branch_codes[i]
        d_ci = d_center.get(('center_to', code_i), MISSING_DIST_SENTINEL)

        for j in range(i + 1, LocationNum + 1):
            code_j = branch_codes[j]
            d_cj = d_center.get(('center_to', code_j), MISSING_DIST_SENTINEL)
            d_ij = dmat[i, j]

            # 余弦定理: cos(θ) = (d_ci² + d_cj² - d_ij²) / (2 * d_ci * d_cj)
            if d_ci > 0 and d_cj > 0:
                cos_theta = (d_ci ** 2 + d_cj ** 2 - d_ij ** 2) / (2 * d_ci * d_cj)
                cos_theta = max(-1.0, min(1.0, cos_theta))
                angle_deg = math.degrees(math.acos(cos_theta))
            else:
                angle_deg = 90.0  # 兜底

            angle_matrix[(code_i, code_j)] = angle_deg
            angle_matrix[(code_j, code_i)] = angle_deg
            is_same_dir = angle_deg <= THETA_MAX
            compatible[(code_i, code_j)] = is_same_dir
            compatible[(code_j, code_i)] = is_same_dir

    logging.info(f"[adapter] 夹角矩阵: {len(angle_matrix)} 对, "
                 f"同方向={sum(1 for v in compatible.values() if v)}")
    return angle_matrix, compatible


def _build_transport_teams(daily_vehicle_limits, work_days, VNums, vehicle_types=None):
    """将每日车辆配额转为 v5_5 transport_teams 格式"""
    transport_teams = []
    vehicle_pool = defaultdict(int)

    # 确定车型 ID 映射：用 vehicle_types（数据库 CAR_TYPE 实际值）
    if vehicle_types and len(vehicle_types) >= 1:
        type_big = vehicle_types[0]
    else:
        type_big = 1
    if vehicle_types and len(vehicle_types) >= 2:
        type_med = vehicle_types[1]
    else:
        type_med = 2
    if vehicle_types and len(vehicle_types) >= 3:
        type_small = vehicle_types[2]
    else:
        type_small = 3

    if daily_vehicle_limits and work_days is not None:
        n_days = len(work_days)
        for day_idx in range(n_days):
            day_counts = daily_vehicle_limits.get(day_idx, {})
            big = day_counts.get(type_big, 0)
            med = day_counts.get(type_med, 0)
            small = day_counts.get(type_small, 0)
            # 每天都加，即使无车也要占位，保持日期索引对齐
            transport_teams.append({'vehicles': [big, med, small]})
            vehicle_pool[type_big] += big
            vehicle_pool[type_med] += med
            vehicle_pool[type_small] += small

    if not transport_teams:
        # 兜底：从 VNums 构造单队
        n_days = len(work_days) if work_days is not None else 1
        big = int(VNums[0]) if len(VNums) > 0 else 0
        med = int(VNums[1]) if len(VNums) > 1 else 0
        small = int(VNums[2]) if len(VNums) > 2 else 0
        for _ in range(n_days):
            transport_teams.append({'vehicles': [big, med, small]})

    logging.info(f"[adapter] transport_teams: {len(transport_teams)} 队, "
                 f"车辆池={dict(vehicle_pool)}")
    return transport_teams, dict(vehicle_pool)


def _build_vehicles(VeCap, team_vehicles_total, VNums, vehicle_types=None):
    """构建 v5_5 vehicles 列表。

    VeCap[i], VNums[i] 都对应 vehicle_types[i]（CAR_TYPE 升序）。
    按容量降序映射到 v5_5：最大=大车(k=0)，中间=中车(k=1)，最小=小车(k=2)。
    """
    n_types = len(VeCap)
    if n_types == 0:
        return [{'name': '大车', 'capacity': 1071, 'max_trips': 0}]

    # 按容量降序排列，记录原始索引
    order = sorted(range(n_types), key=lambda i: float(VeCap[i]), reverse=True)
    names = ['大车', '中车', '小车'][:n_types]

    vehicles = []
    for k, orig_idx in enumerate(order):
        cap = int(float(VeCap[orig_idx]))
        car_type = vehicle_types[orig_idx] if vehicle_types and orig_idx < len(vehicle_types) else (k + 1)
        max_t = 0
        if team_vehicles_total and car_type in team_vehicles_total:
            max_t = team_vehicles_total[car_type]
        elif orig_idx < len(VNums):
            max_t = int(VNums[orig_idx])
        vehicles.append({'name': names[k], 'capacity': cap, 'max_trips': max_t})

    return vehicles


def _build_priority_tasks(node_priority, branch_codes, demand_boxes, LocationNum):
    """旧版：简单阈值过滤（保留兼容）"""
    priority_tasks = {}
    if node_priority is None:
        return priority_tasks
    for i in range(1, LocationNum + 1):
        prob = float(node_priority[i]) if i < len(node_priority) else 0.0
        if prob > 0.3 and i < len(branch_codes):
            code = branch_codes[i]
            boxes = int(demand_boxes[i - 1]) if i - 1 < len(demand_boxes) else 0
            if boxes > 0:
                priority_tasks[code] = boxes
    return priority_tasks


def _build_priority_tasks_v2(node_priority, branch_codes, demand_boxes, LocationNum,
                              cur_date, code_to_idx):
    """新版：用 stockout_detector 公式（D12/D3/T/t/z）计算缺货，输出 prio tasks"""
    priority_tasks = {}

    if cur_date is None:
        logging.warning("[adapter] 无日期，缺货检测跳过")
        return priority_tasks

    try:
        from backend.Scheduling.stockout_detector import detect_stockout
        date_str = cur_date.strftime('%Y-%m-%d') if hasattr(cur_date, 'strftime') else str(cur_date)[:10]
        df = detect_stockout(date_str)
        if df.empty:
            logging.info("[adapter] 缺货检测：无缺货风险")
            return priority_tasks

        # 按 ORG 汇总原始箱数
        org_boxes = df.groupby('ORG')['原始箱数'].sum()
        for org, boxes in org_boxes.items():
            if boxes > 0:
                # ORG → branch_code → priority_tasks
                code = str(org).strip()
                if code in code_to_idx:
                    priority_tasks[code] = int(boxes)

        logging.info(f"[adapter] 缺货检测: {len(df)} 条风险 → {len(priority_tasks)} 个优先网点, "
                     f"总优先箱数={sum(priority_tasks.values())}")
    except Exception as e:
        logging.warning(f"[adapter] 缺货检测失败({e})，回退到旧版阈值过滤")
        priority_tasks = _build_priority_tasks(
            node_priority, branch_codes, demand_boxes, LocationNum)

    return priority_tasks


def build_teams_dict(transport_teams):
    """构建 scheduler 需要的 teams 字典 {工作日序号: [大车,中车,小车]}"""
    return {i + 1: list(t['vehicles']) for i, t in enumerate(transport_teams)}
