from abc import ABC, abstractmethod

class CostCalculator(ABC):
    """成本计算基类"""
    @abstractmethod
    def calculate_holding_cost(self, inventory: float) -> float:
        """计算持有成本"""
        pass
    
    @abstractmethod
    def calculate_shortage_cost(self, shortage: float) -> float:
        """计算缺货成本"""
        pass

class DefaultCostCalculator(CostCalculator):
    """默认成本计算"""
    def __init__(self, holding_cost_rate: float, shortage_cost_rate: float):
        self.holding_cost_rate = holding_cost_rate
        self.shortage_cost_rate = shortage_cost_rate
    
    def calculate_holding_cost(self, inventory: float) -> float:
        return inventory * self.holding_cost_rate
    
    def calculate_shortage_cost(self, shortage: float) -> float:
        return shortage * self.shortage_cost_rate
