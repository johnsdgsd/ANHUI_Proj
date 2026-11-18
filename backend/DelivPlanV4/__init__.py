"""
DelivPlanV4 — 日配送集合划分模块

使用两阶段算法:
    Stage 1: 候选路径枚举（滑动窗口 + 极角约束）
    Stage 2: 集合划分 ILP（PuLP + CBC）

公开入口:
    AdjustDaliyDeliveryV4(date_str) → (MainScheme, DetailScheme)
"""

import logging
import sys

from backend.DelivPlanV4.orchestrator import run_deliv_plan_v4


def AdjustDaliyDeliveryV4(date_str, max_stops=None, gk_dist_mist=None):
    """
    日配送集合划分算法 V4。

    Args:
        date_str: 配送日期，格式 'YYYY-MM-DD'
        max_stops: 可选，覆盖 ADAM_SYS_PARAM.GK_MAX_SITE_NUM（1~4）
        gk_dist_mist: 可选，覆盖 ADAM_SYS_PARAM.GK_DIST_MIST（km）

    Returns:
        tuple: (MainScheme DataFrame, DetailScheme DataFrame)
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        stream=sys.stdout
    )
    try:
        return run_deliv_plan_v4(date_str, max_stops=max_stops, gk_dist_mist=gk_dist_mist)
    except Exception:
        logging.exception(f"AdjustDaliyDeliveryV4 异常: date={date_str}")
        raise
