"""87 单位 1-6 月缺货月度统计"""
import pandas as pd, os

out = r"D:\WYJ\库存优化与检定排程\Proj\backend\data_cleaning\simulation\output"
daily = pd.read_excel(os.path.join(out, "地市每日库存_1-6月.xlsx"))

daily['是否缺货'] = daily['期末库存'] < 0
daily['缺货量'] = daily['期末库存'].clip(upper=0).abs()

# 按月份汇总
monthly = daily.groupby('月份').agg(
    总记录数=('是否缺货', 'count'),
    缺货记录数=('是否缺货', 'sum'),
    总缺货量=('缺货量', 'sum'),
    总配送量=('配送量', 'sum'),
    总安装量=('安装量', 'sum'),
    期初总库存=('期初库存', 'first'),  # 每月第一天
).reset_index()

monthly['缺货占比'] = (monthly['缺货记录数'] / monthly['总记录数'] * 100).round(1)
monthly['日均缺货量'] = (monthly['总缺货量'] / monthly.groupby('月份')['月份'].transform('count').clip(lower=1)).round(0)
# 实际天数
days_per_month = daily.groupby('月份')['日期'].nunique()
monthly['当月天数'] = monthly['月份'].map(days_per_month)
monthly['缺货天数占比'] = (monthly['缺货记录数'] / (monthly['当月天数'] * 87 * 6) * 100).round(1)

print("=" * 60)
print("87 单位 1-6 月缺货月度统计")
print("=" * 60)
print(f"\n{'月份':<6} {'总记录':<8} {'缺货记录':<8} {'缺货占比':<8} {'总缺货量':<12} {'总配送':<12} {'总安装':<12}")
print("-" * 66)
for _, r in monthly.iterrows():
    print(f"{int(r['月份']):<6} {int(r['总记录数']):<8} {int(r['缺货记录数']):<8} "
          f"{r['缺货占比']}%{'':<4} {int(r['总缺货量']):<12,} {int(r['总配送量']):<12,} {int(r['总安装量']):<12,}")

print(f"\n总计: 缺货 {int(monthly['总缺货量'].sum()):,} 件")
