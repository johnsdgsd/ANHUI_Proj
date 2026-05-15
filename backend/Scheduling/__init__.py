"""
排程模块初始化脚本
"""
from .main import bp as aps_scheduling_bp

# 导出蓝图，供api模块的run脚本统一注册
__all__ = ['aps_scheduling_bp']