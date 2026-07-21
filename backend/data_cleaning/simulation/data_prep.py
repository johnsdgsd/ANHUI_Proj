"""
数据准备模块

从 Excel + DB 加载仿真所需的所有数据，统一转为标准 DataFrame 格式。
严格校验每一步的数据格式、维度、完整性。

输出函数:
    load_initial_inventory()  → DataFrame[ORG_NO, DEV_CODE, STOCK_NUM]
    load_monthly_demand(ym)   → DataFrame[ORG_NO, DEV_CODE, PRE_NUM]
    load_device_specs()       → DataFrame[DEV_CODE, DEV_CLS, DEV_CATEG, PACK_BOX_NUM]
    load_item_costs()         → DataFrame[DEV_CODE, TAX_UP]
    load_monthly_installs()   → DataFrame[ORG_NO, DEV_CODE, MONTH, INSTAL_NUM]
    build_org_county_map()    → dict[子ORG_NO → 县ORG_NO]
    build_device_category_map() → dict[DEV_CODE → 类别名]
"""

import logging
import os
import sys

import numpy as np
import pandas as pd
import requests

# 路径设置
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))          # .../simulation/
_PROJ_DIR = os.path.dirname(os.path.dirname(os.path.dirname(_BASE_DIR)))  # .../Proj/
_BACKEND_DIR = os.path.join(_PROJ_DIR, 'backend')                 # .../Proj/backend/
sys.path.insert(0, _BACKEND_DIR)

# 绕过 api/__init__.py 的导入链，直接加载 fetch_data 模块
import importlib.util as _importlib_util
_fetch_data_path = os.path.join(_BACKEND_DIR, 'api', 'data_api', 'fetch_data.py')
_fetch_spec = _importlib_util.spec_from_file_location("fetch_data", _fetch_data_path)
_fetch_data = _importlib_util.module_from_spec(_fetch_spec)
_fetch_spec.loader.exec_module(_fetch_data)

# 导出常用查询函数
query_adam_spec_code_config = _fetch_data.query_adam_spec_code_config
query_adam_yqm_dmd_pre_by_year_month = _fetch_data.query_adam_yqm_dmd_pre_by_year_month
query_adam_pre_range_info = _fetch_data.query_adam_pre_range_info
query_adam_y_mgt_org = _fetch_data.query_adam_y_mgt_org
query_device_install_data_by_month_range = _fetch_data.query_device_install_data_by_month_range

from data_cleaning.simulation.config import (
    OUTPUT_DIR,
    DATA_DIR, INVENTORY_EXCEL, DEVICE_MAPPING_EXCEL, SMCP_EXCEL, INSTALL_EXCEL,
    CATEGORIES, CATEGORY_TO_DEVCODE, DEVCODE_TO_CATEGORY, DEV_CLS_OVERRIDE,
    INVENTORY_COL_TO_CATEGORY, MAP_TABLE_CLASS_TO_CATEGORY,
    SIM_START_MONTH, SIM_END_MONTH, SIM_YEAR,
)

# ==================== 内部工具 ====================

def _validate_not_empty(df, name):
    """校验 DataFrame 非空。"""
    if df is None or (isinstance(df, pd.DataFrame) and df.empty):
        raise ValueError(f"[数据校验] {name} 为空！")
    return df

def _validate_columns(df, required_cols, name):
    """校验 DataFrame 包含所有必需列。"""
    missing = set(required_cols) - set(df.columns)
    if missing:
        raise ValueError(f"[数据校验] {name} 缺少列: {missing}，实际列: {list(df.columns)}")
    return df

def _validate_no_na(df, cols, name):
    """校验指定列无缺失值。"""
    for c in cols:
        na_count = df[c].isna().sum()
        if na_count > 0:
            raise ValueError(f"[数据校验] {name}.{c} 有 {na_count} 个缺失值")
    return df

def _normalize_org(df, col='ORG_NO'):
    """标准化 ORG_NO：int/float → 去小数点 → 去空格。"""
    df = df.copy()
    # 处理 float64: 34401.0 → '34401'
    if df[col].dtype == 'float64':
        df[col] = df[col].apply(lambda x: str(int(x)) if pd.notna(x) else '')
    else:
        df[col] = df[col].astype(str).str.strip()
    return df

def _normalize_devcode(df, col='DEV_CODE'):
    """标准化 DEV_CODE：int/float → 字符串。"""
    df = df.copy()
    if df[col].dtype in ('int64', 'float64'):
        df[col] = df[col].apply(lambda x: str(int(x)) if pd.notna(x) else '')
    else:
        df[col] = df[col].astype(str).str.strip()
    return df

# ==================== 1. 期初库存 ====================

def load_initial_inventory():
    """
    加载 1月1日期初库存。

    输入: 库存统计表1月1日.xlsx (88行 × 10列)
    处理: 宽表 melt → 长表, 类别 → 典型设备码
    输出: DataFrame[ORG_NO, DEV_CODE, CATEGORY, STOCK_NUM]
          87县 × 6类别 = 522行
    """
    logging.info("[数据] 加载期初库存...")

    df = pd.read_excel(INVENTORY_EXCEL)
    _validate_not_empty(df, "库存统计表")
    _validate_columns(df, ['单位编码'] + list(INVENTORY_COL_TO_CATEGORY.keys()), "库存统计表")

    # 标准化 ORG_NO: float → int → str
    df = _normalize_org(df, '单位编码')
    df.rename(columns={'单位编码': 'ORG_NO'}, inplace=True)

    # 移除总计行：ORG_NO 为空或全0
    df = df[df['ORG_NO'].notna() & (df['ORG_NO'] != '') & ~df['ORG_NO'].str.match(r'^0+$')].copy()
    _validate_not_empty(df, "库存统计表(去总计后)")
    _validate_no_na(df, ['ORG_NO'], "库存统计表")

    # 宽→长: 6个类别列 melt
    id_vars = ['ORG_NO']
    value_vars = [c for c in INVENTORY_COL_TO_CATEGORY.keys() if c in df.columns]

    melted = df.melt(id_vars=id_vars, value_vars=value_vars,
                     var_name='类别列名', value_name='STOCK_NUM')

    # 映射类别名
    melted['CATEGORY'] = melted['类别列名'].map(INVENTORY_COL_TO_CATEGORY)
    unmapped = melted[melted['CATEGORY'].isna()]
    if len(unmapped) > 0:
        logging.warning(f"[数据] {len(unmapped)} 行无法映射类别，已丢弃")
    melted = melted.dropna(subset=['CATEGORY'])

    # 类别 → 典型设备码
    melted['DEV_CODE'] = melted['CATEGORY'].map(CATEGORY_TO_DEVCODE)
    missing_dev = melted[melted['DEV_CODE'].isna()]
    if len(missing_dev) > 0:
        cats = missing_dev['CATEGORY'].unique().tolist()
        raise ValueError(f"[数据] 类别 {cats} 无典型设备码映射")

    # 转为整数库存
    melted['STOCK_NUM'] = pd.to_numeric(melted['STOCK_NUM'], errors='coerce').fillna(0).astype(int)

    result = melted[['ORG_NO', 'DEV_CODE', 'CATEGORY', 'STOCK_NUM']].reset_index(drop=True)

    # 校验
    n_orgs = result['ORG_NO'].nunique()
    n_cats = result['CATEGORY'].nunique()
    total_stock = result['STOCK_NUM'].sum()
    expected_rows = n_orgs * n_cats
    if len(result) != expected_rows:
        logging.warning(f"[数据] 库存表期望 {expected_rows} 行, 实际 {len(result)} 行")

    logging.info(
        f"[数据] 期初库存: {n_orgs}县 × {n_cats}类 = {len(result)}行, "
        f"总库存={total_stock:,}件"
    )
    return result

# ==================== 2. 月度需求预测 ====================

def load_monthly_demand(year, month):
    """
    加载单月需求预测，按设备类别汇总（非单个设备码）。

    输入: 年份(2026), 月份(1-6)
    处理:
        1. 读 SMCP Excel → 41 个设备码
        2. 用设备码归类映射 → 标准类别（A级表/B级表/...）
        3. 同类所有设备码的 PRE_NUM 加总
        4. 分离 BUS_TYPE: λ=01+02, direct=03
        5. 类别 → 典型设备码（给 GA 用）
    输出: DataFrame[ORG_NO, DEV_CODE, LAMBDA_0102, DIRECT_03]
    """
    ym_label = f"{year}-{month:02d}"
    logging.info(f"[数据] 加载 {ym_label} 需求预测 (SMCP Excel, 按类别汇总)...")

    df = _load_demand_from_excel(year, month)

    _validate_not_empty(df, f"需求预测 {ym_label}")
    _validate_columns(df, ['ORG_NO', 'DEV_CODE', 'BUS_TYPE', 'PRE_NUM'], f"需求预测 {ym_label}")

    # 标准化
    df = _normalize_org(df, 'ORG_NO')
    df = _normalize_devcode(df, 'DEV_CODE')

    # ---- 关键修复: 所有设备码 → 类别 → 汇总 ----
    cat_map = build_device_category_map()  # {dev_code: typical_dev_code}
    df['TYPICAL_DEV'] = df['DEV_CODE'].map(cat_map)

    n_before = df['DEV_CODE'].nunique()
    unmapped = df[df['TYPICAL_DEV'].isna()]
    if len(unmapped) > 0:
        lost_pieces = unmapped['PRE_NUM'].sum()
        lost_devs = unmapped['DEV_CODE'].nunique()
        logging.warning(
            f"[数据] {ym_label}: {lost_devs} 个设备码无类别映射, "
            f"丢弃 {len(unmapped)} 行 ({lost_pieces:,}件)"
        )
        # 列出未映射的设备码
        for d in sorted(unmapped['DEV_CODE'].unique()):
            subtotal = unmapped[unmapped['DEV_CODE'] == d]['PRE_NUM'].sum()
            logging.warning(f"    未映射: {d} = {int(subtotal):,}件")

    df = df.dropna(subset=['TYPICAL_DEV']).copy()
    df['DEV_CODE'] = df['TYPICAL_DEV']
    df.drop(columns=['TYPICAL_DEV'], inplace=True)

    if df.empty:
        raise ValueError(f"[数据] {ym_label} 需求预测全部无法映射到类别")

    n_after = df['DEV_CODE'].nunique()
    logging.info(f"[数据] {ym_label}: {n_before}设备码 → {n_after}类别")

    # 分离 BUS_TYPE
    df['BUS_TYPE'] = df['BUS_TYPE'].astype(int)
    df_0102 = df[df['BUS_TYPE'].isin([1, 2])].groupby(
        ['ORG_NO', 'DEV_CODE'], as_index=False)['PRE_NUM'].sum().rename(
        columns={'PRE_NUM': 'LAMBDA_0102'})
    df_03 = df[df['BUS_TYPE'] == 3].groupby(
        ['ORG_NO', 'DEV_CODE'], as_index=False)['PRE_NUM'].sum().rename(
        columns={'PRE_NUM': 'DIRECT_03'})

    result = df_0102.merge(df_03, on=['ORG_NO', 'DEV_CODE'], how='outer').fillna(0)
    result['LAMBDA_0102'] = result['LAMBDA_0102'].astype(int)
    result['DIRECT_03'] = result['DIRECT_03'].astype(int)

    n_orgs = result['ORG_NO'].nunique()
    n_cats = result['DEV_CODE'].nunique()
    total_lam = result['LAMBDA_0102'].sum()
    total_dir = result['DIRECT_03'].sum()

    logging.info(
        f"[数据] {ym_label} 需求: {n_orgs}县 × {n_cats}类别, {len(result)}行, "
        f"λ(01+02)={total_lam:,}件, direct(03)={total_dir:,}件, "
        f"合计={total_lam + total_dir:,}件"
    )
    return result

def _load_demand_from_db(year, month):
    """从 DB 加载需求预测。"""
    try:
        df = query_adam_yqm_dmd_pre_by_year_month(str(year), f'{month:02d}')
        if df is not None and not df.empty:
            # 过滤 PRE_TYPE='03'
            if 'PRE_TYPE' in df.columns:
                df = df[df['PRE_TYPE'].astype(str).str.strip() == '3'].copy()
            # 统一列名
            if 'PRE_NUM' not in df.columns and 'DEMAND_NUM' in df.columns:
                df.rename(columns={'DEMAND_NUM': 'PRE_NUM'}, inplace=True)
            return df[['ORG_NO', 'DEV_CODE', 'PRE_NUM']]
    except Exception as e:
        logging.warning(f"[数据] DB 需求查询失败 ({year}-{month:02d}): {e}")
    return None

def _load_demand_from_excel(year, month):
    """从 SMCP Excel 加载需求预测，保留 BUS_TYPE 列。"""
    df = pd.read_excel(SMCP_EXCEL)
    df = df[
        (df['PRE_YEAR'].astype(int) == year) &
        (df['PRE_MONTH'].astype(int) == month)
    ].copy()
    # PRE_TYPE='03' (季度预测)
    if 'PRE_TYPE' in df.columns:
        df = df[df['PRE_TYPE'].astype(str).str.strip() == '3'].copy()
    return df[['ORG_NO', 'DEV_CODE', 'BUS_TYPE', 'PRE_NUM']]

# ==================== 3. 设备规格 ====================

def load_device_specs():
    """从 DB 加载设备规格，覆写 DEV_CLS 使 GA 按类别独立优化 alpha。"""
    logging.info("[数据] 加载设备规格...")
    df = query_adam_spec_code_config()
    _validate_not_empty(df, "设备规格")
    _validate_columns(df, ['DEV_CODE', 'DEV_CLS', 'DEV_CATEG'], "设备规格")

    df = _normalize_devcode(df, 'DEV_CODE')

    # 只保留 6 个典型设备码
    target_devs = list(CATEGORY_TO_DEVCODE.values())
    df = df[df['DEV_CODE'].isin(target_devs)].copy()

    if len(df) < 6:
        missing = set(target_devs) - set(df['DEV_CODE'].unique())
        logging.warning(f"[数据] 规格表缺少设备码: {missing}")

    # 覆写 DEV_CLS: 每个设备码分配不同 CLS，使 GA 按类别独立优化 alpha
    df['DEV_CLS_ORIG'] = df['DEV_CLS']
    df['DEV_CLS'] = df['DEV_CODE'].map(DEV_CLS_OVERRIDE).fillna(df['DEV_CLS'])
    for _, row in df.iterrows():
        logging.info(f"  {row['DEV_CODE']}: DEV_CLS={row['DEV_CLS_ORIG']} → {row['DEV_CLS']}, "
                     f"DEV_CATEG={row['DEV_CATEG']}")

    # 确保 PACK_BOX_NUM 存在
    if 'PACK_BOX_NUM' not in df.columns:
        df['PACK_BOX_NUM'] = 1

    logging.info(f"[数据] 设备规格: {len(df)}行, DEV_CLS={sorted(df['DEV_CLS'].unique())}")
    return df[['DEV_CODE', 'DEV_CLS', 'DEV_CATEG', 'PACK_BOX_NUM']]

# ==================== 4. 设备成本 ====================

def load_item_costs():
    """从 DB 加载设备单价。"""
    logging.info("[数据] 加载设备成本...")
    df = query_adam_pre_range_info()
    _validate_not_empty(df, "设备成本")

    df = _normalize_devcode(df, 'DEV_CODE')

    # 列名可能是 AVG_PRICE 或 TAX_UP
    price_col = 'TAX_UP' if 'TAX_UP' in df.columns else 'AVG_PRICE'
    if price_col not in df.columns:
        raise ValueError(f"[数据] 设备成本表无价格列，实际列: {list(df.columns)}")

    # 只保留 6 个典型设备码
    target_devs = list(CATEGORY_TO_DEVCODE.values())
    df = df[df['DEV_CODE'].isin(target_devs)].copy()

    if len(df) < 6:
        missing = set(target_devs) - set(df['DEV_CODE'].unique())
        logging.warning(f"[数据] 成本表缺少设备码: {missing}")

    df['TAX_UP'] = pd.to_numeric(df[price_col], errors='coerce').fillna(0)

    logging.info(f"[数据] 设备成本: {len(df)}行")
    for _, r in df.iterrows():
        logging.info(f"  {r['DEV_CODE']} = {r['TAX_UP']:.2f}元")

    return df[['DEV_CODE', 'TAX_UP']]

# ==================== 5. 月安装量 ====================

def load_monthly_installs(inventory_orgs=None):
    """
    加载 1-6 月安装量（Excel），归并到 87 家单位维度。

    归并规则:
        - 5位编码 → 市级，保留
        - 7位编码 → 县级，保留
        - 9位编码 → 供电所级，取前7位归到所属县
        - 归并后不在87家单位中的 → 再取前5位归到市
    设备码按归类映射汇总到 6 个典型设备码。
    BUS_TYPE 全部加总（安装消耗库存，不区分业务类型）。

    Args:
        inventory_orgs: set/None, 87家单位的 ORG_NO 集合，用于过滤

    输出: DataFrame[ORG_NO, DEV_CODE, MONTH, INSTAL_NUM]
    """
    logging.info("[数据] 加载月安装量 (Excel, 归并供电所→县→市)...")

    df = _load_installs_from_excel()
    _validate_not_empty(df, "安装数据")
    _validate_columns(df, ['ORG_NO', 'DEV_CODE', 'INSTAL_DAY', 'BUS_TYPE', 'INSTAL_NUM'], "安装数据")

    df = _normalize_org(df, 'ORG_NO')
    df = _normalize_devcode(df, 'DEV_CODE')
    df['INSTAL_NUM'] = pd.to_numeric(df['INSTAL_NUM'], errors='coerce').fillna(0).astype(int)

    # 提取月份: INSTAL_DAY 是 int(20260519)
    df['INSTAL_DAY_STR'] = df['INSTAL_DAY'].astype(str).str.strip()
    df['MONTH'] = df['INSTAL_DAY_STR'].str[:6].astype(int)

    # 只保留 1-6 月
    df = df[(df['MONTH'] >= 202600 + SIM_START_MONTH) &
            (df['MONTH'] <= 202600 + SIM_END_MONTH)].copy()
    logging.info(f"[数据] 安装原始: {len(df)}行, {df['ORG_NO'].nunique()}个ORG_NO, "
                 f"月={sorted(df['MONTH'].unique())}")

    # ---- Step 1: 供电所(9位)→县(7位) ----
    df['ORG_LEN'] = df['ORG_NO'].str.len()
    before_5 = df[df['ORG_LEN']==5]['ORG_NO'].nunique()
    before_7 = df[df['ORG_LEN']==7]['ORG_NO'].nunique()
    before_9 = df[df['ORG_LEN']==9]['ORG_NO'].nunique()
    logging.info(f"[数据] 归集前: 5位市={before_5}, 7位县={before_7}, 9位所={before_9}")

    # 9位→前7位(县), 7位→本县, 5位→本市
    df['PARENT_ORG'] = df['ORG_NO'].apply(lambda x: x[:7] if len(x) >= 7 else x)

    # ---- Step 2: 不在87家库存单位中的，再往上一级归到市(前5位) ----
    if inventory_orgs is not None:
        unmatched_7 = set(df['PARENT_ORG'].unique()) - inventory_orgs
        if unmatched_7:
            logging.info(f"[数据] 归集后 {len(unmatched_7)} 个ORG_NO 不在87家库存中 → 上归到市(前5位)")
            df['PARENT_ORG'] = df.apply(
                lambda r: r['PARENT_ORG'][:5] if r['PARENT_ORG'] in unmatched_7 else r['PARENT_ORG'],
                axis=1
            )
            # 再检查一次
            still_unmatched = set(df['PARENT_ORG'].unique()) - inventory_orgs
            if still_unmatched:
                lost_rows = df[df['PARENT_ORG'].isin(still_unmatched)]
                logging.warning(
                    f"[数据] {len(still_unmatched)} 个ORG_NO 归到市后仍不在87家中, "
                    f"丢弃 {len(lost_rows)} 行 ({lost_rows['INSTAL_NUM'].sum():,}件)"
                )
                df = df[~df['PARENT_ORG'].isin(still_unmatched)].copy()

    df['ORG_NO'] = df['PARENT_ORG']
    df.drop(columns=['PARENT_ORG', 'ORG_LEN'], inplace=True)
    after_n = df['ORG_NO'].nunique()
    logging.info(f"[数据] 归集后: {after_n}个 ORG_NO (与库存交集={len(set(df['ORG_NO'].unique()) & inventory_orgs) if inventory_orgs else '?'})")

    # ---- Step 3: 设备码归类 → 6类别 ----
    cat_map = build_device_category_map()
    df['MAPPED_DEV'] = df['DEV_CODE'].map(cat_map)
    unmapped = df[df['MAPPED_DEV'].isna()]
    if len(unmapped) > 0:
        logging.info(f"[数据] {unmapped['DEV_CODE'].nunique()} 个设备码无法归类, "
                     f"丢弃 {len(unmapped)} 行 ({unmapped['INSTAL_NUM'].sum():,}件)")
    df = df.dropna(subset=['MAPPED_DEV']).copy()
    df['DEV_CODE'] = df['MAPPED_DEV']
    df.drop(columns=['MAPPED_DEV'], inplace=True)

    # ---- Step 4: 提取 BUS_TYPE=03 实际新装量（用于 direct） ----
    df_03 = df[df['BUS_TYPE'] == 3].copy()
    result_03 = df_03.groupby(['ORG_NO', 'DEV_CODE', 'MONTH'], as_index=False)['INSTAL_NUM'].sum()
    install_03_index = {}
    for _, r in result_03.iterrows():
        install_03_index[(str(r['ORG_NO']).strip(), str(r['DEV_CODE']).strip(), int(r['MONTH']))] = float(r['INSTAL_NUM'])
    logging.info(f"[数据] BUS_TYPE=03 实际新装量(用于direct): {result_03['INSTAL_NUM'].sum():,}件, "
                 f"{result_03.shape[0]}条")

    # ---- Step 5: 按 (ORG_NO, DEV_CODE, MONTH) 汇总（BUS_TYPE 全加） ----
    result = df.groupby(['ORG_NO', 'DEV_CODE', 'MONTH'], as_index=False)['INSTAL_NUM'].sum()

    monthly = result.groupby('MONTH')['INSTAL_NUM'].sum()
    for m, s in monthly.items():
        logging.info(f"  {m}: {s:,}件")

    logging.info(f"[数据] 月安装量: {result.shape[0]}行, {result['ORG_NO'].nunique()}县, "
                 f"总计={result['INSTAL_NUM'].sum():,}件")
    return result, install_03_index

def _load_installs_from_db():
    """从 DB 加载安装数据。"""
    try:
        start_ym = int(f"{SIM_YEAR}{SIM_START_MONTH:02d}")
        end_ym = int(f"{SIM_YEAR}{SIM_END_MONTH:02d}")
        df = query_device_install_data_by_month_range(start_ym, end_ym)
        if df is not None and not df.empty:
            logging.info(f"[数据] DB 安装数据原始列: {list(df.columns)}, {len(df)}行")
            # 精确列名映射（避免 UNIT_CODE/UNIT_NAME 冲突）
            col_map = {}
            for c in df.columns:
                cu = c.upper().strip()
                if cu == 'UNIT_CODE':
                    col_map[c] = 'ORG_NO'
                elif cu == 'DEVICE_CODE':
                    col_map[c] = 'DEV_CODE'
                elif cu == 'STAT_MONTH':
                    col_map[c] = 'INSTAL_DAY'
                elif cu == 'INSTALL_NUM':
                    col_map[c] = 'INSTAL_NUM'
            df = df.rename(columns=col_map)
            # 如果只有 MONTH 没有 INSTAL_DAY，用月份+15构造日期
            if 'INSTAL_DAY' not in df.columns:
                for c in df.columns:
                    if 'MONTH' in c.upper():
                        df['INSTAL_DAY'] = df[c].astype(str) + '15'
                        break
            return df
    except Exception as e:
        logging.warning(f"[数据] DB 安装数据查询失败: {e}")
    return None

def _load_installs_from_excel():
    """从 Excel 加载安装数据。"""
    df = pd.read_excel(INSTALL_EXCEL)
    return df

# ==================== 6. 设备码 → 类别映射 ====================

def build_device_category_map():
    """
    构建 DEV_CODE → 典型设备码的映射。

    逻辑:
    1. 读 设备码归类映射.xlsx
    2. 取"归类"列，匹配 MAP_TABLE_CLASS_TO_CATEGORY → 标准类别
    3. 标准类别 → 典型设备码 (CATEGORY_TO_DEVCODE)
    4. 返回 dict: {原始设备码: 典型设备码}
    """
    df = pd.read_excel(DEVICE_MAPPING_EXCEL)
    _validate_not_empty(df, "设备码归类映射")

    # 列名: 设备码, 设备码名称, 归类, 归类方式, 参考描述 (参考价格)
    dev_col = df.columns[0]       # 设备码
    class_col = df.columns[2]     # 归类

    df = _normalize_devcode(df, dev_col)
    df['归类'] = df[class_col].astype(str).str.strip()

    # 归类 → 标准类别
    df['CATEGORY'] = df['归类'].map(MAP_TABLE_CLASS_TO_CATEGORY)
    unmapped_classes = df[df['CATEGORY'].isna()]['归类'].unique()
    if len(unmapped_classes) > 0:
        logging.info(f"[数据] 设备码归类映射中 {len(unmapped_classes)} 个归类未使用: "
                     f"{sorted(unmapped_classes)}")

    df = df.dropna(subset=['CATEGORY'])

    # 标准类别 → 典型设备码
    df['TYPICAL_DEV'] = df['CATEGORY'].map(CATEGORY_TO_DEVCODE)
    df = df.dropna(subset=['TYPICAL_DEV'])

    # 去重
    mapping = dict(zip(df[dev_col], df['TYPICAL_DEV']))

    # 6 个典型设备码映射到自身
    for dev in CATEGORY_TO_DEVCODE.values():
        mapping[dev] = dev

    logging.info(f"[数据] 设备码→类别映射: {len(mapping)} 个设备码 → 6 类别")
    for cat, dev in CATEGORY_TO_DEVCODE.items():
        count = sum(1 for v in mapping.values() if v == dev)
        logging.info(f"  {cat} ({dev}): {count} 个设备码")

    return mapping

# ==================== 7. 供电所 → 县 映射 ====================

def build_org_county_map():
    """
    构建子级 ORG_NO → 县级 ORG_NO 的映射。

    规则: ORG_NO 按长度分级:
        - 5位: 市级 (如 34401) → 自身
        - 7位: 县级 (如 3440101) → 自身
        - 9+位: 供电所级 (如 344010105) → 取前7位
    """
    logging.info("[数据] 构建 ORG_NO 县级映射...")

    # 先尝试用组织层级表
    try:
        org_df = query_adam_y_mgt_org()
        if org_df is not None and not org_df.empty:
            code_col = 'MGT_ORG_CODE'
            if code_col in org_df.columns:
                org_df = _normalize_org(org_df, code_col)
                org_df['len'] = org_df[code_col].str.len()

                # 7位 = 县级, 9+位 = 下级
                county_codes = set(org_df[org_df['len'] <= 7][code_col])
                mapping = {}
                for _, row in org_df.iterrows():
                    code = row[code_col]
                    if len(code) >= 9:
                        parent = code[:7]
                        if parent in county_codes or True:
                            mapping[code] = parent
                    elif len(code) == 7:
                        mapping[code] = code
                    elif len(code) == 5:
                        mapping[code] = code

                if mapping:
                    logging.info(f"[数据] 组织层级映射: {len(mapping)} 条 (基于 MGT_ORG 表)")
                    return mapping
    except Exception as e:
        logging.warning(f"[数据] 组织层级表查询失败: {e}")

    # 回退: 简单前缀规则
    logging.info("[数据] 使用简单前缀规则构建县级映射")
    return {}  # 由调用方自行处理

# ==================== 8. 市级汇总 ====================

def aggregate_to_city_level(inventory_df):
    """
    将县级(7位) ORG_NO 汇总到市级(5位)。

    库存表包含 16 市 + 71 县。SMCP 需求也同时有市县。
    但安装数据仅 17 市。统一汇总到市级以匹配安装数据。

    Args:
        inventory_df: [ORG_NO, DEV_CODE, CATEGORY, STOCK_NUM]

    Returns:
        DataFrame[ORG_NO, DEV_CODE, CATEGORY, STOCK_NUM] — 16市
    """
    df = inventory_df.copy()
    # 7位县 → 5位市
    df['CITY_ORG'] = df['ORG_NO'].str[:5]
    result = df.groupby(['CITY_ORG', 'DEV_CODE', 'CATEGORY'], as_index=False)['STOCK_NUM'].sum()
    result.rename(columns={'CITY_ORG': 'ORG_NO'}, inplace=True)
    logging.info(
        f"[数据] 市级汇总: {inventory_df['ORG_NO'].nunique()}→{result['ORG_NO'].nunique()}市, "
        f"库存={result['STOCK_NUM'].sum():,}件"
    )
    return result

# ==================== 9. 批量加载 ====================

def load_all_data():
    """
    一次性加载所有静态数据 + 初始库存。

    Returns:
        inventory:   DataFrame[ORG_NO, DEV_CODE, CATEGORY, STOCK_NUM]
        spec_df:      DataFrame[DEV_CODE, DEV_CLS, DEV_CATEG, PACK_BOX_NUM]
        cost_df:      DataFrame[DEV_CODE, TAX_UP]
        install_df:   DataFrame[ORG_NO, DEV_CODE, MONTH, INSTAL_NUM]
    """
    inventory = load_initial_inventory()
    spec_df = load_device_specs()
    cost_df = load_item_costs()
    # 传递库存 ORG_NO 用于过滤安装数据
    valid_orgs = set(inventory['ORG_NO'].unique())
    install_df = load_monthly_installs(inventory_orgs=valid_orgs)

    # 交叉校验 ORG_NO 一致性
    inv_orgs = set(inventory['ORG_NO'].unique())
    inst_orgs = set(install_df['ORG_NO'].unique())
    common = inv_orgs & inst_orgs
    only_inv = inv_orgs - inst_orgs
    only_inst = inst_orgs - inv_orgs

    logging.info(f"[数据] ORG_NO 交叉校验: 库存{len(inv_orgs)}县 ∩ 安装{len(inst_orgs)}县 = {len(common)}共有")
    if only_inv:
        logging.info(f"[数据] 仅库存表有 ({len(only_inv)}县): {sorted(only_inv)[:5]}...")
    if only_inst:
        logging.info(f"[数据] 仅安装表有 ({len(only_inst)}县): {sorted(only_inst)[:5]}...")

    # ---- 保存中间数据 ----
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    inventory.to_excel(os.path.join(OUTPUT_DIR, "中间数据_期初库存.xlsx"), index=False)
    spec_df.to_excel(os.path.join(OUTPUT_DIR, "中间数据_设备规格.xlsx"), index=False)
    cost_df.to_excel(os.path.join(OUTPUT_DIR, "中间数据_设备成本.xlsx"), index=False)
    install_df.to_excel(os.path.join(OUTPUT_DIR, "中间数据_月安装量.xlsx"), index=False)
    logging.info(f"[数据] 中间数据已保存至 {OUTPUT_DIR}")

    return inventory, spec_df, cost_df, install_df
