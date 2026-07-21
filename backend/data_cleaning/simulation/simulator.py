"""
逐月仿真引擎

流程:
    对每个月 1..6:
        1. 准备当月 init_stock（上月期末库存 → 下月期初）
        2. 加载当月需求预测（SMCP Excel，按类别汇总，分离 BUS_TYPE 01+02 和 03）
        3. 调用 GA 优化 alpha（每个仓库 × DEV_CLS 独立）
        4. 计算基准库存: S = Poisson_ppf(α, λ), λ = 预测01+02 + 真实03安装
        5. 补货量 q = max(0, S - I₀)
        6. 合并安装数据（供电所→县），扣除当月安装量 → 期末库存

输出:
    - output/仿真补库数据_1-6月.xlsx（明细）
    - output/仿真补库数据_月度分类汇总.xlsx
    - output/仿真补库数据_安装量归并后.xlsx
    - output/仿真补库数据_库存变化图.png
"""

import logging
import os
import sys

import numpy as np
import pandas as pd

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJ_DIR = os.path.dirname(os.path.dirname(os.path.dirname(_BASE_DIR)))
_BACKEND_DIR = os.path.join(_PROJ_DIR, 'backend')
sys.path.insert(0, _BACKEND_DIR)

from data_cleaning.simulation.config import (
    SIM_YEAR, SIM_START_MONTH, SIM_END_MONTH,
    CATEGORIES, CATEGORY_TO_DEVCODE, DEVCODE_TO_CATEGORY,
    GA_EPSILON, OUTPUT_DIR,
)
from data_cleaning.simulation.data_prep import (
    load_initial_inventory, load_device_specs, load_item_costs,
    load_monthly_installs, load_monthly_demand,
)
from data_cleaning.simulation.ga_adapter import run_ga_one_month, apply_alpha_to_ppf


def run_simulation():
    """
    主仿真入口 — 逐月运行 1-6 月，输出 Excel + 图表。
    """
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # ================================================================
    # 1. 加载静态数据
    # ================================================================
    logging.info("=" * 60)
    logging.info("[仿真] Step 1/4: 加载静态数据...")
    inventory = load_initial_inventory()       # [ORG_NO, DEV_CODE, CATEGORY, STOCK_NUM]
    spec_df = load_device_specs()              # [DEV_CODE, DEV_CLS, DEV_CATEG]
    cost_df = load_item_costs()                # [DEV_CODE, TAX_UP]
    valid_orgs = set(inventory['ORG_NO'].unique())
    install_raw, install_03_index = load_monthly_installs(inventory_orgs=valid_orgs)

    dev_to_cls = dict(zip(spec_df['DEV_CODE'], spec_df['DEV_CLS']))
    install_index = install_raw.set_index(['ORG_NO', 'DEV_CODE', 'MONTH'])['INSTAL_NUM']

    # 保存归并后的安装数据
    install_raw.to_excel(os.path.join(OUTPUT_DIR, "仿真补库数据_安装量归并后.xlsx"), index=False)

    # ================================================================
    # 2. 初始化库存追踪
    # ================================================================
    current_inv = inventory[['ORG_NO', 'DEV_CODE', 'CATEGORY', 'STOCK_NUM']].copy()
    current_inv.rename(columns={'STOCK_NUM': 'STOCK'}, inplace=True)
    current_inv['STOCK'] = current_inv['STOCK'].astype(float)

    all_results = []
    alpha_records = []

    # ================================================================
    # 3. 逐月仿真
    # ================================================================
    for month in range(SIM_START_MONTH, SIM_END_MONTH + 1):
        ym = int(f"{SIM_YEAR}{month:02d}")
        logging.info(f"\n{'=' * 60}")
        logging.info(f"[仿真] Step 2/4: ======== 第 {month} 月 ({ym}) ========")

        # ---- 3a. 当月需求 ----
        demand = load_monthly_demand(SIM_YEAR, month)

        # ---- 3b. GA 优化 alpha ----
        ga_stock = current_inv[['ORG_NO', 'DEV_CODE', 'STOCK']].rename(columns={'STOCK': 'STOCK_NUM'})
        ga_demand = demand[['ORG_NO', 'DEV_CODE', 'LAMBDA_0102']].copy()

        logging.info(f"[仿真] {month}月 GA 优化...")
        try:
            alpha_dict, best_cost = run_ga_one_month(
                init_stock_df=ga_stock,
                demand_df=ga_demand,
                spec_df=spec_df,
                cost_df=cost_df,
                target_month=ym,
            )
            alpha_records.append({'月份': month, 'best_cost': best_cost, 'n_warehouses': len(alpha_dict)})
        except Exception as e:
            logging.error(f"[仿真] {month}月 GA 失败: {e}，使用默认 alpha")
            alpha_dict = {}

        # ---- 3c. 计算补货 ----
        month_rows = []
        for _, row in current_inv.iterrows():
            org = str(row['ORG_NO']).strip()
            dev = str(row['DEV_CODE']).strip()
            cat = row['CATEGORY']
            I0 = float(row['STOCK'])

            # 需求: λ = 01+02 预测 + 03 真实安装
            dm_row = demand[(demand['ORG_NO'] == org) & (demand['DEV_CODE'] == dev)]
            lam_0102 = int(dm_row['LAMBDA_0102'].sum())
            direct_03 = install_03_index.get((org, dev, int(ym)), 0.0)
            lam = lam_0102 + int(direct_03)

            # 基准库存: Poisson 分位数直接用总 λ
            dev_cls = dev_to_cls.get(dev, '00')
            alpha_val = alpha_dict.get(org, {}).get(dev_cls, GA_EPSILON) if alpha_dict else GA_EPSILON
            S = apply_alpha_to_ppf(alpha_dict, org, dev_cls, lam, GA_EPSILON) if lam > 0 else 0.0
            q = max(0.0, S - I0)

            # 安装量
            try:
                inst = install_index.get((org, dev, int(ym)))
                inst = float(inst) if pd.notna(inst) else 0.0
            except (KeyError, ValueError):
                inst = 0.0

            I1 = I0 + q - inst

            month_rows.append({
                '月份': month,
                'ORG_NO': org,
                'DEV_CODE': dev,
                '类别': cat,
                'alpha': round(alpha_val, 4),
                '期初库存': I0,
                '需求预测值(λ)': lam,
                'direct(03)': direct_03,
                '基准库存S': S,
                '补货量q': q,
                '安装量': inst,
                '期末库存': I1,
            })

        df_m = pd.DataFrame(month_rows)
        total_I0 = df_m['期初库存'].sum()
        total_S = df_m['基准库存S'].sum()
        total_q = df_m['补货量q'].sum()
        total_inst = df_m['安装量'].sum()
        total_I1 = df_m['期末库存'].sum()

        logging.info(
            f"[仿真] {month}月: 期初={total_I0:,.0f} → S={total_S:,.0f} → "
            f"补货={total_q:,.0f} → 安装={total_inst:,.0f} → 期末={total_I1:,.0f}"
        )
        # 按类别
        for cat in CATEGORIES:
            sub = df_m[df_m['类别'] == cat]
            logging.info(
                f"  {cat}: 期初={sub['期初库存'].sum():,.0f}, "
                f"补货={sub['补货量q'].sum():,.0f}, "
                f"安装={sub['安装量'].sum():,.0f}, "
                f"期末={sub['期末库存'].sum():,.0f}"
            )

        all_results.append(df_m)

        # ---- 3d. 更新库存（下月期初 = 本月期末，允许负值表示缺口）----
        for _, r in df_m.iterrows():
            mask = ((current_inv['ORG_NO'] == r['ORG_NO']) &
                    (current_inv['DEV_CODE'] == r['DEV_CODE']))
            current_inv.loc[mask, 'STOCK'] = r['期末库存']

    # ================================================================
    # 4. 汇总输出
    # ================================================================
    logging.info(f"\n{'=' * 60}")
    logging.info("[仿真] Step 3/4: 保存结果...")

    result_df = pd.concat(all_results, ignore_index=True)
    # 输出时删除内部使用的 DEV_CODE 列，以类别为主维度
    result_out = result_df.drop(columns=['DEV_CODE'])
    result_out.to_excel(os.path.join(OUTPUT_DIR, "仿真补库数据_1-6月.xlsx"), index=False)

    # 月度 × 类别 汇总
    monthly_cat = result_df.groupby(['月份', '类别']).agg(
        期初库存=('期初库存', 'sum'),
        基准库存S=('基准库存S', 'sum'),
        补货量q=('补货量q', 'sum'),
        安装量=('安装量', 'sum'),
        期末库存=('期末库存', 'sum'),
    ).reset_index()
    monthly_cat.to_excel(os.path.join(OUTPUT_DIR, "仿真补库数据_月度分类汇总.xlsx"), index=False)

    # 月度总计
    monthly_total = result_df.groupby('月份').agg(
        期初库存=('期初库存', 'sum'),
        需求预测值=('需求预测值(λ)', 'sum'),
        direct_03=('direct(03)', 'sum'),
        基准库存S=('基准库存S', 'sum'),
        补货量q=('补货量q', 'sum'),
        安装量=('安装量', 'sum'),
        期末库存=('期末库存', 'sum'),
    ).astype(int)
    monthly_total.to_excel(os.path.join(OUTPUT_DIR, "仿真补库数据_月度汇总.xlsx"))

    logging.info(f"\n[仿真] 月度汇总:")
    logging.info(f"\n{monthly_total.to_string()}")

    # ================================================================
    # 5. 画图
    # ================================================================
    logging.info(f"\n[仿真] Step 4/4: 绘制库存变化图...")
    _plot_inventory_chart(monthly_cat)

    # ================================================================
    # 6. 打印最终摘要
    # ================================================================
    logging.info(f"\n{'=' * 60}")
    logging.info("[仿真] 完成! 输出文件:")
    for f in os.listdir(OUTPUT_DIR):
        fpath = os.path.join(OUTPUT_DIR, f)
        size_kb = os.path.getsize(fpath) / 1024
        logging.info(f"  {f} ({size_kb:.1f} KB)")

    return result_df


def _plot_inventory_chart(monthly_cat):
    """绘制各类别逐月期初库存变化图。"""
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import matplotlib.ticker as ticker

        # 支持中文
        plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'DejaVu Sans']
        plt.rcParams['axes.unicode_minus'] = False

        fig, ax = plt.subplots(figsize=(14, 7))

        colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', '#8c564b']
        markers = ['o', 's', 'D', '^', 'v', 'p']

        for i, cat in enumerate(CATEGORIES):
            data = monthly_cat[monthly_cat['类别'] == cat].sort_values('月份')
            ax.plot(data['月份'], data['期初库存'] / 10000,  # 转万件
                    marker=markers[i], color=colors[i], linewidth=2,
                    markersize=8, label=cat)

        ax.set_xlabel('月份', fontsize=13)
        ax.set_ylabel('期初库存（万件）', fontsize=13)
        ax.set_title('2026年1-6月 各类别期初库存变化', fontsize=15, fontweight='bold')
        ax.legend(loc='upper left', fontsize=11, ncol=3)
        ax.grid(True, alpha=0.3)
        ax.xaxis.set_major_locator(ticker.MaxNLocator(integer=True))

        # 数值标签
        for i, cat in enumerate(CATEGORIES):
            data = monthly_cat[monthly_cat['类别'] == cat].sort_values('月份')
            for _, row in data.iterrows():
                ax.annotate(f'{row["期初库存"]/10000:.1f}',
                            (row['月份'], row['期初库存']/10000),
                            textcoords="offset points", xytext=(0, 10),
                            fontsize=7, ha='center', color=colors[i])

        plt.tight_layout()
        chart_path = os.path.join(OUTPUT_DIR, "仿真补库数据_库存变化图.png")
        fig.savefig(chart_path, dpi=150, bbox_inches='tight')
        plt.close(fig)
        logging.info(f"[仿真] 图表已保存: {chart_path}")

    except Exception as e:
        logging.warning(f"[仿真] 图表生成失败: {e}")
