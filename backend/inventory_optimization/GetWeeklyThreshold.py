import datetime
import time

import pandas as pd
from backend.inventory_optimization.item import Item
from backend.inventory_optimization.demand_distribution import PoissonDistribution
from .warehouse_initializer import LocalWarehouseInitializer

def GenerateWeeklyThreshold(year:str, month:str):
    '''
    计算周度各市县库存阈值
    '''
    from backend.config.scheme_config import get_approved_scheme_config

    year_month = year + month
    global_scheme_id, epsilon = get_approved_scheme_config(year_month)
    beta = 0.95
    alpha = epsilon
    print(f'使用审批方案: GLOBAL_SCHEME_ID={global_scheme_id}, epsilon={epsilon}')

    from backend.api.data_api.fetch_data import (
        query_adam_wd_dmd_pre_by_year_month_and_pretype,
        query_adam_del_site_conf,
        query_adam_spec_code_config,
        insert_into_adam_stock_week_limt_pre,
        delete_adam_stock_week_limt_pre_by_ym)

    pre_type = '04'
    #获得站点信息，去掉省中心
    org_df = query_adam_del_site_conf()
    org_df = org_df[org_df['STAT_NAME'] != '营销服务中心']
    org_dict = org_df.set_index('ORG_NO')['ORG_NAME'].to_dict()
    #获得预测结果表
    df = query_adam_wd_dmd_pre_by_year_month_and_pretype(year,month,pre_type)
    print(f'成功读取周度预测结果，时间：{year}{month}类型：{pre_type}，数据量：{len(df)}条')
    columns_to_drop = ['WD_DMD_PRE_ID', 'PRE_TYPE', 'PRE_DATE']
    df = df.drop(columns=columns_to_drop, errors='ignore')
    df['ORG_NAME'] = df['ORG_NO'].map(org_dict)
    # 按单位、设备码、周次分组，汇总预测数量（将新装、故障、更换三类数据相加）
    df_grouped = df.groupby(
        ['PRE_YEAR', 'PRE_MONTH', 'PRE_QUARTER', 'PRE_WEEK', 'ORG_NO', 'DEV_CODE','ORG_NAME'],
        as_index=False
    )['PRE_NUM'].sum()
    
    # 重命名列名
    df_grouped = df_grouped.rename(columns={'PRE_NUM': '预测数量'})

    print(f'聚合后周度预测值数据为：{len(df_grouped)}')
    # 准备初始化仓库的临时数据
    df_temp = df_grouped[['ORG_NO','ORG_NAME']].drop_duplicates()
    print(f'准备初始化仓库的临时数据，数据量为:{len(df_temp)}')
    LWI = LocalWarehouseInitializer()
    LWI.load_city_mapping(df_temp)
    local_warehouses = LWI.initialize_warehouses(df_temp)
    
    # 创建仓库字典，方便根据城市编码查找
    warehouse_dict = {w.city_code: w for w in local_warehouses}
    # 按照 ORG_NO, ORG_NAME, DEV_CODE 分组
    grouped = df_grouped.groupby(['ORG_NO', 'ORG_NAME', 'DEV_CODE'])
    
    item_count = 0
    for (org_no, org_name, dev_code), group in grouped:
        warehouse = warehouse_dict.get(str(org_no))
        if not warehouse:
            continue
        item = Item(
            cls=None,
            dev_code=dev_code,
            initial_inventory=0.0,
            holding_cost=0.0,
            shortage_cost=0.0,
            alpha=0.0
        )
        for _, row in group.iterrows():
            week = int(row['PRE_WEEK'])
            lambda_value = float(row['预测数量'])
            # 创建泊松分布并设置到物资
            distribution = PoissonDistribution(lambda_=lambda_value)
            item.set_demand_distribution(week, distribution)
        # 将物资添加到仓库
        warehouse.add_item(dev_code, item)
        item_count += 1
    print(f"物资创建完成，共 {item_count} 个物资", flush=True)
    
    WeekSeq = df_grouped['PRE_WEEK'].unique()
    res_df = []


    # 读取设备分类和类别映射
    print("开始读取设备分类配置...", flush=True)
    spec_df = query_adam_spec_code_config()
    dev_cls_mapping = spec_df.set_index('DEV_CODE')['DEV_CLS'].to_dict()
    dev_categ_mapping = spec_df.set_index('DEV_CODE')['DEV_CATEG'].to_dict()
    print(f"设备分类配置读取完成，共 {len(dev_cls_mapping)} 种设备", flush=True)

    stock_id = int(time.time() * 1000)
    pretime = datetime.datetime.now().strftime("%Y-%m-%d")
    total_items = sum(len(w.items) for w in local_warehouses)
    processed = 0
    for warehouse in local_warehouses:
        for item_key,item in warehouse.items.items():
            try:
                for week_seq in sorted(item.demand_distributions.keys()):
                    low , mid , high = item.get_weekly_threshold(week_seq,beta,alpha)
                    # 获取设备分类和类别
                    dev_cls = dev_cls_mapping.get(item.dev_code, '').zfill(2)
                    dev_categ = dev_categ_mapping.get(item.dev_code, '').zfill(2)
                    record = {
                        "STOCK_WEEK_LIMT_PRE_ID": stock_id,
                        "PRE_YEAR": year,
                        "PRE_QUARTER": df_grouped['PRE_QUARTER'].iloc[0],
                        "PRE_MONTH": month,
                        "PRE_WEEK": week_seq,
                        "ORG_NO": warehouse.city_code,
                        "DEV_CLS": dev_cls,
                        "DEV_CATEG": dev_categ,
                        "DEV_CODE": item.dev_code,
                        "PRE_UP": high,
                        "PRE_DOWN": low,
                        "BASE_LIMT": mid,
                        "PRE_TIME": pretime,
                        "GLOBAL_SCHEME_ID": global_scheme_id
                    }
                    res_df.append(record)
                    stock_id += 1
                processed += 1
                if processed % 200 == 0:
                    print(f"阈值计算进度: {processed}/{total_items}", flush=True)
            except Exception as e:
                print(f"计算阈值失败: dev_code={item.dev_code}, error={e}", flush=True)
                raise

    print(f"阈值计算完成，共处理 {processed} 个物资", flush=True)
    WeeklyThreshold = pd.DataFrame(res_df)
    print(f'生成周度阈值结果{len(WeeklyThreshold)}条', flush=True)

    # 删除旧数据（防御性处理，删除失败不影响后续插入）
    try:
        del_res = delete_adam_stock_week_limt_pre_by_ym(year, month)
        print(f'删除周度阈值旧数据结果{del_res}', flush=True)
    except Exception as e:
        print(f'删除周度阈值旧数据失败（继续执行插入）: {e}', flush=True)

    print(f"开始插入周度阈值数据，共 {len(WeeklyThreshold)} 条...", flush=True)
    insert_result = insert_into_adam_stock_week_limt_pre(WeeklyThreshold)
    print(f"插入周度阈值数据结果: {insert_result}", flush=True)
    return WeeklyThreshold, insert_result

    
    
    
    
    

