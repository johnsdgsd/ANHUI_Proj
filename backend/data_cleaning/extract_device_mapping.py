import pandas as pd
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))


def extract_device_mapping(input_path: str, output_path: str) -> pd.DataFrame:
    """从配送数据中提取设备码映射表
    
    提取设备类型、设备类型名称、设备码、设备名称四列，
    去重后保存为xlsx文件
    
    Args:
        input_path: 输入xlsx文件路径
        output_path: 输出xlsx文件路径
        
    Returns:
        设备映射表DataFrame
    """
    df = pd.read_excel(input_path)
    
    print(f"原始数据形状: {df.shape}")
    print(f"原始列名: {df.columns.tolist()}")
    
    required_columns = ['设备类型', '设备类型名称', '设备码', '设备名称']
    
    for col in required_columns:
        if col not in df.columns:
            raise ValueError(f"输入文件缺少必要列: {col}")
    
    result = df[required_columns].drop_duplicates()
    
    result.to_excel(output_path, index=False)
    
    print(f"处理完成，数据形状: {result.shape}")
    print(f"输出文件: {output_path}")
    
    return result


if __name__ == '__main__':
    data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")
    
    input_file = os.path.join(data_dir, "配送规划-1中心配送到区县数据.xlsx")
    output_file = os.path.join(data_dir, "设备码映射表.xlsx")
    
    if not os.path.exists(input_file):
        print(f"输入文件不存在: {input_file}")
    else:
        result = extract_device_mapping(input_file, output_file)
        print("\n设备映射表预览:")
        print(result.head(20))
