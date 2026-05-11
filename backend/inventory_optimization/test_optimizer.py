from backend.inventory_optimization.optimizer import InventoryOptimizer
from backend.inventory_optimization.warehouse import CentralWarehouse,LocalWarehouse
from backend.inventory_optimization.item import Item
from backend.api.data_api.fetch_data import *
from backend.inventory_optimization.demand_distribution import PoissonDistribution
from backend.inventory_optimization.RunOptimize import run_optimization_from_api
from datetime import datetime
from backend.inventory_optimization import DailyReplenishmentPlan
import ast 
from backend.data_cleaning.process_historical_install_data import process_historical_install_data

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

def f2():
    import pandas as pd
    # 1. 读取初始库存数据并去重
    stock_file_path = r'C:\Users\Administrator\Desktop\NARI_APS_INVENTORY_INIT_STOCK.xlsx'
    stock_df = pd.read_excel(stock_file_path)
    # 按单位编码和设备码去重
    stock_df = stock_df.drop_duplicates(subset=['UNIT_CODE', 'DEVICE_CODE'], keep='first')
    print(f"初始库存数据共 {len(stock_df)} 行（去重后）")
    
    # 2. 读取安装量数据
    install_df = process_historical_install_data()
    print(f"安装量数据共 {len(install_df)} 行")
    
    # 3. 读取满足率数据
    fulfill_file_path = r'C:\Users\Administrator\Desktop\NARI_APS_INVENTORY_FULFILL_RATE.xlsx'
    fulfill_df = pd.read_excel(fulfill_file_path)
    print(f"满足率数据共 {len(fulfill_df)} 行")
    
    # 4. 初始化LocalWarehouse列表
    warehouses = []
    # 按单位编码分组，一个仓库对应多个物资
    for unit_code, group in fulfill_df.groupby('UNIT_CODE'):
        # 创建仓库
        warehouse = LocalWarehouse(warehouse_id=None,city_code=unit_code,city_name=None)
        
        # 遍历该仓库下的所有设备码
        for device_code, device_group in group.groupby('DEVICE_CODE'):
            # 获取满足率
            fulfill_rate = device_group['FULFILL_RATE'].iloc[0]
            
            # 获取初始库存（确保数据类型一致）
            stock_row = stock_df[
                (stock_df['UNIT_CODE'].astype(str) == str(unit_code)) & 
                (stock_df['DEVICE_CODE'].astype(str) == str(device_code))
            ]
            begin_stock_num = stock_row['BEGIN_STOCK_NUM'].iloc[0] if not stock_row.empty else 0
            
            # 获取安装量（确保数据类型一致）
            install_row = install_df[
                (install_df['单位编码'].astype(str) == str(unit_code)) & 
                (install_df['设备码编号'].astype(str) == str(device_code))
            ]
            avg_install = install_row['平均安装数量'].iloc[0] if not install_row.empty else 5
            
            # 添加物资到仓库
            item = Item(dev_code=device_code, initial_inventory=begin_stock_num, alpha=fulfill_rate,cls = None,holding_cost=0,shortage_cost=0)
            item.set_demand_distribution(5,PoissonDistribution(lambda_=avg_install))
            warehouse.add_item(device_code,item)
            
            print(f"仓库 {unit_code} 物资 {device_code} 初始化完成：初始库存 {begin_stock_num}，安装量 {avg_install}，满足率 {fulfill_rate}")
        
        # 将仓库添加到列表
        warehouses.append(warehouse)


    tag = datetime.now().strftime("%Y%m%d%H%M%S")
    demand_result_data = []
    order_result_data = []
    for local_warehouse in warehouses:
        for item_key, item in local_warehouse.items.items():
            demand = item.generate_demand_quantile(5)
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
    return pd.DataFrame(demand_result_data),pd.DataFrame(order_result_data)


if __name__ == '__main__':
    print()
    from backend.inventory_optimization.DailyReplenishmentPlan import AdjustDaliyDelivery,DailyReplenishmentPlan
    # DailyReplenishmentPlan('2026-05-01','2026-05-31')
    MainScheme , DetailScheme = AdjustDaliyDelivery('2026-05-06')
    print(MainScheme ,'\n', DetailScheme)
    # demand,order = f2()
    # demand.to_excel("使用新的历史安装量并转换设备码后的库存阈值.xlsx",index=False)
    # order.to_excel("使用新的历史安装量并转换设备码后的各单位补货量.xlsx",index=False)
    # print(demand)
    # print(order)