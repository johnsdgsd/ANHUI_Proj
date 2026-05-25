"""
生成月度库存阈值和补货订单
"""
import time

import pandas as pd
import numpy as np
from datetime import datetime

from numpy.matlib import empty

from backend.inventory_optimization.item import Item
from backend.inventory_optimization.demand_distribution import PoissonDistribution
from backend.inventory_optimization.warehouse_initializer import LocalWarehouseInitializer


def GenerateMonthlyThresholdAndOrder(year: str, month: str,init_stock:pd.DataFrame,tag:str,alpha:float):
    '''
    计算月度各市县库存阈值
    '''
    # beta = 0.95  # 基准库存置信水平
    # alpha = 0.99  # 库存上限置信水平

    from backend.api.data_api.fetch_data import (
        query_adam_yqm_dmd_pre_by_year_month,
        query_adam_del_site_conf,
        query_adam_spec_code_config,
        )

    # 生成初始时间戳（精确到毫秒）
    timestamp = int(time.time()*1000)
    # 获得月度预测结果表
    df = query_adam_yqm_dmd_pre_by_year_month(year, month)
    print(f'获取月度需求预测数据成功，日期{year}{month}，数据量：{len(df)}')
    # 按单位、设备码分组，汇总预测数量（将所有业务类型的数据相加）
    df_grouped = df.groupby(
        ['PRE_YEAR', 'PRE_MONTH', 'ORG_NO', 'DEV_CODE'],
        as_index=False
    )['PRE_NUM'].sum()
    
    # 重命名列名
    df_grouped = df_grouped.rename(columns={'PRE_NUM': '预测数量'})
    
    # 初始化地方库
    LWI = LocalWarehouseInitializer()
    LWI.load_city_mapping(init_stock)
    local_warehouses = LWI.initialize_warehouses(init_stock)
    
    # 创建仓库字典，方便根据城市编码查找
    warehouse_dict = {w.city_code: w for w in local_warehouses}
    
    # 按照 ORG_NO, DEV_CODE 分组
    grouped = df_grouped.groupby(['ORG_NO', 'DEV_CODE'])

    demand_pre_res = []

    for (org_no, dev_code), group in grouped:
        warehouse = warehouse_dict.get(str(org_no))
        if not warehouse:
            continue

        # 获取月度预测数量
        monthly_demand = group['预测数量'].iloc[0]
        mask = (init_stock['ORG_NO'] == org_no) & (init_stock['DEV_CODE'] == dev_code)
        init_stock_val = init_stock.loc[mask, 'STOCK_NUM'].sum()
        # 创建物资
        item = Item(
            cls=None,
            dev_code=dev_code,
            initial_inventory=init_stock_val,
            holding_cost=0.0,
            shortage_cost=0.0,
            alpha=alpha
        )
        
        # 设置月度需求分布（使用泊松分布）
        distribution = PoissonDistribution(lambda_ = monthly_demand,tn=0.5,T=1)
        item.set_demand_distribution(int(month), distribution)  # 月度用第1周表示
        
        # 将物资添加到仓库
        warehouse.add_item(dev_code, item)

        demand_item = {
            'ORG_NO':org_no,
            'DEV_CODE':dev_code,
            'PRE_NUM':monthly_demand
        }
        demand_pre_res.append(demand_item)
    
    threshold_df = []
    order_df = []

    stock_id = timestamp

    spec_df = query_adam_spec_code_config()
    dev_cls_mapping = spec_df.set_index('DEV_CODE')['DEV_CLS'].to_dict()
    dev_categ_mapping = spec_df.set_index('DEV_CODE')['DEV_CATEG'].to_dict()

    # 生成时间戳和日期
    pretime = datetime.now().strftime("%Y-%m-%d")
    
    for warehouse in local_warehouses:
        for item_key, item in warehouse.items.items():
            # 获取月度阈值
            demand = item.generate_demand_quantile(int(month))
            demand = round(demand)
            # 月度补货量
            order = demand - item.initial_inventory
            order = max(0,order)
            # 获取设备分类和类别
            dev_cls = dev_cls_mapping.get(item.dev_code, '')
            dev_categ = dev_categ_mapping.get(item.dev_code, '')
            
            record1 = {
                "STOCK_MONTH_LIMIT_PRE_ID": stock_id,  # 修正字段名拼写
                "PRE_YEAR": year,
                "PRE_MONTH": month,
                "ORG_NO": warehouse.city_code,
                "DEV_CLS": dev_cls,
                "DEV_CATEG": dev_categ,
                "DEV_CODE": item.dev_code,
                "BASE_LIMIT": int(demand),  # 修正字段名拼写并转换为整数
                "PRE_TIME": pretime,  # 确保为日期格式字符串
                "GLOBAL_SCHEME_ID": int(tag)  # 转换为整数类型
            }
            #补货量df
            record2 = {
                'PLAN_MONTH_IAS_PRE_ID':stock_id,
                'PRE_YEAR':year,
                'PRE_MONTH':month,
                'REC_ORG_CODE':warehouse.city_code,
                'DEV_CLS':dev_cls,
                'DEV_CATEG':dev_categ,
                'DEV_CODE':item.dev_code,
                'PLAN_IAS_NUM':order,
                'GLOBAL_SCHEME_ID':int(tag)
            }
            threshold_df.append(record1)
            order_df.append(record2)
            stock_id += 1
    
    MonthlyThreshold = pd.DataFrame(threshold_df)
    MonthlyOrder = pd.DataFrame(order_df)
    return MonthlyThreshold,MonthlyOrder,pd.DataFrame(demand_pre_res)