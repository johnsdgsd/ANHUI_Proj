from typing import Dict, Optional, List, Any
import pandas as pd
from .item import Item
from .demand_distribution import DemandDistribution, NormalDistribution, PoissonDistribution, UniformDistribution

class LocalWarehouse:
    """地方库"""
    _DISTRIBUTION_TYPES = [
        {'type': 'normal', 'class': NormalDistribution, 'params': ['mean', 'std']},
        {'type': 'poisson', 'class': PoissonDistribution, 'params': ['lambda_']},
        {'type': 'uniform', 'class': UniformDistribution, 'params': ['low', 'high']}
    ]
    
    def __init__(self, warehouse_id: str, city_code: str, city_name: str, T: int = 1, tn: float = 0.25):
        """初始化地方库
        
        Args:
            warehouse_id: 仓库ID
            city_code: 地市编码
            city_name: 地市名称
            T: 决策周期，默认为1
            tn: 提前期，范围0-1，默认为0.25
        """
        self.warehouse_id = warehouse_id
        self.city_code = city_code
        self.city_name = city_name
        self.T = T
        self.tn = tn
        self.items: Dict[str, Item] = {}  # item_key -> Item
    
    def add_item(self, item_key: str, item: Item):
        """添加物资
        
        Args:
            item_key: 物资键（可以是设备码或其他唯一标识）
            item: 物资对象
        """
        self.items[item_key] = item
    
    def get_item(self, item_key: str) -> Optional[Item]:
    
        """获取物资"""
        return self.items.get(item_key)
    
    def simulate(self, start_year_month: int, end_year_month: int):
        """仿真函数，对所有物资进行仿真
        
        Args:
            start_year_month: 起始年月，格式如202501表示2025年1月
            end_year_month: 结束年月，格式如202512表示2025年12月
        """
        for item in self.items.values():
            item.simulate(start_year_month, end_year_month)
    
    def initialize_from_dataframe(self, df: pd.DataFrame, filter_columns: str):
        """从DataFrame初始化所有物资的需求分布
        
        Args:
            df: 包含地方仓库物资数据的DataFrame，应包含以下列：
                - 设备类别
                - 设备码
                - 需求分布类型
                - 需求分布参数
                - 月份
                - 以及filter_columns指定的筛选列
            filter_columns: 筛选列名，用于筛选数据
                例如：'地方编码'，会筛选出地方编码等于当前仓库的记录
                
        Raises:
            ValueError: 当筛选列不存在于DataFrame中时
        """
        # 构建筛选条件
        if filter_columns not in df.columns:
            raise ValueError(f"筛选列 '{filter_columns}' 不存在于DataFrame中")
        
        df_filter = df.copy()
        df_filter[filter_columns] = df_filter[filter_columns].astype(str)
        city_code_str = str(self.city_code)
        city_name_str = str(self.city_name)
        
        condition = False
        if filter_columns == '地方编码' or '编码' in filter_columns:
            condition = (df_filter[filter_columns] == city_code_str)
        elif filter_columns == '地方名称' or '名称' in filter_columns:
            condition = (df_filter[filter_columns] == city_name_str)
        
        # 筛选数据
        df_filtered = df[condition]
        
        # 按设备类别分组
        grouped = df_filtered.groupby(['设备类别'])
        
        for cls, group in grouped:
            # 为每个设备码创建物资对象
            for dev_code in group['设备码'].unique():
                # 创建物资对象，初始库存、持有成本、缺货成本、满足率都设为0
                item = Item(
                    cls=cls[0],
                    dev_code=dev_code,
                    initial_inventory=0.0,
                    holding_cost=0.0,
                    shortage_cost=0.0,
                    alpha=0.0
                )
                
                # 为该设备码的每个月设置需求分布
                dev_code_data = group[group['设备码'] == dev_code]
                for _, row in dev_code_data.iterrows():
                    year_month = int(row['月份'])
                    month = year_month % 100  # 从年月（如202601）中提取月份（1-12）
                    dist_type = row['需求分布类型']
                    params = row['需求分布参数']
                    
                    distribution = None
                    for dist_info in self._DISTRIBUTION_TYPES:
                        if dist_type == dist_info['type']:
                            distribution = dist_info['class'](
                                **{k: params[k] for k in dist_info['params']},
                                T=self.T,
                                tn=self.tn
                            )
                            break
                    
                    if distribution is None:
                        supported_types = [d['type'] for d in self._DISTRIBUTION_TYPES]
                        raise ValueError(f"未知的需求分布类型: '{dist_type}'，支持的类型有: {supported_types}")
                    
                    item.set_demand_distribution(month, distribution)
                
                # 物资键就是设备码
                self.add_item(str(dev_code), item)

    def set_alpha_by_category(self, category_alpha: Dict[str, float]):
        """根据设备类别设置满足率
        
        Args:
            category_alpha: 字典，键为设备类别，值为满足率
        """
        for item_key, item in self.items.items():
            if item.cls in category_alpha:
                item.alpha = category_alpha[item.cls]
    
    def set_initial_inventory(self, dev_code: str, initial_inventory: float):
        """设置指定设备的初始库存
        
        Args:
            dev_code: 设备码
            initial_inventory: 初始库存
        """
        item = self.get_item(dev_code)
        if item:
            item.initial_inventory = initial_inventory
            item.reset_inventory()
    
    def set_holding_cost(self, dev_code: str, holding_cost: float):
        """设置指定设备的持有成本单价
        
        Args:
            dev_code: 设备码
            holding_cost: 持有成本单价
        """
        item = self.get_item(dev_code)
        if item:
            item.holding_cost = holding_cost
    
    def set_shortage_cost(self, dev_code: str, shortage_cost: float):
        """设置指定设备的缺货成本单价
        
        Args:
            dev_code: 设备码
            shortage_cost: 缺货成本单价
        """
        item = self.get_item(dev_code)
        if item:
            item.shortage_cost = shortage_cost
    
    def set_alpha_by_dev_code(self, dev_code: str, alpha: float):
        """设置指定设备的满足率
        
        Args:
            dev_code: 设备码
            alpha: 满足率
        """
        item = self.get_item(dev_code)
        if item:
            item.alpha = alpha
    
    def set_costs_by_category(self, category_costs: Dict[str, Dict[str, float]]):
        """根据设备类别设置成本
        
        Args:
            category_costs: 字典，键为设备类别，值为包含holding_cost和shortage_cost的字典
        """
        for item_key, item in self.items.items():
            if item.cls in category_costs:
                item.holding_cost = category_costs[item.cls]['holding_cost']
                item.shortage_cost = category_costs[item.cls]['shortage_cost']


class CentralWarehouse:
    """中心库"""
    def __init__(self):
        self.items: Dict[str, Item] = {}  # item_key -> Item
        self.local_warehouses: Dict[str, LocalWarehouse] = {}  # warehouse_id -> LocalWarehouse
        self.warehouse_id = None
        self.name = None
    
    def initialize_from_local_warehouse(self, local_warehouse: LocalWarehouse):
        """根据地方仓库初始化中心库的物资
        
        中心库的物资种类和类别与地方仓库相同，
        只有持有成本，没有缺货成本
        
        Args:
            local_warehouse: 任意一个地方仓库对象
        """
        for item_key, local_item in local_warehouse.items.items():
            central_item = Item(
                cls=local_item.cls,
                dev_code=local_item.dev_code,
                initial_inventory=0,
                holding_cost=local_item.holding_cost,
                shortage_cost=0,
                alpha=local_item.alpha
            )
            for month, dist in local_item.demand_distributions.items():
                central_item.demand_distributions[month] = dist
            
            self.add_item(item_key, central_item)
    
    def add_local_warehouse(self, warehouse: LocalWarehouse):
        """添加地方库"""
        self.local_warehouses[warehouse.city_code] = warehouse
    
    def add_item(self, item_key: str, item: Item):
        """添加物资
        
        Args:
            item_key: 物资键（可以是设备码或其他唯一标识）
            item: 物资对象
        """
        self.items[item_key] = item
    