"""
日配送调整 — 复用 Scheduling 模块 GetDelivPlan (ALNS)

流程:
    1. _v3_load_deliv_data  — 加载数据，DMAT 从数据库里程表构建（与 Scheduling 一致）
    2. GetDelivPlan         — ALNS 装箱 + 路由求解（复用 Scheduling）
    3. 自定义拆箱           — 体积箱 → 件数（自己算）
    4. 自定义归并           — 低满载率路线合并（自己算）
    5. 重算 DIST_EXP        — PLAN_BOX_NUM × EST_TOT_DIST_MIST × 单价
"""
import numpy as np
import pandas as pd
import logging
import sys
import time
import datetime
import itertools
import requests
from collections import defaultdict

from backend.config.config import API_CONFIG
from backend.Scheduling.GetDelivPlan import GetDelivPlan


# ==================== V3 数据加载 ====================

def _v3_load_deliv_data(date: str):
    """
    V3 专用数据加载。与 DailyReplenishmentPlan.LoadDelivData 相比:
        - 需求载入、已确认扣除、车辆配置等逻辑完全复用
        - DMAT 改为从数据库里程表 gk-adam-query_distance_matrix 构建（与 Scheduling 模块一致）
        - 不对互感器做 PACK_BOX_NUM/3 体积折算（adjust_pack_box=False）

    返回:
        Demands, LocationNum, SubTypeList, VeUnitPrice, VeTypeNum, VNums, VeCap, DMat,
        MaxLen, VeType, lons, lats, org_labels
    """
    from backend.api.data_api.fetch_data import (
        query_adam_spec_code_config,
        query_adam_del_site_conf,
        query_adam_plan_day_ias_pre_by_date,
        query_vehicle_conf,
    )

    # ---- 1. 设备规格配置 ----
    SubTypeList = query_adam_spec_code_config()
    SubTypeList['PACK_BOX_NUM_ORIG'] = SubTypeList['PACK_BOX_NUM']
    logging.info(f'载入配送数据：查询到{len(SubTypeList)}条规格设备码数据')
    logging.info('V3 模式：跳过互感器体积折算，保留原始 PACK_BOX_NUM')
    SubTypeNum = len(SubTypeList)

    # ---- 2. 配送站点信息 ----
    tb1 = query_adam_del_site_conf()
    logging.info(f'载入配送数据：查询到{len(tb1)}条配送站点信息')

    marketing_center = tb1[tb1['STAT_NAME'] == '营销服务中心']
    center_org = str(marketing_center['ORG_NO'].iloc[0]).strip()
    center_lon = marketing_center['LONGITUDE'].iloc[0]
    center_lat = marketing_center['LATITUDE'].iloc[0]
    # DMAT 中心编码：距离矩阵表中省级总库固定为 '34101'（与 Scheduling/LoadDeliChcekData 一致）
    dmat_center_org = '34101'
    logging.info(f'载入配送数据：识别到营销服务中心 ORG_NO={center_org}, DMAT中心编码={dmat_center_org}')

    tb1 = tb1[tb1['STAT_NAME'] != '营销服务中心'].sort_values('ORG_NO').reset_index(drop=True)
    LocationNum = len(tb1)
    logging.info(f'载入配送数据：筛选出{LocationNum}个非营销服务中心站点')

    # ---- 3. 当日补库计划 ----
    tb2 = query_adam_plan_day_ias_pre_by_date(date)
    logging.info(f'载入配送数据：查询到{len(tb2)}条当日补库计划')
    if not tb2.empty and 'REPLE_TASK_TYPE' in tb2.columns:
        type_counts = tb2['REPLE_TASK_TYPE'].value_counts().to_dict()
        logging.info(f'日补库计划分类: 总数={len(tb2)}, '
                     f'01(临时补库)= {type_counts.get("01", 0)}, '
                     f'02(紧急补库)= {type_counts.get("02", 0)}, '
                     f'03(日常补库)= {type_counts.get("03", 0)}')

    # 构建 lons/lats（即使 DMAT 不靠经纬度算，返回值签名仍需保留）
    lons = [center_lon] + list(tb1['LONGITUDE'])
    lats = [center_lat] + list(tb1['LATITUDE'])

    if tb2.empty:
        logging.warning(f'当日 ({date}) 无补库计划，返回空需求')
        VeCap, VNums, VeUnitPrice, VeTypeNum, VeType = query_vehicle_conf()
        Demands = pd.DataFrame(np.zeros((LocationNum, SubTypeNum)))
        org_labels = [center_org] + list(tb1['ORG_NO'])
        DMat = pd.DataFrame(np.zeros((LocationNum + 1, LocationNum + 1)),
                            index=org_labels, columns=org_labels)
        return Demands, LocationNum, SubTypeList, VeUnitPrice, VeTypeNum, VNums, VeCap, DMat, \
            2, VeType, lons, lats, org_labels

    # ---- 4. 构建需求矩阵 ----
    Location = tb1['ORG_NO']
    LocationInd = tb2['REC_ORG_NO']
    SubTypeInd = tb2['DEV_CODE']
    Number = tb2['PLAN_IAS_NUM']
    SubType = SubTypeList['DEV_CODE']
    Demands = np.zeros((LocationNum, SubTypeNum))
    for i in range(LocationNum):
        for j in range(SubTypeNum):
            idx = (LocationInd == Location[i]) & (SubTypeInd == SubType[j])
            if idx.any():
                Demands[i, j] = Number[idx].values[0]
    Demands = pd.DataFrame(Demands)

    MaxLen = 3 if len(set(LocationInd)) >= 3 else 2

    # ---- 5. 车辆配置 ----
    VeCap, VNums, VeUnitPrice, VeTypeNum, VeType = query_vehicle_conf()

    # ---- 6. 扣除已确认配送计划的需求和车辆 ----
    from backend.api.data_api.fetch_data import (
        query_adam_dist_scheme_by_date_range,
        query_adam_dist_scheme_det_by_distschemeid)
    try:
        existing_schemes = query_adam_dist_scheme_by_date_range(date, date)
        confirmed = existing_schemes[existing_schemes['DIST_FLAG'] == '02']
        if not confirmed.empty and not tb2.empty:
            org_to_idx = {org: i for i, org in enumerate(Location)}
            dev_to_idx = {dev: j for j, dev in enumerate(SubType)}
            for _, crow in confirmed.iterrows():
                ct = crow['CAR_TYPE']
                for vi in range(VeTypeNum):
                    if VeType[vi] == ct and VNums[vi] > 0:
                        VNums[vi] -= 1
                        break
                sid = crow['DIST_SCHEME_ID']
                dets = query_adam_dist_scheme_det_by_distschemeid(sid)
                for _, drow in dets.iterrows():
                    oi = org_to_idx.get(drow['REC_ORG_NO'])
                    di = dev_to_idx.get(drow['DEV_CODE'])
                    if oi is not None and di is not None:
                        Demands.iloc[oi, di] = max(0, Demands.iloc[oi, di] - drow['PLAN_DIST_NUM'])
            logging.info(f'扣除已确认方案: {len(confirmed)} 条, 剩余可用车辆 {[int(n) for n in VNums]}')
    except ValueError:
        pass

    # ---- 7. 从数据库加载实际运输里程矩阵（与 Scheduling 模块一致） ----
    logging.info(">>> 从数据库加载网点实际运输距离矩阵...")
    num_nodes = LocationNum + 1

    # 构建 DMAT 用 locations: index 0 = 中心('34101', 与 Scheduling 硬编码一致), 1..N = 站点
    center_row = pd.DataFrame([{'ORG_NO': dmat_center_org}])
    stations_rows = tb1[['ORG_NO']].reset_index(drop=True)
    locations = pd.concat([center_row, stations_rows], ignore_index=True)

    # ORG_NO → 矩阵索引映射
    org_to_dmat_idx = {str(locations.loc[i, 'ORG_NO']).strip(): i for i in range(num_nodes)}

    DMAT_arr = np.zeros((num_nodes, num_nodes))
    try:
        url = f"http://{API_CONFIG['database']['host']}:{API_CONFIG['database']['port']}/exec/gk-adam-query_distance_matrix"
        response = requests.post(url, json={}, timeout=30)
        response.raise_for_status()
        data = response.json()
        df_dist = pd.DataFrame(data) if isinstance(data, list) else pd.DataFrame()
        if not df_dist.empty:
            df_dist.columns = [c.upper() for c in df_dist.columns]
            matched = 0
            unmatched = []
            for _, r in df_dist.iterrows():
                from_org = str(r['DIST_ORG_NO']).strip()
                to_org = str(r['RECEIVE_ORG_NO']).strip()
                dist_val = float(r['DIST_MIST'])
                fi = org_to_dmat_idx.get(from_org)
                ti = org_to_dmat_idx.get(to_org)
                if fi is not None and ti is not None and dist_val > 0:
                    DMAT_arr[fi, ti] = dist_val
                    matched += 1
                elif dist_val > 0:
                    if fi is None: unmatched.append(from_org)
                    if ti is None: unmatched.append(to_org)
            unmatched_set = list(set(unmatched))
            logging.info(f"距离矩阵: {len(df_dist)}条记录, 成功匹配{matched}对, "
                         f"覆盖率={matched/max(num_nodes*num_nodes,1)*100:.1f}%")
            if unmatched_set:
                logging.warning(f"⚠ 未匹配的ORG_NO ({len(unmatched_set)}个), "
                                f"本地样本: {list(org_to_dmat_idx.keys())[:3]}, "
                                f"表样本: {unmatched_set[:5]}{'...' if len(unmatched_set) > 5 else ''}")
        else:
            logging.warning("未获取到实际距离数据，矩阵全为0！")
    except Exception as e:
        logging.warning(f"查询距离矩阵失败: {e}，矩阵全为0！")

    # 对称化：与 GetDelivPlan 内部处理一致，兼容方向性/上三角矩阵
    DMAT_arr = np.maximum(DMAT_arr, DMAT_arr.T)

    # 构建 org_labels 和 DMat DataFrame
    org_labels = [center_org] + list(tb1['ORG_NO'])

    # 诊断：检查中心(索引0)→各站点的距离是否缺失
    center_dists = DMAT_arr[0, 1:]
    n_center_zero = int((center_dists <= 0.001).sum())
    if n_center_zero > 0:
        zero_stations = [org_labels[i + 1] for i in range(LocationNum) if center_dists[i] <= 0.001]
        logging.warning(f"⚠ 中心({center_org})→{n_center_zero}/{LocationNum}个站点距离为0: "
                        f"{zero_stations[:5]}{'...' if n_center_zero > 5 else ''}")
    DMat = pd.DataFrame(DMAT_arr, index=org_labels, columns=org_labels)

    logging.info(f"DMAT 构建完成: {num_nodes}×{num_nodes}, "
                 f"非零比={np.count_nonzero(DMAT_arr)/DMAT_arr.size*100:.1f}%")

    return Demands, LocationNum, SubTypeList, VeUnitPrice, VeTypeNum, VNums, VeCap, DMat, \
        MaxLen, VeType, lons, lats, org_labels


# ==================== V3 主入口 ====================

def AdjustDaliyDeliveryV3(date: str, max_stops: int = 3, max_iter: int = 600):
    """
    日配送调整 V3 — 复用 Scheduling 模块的 GetDelivPlan (ALNS)。

    参数:
        date:       配送日期，格式 'YYYY-MM-DD'
        max_stops:  每条路径最多经过的站点数（默认 3）
        max_iter:   启发式迭代次数（默认 600）

    返回:
        (MainScheme, DetailScheme) — 与 AdjustDaliyDelivery / AdjustDaliyDeliveryV2 格式一致
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        stream=sys.stdout
    )

    try:
        return _adjust_daily_delivery_v3_impl(date, max_stops, max_iter)
    except Exception:
        logging.exception(f"AdjustDaliyDeliveryV3 异常: date={date}")
        raise


def _adjust_daily_delivery_v3_impl(date: str, max_stops: int, max_iter: int):
    """V3 算法实现体"""

    from backend.api.data_api.fetch_data import (
        query_adam_dist_scheme_by_date_range,
        delete_adam_dist_scheme_det_by_scheme_id,
        delete_adam_dist_scheme_by_id
    )

    # ---- 0. 删除当天未确认的旧方案 ----
    try:
        existing = query_adam_dist_scheme_by_date_range(date, date)
        if not existing.empty:
            unconfirmed = existing[existing['DIST_FLAG'] != '02']
            for sid in unconfirmed['DIST_SCHEME_ID'].tolist():
                delete_adam_dist_scheme_det_by_scheme_id(sid)
                delete_adam_dist_scheme_by_id(sid)
            kept = len(existing) - len(unconfirmed)
            logging.info(f"已删除 {len(unconfirmed)} 条未确认方案" + (f"，保留 {kept} 条已确认方案" if kept > 0 else ""))
    except ValueError:
        logging.info(f"当天 ({date}) 无旧配送方案，跳过删除")

    # ---- 1. 前置：载入数据（V3 专用：DMAT 从数据库里程表构建） ----
    logging.info("=" * 60)
    logging.info(f"V3 日配送开始: date={date}, max_stops={max_stops}, max_iter={max_iter}")
    t_start = time.time()

    Demands, LocationNum, SubTypeList, VeUnitPrice, VeTypeNum, VNums, VeCap, DMAT, _, CarTypeStrList, lons, lats, org_labels = \
        _v3_load_deliv_data(date)
    SubTypeNum = len(SubTypeList)
    logging.info(f"数据载入完成: {LocationNum} 个配送站点, {SubTypeNum} 种设备, {VeTypeNum} 种车型")
    logging.info(f"车型容量: {[int(c) for c in VeCap]}, 各车型数量: {[int(n) for n in VNums]}")

    # 空方案检查
    empty_cols = {
        'main': ['DIST_SCHEME_ID', 'CAR_TYPE', 'PLAN_DIST_DATE', 'DIST_FLAG', 'LATE_FLAG',
                 'LOAD_RATE', 'CREATE_DATE', 'UPDATE_DATE', 'GLOBAL_SCHEME_ID'],
        'detail': ['DIST_SCHEME_DET_ID', 'DIST_SCHEME_ID', 'REC_ORG_NO', 'DEV_CODE',
                   'DEV_CLS', 'DEV_CATEG', 'DIST_SEQ', 'LOAD_SEQ', 'PLAN_DIST_NUM',
                   'PLAN_BOX_NUM', 'EST_TOT_DIST_MIST', 'DIST_EXP', 'GLOBAL_SCHEME_ID']
    }
    total_pieces = Demands.values.sum()
    if total_pieces == 0:
        logging.warning("无配送需求，返回空方案")
        return pd.DataFrame(columns=empty_cols['main']), pd.DataFrame(columns=empty_cols['detail'])
    if sum(VNums) == 0:
        logging.warning("无可用车辆，返回空方案")
        return pd.DataFrame(columns=empty_cols['main']), pd.DataFrame(columns=empty_cols['detail'])

    # ---- 2. 中间：调 Scheduling GetDelivPlan (ALNS 路径规划) ----
    logging.info("-" * 40)
    logging.info(f"开始 GetDelivPlan: {LocationNum} 站点, {SubTypeNum} 设备, DelivDay=1")

    # TypeList: 仅 Scheduling 拆箱时用到(按DEV_CODE_NO查UnitPerBox)，GetDelivPlan 内部不实际使用
    TypeList = SubTypeList[['DEV_CODE', 'PACK_BOX_NUM']].copy()
    TypeList.rename(columns={'DEV_CODE': 'DEV_CODE_NO', 'PACK_BOX_NUM': 'UnitPerBox'}, inplace=True)

    # Demands: (LocationNum × SubTypeNum) DataFrame → numpy
    Demands_arr = Demands.values if isinstance(Demands, pd.DataFrame) else Demands

    # DMAT_arr: 已由 _v3_load_deliv_data 做好对称化
    DMAT_arr = DMAT.values

    t_solve = time.time()
    best_sol = GetDelivPlan(
        Demands_arr, LocationNum,
        TypeList, SubTypeList,
        DelivDay=1,
        VeUnitPrice=VeUnitPrice, VeTypeNum=VeTypeNum,
        VNums=VNums, VeCap=VeCap, DMAT=DMAT
    )
    t_solve_end = time.time()

    if not best_sol:
        logging.warning("GetDelivPlan 返回空路线")
        return pd.DataFrame(columns=empty_cols['main']), pd.DataFrame(columns=empty_cols['detail'])

    logging.info(f"GetDelivPlan 完成: {len(best_sol)} 条路线, 耗时 {t_solve_end - t_solve:.1f}s")

    # 打印路线概览
    final_rates = []
    type_counts = defaultdict(int)
    for r in best_sol:
        cap = VeCap[int(r.get('vehicle_type', 1)) - 1]
        load = sum(a for _, a in r['deliveries'])
        rate = load / cap * 100 if cap > 0 else 0
        final_rates.append(rate)
        type_counts[r['vehicle_type']] += 1
        logging.info(f"  车型={r['vehicle_type']}, 站点数={len(r['deliveries'])}, 体积箱数={load}, 满载率={rate:.1f}%")
    logging.info(f"满载率统计: avg={np.mean(final_rates):.1f}%, min={np.min(final_rates):.1f}%, max={np.max(final_rates):.1f}%")

    # ---- 3. best_sol → 主表（不依赖拆箱） ----
    logging.info("-" * 40)
    logging.info("生成主表...")
    MainScheme = _v3_generate_main_scheme(best_sol, VeCap, CarTypeStrList, date)
    logging.info(f"主表: {len(MainScheme)} 行")

    # ---- 4. 拆箱 → 明细表（直接计算 DIST_EXP） ----
    logging.info("拆箱生成明细表...")
    DetailScheme = _v3_unbox_to_detail(
        best_sol, MainScheme, Demands, SubTypeList, VeUnitPrice, DMAT_arr, org_labels
    )
    logging.info(f"明细表: {len(DetailScheme)} 行")

    # ---- 5. 核验：配送明细件数 == 需求件数 ----
    _v3_verify_delivery(Demands, DetailScheme, SubTypeList, org_labels)

    t_end = time.time()
    logging.info(f"V3 总耗时: {t_end - t_start:.1f}s")
    logging.info("=" * 60)
    return MainScheme, DetailScheme


# ==================== V3 主表生成（直接从 best_sol） ====================

def _v3_generate_main_scheme(best_sol, VeCap, CarTypeStrList, date):
    """
    直接从 best_sol 生成主表，不需拆箱。

    best_sol 每条路线包含:
        vehicle_type → CAR_TYPE、LOAD_RATE
        deliveries   → 体积箱合计 → LOAD_RATE
    """
    GlobalSchemeId = int(date.replace('-', ''))
    CurrentDateStr = datetime.datetime.now().strftime('%Y-%m-%d')
    base_ts = int(time.time() * 1000)

    main_cols = ['DIST_SCHEME_ID', 'CAR_TYPE', 'PLAN_DIST_DATE', 'DIST_FLAG', 'LATE_FLAG',
                 'LOAD_RATE', 'CREATE_DATE', 'UPDATE_DATE', 'GLOBAL_SCHEME_ID']
    rows = []
    for i, route in enumerate(best_sol):
        v_type = int(route.get('vehicle_type', 1))
        v_idx = v_type - 1
        ve_cap = float(VeCap[v_idx])
        total_vol = sum(amt for _, amt in route['deliveries'])
        load_rate = f"{min(total_vol / ve_cap * 100, 100.0):.1f}%" if ve_cap > 0 else "0%"

        rows.append({
            'DIST_SCHEME_ID': base_ts * 1000 + i + 1,
            'CAR_TYPE': CarTypeStrList[v_idx],
            'PLAN_DIST_DATE': date,
            'DIST_FLAG': '01',
            'LATE_FLAG': '01',
            'LOAD_RATE': load_rate,
            'CREATE_DATE': CurrentDateStr,
            'UPDATE_DATE': CurrentDateStr,
            'GLOBAL_SCHEME_ID': GlobalSchemeId
        })

    return pd.DataFrame(rows, columns=main_cols) if rows else pd.DataFrame(columns=main_cols)


# ==================== V3 拆箱 → 明细表 ====================

def _v3_unbox_to_detail(best_sol, MainScheme, Demands, SubTypeList, VeUnitPrice,
                         DMAT_arr, org_labels):
    """
    从 best_sol 拆箱生成 DetailScheme。

    核心逻辑:
        - 追踪剩余需求，每次分配基于剩余量动态计算体积箱占比
        - 硬封顶不超过剩余需求，确保 Σ配送件数 ≤ 原始需求
    """
    LocationNum = Demands.shape[0]
    SubTypeNum = len(SubTypeList)

    # 预计算设备属性
    dev_attrs = []
    for dev_idx in range(SubTypeNum):
        box_cap = float(SubTypeList.iloc[dev_idx]['PACK_BOX_NUM'])
        cls_val = str(SubTypeList.iloc[dev_idx].get('DEV_CLS', '')).replace('.0', '').strip().zfill(2)
        vol_mult = 2.5 if cls_val == '02' else 1.0
        dev_attrs.append((
            box_cap, vol_mult,
            SubTypeList.iloc[dev_idx]['DEV_CODE'],
            SubTypeList.iloc[dev_idx].get('DEV_CLS', ''),
            SubTypeList.iloc[dev_idx].get('DEV_CATEG', '')
        ))

    # 追踪剩余需求
    remaining = Demands.copy().astype(float)

    detail_cols = ['DIST_SCHEME_DET_ID', 'DIST_SCHEME_ID', 'REC_ORG_NO', 'DEV_CODE',
                   'DEV_CLS', 'DEV_CATEG', 'DIST_SEQ', 'LOAD_SEQ', 'PLAN_DIST_NUM',
                   'PLAN_BOX_NUM', 'EST_TOT_DIST_MIST', 'DIST_EXP', 'GLOBAL_SCHEME_ID']
    base_ts = int(time.time() * 1000)
    GlobalSchemeId = int(MainScheme['GLOBAL_SCHEME_ID'].iloc[0]) if not MainScheme.empty else 0

    rows = []
    det_seq = 0

    for r_idx, route in enumerate(best_sol):
        scheme_id = MainScheme['DIST_SCHEME_ID'].iloc[r_idx]
        v_type = int(route.get('vehicle_type', 1))
        unit_price = float(VeUnitPrice[v_type - 1])
        deliveries = route['deliveries']

        seg_dists = []
        prev_node = 0
        for node_id, _ in deliveries:
            seg_dists.append(DMAT_arr[prev_node, node_id])
            prev_node = node_id

        # 诊断：检查零距离段
        for s_idx, (nid, vol) in enumerate(deliveries):
            sd = seg_dists[s_idx]
            if sd <= 0.001 and vol > 0:
                prev_n = 0 if s_idx == 0 else deliveries[s_idx - 1][0]
                logging.warning(f"⚠ 零距离段: 路线{r_idx}, 段{s_idx}, "
                                f"prev_node={prev_n}→node={nid}, "
                                f"ORG={org_labels[nid]}, vol_boxes={vol}, "
                                f"DMAT[{prev_n},{nid}]={DMAT_arr[prev_n, nid]}")

        for stop_idx, (node_id, vol_boxes_delivered) in enumerate(deliveries):
            loc_idx = node_id - 1

            if vol_boxes_delivered <= 0:
                continue

            # 基于剩余需求动态计算体积箱构成
            cur_devs = []
            cur_total_vol = 0.0
            for dev_idx in range(SubTypeNum):
                rem = remaining.iloc[loc_idx, dev_idx]
                if rem <= 0:
                    continue
                box_cap, vol_mult, _, _, _ = dev_attrs[dev_idx]
                reg = np.ceil(rem / box_cap)
                vol = np.ceil(reg * vol_mult)
                cur_devs.append((dev_idx, vol, box_cap, vol_mult, rem))
                cur_total_vol += vol

            if cur_total_vol <= 0:
                continue

            ratio = min(vol_boxes_delivered / cur_total_vol, 1.0)

            for dev_idx, vol, box_cap, vol_mult, rem in cur_devs:
                alloc_regular = int(np.ceil(vol * ratio / vol_mult))
                alloc_pieces = int(min(alloc_regular * box_cap, rem))
                if alloc_pieces <= 0:
                    continue
                remaining.iloc[loc_idx, dev_idx] -= alloc_pieces
                plan_box_num = int(np.ceil(alloc_pieces / box_cap))
                _, _, dev_code, dev_cls, dev_categ = dev_attrs[dev_idx]
                seg_dist = seg_dists[stop_idx]
                det_seq += 1

                rows.append({
                    'DIST_SCHEME_DET_ID': base_ts * 1000 + det_seq,
                    'DIST_SCHEME_ID': scheme_id,
                    'REC_ORG_NO': str(org_labels[node_id]),
                    'DEV_CODE': dev_code,
                    'DEV_CLS': dev_cls,
                    'DEV_CATEG': dev_categ,
                    'DIST_SEQ': stop_idx + 1,
                    'LOAD_SEQ': len(deliveries) - stop_idx,
                    'PLAN_DIST_NUM': alloc_pieces,
                    'PLAN_BOX_NUM': plan_box_num,
                    'EST_TOT_DIST_MIST': round(seg_dist, 4),
                    'DIST_EXP': round(unit_price * plan_box_num * seg_dist, 4),
                    'GLOBAL_SCHEME_ID': GlobalSchemeId
                })

    DetailDf = pd.DataFrame(rows, columns=detail_cols) if rows else pd.DataFrame(columns=detail_cols)

    total_demand = Demands.values.sum()
    total_deliv = DetailDf['PLAN_DIST_NUM'].sum() if not DetailDf.empty else 0
    unalloc = remaining.values.sum()
    logging.info(f"[V3校验] 需求总件数: {int(total_demand)}, 配送总件数: {int(total_deliv)}, "
                 f"未分配: {int(unalloc)}")

    return DetailDf


# ==================== V3 数量核验 ====================

def _v3_verify_delivery(Demands, DetailScheme, SubTypeList, org_labels):
    """
    硬核验：配送明细 (REC_ORG_NO, DEV_CODE) 汇总件数 必须等于 需求件数。
    不一致则抛出 ValueError。
    """
    if DetailScheme.empty:
        total_demand = Demands.values.sum()
        if total_demand > 0:
            raise ValueError(f"[V3核验失败] 有需求({int(total_demand)}件)但明细表为空！")
        return

    # 构建需求表: (ORG_NO, DEV_CODE) → 件数
    dev_code_list = SubTypeList['DEV_CODE'].tolist()
    demand_map = {}
    for loc_idx in range(Demands.shape[0]):
        org_no = str(org_labels[loc_idx + 1])  # org_labels[0] = 中心
        for dev_idx, dev_code in enumerate(dev_code_list):
            qty = int(Demands.iloc[loc_idx, dev_idx])
            if qty > 0:
                key = (org_no, str(dev_code))
                demand_map[key] = demand_map.get(key, 0) + qty

    # 汇总明细表
    deliv_map = {}
    for _, row in DetailScheme.iterrows():
        key = (str(row['REC_ORG_NO']), str(row['DEV_CODE']))
        deliv_map[key] = deliv_map.get(key, 0) + int(row['PLAN_DIST_NUM'])

    # 比对
    all_keys = set(demand_map.keys()) | set(deliv_map.keys())
    errors = []
    for key in sorted(all_keys):
        dmd = demand_map.get(key, 0)
        dlv = deliv_map.get(key, 0)
        if dmd != dlv:
            errors.append(f"  {key[0]} / {key[1]}: 需求={dmd}, 配送={dlv}, 差异={dlv - dmd}")

    if errors:
        raise ValueError(
            f"[V3核验失败] 配送数量与需求不一致，共 {len(errors)} 处差异:\n" + "\n".join(errors))

    logging.info(f"[V3核验通过] {len(demand_map)} 个 (站点,设备码) 组合，配送数量 == 需求数量 ✓")


# ==================== V3 归并低满载率路线 ====================

def _v3_merge_scheme_tables(MainDf, DetailDf, VeCap, CarTypeStrList, max_stops, DMAT_arr, org_labels):
    """
    V3 归并低满载率配送方案。

    规则:
        - 相同车型、合并后满载率 < 100%、站点数 ≤ max_stops
        - 重新排序站点使路径最短
        - 满载率使用体积箱计算（互感器 ×2.5）
    """
    if len(MainDf) <= 1:
        logging.info("无需归并")
        return MainDf, DetailDf

    org_to_idx = {str(org): i for i, org in enumerate(org_labels)}
    car_type_to_idx = {ct: i for i, ct in enumerate(CarTypeStrList)}

    main_rows = MainDf.to_dict('records')
    detail_groups = {}
    for _, row in DetailDf.iterrows():
        sid = row['DIST_SCHEME_ID']
        detail_groups.setdefault(sid, []).append(row.to_dict())

    total_merged = 0

    while True:
        best_pair = None
        best_combined_rate = -1.0

        for i in range(len(main_rows)):
            for j in range(i + 1, len(main_rows)):
                if main_rows[i]['CAR_TYPE'] != main_rows[j]['CAR_TYPE']:
                    continue
                ri = float(str(main_rows[i]['LOAD_RATE']).rstrip('%'))
                rj = float(str(main_rows[j]['LOAD_RATE']).rstrip('%'))
                if ri + rj >= 100:
                    continue

                sid_i = main_rows[i]['DIST_SCHEME_ID']
                sid_j = main_rows[j]['DIST_SCHEME_ID']
                stops_i = {str(d['REC_ORG_NO']) for d in detail_groups.get(sid_i, [])}
                stops_j = {str(d['REC_ORG_NO']) for d in detail_groups.get(sid_j, [])}
                if len(stops_i | stops_j) > max_stops:
                    continue

                if ri + rj > best_combined_rate:
                    best_combined_rate = ri + rj
                    best_pair = (i, j)

        if best_pair is None:
            break

        i, j = best_pair
        sid_i = main_rows[i]['DIST_SCHEME_ID']
        sid_j = main_rows[j]['DIST_SCHEME_ID']
        car_type = main_rows[i]['CAR_TYPE']
        rate_i = float(str(main_rows[i]['LOAD_RATE']).rstrip('%'))
        rate_j = float(str(main_rows[j]['LOAD_RATE']).rstrip('%'))

        combined_details = detail_groups.pop(sid_i, []) + detail_groups.pop(sid_j, [])

        stop_boxes = defaultdict(float)
        for d in combined_details:
            stop_boxes[str(d['REC_ORG_NO'])] += float(d['PLAN_BOX_NUM'])

        unique_stops = list(stop_boxes.keys())
        best_order = unique_stops
        best_path_dist = float('inf')
        for perm in itertools.permutations(unique_stops):
            dist = DMAT_arr[0, org_to_idx[perm[0]]]
            for k in range(len(perm) - 1):
                dist += DMAT_arr[org_to_idx[perm[k]], org_to_idx[perm[k + 1]]]
            if dist < best_path_dist:
                best_path_dist = dist
                best_order = list(perm)

        total_merged += 1
        new_details = []
        for pos, org in enumerate(best_order):
            dist_seq = pos + 1
            load_seq = len(best_order) - pos
            org_details = [d for d in combined_details if str(d['REC_ORG_NO']) == org]
            for d in org_details:
                nd = d.copy()
                nd['DIST_SCHEME_ID'] = sid_i
                nd['DIST_SEQ'] = dist_seq
                nd['LOAD_SEQ'] = load_seq
                if pos == 0:
                    nd['EST_TOT_DIST_MIST'] = round(DMAT_arr[0, org_to_idx[org]], 4)
                else:
                    prev_org = best_order[pos - 1]
                    nd['EST_TOT_DIST_MIST'] = round(DMAT_arr[org_to_idx[prev_org], org_to_idx[org]], 4)
                new_details.append(nd)

        # 计算新装载率（体积箱：互感器 ×2.5）
        real_boxes = 0.0
        for d in new_details:
            boxes = float(d['PLAN_BOX_NUM'])
            if str(d.get('DEV_CLS', '')).replace('.0', '').strip().zfill(2) == '02':
                boxes *= 2.5
            real_boxes += boxes
        ve_idx = car_type_to_idx.get(car_type, -1)
        ve_cap = VeCap[ve_idx] if 0 <= ve_idx < len(VeCap) else 1
        new_load_rate = f"{min(real_boxes / ve_cap * 100, 100.0):.1f}%"

        main_rows[i]['LOAD_RATE'] = new_load_rate
        main_rows[i]['UPDATE_DATE'] = datetime.datetime.now().strftime('%Y-%m-%d')
        main_rows = [r for idx, r in enumerate(main_rows) if idx != j]
        detail_groups[sid_i] = new_details

        logging.info(f"[归并] {car_type}: 合并2条方案, "
                     f"原满载率={rate_i:.1f}%+{rate_j:.1f}%→{new_load_rate}, 站点数={len(best_order)}")

    if total_merged > 0:
        all_details = [d for details in detail_groups.values() for d in details]
        new_MainDf = pd.DataFrame(main_rows)
        new_DetailDf = pd.DataFrame(all_details) if all_details else pd.DataFrame(columns=DetailDf.columns)
        logging.info(f"[归并] 共归并 {total_merged} 对方案, 最终 {len(new_MainDf)} 条主表记录")
        return new_MainDf, new_DetailDf
    else:
        logging.info("无需归并")
        return MainDf, DetailDf


# ==================== V3 重算 DIST_EXP ====================

def _v3_recalc_dist_exp(MainDf, DetailDf, CarTypeStrList, VeUnitPrice):
    """
    落库前重算配送费用: DIST_EXP = PLAN_BOX_NUM × EST_TOT_DIST_MIST × 单价
    """
    if MainDf.empty or DetailDf.empty:
        return MainDf, DetailDf

    car_price_map = {CarTypeStrList[i]: VeUnitPrice[i] for i in range(len(CarTypeStrList))}
    scheme_price_map = MainDf.set_index('DIST_SCHEME_ID')['CAR_TYPE'].map(car_price_map)

    DetailDf = DetailDf.copy()
    DetailDf['DIST_EXP'] = (
        DetailDf['PLAN_BOX_NUM'].astype(float) *
        DetailDf['EST_TOT_DIST_MIST'].astype(float) *
        DetailDf['DIST_SCHEME_ID'].map(scheme_price_map)
    ).round(4)

    logging.info(f"DIST_EXP 重算完成, 总费用: {DetailDf['DIST_EXP'].sum():.2f}")
    return MainDf, DetailDf
