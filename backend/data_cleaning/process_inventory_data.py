import os
import sys
import pandas as pd
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def process_inventory_data():
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    data_dir = os.path.join(base_dir, "data")
    
    main_file = os.path.join(data_dir, "合格品库库存优化-1不同设备在不同二级库的月使用量（安装量）分布.xlsx")
    device_mapping_file = os.path.join(data_dir, "APS_PRO_DEV_MAPPING.xlsx")
    city_mapping_file = os.path.join(data_dir, "地市映射表.xlsx")
    output_file = os.path.join(data_dir, "处理后库存数据.xlsx")
    
    print("开始处理库存数据")
    
    df = pd.read_excel(main_file)
    print(f"读取主数据文件，共 {len(df)} 条记录")
    
    if '设备码' in df.columns:
        df['设备码'] = df['设备码'].astype(str).str.replace('.0', '', regex=False)
        print("设备码格式处理完成")
    
    device_mapping_df = pd.read_excel(device_mapping_file)
    print(f"读取设备映射表，共 {len(device_mapping_df)} 条记录")
    
    if '设备码' in device_mapping_df.columns:
        device_mapping_df['设备码'] = device_mapping_df['设备码'].astype(str).str.replace('.0', '', regex=False)
    
    if '设备分类' in device_mapping_df.columns:
        device_type_map = device_mapping_df.set_index('设备码')['设备分类'].to_dict()
        df['设备分类'] = df['设备码'].map(device_type_map)
        print("设备分类列添加完成")
    
    city_mapping_df = pd.read_excel(city_mapping_file)
    print(f"读取地市映射表，共 {len(city_mapping_df)} 条记录")
    
    if '单位编码' in city_mapping_df.columns:
        city_mapping_df['单位编码'] = city_mapping_df['单位编码'].astype(str).str.strip()
    
    if '单位名称' in city_mapping_df.columns:
        city_name_map = city_mapping_df.set_index('单位编码')['单位名称'].to_dict()
        df['单位名称'] = df['地市编码'].astype(str).map(city_name_map)
        print("单位名称列添加完成")
    
    if '设备名称' in df.columns:
        df = df.drop(columns=['设备名称'])
        print("设备名称列已删除")
    
    df.to_excel(output_file, index=False)
    print(f"处理完成，结果已保存至: {output_file}")
    
    return df

def f1():
    input_excel_path = r"D:\WYJ\库存优化与检定排程\Proj\data\合格品库库存优化-1不同设备在不同二级库的月使用量（安装量）分布.xlsx"
    city_mapping_path = r"D:\WYJ\库存优化与检定排程\Proj\data\地市映射表.xlsx"
    output_excel_path = r"D:\WYJ\库存优化与检定排程\Proj\backend\data_cleaning\设备安装量表_整理后.csv"
    start_id = 1000000000
    
    df = pd.read_excel(input_excel_path, engine="openpyxl")
    print(f"读取原始数据，共 {len(df)} 条记录")
    
    if '设备名称' in df.columns:
        def get_device_type(name):
            if pd.isna(name):
                return '通信设备'
            name = str(name)
            if '电能表' in name:
                return '电能表'
            elif '互感器' in name:
                return '互感器'
            elif '采集' in name or '终端' in name:
                return '终端'
            else:
                return '通信设备'
        
        df['设备类别'] = df['设备名称'].apply(get_device_type).astype(str)
        print("设备类别列生成完成")
    
    city_df = pd.read_excel(city_mapping_path, engine="openpyxl")
    if '单位编码' in city_df.columns and '单位名称' in city_df.columns:
        city_df['单位编码'] = city_df['单位编码'].astype(str).str.strip()
        city_name_map = city_df.set_index('单位编码')['单位名称'].to_dict()
        if '地市编码' in df.columns:
            df['单位名称'] = df['地市编码'].astype(str).map(city_name_map)
            print("单位名称列生成完成")
    
    if '设备码' in df.columns:
        df['设备码'] = df['设备码'].astype(str).str.replace('.0', '', regex=False)
    
    rename_map = {
        "时间": "STAT_MONTH",
        "地市编码": "UNIT_CODE",
        "单位名称": "UNIT_NAME",
        "设备类别": "DEVICE_TYPE",
        "设备码": "DEVICE_CODE",
        "数量": "INSTALL_NUM"
    }
    df = df.rename(columns=rename_map)
    
    df["INSTALL_ID"] = np.arange(start_id, start_id + len(df))
    
    target_order = [
        "INSTALL_ID",
        "STAT_MONTH",
        "UNIT_CODE",
        "UNIT_NAME",
        "DEVICE_TYPE",
        "DEVICE_CODE",
        "INSTALL_NUM"
    ]
    df = df[target_order]
    
    df.to_csv(output_excel_path, index=False, encoding='utf-8-sig')
    
    print(f"处理完成，共 {len(df)} 条记录")
    print(f"结果已保存至: {output_excel_path}")

if __name__ == '__main__':
    f1()
    # process_inventory_data()
