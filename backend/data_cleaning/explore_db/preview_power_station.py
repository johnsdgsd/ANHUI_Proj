"""读取 ADAM_POWER_STATION Excel 并预览"""
import pandas as pd
import os

path = r"D:\WYJ\库存优化与检定排程\Proj\二阶段目标设计开发\仓网布局方案\ADAM_POWER_STATION.xlsx"
print(f"文件存在: {os.path.exists(path)}")
df = pd.read_excel(path)
print(f"列名: {list(df.columns)}")
print(f"行数: {len(df)}")
print(f"\ndtypes:\n{df.dtypes}")
print(f"\n前10行:")
print(df.head(10).to_string())
