"""
库存优化运行脚本
封装完整的库存优化流程
"""

import pandas as pd
from backend.inventory_optimization.optimizer import InventoryOptimizer
from backend.inventory_optimization.warehouse import LocalWarehouse


def run_optimization_from_api(
    init_stock_month: int,
    install_start_month: int,
    install_end_month: int,
    tag:str,
    central_warehouse_name: str = '合肥供电公司',
    n_iter: int = 1,
    pop_size: int = 200,
    epsilon: float = 0.95,
    n_processor: int = 10
):
    """从API数据源运行库存优化
    
    从数据API获取数据后运行完整的库存优化流程
    
    Args:
        init_stock_month: 初始库存月份 (YYYYMM)
        install_start_month: 安装量数据起始月份 (YYYYMM)
        install_end_month: 安装量数据结束月份 (YYYYMM)
        central_warehouse_name: 中心库名称
        n_iter: 遗传算法迭代次数
        pop_size: 种群大小
        epsilon: 目标满足率
        n_processor: 并行处理器数量
        
    Returns:
        dict: 优化结果
    """
    # 在函数内部导入，避免循环导入
    from backend.api.data_api.fetch_data import (
        query_aps_inventory_init_stock_by_month,
        query_device_install_data_by_month_range,
        query_aps_inventory_item_cost,
        insert_into_aps_inventory_fulfill_rate,
        insert_into_aps_inventory_replenish,
        insert_into_aps_inventory_replenish_qty
    )
    
    try:
        # 获取数据
        init_stock = query_aps_inventory_init_stock_by_month(init_stock_month)
        install_df = query_device_install_data_by_month_range(install_start_month, install_end_month)
        item_cost = query_aps_inventory_item_cost()
        
        # 创建库存优化器
        optimizer = InventoryOptimizer(init_stock)
        
        # 获取需求分布
        optimizer.get_distributions_from_install_data(install_df)
        
        # 初始化地方库
        optimizer.set_local_warehouses_from_dataframe()
        
        # 设置物资成本
        optimizer.set_item_costs_from_dataframe(item_cost)
        
        # 设置中心库
        optimizer.set_central_warehouse(central_warehouse_name)
        
        # 运行优化
        best_solution, best_cost = optimizer.optimize_alpha(
            n_iter=n_iter,
            pop_size=pop_size,
            epsilon=epsilon,
            n_processor=n_processor
        )

        alpha_dict = optimizer._build_alpha_dict(best_solution,optimizer.context)
        optimizer.set_alpha(alpha_dict,optimizer.context)

        # 构建满足率结果DataFrame
        result_data = []
        for warehouse in optimizer.context.local_warehouses:
            for item_key, item in warehouse.items.items():
                result_data.append({
                    'STAT_MONTH': init_stock_month,
                    'UNIT_CODE': warehouse.city_code,
                    'UNIT_NAME': warehouse.city_name,
                    'DEVICE_TYPE': item.cls,
                    'DEVICE_CODE': item.dev_code,
                    'TAG': tag,
                    'FULFILL_RATE': round(item.alpha, 4)
                })
        alpha_result_df = pd.DataFrame(result_data)
        insert_into_aps_inventory_fulfill_rate(alpha_result_df)
        #构建库存阈值上限结果以及补货量结果
        month_id = init_stock_month % 100
        demand_result_data = []
        order_result_data = []
        for local_warehouse in optimizer.context.local_warehouses:
            for item_key, item in local_warehouse.items.items():
                demand = item.generate_demand_quantile(month_id)
                order = max(0,demand - item.initial_inventory)
                demand_result_data.append(
                    {
                        'STAT_MONTH':init_stock_month,
                        'UNIT_CODE':local_warehouse.city_code,
                        'UNIT_NAME':local_warehouse.city_name,
                        'DEVICE_TYPE':item.cls,
                        'DEVICE_CODE':item.dev_code,
                        'TAG':tag,
                        'BASE_STOCK_NUM':int(demand)
                    }
                )
                order_result_data.append(
                    {
                        'STAT_MONTH':init_stock_month,
                        'UNIT_CODE':local_warehouse.city_code,
                        'UNIT_NAME':local_warehouse.city_name,
                        'DEVICE_TYPE':item.cls,
                        'DEVICE_CODE':item.dev_code,
                        'TAG':tag,
                        'REPLENISH_NUM':order
                    }
                )
        
        InventoryThreshold = pd.DataFrame(demand_result_data)
        insert_into_aps_inventory_replenish(InventoryThreshold)
        InventoryOrder = pd.DataFrame(order_result_data)
        insert_into_aps_inventory_replenish_qty(InventoryOrder)


   
    except Exception as e:
        raise
