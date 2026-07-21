"""查看仓网布局 Excel 文件内容"""
import pandas as pd
import os

base = r"D:\WYJ\库存优化与检定排程\Proj\backend\二阶段目标设计开发\仓网布局方案"

for f in os.listdir(base):
    if f.endswith('.xlsx') and f != 'ADAM_POWER_STATION.xlsx':
        path = os.path.join(base, f)
        try:
            # 查看所有 sheet
            xl = pd.ExcelFile(path)
            print(f"\n{'='*60}")
            print(f"文件: {f}")
            print(f"Sheet: {xl.sheet_names}")
            for sheet in xl.sheet_names:
                df = pd.read_excel(path, sheet_name=sheet)
                print(f"\n  Sheet '{sheet}': {len(df)} 行, 列: {list(df.columns)[:15]}")
                print(f"  前2行:")
                for _, row in df.head(2).iterrows():
                    print(f"    {dict(row)}")
        except Exception as e:
            print(f"\n{f}: 读取失败 - {e}")
