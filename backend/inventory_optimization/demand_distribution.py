from abc import ABC, abstractmethod
from typing import List
import math
import numpy as np
from scipy.stats import norm, poisson, uniform

class DemandDistribution(ABC):
    """需求分布基类"""
    def __init__(self, T: int = 1, tn: float = 0.25):
        """初始化需求分布
        
        Args:
            T: 决策周期，默认为1
            tn: 提前期，范围0-1，默认为0.25
        """
        # 检查T参数
        if not isinstance(T, int):
            raise TypeError("T必须是整数类型")
        if T <= 0:
            raise ValueError("T必须大于0")
        
        # 检查tn参数
        if not isinstance(tn, (int, float)):
            raise TypeError("tn必须是数字类型")
        if tn < 0 or tn > 1:
            print(f"警告: tn值 {tn} 不在[0, 1]范围内，已自动调整到有效范围")
            tn = max(0, min(1, tn))  # 确保tn在0-1之间
        
        self.T = T
        self.tn = tn
        self.rate = (T + tn) / T  # 调整比例
    
    @abstractmethod
    def generate_demand(self, alpha: float) -> float:
        """生成需求
        
        Args:
            alpha: 满足率
            
        Returns:
            需求值
        """
        pass
    
    @abstractmethod
    def generate_random(self) -> float:
        """生成随机需求
        
        Returns:
            随机需求值
        """
        pass
    
    @abstractmethod
    def get_mean(self) -> float:
        """获取分布的均值
        
        Returns:
            分布均值
        """
        pass
    
    @abstractmethod
    def calculate_fill_rate(self, inventory: float) -> float:
        """根据库存值计算满足率（需求小于等于库存的概率）
        
        Args:
            inventory: 库存值
            
        Returns:
            满足率，范围0-1
        """
        pass

    @abstractmethod
    def calculate_fillrate_with_rate(self, rate: float, inventory: float) -> float:
        """根据库存值计算满足率（需求小于等于库存的概率）
        
        Args:
            inventory: 库存值
            
        Returns:
            满足率，范围0-1
        """
        pass

class NormalDistribution(DemandDistribution):
    """正态分布"""
    def __init__(self, mean: float, std: float, T: int = 1, tn: float = 0.25):
        super().__init__(T, tn)
        self.mean = mean
        self.std = std
    
    def generate_demand(self, alpha: float) -> float:
        # 生成需求：返回上alpha分位数，使用比例调整
        period_mean = self.mean * self.rate
        period_std = self.std * (self.rate ** 0.5)  # 独立同分布的方差按比例调整
        return norm.ppf(alpha, loc=period_mean, scale=period_std)
    
    def generate_random(self) -> float:
        # 生成随机需求：使用原始参数，不进行rate调整
        return max(0, norm.rvs(loc=self.mean, scale=self.std))
    
    def get_mean(self) -> float:
        return self.mean
    
    def calculate_fill_rate(self, inventory: float) -> float:
        # 使用CDF计算需求小于等于库存的概率
        period_mean = self.mean * self.rate
        period_std = self.std * (self.rate ** 0.5)
        return norm.cdf(inventory, loc=period_mean, scale=period_std)
        
    def calculate_fillrate_with_rate(self, rate: float, inventory: float) -> float:
        # 使用CDF计算调整后的需求小于等于库存的概率
        adjusted_mean = self.mean * rate
        adjusted_std = self.std * (rate ** 0.5)
        return norm.cdf(inventory, loc=adjusted_mean, scale=adjusted_std)

class PoissonDistribution(DemandDistribution):
    """泊松分布"""
    def __init__(self, lambda_: float, T: int = 1, tn: float = 0.25):
        super().__init__(T, tn)
        self.lambda_ = lambda_
    
    def generate_demand(self, alpha: float) -> float:
        # 生成需求：返回上alpha分位数，使用比例调整
        period_lambda = self.lambda_ * self.rate
        # 使用scipy的ppf函数并向上取整
        quantile = poisson.ppf(alpha, period_lambda)
        return float(math.ceil(quantile))
    
    def calculate_fillrate_with_rate(self,rate:float,inventory:float) -> float:
        period_lambda = self.lambda_ * rate
        return poisson.cdf(inventory,period_lambda)

    def generate_random(self) -> float:
        # 生成随机需求：使用原始lambda，不进行rate调整
        return float(poisson.rvs(self.lambda_))
    
    def get_mean(self) -> float:
        return self.lambda_
    
    def calculate_fill_rate(self, inventory: float) -> float:
        # 使用CDF计算需求小于等于库存的概率
        period_lambda = self.lambda_ * self.rate
        return poisson.cdf(inventory, period_lambda)

class UniformDistribution(DemandDistribution):
    """均匀分布"""
    def __init__(self, low: float, high: float, T: int = 1, tn: float = 0.25):
        super().__init__(T, tn)
        self.low = low
        self.high = high
    
    def generate_demand(self, alpha: float) -> float:
        # 生成需求：返回上alpha分位数，使用比例调整
        period_low = self.low * self.rate
        period_high = self.high * self.rate
        return period_low + alpha * (period_high - period_low)
    
    def generate_random(self) -> float:
        # 生成随机需求：使用原始范围，不进行rate调整
        return uniform.rvs(loc=self.low, scale=self.high - self.low)
    
    def get_mean(self) -> float:
        return (self.low + self.high) / 2
    
    def calculate_fill_rate(self, inventory: float) -> float:
        # 使用CDF计算需求小于等于库存的概率
        period_low = self.low * self.rate
        period_high = self.high * self.rate
        
        if inventory <= period_low:
            return 0.0
        elif inventory >= period_high:
            return 1.0
        else:
            return (inventory - period_low) / (period_high - period_low)

    def calculate_fillrate_with_rate(self, rate: float, inventory: float) -> float:
        # 使用CDF计算调整后的需求小于等于库存的概率
        adjusted_low = self.low * rate
        adjusted_high = self.high * rate
        
        if inventory <= adjusted_low:
            return 0.0
        elif inventory >= adjusted_high:
            return 1.0
        else:
            return (inventory - adjusted_low) / (adjusted_high - adjusted_low)
