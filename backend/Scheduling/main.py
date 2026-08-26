import sys
from flask import Blueprint, request, jsonify
import threading
import logging
import random
import requests
from ortools.sat.python import cp_model  # 必须在 pandas 之前，否则 DLL 冲突
import pandas as pd

pd.set_option('future.no_silent_downcasting', True)  # 彻底消灭类型降级警告
from datetime import datetime
from dateutil.relativedelta import relativedelta
import math
import calendar
from backend.Scheduling.Service_CheckDeliver import run_check_deliver_process
from backend.Scheduling.GetArrPlan import GetArrPlan
from backend.Scheduling.Getworkday import Getworkday
from backend.config.config import API_CONFIG
from backend.api.concurrency_lock import try_acquire, release, busy_json

bp = Blueprint('aps_scheduling', __name__, url_prefix='/api/aps')
logger = logging.getLogger()
logger.setLevel(logging.INFO)
for handler in logger.handlers[:]:
    logger.removeHandler(handler)
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
logger.addHandler(console_handler)

host = API_CONFIG["database"]["host"]
port = API_CONFIG["database"]["port"]
SQL_API_URL = f"http://{host}:{port}/exec"
PK_API_URL = f"http://{host}:{port}/pk/next"  # 发号器接口地址


def generate_safe_id():
    """断网兜底使用的随机ID"""
    return random.randint(100000000000000, 999999999999999)


def fetch_primary_keys(pk_code, num):
    """
    统一调用序列号接口，批量获取主键
    """
    if num <= 0: return []
    try:
        response = requests.post(PK_API_URL, json={"pkCode": pk_code, "num": str(num)}, timeout=10)
        response.raise_for_status()
        keys = response.json()
        if isinstance(keys, list) and len(keys) == num:
            return keys
        else:
            logging.error(
                f"[{pk_code}] 返回的主键数量不匹配！请求:{num}, 返回:{len(keys) if isinstance(keys, list) else '非列表'}")
    except Exception as e:
        logging.error(f"[{pk_code}] 请求主键发号器失败，启动随机兜底机制！错误: {e}")

    # 接口崩溃兜底，防止业务中断
    return [generate_safe_id() for _ in range(num)]


def fetch_data(sql_id, params=None):
    url = f"{SQL_API_URL}/{sql_id}"
    try:
        response = requests.post(url, json=params or {})
        response.raise_for_status()
        data = response.json()
        if not data:
            return pd.DataFrame()
        return pd.DataFrame(data) if isinstance(data, list) else pd.DataFrame([data])
    except Exception as e:
        logging.error(f"查询接口 [{sql_id}] 失败: {e}")
        return pd.DataFrame()


def execute_batch(sql_id, data_list):
    if not data_list: return
    url = f"{SQL_API_URL}/{sql_id}"
    success_count = 0
    for data in data_list:
        try:
            response = requests.post(url, json=data)
            response.raise_for_status()
            resp_json = response.json() if response.text else {}
            if isinstance(resp_json, dict) and resp_json.get('code') in [500, -1]:
                logging.error(f"[{sql_id}] 接口返回失败: {resp_json}")
            else:
                success_count += 1
        except Exception as e:
            logging.error(f"[{sql_id}] 操作数据失败: {e}")
    logging.info(f"[{sql_id}] 成功执行 {success_count}/{len(data_list)} 次数据库操作")


def update_pre_conc_status(preConcId, stat):
    if not preConcId: return
    url = f"{SQL_API_URL}/gk-adam-update_pre_conc_status"
    try:
        response = requests.post(url, json={"pre_conc_id": preConcId, "stat": stat})
        response.raise_for_status()
        logging.info(f"状态更新成功: preConcId [{preConcId}] -> STAT [{stat}]")
    except Exception as e:
        logging.error(f"状态更新失败: preConcId [{preConcId}] 报错: {e}")


# ============================================================
# 到货排程 —— 两阶段
#   一阶段(PHASE=01)：业扩(BUS_TYPE=01)+故障(02) 排上半月
#   二阶段(PHASE=02)：月度补库 = REQ_NUM - 业扩 - 故障 排下半月
# ============================================================
MAX_DAILY_BOX = 2500          # 单日入库物理红线（箱/天）
CAP_DISCOUNT = 0.8            # 入库能力折扣系数（老板要求写死，可调）
EFFECTIVE_CAP = int(MAX_DAILY_BOX * CAP_DISCOUNT)   # 排程有效能力 = 2000 箱/天


def fetch_phase1_demand(preMonth):
    """查需求预测表 ADAM_YQM_DMD_PRE 业扩(01)+故障(02)月度预测量，按 DEV_CODE 汇总（确认量优先）。"""
    year = preMonth[:4]
    month = preMonth[4:6]
    df = fetch_data("gk-adam-query-adam-yqm-dmd-pre-by-bus-type", {"year": year, "month": month})
    if df.empty:
        return {}
    df.columns = [c.upper() for c in df.columns]
    df['DEV_CODE'] = df['DEV_CODE'].astype(str).str.strip().str.replace(r'\.0$', '', regex=True)
    df['BUS'] = df['BUS_TYPE'].astype(str).str.strip()
    df['Q'] = pd.to_numeric(df['DMD_NUM'], errors='coerce').fillna(0.0)
    d12 = df[df['BUS'].isin(['01', '02'])]
    if d12.empty:
        return {}
    return d12.groupby('DEV_CODE')['Q'].sum().to_dict()


def _load_base_data(preMonth):
    """拉取基础数据并推算排程月初的合格库存/待检库存（两阶段共用）。"""
    target_dt = datetime.strptime(preMonth, '%Y%m')
    target_start_str = target_dt.strftime('%Y-%m-%d 00:00:00')
    current_time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    is_forward = current_time_str < target_start_str
    start_bound = current_time_str if is_forward else target_start_str
    end_bound = target_start_str if is_forward else current_time_str
    interval_params = {"start_bound": start_bound, "end_bound": end_bound}

    df_demand = fetch_data("gk-adam-query_replenish_demand", {"stat_month": preMonth})
    df_qua = fetch_data("gk-adam-query_realtime_qua_stock")
    df_orders = fetch_data("gk-adam-query_unused_pur_orders")
    df_mapping = fetch_data("gk-adam-query_aps_pro_dev_mapping")
    df_realtime_pend = fetch_data("gk-adam-query_realtime_pend_stock")
    df_future_arr = fetch_data("gk-adam-query_future_arrivals", interval_params)
    df_future_det = fetch_data("gk-adam-query_future_detections", interval_params)
    df_future_deliv = fetch_data("gk-adam-query_future_deliveries", interval_params)

    dfs_to_clean = [df_demand, df_qua, df_realtime_pend, df_future_arr, df_future_det, df_future_deliv, df_orders, df_mapping]
    for df in dfs_to_clean:
        if not df.empty:
            df.columns = [c.upper() for c in df.columns]
            if 'DEV_CODE' in df.columns:
                df['DEV_CODE'] = df['DEV_CODE'].astype(str).str.strip().str.replace(r'\.0$', '', regex=True)
            if 'DEV_CODE_NO' in df.columns:
                df['DEV_CODE_NO'] = df['DEV_CODE_NO'].astype(str).str.strip().str.replace(r'\.0$', '', regex=True)

    global_scheme_id = None
    if not df_demand.empty and 'GLOBAL_SCHEME_ID' in df_demand.columns:
        first_valid = df_demand['GLOBAL_SCHEME_ID'].dropna()
        if not first_valid.empty:
            global_scheme_id = int(float(first_valid.iloc[0]))

    box_mapping = {}
    if not df_mapping.empty:
        for _, r in df_mapping.iterrows():
            code = str(r.get('DEV_CODE') or r.get('DEV_CODE_NO', ''))
            pack = r.get('PACK_BOX_NUM', r.get('UNITPERBOX', 5))
            try:
                box_mapping[code] = int(pack)
            except:
                box_mapping[code] = 5

    df_demand_agg = (df_demand.groupby('DEV_CODE', as_index=False)['REQ_NUM'].sum()
                     if not df_demand.empty else pd.DataFrame(columns=['DEV_CODE', 'REQ_NUM']))

    # --- 合格品库存: 实时 + 期间检定 - 期间配送 ---
    if not df_qua.empty:
        if 'DEV_CODE_NO' in df_qua.columns:
            df_qua['DEV_CODE'] = df_qua['DEV_CODE_NO']
        if 'QUA_STOCK_NUM' in df_qua.columns:
            df_qua_agg = df_qua.groupby('DEV_CODE', as_index=False)['QUA_STOCK_NUM'].sum()
        else:
            df_qua_agg = pd.DataFrame(columns=['DEV_CODE', 'QUA_STOCK_NUM'])
    else:
        df_qua_agg = pd.DataFrame(columns=['DEV_CODE', 'QUA_STOCK_NUM'])

    if not df_future_det.empty and 'DETECT_NUM' in df_future_det.columns:
        df_det_agg = df_future_det.groupby('DEV_CODE', as_index=False)['DETECT_NUM'].sum()
    else:
        df_det_agg = pd.DataFrame(columns=['DEV_CODE', 'DETECT_NUM'])

    if not df_future_deliv.empty and 'DELIVERED_NUM' in df_future_deliv.columns:
        df_deliv_agg = df_future_deliv.groupby('DEV_CODE', as_index=False)['DELIVERED_NUM'].sum()
    else:
        df_deliv_agg = pd.DataFrame(columns=['DEV_CODE', 'DELIVERED_NUM'])

    df_qua_agg = df_qua_agg.merge(df_det_agg, on='DEV_CODE', how='outer').merge(df_deliv_agg, on='DEV_CODE', how='outer')
    df_qua_agg.fillna(0, inplace=True)
    if is_forward:
        df_qua_agg['QUA_STOCK'] = df_qua_agg['QUA_STOCK_NUM'] + df_qua_agg['DETECT_NUM'] - df_qua_agg['DELIVERED_NUM']
    else:
        df_qua_agg['QUA_STOCK'] = df_qua_agg['QUA_STOCK_NUM'] - df_qua_agg['DETECT_NUM'] + df_qua_agg['DELIVERED_NUM']
    df_qua_agg['QUA_STOCK'] = df_qua_agg['QUA_STOCK'].clip(lower=0)
    df_qua_agg = df_qua_agg[['DEV_CODE', 'QUA_STOCK']]

    # --- 待检库存: 实时 + 期间到货 - 期间检定 ---
    if not df_realtime_pend.empty and 'NOW_PEND_NUM' in df_realtime_pend.columns:
        df_rt_agg = df_realtime_pend.groupby('DEV_CODE', as_index=False)['NOW_PEND_NUM'].sum()
    else:
        df_rt_agg = pd.DataFrame(columns=['DEV_CODE', 'NOW_PEND_NUM'])

    if not df_future_arr.empty and 'ARR_NUM' in df_future_arr.columns:
        df_arr_agg = df_future_arr.groupby('DEV_CODE', as_index=False)['ARR_NUM'].sum()
    else:
        df_arr_agg = pd.DataFrame(columns=['DEV_CODE', 'ARR_NUM'])

    if not df_future_det.empty and 'DETECT_NUM' in df_future_det.columns:
        df_det2_agg = df_future_det.groupby('DEV_CODE', as_index=False)['DETECT_NUM'].sum()
    else:
        df_det2_agg = pd.DataFrame(columns=['DEV_CODE', 'DETECT_NUM'])

    df_pend_agg = df_rt_agg.merge(df_arr_agg, on='DEV_CODE', how='outer').merge(df_det2_agg, on='DEV_CODE', how='outer')
    df_pend_agg.fillna(0, inplace=True)
    if is_forward:
        df_pend_agg['UNQUA_STOCK'] = df_pend_agg['NOW_PEND_NUM'] + df_pend_agg['ARR_NUM'] - df_pend_agg['DETECT_NUM']
    else:
        df_pend_agg['UNQUA_STOCK'] = df_pend_agg['NOW_PEND_NUM'] - df_pend_agg['ARR_NUM'] + df_pend_agg['DETECT_NUM']
    df_pend_agg['UNQUA_STOCK'] = df_pend_agg['UNQUA_STOCK'].clip(lower=0)
    df_pend_agg = df_pend_agg[['DEV_CODE', 'UNQUA_STOCK']]

    return {
        'target_dt': target_dt,
        'current_time_str': current_time_str,
        'df_demand_agg': df_demand_agg,
        'df_qua_agg': df_qua_agg,
        'df_pend_agg': df_pend_agg,
        'df_orders': df_orders,
        'df_mapping': df_mapping,
        'box_mapping': box_mapping,
        'global_scheme_id': global_scheme_id,
    }


def _compute_net_demand(total_req_df, df_qua_agg, df_pend_agg):
    """净需求公式：到货量 = ceil(1.25*(1.25*总需求 - 合格库存) - 待检库存)，取 >0。"""
    df_merged = total_req_df.merge(df_qua_agg, on='DEV_CODE', how='left').merge(df_pend_agg, on='DEV_CODE', how='left')
    df_merged.fillna({'QUA_STOCK': 0, 'UNQUA_STOCK': 0}, inplace=True)
    df_merged['CALC_REQ'] = 1.25 * (1.25 * df_merged['TOTAL_REQ'] - df_merged['QUA_STOCK']) - df_merged['UNQUA_STOCK']
    df_merged['TARGET_REQ'] = df_merged['CALC_REQ'].apply(math.ceil)
    return df_merged[df_merged['TARGET_REQ'] > 0][['DEV_CODE', 'TARGET_REQ']].copy()


def _greedy_split(df_net_demand, df_orders, box_mapping, preMonth, target_dt, current_time_str, global_scheme_id):
    """贪心批次拆分（规格贪心 + 防散件 + 防爆仓），返回 (LotList, month_plan_data)。"""
    lot_list_data = []
    month_plan_data = []

    for _, row in df_net_demand.iterrows():
        dev_code = row['DEV_CODE']
        target_req = int(row['TARGET_REQ'])

        if df_orders.empty:
            continue
        dev_orders = df_orders[df_orders['DEV_CODE'] == dev_code]
        if dev_orders.empty:
            continue

        unique_orders = sorted([int(x) for x in dev_orders['ORDER_NUM'].unique() if int(x) > 0], reverse=True)
        if not unique_orders:
            continue

        M = unique_orders[-1]                          # 绝对最小发货标准
        box_cap = box_mapping.get(dev_code, 5)
        max_pieces_per_batch = EFFECTIVE_CAP * box_cap  # 防爆仓：单批 <= 有效入库能力

        actual_assigned_qty = 0
        dev_lot_list = []

        for order_qty in unique_orders:
            if target_req <= 0:
                break
            num_full_batches = target_req // order_qty
            for _ in range(num_full_batches):
                remaining_in_batch = order_qty
                while remaining_in_batch > 0:
                    if remaining_in_batch <= max_pieces_per_batch:
                        chunk = remaining_in_batch
                    else:
                        chunk = max_pieces_per_batch
                        remainder = remaining_in_batch - chunk
                        if 0 < remainder < M:
                            chunk = remaining_in_batch - M
                            if chunk <= 0:
                                chunk = max_pieces_per_batch
                    dev_lot_list.append({
                        'DEV_CODE_NO': dev_code,
                        'PLAN_ARR_NUM': chunk,
                        'PLAN_ARR_DATE': target_dt.replace(day=1).strftime('%Y-%m-%d %H:%M:%S')
                    })
                    remaining_in_batch -= chunk
                    actual_assigned_qty += chunk
                target_req -= order_qty

        if target_req > 0:
            tail_qty = max(target_req, M)
            remaining_in_batch = tail_qty
            while remaining_in_batch > 0:
                if remaining_in_batch <= max_pieces_per_batch:
                    chunk = remaining_in_batch
                else:
                    chunk = max_pieces_per_batch
                    remainder = remaining_in_batch - chunk
                    if 0 < remainder < M:
                        chunk = remaining_in_batch - M
                        if chunk <= 0:
                            chunk = max_pieces_per_batch
                dev_lot_list.append({
                    'DEV_CODE_NO': dev_code,
                    'PLAN_ARR_NUM': chunk,
                    'PLAN_ARR_DATE': target_dt.replace(day=1).strftime('%Y-%m-%d %H:%M:%S')
                })
                remaining_in_batch -= chunk
                actual_assigned_qty += chunk
            target_req = 0

        if actual_assigned_qty > 0:
            lot_list_data.extend(dev_lot_list)
            month_plan_data.append({
                "MONTH_PLAN_ARR_ID": None,
                "PLAN_ARR_NO": f"MP-{preMonth}-{dev_code}",
                "PRE_YEAR": target_dt.strftime('%Y'),
                "PRE_MONTH": target_dt.strftime('%m'),
                "DEV_CODE": dev_code,
                "PLAN_ARR_NUM": actual_assigned_qty,
                "UPDATE_TIME": current_time_str,
                "GLOBAL_SCHEME_ID": global_scheme_id
            })

    return pd.DataFrame(lot_list_data), month_plan_data


def _build_phase1_occupation(preMonth, box_mapping):
    """查一阶段已排日计划，构造二阶段扣减信息：pre_occupied(每日已占箱数)、phase1 各设备件数。"""
    df = fetch_data("gk-adam-query_day_plan_arr_by_phase", {"target_month": preMonth, "phase": "01"})
    result = {'pre_occupied': {}, 'phase1_total': {}}
    if df.empty:
        return result
    df.columns = [c.upper() for c in df.columns]

    num_days = calendar.monthrange(int(preMonth[:4]), int(preMonth[4:6]))[1]

    for _, r in df.iterrows():
        dev = str(r.get('DEV_CODE', '')).strip()
        if dev.endswith('.0'):
            dev = dev[:-2]
        num = pd.to_numeric(r.get('PLAN_ARR_NUM'), errors='coerce')
        num = 0.0 if pd.isna(num) else float(num)
        result['phase1_total'][dev] = result['phase1_total'].get(dev, 0.0) + num

        cap = box_mapping.get(dev, 5) or 5
        boxes = math.ceil(num / cap) if num > 0 else 0

        d = r.get('PLAN_ARR_DATE')
        day_idx = None
        if d is not None:
            if isinstance(d, str):
                try:
                    day_idx = datetime.strptime(d[:10], '%Y-%m-%d').day - 1
                except Exception:
                    day_idx = None
            else:
                try:
                    day_idx = int(getattr(d, 'day', 0)) - 1
                except Exception:
                    day_idx = None
        if day_idx is not None and 0 <= day_idx < num_days:
            result['pre_occupied'][day_idx] = result['pre_occupied'].get(day_idx, 0) + boxes

    return result


def _total_boxes(LotList, box_mapping):
    total = 0
    for _, r in LotList.iterrows():
        dev = str(r['DEV_CODE_NO']).strip()
        cap = box_mapping.get(dev, 5) or 5
        total += math.ceil(int(r['PLAN_ARR_NUM']) / cap)
    return total


def run_first_phase_process(preMonth, preConcId=None):
    """一阶段：业扩+故障到货，尽量排上半月（前多后少）。"""
    try:
        logging.info(f">>> [一阶段] 开始执行 {preMonth} 到货排程(业扩+故障)...")
        update_pre_conc_status(preConcId, '02')

        base = _load_base_data(preMonth)
        target_dt = base['target_dt']
        current_time_str = base['current_time_str']
        global_scheme_id = base['global_scheme_id']
        df_orders = base['df_orders']
        df_mapping = base['df_mapping']
        box_mapping = base['box_mapping']

        phase1_req = fetch_phase1_demand(preMonth)
        if not phase1_req:
            logging.info("一阶段业扩+故障需求为空，无需排程。")
            update_pre_conc_status(preConcId, '03')
            return

        total_req_df = pd.DataFrame(
            [(k, float(v)) for k, v in phase1_req.items()], columns=['DEV_CODE', 'TOTAL_REQ'])
        df_net_demand = _compute_net_demand(total_req_df, base['df_qua_agg'], base['df_pend_agg'])
        if df_net_demand.empty:
            logging.info("一阶段净需求为空，无需排程。")
            update_pre_conc_status(preConcId, '03')
            return

        LotList, _ = _greedy_split(df_net_demand, df_orders, box_mapping, preMonth, target_dt, current_time_str, global_scheme_id)
        if LotList.empty:
            logging.warning("一阶段匹配不到可用订单，排程结束。")
            update_pre_conc_status(preConcId, '03')
            return

        # ILP 之外确定结束自然日：初始 = 前半个月(前 num_days//2 天)，装不下则往后延长
        year = int(preMonth[:4])
        month = int(preMonth[4:6])
        num_days = calendar.monthrange(year, month)[1]
        end_index = max(0, num_days // 2 - 1)   # 前半个月最后一天(0-based)
        total_boxes = _total_boxes(LotList, box_mapping)
        while end_index < num_days - 1 and (end_index + 1) * EFFECTIVE_CAP < total_boxes:
            end_index += 1
        logging.info(f"一阶段截止自然日: {end_index + 1} 号 (共 {num_days} 天, 总箱数 {total_boxes})")

        work_days = Getworkday(int(preMonth))
        ARR_PLAN_RESULT = GetArrPlan(LotList, df_mapping, work_days, end_index=end_index, max_cap=EFFECTIVE_CAP)

        # 只删/写一阶段(PHASE=01)日计划，不碰月计划
        del_params = {
            "pre_year": target_dt.strftime('%Y'),
            "pre_month": target_dt.strftime('%m'),
            "pre_month_str": preMonth,
            "phase": "01"
        }
        execute_batch("gk-adam-delete_day_plan_arr", [del_params])

        day_plan_data = []
        if not ARR_PLAN_RESULT.empty:
            day_ids = fetch_primary_keys("SEQ_ADAM_DAY_PLAN_ARR_PRE", len(ARR_PLAN_RESULT))
            for i, (idx, row) in enumerate(ARR_PLAN_RESULT.iterrows()):
                plan_date = row['PLAN_ARR_DATE']
                if pd.notnull(plan_date):
                    plan_date_str = plan_date.strftime('%Y-%m-%d %H:%M:%S') if not isinstance(plan_date, str) else plan_date
                else:
                    plan_date_str = current_time_str
                day_plan_data.append({
                    "DAY_PLAN_ARR_PRE_ID": day_ids[i],
                    "PLAN_ARR_NO": f"DP-{preMonth}-{row['DEV_CODE_NO']}-{idx}",
                    "DEV_CODE": row['DEV_CODE_NO'],
                    "PLAN_ARR_NUM": int(row['PLAN_ARR_NUM']),
                    "PLAN_ARR_DATE": plan_date_str,
                    "UPDATE_TIME": current_time_str,
                    "PLAN_STAT": "01",
                    "GLOBAL_SCHEME_ID": global_scheme_id,
                    "PHASE": "01"
                })

        execute_batch("gk-adam-insert_day_plan_arr_batch", day_plan_data)
        logging.info(f">>> [一阶段] {preMonth} 任务结束。")
        update_pre_conc_status(preConcId, '03')
    except Exception as e:
        logging.error(f">>> [一阶段错误] {str(e)}", exc_info=True)
        update_pre_conc_status(preConcId, '04')


def run_full_aps_process(preMonth, preConcId=None):
    """二阶段：月度补库 = REQ_NUM - 业扩 - 故障，排下半月，每日能力扣一阶段已占。"""
    try:
        logging.info(f">>> [二阶段] 开始执行 {preMonth} 到货排程(月度补库)...")
        update_pre_conc_status(preConcId, '02')

        base = _load_base_data(preMonth)
        target_dt = base['target_dt']
        current_time_str = base['current_time_str']
        global_scheme_id = base['global_scheme_id']
        df_orders = base['df_orders']
        df_mapping = base['df_mapping']
        box_mapping = base['box_mapping']

        df_demand_agg = base['df_demand_agg']
        if df_demand_agg.empty:
            logging.info("补货需求为空，无需排程。")
            update_pre_conc_status(preConcId, '03')
            return

        phase1_req = fetch_phase1_demand(preMonth)
        df_demand_agg['TOTAL_REQ'] = df_demand_agg['REQ_NUM'] - df_demand_agg['DEV_CODE'].map(phase1_req).fillna(0)
        total_req_df = df_demand_agg[df_demand_agg['TOTAL_REQ'] > 0][['DEV_CODE', 'TOTAL_REQ']].copy()
        if total_req_df.empty:
            logging.info("二阶段补库需求(REQ_NUM-业扩-故障)为空，无需排程。")
            update_pre_conc_status(preConcId, '03')
            return

        df_net_demand = _compute_net_demand(total_req_df, base['df_qua_agg'], base['df_pend_agg'])
        if df_net_demand.empty:
            logging.info("二阶段净需求为空，无需排程。")
            update_pre_conc_status(preConcId, '03')
            return

        LotList, month_plan_data = _greedy_split(df_net_demand, df_orders, box_mapping, preMonth, target_dt, current_time_str, global_scheme_id)
        if LotList.empty:
            logging.warning("二阶段匹配不到可用订单，排程结束。")
            update_pre_conc_status(preConcId, '03')
            return

        # 查一阶段已排 -> pre_occupied / phase1 总量
        occ = _build_phase1_occupation(preMonth, box_mapping)
        pre_occupied = occ['pre_occupied']
        phase1_total = occ['phase1_total']

        # 二阶段从第二周第一天开始(自然日索引)，与一阶段重叠日期靠 pre_occupied 扣减
        year = int(preMonth[:4])
        month = int(preMonth[4:6])
        first_weekday = datetime(year, month, 1).weekday()   # 0=周一
        start_index = 7 - first_weekday                       # 第二周第一天(0-based)
        logging.info(f"二阶段起始自然日: 第 {start_index + 1} 号, 一阶段已占自然日 {len(pre_occupied)}")

        work_days = Getworkday(int(preMonth))
        ARR_PLAN_RESULT = GetArrPlan(LotList, df_mapping, work_days,
                                     start_index=start_index, pre_occupied=pre_occupied, max_cap=EFFECTIVE_CAP)

        # 月计划不分阶段：总量 = 一阶段量 + 二阶段量
        for data in month_plan_data:
            dev = data['DEV_CODE']
            data['PLAN_ARR_NUM'] = int(data['PLAN_ARR_NUM'] + phase1_total.get(dev, 0.0))

        # 删旧数据：月计划(全删) + 二阶段(PHASE=02)日计划
        del_params = {
            "pre_year": target_dt.strftime('%Y'),
            "pre_month": target_dt.strftime('%m'),
            "pre_month_str": preMonth
        }
        execute_batch("gk-adam-delete_month_plan_arr", [del_params])
        del_day_params = dict(del_params)
        del_day_params["phase"] = "02"
        execute_batch("gk-adam-delete_day_plan_arr", [del_day_params])

        if month_plan_data:
            month_ids = fetch_primary_keys("SEQ_ADAM_MONTH_PLAN_ARR_PRE", len(month_plan_data))
            for i, data in enumerate(month_plan_data):
                data["MONTH_PLAN_ARR_ID"] = month_ids[i]
        execute_batch("gk-adam-insert_month_plan_arr_batch", month_plan_data)

        day_plan_data = []
        if not ARR_PLAN_RESULT.empty:
            day_ids = fetch_primary_keys("SEQ_ADAM_DAY_PLAN_ARR_PRE", len(ARR_PLAN_RESULT))
            for i, (idx, row) in enumerate(ARR_PLAN_RESULT.iterrows()):
                plan_date = row['PLAN_ARR_DATE']
                if pd.notnull(plan_date):
                    plan_date_str = plan_date.strftime('%Y-%m-%d %H:%M:%S') if not isinstance(plan_date, str) else plan_date
                else:
                    plan_date_str = current_time_str
                day_plan_data.append({
                    "DAY_PLAN_ARR_PRE_ID": day_ids[i],
                    "PLAN_ARR_NO": f"DP-{preMonth}-{row['DEV_CODE_NO']}-{idx}",
                    "DEV_CODE": row['DEV_CODE_NO'],
                    "PLAN_ARR_NUM": int(row['PLAN_ARR_NUM']),
                    "PLAN_ARR_DATE": plan_date_str,
                    "UPDATE_TIME": current_time_str,
                    "PLAN_STAT": "01",
                    "GLOBAL_SCHEME_ID": global_scheme_id,
                    "PHASE": "02"
                })

        execute_batch("gk-adam-insert_day_plan_arr_batch", day_plan_data)
        logging.info(f">>> [二阶段] {preMonth} 任务结束。")
        update_pre_conc_status(preConcId, '03')
    except Exception as e:
        logging.error(f">>> [二阶段错误] {str(e)}", exc_info=True)
        update_pre_conc_status(preConcId, '04')


@bp.route('/plan/firstRun', methods=['POST'])
def handle_first_run_request():
    try:
        data = request.get_json() or {}
        preMonth = data.get('preMonth')
        if not preMonth:
            next_month_dt = datetime.now() + relativedelta(months=1)
            preMonth = next_month_dt.strftime('%Y%m')
        else:
            preMonth = str(preMonth)
        preConcId = data.get('preConcId')

        # 并发保护：与二阶段 /plan/run 共用一把锁（写同一张日/月到货计划表）
        LOCK_KEY = 'scheduling-plan'
        if not try_acquire(LOCK_KEY, f"到货排程一阶段(业扩+故障) {preMonth} preConcId={preConcId}"):
            return jsonify(busy_json(LOCK_KEY)), 409

        def _wrapped():
            try:
                run_first_phase_process(preMonth, preConcId)
            finally:
                release(LOCK_KEY)

        try:
            threading.Thread(target=_wrapped, daemon=True).start()
        except Exception:
            release(LOCK_KEY)
            raise

        return jsonify({"code": 200, "msg": f"一阶段排程生成中: {preMonth}", "preConcId": preConcId}), 200
    except Exception as e:
        return jsonify({"code": 500, "msg": str(e)}), 500


@bp.route('/plan/run', methods=['POST'])
def handle_run_request():
    try:
        data = request.get_json() or {}

        preMonth = data.get('preMonth')
        if not preMonth:
            next_month_dt = datetime.now() + relativedelta(months=1)
            preMonth = next_month_dt.strftime('%Y%m')
        else:
            preMonth = str(preMonth)

        preConcId = data.get('preConcId')

        # 并发保护：同一接口同一时间只允许一次调用，拿不到锁立即 409
        LOCK_KEY = 'scheduling-plan'
        if not try_acquire(LOCK_KEY, f"到货排程二阶段(月度补库) {preMonth} preConcId={preConcId}"):
            return jsonify(busy_json(LOCK_KEY)), 409

        def _wrapped():
            try:
                run_full_aps_process(preMonth, preConcId)
            finally:
                release(LOCK_KEY)

        try:
            threading.Thread(target=_wrapped, daemon=True).start()
        except Exception:
            release(LOCK_KEY)
            raise

        return jsonify({"code": 200, "msg": f"排程生成中: {preMonth}", "preConcId": preConcId}), 200
    except Exception as e:
        return jsonify({"code": 500, "msg": str(e)}), 500


@bp.route('/plan/check_deliver/run', methods=['POST'])
def handle_check_deliver_request():
    try:
        data = request.get_json() or {}
        if 'preTime' not in data:
            return jsonify({"code": 400, "msg": "参数错误，必须包含 preTime (例如: 20260501)"}), 400

        raw_date_str = str(data['preTime']).strip()
        preConcId = data.get('preConcId')
        comp_flag = data.get('comp_flag', '02')
        dt_obj = datetime.strptime(raw_date_str, '%Y%m%d')
        algorithm_date_str = dt_obj.strftime('%Y-%m-%d')

        # 并发保护：同一接口同一时间只允许一次调用，拿不到锁立即 409
        LOCK_KEY = 'scheduling-check-deliver'
        if not try_acquire(LOCK_KEY, f"检定+配送滚动排程 {raw_date_str} preConcId={preConcId} comp_flag={comp_flag}"):
            return jsonify(busy_json(LOCK_KEY)), 409

        def _wrapped():
            try:
                run_check_deliver_process(algorithm_date_str, preConcId, comp_flag)
            finally:
                release(LOCK_KEY)

        try:
            threading.Thread(target=_wrapped, daemon=True).start()
        except Exception:
            release(LOCK_KEY)
            raise

        return jsonify({
            "code": 200,
            "msg": f"检定和配送排程后台生成中, 接收参数: {raw_date_str}, 算法已识别为: {algorithm_date_str}",
            "preConcId": preConcId
        }), 200

    except ValueError:
        return jsonify({"code": 400, "msg": "日期格式错误，请使用 8位数字 的 YYYYMMDD 格式，例如: 20260501"}), 400
    except Exception as e:
        return jsonify({"code": 500, "msg": str(e)}), 500


if __name__ == '__main__':
    # 独立调试入口：从项目根运行 python -m backend.Scheduling.main
    from flask import Flask
    _app = Flask(__name__)
    _app.register_blueprint(bp)
    _app.run(host='0.0.0.0', port=2500)