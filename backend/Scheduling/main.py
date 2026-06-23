import sys
from flask import Blueprint, request, jsonify
import threading
import logging
import random
import requests
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)  # 彻底消灭类型降级警告
from datetime import datetime
from dateutil.relativedelta import relativedelta
import math
# 在 main.py 顶部追加导入
from backend.Scheduling.Service_CheckDeliver import run_check_deliver_process
from backend.config.config import API_CONFIG
from backend.Scheduling.GetArrPlan import GetArrPlan
from backend.Scheduling.Getworkday import Getworkday

# 创建蓝图
bp = Blueprint('aps_scheduling', __name__, url_prefix='/api/aps')
host = API_CONFIG["database"]["host"]
port = API_CONFIG["database"]["port"]
SQL_API_URL = f"http://{host}:{port}/exec"
PK_API_URL =  f"http://{host}:{port}/pk/next"

logger = logging.getLogger()
logger.setLevel(logging.INFO)
# 清理可能被 Flask 劫持的旧输出通道
for handler in logger.handlers[:]:
    logger.removeHandler(handler)
# 建立新的屏幕输出通道，并强制刷新
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
logger.addHandler(console_handler)


def generate_safe_id():
    """生成15位安全ID，防止达梦数据库 NUMBER(16,0) 溢出"""
    return random.randint(100000000000000, 999999999999999)

def fetch_primary_keys(pk_code, num):
    """
    【新增】统一调用序列号接口，批量获取主键
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
    if not data_list:
        return
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
            logging.error(f"[{sql_id}] 插入单条数据失败: {e}")

    logging.info(f"[{sql_id}] 成功插入 {success_count}/{len(data_list)} 条数据")



def update_pre_conc_status(preConcId, stat):
    """辅助函数：更新预测结论表的状态"""
    if not preConcId:
        return
    url = f"{SQL_API_URL}/gk-adam-update_pre_conc_status"
    try:
        # 传给 Java 后端的 key 依然叫 pre_conc_id，因为 DS_SQL 里配置的是 #{pre_conc_id}
        response = requests.post(url, json={"pre_conc_id": preConcId, "stat": stat})
        response.raise_for_status()
        logging.info(f"状态更新成功: preConcId [{preConcId}] -> STAT [{stat}]")
    except Exception as e:
        logging.error(f"状态更新失败: preConcId [{preConcId}] 报错: {e}")


def run_full_aps_process(preMonth, preConcId=None):
    try:
        logging.info(f">>> [开始] 执行 {preMonth} 任务...")
        update_pre_conc_status(preConcId, '02')

        target_dt = datetime.strptime(preMonth, '%Y%m')
        target_start_str = target_dt.strftime('%Y-%m-%d 00:00:00')

        prev_month_dt = target_dt - relativedelta(months=2)
        prev_year = prev_month_dt.strftime('%Y')
        prev_month = prev_month_dt.strftime('%m')
        current_time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        logging.info("提取基础业务数据...")
        df_demand = fetch_data("gk-adam-query_replenish_demand", {"stat_month": preMonth})
        df_qua = fetch_data("gk-adam-query_qua_stock", {"sam_year": prev_year, "sam_month": prev_month})
        df_orders = fetch_data("gk-adam-query_unused_pur_orders")
        df_mapping = fetch_data("gk-adam-query_aps_pro_dev_mapping")

        is_forward = current_time_str < target_start_str
        start_bound = current_time_str if is_forward else target_start_str
        end_bound = target_start_str if is_forward else current_time_str

        interval_params = {
            "start_bound": start_bound,
            "end_bound": end_bound
        }

        logging.info(f"推演 {preMonth} 月初的待检库存状态...")
        df_realtime_pend = fetch_data("gk-adam-query_realtime_pend_stock")
        df_future_arr = fetch_data("gk-adam-query_future_arrivals", interval_params)
        df_future_det = fetch_data("gk-adam-query_future_detections", interval_params)

        if df_demand.empty:
            logging.info("补货需求为空，无需排程。")
            update_pre_conc_status(preConcId, '03')
            return

        dfs_to_clean = [df_demand, df_qua, df_realtime_pend, df_future_arr, df_future_det, df_orders, df_mapping]
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

        logging.info("开始聚合实时数据，推算排程月初库存...")
        df_demand_agg = df_demand.groupby('DEV_CODE', as_index=False)['REQ_NUM'].sum()

        if not df_qua.empty and 'QUA_STOCK' in df_qua.columns:
            df_qua_agg = df_qua.groupby('DEV_CODE', as_index=False)['QUA_STOCK'].sum()
        else:
            df_qua_agg = pd.DataFrame(columns=['DEV_CODE', 'QUA_STOCK'])

        if not df_realtime_pend.empty and 'NOW_PEND_NUM' in df_realtime_pend.columns:
            df_rt_agg = df_realtime_pend.groupby('DEV_CODE', as_index=False)['NOW_PEND_NUM'].sum()
        else:
            df_rt_agg = pd.DataFrame(columns=['DEV_CODE', 'NOW_PEND_NUM'])

        if not df_future_arr.empty and 'ARR_NUM' in df_future_arr.columns:
            df_arr_agg = df_future_arr.groupby('DEV_CODE', as_index=False)['ARR_NUM'].sum()
        else:
            df_arr_agg = pd.DataFrame(columns=['DEV_CODE', 'ARR_NUM'])

        if not df_future_det.empty and 'DETECT_NUM' in df_future_det.columns:
            df_det_agg = df_future_det.groupby('DEV_CODE', as_index=False)['DETECT_NUM'].sum()
        else:
            df_det_agg = pd.DataFrame(columns=['DEV_CODE', 'DETECT_NUM'])

        df_pend_agg = df_rt_agg.merge(df_arr_agg, on='DEV_CODE', how='outer').merge(df_det_agg, on='DEV_CODE',
                                                                                    how='outer')
        df_pend_agg.fillna(0, inplace=True)

        if is_forward:
            df_pend_agg['UNQUA_STOCK'] = df_pend_agg['NOW_PEND_NUM'] + df_pend_agg['ARR_NUM'] - df_pend_agg[
                'DETECT_NUM']
        else:
            df_pend_agg['UNQUA_STOCK'] = df_pend_agg['NOW_PEND_NUM'] - df_pend_agg['ARR_NUM'] + df_pend_agg[
                'DETECT_NUM']

        df_pend_agg['UNQUA_STOCK'] = df_pend_agg['UNQUA_STOCK'].clip(lower=0)
        df_pend_agg = df_pend_agg[['DEV_CODE', 'UNQUA_STOCK']]

        df_merged = df_demand_agg.merge(df_qua_agg, on='DEV_CODE', how='left')
        df_merged = df_merged.merge(df_pend_agg, on='DEV_CODE', how='left')
        df_merged.fillna({'QUA_STOCK': 0, 'UNQUA_STOCK': 0}, inplace=True)

        df_merged['CALC_REQ'] = 1.25 * (1.25 * df_merged['REQ_NUM'] - df_merged['QUA_STOCK']) - df_merged['UNQUA_STOCK']
        df_merged['TARGET_REQ'] = df_merged['CALC_REQ'].apply(math.ceil)
        df_net_demand = df_merged[df_merged['TARGET_REQ'] > 0].copy()

        lot_list_data = []
        month_plan_data = []

        for _, row in df_net_demand.iterrows():
            dev_code = row['DEV_CODE']
            target_req = int(row['TARGET_REQ'])

            if df_orders.empty: continue

            dev_orders = df_orders[df_orders['DEV_CODE'] == dev_code]
            if dev_orders.empty: continue

            # =========================================================================
            #严格基于订单模板 + 绝对防爆仓 + 绝对不产生散件
            # =========================================================================
            unique_orders = sorted([int(x) for x in dev_orders['ORDER_NUM'].unique() if int(x) > 0], reverse=True)
            if not unique_orders: continue

            M = unique_orders[-1]  # 绝对最小发货标准
            box_cap = box_mapping.get(dev_code, 5)
            max_pieces_per_batch = 2500 * box_cap

            actual_assigned_qty = 0
            dev_lot_list = []

            # 1. 规格贪心切分：从大到小吃需求
            for order_qty in unique_orders:
                if target_req <= 0: break

                num_full_batches = target_req // order_qty

                for _ in range(num_full_batches):
                    remaining_in_batch = order_qty

                    # 循环切分当前订单规格
                    while remaining_in_batch > 0:
                        # 场景 A: 剩下的小于库房极限，一刀切完，绝不爆仓
                        if remaining_in_batch <= max_pieces_per_batch:
                            chunk = remaining_in_batch
                        # 场景 B: 剩下的大于库房极限，需要顶着极限切一刀
                        else:
                            chunk = max_pieces_per_batch
                            remainder = remaining_in_batch - chunk

                            # 核心防散件逻辑：如果切完极限后，剩下的尾巴变成散件(小于 M)了
                            if 0 < remainder < M:
                                # 调整当前刀法：把当前这一刀切小一点，从而给最后正好留出 M
                                chunk = remaining_in_batch - M
                                # 极端物理异常：如果库房极限比 M 还小，为了【绝对不爆仓】，只能截断极限
                                if chunk <= 0:
                                    chunk = max_pieces_per_batch

                        dev_lot_list.append({
                            'DEV_CODE_NO': dev_code,
                            'PLAN_ARR_NUM': chunk,
                            'PLAN_ARR_DATE': target_dt.replace(day=1).strftime('%Y-%m-%d %H:%M:%S')
                        })
                        remaining_in_batch -= chunk
                        actual_assigned_qty += chunk

                    # 扣除已安排的需求
                    target_req -= order_qty

            # 2. 尾批兜底：如果还剩一点点零散需求未被吃掉
            if target_req > 0:
                # 铁律：尾批订单很小，强制拉高到最小采购量 M
                tail_qty = max(target_req, M)

                remaining_in_batch = tail_qty
                while remaining_in_batch > 0:
                    # 使用与上面完全一样的绝对安全刀法
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

        LotList = pd.DataFrame(lot_list_data)

        if LotList.empty:
            logging.warning("匹配不到可用订单，排程结束。")
            update_pre_conc_status(preConcId, '03')
            return

        if month_plan_data:
            month_ids = fetch_primary_keys("SEQ_ADAM_MONTH_PLAN_ARR_PRE", len(month_plan_data))
            for i, data in enumerate(month_plan_data):
                data["MONTH_PLAN_ARR_ID"] = month_ids[i]

        logging.info(f"开始排程算法，共生成 {len(LotList)} 个有效批次...")
        work_days = Getworkday(int(preMonth))
        ARR_PLAN_RESULT = GetArrPlan(LotList, df_mapping, work_days)

        logging.info("回写数据库前，清理当月旧的到货排程数据...")
        del_arr_params = {
            "pre_year": target_dt.strftime('%Y'),
            "pre_month": target_dt.strftime('%m'),
            "pre_month_str": preMonth
        }
        execute_batch("gk-adam-delete_month_plan_arr", [del_arr_params])
        execute_batch("gk-adam-delete_day_plan_arr", [del_arr_params])

        logging.info("开始回写数据库...")
        execute_batch("gk-adam-insert_month_plan_arr_batch", month_plan_data)

        day_plan_data = []
        if not ARR_PLAN_RESULT.empty:
            day_ids = fetch_primary_keys("SEQ_ADAM_DAY_PLAN_ARR_PRE", len(ARR_PLAN_RESULT))
            for i, (idx, row) in enumerate(ARR_PLAN_RESULT.iterrows()):
                plan_date = row['PLAN_ARR_DATE']
                if pd.notnull(plan_date):
                    plan_date_str = plan_date.strftime('%Y-%m-%d %H:%M:%S') if not isinstance(plan_date,
                                                                                              str) else plan_date
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
                    "GLOBAL_SCHEME_ID": global_scheme_id
                })

        execute_batch("gk-adam-insert_day_plan_arr_batch", day_plan_data)
        logging.info(f">>> [成功] {preMonth} 任务结束。")

        update_pre_conc_status(preConcId, '03')

    except Exception as e:
        logging.error(f">>> [错误] {str(e)}", exc_info=True)
        update_pre_conc_status(preConcId, '04')


@bp.route('/plan/run', methods=['POST'])
def handle_run_request():
    try:
        data = request.get_json() or {}

        # 1. 获取时间参数 preMonth，如果不传或为空，默认计算下个月
        preMonth = data.get('preMonth')
        if not preMonth:
            next_month_dt = datetime.now() + relativedelta(months=1)
            preMonth = next_month_dt.strftime('%Y%m')
            logging.info(f"未传入 preMonth，默认使用下个月: {preMonth}")
        else:
            preMonth = str(preMonth)

        # 2. 获取预测结果表ID preConcId
        preConcId = data.get('preConcId')

        # 3. 开启后台线程执行
        threading.Thread(target=run_full_aps_process, args=(preMonth, preConcId)).start()

        return jsonify({"code": 200, "msg": f"排程生成中: {preMonth}", "preConcId": preConcId}), 200
    except Exception as e:
        return jsonify({"code": 500, "msg": str(e)}), 500


@bp.route('/plan/check_deliver/run', methods=['POST'])
def handle_check_deliver_request():
    from backend.inventory_optimization.DailyReplenishmentPlan import DailyReplenishmentPlan
    from datetime import datetime, timedelta
    """
    接收前端传来的滚动排程请求，具体到日。
    请求体示例: {"preTime": "20260501", "preConcId": "123456"}
    """
    try:
        data = request.get_json() or {}
        if 'preTime' not in data:
            return jsonify({"code": 400, "msg": "参数错误，必须包含 preTime (例如: 20260501)"}), 400

        # 1. 获取前端传来的纯数字字符串
        raw_date_str = str(data['preTime']).strip()

        # 2. 获取预测结果表ID
        preConcId = data.get('preConcId')

        # 3. 按照 YYYYMMDD 格式解析
        dt_obj = datetime.strptime(raw_date_str, '%Y%m%d')

        # 4. 转换回底层算法需要的 YYYY-MM-DD 标准格式
        algorithm_date_str = dt_obj.strftime('%Y-%m-%d')

        start_date = datetime.strptime(algorithm_date_str,'%Y-%m-%d')
        if start_date.month == 12:
            end_date = start_date.replace(year=start_date.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            end_date = start_date.replace(month=start_date.month + 1, day=1) - timedelta(days=1)
        # 5. 开启后台线程执行，传入标准日期和 preConcId
        start_date = start_date.strftime('%Y-%m-%d')
        end_date = end_date.strftime('%Y-%m-%d')
        threading.Thread(target=run_check_deliver_process, args=(algorithm_date_str, start_date,end_date,preConcId)).start()


        return jsonify({
            "code": 200,
            "msg": f"检定和配送排程后台生成中, 接收参数: {raw_date_str}, 算法已识别为: {algorithm_date_str}",
            "preConcId": preConcId
        }), 200

    except ValueError:
        return jsonify({"code": 400, "msg": "日期格式错误，请使用 8位数字 的 YYYYMMDD 格式，例如: 20260501"}), 400
    except Exception as e:
        return jsonify({"code": 500, "msg": str(e)}), 500
