"""
生成HIGH_NUM列数据
用于为库存信息表随机生成HIGH_NUM列的值（0-100之间）
"""
import pandas as pd
import numpy as np
from datetime import datetime
import logging

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)


def generate_high_num(input_path: str, output_path: str = None) -> pd.DataFrame:
    """
    为库存信息表随机生成HIGH_NUM列的值
    
    Args:
        input_path: 输入文件路径（库存信息表.xlsx）
        output_path: 输出文件路径，若为None则覆盖原文件
            
    Returns:
        处理后的DataFrame
    """
    logger.info(f"开始处理库存信息表: {input_path}")
    
    try:
        # 读取Excel文件
        df = pd.read_excel(input_path)
        logger.info(f"成功读取文件，共 {len(df)} 条记录")
        
        # 随机生成HIGH_NUM列的值（0-100之间的整数）
        np.random.seed(int(datetime.now().strftime('%Y%m%d')))  # 设置随机种子，确保结果可复现
        df['HIGH_NUM'] = np.random.randint(0, 101, size=len(df))
        logger.info(f"已为HIGH_NUM列生成随机值（0-100之间）")
        
        # 将DEV_CLS、DEV_CATEG和OLD_NEW_FLAG列改为字符串并在开头加0
        def format_column(value):
            """格式化列值，转换为字符串并在开头加0"""
            if pd.isna(value) or str(value).strip() == '':
                return '00'
            str_value = str(value).strip()
            if str_value.isdigit():
                return str_value.zfill(2)
            return str_value
        
        df['DEV_CLS'] = df['DEV_CLS'].apply(format_column)
        df['DEV_CATEG'] = df['DEV_CATEG'].apply(format_column)
        df['OLD_NEW_FLAG'] = df['OLD_NEW_FLAG'].apply(format_column)
        logger.info(f"已格式化DEV_CLS、DEV_CATEG和OLD_NEW_FLAG列，转换为字符串并在开头加0")
        
        # 处理STOCK_COUNT_SAMPLE_ID列，按顺序自增
        df['STOCK_COUNT_SAMPLE_ID'] = range(1, len(df) + 1)
        logger.info(f"已处理STOCK_COUNT_SAMPLE_ID列，按顺序自增")
        
        # 如果没有指定输出路径，使用原路径
        if not output_path:
            output_path = input_path
        
        # 保存处理后的文件
        df.to_excel(output_path, index=False)
        logger.info(f"处理后的文件已保存到: {output_path}")
        
        return df
        
    except Exception as e:
        logger.error(f"处理库存信息表失败: {str(e)}")
        raise


def generate_high_num_with_distribution(input_path: str, output_path: str = None, 
                                       distribution: str = 'uniform') -> pd.DataFrame:
    """
    为库存信息表生成HIGH_NUM列的值，支持不同分布
    
    Args:
        input_path: 输入文件路径（库存信息表.xlsx）
        output_path: 输出文件路径，若为None则覆盖原文件
        distribution: 分布类型，可选 'uniform'（均匀分布）、'normal'（正态分布）、'poisson'（泊松分布）
            
    Returns:
        处理后的DataFrame
    """
    logger.info(f"开始处理库存信息表，使用 {distribution} 分布生成HIGH_NUM值")
    
    try:
        # 读取Excel文件
        df = pd.read_excel(input_path)
        logger.info(f"成功读取文件，共 {len(df)} 条记录")
        
        # 设置随机种子
        np.random.seed(int(datetime.now().strftime('%Y%m%d')))
        
        # 根据分布类型生成数据
        if distribution == 'uniform':
            # 均匀分布（0-100）
            df['HIGH_NUM'] = np.random.randint(0, 101, size=len(df))
        elif distribution == 'normal':
            # 正态分布（均值50，标准差15，截断到0-100）
            values = np.random.normal(50, 15, size=len(df))
            df['HIGH_NUM'] = np.clip(values, 0, 100).astype(int)
        elif distribution == 'poisson':
            # 泊松分布（lambda=50，截断到0-100）
            values = np.random.poisson(50, size=len(df))
            df['HIGH_NUM'] = np.clip(values, 0, 100)
        else:
            raise ValueError(f"不支持的分布类型: {distribution}")
        
        logger.info(f"已为HIGH_NUM列生成 {distribution} 分布随机值（0-100之间）")
        
        # 如果没有指定输出路径，使用原路径
        if not output_path:
            output_path = input_path
        
        # 保存处理后的文件
        df.to_excel(output_path, index=False)
        logger.info(f"处理后的文件已保存到: {output_path}")
        
        return df
        
    except Exception as e:
        logger.error(f"处理库存信息表失败: {str(e)}")
        raise


if __name__ == '__main__':
    # 示例用法
    input_path = r'D:\WYJ\hengxiang\库存信息表.xlsx'
    output_path = r'D:\WYJ\hengxiang\库存信息表_带HIGH_NUM.xlsx'
    
    # 使用均匀分布生成
    generate_high_num(input_path, output_path)
    
    # 或者使用正态分布生成
    # generate_high_num_with_distribution(input_path, output_path, distribution='normal')
    
    print("处理完成！")