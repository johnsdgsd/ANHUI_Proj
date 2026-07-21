"""
二阶段 (R,S) 补货算法 — 编排器

完整流程: 加载参数 → 获取数据 → 执行算法 → 写入结果
"""
import logging
import os
import sys
from datetime import date, timedelta
from typing import Dict

import pandas as pd

# Ensure backend is importable
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJ_DIR = os.path.dirname(os.path.dirname(os.path.dirname(_BASE_DIR)))
sys.path.insert(0, _PROJ_DIR)

from backend.api.data_api.fetch_data import (
    query_adam_sys_param,
    query_adam_city_county_stock_sample,
    query_adam_sub_dmd_pre,
    query_adam_spec_code_config,
    query_adam_y_mgt_org,
    insert_into_adam_replenish_order,
    query_pk_next,
)
from backend.config.scheme_config import get_approved_scheme_config
from backend.algorithm.substation.config_loader import load_substation_params
from backend.algorithm.substation.algorithm import compute_rs_plan

logger = logging.getLogger(__name__)


def run_substation_replenishment() -> dict:
    """
    执行每日 (R,S) 补货流程。

    步骤:
      1. 加载系统参数（ADAM_SYS_PARAM）
      2. 确定补货日（明天），筛选需补货的供电所
      3. 获取库存快照（ADAM_CITY_COUNTY_STOCK_SAMPLE, data_date=昨天）
      4. 获取日需求预测（ADAM_SUB_DMD_PRE, PRE_TYPE='05'）
      5. 获取设备规格（ADAM_SPEC_CODE_CONFIG）
      6. 执行 (R,S) 算法
      7. 获取主键序列
      8. 写入 ADAM_REPLENISH_ORDER

    Returns:
        dict: {success, message, details}
    """
    today = date.today()
    tomorrow = today + timedelta(days=1)
    yesterday = today - timedelta(days=1)

    logger.info("=" * 60)
    logger.info(f"[RS] (R,S) 联合补货流程启动 — 今天={today}, 补货日={tomorrow}")
    logger.info("=" * 60)

    details: Dict = {
        'today': str(today),
        'tomorrow': str(tomorrow),
    }

    try:
        # ---- Step 1: 加载参数 ----
        logger.info("[RS] Step 1/7: 加载系统参数 (ADAM_SYS_PARAM)...")
        param_df = query_adam_sys_param()
        substation_params, default_params = load_substation_params(param_df)
        details['n_substation_params'] = len(substation_params)

        # ---- Step 2: 获取库存 ----
        logger.info(f"[RS] Step 2/7: 获取库存快照 (DATA_DATE={yesterday})...")
        stock_df = query_adam_city_county_stock_sample(yesterday.isoformat())
        if stock_df.empty:
            logger.warning("[RS] 库存数据为空，所有供电所视为库存 0")
        details['stock_rows'] = len(stock_df)
        details['stock_orgs'] = stock_df['ORG_NO'].nunique() if not stock_df.empty else 0

        # ---- Step 3: 获取需求预测 ----
        # 计算需要拉取的日期范围
        # 最大 T 值决定了需要多少天的预测
        all_t_values = [p['T'] for p in substation_params.values()]
        if default_params:
            all_t_values.append(default_params['T'])
        max_t = max(all_t_values) if all_t_values else 5

        forecast_end = tomorrow + timedelta(days=max_t - 1)
        logger.info(
            f"[RS] Step 3/7: 获取日需求预测 (PRE_TYPE='05', "
            f"{tomorrow} ~ {forecast_end})..."
        )

        # 跨月处理：如果需要跨月，分别拉取
        demand_frames = []
        d = tomorrow
        while d <= forecast_end:
            ym = d.strftime('%Y%m')
            year = ym[:4]
            month = ym[4:6]
            # 对于同一个月，只拉一次（整个月）
            start_of_month = d
            end_of_month = min(forecast_end, date(int(year), int(month),
                               _last_day_of_month(int(year), int(month))))
            if start_of_month <= end_of_month:
                df = query_adam_sub_dmd_pre(
                    pre_type='05',
                    start_date=start_of_month.isoformat(),
                    end_date=end_of_month.isoformat(),
                )
                if not df.empty:
                    demand_frames.append(df)
            # 跳到下个月
            if int(month) == 12:
                d = date(int(year) + 1, 1, 1)
            else:
                d = date(int(year), int(month) + 1, 1)

        if demand_frames:
            demand_df = pd.concat(demand_frames, ignore_index=True)
        else:
            demand_df = pd.DataFrame()
            logger.warning("[RS] 需求预测数据为空")

        details['demand_rows'] = len(demand_df)
        details['demand_orgs'] = demand_df['ORG_NO'].nunique() if not demand_df.empty else 0

        # ---- Step 4: 获取设备规格 ----
        logger.info("[RS] Step 4/7: 获取设备规格配置...")
        spec_df = query_adam_spec_code_config()
        details['spec_rows'] = len(spec_df)

        # ---- Step 4b: 获取组织层级统计信息 ----
        # DS_SQL 已通过 EXISTS 子查询过滤 DIST_LV='05' + VALID_FLAG='02'
        # 此处仅统计组织架构规模，不再构建县级回退映射（DB 端已完成过滤）
        logger.info("[RS] Step 4b/7: 统计组织架构层级...")
        org_df = query_adam_y_mgt_org()
        if not org_df.empty:
            active = org_df[org_df['VALID_FLAG'] == '02']
            substations = active[active['DIST_LV'] == '05']
            counties = active[(active['DIST_LV'] == '04') & (active['MGT_ORG_CODE'].astype(str).str.len() == 7)]
            details['substation_orgs'] = len(substations)
            details['county_orgs'] = len(counties)
            details['total_orgs'] = len(org_df)
            logger.info(
                f"  组织架构: 总{len(org_df)}条, 有效{len(active)}条, "
                f"供电所(DIST_LV=05) {len(substations)}个, 县(DIST_LV=04) {len(counties)}个"
            )

        # ---- Step 5: 执行算法 ----
        logger.info("[RS] Step 5/7: 执行 (R,S) 补货算法...")
        result_df = compute_rs_plan(
            inventory_df=stock_df,
            demand_df=demand_df,
            spec_df=spec_df,
            substation_params=substation_params,
            default_params=default_params,
            replenishment_date=tomorrow,
        )
        details['result_rows'] = len(result_df)

        if result_df.empty:
            logger.info("[RS] 无补货建议，流程结束")
            return {
                "success": True,
                "message": "今日无补货建议生成（非补货日或无需求）",
                "details": details,
            }

        # ---- Step 6: 获取主键 ----
        logger.info(f"[RS] Step 6/7: 获取主键序列 ({len(result_df)} 条)...")
        pk_list = query_pk_next("SEQ_ADAM_REPLENISH_ORDER", len(result_df))
        if not pk_list or len(pk_list) < len(result_df):
            raise RuntimeError(f"主键序列获取不足: 需要 {len(result_df)}, 实际 {len(pk_list) if pk_list else 0}")

        result_df['ORDER_ID'] = [int(p) for p in pk_list[:len(result_df)]]

        # ---- Step 7: 写入 ----
        logger.info("[RS] Step 7/7: 写入补货建议表 (ADAM_REPLENISH_ORDER)...")
        insert_result = insert_into_adam_replenish_order(result_df)
        details['insert'] = insert_result

        total_qty = result_df['REPLENISH_QTY'].sum() if 'REPLENISH_QTY' in result_df.columns else 0

        logger.info(
            f"[RS] 流程结束: 写入 {insert_result.get('success_count', 0)} 条, "
            f"补货总量 {total_qty:.0f} 件"
        )
        logger.info("=" * 60)

        return {
            "success": insert_result.get("success", False),
            "message": f"补货建议生成完成: {insert_result.get('success_count', 0)} 条, 补货总量 {total_qty:.0f} 件",
            "details": details,
        }

    except Exception as e:
        logger.error(f"[RS] 流程异常终止: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "details": details,
        }


def _last_day_of_month(year: int, month: int) -> int:
    """返回指定年月的最后一天"""
    import calendar
    return calendar.monthrange(year, month)[1]
