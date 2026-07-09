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
    # 审批未通过则默认 0.99
    if epsilon is None:
        epsilon = 0.99
    print(f'使用审批方案: GLOBAL_SCHEME_ID={global_scheme_id}, epsilon={epsilon}')

    from backend.api.data_api.fetch_data import (
        query_adam_wd_dmd_pre_by_year_month_and_pretype,
        query_adam_yqm_dmd_pre_by_year_month,
        query_adam_del_site_conf,
        query_adam_spec_code_config,
        insert_into_adam_stock_week_limt_pre,
        delete_adam_stock_week_limt_pre_by_ym)

    # ============ 1. 加载月度需求预测（用于上限） ============
    monthly_df = query_adam_yqm_dmd_pre_by_year_month(year, month)
    print(f'成功读取月度需求预测，时间：{year}{month}，数据量：{len(monthly_df)}条')
    monthly_demand = {}
    for _, row in monthly_df.iterrows():
        key = (str(row['ORG_NO']), str(row['DEV_CODE']))
        monthly_demand[key] = float(row['PRE_NUM'])

    # ============ 2. 加载周度需求预测（用于下限） ============
    pre_type = '04'
    org_df = query_adam_del_site_conf()
    org_df = org_df[org_df['STAT_NAME'] != '营销服务中心']
    org_dict = org_df.set_index('ORG_NO')['ORG_NAME'].to_dict()

    df = query_adam_wd_dmd_pre_by_year_month_and_pretype(year, month, pre_type)
    print(f'成功读取周度预测结果，时间：{year}{month}类型：{pre_type}，数据量：{len(df)}条')
    columns_to_drop = ['WD_DMD_PRE_ID', 'PRE_TYPE', 'PRE_DATE']
    df = df.drop(columns=columns_to_drop, errors='ignore')
    df['ORG_NAME'] = df['ORG_NO'].map(org_dict)
    df_grouped = df.groupby(
        ['PRE_YEAR', 'PRE_MONTH', 'PRE_QUARTER', 'PRE_WEEK', 'ORG_NO', 'DEV_CODE', 'ORG_NAME'],
        as_index=False
    )['PRE_NUM'].sum()
    df_grouped = df_grouped.rename(columns={'PRE_NUM': '预测数量'})
    print(f'聚合后周度预测值数据为：{len(df_grouped)}')

    # ============ 3. 构建仓库（基于周度数据中的单位） ============
    df_temp = df_grouped[['ORG_NO', 'ORG_NAME']].drop_duplicates()
    print(f'准备初始化仓库的临时数据，数据量为:{len(df_temp)}')
    LWI = LocalWarehouseInitializer()
    LWI.load_city_mapping(df_temp)
    local_warehouses = LWI.initialize_warehouses(df_temp)
    warehouse_dict = {w.city_code: w for w in local_warehouses}

    # ============ 4. 为每个物资装入周度需求分布 ============
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
            distribution = PoissonDistribution(lambda_=lambda_value)
            item.set_demand_distribution(week, distribution)
        warehouse.add_item(dev_code, item)
        item_count += 1
    print(f"物资创建完成，共 {item_count} 个物资", flush=True)

    # ============ 5. 读取设备分类映射 ============
    print("开始读取设备分类配置...", flush=True)
    spec_df = query_adam_spec_code_config()
    dev_cls_mapping = spec_df.set_index('DEV_CODE')['DEV_CLS'].to_dict()
    dev_categ_mapping = spec_df.set_index('DEV_CODE')['DEV_CATEG'].to_dict()
    print(f"设备分类配置读取完成，共 {len(dev_cls_mapping)} 种设备", flush=True)

    # ============ 6. 计算周度阈值 ============
    # 上限 = 月度需求 × 1.5 倍泊松 × epsilon
    # 下限 = 周度需求 × 0.467 倍泊松 × epsilon
    # 基准 = (上限 + 下限) / 2
    import math as _math
    from scipy.stats import poisson as _poisson

    res_df = []
    stock_id = int(time.time() * 1000)
    pretime = datetime.datetime.now().strftime("%Y-%m-%d")
    total_items = sum(len(w.items) for w in local_warehouses)
    processed = 0
    zero_monthly = 0

    for warehouse in local_warehouses:
        for item_key, item in warehouse.items.items():
            try:
                m_key = (warehouse.city_code, str(item.dev_code))
                monthly_lambda = monthly_demand.get(m_key, 0)
                if monthly_lambda <= 0:
                    zero_monthly += 1

                for week_seq in sorted(item.demand_distributions.keys()):
                    weekly_dist = item.demand_distributions[week_seq]
                    weekly_lambda = weekly_dist.lambda_

                    # 上限: 月度需求 × 1.5 (tn=0.5)
                    high = int(_math.ceil(_poisson.ppf(epsilon, monthly_lambda * 1.5)))
                    # 下限: 周度需求 × 0.467
                    low = int(_math.ceil(_poisson.ppf(epsilon, monthly_lambda * 0.467)))
                    # 基准 = (上限 + 下限) / 2
                    mid = int((high + low) / 2)

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

    if zero_monthly > 0:
        print(f"⚠ 注意: {zero_monthly}/{total_items} 个物资无月度需求数据，上限=0", flush=True)

    print(f"阈值计算完成，共处理 {processed} 个物资", flush=True)
    WeeklyThreshold = pd.DataFrame(res_df)
    print(f'生成周度阈值结果{len(WeeklyThreshold)}条', flush=True)

    # 删除旧数据（防御性处理，删除失败不影响后续插入）
    try:
        del_res = delete_adam_stock_week_limt_pre_by_ym(year, month)
        print(f'删除周度阈值旧数据结果{del_res}', flush=True)
    except Exception as e:
        print(f'删除周度阈值旧数据失败（继续执行插入）: {e}', flush=True)

    from backend.api.data_api.fetch_data import query_pk_next
    WeeklyThreshold['STOCK_WEEK_LIMT_PRE_ID'] = [int(x) for x in query_pk_next("SEQ_ADAM_STOCK_WEEK_LIMT_PRE", len(WeeklyThreshold))]
    print(f"开始插入周度阈值数据，共 {len(WeeklyThreshold)} 条...", flush=True)
    insert_result = insert_into_adam_stock_week_limt_pre(WeeklyThreshold)
    print(f"插入周度阈值数据结果: {insert_result}", flush=True)
    return WeeklyThreshold, insert_result

    
    
    
    
    

