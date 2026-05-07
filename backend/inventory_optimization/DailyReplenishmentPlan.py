"""
日补库计划生成脚本
汇总配送方案结果
"""

import pandas as pd
from datetime import datetime, timedelta
from typing import List, Dict, Tuple
from backend.api.data_api.fetch_data import *


def DailyReplenishmentPlan(start_date:str, end_date:str):
    DistSchemeDf = query_adam_dist_scheme_by_date_range(start_date , end_date)
    Dist_Scheme_ID = DistSchemeDf['DIST_SCHEME_ID'].tolist()
    
    # 提取DIST_SCHEME_ID和PLAN_DIST_DATE作为字典映射
    scheme_date_map = DistSchemeDf.set_index('DIST_SCHEME_ID')['PLAN_DIST_DATE'].to_dict()
    
    #获取日期范围内的配送方案明细
    Dist_Scheme_Det = []
    for id in Dist_Scheme_ID:
        res = query_adam_dist_scheme_det_by_distschemeid(id)
        Dist_Scheme_Det.append(res)
    Dist_Scheme_Det = pd.concat(Dist_Scheme_Det, ignore_index=True)
    
    # 仅保留需要的列
    columns_to_keep = [
        'REC_ORG_NO',       # 接收单位（市/县）
        'DEV_CODE',         # 设备码
        'DEV_CLS',          # 设备分类
        'DEV_CATEG',        # 设备类别
        'PLAN_DIST_NUM',    # 计划配送数量
        'DIST_SCHEME_ID',   # 方案唯一标识
        'GLOBAL_SCHEME_ID'  # 全局方案标识
    ]
    #日补库计划结果
    DaliyReplPlan = Dist_Scheme_Det[columns_to_keep]
    
    # 新增PLAN_DIST_DATE列，根据DIST_SCHEME_ID从scheme_date_map中取值
    DaliyReplPlan['PLAN_DIST_DATE'] = DaliyReplPlan['DIST_SCHEME_ID'].map(scheme_date_map)
    
    # 重命名列名
    column_mapping = {
        'REC_ORG_NO': 'REC_ORG_NO',       # 接收单位编码（市/县）
        'DEV_CLS': 'DEV_CLS',             # 设备分类
        'DEV_CATEG': 'DEV_CATEG',         # 设备类别
        'DEV_CODE': 'DEV_CODE',           # 设备码
        'PLAN_DIST_NUM': 'PLAN_IAS_NUM',  # 计划补库数量
        'PLAN_DIST_DATE': 'PRE_DATE',     # 补库日期
        'GLOBAL_SCHEME_ID': 'GLOBAL_SCHEME_ID'  # 全局方案标识
    }
    #映射并过滤DIST_SCHEME_ID列
    DaliyReplPlan = DaliyReplPlan.rename(columns=column_mapping)[list(column_mapping.values())]
    #预计库存以及唯一标识怎么写？
    #TODO
    
    # 按照日期从小到大排列，相同单位相同设备码的数据放在一起
    DaliyReplPlan = DaliyReplPlan.sort_values(
        by=['PRE_DATE', 'REC_ORG_NO', 'DEV_CODE'],
        ascending=[True, True, True]
    ).reset_index(drop=True)
    
    pass