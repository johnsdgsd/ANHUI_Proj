"""
仓网布局优化 — 数据预处理与 ε-约束主循环
"""
import logging
from typing import List, Tuple

import numpy as np
import pandas as pd

from backend.algorithm.warehouse_layout.config import (
    R_w, L_w, z_w, ANNUAL_INTEREST_RATE, TRANSPORT_UNIT_PRICE,
    N_PARETO_SOLUTIONS,
)
from backend.algorithm.warehouse_layout.milp_solver import (
    solve_distance_milp,
    solve_cost_milp,
    solve_constrained_milp,
)

logger = logging.getLogger(__name__)


# ================================================================
# 数据预处理
# ================================================================


def prepare_data(
    demand_df: pd.DataFrame,
    warehouse_df: pd.DataFrame,
    station_df: pd.DataFrame,
    dist_df: pd.DataFrame,
) -> dict:
    """构建算法所需矩阵和索引。

    处理步骤:
      1. 提取三维度（供电所、库房、有单价设备码）
      2. 取三表交集供电所（在 station + demand + dist 中均出现）
      3. 构建需求矩阵 S×D、距离矩阵 W×S、成本向量
    """
    # ---- 1. 供电所维度（三表交集） ----
    station_codes_all = set(station_df['STATION_ORG_CODE'].astype(str).str.strip())
    demand_stations = set(demand_df['STATION_ORG_CODE'].astype(str).str.strip())
    dist_stations = set(dist_df['STATION_ORG_CODE'].astype(str).str.strip())

    if len(station_df) > len(station_codes_all):
        logger.warning(f"[数据准备] 供电所表有 {len(station_df) - len(station_codes_all)} 条重复编码，"
                       f"已去重 ({len(station_df)} → {len(station_codes_all)})")

    logger.info(f"[数据准备] 原始集合: 供电所={len(station_codes_all)}, "
                f"年需求={len(demand_stations)}, 距离={len(dist_stations)}")

    common = station_codes_all & demand_stations & dist_stations
    missing = station_codes_all - common
    if missing:
        no_demand = [m for m in missing if m not in demand_stations]
        no_dist = [m for m in missing if m not in dist_stations]
        logger.warning(f"[数据准备] {len(missing)} 个供电所排除: "
                       f"缺年需求 {len(no_demand)} 个, 缺距离 {len(no_dist)} 个")
        for m in sorted(missing)[:5]:
            reasons = []
            if m not in demand_stations:
                reasons.append("缺年需求")
            if m not in dist_stations:
                reasons.append("缺距离")
            logger.warning(f"[数据准备]   {m}: {', '.join(reasons)}")
        if len(missing) > 5:
            logger.warning(f"[数据准备]   ... 等共 {len(missing)} 个")

    station_codes = sorted(common)
    s2i = {c: i for i, c in enumerate(station_codes)}
    S = len(station_codes)

    # ---- 2. 库房维度 ----
    wh_codes = sorted(set(warehouse_df['ORG_NO'].astype(str).str.strip()))
    w2i = {c: i for i, c in enumerate(wh_codes)}
    W = len(wh_codes)

    # ---- 3. 设备码维度（有单价的） ----
    prices = {}
    for _, row in demand_df.iterrows():
        dc = str(row['DEV_CODE']).strip()
        p = row.get('AVG_PRICE')
        if pd.notna(p) and float(p) > 0:
            prices[dc] = float(p)

    all_devs = set(demand_df['DEV_CODE'].astype(str).str.strip())
    dev_codes = sorted(set(d for d in all_devs if d in prices))
    skipped = all_devs - set(dev_codes)
    if skipped:
        logger.warning(f"[数据准备] {len(skipped)} 个设备码无单价，跳过: {sorted(skipped)}")
    D = len(dev_codes)
    d2i = {c: i for i, c in enumerate(dev_codes)}

    # ---- 4. 需求矩阵 S×D ----
    demand_mat = np.zeros((S, D), dtype=float)
    for _, row in demand_df.iterrows():
        s = str(row['STATION_ORG_CODE']).strip()
        d = str(row['DEV_CODE']).strip()
        if s in s2i and d in d2i:
            demand_mat[s2i[s], d2i[d]] += float(row.get('ANNUAL_DEMAND', 0))

    # ---- 5. 距离矩阵 W×S ----
    dist_mat = np.full((W, S), 1e9)
    for _, row in dist_df.iterrows():
        w = str(row['ORG_NO']).strip()
        s = str(row['STATION_ORG_CODE']).strip()
        if w in w2i and s in s2i:
            dist_mat[w2i[w], s2i[s]] = float(row['DISTANCE'])

    # ---- 6. 成本向量 ----
    fixed_cost = np.zeros(W)
    wh_trans_dist = np.zeros(W)
    for _, row in warehouse_df.iterrows():
        w = str(row['ORG_NO']).strip()
        if w in w2i:
            fixed_cost[w2i[w]] = float(row.get('FIXED_COST_F', 0))
            wh_trans_dist[w2i[w]] = float(row.get('TRANS_DIST', 0))

    trans_cost = TRANSPORT_UNIT_PRICE * wh_trans_dist

    holding_cost = np.zeros(D)
    dev_categs = [''] * D
    for _, row in demand_df.iterrows():
        d = str(row['DEV_CODE']).strip()
        if d in d2i:
            p = prices.get(d, 0)
            holding_cost[d2i[d]] = p * ANNUAL_INTEREST_RATE
            dev_categs[d2i[d]] = str(row.get('DEV_CATEG', ''))

    # ---- 7. 校验 ----
    errors = []
    for name, df in [("年需求", demand_df), ("候选库房", warehouse_df),
                      ("供电所", station_df), ("距离矩阵", dist_df)]:
        if len(df) == 0:
            errors.append(f"{name}表查询结果为空")
    if S == 0:
        missing_detail = []
        no_demand = station_codes_all - demand_stations
        no_dist = station_codes_all - dist_stations
        if no_demand:
            missing_detail.append(f"{len(no_demand)}个缺年需求")
        if no_dist:
            missing_detail.append(f"{len(no_dist)}个缺距离")
        errors.append(f"三表交集供电所为0 ({'; '.join(missing_detail)})")
    if W == 0:
        errors.append("候选库房为0")
    if D == 0:
        errors.append("有效设备码为0（全部无单价或单价无效）")
    if errors:
        raise ValueError("[数据校验失败] " + "; ".join(errors))

    if demand_mat.sum() <= 0:
        logger.warning("[数据校验] 需求总量为0")
    if fixed_cost.sum() <= 0:
        logger.warning("[数据校验] 库房固定成本全为0")
    if (dist_mat.sum() >= 1e9 * W * S):
        logger.warning("[数据校验] 距离矩阵全部不可达，无可行解")
    orphan = [i for i in range(S) if dist_mat[:, i].min() >= 1e9]
    if orphan:
        logger.warning(f"[数据校验] {len(orphan)} 个供电所无可达库房: "
                       f"{[station_codes[i] for i in orphan[:5]]}")

    # ---- 8. 日志 ----
    reachable_ratio = (dist_mat < 1e9).mean()
    logger.info(f"[数据准备] 供电所 {S} 个 | 候选库房 {W} 个 | 有效设备码 {D} 个")
    logger.info(f"[数据准备] 需求总量 {demand_mat.sum():,.0f} 件 | 距离矩阵可达率 {reachable_ratio:.1%}")

    return {
        'station_codes': station_codes,
        'wh_codes': wh_codes,
        'dev_codes': dev_codes,
        'dev_categs': dev_categs,
        'demand': demand_mat,
        'distance': dist_mat,
        'fixed_cost': fixed_cost,
        'trans_cost': trans_cost,
        'holding_cost': holding_cost,
        's2i': s2i,
        'w2i': w2i,
        'd2i': d2i,
    }


def build_reachable_pairs(data: dict) -> List[Tuple[int, int]]:
    """提取可达的 (warehouse_idx, station_idx) 对。"""
    dist = data['distance']
    W, S = dist.shape
    pairs = [(j, i) for j in range(W) for i in range(S) if dist[j, i] < 1e9]
    logger.info(f"[数据准备] 可达对: {len(pairs)}/{W*S} ({len(pairs)/(W*S)*100:.1f}%)")
    return pairs


# ================================================================
# ε-约束 主循环
# ================================================================


def optimize_warehouse_layout(data: dict) -> List[dict]:
    """双目标 ε-约束法，返回帕累托前沿解集。

    流程:
      1. min Z₂ → D_min（距离下界）
      2. min Z₁ → D_max（距离上界）
      3. [D_min, D_max] 均匀 N 个阈值 → min Z₁ s.t. Z₂ ≤ D_n
      4. 去重排序
    """
    pairs = build_reachable_pairs(data)

    logger.info("=" * 60)
    logger.info("[ε-约束] Step 1/2: 求解距离最优...")
    sol_dist = solve_distance_milp(data, pairs)

    logger.info("[ε-约束] Step 2/2: 求解成本最优...")
    sol_cost = solve_cost_milp(data, pairs)

    D_min = sol_dist['Z2']
    D_max = sol_cost['Z2']
    C_min_dist = sol_dist['Z1']
    C_min = sol_cost['Z1']

    logger.info(f"[ε-约束] 极值: D ∈ [{D_min:.1f}, {D_max:.1f}] km | "
                f"C ∈ [{C_min:,.0f}, {C_min_dist:,.0f}] 元")

    solutions = [sol_dist, sol_cost]

    # 生成帕累托前沿
    if D_max > D_min + 0.1:
        for n in range(1, N_PARETO_SOLUTIONS - 1):
            d_bound = D_min + (D_max - D_min) * n / (N_PARETO_SOLUTIONS - 1)
            label = f'PARETO_{n:02d}'
            logger.info(f"[ε-约束] 约束求解 {n}/{N_PARETO_SOLUTIONS-2}: Z₂ ≤ {d_bound:.1f}km")
            try:
                sol = solve_constrained_milp(data, pairs, d_bound, label)
                if sol['status'] == 'Optimal':
                    solutions.append(sol)
                else:
                    logger.warning(f"[ε-约束] {label}: {sol['status']}, 跳过")
            except Exception as e:
                logger.error(f"[ε-约束] {label}: 求解异常: {e}")

    # 去重排序
    solutions.sort(key=lambda s: s['Z2'])
    deduped = []
    seen = set()
    for s in solutions:
        key = round(s['Z2'], 1)
        if key not in seen:
            seen.add(key)
            deduped.append(s)

    logger.info(f"[ε-约束] 完成! 帕累托前沿 {len(deduped)} 组解 "
                f"(已分配供电所 {len(data['station_codes'])} 个):")
    for s in deduped:
        logger.info(f"  {s.get('label', '?'):<12} Z₁={s['Z1']:>12,.0f} 元  "
                    f"Z₂={s['Z2']:>6.1f} km  库房 {s['n_opened']:>3} 个")

    return deduped
