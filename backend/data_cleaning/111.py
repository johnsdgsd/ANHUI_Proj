import pandas as pd

# 你的文件路径（注意 r 原始字符串，防止转义）
file_path = r"D:\WYJ\库存优化与检定排程\数据\模型样本数据统计收集-05-08\新旧设备码映射.xlsx"

# 读取 Excel
df = pd.read_excel(file_path, engine="openpyxl")
print(df)
# 在最前面插入 id 列，从 1 开始自增
df.insert(0, "id", range(1, len(df) + 1))  # 0 表示插在第一列

# 查看前几行，确认效果
print(df.head())

# 可选：保存为新文件（避免覆盖原文件）
out_path = r"D:\WYJ\库存优化与检定排程\数据\新旧设备码映射_带id.xlsx"
df.to_excel(out_path, index=False, engine="openpyxl")
print("已保存到：", out_path)