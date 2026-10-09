"""
供电所库存阈值预测 — 编排器

完整流程: 解析参数 → 计算窗口 → 取需求 → 执行算法 → 先删后插
"""
import logging
import os
import sys
from datetime import date, timedelta
from typing import Dict, Optional

import pandas as pd

# Ensure backend is importable
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJ_DIR = os.path.dirname(os.path.dirname(os.path.dirname(_BASE_DIR)))
sys.path.insert(0, _PROJ_DIR)

from backend.api.data_api.fetch_data import (
    query_adam_sub_dmd_pre_latest_date,
    query_adam_sub_dmd_pre_window,
    query_adam_spec_code_config,
    query_adam_y_mgt_org,
    insert_into_adam_sub_stock_limt_pre,
    delete_adam_sub_stock_limt_pre,
    query_pk_next,
)
from backend.algorithm.sub_stock_limit.config_loader import (
    parse_alpha,
    parse_org_code,
    parse_window_days,
    resolve_alpha,
)
from backend.algorithm.sub_stock_limit.algorithm import compute_threshold_plan

logger = logging.getLogger(__name__)

SEQ_NAME = 'SEQ_ADAM_SUB_STOCK_LIMT_PRE'


def _lookup_org_name(org_code: str) -> str:
    """
    按编码取组织中文名，仅用于日志与返回展示（如 34401 → 合肥供电公司）。

    查不到或查询失败一律返回空串 —— 展示信息不应影响阈值计算主流程。
    """
    if not org_code or org_code == 'ALL':
        return ''
    try:
        org_df = query_adam_y_mgt_org()
        hit = org_df[org_df['MGT_ORG_CODE'].astype(str).str.strip() == org_code]
        if hit.empty:
            return ''
        return str(hit.iloc[0]['MGT_ORG_NAME']).strip()
    except Exception as e:
        logger.warning(f"[阈值] 组织名称查询失败（仅影响日志展示，不影响计算）: {e}")
        return ''


def run_sub_stock_limit(
    org_code=None,
    alpha=None,
    down_days=None,
    up_days=None,
) -> dict:
    """
    执行供电所库存阈值预测。

    步骤:
      1. 解析参数（范围组织编码 / alpha / 上下限窗口天数）
      2. 计算需求窗口 [今天, 今天+D-1]（严格未来窗口，不回落到历史数据）
      3. 查询窗口需求合计（DS_SQL 内完成范围下钻与聚合）
      4. 获取设备规格（ADAM_SPEC_CODE_CONFIG）
      5. 执行阈值算法
      6. 删除范围内旧阈值
      7. 获取主键序列并写入 ADAM_SUB_STOCK_LIMT_PRE

    Args:
        org_code: 可选，市县编码（5 位或 7 位）或供电所编码（9 位）；
                  None 或空表示全省；传入市县码时只预测该市县名下的供电所
        alpha: 可选，服务水平，缺省 0.95（不读参数配置表）
        down_days: 可选，下限窗口天数，默认 7
        up_days: 可选，上限窗口天数，默认 14

    Returns:
        dict: {success, message, details}
    """
    today = date.today()

    logger.info("=" * 60)
    logger.info("[阈值] 供电所库存阈值预测流程启动")
    logger.info("=" * 60)

    details: Dict = {}

    try:
        # ---- Step 1: 解析参数 ----
        logger.info("[阈值] Step 1/7: 解析入参...")
        org_scope = parse_org_code(org_code)
        org_name = _lookup_org_name(org_scope)
        # 日志里带上中文名，现场不必再拿编码去反查是哪个市县
        scope_label = f"{org_scope}（{org_name}）" if org_name else org_scope
        alpha_override = parse_alpha(alpha)
        alpha = resolve_alpha(alpha_override)
        d_down, d_up = parse_window_days(down_days, up_days)

        details['org_scope'] = org_scope
        details['org_name'] = org_name
        details['down_days'] = d_down
        details['up_days'] = d_up
        details['alpha'] = alpha
        details['alpha_override'] = alpha_override
        logger.info(
            f"  范围={scope_label}, 窗口 下限{d_down}天/上限{d_up}天, "
            f"alpha={alpha}{'（请求参数）' if alpha_override is not None else '（默认）'}"
        )

        # ---- Step 2: 计算需求窗口（严格未来窗口）----
        # 窗口起点固定为今天；需求表数据滞后时窗口内查不到数据，按 0 计，
        # 阈值随之算为 0 并由算法过滤（不落库），等预测数据滚动更新后自然恢复
        start_date = today
        down_end = today + timedelta(days=d_down - 1)
        up_end = today + timedelta(days=d_up - 1)

        details['window'] = {
            'start': start_date.isoformat(),
            'down_end': down_end.isoformat(),
            'up_end': up_end.isoformat(),
        }
        logger.info(
            f"[阈值] Step 2/7: 需求窗口: 下限 [{start_date} ~ {down_end}] {d_down}天, "
            f"上限 [{start_date} ~ {up_end}] {d_up}天"
        )

        # ---- Step 2b: 需求数据范围检查（特殊情况日志）----
        # 需求预测表滚动不及时时，窗口可能落在数据范围之外，此时窗口内需求按 0 计，
        # 阈值会算成 0 并被过滤 —— 属预期行为，但必须留下明确的日志便于排查。
        # 本步是可选增强：依赖 DS_SQL 条目 gk-adam-query-adam-sub-dmd-pre-latest-date，
        # 该条目现场可不部署（删除），此时仅少一条诊断日志，不影响阈值计算与落库。
        try:
            latest = query_adam_sub_dmd_pre_latest_date()
            diag_ok = True
        except Exception as e:
            logger.warning(f"[阈值] 需求数据范围诊断不可用，跳过该检查（不影响阈值计算）: {e}")
            latest, diag_ok = None, False
        details['latest_pre_date'] = latest
        details['range_check_available'] = diag_ok

        if not diag_ok:
            pass
        elif latest is None:
            logger.warning(
                "[阈值] 需求预测表无可用日预测数据 (PRE_TYPE='05')，本次不生成阈值"
            )
        else:
            latest_date = date.fromisoformat(latest)
            if start_date > latest_date:
                logger.warning(
                    f"[阈值] 需求窗口整体超出预测数据范围: 窗口 [{start_date} ~ {up_end}]，"
                    f"数据最新到 {latest_date}，窗口起点已超出 {(start_date - latest_date).days} 天，"
                    f"本次不生成阈值（等待需求预测滚动到未来日期）"
                )
            elif up_end > latest_date:
                over_down = (down_end - latest_date).days
                logger.warning(
                    f"[阈值] 需求窗口上限超出预测数据范围: 上限截止 {up_end} 超出数据最新日期 "
                    f"{latest_date} {(up_end - latest_date).days} 天；下限截止 {down_end} "
                    + (f"亦超出 {over_down} 天" if down_end > latest_date else "未超出")
                    + "，超出部分按无需求(0)处理，算出的阈值可能偏低"
                )
            else:
                logger.info(f"[阈值] 数据范围检查通过: 窗口 [{start_date} ~ {up_end}] 在数据范围 {latest_date} 内")

        # ---- Step 3: 查询窗口需求 ----
        logger.info(f"[阈值] Step 3/7: 查询窗口需求 (PRE_TYPE='05', 范围={scope_label})...")
        window_df = query_adam_sub_dmd_pre_window(
            start_date=start_date.isoformat(),
            down_end=down_end.isoformat(),
            up_end=up_end.isoformat(),
            org_no=org_scope,
        )
        details['window_rows'] = len(window_df)
        details['window_orgs'] = window_df['ORG_NO'].nunique() if not window_df.empty else 0
        details['window_devs'] = window_df['DEV_CODE'].nunique() if not window_df.empty else 0

        if window_df.empty:
            logger.warning(
                "[阈值] 窗口内无需求数据 —— 需求预测表可能尚未滚动到未来日期，"
                "本次不生成阈值"
            )
            suffix = ''
            if diag_ok:
                suffix = (f"需求预测数据最新到 {latest}，请确认供电所需求预测表已滚动到未来日期"
                          if latest else "需求预测表无可用日预测数据")
            return {
                "success": True,
                "message": f"窗口 [{start_date} ~ {up_end}] 内无需求预测数据，未生成阈值。" + suffix,
                "details": details,
            }

        # ---- Step 4: 获取设备规格 ----
        logger.info("[阈值] Step 4/7: 获取设备规格配置...")
        spec_df = query_adam_spec_code_config()
        details['spec_rows'] = len(spec_df)

        # ---- Step 5: 执行算法 ----
        logger.info("[阈值] Step 5/7: 执行阈值算法...")
        result_df = compute_threshold_plan(
            window_df=window_df,
            spec_df=spec_df,
            alpha=alpha,
            down_days=d_down,
            up_days=d_up,
        )
        details['result_rows'] = len(result_df)

        if result_df.empty:
            logger.info("[阈值] 无阈值生成（窗口内需求合计均为 0）")
            return {
                "success": True,
                "message": "窗口内需求合计均为 0，未生成阈值",
                "details": details,
            }

        # ---- Step 6: 删除范围内旧阈值 ----
        logger.info(f"[阈值] Step 6/7: 删除范围内旧阈值 (范围={scope_label})...")
        try:
            del_result = delete_adam_sub_stock_limt_pre(org_scope)
            details['delete'] = del_result
        except Exception as e:
            # 与补货模块一致：删除失败不阻断插入，但记录告警
            logger.warning(f"[阈值] 删除旧阈值失败（继续执行插入）: {e}")
            details['delete_error'] = str(e)

        # ---- Step 7a: 获取主键 ----
        logger.info(f"[阈值] Step 7/7: 获取主键序列 ({len(result_df)} 条)...")
        pk_list = query_pk_next(SEQ_NAME, len(result_df))
        if not pk_list or len(pk_list) < len(result_df):
            raise RuntimeError(
                f"主键序列获取不足: 需要 {len(result_df)}, "
                f"实际 {len(pk_list) if pk_list else 0}"
            )
        result_df['SUB_STOCK_LIMT_ID'] = [int(p) for p in pk_list[:len(result_df)]]

        # ---- Step 7b: 写入 ----
        logger.info("[阈值] Step 7/7: 写入阈值表 (ADAM_SUB_STOCK_LIMT_PRE)...")
        insert_result = insert_into_adam_sub_stock_limt_pre(result_df)
        details['insert'] = insert_result

        total_up = int(result_df['PRE_UP'].sum())
        total_down = int(result_df['PRE_DOWN'].sum())

        logger.info(
            f"[阈值] 流程结束: 写入 {insert_result.get('success_count', 0)} 条, "
            f"下限合计 {total_down}, 上限合计 {total_up}"
        )
        logger.info("=" * 60)

        return {
            "success": insert_result.get("success", False),
            "message": (
                f"阈值生成完成: {insert_result.get('success_count', 0)} 条, "
                f"下限合计 {total_down}, 上限合计 {total_up}, "
                f"范围 {scope_label}, 窗口 {d_down}/{d_up} 天"
            ),
            "details": details,
        }

    except Exception as e:
        logger.error(f"[阈值] 流程异常终止: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "details": details,
        }
