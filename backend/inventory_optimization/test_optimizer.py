import os
import sys
from numpy import rec

from backend.inventory_optimization.optimizer import InventoryOptimizer
from backend.inventory_optimization.warehouse import CentralWarehouse
from backend.api.data_api.fetch_data import *
from backend.inventory_optimization.RunOptimize import run_optimization_from_api
from datetime import datetime
from backend.inventory_optimization import DailyReplenishmentPlan
import ast

def f1():
    print()
    # demand_data_file = os.path.join(data_dir, "处理后数据.xlsx")
    print("=== 获取初始库存数据 ===")
    init_stock = query_aps_inventory_init_stock_by_month(202605)
    print(f"获取到 {len(init_stock)} 条初始库存数据")

    print("=== 获取安装量数据 ===")
    install_df = query_device_install_data_by_month_range(202204, 202604)
    item_cost = query_aps_inventory_item_cost()
    print(f"获取到 {len(install_df)} 条安装量数据")
    # #
    # print("=== 创建库存优化器 ===")
    optimizer = InventoryOptimizer(init_stock)
    optimizer.get_distributions_from_install_data(install_df)
    optimizer.set_local_warehouses_from_dataframe()
    print()
    # res = []
    # month = 5
    # for lw in optimizer.local_warehouses:
    #     for item_key,item in lw.items.items():
    #         alpha,_ = item.calculate_initial_fill_rate(month)
    #         record = {
    #             "month":5,
    #             "ORG_CODE":lw.city_code,
    #             "ORG_NAME":lw.city_name,
    #             "DEV_CLS":item.cls,
    #             "DEV_CODE":item.dev_code,
    #             "INIT_STOCK_NUM":item.initial_inventory,
    #             "Alpha":round(alpha,8),
    #         }
    #         res.append(record)
    #
    # Alpha_df = pd.DataFrame(res)
    # Alpha_df.to_excel("初始满足率.xlsx",index=False)

    tag = datetime.now().strftime("%Y%m%d%H%M%S")

    optimizer.central_warehouse = CentralWarehouse()
    optimizer.central_warehouse.city_name = '中心库'
    qua_sto = query_aps_qua_sto_by_month(202605)
    unqua_sto = query_aps_unqua_sto_by_month(202605)
    optimizer.central_warehouse.initialize_from_sto_data(qua_sto, unqua_sto)
    optimizer.central_warehouse.update_items_from_local_warehouses(optimizer.local_warehouses)
    optimizer.set_item_costs_from_dataframe(item_cost)
    # 运行优化
    best_solution, best_cost = optimizer.optimize_alpha(
        n_iter=1,
        pop_size=10,
        epsilon=0.99,
        n_processor=10
    )

    alpha_dict = optimizer._build_alpha_dict(best_solution, optimizer.context)
    optimizer.set_alpha(alpha_dict, optimizer.context)

    # 构建满足率结果DataFrame
    result_data = []
    for warehouse in optimizer.context.local_warehouses:
        for item_key, item in warehouse.items.items():
            result_data.append({
                'STAT_MONTH': 202605,
                'UNIT_CODE': warehouse.city_code,
                'UNIT_NAME': warehouse.city_name,
                'DEVICE_TYPE': item.cls,
                'DEVICE_CODE': item.dev_code,
                'TAG': tag,
                'FULFILL_RATE': round(item.alpha, 4)
            })
    alpha_result_df = pd.DataFrame(result_data)
    insert_into_aps_inventory_fulfill_rate(alpha_result_df)
    # 构建库存阈值上限结果以及补货量结果
    month_id = 202605 % 100
    demand_result_data = []
    order_result_data = []
    for local_warehouse in optimizer.context.local_warehouses:
        for item_key, item in local_warehouse.items.items():
            demand = item.generate_demand_quantile(month_id)
            order = max(0, demand - item.initial_inventory)
            demand_result_data.append(
                {
                    'STAT_MONTH': 202605,
                    'UNIT_CODE': local_warehouse.city_code,
                    'UNIT_NAME': local_warehouse.city_name,
                    'DEVICE_TYPE': item.cls,
                    'DEVICE_CODE': item.dev_code,
                    'TAG': tag,
                    'BASE_STOCK_NUM': int(demand)
                }
            )
            order_result_data.append(
                {
                    'STAT_MONTH': 202605,
                    'UNIT_CODE': local_warehouse.city_code,
                    'UNIT_NAME': local_warehouse.city_name,
                    'DEVICE_TYPE': item.cls,
                    'DEVICE_CODE': item.dev_code,
                    'TAG': tag,
                    'REPLENISH_NUM': order
                }
            )

    InventoryThreshold = pd.DataFrame(demand_result_data)
    insert_into_aps_inventory_replenish(InventoryThreshold)
    InventoryOrder = pd.DataFrame(order_result_data)
    insert_into_aps_inventory_replenish_qty(InventoryOrder)





if __name__ == '__main__':
    print()
    from backend.inventory_optimization.DailyReplenishmentPlan import AdjustDaliyDelivery
    # DelivPlan = AdjustDaliyDelivery('2026-05-08')
    # print(DelivPlan)
    DelivPlan = pd.read_excel('delivery_plan.xlsx')
    site_info = query_adam_del_site_conf()
    site_info = site_info[site_info['STAT_NAME'] != '营销服务中心']
    Path_no = []
    for planpath in DelivPlan['PlanPath']:
        p = []
        idx_list = ast.literal_eval(planpath)
        for idx in idx_list:
            p.append(site_info.loc[idx-1,'ORG_NO'])
        Path_no.append(p)
    DelivPlan['PathNo'] = Path_no
    print(DelivPlan)