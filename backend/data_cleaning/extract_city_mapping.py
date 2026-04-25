import pandas as pd
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))


def extract_city_mapping(input_path: str, output_path: str) -> pd.DataFrame:
    """从数据中提取地市名称和地市编码映射表
    
    Args:
        input_path: 输入xlsx文件路径
        output_path: 输出xlsx文件路径
        
    Returns:
        地市映射表DataFrame
    """
    df = pd.read_excel(input_path)
    
    print(f"原始数据形状: {df.shape}")
    print(f"原始列名: {df.columns.tolist()}")
    
    required_columns = ['单位编码', '单位名称']
    
    for col in required_columns:
        if col not in df.columns:
            raise ValueError(f"输入文件缺少必要列: {col}")
    
    result = df[required_columns].copy()
    
    result['单位编码'] = result['单位编码'].astype(str).str.strip()
    result['单位名称'] = result['单位名称'].astype(str).str.strip()
    
    result = result.drop_duplicates().sort_values('单位编码').reset_index(drop=True)
    
    result.to_excel(output_path, index=False)
    
    return result


if __name__ == '__main__':
    data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")
    
    input_file = os.path.join(data_dir, "市县储位总箱数.xlsx")
    output_file = os.path.join(data_dir, "地市映射表.xlsx")
    
    if not os.path.exists(input_file):
        print(f"输入文件不存在: {input_file}")
    else:
        result = extract_city_mapping(input_file, output_file)
        print("\n地市映射表预览:")
        print(result)
