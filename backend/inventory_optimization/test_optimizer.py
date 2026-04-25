import os
from sre_parse import OP_IGNORE
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from backend.inventory_optimization.optimizer import InventoryOptimizer


if __name__ == '__main__':
    data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")
    
    city_mapping_file = os.path.join(data_dir, "地市映射表.xlsx")
    dev_cost_file = os.path.join(data_dir, "物资价格.xlsx")
    demand_data_file = os.path.join(data_dir, "处理后数据.xlsx")#需求分布数据
    
    print("=== 创建库存优化器 ===")
    optimizer = InventoryOptimizer(city_mapping_file, demand_data_file)
    optimizer.set_local_warehouses_from_dataframe(demand_data_file)
    optimizer.set_item_costs_from_dataframe(dev_cost_file)
    alpha_dict = optimizer.generate_alpha_dict()
    optimizer.set_alpha(alpha_dict)
    optimizer.simulate(202701,202712)
    print()