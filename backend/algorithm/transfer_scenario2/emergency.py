"""
调拨场景二 — 【独立】紧急补库核心

输入: 省中心有货的缺货组合 [(org, dev, qty, recommend_date)] + 规格 + 全局方案
输出: 生成补库记录并写 ADAM_PLAN_DAY_IAS_PRE（REPLE_TASK_TYPE='02' 紧急补库）

本模块与调拨核心(transfer)完全解耦：只负责「省中心有货时的紧急补库落库」，
不做省中心判定（由 orchestrator 分流），不做缺货判定（由 stockout_detect 提供）。
"""
import logging

import pandas as pd

from backend.algorithm.transfer_scenario2.config import (
    SEQ_PLAN_DAY_IAS_PRE,
    REPLE_TASK_TYPE,
    TASK_SOURCE,
    DAILY_PLAN_STATUS,
    DEFAULT_DEV_CLS,
    DEFAULT_DEV_CATEG,
)
from backend.api.data_api.fetch_data import (
    query_pk_next,
    insert_into_adam_plan_day_ias_pre,
)

logger = logging.getLogger(__name__)


def build_emergency_records(shortages, spec_df, global_scheme_id):
    """由省中心有货的缺货组合生成紧急补库记录（不落库）。

    Args:
        shortages: [(org, dev, qty, recommend_date), ...]（缺货量 qty > 0 且省中心有货）
        spec_df: 设备规格表（DEV_CODE → DEV_CLS/DEV_CATEG）
        global_scheme_id: 全局方案 ID

    Returns:
        list[dict]: 对齐 ADAM_PLAN_DAY_IAS_PRE 的记录（不含主键，落库时分配）
    """
    if not shortages:
        return []

    spec = spec_df if spec_df is not None and not spec_df.empty else pd.DataFrame(
        columns=['DEV_CODE', 'DEV_CLS', 'DEV_CATEG'])
    cls_map = dict(zip(spec['DEV_CODE'], spec['DEV_CLS'])) if 'DEV_CODE' in spec.columns else {}
    categ_map = dict(zip(spec['DEV_CODE'], spec['DEV_CATEG'])) if 'DEV_CODE' in spec.columns else {}

    records = []
    for org, dev, qty, date in shortages:
        if qty <= 0:
            continue
        records.append({
            'PRE_DATE': date,
            'REC_ORG_NO': org,
            'DEV_CLS': str(cls_map.get(dev, DEFAULT_DEV_CLS)),
            'DEV_CATEG': str(categ_map.get(dev, DEFAULT_DEV_CATEG)),
            'DEV_CODE': dev,
            'PLAN_IAS_NUM': int(qty),
            'EST_STOCK_NUM': None,
            'GLOBAL_SCHEME_ID': int(global_scheme_id),
            'DAILY_PLAN_STATUS': DAILY_PLAN_STATUS,
            'REPLE_TASK_TYPE': REPLE_TASK_TYPE,
            'TASK_SOURCE': TASK_SOURCE,
        })
        logger.info(
            f"紧急补库: 单位 {org} 设备码 {dev} 补库 {int(qty)} 台, "
            f"建议日期 {date}")
    logger.info(f"紧急补库: 生成 {len(records)} 条补库记录 (REPLE_TASK_TYPE={REPLE_TASK_TYPE})")
    return records


def insert_emergency_records(records):
    """紧急补库记录落库（主键分配 + 插入 ADAM_PLAN_DAY_IAS_PRE）。"""
    if not records:
        return None
    pks = query_pk_next(SEQ_PLAN_DAY_IAS_PRE, len(records))
    df = pd.DataFrame(records)
    df['PLAN_MONTH_IAS_PRE_ID'] = [int(x) for x in pks]
    res = insert_into_adam_plan_day_ias_pre(df)
    logger.info(f"紧急补库落库: {res}")
    return res
