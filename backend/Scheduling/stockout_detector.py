# -*- coding: utf-8 -*-
"""
缺货检测脚本（DB 版）— 输入日期，返回该日所有 (管理单位, 设备码) 的缺货风险数据 DataFrame

用法:
    from stockout_detector import detect_stockout
    df = detect_stockout('2026-08-10')     # 输入日期 → 输出该日缺货数据 DataFrame

数据全部从数据库读取（HTTP 中间件 /exec/{sql_id}），不再依赖任何 Excel/CSV 文件。
检测维度: (管理单位 ORG, 设备码 DEV_CODE)。

公式（对齐《补库预警和缺货统计.md》与仿真 code0728 缺货跟踪口径）:
    5 天预警:  w₅=min(5,T-t);  R₅=w₅·(D₃-d₃)/(T-t)+max(0,5-(T-t))·D₃/T;  λ₅=5·D₁₂/T
              Demand₅ = R₅+λ₅+z√λ₅        缺货风险 = 库存 I < Demand₅
    10 天补库: w₁₀=min(10,T-t); R₁₀=w₁₀·(D₃-d₃)/(T-t)+max(0,10-(T-t))·D₃/T; λ₁₀=10·D₁₂/T
              Demand₁₀ = R₁₀+λ₁₀+z√λ₁₀    应补库 = max(Demand₁₀ - I, 0)

系统参数（beta 需求满足率 / z 值 / 5·10天窗口）写在函数内。
数据来源:
  D12/D3  → gk-adam-query-adam-yqm-dmd-pre-by-bus-type  (ADAM_YQM_DMD_PRE, BUS_TYPE 01+02→D12, 03→D3; 确认量优先)
  d3      → gk-adam-query-adam-his-day-instal-sample    (ADAM_HIS_DAY_INSTAL_SAMPLE, 轮换 BUS_TYPE=3)
  stock   → gk-adam-query_adam_stock_count_sample_all   (ADAM_STOCK_COUNT_SAMPLE, 04级实时快照)
  pk      → gk-adam-query_adam_spec_code_config         (ADAM_SPEC_CODE_CONFIG.PACK_BOX_NUM)
"""
import os
import sys
import math
from datetime import date, datetime

import pandas as pd
import requests

# 中间件地址：仓库内优先读 API_CONFIG；脚本被拷贝独立使用时回退到环境变量 DB_HOST/DB_PORT（默认 localhost:8081）
try:
    _PROJ = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), *['..'] * 5))
    if _PROJ not in sys.path:
        sys.path.insert(0, _PROJ)
    from backend.config.config import API_CONFIG
    _DB_HOST = API_CONFIG["database"]["host"]
    _DB_PORT = int(API_CONFIG["database"]["port"])
except Exception:
    _DB_HOST = os.environ.get("DB_HOST", "localhost")
    _DB_PORT = int(os.environ.get("DB_PORT", 8081))

_MIDDLEWARE = f"http://{_DB_HOST}:{_DB_PORT}/exec"

# ================================================================
# 系统参数（写在函数内，与 stockout_tracker 口径一致）
# ================================================================
W_SHORT = 5    # 短期预警窗口（工作日）
W_LONG = 10    # 长期补库窗口（工作日）
Z_VALUES = {0.90: 1.282, 0.95: 1.645, 0.975: 1.960, 0.99: 2.326}


# ================================================================
# 内部 HTTP 助手（仅本脚本使用，不改 fetch_data.py）
# ================================================================
def _fetch(sql_id, params=None):
    """调用中间件 /exec/{sql_id} 返回 DataFrame。空结果返回空 DataFrame。"""
    url = f"{_MIDDLEWARE}/{sql_id}"
    resp = requests.post(url, json=params or {}, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, list):
        return pd.DataFrame(data)
    return pd.DataFrame([data]) if data else pd.DataFrame()


def _parse_date(x):
    """日期解析: 'YYYY-MM-DD' 或 date/datetime → date"""
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    return date.fromisoformat(str(x).strip())


# ================================================================
# 缺货判定核心（公式）
# ================================================================
def _stockout_risk(D12, D3, d3, T, t, stock_pcs, pk, beta=0.95):
    """单 (org, dev) 缺货风险指标。D12+D3<=0 返回 None。"""
    if D12 + D3 <= 0:
        return None

    z = Z_VALUES.get(beta, 1.645)
    rem_days = max(1, T - t)                 # 剩余工作日（t=T 时兜底为 1）

    # ---- 5 天预警 ----
    w5 = min(W_SHORT, rem_days)
    remain_rot = max(0.0, D3 - d3)
    R5 = w5 * remain_rot / rem_days + max(0, W_SHORT - rem_days) * D3 / max(T, 1)
    lambda5 = W_SHORT * D12 / max(T, 1)
    Demand5 = R5 + lambda5 + z * math.sqrt(max(0.0, lambda5))

    # ---- 10 天补库 ----
    w10 = min(W_LONG, rem_days)
    R10 = w10 * remain_rot / rem_days + max(0, W_LONG - rem_days) * D3 / max(T, 1)
    lambda10 = W_LONG * D12 / max(T, 1)
    Demand10 = R10 + lambda10 + z * math.sqrt(max(0.0, lambda10))

    has_risk = stock_pcs < Demand5 - 0.01
    add_pcs = max(0.0, Demand10 - stock_pcs)
    add_boxes = math.ceil(add_pcs / pk) if pk > 0 else 0
    shortage = max(0.0, -stock_pcs)

    return {
        'D12': D12, 'D3': D3, 'd3_累计': d3,
        'T': T, 't': t, 'rem_days': rem_days,
        'w5': w5, 'R5': R5, 'lambda5': lambda5, 'Demand5': Demand5,
        'w10': w10, 'R10': R10, 'lambda10': lambda10, 'Demand10': Demand10,
        'stock': stock_pcs,
        'has_risk': has_risk, 'add_pcs': add_pcs, 'add_boxes': add_boxes,
        'shortage': shortage, 'z': z, 'beta': beta, 'pk': pk,
    }


# ================================================================
# 数据加载（全部走 DB）
# ================================================================
def _load_forecast(year, month):
    """月度预测 D12/D3，按 (ORG, DEV_CODE) 聚合。

    返回 dict {(org, dev): (D12, D3)}；D12=BUS_TYPE 01+02、D3=BUS_TYPE 03 的 DMD_NUM 合计。
    """
    df = _fetch("gk-adam-query-adam-yqm-dmd-pre-by-bus-type",
                {"year": str(year), "month": f"{month:02d}"})
    out = {}
    if df.empty:
        return out
    df.columns = [c.upper() for c in df.columns]
    df['ORG'] = df['ORG_NO'].astype(str).str.strip()
    df['DEV'] = df['DEV_CODE'].astype(str).str.strip()
    df['BUS'] = df['BUS_TYPE'].astype(str).str.strip()
    df['Q'] = pd.to_numeric(df['DMD_NUM'], errors='coerce').fillna(0.0)
    d12 = df[df['BUS'].isin(['01', '02'])].groupby(['ORG', 'DEV'])['Q'].sum()
    d3 = df[df['BUS'] == '03'].groupby(['ORG', 'DEV'])['Q'].sum()
    for k in set(d12.index) | set(d3.index):
        out[k] = (float(d12.get(k, 0.0)), float(d3.get(k, 0.0)))
    return out


def _align_org(series, valid_orgs):
    """把安装 ORG 归并到预测出现的组织口径（9→7→5，取第一个命中 valid_orgs 的前缀，否则丢弃）。

    供电所(9位) → 市县(7位) → 地市(5位)，只保留能在预测组织集合中对上的层级。
    """
    valid = set(valid_orgs)

    def norm(o):
        s = str(o).strip()
        for n in (7, 5):
            if len(s) >= n and s[:n] in valid:
                return s[:n]
        return None

    return series.apply(norm)


def _load_install(start_date, end_date, valid_orgs):
    """日安装轮换(d3)，按 (date, ORG, DEV) 汇总。

    入参 YYYYMMDD 字符串（当月1日 ~ 检测日）；ORG 归并到 valid_orgs（预测出现的 04 级组织）。
    返回 DataFrame[date, ORG, DEV, num]（仅轮换 BUS_TYPE）。
    """
    cols = ['date', 'ORG', 'DEV', 'num']
    df = _fetch("gk-adam-query-adam-his-day-instal-sample",
                {"start_date": start_date, "end_date": end_date})
    if df.empty:
        return pd.DataFrame(columns=cols)
    df.columns = [c.upper() for c in df.columns]
    df = df[df['BUS_TYPE'].astype(str).str.strip().isin(['3', '03'])].copy()  # 仅轮换
    df['ORG'] = _align_org(df['ORG_NO'], valid_orgs)
    df = df.dropna(subset=['ORG']).copy()
    df['DEV'] = df['DEV_CODE'].astype(str).str.strip()
    df['num'] = pd.to_numeric(df['INSTAL_NUM'], errors='coerce').fillna(0.0)
    df['date'] = pd.to_datetime(df['INSTAL_DAY'].astype(str).str.strip(), format='%Y%m%d', errors='coerce').dt.date
    df = df.dropna(subset=['date']).copy()
    g = df.groupby(['date', 'ORG', 'DEV'], as_index=False)['num'].sum()
    g.columns = cols
    return g


def _load_stock():
    """实时库存快照，按 (ORG, DEV_CODE) 汇总 STOCK_NUM。返回 dict {(org, dev): stock}。"""
    df = _fetch("gk-adam-query_adam_stock_count_sample_all")
    out = {}
    if df.empty:
        return out
    df.columns = [c.upper() for c in df.columns]
    df['ORG'] = df['MGT_ORG_CODE'].astype(str).str.strip()
    df['DEV'] = df['DEV_CODE_NO'].astype(str).str.strip()
    df['STOCK_NUM'] = pd.to_numeric(df['STOCK_NUM'], errors='coerce').fillna(0.0)
    s = df.groupby(['ORG', 'DEV'])['STOCK_NUM'].sum()
    out = {(o, d): float(v) for (o, d), v in s.items()}
    return out


def _load_pk():
    """每箱件数 PK，按 DEV_CODE。返回 dict {dev: pk}，缺失兜底 4.0。"""
    df = _fetch("gk-adam-query_adam_spec_code_config")
    pk = {}
    if not df.empty:
        df.columns = [c.upper() for c in df.columns]
        if 'DEV_CODE' in df.columns and 'PACK_BOX_NUM' in df.columns:
            for _, r in df.iterrows():
                v = r['PACK_BOX_NUM']
                pk[str(r['DEV_CODE']).strip()] = float(v) if pd.notna(v) else 4.0
    return pk


def _workdays(year):
    """当年全部工作日日期列表（chinese_calendar）。"""
    from chinese_calendar import is_workday
    rows = []
    for mo in range(1, 13):
        mdays = pd.Period(f'{year}-{mo:02d}', 'M').days_in_month
        for d in range(1, mdays + 1):
            dt = date(year, mo, d)
            if is_workday(dt):
                rows.append(dt)
    return rows


# ================================================================
# 缺货检测输出上限：最多保留缺货件数最大的 N 个网点（可配置）
TOP_SHORTAGE_ORGS = 10


# 核心接口：输入日期 → 输出当日缺货风险数据 DataFrame
# ================================================================
def detect_stockout(cur_date, beta=0.95, top_n=TOP_SHORTAGE_ORGS):
    """检测指定日期的缺货风险，只返回存在缺货风险（库存 < Demand5）的组合。

    每行一个 (管理单位, 设备码)，列: ORG, DEV_CODE, 件数, 原始箱数, 当前库存, 日期
       ORG / DEV_CODE : 管理单位(04级) / 设备码 (str)
       件数           : 应补库件数 = max(Demand10 - 当前库存, 0)（int）
       原始箱数       : 应补库箱数 = ceil(件数 / 每箱件数)（int）
       当前库存       : 实时库存快照汇总（件，int）
       日期           : 检测日期（str）

    Args:
        cur_date: 'YYYY-MM-DD' 或 date/datetime
        beta:     需求满足率（默认 0.95）
        top_n:    输出上限，仅保留缺货件数最大的 top_n 个网点（默认 10，可配置）
    """
    cur_date = _parse_date(cur_date)
    year, month = cur_date.year, cur_date.month

    # ---- 加载数据（全部走 DB） ----
    forecast = _load_forecast(year, month)                                    # {(org,dev): (D12,D3)}
    forecast_orgs = {org for (org, _dev) in forecast}                         # 04级预测组织口径
    install = _load_install(f'{cur_date:%Y%m}01', f'{cur_date:%Y%m%d}', forecast_orgs)  # 当月1日~当日（轮换d3）
    stock = _load_stock()                                                     # {(org,dev): 库存}
    pk_map = _load_pk()                                                       # {dev: 每箱件数}

    # ---- T / t（当月工作日 / 截至当日已过工作日） ----
    mo_wd = [d for d in _workdays(year) if d.month == month]
    T = len(mo_wd)
    t = sum(1 for d in mo_wd if d <= cur_date)

    # ---- 逐 (ORG, DEV_CODE) 判定 ----
    rows = []
    for (org, dev), (D12, D3) in forecast.items():
        if D12 + D3 <= 0:
            continue
        d3 = float(install[(install['ORG'] == org) & (install['DEV'] == dev)]['num'].sum())
        I = stock.get((org, dev), 0.0)
        pk = pk_map.get(dev, 4.0)

        r = _stockout_risk(D12, D3, d3, T, t, I, pk, beta=beta)
        if r is None:
            continue
        if r['has_risk']:   # 只保留存在缺货风险的组合
            rows.append({
                'ORG': org,
                'DEV_CODE': dev,
                '件数': int(round(r['add_pcs'])),
                '原始箱数': int(r['add_boxes']),
                '当前库存': int(round(r['stock'])),
                '日期': cur_date.isoformat(),
            })

    result_df = pd.DataFrame(rows, columns=['ORG', 'DEV_CODE', '件数', '原始箱数', '当前库存', '日期'])
    # 最多保留缺货数量(件数)最大的 top_n 个网点
    if not result_df.empty:
        org_shortage = result_df.groupby('ORG')['件数'].sum()
        top_orgs = set(org_shortage.nlargest(top_n).index)
        result_df = result_df[result_df['ORG'].isin(top_orgs)].reset_index(drop=True)
    return result_df


# ================================================================
# 独立运行演示
# ================================================================
if __name__ == '__main__':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

    demo_date = '2026-08-10'
    if len(sys.argv) > 1:
        demo_date = sys.argv[1]
    df = detect_stockout(demo_date)
    print(f"\n=== 缺货检测(DB): {demo_date} ===")
    if df.empty:
        print("无缺货风险")
    else:
        print(f"缺货风险组合: {len(df)} 个, 应补库合计: {int(df['件数'].sum())} 件 / {int(df['原始箱数'].sum())} 箱")
        print(df.to_string(index=False))
