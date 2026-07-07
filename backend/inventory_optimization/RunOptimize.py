"""
库存优化运行脚本
封装完整的库存优化流程
"""

import pandas as pd
from datetime import datetime
from backend.inventory_optimization.optimizer import InventoryOptimizer
from backend.inventory_optimization.warehouse import CentralWarehouse


def get_device_install_data(year:str):
    """
    获取某一年所有月份所有单位设备码的月度预测信息
    """
    from backend.api.data_api.fetch_data import (
        query_adam_yqm_dmd_pre_by_year,
        query_adam_spec_code_config,query_adam_del_site_conf)
    
    # 获取基础数据
    df = query_adam_yqm_dmd_pre_by_year(year)
    stat = '01' #暂时获取初始状态的数据
    pre_type = '03' #获取月度预测信息
    col = 'PRE_NUM'

    # 筛选数据：STAT列为01，PRE_TYPE列为03
    df = df[(df['STAT'] == stat) & (df['PRE_TYPE'] == pre_type)]
    # 提取指定列
    df = df[['PRE_YEAR', 'PRE_MONTH', 'ORG_NO', 'DEV_CODE', col]]
    
    # 按年、月、单位编码、设备码分组，汇总所有业务类型的数量
    df = df.groupby(
        ['PRE_YEAR', 'PRE_MONTH', 'ORG_NO', 'DEV_CODE'],
        as_index=False
    )[col].sum()
    
    # 获取设备类型配置
    spec_df = query_adam_spec_code_config()
    spec_dict = spec_df.set_index('DEV_CODE')['DEV_TYPE'].to_dict()
    
    # 获取站点信息
    site_df = query_adam_del_site_conf()
    site_dict = site_df.set_index('ORG_NO')['ORG_NAME'].to_dict()
    
    # 转换为目标格式
    result_df = df.copy()
    result_df['INSTALL_ID'] = range(1, len(result_df) + 1)
    result_df['STAT_MONTH'] = result_df['PRE_YEAR'] + result_df['PRE_MONTH']
    result_df['UNIT_CODE'] = result_df['ORG_NO']
    result_df['UNIT_NAME'] = result_df['ORG_NO'].map(site_dict)
    result_df['DEVICE_TYPE'] = result_df['DEV_CODE'].map(spec_dict)
    result_df['DEVICE_CODE'] = result_df['DEV_CODE']
    result_df['INSTALL_NUM'] = result_df[col]
    
    # 保留目标列
    result_df = result_df[['INSTALL_ID', 'STAT_MONTH', 'UNIT_CODE', 'UNIT_NAME', 'DEVICE_TYPE', 'DEVICE_CODE', 'INSTALL_NUM']]
    
    # 填充缺失值
    result_df['UNIT_NAME'] = result_df['UNIT_NAME'].fillna('未知单位')
    result_df['DEVICE_TYPE'] = result_df['DEVICE_TYPE'].fillna('未知设备类型')
    # 重置索引
    result_df = result_df.reset_index(drop=True)
    
    return result_df

  

def _round_order_qty(qty: int, dev_cls: str, dev_categ: str) -> int:
    """按设备类别/品类取整补库数量（与分析版 GetMonthlyOrder 一致）"""
    if qty == 0:
        return 0
    dev_cls = str(dev_cls).replace('.0', '').strip().zfill(2)
    if dev_cls == '02':
        return ((qty + 35) // 36) * 36
    elif dev_cls == '09':
        return ((qty + 19) // 20) * 20
    elif dev_cls == '01':
        return ((qty + 59) // 60) * 60 if str(dev_categ) == '01_01' else ((qty + 19) // 20) * 20
    return qty


def run_optimization_from_api(
    init_stock_month: int,
    tag: str,
    n_iter: int = 10,
    pop_size: int = 200,
    epsilon: float = 0.95,
    n_processor: int = 10,
    verbose: bool = False
):
    """使用遗传算法优化月度库存阈值与补货量（单月版本）

    入参与 GenerateMonthlyThresholdAndOrder 对齐：
        init_stock_month: 目标月份 (YYYYMM)
        tag:              全局方案标识
        n_iter:           遗传算法迭代次数
        pop_size:         种群大小
        epsilon:          目标满足率下限
        n_processor:      并行处理器数量

    Returns:
        (InventoryThreshold, InventoryOrder)
        列名与分析版 GenerateMonthlyThresholdAndOrder 完全一致。
    """
    from types import SimpleNamespace
    from backend.api.data_api.fetch_data import (
        query_adam_org_stock_sample_estimated,
        query_adam_yqm_dmd_pre_by_year_month,
        query_adam_pre_range_info,
        query_adam_spec_code_config,
    )
    from backend.inventory_optimization.demand_distribution import PoissonDistribution
    from backend.inventory_optimization.item import Item as GaItem
    from backend.inventory_optimization.warehouse_initializer import LocalWarehouseInitializer

    try:
        # ---- 1. 解析日期 ----
        init_stock_month_str = str(init_stock_month)
        year = init_stock_month_str[:4]
        month = init_stock_month_str[4:6]
        target_month = int(month)

        # ---- 2. 加载数据 ----
        init_stock = query_adam_org_stock_sample_estimated(init_stock_month_str)
        print(f'推算月初库存成功，数据量{len(init_stock)}条', flush=True)

        demand_df = query_adam_yqm_dmd_pre_by_year_month(year, month)
        print(f'获取{year}年{month}月需求预测数据成功，数据量{len(demand_df)}条', flush=True)

        item_cost = query_adam_pre_range_info()
        # 仅保留初始库存中存在的物资价格
        valid_dev_codes = init_stock['DEV_CODE'].unique()
        item_cost = item_cost[item_cost['DEV_CODE'].isin(valid_dev_codes)]
        print(f'物资价格匹配: {len(item_cost)}/{len(valid_dev_codes)} 种设备码', flush=True)
        spec_df = query_adam_spec_code_config()
        spec_dev_dict = spec_df.set_index('DEV_CODE')[['DEV_CLS', 'DEV_CATEG']].to_dict('index')

        # 成本映射
        cost_df = item_cost[['DEV_CODE', 'TAX_UP']].copy()
        cost_df['holding_cost'] = (cost_df['TAX_UP'] * 0.1).round(1)
        cost_df['shortage_cost'] = (cost_df['TAX_UP'] * 0.5).round(1)
        cost_dict = cost_df.set_index('DEV_CODE')[['holding_cost', 'shortage_cost']].to_dict('index')

        # ---- 3. 构建仓库与物资 ----
        LWI = LocalWarehouseInitializer()
        LWI.load_city_mapping(init_stock)
        local_warehouses = LWI.initialize_warehouses(init_stock)
        warehouse_dict = {w.city_code: w for w in local_warehouses}

        # 跟踪每个仓库是否有物资
        wh_has_items = {w.city_code: False for w in local_warehouses}

        for _, row in demand_df.iterrows():
            org_no = str(row['ORG_NO'])
            dev_code = str(row['DEV_CODE'])
            monthly_demand = float(row['PRE_NUM'])

            wh = warehouse_dict.get(org_no)
            if not wh:
                continue
            wh_has_items[org_no] = True

            # 初始库存
            mask = ((init_stock['ORG_NO'].astype(str) == org_no) &
                    (init_stock['DEV_CODE'].astype(str) == dev_code))
            init_stock_val = float(init_stock.loc[mask, 'STOCK_NUM'].sum()) if mask.any() else 0.0

            # 成本
            costs = cost_dict.get(dev_code, {})
            holding_cost = float(costs.get('holding_cost', 0.0))
            shortage_cost = float(costs.get('shortage_cost', 0.0))

            # 设备类别
            dev_info = spec_dev_dict.get(dev_code, {})
            dev_cls = str(dev_info.get('DEV_CLS', '00')).replace('.0', '').strip().zfill(2)

            # 创建物资
            item = GaItem(
                cls=dev_cls,
                dev_code=dev_code,
                initial_inventory=init_stock_val,
                holding_cost=holding_cost,
                shortage_cost=shortage_cost,
                alpha=0.95
            )

            # 设置单月需求分布 (tn=0.5 → rate=1.5，与分析版一致)
            distribution = PoissonDistribution(lambda_=monthly_demand, T=1, tn=0.5)
            item.set_demand_distribution(target_month, distribution)

            wh.add_item(dev_code, item)

        # 移除没有任何物资的空仓库（避免 GA 维度膨胀）
        local_warehouses = [w for w in local_warehouses if wh_has_items.get(w.city_code, False)]
        if not local_warehouses:
            raise ValueError("没有任何仓库存在物资！请检查需求预测数据与月初库存数据是否匹配。")
        print(f'有效仓库数: {len(local_warehouses)}', flush=True)

        # ---- 4. 构建 GA 上下文 ----
        context = SimpleNamespace()
        context.local_warehouses = local_warehouses
        context.central_warehouse = CentralWarehouse()  # 空中心库（单期不需要）

        target_ym = int(f"{year}{month}")

        # 创建优化器并挂载上下文
        optimizer = InventoryOptimizer(init_stock)
        optimizer.local_warehouses = local_warehouses
        optimizer.central_warehouse = context.central_warehouse
        optimizer.context = context

        # ---- 5. 遗传算法寻优 ----
        print(f'开始遗传算法寻优: n_iter={n_iter}, pop_size={pop_size}, epsilon={epsilon}, target_ym={target_ym}')
        best_solution, best_cost = optimizer.optimize_alpha(
            n_iter=n_iter,
            pop_size=pop_size,
            epsilon=epsilon,
            n_processor=n_processor,
            target_ym=target_ym,
            end_ym=target_ym,
            verbose=verbose
        )
        print(f'GA 完成: best_cost={best_cost:.2f}, best_alpha={best_solution}', flush=True)

        # 应用最优 alpha
        alpha_dict = InventoryOptimizer._build_alpha_dict(best_solution, optimizer.context)
        InventoryOptimizer.set_alpha(alpha_dict, optimizer.context)

        # ---- 6. 构建输出 ----
        pretime = datetime.now().strftime('%Y-%m-%d')
        stock_id = 1
        threshold_rows = []
        order_rows = []

        for wh in local_warehouses:
            for item_key, item in wh.items.items():
                demand = item.generate_demand_quantile(target_month)
                demand = round(demand)
                order = max(0, demand - item.initial_inventory)

                dev_info = spec_dev_dict.get(item.dev_code, {})
                dev_cls = str(dev_info.get('DEV_CLS', '00')).replace('.0', '').strip().zfill(2)
                dev_categ = str(dev_info.get('DEV_CATEG', '00_00'))

                order = _round_order_qty(int(order), dev_cls, dev_categ)

                threshold_rows.append({
                    'STOCK_MONTH_LIMIT_PRE_ID': stock_id,
                    'PRE_YEAR': year,
                    'PRE_MONTH': month,
                    'ORG_NO': wh.city_code,
                    'DEV_CLS': dev_cls,
                    'DEV_CATEG': dev_categ,
                    'DEV_CODE': item.dev_code,
                    'BASE_LIMIT': int(demand),
                    'PRE_TIME': pretime,
                    'GLOBAL_SCHEME_ID': int(tag)
                })

                order_rows.append({
                    'PLAN_MONTH_IAS_PRE_ID': stock_id,
                    'PRE_YEAR': year,
                    'PRE_MONTH': month,
                    'REC_ORG_NO': wh.city_code,
                    'DEV_CLS': dev_cls,
                    'DEV_CATEG': dev_categ,
                    'DEV_CODE': item.dev_code,
                    'PLAN_IAS_NUM': int(order),
                    'GLOBAL_SCHEME_ID': int(tag)
                })

                stock_id += 1

        InventoryThreshold = pd.DataFrame(threshold_rows)
        InventoryOrder = pd.DataFrame(order_rows)

        total_order = int(InventoryOrder['PLAN_IAS_NUM'].sum()) if not InventoryOrder.empty else 0
        total_threshold = int(InventoryThreshold['BASE_LIMIT'].sum()) if not InventoryThreshold.empty else 0
        total_init_stock = sum(
            item.initial_inventory for wh in local_warehouses for item in wh.items.values()
        )
        print(f'[GA] 总阈值={total_threshold}, 总补货量={total_order}, 总初始库存={total_init_stock:.0f}', flush=True)

        print(f'生成阈值数据{len(InventoryThreshold)}条，补货量数据{len(InventoryOrder)}条', flush=True)
        return InventoryThreshold, InventoryOrder

    except Exception as e:
        raise
