"""
二阶段 (R,S) 补货算法 — 核心算法

固定周期 T 联合补货，动态基准库存 S = Poisson_ppf(alpha, mu)。
"""
import logging
from datetime import date, timedelta
from typing import Dict, Tuple

import numpy as np
import pandas as pd
from scipy.stats import poisson

logger = logging.getLogger(__name__)


def is_replenishment_day(target_date: date, d0: date, t: int) -> bool:
    """
    判断 target_date 是否为补货日。
    规则: (target_date - D0) % T == 0
    """
    if t <= 0:
        return False
    delta = (target_date - d0).days
    return delta >= 0 and delta % t == 0


def is_holiday(d: date) -> bool:
    """
    判断是否为中国节假日（含周末 + 法定假日）。
    使用 chinese_calendar 库。
    """
    try:
        from chinese_calendar import is_holiday as chinese_is_holiday
        return chinese_is_holiday(d)
    except ImportError:
        logger.warning("chinese_calendar 未安装，仅判断周末")
        return d.weekday() >= 5
    except NotImplementedError:
        # chinese_calendar 仅支持到 2025，2026+ 退化为周末判断
        logger.warning(f"chinese_calendar 不支持 {d.year} 年，退化为周末判断")
        return d.weekday() >= 5


def _date_range_strs(start: date, end: date):
    """生成日期字符串列表 ['YYYY-MM-DD', ...]"""
    return [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]


def compute_rs_plan(
    inventory_df: pd.DataFrame,
    demand_df: pd.DataFrame,
    spec_df: pd.DataFrame,
    substation_params: Dict[str, dict],
    default_params: dict,
    replenishment_date: date,
) -> pd.DataFrame:
    """
    核心 (R,S) 算法。

    对每个 (供电所, DEV_CODE) 组合:
      1. 获取该供电所的 T, alpha, D0
      2. 检查明天是否为补货日 + 非节假日 → 否则跳过
      3. μ = Σ 未来 T 天 (PRE_DATE ∈ [明天, 明天+T-1]) 的 PRE_NUM
      4. S = 0 if μ == 0 else poisson.ppf(alpha, mu)
      5. I = 从库存表查找，默认 0
      6. q = max(0, S - I)

    Args:
        inventory_df: ADAM_CITY_COUNTY_STOCK_SAMPLE 查询结果 (DS_SQL 已过滤 DIST_LV='05', DEV_STAT='01', OLD_NEW_FLAG='01')
                      列 ORG_NO, DEV_CODE, STOCK_NUM, DEV_CLS
        demand_df: ADAM_SUB_DMD_PRE 查询结果 (DS_SQL 已过滤 DIST_LV='05', VALID_FLAG='02', PRE_TYPE='05')
                   列 ORG_NO, DEV_CODE, PRE_DATE, PRE_NUM, BUS_TYPE
        spec_df: ADAM_SPEC_CODE_CONFIG 查询结果
                 列 DEV_CODE, DEV_CLS, DEV_CATEG
        substation_params: {org_no: {T, alpha, D0}}
        default_params: {T, alpha, D0}
        replenishment_date: 补货日（明天）

    Returns:
        pd.DataFrame: ADAM_REPLENISH_ORDER 对应列
    """
    from .config_loader import get_substation_param

    tomorrow = replenishment_date

    # ---- 1. 构建库存查找表 ----
    # DS_SQL 已通过 EXISTS 过滤: DIST_LV='05' + DEV_STAT='01' + OLD_NEW_FLAG='01'
    # 库存 ORG_NO 均为供电所级(9位)，直接精确匹配即可，无需县级回退
    if inventory_df.empty:
        stock_lookup: Dict[Tuple[str, str], float] = {}
    else:
        inv = inventory_df.copy()
        inv['ORG_NO'] = inv['ORG_NO'].astype(str).str.strip()
        inv['DEV_CODE'] = inv['DEV_CODE'].astype(str).str.strip()
        stock_lookup = dict(
            zip(
                zip(inv['ORG_NO'], inv['DEV_CODE']),
                inv['STOCK_NUM'].fillna(0).astype(float)
            )
        )

    # ---- 2. 构建设备规格查找 ----
    spec = spec_df.copy()
    spec['DEV_CODE'] = spec['DEV_CODE'].astype(str).str.strip()
    spec_lookup: Dict[str, dict] = {}
    for _, row in spec.iterrows():
        spec_lookup[row['DEV_CODE']] = {
            'DEV_CLS': str(row.get('DEV_CLS', '')).strip(),
            'DEV_CATEG': str(row.get('DEV_CATEG', '')).strip(),
        }

    # ---- 3. 预处理需求数据 ----
    if demand_df.empty:
        logger.warning("需求预测数据为空，无补货建议生成")
        return pd.DataFrame()

    dmd = demand_df.copy()
    dmd['ORG_NO'] = dmd['ORG_NO'].astype(str).str.strip()
    dmd['DEV_CODE'] = dmd['DEV_CODE'].astype(str).str.strip()
    dmd['PRE_DATE'] = pd.to_datetime(dmd['PRE_DATE']).dt.date
    dmd['PRE_NUM'] = dmd['PRE_NUM'].fillna(0).astype(float)

    # ---- 4. 遍历每个 (供电所, DEV_CODE) 组合 ----
    # 注: DS_SQL 已通过 EXISTS 过滤 DIST_LV='05' + VALID_FLAG='02'，无需 Python 端再过滤
    # 获取需求中所有唯一组合
    demand_pairs = dmd[['ORG_NO', 'DEV_CODE']].drop_duplicates()
    # 补充库存中有但需求中没有的组合（不生成补货记录，但供参考）

    results = []
    stats = {
        'total_pairs': len(demand_pairs),
        'non_replenishment_day': 0,
        'holiday_skip': 0,
        'mu_zero': 0,
        'stock_sufficient': 0,
        'replenishment': 0,
        'warn_insufficient_days': 0,
        'error_skip': 0,
        'total_qty': 0.0,
    }

    for _, pair in demand_pairs.iterrows():
        org = pair['ORG_NO']
        dev = pair['DEV_CODE']

        try:
            # 4a. 获取参数
            params = get_substation_param(org, substation_params, default_params)
            T = params['T']
            alpha = params['alpha']
            D0 = params['D0']

            # 4b. 检查补货日
            if not is_replenishment_day(tomorrow, D0, T):
                stats['non_replenishment_day'] += 1
                continue

            # 4c. 节假日跳过
            if is_holiday(tomorrow):
                stats['holiday_skip'] += 1
                continue

            # 4d. 聚合需求: PRE_DATE ∈ [tomorrow, tomorrow + T - 1]
            end_date = tomorrow + timedelta(days=T - 1)
            mask = (
                (dmd['ORG_NO'] == org) &
                (dmd['DEV_CODE'] == dev) &
                (dmd['PRE_DATE'] >= tomorrow) &
                (dmd['PRE_DATE'] <= end_date)
            )
            future_demand = dmd.loc[mask, 'PRE_NUM'].sum()

            # 检查是否不足 T 天
            actual_days = dmd.loc[mask, 'PRE_DATE'].nunique()
            if actual_days < T:
                stats['warn_insufficient_days'] += 1
                logger.debug(f"  [{org}/{dev}] 需求不足 T={T} 天，实际 {actual_days} 天")

            mu = future_demand

            # 4e. 计算 S
            if mu <= 0:
                S = 0.0
                q = 0.0
                stats['mu_zero'] += 1
            else:
                S = float(poisson.ppf(alpha, mu))
                S = np.ceil(S)  # 向上取整

            # 4f. 获取库存 — DS_SQL 已过滤为供电所数据，直接精确匹配，无数据则默认 0
            I = stock_lookup.get((org, dev), 0.0)

            q = max(0.0, S - I)

            if q <= 0:
                stats['stock_sufficient'] += 1
            else:
                stats['replenishment'] += 1
                stats['total_qty'] += q

            # 4g. 获取设备规格
            info = spec_lookup.get(dev, {})
            dev_cls = info.get('DEV_CLS', '')
            dev_categ = info.get('DEV_CATEG', '')

            results.append({
                'ORG_NO': org,
                'DEV_CLS': dev_cls,
                'DEV_CATEG': dev_categ,
                'DEV_CODE': dev,
                'REPLENISH_QTY': q,
                'TARGET_STOCK_S': S,
                'CAL_DATE': tomorrow,
                'CREATE_TIME': pd.Timestamp.now(),
            })

        except Exception as e:
            stats['error_skip'] += 1
            logger.warning(f"[{org}/{dev}] 计算异常，跳过: {e}")

    # ---- 5. 输出统计 ----
    logger.info(
        f"[算法] 完成: 总组合 {stats['total_pairs']}, "
        f"非补货日跳过 {stats['non_replenishment_day']}, "
        f"节假日跳过 {stats['holiday_skip']}, "
        f"mu=0 {stats['mu_zero']}, "
        f"库存充足 {stats['stock_sufficient']}, "
        f"需补货 {stats['replenishment']}, "
        f"补货总量 {stats['total_qty']:.0f}, "
        f"预测不足 T 天 {stats['warn_insufficient_days']}, "
        f"异常跳过 {stats['error_skip']}"
    )

    if not results:
        logger.info("[算法] 无补货建议生成")
        return pd.DataFrame()

    result_df = pd.DataFrame(results)
    return result_df
