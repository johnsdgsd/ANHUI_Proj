"""
global_optimization模块日志记录器
提供模块级别的全局单例日志记录功能
"""
import logging
import os
from datetime import datetime

# 配置日志记录器
logger = logging.getLogger('global_optimization')
logger.setLevel(logging.INFO)

# 避免重复添加处理器
if not logger.handlers:
    # 创建格式器
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # 添加控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # # 添加文件处理器
    # log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'logs')
    # os.makedirs(log_dir, exist_ok=True)
    # log_file = os.path.join(log_dir, f'global_optimization_{datetime.now().strftime("%Y%m%d")}.log')
    
    # file_handler = logging.FileHandler(log_file, encoding='utf-8')
    # file_handler.setFormatter(formatter)
    # logger.addHandler(file_handler)
