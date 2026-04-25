import pandas as pd
import json
import os

# 设备类别和设备码映射
equipment_categories = {
    "电能表-01": ["E01001", "E01002", "E01003"],
    "互感器-02": ["E02001", "E02002"],
    "终端-09": ["E09001", "E09002", "E09003", "E09004"],
    "通信模块-54": ["E54001"]
}

# 需求分布类型（固定为泊松分布）
dist_type = "poisson"

# 生成示例数据
data = []
for month in range(1, 13):
    year_month = 202600 + month  # 202601 到 202612
    
    for category, device_codes in equipment_categories.items():
        for device_code in device_codes:
            # 为每个设备使用泊松分布
            params = {"lambda_": 5 + len(data) % 15}
            
            data.append({
                "月份": year_month,
                "地方名称": "徐州市供电公司",
                "地方编码": "320300",
                "设备类别": category,
                "设备码": device_code,
                "需求分布类型": dist_type,
                "需求分布参数": params
            })

# 创建数据框
df = pd.DataFrame(data)

# 确保输出文件夹存在（输出到项目根目录下的data文件夹）
output_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")
if not os.path.exists(output_dir):
    os.makedirs(output_dir)

# 写入CSV文件
csv_path = os.path.join(output_dir, "inventory_test_data.csv")
df.to_csv(csv_path, index=False, encoding="utf-8-sig")

# 打印生成信息
print(f"示例数据生成完成，共 {len(data)} 条记录")
print(f"数据已保存到: {csv_path}")
print("\n数据预览:")
print(df.head())
print("\n设备类别统计:")
print(df["设备类别"].value_counts())
print("\n分布类型统计:")
print(df["需求分布类型"].value_counts())