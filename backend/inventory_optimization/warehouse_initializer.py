import pandas as pd
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from backend.inventory_optimization.warehouse import LocalWarehouse


class LocalWarehouseInitializer:
    """地方仓库初始化器"""
    
    def __init__(self, city_mapping_path: str):
        """初始化器
        
        Args:
            city_mapping_path: 地市编码和名称映射表Excel路径
        """
        self.city_mapping_path = city_mapping_path
        self.city_map: dict = {}
        self._load_city_mapping()
    
    def _load_city_mapping(self):
        """加载地市映射表"""
        df = pd.read_excel(self.city_mapping_path)
        
        columns = df.columns.tolist()
        code_col = None
        name_col = None
        
        for col in columns:
            if '编码' in col:
                code_col = col
            if '名称' in col:
                name_col = col
        
        if not code_col or not name_col:
            raise ValueError(f"映射表必须包含编码列和名称列，当前列: {columns}")
        
        temp_map = df.set_index(code_col)[name_col].to_dict()
        self.city_map = {str(k): v for k, v in temp_map.items()}
        print(f"加载地市映射表，共 {len(self.city_map)} 条记录")
    
    def initialize_warehouses(self, city_codes_path: str) -> list:
        """初始化所有地方仓库
        
        Args:
            city_codes_path: 包含地市编码的Excel路径
            
        Returns:
            列表，包含初始化的所有LocalWarehouse对象
        """
        df = pd.read_excel(city_codes_path)
        
        code_col = None
        for col in df.columns:
            if '编码' in col:
                code_col = col
                break
        
        if not code_col:
            raise ValueError(f"地市编码表必须包含编码列，当前列: {df.columns.tolist()}")
        
        city_codes = df[code_col].dropna().unique()
        
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


