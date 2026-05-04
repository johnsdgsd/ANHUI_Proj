from typing import Dict, List, Optional
import pandas as pd
import numpy as np
from backend.inventory_optimization.warehouse import CentralWarehouse, LocalWarehouse, Item
from backend.inventory_optimization.warehouse_initializer import LocalWarehouseInitializer
import warnings

class InventoryOptimizer:
    """库存优化器"""
    def __init__(self, init_stock_df: pd.DataFrame):
        self.init_stock_df = init_stock_df
        self.distribution_data = None

        self.local_warehouse_initializer = LocalWarehouseInitializer()
        self.local_warehouse_initializer.load_city_mapping(init_stock_df)
        self.local_warehouses = self.local_warehouse_initializer.initialize_warehouses(init_stock_df)
        self.central_warehouse = None
        self.data_df: Optional[pd.DataFrame] = None
    

    def get_distributions_from_install_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """从安装数据获取需求分布
        
        输入的df包括INSTALL_ID, STAT_MONTH, UNIT_CODE, UNIT_NAME, DEVICE_TYPE, DEVICE_CODE, INSTALL_NUM
        去掉ID列，生成单位编码、设备码、月份(1-12)的完整笛卡尔积，
        并填充每个月份的数据为所有相同月份数据的均值
        
        Args:
            df: DataFrame，包含 INSTALL_ID, STAT_MONTH, UNIT_CODE, UNIT_NAME, DEVICE_TYPE, DEVICE_CODE, INSTALL_NUM 等列
            
        """
        df_clean = df.copy()
        
        if 'INSTALL_ID' in df_clean.columns:
            df_clean = df_clean.drop(columns=['INSTALL_ID'])
        
        df_clean = df_clean.dropna(how='any')
        
        df_clean['月序号'] = df_clean['STAT_MONTH'] % 100
        
        # 从原始数据中获取所有唯一设备组合
        unique_devices = df_clean[['UNIT_CODE', 'UNIT_NAME', 'DEVICE_TYPE', 'DEVICE_CODE']].drop_duplicates()
        
        # 生成完整的笛卡尔积（每个设备组合都有1-12月数据）
        months = pd.DataFrame({'月序号': range(1, 13)})
        full_cartesian = unique_devices.merge(months, how='cross')
        
        # 计算每个设备每个月的均值
        monthly_avg = df_clean.groupby(['UNIT_CODE', 'DEVICE_CODE', '月序号'], as_index=False)['INSTALL_NUM'].mean()
        monthly_avg = monthly_avg.rename(columns={'INSTALL_NUM': '月均安装数量'})
        
        # 将均值填充到完整笛卡尔积中
        result = full_cartesian.merge(
            monthly_avg,
            on=['UNIT_CODE', 'DEVICE_CODE', '月序号'],
            how='left'
        )
        
        result = result.rename(columns={
            'UNIT_CODE': '单位编码',
            'UNIT_NAME': '单位名称',
            'DEVICE_TYPE': '设备类别',
            'DEVICE_CODE': '设备码'
        })
        
        result['需求分布类型'] = 'poisson'
        result['需求分布参数'] = result['月均安装数量'].apply(
            lambda x: {'lambda_': round(x)} if pd.notna(x) else {'lambda_': 0}
        )
        
        self.distribution_data = result
        return result

    def set_local_warehouses_from_dataframe(self):
        """根据初始库存数据和需求分布数据设置地方库的物资
        
        根据初始库存数据中的单位以及设备码信息从需求分布数据中筛选匹配数据，
        然后用筛选后的需求分布数据初始化各个地方库的物资。
        对于没有匹配到数据的组合，也生成完整的12个月数据并填充默认值
        """
        if self.distribution_data is None:
            raise ValueError("需要先调用 get_distributions_from_install_data 获取需求分布数据")
        
        # 从初始库存数据中提取需要的唯一组合
        stock_combos = self.init_stock_df[['UNIT_CODE','UNIT_NAME', 'DEVICE_TYPE','DEVICE_CODE']].drop_duplicates().astype(str)
        # 每个组合都要有1-12月，月序号列暂时填充nan
        months = pd.DataFrame({'月序号': range(1, 13)})
        stock_combos_with_months = stock_combos.merge(months, how='cross')
        # 首先找出能匹配到的组合
        stock_combos_renamed = stock_combos_with_months.rename(columns={
            'UNIT_CODE': '单位编码',
            'UNIT_NAME': '单位名称',
            'DEVICE_TYPE': '设备类别',
            'DEVICE_CODE': '设备码'
        })
        matched_dist = stock_combos_renamed.merge(
            self.distribution_data,
            on=['单位编码', '单位名称', '设备码', '月序号'],
            how='left'
        )

        matched_dist['设备类别'] = matched_dist['设备类别_x']
        
        # 映射设备类别编码为名称
        device_type_map = {
            '01': '01-电能表',
            '02': '02-互感器',
            '09': '09-终端',
            '54': '54-通信设备'
        }
        matched_dist['设备类别'] = matched_dist['设备类别'].map(device_type_map)
        matched_dist = matched_dist.drop(columns=['设备类别_y','设备类别_x'])
        matched_dist['月均安装数量'] = matched_dist['月均安装数量'].fillna(0).round(0).astype(int)
        # 统计月均安装数量为0或nan的行数
        zero_mask = (matched_dist['月均安装数量']== 0)
        missing_count = zero_mask.sum()
        if missing_count > 0:
            warnings.warn(f"警告：有 {missing_count} 行数据没有匹配到需求分布，将使用默认的需求分布参数 (lambda_=5)", UserWarning)
        
        # 将月均安装数量为0或nan的行的需求分布参数统一置为5
        matched_dist.loc[zero_mask, '需求分布参数'] = matched_dist.loc[zero_mask].apply(lambda row: {'lambda_': 5}, axis=1)
        matched_dist.loc[zero_mask, '月均安装数量'] = 0
        matched_dist.loc[zero_mask, '需求分布类型'] = 'poisson'
        filter_col = '单位编码'
        for local_warehouse in self.local_warehouses:
            local_warehouse.initialize_from_dataframe(matched_dist, filter_col)
        
        # 根据初始库存数据设置物资的初始库存
        if 'BEGIN_STOCK_NUM' in self.init_stock_df.columns:
            for local_warehouse in self.local_warehouses:
                warehouse_code = local_warehouse.city_code
                # 筛选当前仓库的初始库存数据
                warehouse_stock = self.init_stock_df[self.init_stock_df['UNIT_CODE'].astype(str) == warehouse_code]
                for _, row in warehouse_stock.iterrows():
                    dev_code = str(row['DEVICE_CODE'])
                    initial_stock = float(row['BEGIN_STOCK_NUM'])
                    local_warehouse.set_initial_inventory(dev_code, initial_stock)

    def convert_local_to_central(self, local_warehouse: LocalWarehouse) -> CentralWarehouse:
        """将地方库转换为中心库
        
        根据地方库创建一个新的中心库，复制物资信息但调整成本结构
        
        Args:
            local_warehouse: 要转换的地方库
            
        Returns:
            创建的中心库对象
        """
        central = CentralWarehouse()
        central.warehouse_id = local_warehouse.warehouse_id
        central.city_name = local_warehouse.city_name
        central.city_code = local_warehouse.city_code
        
        # 复制物资，但中心库只有持有成本，没有缺货成本
        for item_key, local_item in local_warehouse.items.items():
            central_item = Item(
                cls=local_item.cls,
                dev_code=local_item.dev_code,
                initial_inventory=local_item.initial_inventory,
                holding_cost=local_item.holding_cost,
                shortage_cost=0,  # 中心库没有缺货成本
                alpha=local_item.alpha
            )
            # 复制需求分布
            for month, dist in local_item.demand_distributions.items():
                central_item.demand_distributions[month] = dist
            
            central.add_item(item_key, central_item)
        return central
    
    def set_central_warehouse(self, name: str):
        """从地方库中指定一个成为中心库
        
        根据名称找到对应的地方库，将其转换为中心库，并从地方库列表中移除
        
        Args:
            name: 要设为中心库的地方库名称（city_name）
        """
        found = False
        for warehouse in self.local_warehouses:
            if warehouse.city_name == name:
                # 转换为中央仓库
                self.central_warehouse = self.convert_local_to_central(warehouse)
                self.local_warehouses.remove(warehouse)
                found = True
                print(f"已将 {name} 设为中心库，并从地方库中移除")
                break
        
        if not found:
            raise ValueError(f"未找到名称为 '{name}' 的地方库")
        
        # 从初始库存数据中分离中心库和地方库的数据
        central_code = self.central_warehouse.city_code
        central_stock_df = self.init_stock_df[self.init_stock_df['UNIT_CODE'].astype(str) == central_code]
        local_stock_df = self.init_stock_df[self.init_stock_df['UNIT_CODE'].astype(str) != central_code]
        
        # 统计物资种类
        central_devices = set(central_stock_df['DEVICE_CODE'].astype(str).unique())
        local_devices = set(local_stock_df['DEVICE_CODE'].astype(str).unique())
        
        print(f"中心库有 {len(central_devices)} 种物资")
        print(f"地方库总计有 {len(local_devices)} 种物资")
        
        # 检查地方库有但中心库没有的物资
        missing_in_central = local_devices - central_devices
        if missing_in_central:
            print(f"地方库有但中心库没有的物资: {len(missing_in_central)} 种")
            # 为缺失的物资在中心库中初始化
            for dev_code in missing_in_central:
                # 从任意一个地方库获取该物资的信息作为模板
                template_item = None
                for warehouse in self.local_warehouses:
                    template_item = warehouse.get_item(dev_code)
                    if template_item:
                        break
                
                if template_item:
                    # 创建新的物资对象添加到中心库
                    new_item = Item(
                        cls=template_item.cls,
                        dev_code=template_item.dev_code,
                        initial_inventory=100,
                        holding_cost=template_item.holding_cost,
                        shortage_cost=0, 
                        alpha=template_item.alpha
                    )
                    # 复制需求分布
                    for month, dist in template_item.demand_distributions.items():
                        new_item.demand_distributions[month] = dist
                    
                    self.central_warehouse.add_item(dev_code, new_item)
                    print(f"已添加物资 {dev_code} 到中心库")
        else:
            print("所有地方库的物资中心库都已包含")

    def set_item_costs_from_dataframe(self, df:pd.DataFrame):
        """根据数据表设置地方库物资的成本
        
        根据数据表中每条记录的设备码，设置对应物资的持有成本和缺货成本
        
        Args:
            data_path: Excel文件路径，包含设备码、持有成本、缺货成本等列
        """
        
        if 'Price' in df.columns:
            df['持有成本'] = (df['Price'] * 0.1).round(1)
            df['缺货成本'] = (df['Price'] * 0.5).round(1)
        
        for local_warehouse in self.local_warehouses:
            for _, row in df.iterrows():
                dev_code = str(row['DEVICE_CODE'])
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
        self.simulate(202601, 202612)
        costs = self.calculate_costs()
        total_alpha = self.calculate_weighted_alpha()
        return costs,total_alpha
        
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
        import pygad
        
        categories = sorted(self._get_unique_categories())
        warehouses = sorted([w.city_code for w in self.local_warehouses])
        n_dim = len(categories) * len(warehouses)
        
        def fitness_func(ga_instance, solution, solution_idx):
            costs,total_alpha = self.objective_function(solution)
            if total_alpha > ga_instance.expect_alpha:
                return -costs['总计']['total_cost']
            else:
                return -1e20

        epsilon = 0.95        
        ga = pygad.GA(
            num_generations=n_iter,
            num_parents_mating=pop_size // 2,
            fitness_func=fitness_func,
            sol_per_pop=pop_size,
            num_genes=n_dim,
            gene_type=float,
            gene_space=[{'low': epsilon, 'high': 0.9999} for _ in range(n_dim)],
            mutation_type="random",
            on_generation = self.on_generation,
            mutation_percent_genes=10,
            # parallel_processing=['thread', 20]
        )
        ga.total_alpha = 0
        ga.expect_alpha = epsilon
        
        ga.run()
        
        best_solution = ga.best_solution()[0]
        best_cost = -ga.best_solution()[1]
        
        print(f'最佳参数组合为:{best_solution}')
        print(f'最低成本为:{best_cost}')
        
        return best_solution, best_cost

    def on_generation(self, ga):
        """回调函数，每代结束后调用"""
        best_solution = ga.best_solution()[0]
        costs, best_alpha = self.objective_function(best_solution)
        best_cost = costs['总计']['total_cost']
        print(f"Gen {ga.generations_completed}: alpha={best_solution}, cost={best_cost:.2f}, total_alpha={best_alpha:.4f}")
