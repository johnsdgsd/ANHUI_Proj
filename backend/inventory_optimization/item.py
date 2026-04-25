from typing import Dict, Optional, Tuple
from .demand_distribution import DemandDistribution

class Item:
    """物资类"""
    def __init__(self, cls: str, dev_code: str, initial_inventory: float, 
                 holding_cost: float, shortage_cost: float, alpha: float):
        """初始化物资
        
        Args:
            cls: 设备类别
            dev_code: 设备码
            initial_inventory: 初始库存
            holding_cost: 持有成本单价
            shortage_cost: 缺货成本单价
            alpha: 满足率
        """
        self.cls = cls
        self.dev_code = dev_code
        self.initial_inventory = initial_inventory
        self.current_inventory = [initial_inventory]  # 当前库存是一个列表
        self.holding_cost = holding_cost
        self.shortage_cost = shortage_cost
        self.alpha = alpha  # 满足率
        # 需求分布字典：键为月份编号（1-12），值为需求分布对象
        self.demand_distributions: Dict[int, DemandDistribution] = {}  # 月份 -> 需求分布对象
        # 期望需求D列表：记录每个阶段需求分布的均值
        self.D_list: list = []
        # 期望下阶段需求列表：记录每个阶段下阶段需求分布均值的四分之一
        self.expected_next_demand_list: list = []
        # 记录列表
        self.order_records = []  # 订货量记录
        self.demand_records = []  # 随机需求记录
        self.shortage_records = []  # 缺货量记录
        # 成本属性
        self.total_holding_cost = 0.0  # 总持有成本
        self.total_shortage_cost = 0.0  # 总缺货成本
        self.total_cost = 0.0  # 总成本
    
    def reset_inventory(self):
        """重置库存状态，将当前库存列表重置为初始库存"""
        self.current_inventory = [self.initial_inventory]
        self.order_records = []
        self.demand_records = []
        self.shortage_records = []
        self.total_holding_cost = 0.0
        self.total_shortage_cost = 0.0
        self.total_cost = 0.0
    
    @staticmethod
    def parse_year_month(year_month: int) -> Tuple[int, int]:
        """解析年月
        
        Args:
            year_month: 年月，格式如202501表示2025年1月
            
        Returns:
            (年份, 月份) 元组
        """
        year = year_month // 100
        month = year_month % 100
        return year, month
    
    def set_demand_distribution(self, month: int, distribution: DemandDistribution):
        """设置需求分布
        
        Args:
            month: 月份编号（1-12）
            distribution: 需求分布对象
        """
        self.demand_distributions[month] = distribution
    
    def generate_demand_quantile(self, month: int) -> float:
        """产生需求分位数
        
        Args:
            month: 月份编号（1-12）
            
        Returns:
            需求分位数
            
        Raises:
            ValueError: 当月份不存在时
        """
        if month not in self.demand_distributions:
            raise ValueError(f"月份 {month} 的需求分布不存在")
        return self.demand_distributions[month].generate_demand(self.alpha)
    
    def generate_random_demand(self, month: int) -> float:
        """产生对应月份需求分布的随机数
        
        Args:
            month: 月份编号（1-12）
            
        Returns:
            随机需求值
            
        Raises:
            ValueError: 当月份不存在时
        """
        if month not in self.demand_distributions:
            raise ValueError(f"月份 {month} 的需求分布不存在")
        return self.demand_distributions[month].generate_random()
    
    def _get_next_year_month(self, year_month: int) -> int:
        """获取下一个月
        
        Args:
            year_month: 年月，格式如202501表示2025年1月
            
        Returns:
            下一个月
        """
        year, month = self.parse_year_month(year_month)
        if month == 12:
            return (year + 1) * 100 + 1
        return year * 100 + month + 1
    
    def update(self, year_month: int):
        """更新库存状态
        
        Args:
            year_month: 年月，格式如202501表示2025年1月
            
        Raises:
            ValueError: 当月份不存在时
        """
        # 将年月转换为月份编号（1-12）
        year, month_number = self.parse_year_month(year_month)
        
        # 获取当前阶段需求分布的均值
        current_distribution = self.demand_distributions.get(month_number)
        if current_distribution:
            current_mean = current_distribution.get_mean()
        else:
            current_mean = 0.0
        
        # 获取下一年月的月份编号
        next_year_month = self._get_next_year_month(year_month)
        _, next_month = self.parse_year_month(next_year_month)
        
        # 获取下阶段需求分布的均值
        next_distribution = self.demand_distributions.get(next_month)
        if next_distribution:
            next_mean = next_distribution.get_mean()
        else:
            next_mean = 0.0
        
        # 记录期望需求D和期望下阶段需求
        self.D_list.append(current_mean)
        self.expected_next_demand_list.append(next_mean * 0.25)
        
        # 获取当月初始库存
        initial_stock = self.current_inventory[-1] if self.current_inventory else self.initial_inventory
        # 获取需求分位数
        demand_quantile = self.generate_demand_quantile(month_number)
        # 计算订货量（需求分位数 - 初始库存，不能为负）
        order_quantity = max(0, demand_quantile - initial_stock)
        # 计算当前库存（初始库存 + 订货量）
        current_stock = initial_stock + order_quantity
        # 产生需求随机数
        random_demand = self.generate_random_demand(month_number)
        # 计算下一时刻库存（当前库存 - 需求，不能为负）
        next_stock = max(0, current_stock - random_demand)
        # 计算缺货量
        shortage = max(0, random_demand - current_stock)
        # 更新当前库存
        self.current_inventory.append(next_stock)
        # 记录数据
        self.order_records.append(order_quantity)
        self.demand_records.append(random_demand)
        self.shortage_records.append(shortage)
    
    def simulate(self, start_year_month: int, end_year_month: int):
        """仿真函数，按顺序更新每个月的库存状态
        
        Args:
            start_year_month: 起始年月，格式如202501表示2025年1月
            end_year_month: 结束年月，格式如202512表示2025年12月
            
        Raises:
            ValueError: 当月份不存在时
        """
        current_year_month = start_year_month
        while current_year_month <= end_year_month:
            self.update(current_year_month)
            current_year_month = self._get_next_year_month(current_year_month)
        # 仿真完成后计算成本
        self.calculate_total_cost()
    
    def calculate_total_cost(self):
        """计算总持有成本和总缺货成本
        
        持有成本计算方式：首尾库存量之和乘0.5乘单价加上中间库存量之和乘单价
        缺货成本为总缺货量乘缺货单价
        """
        if not self.current_inventory:
            self.total_holding_cost = 0.0
        else:
            # 计算持有成本
            if len(self.current_inventory) == 1:
                # 只有一个库存记录时
                self.total_holding_cost = self.current_inventory[0] * self.holding_cost
            else:
                # 首尾库存量之和乘0.5乘单价
                first_last = (self.current_inventory[0] + self.current_inventory[-1]) * 0.5 * self.holding_cost
                # 中间库存量之和乘单价
                middle = sum(self.current_inventory[1:-1]) * self.holding_cost
                self.total_holding_cost = first_last + middle
        
        # 计算缺货成本
        self.total_shortage_cost = sum(self.shortage_records) * self.shortage_cost
        
        # 计算总成本
        self.total_cost = self.total_holding_cost + self.total_shortage_cost
