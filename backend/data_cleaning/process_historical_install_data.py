"""
处理历史每月安装数据
从Excel文件中读取数据并进行筛选处理
"""
import pandas as pd
import os


def process_historical_install_data(
    install_data_path: str = r'D:\WYJ\库存优化与检定排程\数据\模型样本数据统计收集-05-08\历史每月安装数据表.xlsx',
    device_code_path: str = r'D:\WYJ\库存优化与检定排程\数据\模型样本数据统计收集-05-08\规格设备码信息-不同设备的检定时长.xlsx',
    output_filename: str = "filtered_install_data.xlsx"
):
    """
    处理历史安装数据：
    1. 从安装数据表中读取数据，筛选安装日期列为202105-202505的数据
    2. 从设备码信息表中读取DEV_CODE列
    3. 筛选安装数据表中设备码编号列在DEV_CODE列中的数据
    
    Args:
        install_data_path: 历史安装数据表路径
        device_code_path: 设备码信息表路径
        output_filename: 输出文件名
        
    Returns:
        pd.DataFrame: 筛选后的数据
    """
    print("=== 开始处理历史安装数据 ===")
    
    # 读取历史安装数据表
    print(f"读取安装数据: {install_data_path}")
    install_df = pd.read_excel(install_data_path)
    print(f"原始安装数据共 {len(install_df)} 行")
    print(f"列名: {install_df.columns.tolist()}")
    
    # 筛选安装日期列为所有五月份的数据（202105, 202205, 202305, 202405, 202505）
    if '安装日期' in install_df.columns:
        # 确保安装日期列为字符串类型
        install_df['安装日期'] = install_df['安装日期'].astype(str)
        # 定义所有五月份的日期
        may_dates = ['202105', '202205', '202305', '202405', '202505']
        # 筛选五月份的数据
        install_df = install_df[install_df['安装日期'].isin(may_dates)]
        print(f"筛选五月份数据后剩余 {len(install_df)} 行")
        print(f"包含的五月份: {install_df['安装日期'].unique().tolist()}")
    else:
        print("警告：安装数据表中未找到'安装日期'列")
    
    # 读取新旧设备码映射表
    mapping_file_path = r'D:\WYJ\库存优化与检定排程\数据\模型样本数据统计收集-05-08\新旧设备码映射.xlsx'
    print(f"\n读取设备码映射表: {mapping_file_path}")
    try:
        mapping_df = pd.read_excel(mapping_file_path)
        if '设备码' in mapping_df.columns and '新设备码' in mapping_df.columns:
            # 确保设备码为字符串类型
            mapping_df['设备码'] = mapping_df['设备码'].astype(str).str.replace(r'\.0$', '', regex=True)
            mapping_df['新设备码'] = mapping_df['新设备码'].astype(str).str.replace(r'\.0$', '', regex=True)
            # 创建设备码映射字典
            device_mapping = mapping_df.set_index('设备码')['新设备码'].to_dict()
            print(f"设备码映射表共 {len(device_mapping)} 条记录")
            
            # 映射install_df的设备码编号列
            if '设备码编号' in install_df.columns:
                original_count = len(install_df)
                # 保存原始设备码
                install_df['设备码编号_原始'] = install_df['设备码编号'].astype(str).str.replace(r'\.0$', '', regex=True)
                # 映射为新设备码
                install_df['设备码编号'] = install_df['设备码编号_原始'].map(device_mapping)
                # 没有对应新设备码的保留原设备码
                install_df['设备码编号'] = install_df['设备码编号'].fillna(install_df['设备码编号_原始'])
                mapped_count = (install_df['设备码编号'] != install_df['设备码编号_原始']).sum()
                install_df = install_df.drop(columns=['设备码编号_原始'])
                print(f"设备码映射完成：共 {original_count} 行，其中 {mapped_count} 行映射为新设备码")
        else:
            print("警告：映射表中未找到'设备码'或'新设备码'列，跳过映射")
    except FileNotFoundError:
        print(f"警告：映射文件未找到 {mapping_file_path}，跳过设备码映射")
    except Exception as e:
        print(f"警告：设备码映射失败：{e}")
    
    # 读取设备码信息表
    print(f"\n读取设备码信息: {device_code_path}")
    device_df = pd.read_excel(device_code_path)
    print(f"设备码信息表共 {len(device_df)} 行")
    print(f"列名: {device_df.columns.tolist()}")
    
    # 获取DEV_CODE列
    if 'DEV_CODE' in device_df.columns:
        dev_code_list = device_df['DEV_CODE'].unique().astype(str)
        print(f"设备码列表共 {len(dev_code_list)} 个")
    else:
        raise ValueError("设备码信息表中未找到'DEV_CODE'列")
    
    # 筛选安装数据表中设备码编号列在DEV_CODE列中的数据
    if '设备码编号' in install_df.columns:
        # 确保设备码编号列为字符串类型，并去掉'.0'后缀
        install_df['设备码编号'] = install_df['设备码编号'].astype(str).str.replace(r'\.0$', '', regex=True)
        dev_code_list = [str(code) for code in dev_code_list]
        
        # 记录筛选前的行数
        before_filter = len(install_df)
        # 筛选
        filtered_df = install_df[install_df['设备码编号'].isin(dev_code_list)]
        after_filter = len(filtered_df)
        deleted_rows = before_filter - after_filter
        
        print(f"\n筛选设备码前 {before_filter} 行，筛选后 {after_filter} 行，删减了 {deleted_rows} 行")
        
        # 提取单位编码、设备码编号、安装数量三列
        # 按单位编码和设备码编号分组，计算安装数量的均值
        if all(col in filtered_df.columns for col in ['单位编码', '设备码编号', '安装数量']):
            # 确保安装数量为数值类型
            filtered_df['安装数量'] = pd.to_numeric(filtered_df['安装数量'], errors='coerce')
            
            import numpy as np
            
            # 按单位编码和设备码编号分组，计算平均安装数量并向上取整
            avg_install_df = filtered_df.groupby(
                ['单位编码', '设备码编号'],
                as_index=False
            )['安装数量'].mean()
            
            # 向上取整
            avg_install_df['安装数量'] = np.ceil(avg_install_df['安装数量'])
            
            # 重命名列
            avg_install_df = avg_install_df.rename(columns={'安装数量': '平均安装数量'})
            
            print(f"汇总后数据共 {len(avg_install_df)} 行（按单位编码和设备码编号分组求均值）")
            print(f"汇总数据预览:\n{avg_install_df.head()}")
            
        else:
            missing_cols = [col for col in ['单位编码', '设备码编号', '安装数量'] if col not in filtered_df.columns]
            print(f"警告：缺少以下列，无法汇总: {missing_cols}")
            avg_install_df = filtered_df
    else:
        raise ValueError("安装数据表中未找到'设备码编号'列")
    
    
    return avg_install_df


# if __name__ == "__main__":
#     # 直接运行脚本时处理数据
#     result_df = process_historical_install_data()
#     print(f"\n数据预览:")
#     print(result_df.head())
