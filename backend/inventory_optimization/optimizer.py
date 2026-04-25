from typing import Dict, List, Optional
import pandas as pd
from backend.inventory_optimization.warehouse import CentralWarehouse, LocalWarehouse, Item
from backend.inventory_optimization.warehouse_initializer import LocalWarehouseInitializer


class InventoryOptimizer:
    """库存优化器"""
    def __init__(self, city_mapping_path: str, city_codes_path: str):
        self.local_warehouse_initializer = LocalWarehouseInitializer(city_mapping_path)
        self.local_warehouses = self.local_warehouse_initializer.initialize_warehouses(city_codes_path)
        self.central_warehouse = CentralWarehouse()
        self.central_warehouse.warehouse_id = "central_warehouse"
        self.central_warehouse.name = "中心库"
        self.data_df: Optional[pd.DataFrame] = None
    
    def set_local_warehouses_from_dataframe(self, data_path: str):
        """根据数据表设置地方库的物资
        
        根据数据表中每条记录的地市编码，匹配对应的地方仓库，
        并初始化该仓库的物资需求分布
        
        Args:
            data_path: Excel文件路径，包含地市编码、设备码、需求分布类型、需求分布参数等列
        """
        df = pd.read_excel(data_path)
        
        df['需求分布类型'] = 'poisson'
        df['需求分布参数'] = df['数量'].apply(lambda x: {'lambda_': x})
        
        self.data_df = df.copy()
        
        filter_columns = '地市编码'
        
        for local_warehouse in self.local_warehouses:
            local_warehouse.initialize_from_dataframe(df, filter_columns)

    def set_item_costs_from_dataframe(self, data_path: str):
        """根据数据表设置地方库物资的成本
        
        根据数据表中每条记录的设备码，设置对应物资的持有成本和缺货成本
        
        Args:
            data_path: Excel文件路径，包含设备码、持有成本、缺货成本等列
        """
        df = pd.read_excel(data_path)
        
        if '平均单价' in df.columns:
            df['持有成本'] = (df['平均单价'] * 0.1).round(1)
            df['缺货成本'] = (df['平均单价'] * 0.5).round(1)
        
        for local_warehouse in self.local_warehouses:
            for _, row in df.iterrows():
                dev_code = str(row['设备码'])
                if '持有成本' in row:
                    local_warehouse.set_holding_cost(dev_code, float(row['持有成本']))
                if '缺货成本' in row:
                    local_warehouse.set_shortage_cost(dev_code, float(row['缺货成本']))
    
    def generate_alpha_dict(self) -> dict:
        """生成满足率字典
        
        根据当前optimizer中的地方仓库编码，生成满足率字典
        不同类别物资设置不同的满足率
        
        Returns:
            嵌套字典，键为仓库编号，值为该仓库不同类别物资满足率字典
        """
        category_list = ['电能表', '互感器', '终端', '通信模块']
        alpha_values = [0.95, 0.90, 0.85, 0.80]
        
        result = {}
        for local_warehouse in self.local_warehouses:
            warehouse_key = local_warehouse.city_code
            category_alpha = {}
            for cat, alpha in zip(category_list, alpha_values):
                category_alpha[cat] = alpha
            result[warehouse_key] = category_alpha
        
        return result
    
    def set_alpha(self, category_alpha_by_warehouse: dict):
        """设置不同仓库不同类别物资的满足率
        
        Args:
            category_alpha_by_warehouse: 嵌套字典，键为仓库编号，值为该仓库不同类别物资满足率字典
                例如: {'320100': {'电能表': 0.95, '互感器': 0.90}, '320200': {'电能表': 0.92}}
        """
        for local_warehouse in self.local_warehouses:
            warehouse_key = local_warehouse.city_code
            category_alpha = category_alpha_by_warehouse.get(warehouse_key, {})
            if category_alpha:
                local_warehouse.set_alpha_by_category(category_alpha)
    
    def set_inventory(self, initial_inventory_by_warehouse: dict):
        """设置不同仓库不同设备码物资的初始库存
        
        Args:
            initial_inventory_by_warehouse: 嵌套字典，键为仓库编号，值为该仓库不同设备码物资初始库存字典
                例如: {'320100': {'设备码1': 100, '设备码2': 50}, '320200': {'设备码1': 80}}
        """
        for local_warehouse in self.local_warehouses:
            warehouse_key = local_warehouse.city_code
            initial_inventory = initial_inventory_by_warehouse.get(warehouse_key, {})
            if initial_inventory:
                for dev_code, inv in initial_inventory.items():
                    local_warehouse.set_initial_inventory(dev_code, float(inv))
    
    def simulate(self, start_year_month: int, end_year_month: int):
        """仿真函数
        
        Args:
            start_year_month: 起始年月，格式如202501表示2025年1月
            end_year_month: 结束年月，格式如202512表示2025年12月
        """
        for local_warehouse in self.local_warehouses:
            local_warehouse.simulate(start_year_month, end_year_month)

        self.central_warehouse.initialize_from_local_warehouse(self.local_warehouses[0])
        
        self.central_warehouse.simulate(self.local_warehouses)
    
    def calculate_order_quantity(self, item: Item, period: int) -> float:
        """计算订货量"""
        # 基于需求分布和满足率alpha计算订货量
        month = (period - 1) % 12 + 1  # 假设period从1开始，映射到1-12月
        try:
            # 生成T+tn时间段内的需求作为订货量参考
            return item.generate_demand_quantile(month)
        except ValueError:
            return 0.0
    
    def calculate_costs(self, results: Dict[str, Dict[str, List[float]]]) -> Dict[str, Dict[str, Dict[str, float]]]:
        """计算成本"""
        costs = {}
        warehouse_map = {w.city_code: w for w in self.local_warehouses}
        
        for warehouse_id, warehouse_results in results.items():
            warehouse_costs = {}
            local_warehouse = warehouse_map.get(warehouse_id)
            if not local_warehouse:
                continue
            for item_id, order_quantities in warehouse_results.items():
                item = local_warehouse.get_item(item_id)
                if item:
                    holding_cost = 0
                    shortage_cost = 0
                    warehouse_costs[item_id] = {
                        'holding_cost': holding_cost,
                        'shortage_cost': shortage_cost,
                        'total_cost': holding_cost + shortage_cost
                    }
            costs[warehouse_id] = warehouse_costs
        return costs
