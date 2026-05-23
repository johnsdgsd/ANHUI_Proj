"""
为运行年限表添加ID列
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


def add_id_column(input_path: str, output_path: str = None, id_column_name: str = 'ID') -> pd.DataFrame:
    """
    为Excel表格添加自增ID列
    
    Args:
        input_path: 输入文件路径
        output_path: 输出文件路径，若为None则覆盖原文件
        id_column_name: ID列的列名，默认'ID'
            
    Returns:
        处理后的DataFrame
    """
    logger.info(f"开始处理文件: {input_path}")
    
    try:
        # 读取Excel文件
        df = pd.read_excel(input_path)
        logger.info(f"成功读取文件，共 {len(df)} 条记录")
        
        # 添加自增ID列
        df.insert(0, id_column_name, range(1, len(df) + 1))
        logger.info(f"已添加 {id_column_name} 列，值从1到 {len(df)} 自增")
        
        # 如果没有指定输出路径，使用原路径
        if not output_path:
            output_path = input_path
        
        # 保存处理后的文件
        df.to_excel(output_path, index=False)
        logger.info(f"处理后的文件已保存到: {output_path}")
        
        return df
        
    except Exception as e:
        logger.error(f"处理文件失败: {str(e)}")
        raise


def add_custom_id_column(input_path: str, output_path: str = None, 
                        id_column_name: str = 'ID', prefix: str = '', 
                        suffix: str = '', padding: int = 0) -> pd.DataFrame:
    """
    为Excel表格添加自定义格式的ID列
    
    Args:
        input_path: 输入文件路径
        output_path: 输出文件路径，若为None则覆盖原文件
        id_column_name: ID列的列名，默认'ID'
        prefix: ID前缀，默认空字符串
        suffix: ID后缀，默认空字符串
        padding: 数字部分的填充长度，默认0（不填充）
            
    Returns:
        处理后的DataFrame
    """
    logger.info(f"开始处理文件: {input_path}")
    
    try:
        # 读取Excel文件
        df = pd.read_excel(input_path)
        logger.info(f"成功读取文件，共 {len(df)} 条记录")
        
        # 生成自定义格式的ID
        def generate_id(index):
            num = index + 1
            if padding > 0:
                num_str = str(num).zfill(padding)
            else:
                num_str = str(num)
            return f"{prefix}{num_str}{suffix}"
        
        df.insert(0, id_column_name, [generate_id(i) for i in range(len(df))])
        logger.info(f"已添加 {id_column_name} 列，格式为: {prefix}[数字]{suffix}")
        
        # 如果没有指定输出路径，使用原路径
        if not output_path:
            output_path = input_path
        
        # 保存处理后的文件
        df.to_excel(output_path, index=False)
        logger.info(f"处理后的文件已保存到: {output_path}")
        
        return df
        
    except Exception as e:
        logger.error(f"处理文件失败: {str(e)}")
        raise


if __name__ == '__main__':
    # 示例用法
    input_path = r'D:\WYJ\库存优化与检定排程\数据\模型样本数据统计收集-05-08\运行年限表.xlsx'
    output_path = r'D:\WYJ\库存优化与检定排程\数据\模型样本数据统计收集-05-08\运行年限表_带ID.xlsx'
    
    # 添加简单自增ID列
    add_id_column(input_path, output_path)
    
    # 或者添加自定义格式的ID列
    # add_custom_id_column(input_path, output_path, prefix='RUN_', padding=6)
    
    print("处理完成！")