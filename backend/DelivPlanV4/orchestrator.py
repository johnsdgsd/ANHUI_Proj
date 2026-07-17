"""
编排模块

整合 DelivPlanV4 全流程:
    加载数据 → 需求转换 → 候选路径枚举 → ILP 求解 → 生成配送方案 → 核验
"""

import logging
import sys
import time

import numpy as np
import pandas as pd

from backend.inventory_optimization.SchedulingDeliveryAdapter import (
    _v3_load_deliv_data,
    _v3_generate_main_scheme,
    _v3_unbox_to_detail,
    _v3_verify_delivery,
)

from backend.DelivPlanV4.demand import (
    compute_volume_boxes,
    build_vehicle_config,
    split_large_demand,
)
from backend.DelivPlanV4.path_enumerator import enumerate_candidate_paths
from backend.DelivPlanV4.ilp_solver import solve_set_partition_ilp
from backend.DelivPlanV4.config import MAX_ROUTE_DIST


def run_deliv_plan_v4(date_str):
    """
    DelivPlanV4 日配送算法主入口。

    流程:
        1. 加载数据（复用 V3 数据加载）
        2. 需求件数 → 体积箱
        3. 构建车辆配置
        4. 运力校验
        5. 大需求拆分 → demand_units
        6. Stage 1: 候选路径枚举
        7. Stage 2: ILP 求解 → best_sol
        8. 生成主表 + 明细表
        9. 核验

    Args:
        date_str: 配送日期，格式 'YYYY-MM-DD'

    Returns:
        tuple: (MainScheme DataFrame, DetailScheme DataFrame)
    """
    from backend.api.data_api.fetch_data import (
        query_adam_dist_scheme_by_date_range,
        delete_adam_dist_scheme_det_by_scheme_id,
        delete_adam_dist_scheme_by_id,
        query_pk_next)

    t_total = time.time()
    timing = {}

    # ---- 0. 删除当天未确认的旧方案（保留已确认 DIST_FLAG='02'） ----
    t0 = time.time()
    logging.info("=" * 60)
    logging.info(f"[V4] ======== 开始日配送 date={date_str} ========")
    logging.info(f"[V4] Step 0/6: 清除旧未确认方案...")
    try:
        existing = query_adam_dist_scheme_by_date_range(date_str, date_str)
        if existing is not None and not existing.empty:
            unconfirmed = existing[existing['DIST_FLAG'] != '02']
            for sid in unconfirmed['DIST_SCHEME_ID'].tolist():
                delete_adam_dist_scheme_det_by_scheme_id(sid)
                delete_adam_dist_scheme_by_id(sid)
            kept = len(existing) - len(unconfirmed)
            logging.info(f"  已删除 {len(unconfirmed)} 条未确认旧方案" + (f"，保留 {kept} 条已确认" if kept > 0 else ""))
    except ValueError:
        logging.info("  无旧配送方案，跳过删除")
    timing['cleanup'] = time.time() - t0

    # ---- 1. 加载数据 ----
    t0 = time.time()
    logging.info(f"[V4] Step 1/6: 加载数据...")

    # 查询日补库原始总量，诊断矩阵构建损失
    total_replenish_pieces, matrix_expected = _query_daily_replenish_total(date_str)

    (demands, location_num, sub_type_list, ve_unit_price, ve_type_num,
     v_nums, ve_cap, dmat, _, car_type_str_list, _, _, org_labels
     ) = _v3_load_deliv_data(date_str)

    dmat_arr = dmat.values if isinstance(dmat, pd.DataFrame) else dmat
    # 只补齐缺失方向，不覆盖已有数据（正反向距离可能不对称，取大值会丢失方向信息）
    mask_zero = (dmat_arr == 0)
    mask_rev_has = (dmat_arr.T > 0)
    dmat_arr[mask_zero & mask_rev_has] = dmat_arr.T[mask_zero & mask_rev_has]

    timing['load'] = time.time() - t0

    # 扣减后净需求
    total_pieces_net = int(demands.values.sum()) if isinstance(demands, pd.DataFrame) else int(demands.sum())
    total_veh_available = sum(v_nums)
    matrix_loss = total_replenish_pieces - matrix_expected  # 矩阵构建损失
    confirmed_deducted = matrix_expected - total_pieces_net  # 已确认方案扣减

    parts = []
    if matrix_loss > 0:
        parts.append(f"矩阵构建损失{matrix_loss}件")
    if confirmed_deducted > 0:
        parts.append(f"已确认扣减{confirmed_deducted}件")
    gap_desc = " + ".join(parts) if parts else "无损失"
    logging.info(
        f"[V4] 汇总: 日补库{total_replenish_pieces}件 → "
        f"Demands矩阵{total_pieces_net}件 ({gap_desc}), "
        f"可用车辆{total_veh_available}辆, 耗时{timing['load']:.1f}s"
    )

    # DMAT 质量检查
    center_dists = dmat_arr[0, 1:]
    n_center_zero = int((center_dists <= 0.001).sum())
    if n_center_zero > 0:
        logging.warning(
            f"[V4] DMAT质量: 省库→{n_center_zero}/{len(center_dists)}站点距离为0, "
            f"这些站点无法参与路径规划"
        )
    logging.info(f"[V4] 数据加载完成: {location_num}站点, 耗时{timing['load']:.1f}s")

    # 空数据检查
    empty_main, empty_detail = _empty_schemes()
    total_pieces = demands.values.sum() if isinstance(demands, pd.DataFrame) else demands.sum()
    if total_pieces <= 0 or sum(v_nums) <= 0:
        logging.info("[V4] 无配送需求或无可用车辆，返回空方案")
        return empty_main, empty_detail

    # ---- 2. 需求 → 体积箱 ----
    t0 = time.time()
    logging.info(f"[V4] Step 2/6: 需求件数→体积箱转换...")
    unit_sum = compute_volume_boxes(demands, sub_type_list)
    total_demand_boxes = sum(unit_sum.values())
    timing['demand'] = time.time() - t0

    if not unit_sum:
        logging.info("[V4] 无体积箱需求，返回空方案")
        return empty_main, empty_detail

    logging.info(
        f"[V4] 需求转换: 总件数={int(total_pieces)}, 总体积箱={total_demand_boxes:.0f}箱, "
        f"{len(unit_sum)}个有需求站点, 耗时{timing['demand']:.1f}s"
    )

    # ---- 3. 车辆配置 + 运力校验 ----
    t0 = time.time()
    logging.info(f"[V4] Step 3/6: 车辆配置与运力校验...")
    vehicle_config = build_vehicle_config(ve_cap, v_nums, ve_type_num)
    max_cap = vehicle_config[-1]['cap']
    total_capacity = sum(cfg['cap'] * cfg['daily_max'] for cfg in vehicle_config)
    timing['vehicle'] = time.time() - t0

    if total_demand_boxes > total_capacity + 0.01:
        shortage = total_demand_boxes - total_capacity
        raise ValueError(
            f"【运力不足，请增加车辆】总需求 {total_demand_boxes:.0f} 箱 > "
            f"总运力 {total_capacity:.0f} 箱, 缺口 {shortage:.0f} 箱"
        )

    capacity_margin = (total_capacity - total_demand_boxes) / total_capacity * 100
    logging.info(
        f"[V4] 运力校验通过: 需求{total_demand_boxes:.0f}箱 ≤ 运力{total_capacity:.0f}箱, "
        f"余量{capacity_margin:.1f}%"
    )

    # ---- 4. 大需求拆分 ----
    t0 = time.time()
    logging.info(f"[V4] Step 4/6: 大需求拆分 (阈值={max_cap:.0f}箱)...")
    demand_units = split_large_demand(unit_sum, max_cap)
    timing['split'] = time.time() - t0

    # ---- 5. Stage 1: 候选路径枚举 ----
    t0 = time.time()
    logging.info(f"[V4] Step 5/6: Stage1 候选路径枚举...")
    candidates = enumerate_candidate_paths(
        demand_units, dmat_arr, max_cap, MAX_ROUTE_DIST
    )
    timing['stage1'] = time.time() - t0

    if not candidates:
        raise ValueError("Stage1 失败: 未生成任何候选路径，请检查距离和角度约束")

    # ---- 6. Stage 2: ILP 求解 ----
    t0 = time.time()
    logging.info(f"[V4] Step 6/6: Stage2 ILP求解...")
    best_sol = solve_set_partition_ilp(
        candidates, demand_units, vehicle_config, ve_unit_price
    )
    timing['stage2'] = time.time() - t0

    # ---- 7. 路线排序 ----
    best_sol.sort(
        key=lambda r: (r['vehicle_type'], sum(a for _, a in r['deliveries']))
    )

    # ---- 8. 配送方案概览 ----
    type_to_cap = {i + 1: float(ve_cap[i]) for i in range(ve_type_num)}
    _print_delivery_summary(best_sol, unit_sum, demand_units, type_to_cap, total_demand_boxes)

    # ---- 9. 生成主表 + 明细表 ----
    t0 = time.time()
    logging.info("[V4] 生成配送方案表...")
    main_scheme = _v3_generate_main_scheme(best_sol, ve_cap, car_type_str_list, date_str)
    detail_scheme = _v3_unbox_to_detail(
        best_sol, main_scheme, demands, sub_type_list, ve_unit_price, dmat_arr, org_labels
    )
    timing['output'] = time.time() - t0
    # 拆箱后统计
    total_deliv_pieces = int(detail_scheme['PLAN_DIST_NUM'].sum()) if not detail_scheme.empty else 0
    total_deliv_boxes = int(detail_scheme['PLAN_BOX_NUM'].sum()) if not detail_scheme.empty else 0
    total_deliv_cost = detail_scheme['DIST_EXP'].sum() if not detail_scheme.empty else 0
    logging.info(
        f"[V4] 方案生成: 主表{len(main_scheme)}行, 明细{len(detail_scheme)}行, "
        f"耗时{timing['output']:.1f}s"
    )
    logging.info(
        f"[V4] 拆箱统计: 配送总件数={total_deliv_pieces}, "
        f"配送总箱数={total_deliv_boxes}, 总运费={total_deliv_cost:.2f}元"
    )

    # ---- 10. 核验 ----
    t0 = time.time()
    try:
        _v3_verify_delivery(demands, detail_scheme, sub_type_list, org_labels)
    except ValueError as e:
        logging.error(f"[V4] ❌ 核验失败: {str(e)[:500]}")
        return _empty_schemes()
    timing['verify'] = time.time() - t0

    # ---- 11. 替换时间戳ID为数据库序列ID ----
    if not main_scheme.empty:
        old_main_ids = main_scheme['DIST_SCHEME_ID'].tolist()
        new_main_ids = [int(x) for x in query_pk_next("SEQ_ADAM_DIST_SCHEME", len(main_scheme))]
        id_map = {old: new for old, new in zip(old_main_ids, new_main_ids)}
        main_scheme['DIST_SCHEME_ID'] = new_main_ids
        if not detail_scheme.empty:
            new_det_ids = [int(x) for x in query_pk_next("SEQ_ADAM_DIST_SCHEME_DET", len(detail_scheme))]
            detail_scheme['DIST_SCHEME_ID'] = detail_scheme['DIST_SCHEME_ID'].map(id_map)
            detail_scheme['DIST_SCHEME_DET_ID'] = new_det_ids
        logging.info(f"[V4] 序列ID替换: 主表{len(new_main_ids)}条, 明细表{len(detail_scheme)}条")

    # ---- 12. 总耗时汇总 ----
    total_elapsed = time.time() - t_total
    timing_str = " | ".join(f"{k}={v:.1f}s" for k, v in timing.items())
    logging.info(
        f"[V4] ======== 完成 ======== "
        f"{len(best_sol)}条路线, 总耗时{total_elapsed:.1f}s "
        f"({timing_str})"
    )
    logging.info("=" * 60)
    return main_scheme, detail_scheme


def _print_delivery_summary(best_sol, unit_sum, demand_units, type_to_cap, total_demand_boxes):
    """
    打印配送方案摘要:
        1. 原始需求（每个站点多少箱）
        2. 每条路线覆盖了哪些站点、各多少箱
        3. 满足统计
    """
    logging.info("-" * 40)
    logging.info(f"[V4] ==================== 配送方案 ====================")

    # ── 原始需求 ──
    logging.info(f"[V4] 【原始需求】{len(unit_sum)}个站点, 总计{total_demand_boxes:.0f}箱:")
    sorted_demand = sorted(unit_sum.items(), key=lambda x: x[1], reverse=True)
    for nid, boxes in sorted_demand:
        logging.info(f"  站点{nid}: {boxes:.0f}箱")

    # ── 配送路线 ──
    logging.info(f"[V4] ")
    logging.info(f"[V4] 【配送路线】共{len(best_sol)}条:")
    total_deliv = 0.0
    max_cap = max(type_to_cap.values()) if type_to_cap else 1
    rates = []

    for i, r in enumerate(best_sol):
        vt = r['vehicle_type']
        cap = type_to_cap.get(vt, max_cap)
        load = sum(a for _, a in r['deliveries'])
        total_deliv += load
        rate = load / cap * 100 if cap > 0 else 0
        rates.append(rate)

        stops_detail = []
        for nid, boxes in r['deliveries']:
            site_demand = unit_sum.get(nid, 0)
            pct = boxes / site_demand * 100 if site_demand > 0 else 0
            if abs(boxes - site_demand) < 0.01:
                stops_detail.append(f"站点{nid} {boxes:.0f}箱(全部)")
            else:
                stops_detail.append(f"站点{nid} {boxes:.0f}箱({pct:.0f}%)")

        stops_str = " → ".join(stops_detail)
        logging.info(
            f"  路线{i+1}: 车型{vt}(容{cap:.0f}箱) [{stops_str}] "
            f"合计{load:.0f}箱 满载率{rate:.1f}%"
        )

    # ── 满足统计 ──
    logging.info(f"[V4] ")
    logging.info(f"[V4] 【满足统计】")
    if rates:
        logging.info(
            f"  满载率: avg={np.mean(rates):.1f}% min={np.min(rates):.1f}% "
            f"max={np.max(rates):.1f}% 中位数={np.median(rates):.1f}%"
        )

    dropped = total_demand_boxes - total_deliv
    if dropped > 0.01:
        logging.error(
            f"  ❌ 丢需求: {dropped:.0f}箱 "
            f"(需求{total_demand_boxes:.0f}箱 vs 配送{total_deliv:.0f}箱)"
        )
    else:
        logging.info(
            f"  ✓ 需求全覆盖: 配送{total_deliv:.0f}箱 = 需求{total_demand_boxes:.0f}箱"
        )

    # 逐站点核验
    delivered_by_site = {}
    for r in best_sol:
        for nid, boxes in r['deliveries']:
            delivered_by_site[nid] = delivered_by_site.get(nid, 0) + boxes

    site_mismatches = []
    for nid, demand in unit_sum.items():
        got = delivered_by_site.get(nid, 0)
        if abs(got - demand) > 0.01:
            site_mismatches.append(f"站点{nid}: 需{demand:.0f}→配{got:.0f}差{demand-got:.0f}")
    if site_mismatches:
        logging.warning(f"  ⚠ 站点级差异 ({len(site_mismatches)}处):")
        for m in site_mismatches:
            logging.warning(f"    {m}")
    else:
        logging.info(f"  站点级核验: {len(unit_sum)}个站点全部满足 ✓")

    logging.info(f"[V4] =================================================")


def _empty_schemes():
    """返回空的 MainScheme 和 DetailScheme DataFrame。"""
    main_cols = [
        'DIST_SCHEME_ID', 'CAR_TYPE', 'PLAN_DIST_DATE', 'DIST_FLAG',
        'LATE_FLAG', 'LOAD_RATE', 'CREATE_DATE', 'UPDATE_DATE', 'GLOBAL_SCHEME_ID'
    ]
    detail_cols = [
        'DIST_SCHEME_DET_ID', 'DIST_SCHEME_ID', 'REC_ORG_NO', 'DEV_CODE',
        'DEV_CLS', 'DEV_CATEG', 'DIST_SEQ', 'LOAD_SEQ', 'PLAN_DIST_NUM',
        'PLAN_BOX_NUM', 'EST_TOT_DIST_MIST', 'DIST_EXP', 'GLOBAL_SCHEME_ID'
    ]
    return pd.DataFrame(columns=main_cols), pd.DataFrame(columns=detail_cols)


def _query_daily_replenish_total(date_str):
    """
    查询日补库原始总量，诊断数据丢失原因。

    丢失可能的三个原因（按优先级）:
        1. 重复 (ORG_NO, DEV_CODE): Demands矩阵只取 .values[0]（首次出现）
        2. ORG_NO 不在配送站点配置表中: 双层循环只有 Location[i] 能命中
        3. DEV_CODE 不在设备规格配置表中: 双层循环只有 SubType[j] 能命中

    Returns:
        (raw_total, matrix_expected): raw_total=日补库原始总件数, matrix_expected=应进入Demands矩阵的件数
    """
    try:
        from backend.api.data_api.fetch_data import (
            query_adam_plan_day_ias_pre_by_date,
            query_adam_del_site_conf,
            query_adam_spec_code_config,
        )
        tb = query_adam_plan_day_ias_pre_by_date(date_str)
        if tb is None or tb.empty:
            return 0, 0

        total = int(tb['PLAN_IAS_NUM'].sum())

        # ---- 获取有效的 ORG_NO 和 DEV_CODE 集合 ----
        try:
            site_conf = query_adam_del_site_conf()
            site_conf = site_conf[site_conf['STAT_NAME'] != '营销服务中心']
            valid_orgs = set(str(o).strip() for o in site_conf['ORG_NO'])
        except Exception:
            valid_orgs = set()

        try:
            spec_conf = query_adam_spec_code_config()
            valid_devs = set(str(d).strip() for d in spec_conf['DEV_CODE'])
        except Exception:
            valid_devs = set()

        # ---- 逐条诊断 ----
        lost_org = 0       # ORG_NO 不匹配
        lost_dev = 0       # DEV_CODE 不匹配
        matched = 0        # 正常进入 Demands 矩阵
        dup_groups = []    # 重复的 (org, dev) 对

        # 按 (ORG_NO, DEV_CODE) 分组
        grouped = tb.groupby(['REC_ORG_NO', 'DEV_CODE'])['PLAN_IAS_NUM'].agg(['sum', 'count'])
        org_mismatch_samples = []
        dev_mismatch_samples = []

        for (org, dev), row in grouped.iterrows():
            org_s = str(org).strip()
            dev_s = str(dev).strip()
            pieces = int(row['sum'])
            count = int(row['count'])

            org_ok = org_s in valid_orgs if valid_orgs else True
            dev_ok = dev_s in valid_devs if valid_devs else True

            if not org_ok:
                lost_org += pieces
                if len(org_mismatch_samples) < 5:
                    org_mismatch_samples.append((org_s, dev_s, pieces))
            elif not dev_ok:
                lost_dev += pieces
                if len(dev_mismatch_samples) < 5:
                    dev_mismatch_samples.append((org_s, dev_s, pieces))
            else:
                matched += pieces
                if count > 1:
                    dup_groups.append((org_s, dev_s, pieces, count))

        # ---- 汇总日志 ----
        gap = total - matched
        if gap > 0:
            parts = []
            if lost_org > 0:
                parts.append(f"ORG_NO不在站点配置表={lost_org}件")
                for org, dev, pcs in org_mismatch_samples:
                    logging.warning(f"  ORG_NO不匹配: {org}/{dev} = {pcs}件")
            if lost_dev > 0:
                parts.append(f"DEV_CODE不在规格配置表={lost_dev}件")
                for org, dev, pcs in dev_mismatch_samples:
                    logging.warning(f"  DEV_CODE不匹配: {org}/{dev} = {pcs}件")
            logging.info(
                f"[V4] 日补库{total}件 → Demands矩阵(预期){matched}件 "
                f"(差额{gap}件={' + '.join(parts)})"
            )
        else:
            logging.info(f"[V4] 日补库{total}件 → Demands矩阵{matched}件 (无丢失)")

        # 重复记录警告（单独打印，不在差额中体现——矩阵至少取一条）
        if dup_groups:
            logging.warning(
                f"[V4] ⚠ 日补库表存在{len(dup_groups)}组重复(站点,设备)记录:"
            )
            for org, dev, pieces, count in dup_groups[:10]:
                logging.warning(f"  {org}/{dev}: {count}条共{pieces}件 → 矩阵只取首条, 其余被丢弃")

        return total, matched
    except Exception:
        pass
    return 0, 0
