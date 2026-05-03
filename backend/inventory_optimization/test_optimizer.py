import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from backend.inventory_optimization.optimizer import InventoryOptimizer
from backend.api.data_api.fetch_data import query_device_install_data_by_month_range


if __name__ == '__main__':
    # demand_data_file = os.path.join(data_dir, "处理后数据.xlsx")
    
    print("=== 获取安装量数据 ===")
    install_df = query_device_install_data_by_month_range(202204,202604)
    print(f"获取到 {len(install_df)} 条安装量数据")
    
    print("=== 创建库存优化器 ===")
    optimizer = InventoryOptimizer(city_mapping_file, demand_data_file)
    optimizer.set_local_warehouses_from_dataframe(demand_data_file)
    optimizer.set_item_costs_from_dataframe(dev_cost_file)
    optimizer.optimize_alpha(100,200)
