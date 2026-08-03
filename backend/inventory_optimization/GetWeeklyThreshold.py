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

    # ============ 1. 加载月度需求预测 ============
    monthly_df = query_adam_yqm_dmd_pre_by_year_month(year, month)
    print(f'成功读取月度需求预测，时间：{year}{month}，数据量：{len(monthly_df)}条')
    monthly_demand = {}
    for _, row in monthly_df.iterrows():
        key = (str(row['ORG_NO']), str(row['DEV_CODE']))
        monthly_demand[key] = float(row['PRE_NUM'])
    # ── 月度预测维度 ──
    monthly_orgs = monthly_df['ORG_NO'].nunique()
    monthly_devs = monthly_df['DEV_CODE'].nunique()
    print(f"[月度预测维度] 单位数: {monthly_orgs}, 设备码数: {monthly_devs}, "
          f"总记录: {len(monthly_df)}, 单位×设备码: {monthly_orgs * monthly_devs}")
    zero_keys = [k for k, v in monthly_demand.items() if v <= 0]
    if zero_keys:
        print(f"[月度预测] PRE_NUM=0 的记录: {len(zero_keys)} 条, 设备码: {sorted(set(d for _,d in zero_keys))}")
        for k in zero_keys[:3]:
            print(f"  [样本] {k} -> {monthly_demand[k]}")

    # ============ 2. 加载周度需求预测（仅取周次信息） ============
    pre_type = '04'
    org_df = query_adam_del_site_conf()
    org_df = org_df[org_df['STAT_NAME'] != '营销服务中心']
    org_dict = org_df.set_index('ORG_NO')['ORG_NAME'].to_dict()

    df = query_adam_wd_dmd_pre_by_year_month_and_pretype(year, month, pre_type)
    print(f'成功读取周度预测结果，时间：{year}{month}类型：{pre_type}，数据量：{len(df)}条')

    # 提取去重周次
    distinct_weeks = sorted(int(w) for w in df['PRE_WEEK'].unique())
    default_quarter = df['PRE_QUARTER'].iloc[0] if len(df) > 0 else ''
    print(f"[周度数据] 周数: {len(distinct_weeks)}, 周次: {distinct_weeks}")

    # 构建周度预测值索引: (ORG_NO, DEV_CODE, PRE_WEEK) → PRE_NUM
    weekly_lookup = {}
    for _, row in df.iterrows():
        wk = (str(row['ORG_NO']), str(row['DEV_CODE']), int(row['PRE_WEEK']))
        weekly_lookup[wk] = weekly_lookup.get(wk, 0) + float(row['PRE_NUM'])
    print(f"[周度数据] 唯一键数量: {len(weekly_lookup)}")

    # ============ 3. 构建仓库（基于月度数据中的单位） ============
    monthly_org_list = monthly_df[['ORG_NO']].drop_duplicates().copy()
    monthly_org_list['ORG_NAME'] = monthly_org_list['ORG_NO'].map(org_dict)
    print(f'[仓库初始化] 单位数: {len(monthly_org_list)}')
    LWI = LocalWarehouseInitializer()
    LWI.load_city_mapping(monthly_org_list)
    local_warehouses = LWI.initialize_warehouses(monthly_org_list)
    warehouse_dict = {w.city_code: w for w in local_warehouses}
    print(f'仓库创建完成，共 {len(local_warehouses)} 个仓库', flush=True)

    # ============ 4. 为每个物资装入周度需求分布（月度维度 × 所有周次） ============
    item_count = 0
    skipped_no_warehouse = 0
    missing_weekly = 0
    for (org_no, dev_code), monthly_val in monthly_demand.items():
        warehouse = warehouse_dict.get(org_no)
        if not warehouse:
            skipped_no_warehouse += 1
            continue
        item = Item(
            cls=None,
            dev_code=dev_code,
            initial_inventory=0.0,
            holding_cost=0.0,
            shortage_cost=0.0,
            alpha=0.0
        )
        has_any_weekly = False
        for week in distinct_weeks:
            wl = weekly_lookup.get((org_no, dev_code, week), 0)
            if wl > 0:
                has_any_weekly = True
            item.set_demand_distribution(week, PoissonDistribution(lambda_=wl))
        if not has_any_weekly:
            missing_weekly += 1
        warehouse.add_item(dev_code, item)
        item_count += 1

    print(f"[物资创建] 共 {item_count} 个物资 (={monthly_orgs}×{monthly_devs}), "
          f"跳过(无仓库): {skipped_no_warehouse}, 无任何周度数据: {missing_weekly}", flush=True)

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
                        "PRE_QUARTER": default_quarter,
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
        sample_count = 0
        for warehouse in local_warehouses:
            for item_key, item in warehouse.items.items():
                m_key = (warehouse.city_code, str(item.dev_code))
                if monthly_demand.get(m_key, 0) <= 0:
                    print(f"  [样本] warehouse.city_code={warehouse.city_code!r}, "
                          f"dev_code={item.dev_code!r}, "
                          f"monthly_demand.get={monthly_demand.get(m_key, 'MISSING')!r}")
                    sample_count += 1
                    if sample_count >= 5:
                        break
            if sample_count >= 5:
                break

    print(f"阈值计算完成，共处理 {processed} 个物资", flush=True)
    WeeklyThreshold = pd.DataFrame(res_df)
    print(f'生成周度阈值结果{len(WeeklyThreshold)}条', flush=True)
    # ── 最终阈值维度 ──
    final_orgs = WeeklyThreshold['ORG_NO'].nunique()
    final_devs = WeeklyThreshold['DEV_CODE'].nunique()
    final_weeks = WeeklyThreshold['PRE_WEEK'].nunique()
    print(f"[最终阈值维度] 单位数: {final_orgs}, 设备码数: {final_devs}, "
          f"周数: {final_weeks}, 总记录: {len(WeeklyThreshold)}, "
          f"单位×设备码×周数: {final_orgs * final_devs * final_weeks}")

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

    
    
    
    
    

