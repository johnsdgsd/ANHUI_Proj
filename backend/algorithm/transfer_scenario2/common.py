"""
调拨场景二 — 公共工具（无业务逻辑）

供四个独立核心模块（stockout_detect / supplier_select / emergency / transfer）共用。
本文件只做：外部数据适配、单位/距离/规格映射、库存上限（可插拔）、省中心查询、输出构造。

复用（不复制）:
    transfer.data_prep.prepare_transfer_network        — 87家单位 + 距离矩阵 + 完整性校验
    transfer.orchestrator._compute_14day_demand        — 库存上限当前实现
    transfer.orchestrator._build_spec_maps             — DEV_CODE → DEV_CLS/DEV_CATEG
"""
import logging
from collections import defaultdict

import pandas as pd

from backend.algorithm.transfer.data_prep import prepare_transfer_network
from backend.algorithm.transfer.orchestrator import (
    _compute_14day_demand,
    _build_spec_maps as _build_spec_maps_s1,
)
from backend.algorithm.transfer_scenario2.config import (
    UPPER_WINDOW_DAYS,
    UPPER_Z,
    DEFAULT_DEV_CLS,
    DEFAULT_DEV_CATEG,
)
from backend.api.data_api.fetch_data import query_adam_realtime_qua_stock

logger = logging.getLogger(__name__)


def load_transfer_network():
    """调拨网络：87家单位 + 距离矩阵 + 完整性校验（复用场景一）。"""
    return prepare_transfer_network()


def build_spec_maps(spec_df):
    """DEV_CODE → (DEV_CLS, DEV_CATEG) 映射（复用场景一）。"""
    return _build_spec_maps_s1(spec_df)


def _compute_upper_limit(yqm_df, month_days, window_days=UPPER_WINDOW_DAYS, z=UPPER_Z):
    """库存上限（调出单位判定标准）：未来两周高服务水平。

    **独立可插拔**：当前实现与场景一 `_compute_14day_demand` 一致
    （μ14 + z·√μ14，z=0.95 分位 1.645）；后续如需更换口径
    （如改读 ADAM_STOCK_MONTH_LIMIT_PRE.BASE_LIMIT），只需替换本函数。

    Returns:
        dict: {(org_no, dev_code): Upper}
    """
    return _compute_14day_demand(yqm_df, month_days, window_days, z)


def _query_province_center_stock(dev):
    """省中心可配送库存：实时合格品表（用户确认 2026-08-07）。

    `query_adam_realtime_qua_stock()` → DS_SQL 按设备码 SUM(QUA_STOCK_NUM)，
    返回 DataFrame(DEV_CODE_NO, QUA_STOCK_NUM)。

    Returns:
        float: 该设备码省中心合格品数量；无数据/空表返回 0（视为无货）。
    """
    try:
        qua = query_adam_realtime_qua_stock()
    except Exception as e:
        logger.warning(f"省中心库存查询失败(实时合格品表): {e}, 按无货处理")
        return 0.0
    if qua is None or qua.empty:
        return 0.0
    if 'DEV_CODE_NO' not in qua.columns:
        logger.warning("省中心实时合格品表缺少 DEV_CODE_NO 列, 按无货处理")
        return 0.0
    rows = qua[qua['DEV_CODE_NO'] == dev]
    if rows.empty:
        return 0.0
    return float(rows['QUA_STOCK_NUM'].sum())


def build_stock_lookup(stock_df):
    """{(org, dev): STOCK_NUM}（用于输出列 SEND_STOCK_NUM/REC_STOCK_NUM 计算）。"""
    return {(str(r['MGT_ORG_CODE']).strip(), r['DEV_CODE_NO']):
            float(r['STOCK_NUM'] or 0.0) for _, r in stock_df.iterrows()}


def build_output_df(rows, stock_lookup, dev_cls, dev_categ,
                    global_scheme_id, allot_date, send_reason):
    """构造 ADAM_ALLOT_DAY_PLAN_PRE 输出 DataFrame。

    SEND_STOCK_NUM = 调出单位该设备码快照 STOCK_NUM − Σ调出量
    REC_STOCK_NUM  = 调入单位该设备码快照 STOCK_NUM + Σ调入量（无记录按 0）

    Args:
        rows: [{'SEND_ORG_NO','REC_ORG_NO','DEV_CODE','SEND_NUM'}, ...]

    Returns:
        pd.DataFrame | None（rows 为空返回 None）
    """
    if not rows:
        return None

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
            'SEND_REASON': send_reason,
        })

    df = pd.DataFrame(out, columns=[
        'ALLOT_DAY_PLAN_PRE_ID', 'ALLOT_DATE', 'SEND_ORG_NO', 'REC_ORG_NO',
        'DEV_CLS', 'DEV_CATEG', 'DEV_CODE', 'SEND_NUM', 'SEND_STOCK_NUM',
        'REC_STOCK_NUM', 'GLOBAL_SCHEME_ID', 'SEND_REASON',
    ])
    neg = df[df['SEND_STOCK_NUM'] < 0]
    if not neg.empty:
        logger.warning(
            f"输出检查: {len(neg)} 条 SEND_STOCK_NUM 为负的记录:\n"
            f"{neg[['SEND_ORG_NO', 'REC_ORG_NO', 'DEV_CODE', 'SEND_NUM', 'SEND_STOCK_NUM']].to_string()}")
    return df
