from typing import Dict, List, Optional
import pandas as pd
import numpy as np
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
    
    def calculate_weighted_alpha(self) -> float:
        """计算加权满足率
        
        Returns:
            加权满足率
        """
        weighted_alpha_sum = 0
        total_demand_sum = 0
        
        for local_warehouse in self.local_warehouses:
            for item in local_warehouse.items.values():
                item_demand_sum = sum(item.D_list)
                weighted_alpha_sum += item.alpha * item_demand_sum
                total_demand_sum += item_demand_sum
        
        return weighted_alpha_sum / total_demand_sum if total_demand_sum > 0 else 0
    
    def calculate_costs(self) -> Dict[str, float]:
        """计算成本
        
        Returns:
            成本字典，包含各仓库成本和总成本
        """
        costs = {}
        total_holding_cost = 0
        total_shortage_cost = 0
        
        for local_warehouse in self.local_warehouses:
            warehouse_holding = 0
            warehouse_shortage = 0
            
            for item in local_warehouse.items.values():
                warehouse_holding += item.total_holding_cost
                warehouse_shortage += item.total_shortage_cost
            
            costs[local_warehouse.city_code] = {
                'holding_cost': warehouse_holding,
                'shortage_cost': warehouse_shortage,
                'total_cost': warehouse_holding + warehouse_shortage
            }
            total_holding_cost += warehouse_holding
            total_shortage_cost += warehouse_shortage
        
        central_holding = 0
        for item in self.central_warehouse.items.values():
            central_holding += item.total_holding_cost
        central_holding = round(central_holding,1)
        
        costs['central_warehouse'] = {
            'holding_cost': central_holding,
            'shortage_cost': 0,
            'total_cost': central_holding
        }
        total_holding_cost += central_holding
        
        costs['总计'] = {
            'holding_cost': total_holding_cost,
            'shortage_cost': total_shortage_cost,
            'total_cost': total_holding_cost + total_shortage_cost,
            'total_alpha': self.calculate_weighted_alpha()
        }
        
        return costs
    
    def objective_function(self, *args):
        """目标函数：将入参解包为满足率，运行仿真并计算总成本"""
        alpha_tuple = tuple(args)
        alpha_dict = self._build_alpha_dict(alpha_tuple[0])
        self.set_alpha(alpha_dict)
        for local_warehouse in self.local_warehouses:
            local_warehouse.reset_inventory()
        self.central_warehouse.reset_inventory()
        self.simulate(202501, 202512)
        costs = self.calculate_costs()
        total_alpha = self.calculate_weighted_alpha()
        expect_alpha = 0.95
        if total_alpha > expect_alpha:
            return costs['总计']['total_cost']
        else:
            return 1e20
    
    def _build_alpha_dict(self, alpha_tuple):
        """将入参元组转换为满足率字典"""
        categories = sorted(self._get_unique_categories())
        warehouses = sorted([w.city_code for w in self.local_warehouses])
        category_alpha_by_warehouse = {}
        idx = 0
        for warehouse_key in warehouses:
            category_alpha = {}
            for cat in categories:
                category_alpha[cat] = alpha_tuple[idx]
                idx += 1
            category_alpha_by_warehouse[warehouse_key] = category_alpha
        
        return category_alpha_by_warehouse
    
    def _get_unique_categories(self):
        """获取所有地方仓库中的唯一类别"""
        if not self.local_warehouses:
            return set()
        sample_warehouse = self.local_warehouses[0]
        return {item.cls for item in sample_warehouse.items.values()}
    
    def optimize_alpha(self, n_iter=50, pop_size=200):
        """使用遗传算法优化满足率"""
        from sko.GA import GA
        
        categories = sorted(self._get_unique_categories())
        warehouses = sorted([w.city_code for w in self.local_warehouses])
        n_dim = len(categories) * len(warehouses)
        lb = [0.95] * n_dim
        ub = [0.99] * n_dim
        
        ga = GA(func=self.objective_function, n_dim=n_dim, size_pop=pop_size, max_iter=n_iter,
                lb=lb, ub=ub, precision=1e-2,prob_mut=0.01)
        
        best_alpha_vector, min_cost = ga.run()
        print(f'最佳参数组合为:{best_alpha_vector}')
        print(f'最低成本为{min_cost}')
        
        return best_alpha_vector, min_cost

