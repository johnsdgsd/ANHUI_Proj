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
import pandas as pd
from datetime import datetime
import numpy as np
import pandas as pd
from geopy.distance import geodesic

def build_dist_calculator_from_db():
    """
    利用数据库中的站点经纬度信息构建距离计算器。
    返回: 函数 dist_calculator(from_org, to_org) -> float (公里)
    """
    # 1. 获取站点配置
    tb = query_adam_del_site_conf()
    center = tb[tb['STAT_NAME'] == '营销服务中心']
    sites = tb[tb['STAT_NAME'] != '营销服务中心'].copy()

    # 中心经纬度
    lon_c = center['LONGITUDE'].iloc[0]
    lat_c = center['LATITUDE'].iloc[0]

    # 2. 按顺序拼接经纬度列表：中心在第一个，其他站点保持原顺序
    lons = [lon_c] + sites['LONGITUDE'].tolist()
    lats = [lat_c] + sites['LATITUDE'].tolist()
    n = len(lons)

    # 3. 构建对称距离矩阵（公里）
    DMat = np.zeros((n, n))
    for i in range(n):
        for j in range(i + 1, n):
            d = geodesic((lats[i], lons[i]), (lats[j], lons[j])).km * 1.15
            DMat[i][j] = d
            DMat[j][i] = d

    # 4. 站点编号列表（第一个为 'CENTER'，其余为 ORG_NO）
    org_list = ['CENTER'] + sites['ORG_NO'].astype(str).tolist()
    org_to_idx = {org: idx for idx, org in enumerate(org_list)}

    # 5. 定义计算函数
    def dist_calculator(from_org, to_org):
        i = org_to_idx[str(from_org)]
        j = org_to_idx[str(to_org)]
        return round(DMat[i][j], 4)

    return dist_calculator

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

def f3():
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
    res = []
    month = 5
    for lw in optimizer.local_warehouses:
        for item_key,item in lw.items.items():
            alpha1,demand5,demand6,demand4 = item.calculate_initial_fillrate_with_rate(month,0.5)
            alpha2,_,_,_ = item.calculate_initial_fillrate_with_rate(month,1.5)
            record = {
                "月份":5,
                "单位编码":lw.city_code,
                "单位名称":lw.city_name,
                "设备类型":item.cls,
                "设备码":item.dev_code,
                "初始库存":item.initial_inventory,
                "半月满足率":round(alpha1,8),
                "一个半月满足率":round(alpha2,8),
                '五月需求':demand5,
                '六月需求':demand6,
                '四月需求':demand4
            }
            res.append(record)
    
    Alpha_df = pd.DataFrame(res)
    Alpha_df.to_excel(r"C:\Users\Administrator\Desktop\hengxiang\满足率结果.xlsx",index=False)

def f4():
    """
    读取4月配送明细数据，按接收单位和设备码分组汇总配送数量
    """
    import pandas as pd
    import os
    
    # 读取配送明细数据
    delivery_file_path = r'D:\WYJ\库存优化与检定排程\数据\4月配送明细.xlsx'
    print(f"读取配送明细数据: {delivery_file_path}")
    delivery_df = pd.read_excel(delivery_file_path)
    print(f"配送明细数据共 {len(delivery_df)} 行")
    
    # 按接收单位和设备码分组汇总配送数量
    if all(col in delivery_df.columns for col in ['接收单位', '设备码', '配送数量']):
        # 分组汇总
        grouped_df = delivery_df.groupby(
            ['接收单位', '设备码'],
            as_index=False
        )['配送数量'].sum()
        
        # 重命名列
        grouped_df = grouped_df.rename(columns={'配送数量': '总配送数量'})
        
        print(f"\n汇总后数据共 {len(grouped_df)} 行")
        print(f"汇总数据预览:\n{grouped_df.head()}")
        
        # 保存汇总后的数据
        output_path = r'C:\Users\Administrator\Desktop\hengxiang\汇总配送数量.xlsx'
        grouped_df.to_excel(output_path, index=False)
        print(f"\n汇总后的配送数量数据已保存到: {output_path}")
        
        return grouped_df
    else:
        missing_cols = [col for col in ['接收单位', '设备码', '配送数量'] if col not in delivery_df.columns]
        print(f"警告：缺少以下列，无法汇总: {missing_cols}")
        return None

def f5():
    """
    读取满足率结果数据，按单位编码和设备类型分组计算平均满足率
    """
    import pandas as pd
    
    # 读取满足率结果数据
    fill_rate_file_path = r'C:\Users\Administrator\Desktop\满足率结果_修改后.xlsx'
    print(f"读取满足率结果数据: {fill_rate_file_path}")
    fill_rate_df = pd.read_excel(fill_rate_file_path)
    print(f"满足率结果数据共 {len(fill_rate_df)} 行")
    
    # 检查是否包含必要的列
    if all(col in fill_rate_df.columns for col in ['单位编码', '设备类型', '优化后满足率']):
        # 按单位编码和设备类型分组计算平均满足率
        avg_fill_rate_df = fill_rate_df.groupby(
            ['单位编码'],
            as_index=False
        )['优化后满足率'].mean()
        
        # 重命名列
        avg_fill_rate_df = avg_fill_rate_df.rename(columns={'优化后满足率': '平均满足率'})
        
        # 合并回原始数据
        result_df = fill_rate_df.merge(
            avg_fill_rate_df,
            on=['单位编码'],
            how='left'
        )
        
        print(f"\n处理后数据共 {len(result_df)} 行（与原始数据行数一致）")
        print(f"处理后数据预览:\n{result_df.head()}")
        
        # 保存处理后的数据
        output_path = r'C:\Users\Administrator\Desktop\满足率结果_带平均满足率.xlsx'
        result_df.to_excel(output_path, index=False)
        print(f"\n处理后的满足率数据已保存到: {output_path}")
        
        return result_df
    else:
        missing_cols = [col for col in ['单位编码', '设备类型', '优化后满足率'] if col not in fill_rate_df.columns]
        print(f"警告：缺少以下列，无法计算平均满足率: {missing_cols}")
        return None

def GenerateSchemeFromRawData(raw_df, dist_calculator):
    """
    根据原始配送明细数据生成 ADAM_DIST_SCHEME 和 ADAM_DIST_SCHEME_DET 两张表。

    参数:
        raw_df: DataFrame，必须包含以下列：
            - 配送日期 (DIST_DATE)     # 新增，格式需能转为日期
            - 接收单位 (REC_ORG_NO)
            - 设备码 (DEV_CODE)
            - 配送数量 (PLAN_DIST_NUM)
            - 车牌号 (CAR_NO)
            - 车型 (CAR_TYPE)
            - 配送箱数 (BOX_NUM)
        ve_cap_dict: 车型容量字典
        dist_calculator: 距离计算函数 f(from_org, to_org) -> float 公里
        ve_unit_price_dict: 车型每箱每公里单价字典，可选
    返回:
        main_df, detail_df
    """
    ve_cap_dict = {
        '01':1100,
        '02':900,
        '03':410
    }
    ve_unit_price_dict={
        '01':0.0695,
        '02':0.0695,
        '03':0.0695
    }
    df = raw_df.copy()
    # 确保配送日期为日期类型，并转为字符串 'YYYY-MM-DD'
    df['配送日期'] = pd.to_datetime(df['配送日期']).dt.strftime('%Y-%m-%d')
    # 添加原始顺序索引（保持读取顺序，确保配送顺序正确）
    df['_order'] = range(len(df))

    # 按日期和车牌号分组（关键修改）
    grouped = df.groupby(['配送日期', '车牌号'], sort=False)

    main_rows = []
    detail_rows = []
    detail_id = 1
    # 全局方案ID可基于整个数据集的日期范围生成，这里采用固定前缀+序号
    global_scheme_id = 20260401  # 可根据需要动态生成
    current_time_str = datetime.now().strftime('%Y-%m-%d')

    for (dist_date, car_no), group in grouped:
        car_type = str(group['车型'].iloc[0]).zfill(2)
        group['接收单位'] = group['接收单位'].astype(str)
        # 提取路径：按第一次出现顺序去重
        seen = set()
        path_orgs = []
        for org in group['接收单位']:
            if org not in seen:
                seen.add(org)
                path_orgs.append(str(org))
        stop_count = len(path_orgs)

        # 站点总箱数
        org_total_boxes = {}
        org_total_pieces = {}
        for org in path_orgs:
            site_data = group[group['接收单位'] == org]
            org_total_boxes[org] = site_data['配送箱数'].sum()
            org_total_pieces[org] = site_data['配送数量'].sum()
        total_boxes = sum(org_total_boxes.values())

        # 装载率
        cap = ve_cap_dict.get(car_type, 1)
        load_rate = f"{total_boxes / cap * 100:.1f}%" if cap > 0 else "0%"

        # 方案ID（简单递增）
        scheme_id = global_scheme_id * 100000 + len(main_rows) + 1

        # 主表记录（日期取自组名 dist_date）
        main_rows.append({
            'DIST_SCHEME_ID': scheme_id,
            'CAR_TYPE': car_type,
            'PLAN_DIST_DATE': dist_date,  # 已是 'YYYY-MM-DD'
            'DIST_FLAG': 'Y',
            'LATE_FLAG': 'N',
            'LOAD_RATE': load_rate,
            'CREATE_DATE': current_time_str,
            'UPDATE_DATE': current_time_str,
            'GLOBAL_SCHEME_ID': global_scheme_id
        })

        # 计算各段距离
        segment_distances = []
        prev_org = 'CENTER'
        for org in path_orgs:
            d = dist_calculator(prev_org, org)
            segment_distances.append(d)
            prev_org = org

        unit_price = ve_unit_price_dict.get(car_type, 0.0) if ve_unit_price_dict else 0.0

        # 明细
        for stop_idx, org in enumerate(path_orgs):
            dist_seq = stop_idx + 1
            load_seq = stop_count - stop_idx
            seg_dist = segment_distances[stop_idx]
            site_boxes = org_total_boxes[org]

            site_total_cost = site_boxes * unit_price * seg_dist if unit_price else 0.0

            site_data = group[group['接收单位'] == org]
            dev_summary = site_data.groupby('设备码').agg(
                配送数量=('配送数量', 'sum'),
                配送箱数=('配送箱数', 'sum')
            ).reset_index()
            print(f"站点 {org} 原始行数: {len(site_data)}, 聚合后行数: {len(dev_summary)}")

            for _, dev_row in dev_summary.iterrows():
                dev_code = str(dev_row['设备码'])
                qty = int(dev_row['配送数量'])
                box_qty = int(dev_row['配送箱数'])
                if qty == 0:
                    continue

                box_ratio = box_qty / site_boxes if site_boxes > 0 else 0
                dist_exp = round(site_total_cost * box_ratio, 4)
                est_dist = round(seg_dist * box_ratio, 4)

                detail_rows.append({
                    'DIST_SCHEME_DET_ID': detail_id,
                    'DIST_SCHEME_ID': scheme_id,
                    'REC_ORG_NO': org,
                    'DEV_CODE': dev_code,
                    'DEV_CLS': '',
                    'DEV_CATEG': '',
                    'DIST_SEQ': dist_seq,
                    'LOAD_SEQ': load_seq,
                    'PLAN_DIST_NUM': qty,
                    'EST_TOT_DIST_MIST': est_dist,
                    'DIST_EXP': dist_exp,
                    'GLOBAL_SCHEME_ID': global_scheme_id
                })
                detail_id += 1

    main_df = pd.DataFrame(main_rows)
    detail_df = pd.DataFrame(detail_rows)

    if not main_df.empty:
        main_df = main_df[['DIST_SCHEME_ID', 'CAR_TYPE', 'PLAN_DIST_DATE', 'DIST_FLAG', 'LATE_FLAG',
                           'LOAD_RATE', 'CREATE_DATE', 'UPDATE_DATE', 'GLOBAL_SCHEME_ID']]
    if not detail_df.empty:
        detail_df = detail_df[['DIST_SCHEME_DET_ID', 'DIST_SCHEME_ID', 'REC_ORG_NO', 'DEV_CODE',
                               'DEV_CLS', 'DEV_CATEG', 'DIST_SEQ', 'LOAD_SEQ', 'PLAN_DIST_NUM',
                               'EST_TOT_DIST_MIST', 'DIST_EXP', 'GLOBAL_SCHEME_ID']]
    return main_df, detail_df

if __name__ == '__main__':
    print()
    # from backend.inventory_optimization.DailyReplenishmentPlan import AdjustDaliyDelivery,DailyReplenishmentPlan
    # DailyReplenishmentPlan('2026-05-01','2026-05-31')
    # MainScheme , DetailScheme = AdjustDaliyDelivery('2026-05-06')
    # print(MainScheme ,'\n', DetailScheme)
    # insert_into_adam_dist_scheme(MainScheme)
    # insert_into_adam_dist_scheme_det(DetailScheme)
    # demand,order = f2()
    # demand.to_excel("使用新的历史安装量并转换设备码后的库存阈值.xlsx",index=False)
    # order.to_excel("使用新的历史安装量并转换设备码后的各单位补货量.xlsx",index=False)
    # print(demand)
    # print(order)
    # f5()
    # 构建距离计算器（只需执行一次）
    # dist_calc = build_dist_calculator_from_db()
    # file_path = r"D:\WYJ\库存优化与检定排程\数据\4月配送明细.xlsx"
    # raw_df = pd.read_excel(file_path)
    # print(raw_df.columns.tolist())
    # print(raw_df.head())
    # 传入生成函数
    # main_df, detail_df = GenerateSchemeFromRawData(
    #     raw_df,
    #     dist_calculator=dist_calc
    # )
    # import os
    # output_dir = r"C:\Users\Administrator\Desktop\hengxiang"
    # os.makedirs(output_dir, exist_ok=True)  # 若目录不存在则创建
    # output_file = os.path.join(output_dir, "四月配送主表与明细表.xlsx")
    #
    # # 2. 写入 Excel（一个文件多个 Sheet）
    # with pd.ExcelWriter(output_file, engine='openpyxl') as writer:
    #     if not main_df.empty:
    #         main_df.to_excel(writer, sheet_name='主表', index=False)
    #     if not detail_df.empty:
    #         detail_df.to_excel(writer, sheet_name='明细表', index=False)
    #
    # print(f"文件已保存至：{output_file}")
    #
    # # 3. 计算并打印明细表总成本
    # if not detail_df.empty:
    #     total_cost = detail_df['DIST_EXP'].sum()
    #     print(f"明细表所有配送费用总和：{total_cost:,.4f} 元")
    # else:
    #     print("明细表为空，无费用数据")

    # query_adam_org_stock_sample_by_month('202605')
    # query_adam_yqm_dmd_pre_by_year('2026')
    # query_adam_glob_strategy_scheme_by_month('202405')
    # query_adam_glob_strategy_scheme_itt_by_schemeid(1)
    # query_adam_yqm_dmd_pre_by_year_month('2026','05')
    # query_adam_veri_config_all()
    # query_adam_single_cost_config_all()
    query_adam_glob_strategy_scheme_cost_by_schemeid(111)