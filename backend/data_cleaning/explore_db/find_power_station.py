"""查找 ADAM_POWER_STATION Excel 文件"""
import os, glob

base = r"D:\WYJ\库存优化与检定排程\Proj"

# Walk through all dirs to find it
for root, dirs, files in os.walk(base):
    for f in files:
        if 'ADAM_POWER_STATION' in f:
            full = os.path.join(root, f)
            print(f"找到: {full}")
            print(f"存在: {os.path.exists(full)}")
            # Try to read it
            import pandas as pd
            df = pd.read_excel(full)
            print(f"列名: {list(df.columns)}")
            print(f"行数: {len(df)}")
            print(f"\ndtypes:\n{df.dtypes}")
            print(f"\n前10行:")
            print(df.head(10).to_string())
