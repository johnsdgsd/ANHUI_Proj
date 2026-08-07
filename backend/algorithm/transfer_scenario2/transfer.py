"""
调拨场景二 — 【独立】调拨核心

输入: 省中心无货的缺货组合 [(org, dev, qty)] + 供应点可调出量 + 调拨网络 + 高库龄优先级
输出: 贪心分配调拨记录 + 写 ADAM_ALLOT_DAY_PLAN_PRE（SEND_REASON='缺货调拨'）

算法（指导④确认，贪心）:
    对每个缺货组合，候选供应点按 (高库龄优先, 距离升序) 排序，依次取
    min(可调出量, 剩余缺货量)；供应点可调出量跨组合全局递减。
    候选耗尽仍未满足 → 记录 unmet_demand（不阻塞其他组合）。

本模块与紧急补库核心(emergency)完全解耦：只负责「省中心无货时的调拨」，
不做省中心判定（由 orchestrator 分流），不做缺货判定/调出判定（由独立模块提供）。
"""
import logging

from backend.algorithm.transfer_scenario2.config import (
    SEQ_ALLOT_DAY_PLAN_PRE,
    SEND_REASON,
)
from backend.algorithm.transfer_scenario2.common import build_output_df
from backend.api.data_api.fetch_data import (
    query_pk_next,
    delete_adam_allot_day_plan_pre_by_date,
    insert_into_adam_allot_day_plan_pre,
)

logger = logging.getLogger(__name__)


def allocate_transfer(shortages, supply_map, net, high_stock_set):
    """贪心分配：对每个缺货组合生成调拨记录。

    Args:
        shortages: [(org, dev, qty), ...]（省中心无货的缺货组合）
        supply_map: {(org, dev): surplus}，由 supplier_select.build_supply_map 提供
        net: 调拨网络 {org_ids, org_names, cost}（common.load_transfer_network）
        high_stock_set: 高库龄单位集合（supplier_select.high_stock_orgs），优先级键

    Returns:
        (rows, unmet_demand):
            rows: [{'SEND_ORG_NO','REC_ORG_NO','DEV_CODE','SEND_NUM'}, ...]
            unmet_demand: float 候选耗尽未满足的缺货量合计
    """
    if not shortages:
        return [], 0.0

    org2i = {o: i for i, o in enumerate(net['org_ids'])}
    cost = net['cost']
    remaining = dict(supply_map)          # 可调出量全局递减

    rows = []
    unmet_demand = 0.0
    for org, dev, qty in shortages:
        if qty <= 0:
            continue
        logger.info(f"调拨需求: {org} 需调入 设备码 {dev} {qty:.0f} 台")
        # 候选供应点: 同设备码、有剩余可调出量、≠需求单位
        cands = [o for (o, d), s in remaining.items()
                 if d == dev and o != org and s > 0]
        # 优先级: ① 高库龄优先 ② 距离升序
        cands.sort(key=lambda o: (0 if o in high_stock_set else 1,
                                  cost[org2i[o], org2i[org]]))

        remain = qty
        for sup in cands:
            if remain <= 0:
                break
            key = (sup, dev)
            take = min(remaining[key], remain)
            if take > 0:
                rows.append({
                    'SEND_ORG_NO': sup,
                    'REC_ORG_NO': org,
                    'DEV_CODE': dev,
                    'SEND_NUM': int(take),
                })
                remaining[key] -= take
                remain -= take
                logger.info(
                    f"调拨: {sup} → {org} 设备码 {dev} 调出 {int(take)} 台")
        if remain > 0:
            unmet_demand += remain
            logger.warning(
                f"调拨 {org}/{dev}: 候选耗尽, 缺货 {remain:.0f} 未满足 (unmet_demand)")

    total_qty = sum(r['SEND_NUM'] for r in rows)
    logger.info(
        f"调拨: {len(rows)} 条记录, 总量 {total_qty}, "
        f"未满足缺口 {unmet_demand:.0f}")
    return rows, unmet_demand


def insert_transfer_records(rows, stock_lookup, dev_cls, dev_categ,
                            global_scheme_id, allot_date, send_reason=SEND_REASON):
    """调拨记录落库：输出构造 + 主键 + 先删后插（写 ADAM_ALLOT_DAY_PLAN_PRE）。

    Returns:
        dict 插入结果; rows 为空返回 None（不落库不删旧数据）
    """
    df = build_output_df(rows, stock_lookup, dev_cls, dev_categ,
                         global_scheme_id, allot_date, send_reason)
    if df is None:
        logger.info("调拨: 无调拨记录, 空方案不落库不删旧数据")
        return None

    pks = query_pk_next(SEQ_ALLOT_DAY_PLAN_PRE, len(df))
    df['ALLOT_DAY_PLAN_PRE_ID'] = [int(x) for x in pks]

    logger.info(f"删除调拨旧数据, ALLOT_DATE={allot_date}")
    del_res = delete_adam_allot_day_plan_pre_by_date(allot_date)
    logger.info(f"删除结果: {del_res}")
    res = insert_into_adam_allot_day_plan_pre(df)
    logger.info(f"调拨落库: {res}")
    return res
