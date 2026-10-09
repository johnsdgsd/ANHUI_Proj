"""
供电所库存阈值预测 — 核心算法

对每个 (供电所, 设备码) 组合:
  1. 取未来 D_down 天需求合计 λ_down、未来 D_up 天需求合计 λ_up
  2. 阈值下限 PRE_DOWN = Poisson_ppf(alpha, λ_down) 向上取整
  3. 阈值上限 PRE_UP   = Poisson_ppf(alpha, λ_up)   向上取整

与供电所补货 (R,S) 的区别: 补货算 q = max(0, S - I)（减现有库存），
阈值预测直接输出 S 本身，不减库存。
"""
import logging
from typing import Dict

import numpy as np
import pandas as pd
from scipy.stats import poisson

from .config_loader import DEFAULT_ALPHA

logger = logging.getLogger(__name__)


def poisson_quantile(alpha: float, lam: float) -> float:
    """
    泊松分布 alpha 分位数（向上取整）。

    Args:
        alpha: 服务水平，取值 (0, 1)
        lam: 泊松分布均值 λ（窗口需求合计）

    Returns:
        float: ceil(Poisson_ppf(alpha, lam))；lam <= 0 时返回 0
    """
    if lam is None or lam <= 0:
        return 0.0
    return float(np.ceil(float(poisson.ppf(alpha, lam))))


def compute_threshold_plan(
    window_df: pd.DataFrame,
    spec_df: pd.DataFrame,
    alpha: float = DEFAULT_ALPHA,
    down_days: int = 7,
    up_days: int = 14,
) -> pd.DataFrame:
    """
    核心阈值算法。

    Args:
        window_df: 窗口需求查询结果（DS_SQL 已完成按 (ORG, DEV) 聚合 + 有效供电所过滤）
                   列 ORG_NO, PRNT_ORG_CODE, DEV_CODE, DOWN_NUM, UP_NUM
        spec_df: ADAM_SPEC_CODE_CONFIG 查询结果，列 DEV_CODE, DEV_CLS, DEV_CATEG
        alpha: 服务水平，全量使用同一个值（默认 0.95）
        down_days: 下限需求窗口天数
        up_days: 上限需求窗口天数

    Returns:
        pd.DataFrame: ADAM_SUB_STOCK_LIMT_PRE 对应列
                      ORG_NO, PRNT_ORG_CODE, DEV_CLS, DEV_CATEG, DEV_CODE,
                      PRE_UP, PRE_DOWN, UPDATE_TIME
    """
    if window_df is None or window_df.empty:
        logger.warning("窗口需求数据为空，无阈值生成")
        return pd.DataFrame()

    # ---- 1. 构建设备规格查找 ----
    spec = spec_df.copy()
    spec['DEV_CODE'] = spec['DEV_CODE'].astype(str).str.strip()
    spec_lookup: Dict[str, dict] = {}
    for _, row in spec.iterrows():
        spec_lookup[row['DEV_CODE']] = {
            'DEV_CLS': str(row.get('DEV_CLS', '')).strip(),
            'DEV_CATEG': str(row.get('DEV_CATEG', '')).strip(),
        }

    # ---- 2. 预处理窗口需求 ----
    win = window_df.copy()
    win['ORG_NO'] = win['ORG_NO'].astype(str).str.strip()
    win['DEV_CODE'] = win['DEV_CODE'].astype(str).str.strip()
    win['DOWN_NUM'] = pd.to_numeric(win['DOWN_NUM'], errors='coerce').fillna(0).astype(float)
    win['UP_NUM'] = pd.to_numeric(win['UP_NUM'], errors='coerce').fillna(0).astype(float)
    if 'PRNT_ORG_CODE' in win.columns:
        win['PRNT_ORG_CODE'] = win['PRNT_ORG_CODE'].astype(str).str.strip()
    else:
        win['PRNT_ORG_CODE'] = ''

    # ---- 3. 遍历每个 (供电所, DEV_CODE) 组合 ----
    now = pd.Timestamp.now()
    results = []
    stats = {
        'total_pairs': len(win),
        'zero_down': 0,       # λ_down == 0，不落库
        'generated': 0,
        'error_skip': 0,
    }

    for _, row in win.iterrows():
        org = row['ORG_NO']
        dev = row['DEV_CODE']
        lam_down = float(row['DOWN_NUM'])
        lam_up = float(row['UP_NUM'])

        try:
            # 3a. λ_down == 0 → 无需求，不生成记录（避免空记录膨胀）
            if lam_down <= 0:
                stats['zero_down'] += 1
                continue

            # 3b. 计算上下限（alpha 全量统一，来自请求参数或默认 0.95）
            pre_down = poisson_quantile(alpha, lam_down)
            pre_up = poisson_quantile(alpha, lam_up)

            # 上限窗口天数 >= 下限窗口天数，理论上 PRE_UP >= PRE_DOWN；
            # 若数据异常导致反转，兜底对齐，保证业务约束
            if pre_up < pre_down:
                logger.warning(
                    f"  [{org}/{dev}] 上限({pre_up}) < 下限({pre_down})，"
                    f"λ_down={lam_down}, λ_up={lam_up}，已修正为相等"
                )
                pre_up = pre_down

            info = spec_lookup.get(dev, {})

            results.append({
                'ORG_NO': org,
                'PRNT_ORG_CODE': row['PRNT_ORG_CODE'],
                'DEV_CLS': info.get('DEV_CLS', ''),
                'DEV_CATEG': info.get('DEV_CATEG', ''),
                'DEV_CODE': dev,
                'PRE_UP': int(pre_up),
                'PRE_DOWN': int(pre_down),
                'UPDATE_TIME': now,
            })
            stats['generated'] += 1

        except Exception as e:
            stats['error_skip'] += 1
            logger.warning(f"[{org}/{dev}] 阈值计算异常，跳过: {e}")

    # ---- 4. 输出统计 ----
    logger.info(
        f"[阈值算法] 完成: 总组合 {stats['total_pairs']}, "
        f"无需求跳过(λ_down=0) {stats['zero_down']}, "
        f"生成阈值 {stats['generated']}, "
        f"异常跳过 {stats['error_skip']}, "
        f"alpha={alpha}, "
        f"窗口 下限{down_days}天/上限{up_days}天"
    )

    if not results:
        logger.info("[阈值算法] 无阈值生成")
        return pd.DataFrame()

    return pd.DataFrame(results)
