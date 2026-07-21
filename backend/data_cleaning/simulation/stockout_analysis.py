"""
87 单位缺货情况统计
基于仿真推演数据（1-6月）
"""
import os
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker

plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

OUTPUT_DIR = r"D:\WYJ\库存优化与检定排程\Proj\backend\data_cleaning\simulation\output"
DATA_DIR = r"D:\WYJ\库存优化与检定排程\二阶段"

# ================================================================
# 1. 加载数据
# ================================================================

monthly = pd.read_excel(os.path.join(OUTPUT_DIR, "仿真补库数据_1-6月.xlsx"))
daily = pd.read_excel(os.path.join(OUTPUT_DIR, "地市每日库存_1-6月.xlsx"))

# 加载 ORG 名称
org_df = pd.read_excel(os.path.join(DATA_DIR, "库存统计表1月1日.xlsx"))
org_df['单位编码'] = org_df['单位编码'].apply(
    lambda x: str(int(x)) if pd.notna(x) else '').str.strip()
org_df['县名'] = org_df['单位（县）'].astype(str).str.strip()
org_df = org_df[org_df['县名'].notna() & (org_df['县名'] != '') & (org_df['县名'] != 'nan')]
org_names = dict(zip(org_df['单位编码'], org_df['县名']))

print("=" * 70)
print("87 单位缺货情况统计 (2026年1-6月)")
print("=" * 70)

# ================================================================
# 2. 月度缺货统计（基于月末库存）
# ================================================================

monthly['缺货量'] = monthly['期末库存'].apply(lambda x: -x if x < 0 else 0)
monthly['是否缺货'] = monthly['期末库存'] < 0
monthly['ORG_NAME'] = monthly['ORG_NO'].astype(str).map(org_names).fillna(monthly['ORG_NO'].astype(str))

print("\n--- 月度缺货概况 ---")
total_months = len(monthly)
stockout_months = monthly[monthly['是否缺货']]
print(f"总记录: {total_months} 条 (87单位×6类别×6月)")
print(f"缺货记录: {len(stockout_months)} 条 ({len(stockout_months)/total_months*100:.1f}%)")

if len(stockout_months) > 0:
    total_stockout_qty = stockout_months['缺货量'].sum()
    print(f"缺货总量: {total_stockout_qty:,.0f} 件")
    print(f"涉及单位: {stockout_months['ORG_NO'].nunique()} 个")

# ================================================================
# 3. 按单位-类别-月份 缺货明细
# ================================================================

stockout_detail = monthly[monthly['是否缺货']].copy()
if len(stockout_detail) > 0:
    stockout_detail = stockout_detail.sort_values(['月份', 'ORG_NO', '类别'])
    stockout_detail['单位名称'] = stockout_detail['ORG_NO'].map(org_names).fillna(stockout_detail['ORG_NO'])
    out_cols = ['月份', 'ORG_NO', '单位名称', '类别', '期初库存', '补货量q', '安装量', '期末库存', '缺货量']
    print(f"\n--- 月度缺货明细 (前30条) ---")
    print(stockout_detail[out_cols].head(30).to_string(index=False))

# ================================================================
# 4. 按单位汇总缺货
# ================================================================

print("\n--- 按单位汇总缺货 ---")
org_stockout = monthly.groupby('ORG_NO').agg(
    单位名称=('ORG_NAME', 'first'),
    缺货月次数=('是否缺货', 'sum'),
    总缺货量=('缺货量', 'sum'),
    平均期末库存=('期末库存', 'mean'),
).reset_index()
org_stockout = org_stockout.sort_values('总缺货量', ascending=False)

# 只显示有缺货的
org_with_stockout = org_stockout[org_stockout['总缺货量'] > 0]
print(f"有缺货的单位: {len(org_with_stockout)} / 87")
print(org_with_stockout.to_string(index=False))

# ================================================================
# 5. 按类别汇总缺货
# ================================================================

print("\n--- 按类别汇总缺货 ---")
cat_stockout = monthly.groupby('类别').agg(
    缺货月次数=('是否缺货', 'sum'),
    总缺货量=('缺货量', 'sum'),
    平均期末库存=('期末库存', 'mean'),
).reset_index()
cat_stockout = cat_stockout.sort_values('总缺货量', ascending=False)
print(cat_stockout.to_string(index=False))

# ================================================================
# 6. 按月份汇总缺货
# ================================================================

print("\n--- 按月份汇总缺货 ---")
month_stockout = monthly.groupby('月份').agg(
    缺货记录数=('是否缺货', 'sum'),
    总缺货量=('缺货量', 'sum'),
    总期末库存=('期末库存', 'sum'),
).reset_index()
month_stockout['缺货占期末库存比'] = (month_stockout['总缺货量'] / month_stockout['总期末库存'].abs() * 100).round(2)
month_stockout['缺货占期末库存比'] = month_stockout['缺货占期末库存比'].apply(lambda x: f'{x}%')
print(month_stockout.to_string(index=False))

# ================================================================
# 7. 每日级别缺货
# ================================================================

daily['是否缺货'] = daily['期末库存'] < 0
daily['缺货量'] = daily['期末库存'].apply(lambda x: -x if x < 0 else 0)
daily['ORG_NAME'] = daily['ORG_NO'].astype(str).map(org_names).fillna(daily['ORG_NO'].astype(str))

print(f"\n--- 每日缺货概况 ---")
total_days = len(daily)
stockout_days = daily[daily['是否缺货']]
print(f"总日记录: {total_days} 条")
print(f"缺货日记录: {len(stockout_days)} 条 ({len(stockout_days)/total_days*100:.1f}%)")

if len(stockout_days) > 0:
    # 按单位-类别统计缺货天数
    daily_org_cat = daily.groupby(['ORG_NO', 'ORG_NAME', '类别']).agg(
        总天数=('是否缺货', 'count'),
        缺货天数=('是否缺货', 'sum'),
        累计缺货量=('缺货量', 'sum'),
        最大缺货量=('缺货量', 'max'),
    ).reset_index()
    daily_org_cat = daily_org_cat[daily_org_cat['缺货天数'] > 0]
    daily_org_cat = daily_org_cat.sort_values('缺货天数', ascending=False)
    print(f"\n--- 每日缺货明细 (按单位-类别, 前30条) ---")
    print(daily_org_cat.head(30).to_string(index=False))

    # 按单位汇总每日缺货天数
    daily_org = daily.groupby(['ORG_NO', 'ORG_NAME']).agg(
        总天数=('是否缺货', 'count'),
        缺货天数=('是否缺货', 'sum'),
        累计缺货量=('缺货量', 'sum'),
    ).reset_index()
    daily_org['缺货天数占比'] = (daily_org['缺货天数'] / daily_org['总天数'] * 100).round(1)
    daily_org = daily_org[daily_org['缺货天数'] > 0].sort_values('缺货天数', ascending=False)
    print(f"\n--- 按单位汇总每日缺货天数 ---")
    print(daily_org.to_string(index=False))

# ================================================================
# 8. 保存 Excel
# ================================================================

excel_path = os.path.join(OUTPUT_DIR, "缺货统计.xlsx")
with pd.ExcelWriter(excel_path, engine='openpyxl') as writer:
    # Sheet 1: 月度缺货明细
    if len(stockout_detail) > 0:
        stockout_detail[out_cols].to_excel(writer, sheet_name='月度缺货明细', index=False)
    # Sheet 2: 单位汇总(月)
    org_stockout.to_excel(writer, sheet_name='单位月度缺货汇总', index=False)
    # Sheet 3: 类别汇总(月)
    cat_stockout.to_excel(writer, sheet_name='类别月度缺货汇总', index=False)
    # Sheet 4: 月份汇总
    month_stockout.to_excel(writer, sheet_name='月份缺货汇总', index=False)
    # Sheet 5: 每日缺货明细
    if len(stockout_days) > 0:
        daily_org_cat.to_excel(writer, sheet_name='每日缺货_单位类别', index=False)
        daily_org.to_excel(writer, sheet_name='每日缺货_单位汇总', index=False)

print(f"\n结果已保存至: {excel_path}")

# ================================================================
# 9. 图表
# ================================================================

fig, axes = plt.subplots(2, 2, figsize=(16, 12))

# 图1: 各类别月度缺货量
ax1 = axes[0, 0]
categories = monthly['类别'].unique()
months = sorted(monthly['月份'].unique())
colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', '#8c564b']
for i, cat in enumerate(categories):
    data = monthly[monthly['类别'] == cat].groupby('月份')['缺货量'].sum()
    ax1.plot(months, [data.get(m, 0) for m in months],
             marker='o', color=colors[i], linewidth=2, markersize=6, label=cat)
ax1.set_title('各类别月度缺货量', fontsize=13, fontweight='bold')
ax1.set_xlabel('月份')
ax1.set_ylabel('缺货量（件）')
ax1.legend(fontsize=9, ncol=3)
ax1.grid(True, alpha=0.3)
ax1.xaxis.set_major_locator(ticker.MaxNLocator(integer=True))

# 图2: 缺货量 Top 10 单位
ax2 = axes[0, 1]
top10 = org_stockout.head(10)
top10_names = [str(n) for n in top10['单位名称'].tolist()]
top10_vals = top10['总缺货量'].tolist()
bars = ax2.barh(range(len(top10)), top10_vals,
                color=['#d62728' if i < 3 else '#1f77b4' for i in range(len(top10))])
ax2.set_yticks(range(len(top10)))
ax2.set_yticklabels([n[:10] for n in top10_names], fontsize=9)
ax2.set_title('缺货量 Top 10 单位', fontsize=13, fontweight='bold')
ax2.set_xlabel('缺货量（件）')
ax2.invert_yaxis()
maxv = max(top10_vals) if top10_vals else 1
for i, (v, n) in enumerate(zip(top10_vals, top10_names)):
    ax2.text(v + maxv*0.01, i, f'{v:,.0f}', va='center', fontsize=8)

# 图3: 月度缺货趋势
ax3 = axes[1, 0]
for i, cat in enumerate(categories):
    data = monthly[monthly['类别'] == cat].groupby('月份')['是否缺货'].sum()
    ax3.plot(months, [data.get(m, 0) for m in months],
             marker='s', color=colors[i], linewidth=2, markersize=6, label=cat)
ax3.set_title('各类别月度缺货记录数 (共87单位×6类=522条/月)', fontsize=13, fontweight='bold')
ax3.set_xlabel('月份')
ax3.set_ylabel('缺货记录数')
ax3.legend(fontsize=9, ncol=3)
ax3.grid(True, alpha=0.3)
ax3.xaxis.set_major_locator(ticker.MaxNLocator(integer=True))

# 图4: 缺货天数 Top 10 单位（每日）
ax4 = axes[1, 1]
if len(daily_org) > 0:
    top10_daily = daily_org.head(10)
    d_names = [str(n) for n in top10_daily['ORG_NAME'].tolist()]
    d_days = top10_daily['缺货天数'].tolist()
    bars = ax4.barh(range(len(top10_daily)), d_days,
                    color=['#d62728' if i < 3 else '#2ca02c' for i in range(len(top10_daily))])
    ax4.set_yticks(range(len(top10_daily)))
    ax4.set_yticklabels([n[:10] for n in d_names], fontsize=9)
    ax4.set_title('每日缺货天数 Top 10 单位', fontsize=13, fontweight='bold')
    ax4.set_xlabel('缺货天数')
    ax4.invert_yaxis()
    maxd = max(d_days) if d_days else 1
    for i, (v, n) in enumerate(zip(d_days, d_names)):
        ax4.text(v + maxd*0.01, i, f'{v}天', va='center', fontsize=8)
else:
    ax4.text(0.5, 0.5, '无缺货', ha='center', va='center', fontsize=16, transform=ax4.transAxes)

plt.tight_layout()
chart_path = os.path.join(OUTPUT_DIR, "缺货统计_图表.png")
fig.savefig(chart_path, dpi=150, bbox_inches='tight')
plt.close(fig)
print(f"图表已保存至: {chart_path}")
