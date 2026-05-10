"""
保存配送计划数据到文件
"""
import pandas as pd
import os


def create_and_save_delivery_plan(filename: str = "delivery_plan.xlsx"):
    """
    构造配送计划DataFrame并保存到data_cleaning目录
    
    DataFrame格式：
        PathInd  VeType       Price  PlanPath        DeNum
    0       86       1  242.593333      [86]        [9.0]
    1      105       1  340.916255   [1, 38]   [3.0, 4.0]
    2      529       1  382.364800  [14, 61]   [9.0, 6.0]
    3        6       2   18.909478       [6]       [11.0]
    4      396       2  198.445193  [10, 16]   [5.0, 5.0]
    5     1086       2  248.054513  [35, 83]  [3.0, 12.0]
    
    Args:
        filename: 保存的文件名，默认为"delivery_plan.xlsx"
        
    Returns:
        str: 保存的文件路径
    """
    # 构造配送计划数据
    data = {
        'PathInd': [86, 105, 529, 6, 396, 1086],
        'VeType': [1, 1, 1, 2, 2, 2],
        'Price': [242.593333, 340.916255, 382.364800, 18.909478, 198.445193, 248.054513],
        'PlanPath': [[86], [1, 38], [14, 61], [6], [10, 16], [35, 83]],
        'DeNum': [[9.0], [3.0, 4.0], [9.0, 6.0], [11.0], [5.0, 5.0], [3.0, 12.0]]
    }
    
    df = pd.DataFrame(data)
    
    # 获取当前文件所在目录
    current_dir = os.path.dirname(os.path.abspath(__file__))
    file_path = os.path.join(current_dir, filename)
    
    # 保存为Excel文件
    df.to_excel(file_path, index=False)
    print(f"配送计划数据已保存到: {file_path}")
    print(f"数据预览:\n{df}")
    
    return file_path


def create_and_save_delivery_plan_csv(filename: str = "delivery_plan.csv"):
    """
    构造配送计划DataFrame并保存到data_cleaning目录（CSV格式）
    
    Args:
        filename: 保存的文件名，默认为"delivery_plan.csv"
        
    Returns:
        str: 保存的文件路径
    """
    # 构造配送计划数据
    data = {
        'PathInd': [86, 105, 529, 6, 396, 1086],
        'VeType': [1, 1, 1, 2, 2, 2],
        'Price': [242.593333, 340.916255, 382.364800, 18.909478, 198.445193, 248.054513],
        'PlanPath': [[86], [1, 38], [14, 61], [6], [10, 16], [35, 83]],
        'DeNum': [[9.0], [3.0, 4.0], [9.0, 6.0], [11.0], [5.0, 5.0], [3.0, 12.0]]
    }
    
    df = pd.DataFrame(data)
    
    # 获取当前文件所在目录
    current_dir = os.path.dirname(os.path.abspath(__file__))
    file_path = os.path.join(current_dir, filename)
    
    # 保存为CSV文件
    df.to_csv(file_path, index=False, encoding='utf-8-sig')
    print(f"配送计划数据已保存到: {file_path}")
    print(f"数据预览:\n{df}")
    
    return file_path


if __name__ == "__main__":
    # 直接运行脚本时创建并保存配送计划
    create_and_save_delivery_plan()
