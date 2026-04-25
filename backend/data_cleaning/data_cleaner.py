import logging
from typing import Any
import pandas as pd
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))


def setup_logger(name: str = __name__, level: int = logging.INFO, console: bool = True, log_file: str = None) -> logging.Logger:
    """配置日志记录器
    
    Args:
        name: 日志记录器名称
        level: 日志级别
        console: 是否打印到控制台
        log_file: 日志文件路径，如果为None则不输出到文件
        
    Returns:
        配置好的日志记录器
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.handlers.clear()
    
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    
    if console:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
    
    if log_file:
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    
    return logger


def check_missing_months(df: pd.DataFrame) -> list:
    """检查缺失月份
    
    Args:
        df: 包含设备码、地市编码、月份、数量列的DataFrame
        
    Returns:
        缺失月份信息的列表
    """
    all_months = set[int](range(1, 13))
    missing_data = []
    
    for (dev_code, city_code), group in df.groupby(['设备码', '地市编码']):
        present_months = set[str](group['月份'].tolist())
        missing_months = all_months - present_months
        if missing_months:
            missing_data.append({
                '设备码': dev_code,
                '地市编码': city_code,
                '缺失月份': sorted(missing_months)
            })
    if missing_data:
        print(f"\n警告: 共有 {len(missing_data)} 条记录缺少部分月份数据")
        print("缺失月份详情:")
    for item in missing_data[:10]:
        print(f"  设备码: {item['设备码']}, 地市编码: {item['地市编码']}, 缺失月份: {item['缺失月份']}")
    if len(missing_data) > 10:
        print(f"  ... 还有 {len(missing_data) - 10} 条记录")
    else:
        print("\n检查通过: 所有地市的所有设备码都有1-12月的数据")
    
    return missing_data


def fill_missing_months(df: pd.DataFrame) -> pd.DataFrame:
    """补齐缺失月份数据
    
    对于每个地市的每个设备码，如果缺少某些月份的数据，
    则使用其他月份数量的均值（四舍五入）进行填充
    
    Args:
        df: 包含设备码、地市编码、月份、数量列的DataFrame
        
    Returns:
        补齐后的DataFrame
    """
    result_rows = []
    
    for (dev_code, city_code), group in df.groupby(['设备码', '地市编码']):
        group = group.copy()
        present_months = set(group['月份'].tolist())
        all_months = set(range(1, 13))
        missing_months = all_months - present_months
        
        if missing_months:
            mean_quantity = round(group['数量'].mean())
            for month in missing_months:
                result_rows.append({
                    '设备码': dev_code,
                    '地市编码': city_code,
                    '月份': month,
                    '数量': mean_quantity
                })
        
        for _, row in group.iterrows():
            result_rows.append({
                '设备码': row['设备码'],
                '地市编码': row['地市编码'],
                '月份': row['月份'],
                '数量': row['数量']
            })
    
    result = pd.DataFrame(result_rows)
    result = result.sort_values(['设备码', '地市编码', '月份']).reset_index(drop=True)
    
    return result


def generate_full_combinations(df: pd.DataFrame) -> pd.DataFrame:
    """生成完整的地市×设备码×月份的笛卡尔积
    
    对于每个地市的每个设备码，生成1-12月的完整组合
    缺失的数据使用该地市该设备码的均值填充
    
    Args:
        df: 包含设备码、地市编码、月份、数量列的DataFrame
        
    Returns:
        完整的DataFrame
    """
    all_cities = df['地市编码'].unique()
    all_devices = df['设备码'].unique()
    all_months = list[int](range(1, 13))
    
    full_index = pd.MultiIndex.from_product(
        [all_cities, all_devices, all_months],
        names=['地市编码', '设备码', '月份']
    )
    full_df = pd.DataFrame(index=full_index).reset_index()
    
    merged = full_df.merge(df, on=['地市编码', '设备码', '月份'], how='left')
    
    mean_by_group = df.groupby(['地市编码', '设备码'])['数量'].mean().round(0).astype(int)
    
    def fill_quantity(row):
        if pd.isna(row['数量']):
            return mean_by_group.get((row['地市编码'], row['设备码']), 5)
        return row['数量']
    
    merged['数量'] = merged.apply(fill_quantity, axis=1).astype(int)
    
    return merged[['设备码', '地市编码', '月份', '数量']]


def print_data_statistics(df: pd.DataFrame):
    """打印数据统计信息
    
    Args:
        df: 包含设备码、地市编码、月份、数量列的DataFrame
    """
    unique_cities = df['地市编码'].nunique()
    unique_devices = df['设备码'].nunique()
    ideal_count = unique_cities * unique_devices * 12
    actual_count = len(df)
    
    print(f"\n数据统计:")
    print(f"  地市数量: {unique_cities}")
    print(f"  设备码数量: {unique_devices}")
    print(f"  理想数据量（地市×设备×12月）: {ideal_count}")
    print(f"  实际数据量: {actual_count}")
    if actual_count < ideal_count:
        print(f"  缺少数据量: {ideal_count - actual_count}")
    
    print(f"\n设备码列表（共 {unique_devices} 种）:")
    device_codes = sorted(df['设备码'].unique())
    for dev_code in device_codes:
        print(f"  {dev_code}")


def clean_and_process_data(input_path: str, price_path: str, output_path: str, console: bool = True) -> pd.DataFrame:
    """清理并处理xlsx数据
    
    处理步骤：
    1. 读取数量xlsx文件和价格xlsx文件
    2. 提取两个表格中公共的设备码
    3. 用公共设备码过滤数量数据
    4. 删除缺失设备码的行
    5. 其他列缺失数据置0
    6. 从时间列提取月份
    7. 按设备码、地市编码、月份分组，求数量平均值
    8. 生成完整的地市×设备码×月份组合
    9. 输出新的xlsx文件
    
    Args:
        input_path: 数量数据xlsx文件路径
        price_path: 价格数据xlsx文件路径（需包含设备码列）
        output_path: 输出xlsx文件路径
        console: 是否打印日志到控制台
        
    Returns:
        处理后的DataFrame
    """
    logger = setup_logger(console=console,log_file='data_cleaner.log')
    
    df = pd.read_excel(input_path)
    df_price = pd.read_excel(price_path)
    
    logger.info(f"原始数量数据形状: {df.shape}")
    logger.info(f"原始价格数据形状: {df_price.shape}")
    logger.info(f"原始数量数据列名: {df.columns.tolist()}")
    logger.info(f"原始价格数据列名: {df_price.columns.tolist()}")
    
    required_columns = ['设备码', '设备名称', '地市编码', '时间', '数量']
    
    for col in required_columns:
        if col not in df.columns:
            raise ValueError(f"输入文件缺少必要列: {col}")
    
    if '设备码' not in df_price.columns:
        raise ValueError(f"价格文件缺少必要列: 设备码")
    
    has_device_type = '设备类型' in df_price.columns
    
    df_price['设备码'] = df_price['设备码'].astype(str).str.replace('.0', '', regex=False).str.strip()
    price_dev_codes = set(df_price['设备码'].dropna().unique())
    logger.info(f"价格表设备码数量: {len(price_dev_codes)}")
    
    if has_device_type:
        device_type_map = df_price.dropna(subset=['设备码', '设备类型']).drop_duplicates('设备码').set_index('设备码')['设备类型'].to_dict()
    else:
        device_type_map = {}
    
    df = df.dropna(subset=['设备码', '时间', '地市编码'])
    df['设备码'] = df['设备码'].astype(str).str.replace('.0', '', regex=False).str.strip()
    
    original_dev_codes = set[Any](df['设备码'].unique())
    logger.info(f"数量表设备码数量: {len(original_dev_codes)}")
    
    common_dev_codes = price_dev_codes & original_dev_codes
    logger.info(f"公共设备码数量: {len(common_dev_codes)}")
    
    if len(common_dev_codes) == 0:
        raise ValueError("数量表和价格表没有公共设备码")
    
    df = df[df['设备码'].isin(common_dev_codes)]
    logger.info(f"过滤后的数据形状: {df.shape}")
    
    df['地市编码'] = pd.to_numeric(df['地市编码'], errors='coerce')
    df = df.dropna(subset=['地市编码'])
    
    logger.info(f"删除缺失数据后的形状: {df.shape}")
    
    df['设备名称'] = df['设备名称'].fillna('')
    df['数量'] = df['数量'].fillna(0)
    
    df['月份'] = df['时间'].apply(lambda x: int(x) % 100 if pd.notna(x) and x != 0 else 0)
    
    df = df[df['月份'].between(1, 12)]
    
    logger.info(f"过滤无效月份后的数据形状: {df.shape}")
    
    result = df.groupby(['设备码', '地市编码', '月份'])['数量'].mean().round(0).astype(int).reset_index()
    
    result['设备码'] = result['设备码'].astype(str)
    result['地市编码'] = result['地市编码'].astype(str)
    result['月份'] = result['月份'].astype(int)
    
    logger.info("生成完整的地市×设备码×月份组合...")
    result = generate_full_combinations(result)
    logger.info(f"生成完整组合后的数据形状: {result.shape}")
    
    result['设备码'] = result['设备码'].astype(str)
    result['地市编码'] = result['地市编码'].astype(str)
    result['月份'] = result['月份'].astype(int)
    
    missing_data = check_missing_months(result)
    
    print_data_statistics(result)
    
    if device_type_map:
        result['设备类型'] = result['设备码'].map(device_type_map)
        logger.info(f"添加设备类型后的数据形状: {result.shape}")
    
    result = result.sort_values(['设备码', '地市编码', '月份'])
    
    result.to_excel(output_path, index=False)
    
    logger.info(f"处理完成，数据形状: {result.shape}")
    logger.info(f"输出文件: {output_path}")
    
    return result


if __name__ == '__main__':
    logger = setup_logger(console=True)
    
    data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")
    
    input_file = os.path.join(data_dir, "合格品库库存优化-1不同设备在不同二级库的月使用量（安装量）分布.xlsx")
    price_file = os.path.join(data_dir, "物资价格.xlsx")
    output_file = os.path.join(data_dir, "处理后数据.xlsx")
    
    if not os.path.exists(input_file):
        logger.error(f"输入文件不存在: {input_file}")
    elif not os.path.exists(price_file):
        logger.error(f"价格文件不存在: {price_file}")
    else:
        result = clean_and_process_data(input_file, price_file, output_file, console=False)
        logger.info("处理后的数据预览:")
        print(result.head(20))
