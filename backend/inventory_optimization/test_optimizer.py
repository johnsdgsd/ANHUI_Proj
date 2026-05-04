import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from backend.inventory_optimization.optimizer import InventoryOptimizer
from backend.api.data_api.fetch_data import query_device_install_data_by_month_range,query_aps_inventory_init_stock_by_month
from backend.api.data_api.fetch_data import query_aps_inventory_item_cost

if __name__ == '__main__':
    # demand_data_file = os.path.join(data_dir, "处理后数据.xlsx")
    
    print("=== 获取安装量数据 ===")
    init_stock = query_aps_inventory_init_stock_by_month(202605)
    install_df = query_device_install_data_by_month_range(202204,202604)
    item_cost = query_aps_inventory_item_cost()
    print(f"获取到 {len(install_df)} 条安装量数据")
    
    print("=== 创建库存优化器 ===")
    optimizer = InventoryOptimizer(init_stock)
    optimizer.get_distributions_from_install_data(install_df)
    optimizer.set_local_warehouses_from_dataframe()
    optimizer.set_item_costs_from_dataframe(item_cost)
    optimizer.set_central_warehouse('合肥供电公司')
    alpha = optimizer.generate_alpha_dict()
    optimizer.set_alpha(alpha)
    optimizer.simulate(202601,202612)
    print()
    optimizer.optimize_alpha(10,200)
    print()

    # optimizer.set_local_warehouses_from_dataframe(demand_data_file)
    # optimizer.set_item_costs_from_dataframe(dev_cost_file)
    # optimizer.optimize_alpha(100,200)
