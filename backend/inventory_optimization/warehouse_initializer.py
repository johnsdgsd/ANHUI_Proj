from typing import Any
import pandas as pd
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from backend.inventory_optimization.warehouse import LocalWarehouse


class LocalWarehouseInitializer:
    """地方仓库初始化器"""
    
    def __init__(self):
        self.city_map: dict = {}
    
    def load_city_mapping(self, init_stock_df: pd.DataFrame):
        """从初始库存数据中提取地市映射表
        
        Args:
            init_stock_df: 初始库存数据DataFrame，包含 ORG_NO 和 ORG_NAME 列
        """
        if 'ORG_NO' not in init_stock_df.columns:
            raise ValueError("init_stock_df 必须包含 ORG_NO 列")
        
        if 'ORG_NAME' not in init_stock_df.columns:
            raise ValueError("init_stock_df 必须包含 ORG_NAME 列")

        print(f'输入初始化地市映射数据条数{len(init_stock_df)}')
        unique_cities = init_stock_df[['ORG_NO', 'ORG_NAME']].drop_duplicates()
        self.city_map = unique_cities.set_index('ORG_NO')['ORG_NAME'].astype(str).to_dict()
        print(f'加载初始库存信息，共{len(unique_cities)}个地市仓库库存数据')
        print(f"加载地市映射信息，共 {len(self.city_map)} 条记录")
    
    def initialize_warehouses(self, init_stock_df: pd.DataFrame) -> list[LocalWarehouse]:
        """根据初始库存数据初始化所有地方仓库
        
        Args:
            init_stock_df: 初始库存数据DataFrame，包含 ORG_NO 列
            
        Returns:
            列表，包含初始化的所有LocalWarehouse对象
        """
        if 'ORG_NO' not in init_stock_df.columns:
            raise ValueError("init_stock_df 必须包含 ORG_NO 列")
        
        city_codes = init_stock_df['ORG_NO'].dropna().unique()
        
        warehouses = []
        for city_code in city_codes:
            city_code_str = str(city_code).strip()
            city_name = self.city_map.get(city_code_str, f"未知_{city_code_str}")
            
            warehouse_id = f"warehouse_{city_code_str}"
            warehouse = LocalWarehouse(
                warehouse_id=warehouse_id,
                city_code=city_code_str,
                city_name=city_name
            )
            warehouses.append(warehouse)
        
        print(f"初始化完成，共 {len(warehouses)} 个地方仓库")
        return warehouses


