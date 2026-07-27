"""
GA 适配器

封装 backend.inventory_optimization 的 GA 优化流水线，
为单个月份计算最优 alpha（按 DEV_CLS 分组，每个类别独立 alpha）。

核心流程（复用 RunOptimize.run_optimization_from_api 的逻辑）:
    1. 用 LocalWarehouseInitializer 从 init_stock 构建仓库
    2. 对每个 Item 设置 PoissonDistribution(lambda=月需求, T=1, tn=0.5)
    3. InventoryOptimizer.optimize_alpha() → best_solution
    4. 解码 → {org_no: {dev_cls: alpha}}
"""

import logging
import os
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd

# 确保 backend 可导入
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJ_DIR = os.path.dirname(os.path.dirname(os.path.dirname(_BASE_DIR)))
_BACKEND_DIR = os.path.join(_PROJ_DIR, 'backend')
sys.path.insert(0, _BACKEND_DIR)

from backend.inventory_optimization.optimizer import InventoryOptimizer
from backend.inventory_optimization.warehouse_initializer import LocalWarehouseInitializer
from backend.inventory_optimization.item import Item as GaItem
from backend.inventory_optimization.demand_distribution import PoissonDistribution
from backend.inventory_optimization.warehouse import LocalWarehouse, CentralWarehouse

from data_cleaning.simulation.config import (
    GA_EPSILON, GA_N_ITER, GA_POP_SIZE, GA_N_PROCESSOR,
    HOLDING_COST_RATE, SHORTAGE_COST_RATE,
    CATEGORIES, CATEGORY_TO_DEVCODE, DEVCODE_TO_CATEGORY,
)


def run_ga_one_month(init_stock_df, demand_df, spec_df, cost_df,
                     target_month, epsilon=GA_EPSILON,
                     n_iter=GA_N_ITER, pop_size=GA_POP_SIZE,
                     n_processor=GA_N_PROCESSOR):
    """
    对单个月份运行 GA，返回最优 alpha。

    Args:
        init_stock_df: DataFrame[ORG_NO, DEV_CODE, STOCK_NUM]
            月初库存（87县 × 6设备码 = 522行）
        demand_df: DataFrame[ORG_NO, DEV_CODE, LAMBDA_0102, DIRECT_03]
            当月需求（仅 LAMBDA_0102 用作 Poisson λ，DIRECT_03 在仿真阶段加）
        spec_df: DataFrame[DEV_CODE, DEV_CLS, DEV_CATEG, PACK_BOX_NUM]
            设备规格（DEV_CLS 已覆写为类别独立值）
        cost_df: DataFrame[DEV_CODE, TAX_UP]
            设备单价
        target_month: int, 目标月份 (202601 ~ 202606)
        epsilon: float, 目标满足率下限
        n_iter: int, GA 代数
        pop_size: int, 种群大小
        n_processor: int, 并行进程数

    Returns:
        tuple: (alpha_dict, best_cost)
            alpha_dict: {org_no: {dev_cls: alpha}}
            best_cost: float, 最优成本
    """
    ym = target_month
    month_num = ym % 100  # 提取月份编号 (1-12)
    logging.info(f"[GA] ======== 开始 GA 优化 {ym} (月份{month_num}) ========")

    # ---- 1. 准备输入 DataFrame ----
    # init_stock 需要 ORG_NAME 列（warehouse_initializer 要求）
    stock_df = init_stock_df.copy()
    if 'ORG_NAME' not in stock_df.columns:
        stock_df['ORG_NAME'] = stock_df['ORG_NO'].apply(lambda x: f'单位_{x}')

    # 确保 STOCK_NUM > 0
    stock_df = stock_df[stock_df['STOCK_NUM'] > 0].copy()
    if stock_df.empty:
        logging.warning(f"[GA] {ym} 无有效库存，跳过 GA")
        return {}, 0.0

    # ---- 2. 构建 spec_dev_dict ----
    spec_dev_dict = {}
    for _, row in spec_df.iterrows():
        spec_dev_dict[str(row['DEV_CODE']).strip()] = row.to_dict()

    # ---- 3. 构建 cost_dev_dict ----
    cost_dev_dict = {}
    for _, row in cost_df.iterrows():
        cost_dev_dict[str(row['DEV_CODE']).strip()] = float(row['TAX_UP'])

    # ---- 4. 构建 demand_dev_dict ----
    demand_dev_dict = {}
    for _, row in demand_df.iterrows():
        dev = str(row['DEV_CODE']).strip()
        org = str(row['ORG_NO']).strip()
        lam = int(row.get('LAMBDA_0102', 0))
        demand_dev_dict[(org, dev)] = lam

    # ---- 5. 构建仓库 ----
    initializer = LocalWarehouseInitializer()
    initializer.load_city_mapping(stock_df)
    local_warehouses = initializer.initialize_warehouses(stock_df)

    # 构建仓库→物资映射
    wh_map = {w.city_code: w for w in local_warehouses}
    wh_has_items = {w.city_code: False for w in local_warehouses}

    item_count = 0
    demand_missing = 0
    cost_missing = 0

    for _, row in stock_df.iterrows():
        org_no = str(row['ORG_NO']).strip()
        dev_code = str(row['DEV_CODE']).strip()
        init_stock_val = float(row['STOCK_NUM'])

        wh = wh_map.get(org_no)
        if wh is None:
            continue

        # 设备类别
        dev_info = spec_dev_dict.get(dev_code, {})
        dev_cls = str(dev_info.get('DEV_CLS', '00')).replace('.0', '').strip().zfill(2)

        # 需求
        monthly_demand = demand_dev_dict.get((org_no, dev_code), 0)
        if monthly_demand <= 0:
            demand_missing += 1
            # 无需求 → 不需要补货，跳过该物资
            continue

        # 成本
        unit_price = cost_dev_dict.get(dev_code, 0)
        if unit_price <= 0:
            cost_missing += 1
            unit_price = 1.0  # fallback

        holding_cost = unit_price * HOLDING_COST_RATE
        shortage_cost = unit_price * SHORTAGE_COST_RATE

        # 创建物资
        item = GaItem(
            cls=dev_cls,
            dev_code=dev_code,
            initial_inventory=init_stock_val,
            holding_cost=holding_cost,
            shortage_cost=shortage_cost,
            alpha=0.95
        )

        # 设置 Poisson 分布 (T=1, tn=0.5 → rate=1.5)
        # 注意: key 是月份编号 (1-12)，不是 year_month (202601)
        distribution = PoissonDistribution(lambda_=monthly_demand, T=1, tn=0.5)
        item.set_demand_distribution(month_num, distribution)

        wh.add_item(dev_code, item)
        wh_has_items[org_no] = True
        item_count += 1

    # 移除空仓库
    local_warehouses = [w for w in local_warehouses if wh_has_items.get(w.city_code, False)]
    if not local_warehouses:
        logging.warning(f"[GA] {ym} 没有任何仓库存在物资，跳过 GA")
        return {}, 0.0

    # 统计
    n_wh = len(local_warehouses)
    categories_set = set()
    for wh in local_warehouses:
        for item in wh.items.values():
            categories_set.add(item.cls)
    categories = sorted(categories_set)

    logging.info(
        f"[GA] {ym}: {n_wh}仓库 × {len(categories)}类别, "
        f"{item_count}个物资, 缺需求{demand_missing}个, 缺成本{cost_missing}个, "
        f"month_key={month_num}"
    )

    # ---- 6. 构建 CentralWarehouse（optimizer 需要，按实际构造函数） ----
    central_wh = CentralWarehouse()
    central_wh.warehouse_id = "central"
    central_wh.city_code = "34101"
    central_wh.city_name = "省级中心库"

    # ---- 7. 构建 context ----
    context = SimpleNamespace(
        local_warehouses=local_warehouses,
        central_warehouse=central_wh
    )

    # ---- 8. 创建优化器并运行 GA ----
    optimizer = InventoryOptimizer(stock_df)
    optimizer.local_warehouses = local_warehouses
    optimizer.central_warehouse = central_wh
    optimizer.context = context

    best_solution, best_cost = optimizer.optimize_alpha(
        n_iter=n_iter,
        pop_size=pop_size,
        epsilon=epsilon,
        n_processor=n_processor,
        target_ym=ym,
        end_ym=ym,
        verbose=False
    )

    # ---- 9. 解码 alpha ----
    alpha_dict = InventoryOptimizer._build_alpha_dict(best_solution, optimizer.context)

    # 打印 alpha 分布
    logging.info(f"[GA] {ym} 完成: best_cost={best_cost:.2f}, alpha 分布:")
    for wh_code in sorted(alpha_dict.keys())[:5]:  # 前5个仓库
        for cls_name, alpha_val in sorted(alpha_dict[wh_code].items()):
            logging.info(f"  {wh_code}/{cls_name}: α={alpha_val:.4f}")
    if len(alpha_dict) > 5:
        logging.info(f"  ... 共 {len(alpha_dict)} 个仓库")

    return alpha_dict, best_cost


def apply_alpha_to_ppf(alpha_dict, org_no, dev_cls, lam, default_alpha=0.95):
    """
    用 GA 优化出的 alpha 计算 Poisson 分位数。

    Args:
        alpha_dict: {org_no: {dev_cls: alpha}}
        org_no: 仓库编码
        dev_cls: 设备类别编码
        lam: Poisson lambda
        default_alpha: alpha 查找失败时的默认值

    Returns:
        float: Poisson_ppf(alpha, λ × 1.5)
    """
    from scipy.stats import poisson

    alpha = alpha_dict.get(str(org_no).strip(), {}).get(dev_cls, default_alpha)
    if lam <= 0:
        return 0.0
    quantile = poisson.ppf(alpha, lam * 1.5)  # rate=1.5 与 Item 保持一致
    return float(np.ceil(quantile))


def apply_alpha_to_normal(alpha_dict, org_no, dev_cls, lam, sigma, default_alpha=0.95):
    """
    用 GA 优化出的 alpha 计算正态分布基准库存（v4 新公式）。

    S = 1.5 × λ + z_α × 1.5 × σ

    其中:
        λ = 预测01+02 + 真实03
        σ = 6 个月残差（预测01+02 − 真实01+02）的标准差，不区分月份
        z_α = norm.ppf(α)

    Args:
        alpha_dict: {org_no: {dev_cls: alpha}}
        org_no: 仓库编码
        dev_cls: 设备类别编码
        lam: 月度总需求 λ
        sigma: 残差标准差（6 个月，所有月份共用）
        default_alpha: alpha 查找失败时的默认值

    Returns:
        float: 基准库存 S = ceil(1.5 × λ + z × 1.5 × σ)
    """
    from scipy.stats import norm

    alpha = alpha_dict.get(str(org_no).strip(), {}).get(dev_cls, default_alpha)
    if lam <= 0:
        return 0.0
    z = norm.ppf(alpha)
    sigma = sigma if sigma > 0 else 0.0
    S = 1.5 * lam + z * 1.5 * sigma
    return float(np.ceil(max(0.0, S)))
