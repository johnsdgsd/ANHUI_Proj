"""
仓网布局优化 — 编排器
完整流程: 加载数据 → 预处理 → 优化算法 → 写入结果
"""
import logging
import os
import sys
from datetime import datetime
from typing import Dict

import pandas as pd

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJ_DIR = os.path.dirname(os.path.dirname(os.path.dirname(_BASE_DIR)))
sys.path.insert(0, _PROJ_DIR)

from backend.api.data_api.fetch_data import (
    query_adam_station_demand_mapped,
    query_adam_warehouse_candidate,
    query_adam_power_station_active,
    query_adam_station_dist_mist,
    insert_adam_layout_result,
    insert_adam_layout_result_det,
    query_pk_next,
)
from backend.algorithm.warehouse_layout.config import N_PARETO_SOLUTIONS
from backend.algorithm.warehouse_layout.algorithm import (
    prepare_data,
    optimize_warehouse_layout,
)

logger = logging.getLogger(__name__)


def run_warehouse_optimization() -> dict:
    """执行仓网布局优化流程。

    Returns:
        dict: {success, message, summary}
    """
    logger.info("=" * 60)
    logger.info("[仓网优化] 布局优化流程启动")
    logger.info("=" * 60)

    try:
        # ---- Step 1: 加载数据 ----
        logger.info("[仓网优化] Step 1/5: 加载数据...")
        demand_df = query_adam_station_demand_mapped()
        warehouse_df = query_adam_warehouse_candidate()
        station_df = query_adam_power_station_active()
        dist_df = query_adam_station_dist_mist()

        # ---- Step 2: 数据预处理 ----
        logger.info("[仓网优化] Step 2/5: 数据预处理...")
        data = prepare_data(demand_df, warehouse_df, station_df, dist_df)

        if len(data['dev_codes']) == 0:
            return {"success": False, "error": "无有效设备码（全部无单价），无法执行优化"}

        # ---- Step 3: 执行优化 ----
        logger.info("[仓网优化] Step 3/5: 执行双目标优化...")
        solutions = optimize_warehouse_layout(data)

        if not solutions:
            return {"success": False, "error": "未生成有效解"}

        # ---- Step 4: 写入结果 ----
        logger.info("[仓网优化] Step 4/5: 写入结果表...")
        scenario_code = datetime.now().strftime("SC%Y%m%d%H%M%S")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 主表
        result_rows = []
        detail_rows = []

        # 通过 /pk/next 申请主表与明细表主键（PK_ALLOC 已注册对应序列）
        result_ids = [int(x) for x in query_pk_next("SEQ_ADAM_LAYOUT_RESULT", len(solutions))]

        for idx, sol in enumerate(solutions):
            result_id = result_ids[idx]
            result_rows.append({
                'RESULT_ID': result_id,
                'SCENARIO_CODE': scenario_code,
                'WEIGHT': idx + 1,
                'OBJECTIVE_COST': round(sol['Z1']),
                'OBJECTIVE_DIST': round(sol['Z2']),
                'CREATE_TIME': now,
            })

            for m in sol['mapping']:
                detail_rows.append({
                    'RESULT_ID': result_id,
                    'SCENARIO_CODE': scenario_code,
                    'ORG_NO': m['wh_code'],
                    'STATION_ORG_CODE': m['station_code'],
                    'CREATE_TIME': now,
                })

        det_ids = [int(x) for x in query_pk_next("SEQ_ADAM_LAYOUT_RESULT_DET", len(detail_rows))]
        for row, det_id in zip(detail_rows, det_ids):
            row['RESULT_DET_ID'] = det_id

        result_df = pd.DataFrame(result_rows)
        detail_df = pd.DataFrame(detail_rows)

        insert_result = insert_adam_layout_result(result_df)
        insert_detail = insert_adam_layout_result_det(detail_df)

        # ---- Step 5: 汇总 ----
        summary = {
            'scenario_code': scenario_code,
            'n_solutions': len(solutions),
            'n_warehouses': len(data['wh_codes']),
            'n_stations': len(data['station_codes']),
            'n_dev_codes': len(data['dev_codes']),
            'total_demand': data['demand'].sum(),
            'solutions': [
                {
                    'label': s['label'],
                    'Z1_cost': round(s['Z1']),
                    'Z2_avg_dist_km': round(s['Z2'], 1),
                    'n_opened_warehouses': s['n_opened'],
                    'opened': s['opened_wh'][:5] + ('...' if len(s['opened_wh']) > 5 else ''),
                }
                for s in solutions
            ],
            'result_insert': insert_result,
            'detail_insert': insert_detail,
        }

        logger.info("[仓网优化] Step 5/5: 流程完成!")
        logger.info(f"[仓网优化] 方案编码: {scenario_code}")
        logger.info(f"[仓网优化] 帕累托前沿 {len(solutions)} 组解:")
        for s in solutions:
            logger.info(f"  {s['label']}: Z₁={s['Z1']:,.0f}, Z₂={s['Z2']:.1f}km, "
                        f"库房 {s['n_opened']}个")

        return {
            "success": True,
            "message": f"仓网布局优化完成, {len(solutions)} 组解",
            "summary": summary,
        }

    except Exception as e:
        logger.exception("[仓网优化] 优化流程异常")
        return {"success": False, "error": str(e)}
