from typing import Dict, List, Any
from .warehouse import CentralWarehouse, LocalWarehouse, Item
from .optimizer import InventoryOptimizer as ModelOptimizer
from .cost_calculator import CostCalculator, DefaultCostCalculator
from .demand_distribution import DemandDistribution, NormalDistribution, PoissonDistribution, UniformDistribution

class InventoryOptimizationService:
    """库存优化服务"""
    def __init__(self):
        self.central_warehouse = CentralWarehouse()
        self.optimizer = ModelOptimizer(self.central_warehouse)
    
    def create_local_warehouse(self, warehouse_id: str, default_cost_calculator: CostCalculator = None) -> LocalWarehouse:
        """创建地方库"""
        warehouse = LocalWarehouse(warehouse_id, default_cost_calculator)
        self.central_warehouse.add_local_warehouse(warehouse)
        return warehouse
    
    def add_item_to_local(self, warehouse_id: str, item: Item):
        """向地方库添加物资"""
        local_warehouse = self.central_warehouse.local_warehouses.get(warehouse_id)
        if local_warehouse:
            local_warehouse.add_item(item)
    
    def add_item_to_central(self, item: Item):
        """向中心库添加物资"""
        self.central_warehouse.add_item(item)
    
    def distribute_from_central(self, local_warehouse_id: str, item_id: str, quantity: float) -> bool:
        """从中心库向地方库分配物资"""
        return self.central_warehouse.distribute_to_local(local_warehouse_id, item_id, quantity)
    
    def run_simulation(self, num_periods: int, alpha: float = 0.95) -> Dict[str, Dict[str, List[float]]]:
        """运行库存仿真"""
        return self.optimizer.simulate(num_periods, alpha)
    
    def calculate_total_costs(self, simulation_results: Dict[str, Dict[str, List[float]]]) -> Dict[str, Dict[str, Dict[str, float]]]:
        """计算总成本"""
        return self.optimizer.calculate_costs(simulation_results)
    
    def get_warehouse_items(self, warehouse_id: str) -> Dict[str, Item]:
        """获取地方库的所有物资"""
        local_warehouse = self.central_warehouse.local_warehouses.get(warehouse_id)
        if local_warehouse:
            return local_warehouse.items
        return {}
    
    def get_central_items(self) -> Dict[str, Item]:
        """获取中心库的所有物资"""
        return self.central_warehouse.items
