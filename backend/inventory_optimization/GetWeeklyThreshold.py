import datetime
import pandas as pd
from backend.inventory_optimization.item import Item
from backend.inventory_optimization.demand_distribution import PoissonDistribution
from .warehouse_initializer import LocalWarehouseInitializer

def GenerateWeeklyThreshold(year:str, month:str):
    '''
    计算周度各市县库存阈值
    '''
    beta = 0.95
    alpha = 0.99

    from backend.api.data_api.fetch_data import (
        query_adam_wd_dmd_pre_by_year_month_and_pretype,
        query_adam_del_site_conf,
        query_adam_spec_code_config,
        insert_into_adam_stock_week_limt_pre)

    pre_type = '周预测'
    #获得站点信息，去掉省中心
    org_df = query_adam_del_site_conf()
    org_df = org_df[org_df['STAT_NAME'] != '营销服务中心']
    org_dict = org_df.set_index('ORG_NO')['ORG_NAME'].to_dict()
    #获得预测结果表
    df = query_adam_wd_dmd_pre_by_year_month_and_pretype(year,month,pre_type)
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
    
    # 准备初始化仓库的临时数据
    df_temp = df_grouped[['ORG_NO','ORG_NAME']].rename(columns={'ORG_NO':'UNIT_CODE','ORG_NAME':'UNIT_NAME'}).drop_duplicates()
    LWI = LocalWarehouseInitializer()
    LWI.load_city_mapping(df_temp)
    local_warehouses = LWI.initialize_warehouses(df_temp)
    
    # 创建仓库字典，方便根据城市编码查找
    warehouse_dict = {w.city_code: w for w in local_warehouses}
    # 按照 ORG_NO, ORG_NAME, DEV_CODE 分组
    grouped = df_grouped.groupby(['ORG_NO', 'ORG_NAME', 'DEV_CODE'])
    
    for (org_no, org_name, dev_code), group in grouped:
        warehouse = warehouse_dict.get(str(org_no))
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
    
    WeekSeq = df_grouped['PRE_WEEK'].unique()
    res_df = []

    
    # 读取设备分类和类别映射
    spec_df = query_adam_spec_code_config()
    dev_cls_mapping = spec_df.set_index('DEV_CODE')['DEV_CLS'].to_dict()
    dev_categ_mapping = spec_df.set_index('DEV_CODE')['DEV_CATEG'].to_dict()

    tag = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    stock_id = int(tag) * 100 
    pretime = datetime.datetime.now().strftime("%Y-%m-%d")
    for warehouse in local_warehouses:
        for item_key,item in warehouse.items.items():
            for week_seq in WeekSeq:
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
                    "DEV_CLS": dev_cls,
                    "DEV_CATEG": dev_categ,
                    "DEV_CODE": item.dev_code,
                    "PRE_UP": high,
                    "PRE_DOWN": low,
                    "BASE_LIMT": mid,
                    "PRE_TIME": pretime,
                    "GLOBAL_SCHEME_ID": tag
                }
                res_df.append(record)
                stock_id += 1
    
    WeeklyThreshold = pd.DataFrame(res_df)
    return WeeklyThreshold,insert_into_adam_stock_week_limt_pre(WeeklyThreshold)

    
    
    
    
    

