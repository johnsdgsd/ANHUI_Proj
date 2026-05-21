"""
库存优化运行脚本
封装完整的库存优化流程
"""

import pandas as pd
from datetime import datetime
from backend.inventory_optimization.optimizer import InventoryOptimizer
from backend.inventory_optimization.warehouse import CentralWarehouse


def get_device_install_data(year:str):
    """
    获取某一年所有月份所有单位设备码的月度预测信息
    """
    from backend.api.data_api.fetch_data import (
        query_adam_yqm_dmd_pre_by_year,
        query_adam_spec_code_config,query_adam_del_site_conf)
    
    # 获取基础数据
    df = query_adam_yqm_dmd_pre_by_year(year)
    stat = '01' #暂时获取初始状态的数据
    pre_type = '03' #获取月度预测信息
    col = 'PRE_NUM'

    # 筛选数据：STAT列为01，PRE_TYPE列为03
    df = df[(df['STAT'] == stat) & (df['PRE_TYPE'] == pre_type)]
    # 提取指定列
    df = df[['PRE_YEAR', 'PRE_MONTH', 'ORG_NO', 'DEV_CODE', col]]
    
    # 按年、月、单位编码、设备码分组，汇总所有业务类型的数量
    df = df.groupby(
        ['PRE_YEAR', 'PRE_MONTH', 'ORG_NO', 'DEV_CODE'],
        as_index=False
    )[col].sum()
    
    # 获取设备类型配置
    spec_df = query_adam_spec_code_config()
    spec_dict = spec_df.set_index('DEV_CODE')['DEV_TYPE'].to_dict()
    
    # 获取站点信息
    site_df = query_adam_del_site_conf()
    site_dict = site_df.set_index('ORG_NO')['ORG_NAME'].to_dict()
    
    # 转换为目标格式
    result_df = df.copy()
    result_df['INSTALL_ID'] = range(1, len(result_df) + 1)
    result_df['STAT_MONTH'] = result_df['PRE_YEAR'] + result_df['PRE_MONTH']
    result_df['UNIT_CODE'] = result_df['ORG_NO']
    result_df['UNIT_NAME'] = result_df['ORG_NO'].map(site_dict)
    result_df['DEVICE_TYPE'] = result_df['DEV_CODE'].map(spec_dict)
    result_df['DEVICE_CODE'] = result_df['DEV_CODE']
    result_df['INSTALL_NUM'] = result_df[col]
    
    # 保留目标列
    result_df = result_df[['INSTALL_ID', 'STAT_MONTH', 'UNIT_CODE', 'UNIT_NAME', 'DEVICE_TYPE', 'DEVICE_CODE', 'INSTALL_NUM']]
    
    # 填充缺失值
    result_df['UNIT_NAME'] = result_df['UNIT_NAME'].fillna('未知单位')
    result_df['DEVICE_TYPE'] = result_df['DEVICE_TYPE'].fillna('未知设备类型')
    # 重置索引
    result_df = result_df.reset_index(drop=True)
    
    return result_df

  

def run_optimization_from_api(
    init_stock_month: int,
    tag:str,
    n_iter: int = 1,
    pop_size: int = 200,
    epsilon: float = 0.95,
    n_processor: int = 10
):
    """从API数据源运行库存优化
    
    从数据API获取数据后运行完整的库存优化流程
    
    Args:
        init_stock_month: 初始库存月份 (YYYYMM)
        install_start_month: 安装量数据起始月份 (YYYYMM)
        install_end_month: 安装量数据结束月份 (YYYYMM)
        n_iter: 遗传算法迭代次数
        pop_size: 种群大小
        epsilon: 目标满足率
        n_processor: 并行处理器数量
        tag: 全局标识
        
    Returns:
         优化结果
    """
    # 在函数内部导入，避免循环导入
    from backend.api.data_api.fetch_data import (
        query_aps_inventory_init_stock_by_month,
        query_device_install_data_by_month_range,
        query_adam_pre_range_info,
        insert_into_aps_inventory_fulfill_rate,
        insert_into_aps_inventory_replenish,
        insert_into_aps_inventory_replenish_qty,
        query_adam_qua_stock_sample_by_year_month,
        query_adam_pend_stock_sample_by_year_month,
        query_adam_org_stock_sample_by_month,
        query_adam_spec_code_config
    )
    
    try:
        year = str(init_stock_month // 100)
        # 月份减1
        month = f"{init_stock_month % 100 -1:02d}"
        # 获取初始库存数据
        init_stock = query_adam_org_stock_sample_by_month(str(init_stock_month))
        #合格品库存
        qua_sto = query_adam_qua_stock_sample_by_year_month(year,month)
        #不合格品库存
        unqua_sto = query_adam_pend_stock_sample_by_year_month(year,month)
        # 获取设备类别配置
        spec_df = query_adam_spec_code_config()
        spec_dict = spec_df.set_index('DEV_CODE')['DEV_CLS'].to_dict()
        # 添加设备类别列
        unqua_sto['DEV_CLS'] = unqua_sto['DEV_CODE'].map(spec_dict)

        #安装量数据-获取2026年所有需求预测信息
        install_df = get_device_install_data(year)
        #预测范围中的物资价格
        item_cost = query_adam_pre_range_info()
        
        # 创建库存优化器
        optimizer = InventoryOptimizer(init_stock)
        # 获取需求分布
        optimizer.get_distributions_from_install_data(install_df)
        
        # 初始化地方库
        optimizer.set_local_warehouses_from_dataframe()
        #初始化中心库
        optimizer.central_warehouse = CentralWarehouse()
        optimizer.central_warehouse.city_name = '中心库'
        optimizer.central_warehouse.initialize_from_sto_data(qua_sto,unqua_sto)
        optimizer.central_warehouse.update_items_from_local_warehouses(optimizer.local_warehouses)
        # 设置地方库和中心库物资成本
        optimizer.set_item_costs_from_dataframe(item_cost)
        
        # 运行优化
        best_solution, best_cost = optimizer.optimize_alpha(
            n_iter=n_iter,
            pop_size=pop_size,
            epsilon=epsilon,
            n_processor=n_processor
        )

        alpha_dict = optimizer._build_alpha_dict(best_solution,optimizer.context)
        optimizer.set_alpha(alpha_dict,optimizer.context)

        # 构建满足率结果DataFrame
        result_data = []
        for warehouse in optimizer.context.local_warehouses:
            for item_key, item in warehouse.items.items():
                result_data.append({
                    'STAT_MONTH': init_stock_month,
                    'UNIT_CODE': warehouse.city_code,
                    'UNIT_NAME': warehouse.city_name,
                    'DEVICE_TYPE': item.cls,
                    'DEVICE_CODE': item.dev_code,
                    'TAG': tag,
                    'FULFILL_RATE': round(item.alpha, 4)
                })
        alpha_result_df = pd.DataFrame(result_data)
        insert_into_aps_inventory_fulfill_rate(alpha_result_df)
        #构建库存阈值上限结果以及补货量结果
        month_id = init_stock_month % 100
        demand_result_data = []
        order_result_data = []
        # 生成唯一标识
        stock_id = 1
        for local_warehouse in optimizer.context.local_warehouses:
            for item_key, item in local_warehouse.items.items():
                demand = item.generate_demand_quantile(month_id)
                order = max(0,demand - item.initial_inventory)
                # 拆分年份和月份
                pre_year = init_stock_month[:4]
                pre_month = init_stock_month[4:6]
                # 获取设备分类和类别（从规格设备码映射字典获取）
                from backend.api.data_api.fetch_data import query_adam_spec_code_config
                spec_df = query_adam_spec_code_config()
                spec_dict = spec_df.set_index('DEV_CODE')[['DEV_CLS', 'DEV_CATEG']].to_dict('index')
                
                device_info = spec_dict.get(item.dev_code, {})
                dev_cls = device_info.get('DEV_CLS', '00').zfill(2)
                dev_categ = device_info.get('DEV_CATEG', '00_00')
                
                demand_result_data.append(
                    {
                        'STOCK_MONTH_LIMIT_PRE_ID': stock_id,
                        'PRE_YEAR': pre_year,
                        'PRE_MONTH': pre_month,
                        'ORG_NO': local_warehouse.city_code,
                        'DEV_CLS': dev_cls,
                        'DEV_CATEG': dev_categ,
                        'DEV_CODE': item.dev_code,
                        'BASE_LIMIT': int(demand),
                        'PRE_TIME': datetime.now().strftime('%Y-%m-%d'),
                        'GLOBAL_SCHEME_ID': tag 
                    }
                )      
                order_result_data.append(
                    {
                        'PLAN_MONTH_IAS_PRE_ID': stock_id,
                        'PRE_YEAR': pre_year,
                        'PRE_MONTH': pre_month,
                        'REC_ORG_NO': local_warehouse.city_code,
                        'DEV_CLS': dev_cls,
                        'DEV_CATEG': dev_categ,
                        'DEV_CODE': item.dev_code,
                        'PLAN_IAS_NUM': int(order),
                        'GLOBAL_SCHEME_ID': tag
                    }
                )
                stock_id+=1
        

        InventoryThreshold = pd.DataFrame(demand_result_data)
        InventoryOrder = pd.DataFrame(order_result_data)

        return InventoryThreshold,InventoryOrder


   
    except Exception as e:
        raise
