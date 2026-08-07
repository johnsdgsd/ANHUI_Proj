"""
调拨场景一 编排层 — 月初高库龄调拨主流程

流程（《调拨场景一_月初高库龄调拨_方案设计.md》§5）:
    1. 解析入参: year_month(YYYYMM) → year/month, 当月天数, ALLOT_DATE(默认当天)
    2. 加载调拨网络: 87家单位 + 距离矩阵 (data_prep.prepare_transfer_network)
    3. 月度补库计划 GAP>0 → 需求点字典 {dev_code: {org_no: GAP}}
    4. 库存快照 HIGH_NUM>0 + 未来14天需求约束 → 供应点字典 {dev_code: {org_no: s_i}}
       s_i = max(0, min(HIGH_NUM, STOCK_NUM − 未来14天需求))
       未来14天需求 = μ + 1.645√μ, μ = 月度需求/当月天数 × 14 (0.95 正态分位, Poisson 近似)
    5. 按设备码循环 solve_transfer (ILP 最小化总运输距离), 收集调拨记录
    6. 汇总输出行 (SEND_STOCK_NUM / REC_STOCK_NUM 按 单位×设备码 先汇总)
    7. 主键分配 + 先删后插落库 ADAM_ALLOT_DAY_PLAN_PRE

口径（用户确认, 2026-08-06）:
    - ALLOT_DATE = 调拨生成日(当天)
    - 未来14天需求按月度折算, 窗口起算不影响
    - 距离缺失在数据准备阶段直接报错退出
    - 空方案不落库不删旧数据, 直接返回
    - 情形2 未满足缺口本期不处理(仅统计), 属场景二
    - 单设备码 ILP 求解失败跳过该设备码, 其余继续
    - 库存多快照保持 SQL 汇总现状
"""
import calendar
import logging
from collections import defaultdict
from datetime import datetime

import numpy as np

from backend.algorithm.transfer.data_prep import prepare_transfer_network
from backend.algorithm.transfer.ilp_solver import solve_transfer
from backend.api.data_api.fetch_data import (
    query_adam_plan_month_ias_pre,
    query_adam_spec_code_config,
    query_adam_stock_count_sample_all,
    query_adam_yqm_dmd_pre_by_year_month,
    query_pk_next,
    delete_adam_allot_day_plan_pre_by_date,
    insert_into_adam_allot_day_plan_pre,
)
from backend.config.scheme_config import get_approved_scheme_config

logger = logging.getLogger(__name__)

# 未来14天需求 0.95 百分位点 (正态近似 Poisson 的 z 值, 与缺货检测 λ+z√λ 口径一致)
DEMAND_Z = 1.645
# 输出表 SEND_REASON 固定值
SEND_REASON = '高库龄'
# 设备规格缺失映射时的默认值
DEFAULT_DEV_CLS = '00'
DEFAULT_DEV_CATEG = '00'


def _compute_14day_demand(yqm_df, month_days, window_days=14, z=DEMAND_Z):
    """由月度需求预测网格计算未来 window_days 天的需求 (0.95 百分位点)。

    Args:
        yqm_df: DS_SQL `gk-adam-query_adam_yqm_dmd_pre_by_year_month` 返回的全网格
                (ORG_NO, DEV_CODE, PRE_NUM)，PRE_NUM 已按 BUS_TYPE 01/02/03 求和，
                缺失组合补 0。
        month_days: 当月实际天数 (31/30/28)
        window_days: 未来窗口天数, 默认 14
        z: 正态分位点, 默认 1.645 (0.95)

    Returns:
        dict: {(org_no, dev_code): need14}
            need14 = μ + z√μ, μ = PRE_NUM/month_days × window_days
    """
    if yqm_df is None or yqm_df.empty:
        logger.warning("未来14天需求: 月度需求预测为空, 按 0 处理 (不阻断)")
        return {}

    need = {}
    for _, r in yqm_df.iterrows():
        org = str(r['ORG_NO']).strip()
        dev = r['DEV_CODE']
        pre = r['PRE_NUM']
        if pre is None:
            continue
        try:
            pre = float(pre)
        except (TypeError, ValueError):
            continue
        if not (pre > 0):            # NaN/≤0 一并跳过
            continue
        daily = pre / month_days
        mu = daily * window_days
        need[(org, dev)] = mu + z * np.sqrt(mu)
    logger.info(
        f"未来14天需求: 计算 {len(need)} 个 (单位,设备码) 组合, "
        f"窗口 {window_days} 天, 分位 z={z}")
    return need


def _build_spec_maps(spec_df):
    """构建设备码 → DEV_CLS / DEV_CATEG 映射字典。

    Returns:
        (dev_cls_dict, dev_categ_dict): {DEV_CODE: 分类/类别}
    """
    dev_cls, dev_categ = {}, {}
    for _, r in spec_df.iterrows():
        dev_cls[r['DEV_CODE']] = str(r['DEV_CLS'])
        dev_categ[r['DEV_CODE']] = str(r['DEV_CATEG'])
    return dev_cls, dev_categ


def _build_demand_map(plan_df, org2i):
    """由月度补库计划构建需求点字典 {dev_code: {org_no: GAP}}。

    过滤: GAP>0 且单位在调拨网络内(87家); 不在网络内的单位告警跳过。
    GAP 为 None/NaN（未填充）一律按无需求处理；仅当列本身缺失才报错。
    """
    if 'GAP' not in plan_df.columns:
        raise ValueError("ADAM_PLAN_MONTH_IAS_PRE 缺少 GAP 列, 请核实表结构")
    dem = defaultdict(dict)
    skipped_orgs = set()
    n_filled = 0
    for _, r in plan_df.iterrows():
        gap = r.get('GAP')
        if gap is None:
            continue                     # GAP 未填充 → 无需求
        try:
            g = float(gap)
        except (TypeError, ValueError):
            continue                     # 非数值 → 无需求
        if not (g > 0):                  # NaN > 0 为 False, 一并跳过
            continue
        n_filled += 1
        org = str(r['REC_ORG_NO']).strip()
        if org not in org2i:
            skipped_orgs.add(org)
            continue
        dem[r['DEV_CODE']][org] = g
    if n_filled == 0:
        logger.warning(
            f"需求点: 月度补库计划 {len(plan_df)} 行 GAP 全部为空/≤0, "
            f"可能上游月度平衡尚未填充, 结果为空方案")
    if skipped_orgs:
        logger.warning(
            f"需求点: {len(skipped_orgs)} 个单位不在调拨网络内, 已跳过: "
            f"{sorted(skipped_orgs)}")
    return dem


def _build_supply_map(stock_df, org2i, d14):
    """由库存快照 + 未来14天需求构建供应点字典 {dev_code: {org_no: s_i}}。

    s_i = max(0, min(HIGH_NUM, STOCK_NUM − 未来14天需求))
    调出仅限高库龄部分(HIGH_NUM 为上限), 且调出后需满足两周用表(STOCK_NUM−需求 为上限)。
    """
    sup = defaultdict(dict)
    for _, r in stock_df.iterrows():
        high = r['HIGH_NUM']
        if high is None:
            continue
        try:
            high = float(high)
        except (TypeError, ValueError):
            continue
        if not (high > 0):           # NaN/≤0 一并跳过
            continue
        org = str(r['MGT_ORG_CODE']).strip()
        if org not in org2i:
            continue
        dev = r['DEV_CODE_NO']
        try:
            stock_num = float(r['STOCK_NUM'] or 0.0)
        except (TypeError, ValueError):
            stock_num = 0.0
        need14 = d14.get((org, dev), 0.0)
        s_i = max(0.0, min(high, stock_num - need14))
        if s_i > 0:
            sup[dev][org] = s_i
        elif stock_num - need14 <= 0:
            logger.debug(
                f"供应点 {org}/{dev}: 库存 {stock_num:.0f} ≤ 未来14天需求 {need14:.0f}, "
                f"可调出量=0 (不影响两周用表)")
    return sup


def _build_output_df(rows, stock_lookup, dev_cls, dev_categ,
                     global_scheme_id, allot_date):
    """汇总调拨记录为输出表 DataFrame (ADAM_ALLOT_DAY_PLAN_PRE)。

    SEND_STOCK_NUM = 调出单位该设备码快照 STOCK_NUM − Σ调出量(该单位该设备码)
    REC_STOCK_NUM  = 调入单位该设备码快照 STOCK_NUM + Σ调入量(该单位该设备码), 快照无记录按 0
    """
    if not rows:
        return None

    # 先按 单位×设备码 汇总出/调入总量（一个单位可出现在多条调拨记录）
    send_total = defaultdict(float)
    recv_total = defaultdict(float)
    for r in rows:
        send_total[(r['SEND_ORG_NO'], r['DEV_CODE'])] += r['SEND_NUM']
        recv_total[(r['REC_ORG_NO'], r['DEV_CODE'])] += r['SEND_NUM']

    out = []
    for r in rows:
        dev = r['DEV_CODE']
        send_stock = (stock_lookup.get((r['SEND_ORG_NO'], dev), 0.0)
                      - send_total[(r['SEND_ORG_NO'], dev)])
        rec_stock = (stock_lookup.get((r['REC_ORG_NO'], dev), 0.0)
                     + recv_total[(r['REC_ORG_NO'], dev)])
        out.append({
            'ALLOT_DATE': allot_date,
            'SEND_ORG_NO': r['SEND_ORG_NO'],
            'REC_ORG_NO': r['REC_ORG_NO'],
            'DEV_CLS': dev_cls.get(dev, DEFAULT_DEV_CLS),
            'DEV_CATEG': dev_categ.get(dev, DEFAULT_DEV_CATEG),
            'DEV_CODE': dev,
            'SEND_NUM': int(r['SEND_NUM']),
            'SEND_STOCK_NUM': int(send_stock),
            'REC_STOCK_NUM': int(rec_stock),
            'GLOBAL_SCHEME_ID': int(global_scheme_id),
            'SEND_REASON': SEND_REASON,
        })

    df = _rows_to_df(out)
    neg = df[df['SEND_STOCK_NUM'] < 0]
    if not neg.empty:
        logger.warning(
            f"输出检查: {len(neg)} 条 SEND_STOCK_NUM 为负的记录:\n"
            f"{neg[['SEND_ORG_NO', 'REC_ORG_NO', 'DEV_CODE', 'SEND_NUM', 'SEND_STOCK_NUM']].to_string()}")
    return df


def _rows_to_df(out):
    """按 ADAM_ALLOT_DAY_PLAN_PRE 目标列顺序构造 DataFrame (主键列留空待分配)。"""
    import pandas as pd
    return pd.DataFrame(out, columns=[
        'ALLOT_DAY_PLAN_PRE_ID', 'ALLOT_DATE', 'SEND_ORG_NO', 'REC_ORG_NO',
        'DEV_CLS', 'DEV_CATEG', 'DEV_CODE', 'SEND_NUM', 'SEND_STOCK_NUM',
        'REC_STOCK_NUM', 'GLOBAL_SCHEME_ID', 'SEND_REASON',
    ])


def run_transfer_scenario1(year_month, allot_date=None, window_days=14):
    """调拨场景一主流程：月初高库龄调拨。

    Args:
        year_month: 业务年月 'YYYYMM'（补库计划/需求预测所属年月）
        allot_date: 调拨执行日(ALLOT_DATE), 默认当天 'YYYY-MM-DD'
        window_days: 两周需求窗口天数, 默认 14

    Returns:
        dict 运行摘要:
            code/message, year_month, allot_date,
            n_devices, n_rows, total_qty, total_dist,
            unmet_demand, leftover_supply, insert_result
    """
    t_start = datetime.now()

    if len(str(year_month)) != 6 or not str(year_month).isdigit():
        raise ValueError(f"year_month 格式应为 YYYYMM, 收到: {year_month}")
    year, month = str(year_month)[:4], str(year_month)[4:]
    month_days = calendar.monthrange(int(year), int(month))[1]
    if allot_date is None:
        allot_date = datetime.now().strftime('%Y-%m-%d')
    logger.info(
        f"调拨场景一启动: 年月 {year_month} ({year}-{month}, {month_days}天), "
        f"ALLOT_DATE={allot_date}, 窗口 {window_days} 天")

    # ---- 1. 加载调拨网络（单位 + 距离矩阵） ----
    net = prepare_transfer_network()
    org2i = {o: i for i, o in enumerate(net['org_ids'])}
    logger.info(
        f"调拨网络: {len(net['org_ids'])} 家单位, "
        f"可达率 {net['stats']['reachable_ratio']:.1%}")

    # ---- 2. 月度补库计划 → 需求点 (GAP>0) ----
    plan_df = query_adam_plan_month_ias_pre(year, month)
    if plan_df is None or plan_df.empty:
        raise ValueError(f"月度补库计划 {year_month} 查询为空")
    dem = _build_demand_map(plan_df, org2i)
    n_demand_orgs = len({o for d in dem.values() for o in d})
    logger.info(
        f"需求点: {len(dem)} 个设备码, {n_demand_orgs} 个单位 (GAP>0)")

    # ---- 3. 库存快照 + 未来14天需求 → 供应点 ----
    stock_df = query_adam_stock_count_sample_all()
    stock_lookup = {(str(r['MGT_ORG_CODE']).strip(), r['DEV_CODE_NO']):
                    float(r['STOCK_NUM'] or 0.0) for _, r in stock_df.iterrows()}
    yqm_df = query_adam_yqm_dmd_pre_by_year_month(year, month)
    d14 = _compute_14day_demand(yqm_df, month_days, window_days)
    sup = _build_supply_map(stock_df, org2i, d14)
    n_supply_orgs = len({o for s in sup.values() for o in s})
    total_s = sum(s for d in sup.values() for s in d.values())
    logger.info(
        f"供应点: {len(sup)} 个设备码, {n_supply_orgs} 个单位, "
        f"可调出总量 Σs={total_s:.0f}")

    # ---- 4. 设备规格映射 + 全局方案 ID ----
    spec_df = query_adam_spec_code_config()
    dev_cls, dev_categ = _build_spec_maps(spec_df)
    global_scheme_id, _ = get_approved_scheme_config(year_month)

    # ---- 5. 按设备码循环求解 ----
    rows = []
    n_skipped = 0
    n_devices = 0
    total_dist = 0.0
    unmet_demand = 0.0
    leftover_supply = 0.0

    devices = sorted(set(dem) | set(sup), key=str)
    for dev in devices:
        d_j = dem.get(dev, {})
        s_i = sup.get(dev, {})
        if not s_i or not d_j:
            continue                       # 无调出(含全0)或无需求, 该设备码跳过
        n_devices += 1

        sup_ids = list(s_i.keys())
        dem_ids = list(d_j.keys())
        s_vals = list(s_i.values())
        d_vals = list(d_j.values())

        # 距离子矩阵: cost[i,j] = 单位编码 si→dj 距离
        C = np.array([[net['cost'][org2i[src], org2i[tgt]]
                       for tgt in dem_ids] for src in sup_ids])

        res = solve_transfer(s_vals, d_vals, C,
                             supply_ids=sup_ids, demand_ids=dem_ids)
        if res['status'] not in ('Optimal', 'Feasible'):
            n_skipped += 1
            logger.warning(
                f"设备码 {dev}: ILP 求解失败 status={res['status']}, 跳过该设备码")
            continue

        for src, tgt, qty, dist in res['x']:
            rows.append({'SEND_ORG_NO': src, 'REC_ORG_NO': tgt,
                         'DEV_CODE': dev, 'SEND_NUM': int(qty), 'DIST': float(dist)})
        total_dist += res['total_dist']
        unmet_demand += res['unmet_demand'] or 0.0
        leftover_supply += res['leftover_supply'] or 0.0
        logger.info(
            f"设备码 {dev}: {len(res['x'])} 条调拨, 情形{res['mode']}, "
            f"Σs={res['total_supply']:.0f} Σd={res['total_demand']:.0f}, "
            f"耗时{res['solve_time']:.2f}s")

    total_qty = sum(r['SEND_NUM'] for r in rows)
    logger.info(
        f"求解完成: {n_devices} 个设备码参与, 跳过 {n_skipped}, "
        f"调拨 {len(rows)} 条, 总量 {total_qty}, 总距离 {total_dist:.0f}")

    # ---- 6. 汇总输出 + 主键 + 先删后插 ----
    df = _build_output_df(rows, stock_lookup, dev_cls, dev_categ,
                          global_scheme_id, allot_date)
    if df is None:
        logger.info("调拨场景一: 无可调出量或缺口, 空方案不落库不删旧数据")
        return {
            'code': 0, 'message': '空方案: 无需求点或无有效可调出量, 未落库',
            'year_month': year_month, 'allot_date': allot_date,
            'n_devices': n_devices, 'n_rows': 0, 'total_qty': 0,
            'total_dist': 0.0, 'unmet_demand': 0.0, 'leftover_supply': 0.0,
            'insert_result': None,
            'elapsed_sec': round((datetime.now() - t_start).total_seconds(), 2),
        }

    pks = query_pk_next('SEQ_ADAM_ALLOT_DAY_PLAN_PRE', len(df))
    df['ALLOT_DAY_PLAN_PRE_ID'] = [int(x) for x in pks]

    logger.info(f"删除调拨旧数据, ALLOT_DATE={allot_date}")
    del_res = delete_adam_allot_day_plan_pre_by_date(allot_date)
    logger.info(f"删除结果: {del_res}")
    insert_res = insert_into_adam_allot_day_plan_pre(df)
    logger.info(f"落库: {insert_res}")

    return {
        'code': 0,
        'message': f"调拨场景一完成: {len(df)} 条记录, 总调拨 {total_qty}, "
                   f"总距离 {total_dist:.0f}",
        'year_month': year_month, 'allot_date': allot_date,
        'n_devices': n_devices, 'n_rows': len(df), 'total_qty': int(total_qty),
        'total_dist': round(total_dist, 2),
        'unmet_demand': round(unmet_demand, 2),
        'leftover_supply': round(leftover_supply, 2),
        'insert_result': insert_res,
        'elapsed_sec': round((datetime.now() - t_start).total_seconds(), 2),
    }


if __name__ == '__main__':
    # 独立运行入口: python -m backend.algorithm.transfer.orchestrator
    import io
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s', stream=sys.stdout)
    ym = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime('%Y%m')
    result = run_transfer_scenario1(ym)
    print(f"\n=== 运行摘要 ===")
    for k, v in result.items():
        print(f"  {k}: {v}")
