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
    
    def reset_inventory(self):
        """重置所有物资的库存状态"""
        for item in self.items.values():
            item.reset_inventory()
    
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
                    initial_inventory=1000.0,
                    holding_cost=10.0,
                    shortage_cost=100.0,
                    alpha=0.0
                )
                
                # 为该设备码的每个月设置需求分布
                dev_code_data = group[group['设备码'] == dev_code]
                for _, row in dev_code_data.iterrows():
                    year_month = int(row['月序号'])
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
        self.city_code = None
        self.city_name = None
    
    def initialize_from_sto_data(self, qua_sto: pd.DataFrame, unqua_sto: pd.DataFrame,):
        """根据合格品和不合格品库存数据初始化中心库物资

        首先合并两个库存数据，然后根据合并结果创建物资对象

        Args:
            qua_sto: 合格品库存DataFrame，包含DEV_CLS, DEV_CODE_NO, QUA_STO列
            unqua_sto: 不合格品库存DataFrame，包含DEV_CLS, PRO_SPEC_NO, UNQUA_STO列
        """

        # 合并库存数据
        merged_df = self.merge_inventory_data(qua_sto, unqua_sto)

        # 根据合并结果初始化物资
        for _, row in merged_df.iterrows():
            cls = row['设备类别']
            dev_code = row['设备码']
            initial_inventory = row['库存值']

            # 创建物资对象
            item = Item(
                cls=cls,
                dev_code=dev_code,
                initial_inventory=initial_inventory,
                holding_cost=0,
                shortage_cost=0,
                alpha=0
            )

            # 生成item_key并添加到中心库
            self.add_item(dev_code, item)

    def merge_inventory_data(self, qualified_df: pd.DataFrame, unqualified_df: pd.DataFrame) -> pd.DataFrame:
        """合并合格品和不合格品库存数据

        从合格品df中提取DEV_CLS, DEV_CODE_NO, QUA_STO三列
        从不合格品df中提取DEV_CLS, DEV_CODE, UNQUA_STO三列
        如果某设备码的库存值两个df都有，结果相加
        如果某设备码的库存值只有其中一个df有，只算这一个

        Args:
            qualified_df: 合格品库存DataFrame，包含DEV_CLS, DEV_CODE_NO, QUA_STOCK_NUM列
            unqualified_df: 不合格品库存DataFrame，包含DEV_CLS, DEV_CODE, ME_UNQUA_STOCK_NUM列

        Returns:
            pd.DataFrame: 合并后的库存数据，包含设备类别、设备码、库存值三列
        """
        # 提取并重命名合格品数据
        qualified = qualified_df[['DEV_CLS', 'DEV_CODE', 'QUA_STOCK_NUM']].copy()
        qualified.columns = ['设备类别', '设备码', '库存值']

        # 提取并重命名不合格品数据
        unqualified = unqualified_df[['DEV_CLS', 'DEV_CODE', 'ME_UNQUA_STOCK_NUM']].copy()
        unqualified.columns = ['设备类别', '设备码', '库存值']

        # 按设备类别和设备码分组求和
        qualified_grouped = qualified.groupby(['设备类别', '设备码'], as_index=False)['库存值'].sum()
        unqualified_grouped = unqualified.groupby(['设备类别', '设备码'], as_index=False)['库存值'].sum()

        # 合并两个数据集
        merged = pd.merge(
            qualified_grouped,
            unqualified_grouped,
            on=['设备类别', '设备码'],
            how='outer',
            suffixes=('_合格', '_不合格')
        )

        # 填充缺失值为0
        merged['库存值_合格'] = merged['库存值_合格'].fillna(0)
        merged['库存值_不合格'] = merged['库存值_不合格'].fillna(0)

        # 计算总库存值
        merged['库存值'] = merged['库存值_合格'] + merged['库存值_不合格']

        # 返回最终的三列结果
        return merged[['设备类别', '设备码', '库存值']]
    
    def reset_inventory(self):
        """重置中心库所有物资的库存状态"""
        for item in self.items.values():
            item.reset_inventory()

    def update_items_from_local_warehouses(self, local_warehouses: list):
        """根据地方仓库列表同步物资

        检查哪些设备码当前中心库没有，没有的要补上
        初始库存默认为0，成本项和地方库的一样

        Args:
            local_warehouses: 地方仓库列表
        """

        # 收集所有地方仓库的设备码
        all_local_items = set()
        item_templates = {}  # 用于存储每个设备码的模板（从任意地方库获取）

        for warehouse in local_warehouses:
            for item_key, item in warehouse.items.items():
                all_local_items.add(item_key)
                if item_key not in item_templates:
                    item_templates[item_key] = item

        # 检查中心库缺少哪些物资
        central_item_keys = set(self.items.keys())
        missing_items = all_local_items - central_item_keys

        # 为缺失的物资创建新的Item对象
        for item_key in missing_items:
            template_item = item_templates[item_key]

            # 创建新的物资对象，初始库存为0
            new_item = Item(
                cls=template_item.cls,
                dev_code=template_item.dev_code,
                initial_inventory=0,
                holding_cost=0,
                shortage_cost=0,  # 中心库没有缺货成本
                alpha=0
            )

            # 添加到中心库
            self.add_item(item_key, new_item)
            print(f"已添加物资 {item_key}到中心库")

        print(f"共添加 {len(missing_items)} 种物资到中心库")
        return len(missing_items)
    
    def simulate(self, local_warehouses: list):
        """中心库仿真
        
        订货量 = 所有地方仓库当前物资订货量之和 + 所有地方仓库当前物资期望下阶段需求之和 - 中心库当前库存
        
        Args:
            local_warehouses: 地方仓库列表
        """
        if not local_warehouses:
            return
        
        sample_local = local_warehouses[0]
        sample_item_key = list(sample_local.items.keys())[0] if sample_local.items else None
        if not sample_item_key:
            return
        
        sample_item = sample_local.items[sample_item_key]
        num_periods = len(sample_item.order_records)
        
        for item_key, central_item in self.items.items():
            central_item.reset_inventory()
        
        for period in range(num_periods):
            for item_key, central_item in self.items.items():
                total_order = 0
                total_next_demand = 0
                
                for local_warehouse in local_warehouses:
                    local_item = local_warehouse.get_item(item_key)
                    if not local_item:
                        continue
                    total_order += local_item.order_records[period]
                    total_next_demand += local_item.expected_next_demand_list[period]
                
                initial_stock = central_item.current_inventory[-1]
                current_order = total_order + total_next_demand - initial_stock
                current_order = max(0,current_order)
                current_stock = initial_stock + current_order
                next_stock = current_stock - total_order
                next_stock = max(0,next_stock)
                
                central_item.current_inventory.append(next_stock)
                central_item.order_records.append(current_order)
                central_item.demand_records.append(total_order)

        
        for item_key,central_item in self.items.items():
            central_item.calculate_total_cost()
    
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