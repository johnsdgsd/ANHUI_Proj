"""
调拨场景二 — 【仅分流】编排层

不做业务计算，只负责:
    1. 取数（库存/日需求/日计划/月度需求/规格/实时合格品/调拨网络/全局方案）
    2. 调独立模块: stockout_detect(缺货判定) / supplier_select(调出判定)
    3. 省中心分流（orchestrator 唯一业务逻辑）:
        省中心实时合格品 > 0 → 紧急补库(emergency)；否则 → 调拨(transfer)
    4. 汇总返回

更换任一核心模块（缺货判定算法/库存上限口径/贪心改ILP/补库落库）不影响本文件。
"""
import calendar
import logging
from datetime import datetime

from backend.algorithm.transfer_scenario2.config import UPPER_WINDOW_DAYS
from backend.algorithm.transfer_scenario2.common import (
    load_transfer_network,
    build_spec_maps,
    _compute_upper_limit,
    _query_province_center_stock,
    build_stock_lookup,
)
from backend.algorithm.transfer_scenario2.stockout_detect import detect_shortage
from backend.algorithm.transfer_scenario2.supplier_select import (
    build_supply_map,
    high_stock_orgs,
)
from backend.algorithm.transfer_scenario2.emergency import (
    build_emergency_records,
    insert_emergency_records,
)
from backend.algorithm.transfer_scenario2.transfer import (
    allocate_transfer,
    insert_transfer_records,
)
from backend.api.data_api.fetch_data import (
    query_adam_stock_count_sample_all,
    query_adam_plan_day_ias_pre_by_month,
    query_adam_wd_dmd_pre_by_year_month_and_pretype,
    query_adam_yqm_dmd_pre_by_year_month,
    query_adam_spec_code_config,
)
from backend.config.scheme_config import get_approved_scheme_config

logger = logging.getLogger(__name__)


def _check_stock_snapshot_date(stock_df, snapshot_date):
    """校验实时库存快照日期（UPDATE_TIME 最大值）与运行日是否相符。

    Returns:
        (stock_snapshot_date, stock_lag_days): 快照日期字符串 / 滞后天数；
        无法校验时返回 (None, None)。
    """
    if stock_df is None or stock_df.empty or 'UPDATE_TIME' not in stock_df.columns:
        logger.warning("库存快照日期校验: 库存数据无 UPDATE_TIME 列, 无法校验")
        return None, None
    try:
        snap = stock_df['UPDATE_TIME'].max()
        if isinstance(snap, str):
            snap_dt = datetime.strptime(snap[:10], '%Y-%m-%d').date()
        else:
            snap_dt = snap.date() if hasattr(snap, 'date') else snap
        run_dt = datetime.strptime(snapshot_date, '%Y-%m-%d').date()
        lag = (run_dt - snap_dt).days
        logger.info(f"库存快照日期校验: 快照={snap_dt} 运行日={snapshot_date} 滞后={lag}天")
        if lag > 1:
            logger.warning(f"⚠ 库存快照滞后 {lag} 天: 补货决策基于 {snap_dt} 的库存, "
                           f"而非运行日 {snapshot_date} 的实时库存, 结果可能失真")
        return str(snap_dt), lag
    except Exception as e:
        logger.warning(f"库存快照日期校验失败: {e}")
        return None, None


def run_transfer_scenario2(year_month, snapshot_date=None,
                           window_upper_days=UPPER_WINDOW_DAYS):
    """调拨场景二主流程（仅分流，业务逻辑在各独立模块）。

    Args:
        year_month: 业务年月 'YYYYMM'（缺货检测/需求预测所属年月）
        snapshot_date: 库存快照/调拨执行日 'YYYY-MM-DD'，默认当天
        window_upper_days: 库存上限窗口天数，默认 14

    Returns:
        dict 运行摘要
    """
    t_start = datetime.now()

    if len(str(year_month)) != 6 or not str(year_month).isdigit():
        raise ValueError(f"year_month 格式应为 YYYYMM, 收到: {year_month}")
    year, month = str(year_month)[:4], str(year_month)[4:]
    month_days = calendar.monthrange(int(year), int(month))[1]
    if snapshot_date is None:
        snapshot_date = datetime.now().strftime('%Y-%m-%d')
    logger.info(
        f"调拨场景二启动: 年月 {year_month} ({month_days}天), "
        f"snapshot_date={snapshot_date}, 库存上限窗口 {window_upper_days} 天")

    # ---- 1. 取数 ----
    net = load_transfer_network()
    org2i = {o: i for i, o in enumerate(net['org_ids'])}
    logger.info(f"调拨网络: {len(net['org_ids'])} 家单位, "
                f"可达率 {net['stats']['reachable_ratio']:.1%}")

    stock_df = query_adam_stock_count_sample_all()
    stock_snapshot_date, stock_lag_days = _check_stock_snapshot_date(stock_df, snapshot_date)
    dplan_df = query_adam_plan_day_ias_pre_by_month(year_month)
    ddmd_df = query_adam_wd_dmd_pre_by_year_month_and_pretype(year, month, '05')
    yqm_df = query_adam_yqm_dmd_pre_by_year_month(year, month)
    spec_df = query_adam_spec_code_config()
    global_scheme_id, _ = get_approved_scheme_config(year_month)

    # ---- 2. 缺货判定（独立模块）----
    shortages = detect_shortage(stock_df, dplan_df, ddmd_df)
    if not shortages:
        logger.info("调拨场景二: 无缺货组合, 空方案不落库")
        return {
            'code': 0, 'message': '无缺货风险组合, 未落库',
            'year_month': year_month, 'snapshot_date': snapshot_date,
            'stock_snapshot_date': stock_snapshot_date, 'stock_lag_days': stock_lag_days,
            'n_shortages': 0, 'n_emergency': 0, 'n_transfer_rows': 0,
            'total_qty': 0, 'total_dist': 0.0, 'unmet_demand': 0.0,
            'insert_result': None,
            'elapsed_sec': round((datetime.now() - t_start).total_seconds(), 2),
        }

    # ---- 3. 调出判定（独立模块）----
    upper_map = _compute_upper_limit(yqm_df, month_days, window_upper_days)
    supply_map = build_supply_map(stock_df, upper_map, org2i)
    high_stock = high_stock_orgs(stock_df)

    # ---- 4. 省中心分流（orchestrator 唯一业务逻辑）----
    emergency_list = []      # [(org, dev, qty, date)] 省中心有货 → 紧急补库
    transfer_list = []       # [(org, dev, qty)] 省中心无货 → 调拨
    for org, dev, qty, date in shortages:
        center_stock = _query_province_center_stock(dev)
        if center_stock > 0:
            emergency_list.append((org, dev, qty, date))
            logger.info(
                f"省中心分流: {org} 设备码 {dev} 缺货 {qty:.0f} 台, "
                f"中心库当前库存 {center_stock:.0f} 台 → 紧急补库")
        else:
            transfer_list.append((org, dev, qty))
            logger.info(
                f"省中心分流: {org} 设备码 {dev} 缺货 {qty:.0f} 台, "
                f"中心库无货 → 调拨")
    logger.info(
        f"省中心分流: 缺货 {len(shortages)} 组合 → 紧急补库 {len(emergency_list)}, "
        f"调拨 {len(transfer_list)}")

    # ---- 5. 紧急补库（独立模块）----
    insert_result = None
    n_emergency = 0
    if emergency_list:
        records = build_emergency_records(emergency_list, spec_df, global_scheme_id)
        n_emergency = len(records)
        insert_result = insert_emergency_records(records)

    # ---- 6. 调拨（独立模块）----
    transfer_res = None
    total_qty = 0
    total_dist = 0.0
    unmet_demand = 0.0
    n_transfer_rows = 0
    if transfer_list:
        rows, unmet = allocate_transfer(transfer_list, supply_map, net, high_stock)
        unmet_demand = unmet
        if rows:
            dev_cls, dev_categ = build_spec_maps(spec_df)
            stock_lookup = build_stock_lookup(stock_df)
            transfer_res = insert_transfer_records(
                rows, stock_lookup, dev_cls, dev_categ,
                global_scheme_id, snapshot_date)
            n_transfer_rows = len(rows)
            total_qty = sum(r['SEND_NUM'] for r in rows)
            # 总距离: 用调拨网络成本矩阵回算
            total_dist = sum(
                net['cost'][org2i[r['SEND_ORG_NO']], org2i[r['REC_ORG_NO']]]
                * r['SEND_NUM'] for r in rows)

    logger.info(
        f"调拨场景二完成: 紧急补库 {n_emergency} 条, 调拨 {n_transfer_rows} 条, "
        f"总量 {total_qty}, 未满足缺口 {unmet_demand:.0f}")
    return {
        'code': 0,
        'message': f"调拨场景二完成: 缺货 {len(shortages)} 组合, "
                   f"紧急补库 {n_emergency} 条, 调拨 {n_transfer_rows} 条",
        'year_month': year_month, 'snapshot_date': snapshot_date,
        'stock_snapshot_date': stock_snapshot_date, 'stock_lag_days': stock_lag_days,
        'n_shortages': len(shortages),
        'n_emergency': n_emergency,
        'n_transfer_rows': n_transfer_rows,
        'total_qty': int(total_qty),
        'total_dist': round(total_dist, 2),
        'unmet_demand': round(unmet_demand, 2),
        'insert_result': insert_result,
        'transfer_result': transfer_res,
        'elapsed_sec': round((datetime.now() - t_start).total_seconds(), 2),
    }


if __name__ == '__main__':
    # 独立运行入口: python -m backend.algorithm.transfer_scenario2.orchestrator
    import io
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s', stream=sys.stdout)
    ym = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime('%Y%m')
    result = run_transfer_scenario2(ym)
    print(f"\n=== 运行摘要 ===")
    for k, v in result.items():
        print(f"  {k}: {v}")
