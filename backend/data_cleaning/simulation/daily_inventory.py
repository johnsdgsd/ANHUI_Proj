"""
地市每日库存动态推演

基于仿真推演结果，计算 1-6 月各地市各类别每日库存变化。

输入:
    - output/仿真补库数据_1-6月.xlsx   (月初库存)
    - output/库存推演结果.xlsx → 地市每日配送 (每日配送量)
    - output/ADAM_HIS_DAY_INSTAL_SAMPLE.xlsx  (每日安装量)
    - 二阶段/设备码归类映射.xlsx              (DEV_CODE → 类别映射)

输出:
    - output/地市每日库存_1-6月.xlsx     (明细)
    - output/地市每日库存_分类汇总.xlsx   (汇总)
    - output/地市每日库存_变化曲线.png    (图表)
"""

import logging
import os
import sys
import calendar
from datetime import date, timedelta

import numpy as np
import pandas as pd

# ==================== 路径设置 ====================
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJ_DIR = os.path.dirname(os.path.dirname(os.path.dirname(_BASE_DIR)))

from data_cleaning.simulation.config import (
    OUTPUT_DIR, DATA_DIR,
    MAP_TABLE_CLASS_TO_CATEGORY, CATEGORIES,
    DEVICE_MAPPING_EXCEL, SIM_YEAR,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

PREFIX = "[日库存]"

# ==================== 1. 加载数据 ====================

def load_monthly_inventory():
    """从仿真补库数据加载每月期初库存。

    Returns:
        pd.DataFrame: [月份, ORG_NO, 类别, 期初库存]
    """
    path = os.path.join(OUTPUT_DIR, "仿真补库数据_1-6月.xlsx")
    logging.info(f"{PREFIX} 加载期初库存: {path}")
    df = pd.read_excel(path)
    # 统一 ORG_NO 为字符串
    df['ORG_NO'] = df['ORG_NO'].astype(str).str.strip()
    df['类别'] = df['类别'].astype(str).str.strip()
    df['期初库存'] = pd.to_numeric(df['期初库存'], errors='coerce').fillna(0)

    result = df[['月份', 'ORG_NO', '类别', '期初库存']].copy()
    month1 = result[result['月份'] == 1]
    total = month1['期初库存'].sum()
    logging.info(f"{PREFIX} 1月期初库存: {month1['ORG_NO'].nunique()}单位 × {month1['类别'].nunique()}类别, 总={total:,.0f}件")
    return result


def load_daily_deliveries():
    """加载每日配送数据（已按设备类别映射到标准类别）。

    Returns:
        pd.DataFrame: [日期, ORG_NO, 类别, 配送量]
    """
    path = os.path.join(OUTPUT_DIR, "库存推演结果.xlsx")
    logging.info(f"{PREFIX} 加载每日配送: {path}")
    df = pd.read_excel(path, sheet_name="地市每日配送")

    df['日期'] = pd.to_datetime(df['日期']).dt.date
    df['ORG_NO'] = df['ORG'].astype(str).str.strip()
    df['配送量'] = pd.to_numeric(df['配送只'], errors='coerce').fillna(0).astype(int)

    # 映射 设备类别 → 标准类别
    df['类别'] = df['类别'].map(MAP_TABLE_CLASS_TO_CATEGORY)
    unmapped = df[df['类别'].isna()]
    if len(unmapped) > 0:
        logging.warning(f"{PREFIX} 配送数据 {len(unmapped)} 行无法映射类别: {sorted(unmapped['类别'].unique())}")
    df = df.dropna(subset=['类别'])
    df = df[df['类别'].isin(CATEGORIES)]

    # 按 (日期, ORG_NO, 类别) 汇总
    result = df.groupby(['日期', 'ORG_NO', '类别'], as_index=False)['配送量'].sum()

    # 验证月度合计
    result['月份'] = result['日期'].apply(lambda x: x.month)
    for m in sorted(result['月份'].unique()):
        logging.info(f"  {m}月配送总量: {result[result['月份']==m]['配送量'].sum():,.0f}件")
    result.drop(columns=['月份'], inplace=True)

    total_days = result['日期'].nunique()
    total_orgs = result['ORG_NO'].nunique()
    total_qty = result['配送量'].sum()
    logging.info(f"{PREFIX} 配送数据: {len(result)}行, {total_days}配送日, {total_orgs}单位, 总量={total_qty:,.0f}件")
    return result


def _build_device_category_map():
    """构建 DEV_CODE → 标准类别 的映射（复用 data_prep 逻辑）。"""
    logging.info(f"{PREFIX} 构建设备码→类别映射: {DEVICE_MAPPING_EXCEL}")
    df = pd.read_excel(DEVICE_MAPPING_EXCEL)
    dev_col = df.columns[0]
    class_col = df.columns[2]

    df[dev_col] = df[dev_col].astype(str).str.strip()
    df['归类'] = df[class_col].astype(str).str.strip()

    df['CATEGORY'] = df['归类'].map(MAP_TABLE_CLASS_TO_CATEGORY)
    df = df.dropna(subset=['CATEGORY'])
    df = df[df['CATEGORY'].isin(CATEGORIES)]

    mapping = dict(zip(df[dev_col], df['CATEGORY']))
    logging.info(f"{PREFIX} 设备码映射: {len(mapping)} 个 DEV_CODE → {len(CATEGORIES)} 类别")
    return mapping


def load_daily_installs(valid_orgs=None):
    """加载每日安装数据，归并 ORG_NO + 映射 DEV_CODE→类别。

    Args:
        valid_orgs: set, 有效单位编码（来自库存表），用于过滤和上归

    Returns:
        pd.DataFrame: [日期, ORG_NO, 类别, 安装量]
    """
    path = os.path.join(OUTPUT_DIR, "ADAM_HIS_DAY_INSTAL_SAMPLE.xlsx")
    logging.info(f"{PREFIX} 加载安装数据: {path}")
    df = pd.read_excel(path)

    df['ORG_NO'] = df['ORG_NO'].astype(str).str.strip()
    df['DEV_CODE'] = df['DEV_CODE'].astype(str).str.strip()
    df['INSTAL_NUM'] = pd.to_numeric(df['INSTAL_NUM'], errors='coerce').fillna(0)
    df['INSTAL_DAY_STR'] = df['INSTAL_DAY'].astype(str).str.strip()

    # 过滤 1-6 月
    start_ym = f"{SIM_YEAR}01"
    end_ym = f"{SIM_YEAR}06"
    df = df[(df['INSTAL_DAY_STR'] >= start_ym + '01') & (df['INSTAL_DAY_STR'] <= end_ym + '30')].copy()
    logging.info(f"{PREFIX} 安装原始: {len(df)}行, ORG_NO={df['ORG_NO'].nunique()}, DEV_CODE={df['DEV_CODE'].nunique()}")

    # ---- ORG_NO 归并: 9位→7位(县), 不在库存中的再上归到5位(市) ----
    df['ORG_LEN'] = df['ORG_NO'].str.len()
    before_5 = df[df['ORG_LEN'] == 5]['ORG_NO'].nunique()
    before_7 = df[df['ORG_LEN'] == 7]['ORG_NO'].nunique()
    before_9 = df[df['ORG_LEN'] == 9]['ORG_NO'].nunique()
    logging.info(f"{PREFIX} 安装ORG归集前: 5位市={before_5}, 7位县={before_7}, 9位所={before_9}")

    df['PARENT_ORG'] = df['ORG_NO'].apply(lambda x: x[:7] if len(x) >= 7 else x)

    if valid_orgs is not None:
        unmatched_7 = set(df['PARENT_ORG'].unique()) - valid_orgs
        if unmatched_7:
            logging.info(f"{PREFIX} 安装归集后 {len(unmatched_7)} 个ORG不在库存单位中 → 上归到市(5位)")
            df['PARENT_ORG'] = df.apply(
                lambda r: r['PARENT_ORG'][:5] if r['PARENT_ORG'] in unmatched_7 else r['PARENT_ORG'],
                axis=1
            )
            still_unmatched = set(df['PARENT_ORG'].unique()) - valid_orgs
            if still_unmatched:
                lost_rows = df[df['PARENT_ORG'].isin(still_unmatched)]
                logging.warning(
                    f"{PREFIX} 安装 {len(still_unmatched)} 个ORG上归到市后仍不在库存中, "
                    f"丢弃 {len(lost_rows)} 行 ({lost_rows['INSTAL_NUM'].sum():,.0f}件)"
                )
                df = df[~df['PARENT_ORG'].isin(still_unmatched)].copy()

    # ---- DEV_CODE → 类别 ----
    dev_cat_map = _build_device_category_map()
    df['类别'] = df['DEV_CODE'].map(dev_cat_map)
    before_devs = df['DEV_CODE'].nunique()
    unmapped = df[df['类别'].isna()]
    if len(unmapped) > 0:
        lost_qty = unmapped['INSTAL_NUM'].sum()
        logging.info(f"{PREFIX} 安装数据 {unmapped['DEV_CODE'].nunique()} 个设备码无法归类, "
                     f"丢弃 {len(unmapped)} 行 ({lost_qty:,.0f}件)")
    df = df.dropna(subset=['类别']).copy()
    logging.info(f"{PREFIX} 安装设备归类: {before_devs}设备码 → {df['DEV_CODE'].nunique()}归类后设备码")

    # ---- 按 (日期, ORG_NO, 类别) 汇总 ----
    df['日期'] = pd.to_datetime(df['INSTAL_DAY_STR'], format='%Y%m%d').dt.date
    result = df.groupby(['日期', 'PARENT_ORG', '类别'], as_index=False)['INSTAL_NUM'].sum()
    result.rename(columns={'PARENT_ORG': 'ORG_NO'}, inplace=True)
    result.rename(columns={'INSTAL_NUM': '安装量'}, inplace=True)

    total_qty = result['安装量'].sum()
    total_days = result['日期'].nunique()
    n_orgs = result['ORG_NO'].nunique()
    logging.info(f"{PREFIX} 安装汇总: {len(result)}行, {total_days}安装日, {n_orgs}单位, 总量={total_qty:,.0f}件")
    return result


# ==================== 2. 推演引擎 ====================

def _get_daily_date_range():
    """生成 1月1日 → 6月30日 日期列表。"""
    start = date(SIM_YEAR, 1, 1)
    end = date(SIM_YEAR, 6, 30)
    return [(start + timedelta(days=i)) for i in range((end - start).days + 1)]


def run_daily_simulation():
    """主推演函数。"""
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # ---- 加载数据 ----
    monthly_inv = load_monthly_inventory()
    deliveries = load_daily_deliveries()
    valid_orgs = set(monthly_inv['ORG_NO'].unique())
    installs = load_daily_installs(valid_orgs=valid_orgs)

    # ---- 构建 lookup ----
    # 配送: {(日期, ORG_NO, 类别): 配送量}
    dlv_lookup = {}
    for _, r in deliveries.iterrows():
        dlv_lookup[(r['日期'], r['ORG_NO'], r['类别'])] = r['配送量']

    # 安装: {(日期, ORG_NO, 类别): 安装量}
    inst_lookup = {}
    for _, r in installs.iterrows():
        inst_lookup[(r['日期'], r['ORG_NO'], r['类别'])] = r['安装量']

    # 月初库存: {(月份, ORG_NO, 类别): 期初库存}
    inv_idx = monthly_inv.set_index(['月份', 'ORG_NO', '类别'])['期初库存']

    # 所有维度
    all_orgs = sorted(set(deliveries['ORG_NO'].unique()) | set(installs['ORG_NO'].unique()))
    all_dates = _get_daily_date_range()

    logging.info(f"{PREFIX} 推演维度: {len(all_dates)}天 × {len(all_orgs)}单位 × {len(CATEGORIES)}类别")

    # ---- 逐日推演 ----
    # 1月1日期初库存作为起点，逐日滚动计算，不再每月重置
    current_inv = {}  # {(ORG_NO, 类别): 当前库存}
    for org in all_orgs:
        for cat in CATEGORIES:
            try:
                val = inv_idx.get((1, org, cat), 0)
            except KeyError:
                val = 0
            current_inv[(org, cat)] = float(val)

    all_records = []

    for d in all_dates:
        month = d.month

        for org in all_orgs:
            for cat in CATEGORIES:
                begin = current_inv[(org, cat)]
                dlv = dlv_lookup.get((d, org, cat), 0.0)
                inst = inst_lookup.get((d, org, cat), 0.0)
                end = begin + dlv - inst
                current_inv[(org, cat)] = end

                all_records.append({
                    '日期': d,
                    '月份': month,
                    'ORG_NO': org,
                    '类别': cat,
                    '期初库存': begin,
                    '配送量': dlv,
                    '安装量': inst,
                    '期末库存': end,
                })

    result_df = pd.DataFrame(all_records)
    # 实物件数取整
    for col in ['期初库存', '配送量', '安装量', '期末库存']:
        if col in result_df.columns:
            result_df[col] = result_df[col].round(0).astype(int)
    logging.info(f"{PREFIX} 推演完成: {len(result_df)} 条日记录")

    return result_df


# ==================== 3. 输出 ====================

def _save_excel(result_df):
    """保存明细和汇总 Excel。"""
    # 明细
    path_detail = os.path.join(OUTPUT_DIR, "地市每日库存_1-6月.xlsx")
    result_df.to_excel(path_detail, index=False)
    logging.info(f"{PREFIX} 明细已保存: {path_detail}")

    # 按日期×类别汇总
    cat_summary = result_df.groupby(['日期', '类别'], as_index=False).agg(
        期初库存=('期初库存', 'sum'),
        配送量=('配送量', 'sum'),
        安装量=('安装量', 'sum'),
        期末库存=('期末库存', 'sum'),
    )
    path_summary = os.path.join(OUTPUT_DIR, "地市每日库存_分类汇总.xlsx")
    cat_summary.to_excel(path_summary, index=False)
    logging.info(f"{PREFIX} 汇总已保存: {path_summary}")

    # 打印每月汇总（所有类别加总）
    for month in range(1, 7):
        sub = cat_summary[cat_summary['日期'].apply(lambda x: x.month) == month]
        first_day = sub['日期'].min()
        last_day = sub['日期'].max()
        m_期初 = sub[sub['日期'] == first_day]['期初库存'].sum()
        m_期末 = sub[sub['日期'] == last_day]['期末库存'].sum()
        logging.info(f"  {month}月: 配送={sub['配送量'].sum():,.0f}, 安装={sub['安装量'].sum():,.0f}, "
                     f"月初总库存={m_期初:,.0f}, 月末总库存={m_期末:,.0f}")

    return cat_summary


def _load_org_names():
    """从库存统计表加载单位编码→县名映射。"""
    path = os.path.join(DATA_DIR, "库存统计表1月1日.xlsx")
    logging.info(f"{PREFIX} 加载单位名称: {path}")
    df = pd.read_excel(path)
    # float → int → str，避免 34401.0
    df['单位编码'] = df['单位编码'].apply(
        lambda x: str(int(x)) if pd.notna(x) else ''
    ).str.strip()
    df['县名'] = df['单位（县）'].astype(str).str.strip()
    df = df[df['县名'].notna() & (df['县名'] != '') & (df['县名'] != 'nan')]
    org_names = dict(zip(df['单位编码'], df['县名']))
    logging.info(f"{PREFIX} 单位名称: {len(org_names)} 条")
    return org_names


def _plot_per_org_charts(result_df):
    """为每个单位绘制双子图: A级表单独 + 其他5类合并。"""
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates

        plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'DejaVu Sans']
        plt.rcParams['axes.unicode_minus'] = False

        org_names = _load_org_names()
        orgs = sorted(result_df['ORG_NO'].unique())
        CAT_OTHER = ['B级表', 'C级表', 'D级表', '集中器', '专变终端']
        colors_other = ['#ff7f0e', '#2ca02c', '#d62728', '#9467bd', '#8c564b']

        chart_dir = os.path.join(OUTPUT_DIR, "各市每日库存")
        os.makedirs(chart_dir, exist_ok=True)

        for idx, org in enumerate(orgs):
            org_name = org_names.get(org, org)
            org_df = result_df[result_df['ORG_NO'] == org]

            fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(16, 10), sharex=True)

            # ---- 子图1: A级表 ----
            data_a = org_df[org_df['类别'] == 'A级表'].sort_values('日期')
            dates = [pd.Timestamp(d).to_pydatetime() for d in data_a['日期']]
            ax1.plot(dates, data_a['期末库存'] / 10000,
                     color='#1f77b4', linewidth=1.5, label='A级表')
            ax1.fill_between(dates, 0, data_a['期末库存'] / 10000,
                             color='#1f77b4', alpha=0.1)
            ax1.set_ylabel('A级表 库存（万件）', fontsize=12, color='#1f77b4')
            ax1.grid(True, alpha=0.2)
            ax1.legend(loc='upper right', fontsize=10)

            # ---- 子图2: B/C/D/集中器/专变终端 ----
            for i, cat in enumerate(CAT_OTHER):
                data = org_df[org_df['类别'] == cat].sort_values('日期')
                ax2.plot(dates, data['期末库存'] / 10000,
                         color=colors_other[i], linewidth=1.2, label=cat, alpha=0.85)
            ax2.set_ylabel('其他类别 库存（万件）', fontsize=12)
            ax2.grid(True, alpha=0.2)
            ax2.legend(loc='upper right', fontsize=10, ncol=5)

            # 月份分隔线
            for m in range(2, 7):
                ms = pd.Timestamp(f'{SIM_YEAR}-{m:02d}-01').to_pydatetime()
                for ax in (ax1, ax2):
                    ax.axvline(x=ms, color='gray', linestyle='--', alpha=0.3, linewidth=0.6)

            ax2.xaxis.set_major_locator(mdates.MonthLocator())
            ax2.xaxis.set_major_formatter(mdates.DateFormatter('%m月'))

            fig.suptitle(f'{org_name}（{org}）2026年1-6月 每日库存变化',
                         fontsize=14, fontweight='bold', y=0.98)
            plt.tight_layout()

            # 文件名：用编码+中文名
            safe_name = org_name.replace('/', '_').replace('\\', '_').replace(':', '_')
            chart_path = os.path.join(chart_dir, f'{org}_{safe_name}_每日库存.png')
            fig.savefig(chart_path, dpi=120, bbox_inches='tight')
            plt.close(fig)

            if (idx + 1) % 20 == 0:
                logging.info(f"{PREFIX} 单位图表进度: {idx + 1}/{len(orgs)}")

        logging.info(f"{PREFIX} 87张单位图表已保存: {chart_dir}")

    except Exception as e:
        logging.warning(f"{PREFIX} 单位图表生成失败: {e}")


def _plot_daily_chart(cat_summary):
    """绘制6类别每日库存变化曲线。"""
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import matplotlib.ticker as ticker
        import matplotlib.dates as mdates

        plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'DejaVu Sans']
        plt.rcParams['axes.unicode_minus'] = False

        fig, ax = plt.subplots(figsize=(16, 8))

        colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', '#8c564b']

        for i, cat in enumerate(CATEGORIES):
            data = cat_summary[cat_summary['类别'] == cat].sort_values('日期')
            dates = [pd.Timestamp(d).to_pydatetime() for d in data['日期']]
            ax.plot(dates, data['期末库存'] / 10000,
                    color=colors[i], linewidth=1.5, label=cat, alpha=0.85)

        ax.set_xlabel('日期', fontsize=13)
        ax.set_ylabel('期末库存（万件）', fontsize=13)
        ax.set_title('2026年1-6月 各地市各类别每日库存变化', fontsize=15, fontweight='bold')
        ax.legend(loc='upper right', fontsize=11, ncol=2)
        ax.grid(True, alpha=0.2)

        # 月份分隔线
        for m in range(2, 7):
            month_start = pd.Timestamp(f'{SIM_YEAR}-{m:02d}-01').to_pydatetime()
            ax.axvline(x=month_start, color='gray', linestyle='--', alpha=0.4, linewidth=0.8)

        ax.xaxis.set_major_locator(mdates.MonthLocator())
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%m月'))
        ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: f'{x:.0f}万'))

        plt.tight_layout()
        chart_path = os.path.join(OUTPUT_DIR, "地市每日库存_变化曲线.png")
        fig.savefig(chart_path, dpi=150, bbox_inches='tight')
        plt.close(fig)
        logging.info(f"{PREFIX} 图表已保存: {chart_path}")

    except Exception as e:
        logging.warning(f"{PREFIX} 图表生成失败: {e}")


# ==================== 4. 入口 ====================

if __name__ == "__main__":
    logging.info(f"{PREFIX} 每日库存推演开始 — 2026年1-6月")
    result_df = run_daily_simulation()
    cat_summary = _save_excel(result_df)
    _plot_daily_chart(cat_summary)
    _plot_per_org_charts(result_df)

    # 验证
    jan1 = result_df[result_df['日期'] == date(2026, 1, 1)]
    jun30 = result_df[result_df['日期'] == date(2026, 6, 30)]
    total_dlv = result_df['配送量'].sum()
    total_inst = result_df['安装量'].sum()
    logging.info(f"{PREFIX} 推演结束: 期初={jan1['期初库存'].sum():,.0f}, "
                 f"配送={total_dlv:,.0f}, 安装={total_inst:,.0f}, "
                 f"期末={jun30['期末库存'].sum():,.0f}")
    logging.info(f"{PREFIX} 平衡校验: 期初+配送-安装={jan1['期初库存'].sum() + total_dlv - total_inst:,.0f} "
                 f"vs 期末={jun30['期末库存'].sum():,.0f}")
