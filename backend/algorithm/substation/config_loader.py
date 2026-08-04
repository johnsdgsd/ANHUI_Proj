"""
二阶段 (R,S) 补货算法 — 参数加载
从 ADAM_AIS_SYS_PARAM 表加载补货周期 T、服务水平 alpha、基准起始日期 D0
"""
import logging
from datetime import date
from typing import Dict, Tuple

import pandas as pd

logger = logging.getLogger(__name__)


def load_substation_params(sys_param_df: pd.DataFrame) -> Tuple[Dict[str, dict], dict]:
    """
    从 ADAM_AIS_SYS_PARAM DataFrame 中解析供电所参数。

    ADAM_AIS_SYS_PARAM 结构:
        REC_ORG_NO: '0000' 为通用默认配置，其他为供电所独立配置
        REPLEISHMENT_CYCLE: 补货周期 T（天）
        TARGET_CYCLE_SERVICE_LEVEL: 服务水平 alpha (0-1)
        CYCLE_BASE_START_DATE: 基准起始日期 D0

    Args:
        sys_param_df: query_adam_ais_sys_param() 返回的 DataFrame

    Returns:
        substation_params: {org_no: {"T": int, "alpha": float, "D0": date}}
           供电所独立配置（不含 '0000'）
        default_params: {"T": int, "alpha": float, "D0": date}
           通用默认配置，REC_ORG_NO='0000'
    """
    substation_params: Dict[str, dict] = {}
    default_params: dict = {}

    if sys_param_df is None or sys_param_df.empty:
        raise ValueError("ADAM_AIS_SYS_PARAM 查询为空，无法加载补货参数")

    for _, row in sys_param_df.iterrows():
        org_no = str(row['REC_ORG_NO']).strip()
        t = int(row.get('REPLEISHMENT_CYCLE', 5))
        alpha = float(row.get('TARGET_CYCLE_SERVICE_LEVEL', 0.95))

        # 解析日期
        d0_val = row.get('CYCLE_BASE_START_DATE')
        if isinstance(d0_val, date):
            d0 = d0_val
        elif isinstance(d0_val, str):
            d0 = date.fromisoformat(d0_val)
        else:
            d0 = date(2026, 7, 16)  # fallback

        params = {"T": t, "alpha": alpha, "D0": d0}

        if org_no == '0000':
            default_params = params
        else:
            substation_params[org_no] = params

    if not default_params:
        raise ValueError("ADAM_SYS_PARAM 中缺少 REC_ORG_NO='0000' 的默认配置")

    logger.info(
        f"[Config] 参数加载完成: 默认 T={default_params['T']}, "
        f"alpha={default_params['alpha']}, D0={default_params['D0']}, "
        f"供电所独立配置 {len(substation_params)} 个"
    )
    return substation_params, default_params


def get_substation_param(
    org_no: str,
    substation_params: Dict[str, dict],
    default_params: dict
) -> dict:
    """获取单个供电所的补货参数。优先用独立配置，否则用默认。"""
    org_no = str(org_no).strip()
    if org_no in substation_params:
        return substation_params[org_no]
    return default_params
