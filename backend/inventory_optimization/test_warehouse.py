import pandas as pd
import json
import sys
import os

# 添加项目根目录到Python路径
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from backend.inventory_optimization.warehouse import LocalWarehouse

# 加载测试数据（从项目根目录下的data文件夹读取）
data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")
csv_path = os.path.join(data_dir, "inventory_test_data.csv")
df = pd.read_csv(csv_path)
# 解析需求分布参数（从字符串转换为字典）
df['需求分布参数'] = df['需求分布参数'].apply(lambda x: json.loads(x.replace("'", '"')))

# 创建地方库
local_warehouse = LocalWarehouse(
    warehouse_id="WH001",
    city_code="320300",
    city_name="徐州市供电公司",
    T=1,
    tn=0.25
)

print("=== 初始化地方库 ===")
try:
    # 从DataFrame初始化物资
    local_warehouse.initialize_from_dataframe(df, filter_columns="地方名称")
    print(f"初始化成功，共创建 {len(local_warehouse.items)} 个物资")
    
    # 打印初始化的物资
    print("\n初始化的物资:")
    for dev_code, item in local_warehouse.items.items():
        print(f"- 设备码: {dev_code}, 类别: {item.cls}, 初始库存: {item.initial_inventory}")
        print(f"  持有成本: {item.holding_cost}, 缺货成本: {item.shortage_cost}, 满足率: {item.alpha}")
        print(f"  需求分布数量: {len(item.demand_distributions)}")
        
except Exception as e:
    print(f"初始化失败: {e}")

print("\n=== 设置成本和满足率 ===")
try:
    # 设置初始库存
    for dev_code in local_warehouse.items:
        local_warehouse.set_initial_inventory(dev_code, 100.0)
    
    # 根据设备类别设置成本
    local_warehouse.set_costs_by_category({
        "电能表-01": {"holding_cost": 2.5, "shortage_cost": 10.0},
        "互感器-02": {"holding_cost": 3.0, "shortage_cost": 12.0},
        "终端-09": {"holding_cost": 4.0, "shortage_cost": 15.0},
        "通信模块-54": {"holding_cost": 5.0, "shortage_cost": 20.0}
    })
    
    # 根据设备类别设置满足率
    local_warehouse.set_alpha_by_category({
        "电能表-01": 0.95,
        "互感器-02": 0.90,
        "终端-09": 0.98,
        "通信模块-54": 0.99
    })
    
    print("设置成功")
    
    # 打印设置后的物资
    print("\n设置后的物资:")
    for dev_code, item in local_warehouse.items.items():
        print(f"- 设备码: {dev_code}, 类别: {item.cls}, 初始库存: {item.initial_inventory}")
        print(f"  持有成本: {item.holding_cost}, 缺货成本: {item.shortage_cost}, 满足率: {item.alpha}")
        
except Exception as e:
    print(f"设置失败: {e}")

print("\n=== 运行仿真 ===")
try:
    # 运行仿真（2026年1月到12月）
    for dev_code, item in local_warehouse.items.items():
        print(f"运行设备 {dev_code} 的仿真...")
        item.simulate(202601, 202612)
        print(f"  总持有成本: {item.total_holding_cost:.2f}")
        print(f"  总缺货成本: {item.total_shortage_cost:.2f}")
        print(f"  总成本: {item.total_holding_cost + item.total_shortage_cost:.2f}")
        # print(f"  库存记录数量: {len(item.current_inventory)}")
        # print(f"  订货记录数量: {len(item.order_records)}")
        # print(f"  需求记录数量: {len(item.demand_records)}")
        # print(f"  缺货记录数量: {len(item.shortage_records)}")
        print()
        
except Exception as e:
    print(f"仿真失败: {e}")

print("=== 测试完成 ===")