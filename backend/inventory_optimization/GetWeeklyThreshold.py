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
        query_adam_del_site_conf)

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
    for warehouse in local_warehouses:
        for item_key,item in warehouse.items.items():
            for week_seq in WeekSeq:
                low , mid , high = item.get_weekly_threshold(week_seq,beta,alpha)
                record = {
                    "ORG_NO":warehouse.city_code,
                    "DEV_CODE":item.dev_code,
                    "ORG_NAME":warehouse.city_name,
                    "PRE_WEEK":week_seq,
                    "LOW":low,
                    "MID":mid,
                    "HIGH":high
                }
                res_df.append(record)
    
    WeeklyThreshold = pd.DataFrame(res_df)
    WeeklyThreshold['PRE_YEAR'] = df_grouped['PRE_YEAR']
    WeeklyThreshold['PRE_MONTH'] = df_grouped['PRE_MONTH']
    WeeklyThreshold['PRE_QUARTER'] = df_grouped['PRE_QUARTER']
    return WeeklyThreshold

    
    
    
    
    

