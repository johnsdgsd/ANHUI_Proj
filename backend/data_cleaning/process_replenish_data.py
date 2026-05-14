import pandas as pd
import os

def process_replenish_data():
    """
    读取补货计划数据、补货数量数据和设备检定时长数据，并映射为目标格式
    """
    # 1. 读取基准库存信息
    BaseStockPath = r'C:\Users\Administrator\Desktop\NARI_APS_INVENTORY_REPLENISH.xlsx'
    print(f"读取基准库存信息: {BaseStockPath}")
    BaseStock = pd.read_excel(BaseStockPath)
    print(f"基准库存信息共 {len(BaseStock)} 行")
    
    # 2. 读取补货量信息
    OrderPath = r'C:\Users\Administrator\Desktop\NARI_APS_INVENTORY_REPLENISH_QTY.xlsx'
    print(f"\n读取补货量信息: {OrderPath}")
    Order = pd.read_excel(OrderPath)
    print(f"补货量信息共 {len(Order)} 行")

    # 3. 读取规格设备码信息
    SpecPath = r'D:\WYJ\库存优化与检定排程\数据\模型样本数据统计收集-05-08\规格设备码信息-不同设备的检定时长.xlsx'
    print(f"\n读取规格设备码信息: {SpecPath}")
    Spec = pd.read_excel(SpecPath)
    print(f"规格设备码信息共 {len(Spec)} 行")
    
    # 4. 创建设备分类和类别映射（后面会重新创建带字符串键的映射）
    
    # 5. 映射基准库存信息为目标格式
    mapped_df = pd.DataFrame()
    mapped_df['STOCK_MONTH_LIMT_PRE_ID'] = BaseStock['THRESHOLD_ID'].astype(str)
    mapped_df['PRE_YEAR'] = BaseStock['STAT_MONTH'].astype(str).str[:4]
    mapped_df['PRE_MONTH'] = BaseStock['STAT_MONTH'].astype(str).str[4:6]
    mapped_df['ORG_NO'] = BaseStock['UNIT_CODE'].astype(str)
    mapped_df['DEV_CODE'] = BaseStock['DEVICE_CODE'].astype(str)
    mapped_df['BASE_LIMT'] = BaseStock['BASE_STOCK_NUM']
    mapped_df['PRE_TIME'] = '20260509'
    mapped_df['GLOBAL_SCHEME_ID'] = BaseStock['TAG'].astype(str)
    
    # 确保DEV_CODE为字符串类型
    mapped_df['DEV_CODE'] = mapped_df['DEV_CODE'].astype(str).str.replace(r'\.0$', '', regex=True)
    
    # 创建设备分类和类别映射（确保键为字符串）
    dev_cls_mapping = Spec.set_index(Spec['DEV_CODE'].astype(str).str.replace(r'\.0$', '', regex=True))['DEV_CLS'].to_dict()
    dev_categ_mapping = Spec.set_index(Spec['DEV_CODE'].astype(str).str.replace(r'\.0$', '', regex=True))['DEV_CATEG'].to_dict()
    
    # 映射设备分类和类别
    mapped_df['DEV_CLS'] = mapped_df['DEV_CODE'].map(dev_cls_mapping)
    mapped_df['DEV_CATEG'] = mapped_df['DEV_CODE'].map(dev_categ_mapping)
    
    # 统计没有映射到的数据
    no_mapping_cls = mapped_df['DEV_CLS'].isna().sum()
    no_mapping_categ = mapped_df['DEV_CATEG'].isna().sum()
    no_mapping_total = mapped_df[mapped_df['DEV_CLS'].isna() & mapped_df['DEV_CATEG'].isna()].shape[0]
    
    print(f"\n基准库存信息映射统计:")
    print(f"总记录数: {len(mapped_df)}")
    print(f"没有映射到设备分类的记录数: {no_mapping_cls}")
    print(f"没有映射到设备类别的记录数: {no_mapping_categ}")
    print(f"既没有映射到设备分类也没有映射到设备类别的记录数: {no_mapping_total}")
    
    # 填充空值
    mapped_df['DEV_CLS'] = mapped_df['DEV_CLS'].fillna('')
    mapped_df['DEV_CATEG'] = mapped_df['DEV_CATEG'].fillna('')
    
    # 确保为2位字符串，格式为01、02这样的形式
    mapped_df['DEV_CLS'] = mapped_df['DEV_CLS'].apply(
        lambda x: f"{int(float(x)):02d}" if pd.notna(x) and str(x).strip().replace('.', '').isdigit() else x
    )
    mapped_df['DEV_CATEG'] = mapped_df['DEV_CATEG'].apply(
        lambda x: f"{int(float(x)):02d}" if pd.notna(x) and str(x).strip().replace('.', '').isdigit() else x
    )
    
    # 6. 保存处理后的数据
        # 调整列顺序
    mapped_df = mapped_df[[
        'STOCK_MONTH_LIMT_PRE_ID',
        'PRE_YEAR',
        'PRE_MONTH',
        'ORG_NO',
        'DEV_CLS',
        'DEV_CATEG',
        'DEV_CODE',
        'BASE_LIMT',
        'PRE_TIME',
        'GLOBAL_SCHEME_ID'
    ]]
    
    # 保存映射后的基准库存信息
    mapped_output_path = r'C:\Users\Administrator\Desktop\hengxiang\ADAM_STOCK_MONTH_LIMT_PRE.xlsx'
    mapped_df.to_excel(mapped_output_path, index=False)
    print(f"\n映射后的基准库存信息已保存到: {mapped_output_path}")
    
    # 7. 处理补货量信息为目标格式
    order_mapped_df = pd.DataFrame()
    order_mapped_df['PLAN_MONTH_IAS_PRE_ID'] = Order['REPLENISH_QTY_ID'].astype(str)
    order_mapped_df['PRE_YEAR'] = Order['STAT_MONTH'].astype(str).str[:4]
    order_mapped_df['PRE_MONTH'] = Order['STAT_MONTH'].astype(str).str[4:6]
    order_mapped_df['REC_ORG_NO'] = Order['UNIT_CODE'].astype(str)
    order_mapped_df['DEV_CODE'] = Order['DEVICE_CODE'].astype(str)
    order_mapped_df['PLAN_IAS_NUM'] = Order['REPLENISH_NUM']
    order_mapped_df['GLOBAL_SCHEME_ID'] = Order['TAG'].astype(str)
    
    # 确保DEV_CODE为字符串类型
    order_mapped_df['DEV_CODE'] = order_mapped_df['DEV_CODE'].str.replace(r'\.0$', '', regex=True)
    
    # 映射设备分类和类别
    order_mapped_df['DEV_CLS'] = order_mapped_df['DEV_CODE'].map(dev_cls_mapping)
    order_mapped_df['DEV_CATEG'] = order_mapped_df['DEV_CODE'].map(dev_categ_mapping)
    
    # 统计没有映射到的数据
    no_mapping_cls = order_mapped_df['DEV_CLS'].isna().sum()
    no_mapping_categ = order_mapped_df['DEV_CATEG'].isna().sum()
    no_mapping_total = order_mapped_df[order_mapped_df['DEV_CLS'].isna() & order_mapped_df['DEV_CATEG'].isna()].shape[0]
    
    print(f"\n补货量信息映射统计:")
    print(f"总记录数: {len(order_mapped_df)}")
    print(f"没有映射到设备分类的记录数: {no_mapping_cls}")
    print(f"没有映射到设备类别的记录数: {no_mapping_categ}")
    print(f"既没有映射到设备分类也没有映射到设备类别的记录数: {no_mapping_total}")
    
    # 填充空值
    order_mapped_df['DEV_CLS'] = order_mapped_df['DEV_CLS'].fillna('')
    order_mapped_df['DEV_CATEG'] = order_mapped_df['DEV_CATEG'].fillna('')
    
    # 确保为2位字符串，格式为01、02这样的形式
    order_mapped_df['DEV_CLS'] = order_mapped_df['DEV_CLS'].apply(
        lambda x: f"{int(float(x)):02d}" if pd.notna(x) and str(x).strip().replace('.', '').isdigit() else x
    )
    order_mapped_df['DEV_CATEG'] = order_mapped_df['DEV_CATEG'].apply(
        lambda x: f"{int(float(x)):02d}" if pd.notna(x) and str(x).strip().replace('.', '').isdigit() else x
    )
    
    # 调整列顺序
    order_mapped_df = order_mapped_df[[
        'PLAN_MONTH_IAS_PRE_ID',
        'PRE_YEAR',
        'PRE_MONTH',
        'REC_ORG_NO',
        'DEV_CLS',
        'DEV_CATEG',
        'DEV_CODE',
        'PLAN_IAS_NUM',
        'GLOBAL_SCHEME_ID'
    ]]
    
    # 保存映射后的补货量信息
    order_output_path = r'C:\Users\Administrator\Desktop\hengxiang\ADAM_PLAN_MONTH_IAS_PRE.xlsx'
    order_mapped_df.to_excel(order_output_path, index=False)
    print(f"\n映射后的补货量信息已保存到: {order_output_path}")
    
    return mapped_df, order_mapped_df, Spec


if __name__ == "__main__":
    mapped_df, order_mapped_df, Spec = process_replenish_data()
    print("\n数据预览:")
    print("映射后的基准库存信息:")
    print(mapped_df.head())
    print("\n映射后的补货量信息:")
    print(order_mapped_df.head())
    print("\n设备规格数据:")
    print(Spec.head())