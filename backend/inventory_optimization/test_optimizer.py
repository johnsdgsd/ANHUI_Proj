import os
import sys
from backend.inventory_optimization.optimizer import InventoryOptimizer
from backend.api.data_api.fetch_data import *
from backend.inventory_optimization.RunOptimize import run_optimization_from_api
from datetime import datetime


if __name__ == '__main__':
    # demand_data_file = os.path.join(data_dir, "处理后数据.xlsx")
    
    # print("=== 获取安装量数据 ===")
    # init_stock = query_aps_inventory_init_stock_by_month(202605)
    # install_df = query_device_install_data_by_month_range(202204,202604)
    # item_cost = query_aps_inventory_item_cost()
    # print(f"获取到 {len(install_df)} 条安装量数据")
    #
    # print("=== 创建库存优化器 ===")
    # optimizer = InventoryOptimizer(init_stock)
    # optimizer.get_distributions_from_install_data(install_df)
    # optimizer.set_local_warehouses_from_dataframe()
    # optimizer.set_item_costs_from_dataframe(item_cost)
    # optimizer.set_central_warehouse('合肥供电公司')
    # # alpha = optimizer.generate_alpha_dict()
    # # optimizer.set_alpha(alpha)
    # # optimizer.simulate(202601,202612)
    # print()
    # optimizer.optimize_alpha(2,200,0.95,n_processor=10)
    # print()

    # optimizer.set_local_warehouses_from_dataframe(demand_data_file)
    # optimizer.set_item_costs_from_dataframe(dev_cost_file)
    # optimizer.optimize_alpha(100,200)
    # tag = datetime.now().strftime('%Y%m%d%H%M%S')
    # #不能使用多进程调试
    # run_optimization_from_api(202605,202204,202604,tag,n_iter=1,pop_size=10,n_processor=1)
    df = query_adam_dist_scheme_by_date_range('2026-05-01','2026-05-31')
    print(df)
    ids = [10001,10002]
    dfs = []
    for id in ids :
        df = query_adam_dist_scheme_det_by_distschemeid(id)
        dfs.append(df)
    result_df = pd.concat(dfs, ignore_index=True)
    print(result_df)