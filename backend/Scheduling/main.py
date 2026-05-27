import sys
from flask import Blueprint, request, jsonify
import threading
import logging
import random
import requests
import pandas as pd
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

        # 【状态机】：一开始立刻把状态改成 02-预测中
        update_pre_conc_status(preConcId, '02')

        target_dt = datetime.strptime(preMonth, '%Y%m')
        prev_month_dt = target_dt - relativedelta(months=2)
        prev_year = prev_month_dt.strftime('%Y')
        prev_month = prev_month_dt.strftime('%m')
        current_time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        logging.info("提取业务数据...")
        df_demand = fetch_data("gk-adam-query_replenish_demand", {"stat_month": preMonth})
        df_qua = fetch_data("gk-adam-query_qua_stock", {"sam_year": prev_year, "sam_month": prev_month})
        df_pend = fetch_data("gk-adam-query_pend_stock", {"sam_year": prev_year, "sam_month": prev_month})
        df_orders = fetch_data("gk-adam-query_unused_pur_orders")
        df_mapping = fetch_data("gk-adam-query_aps_pro_dev_mapping")

        if df_demand.empty:
            logging.info("补货需求为空，无需排程。")
            update_pre_conc_status(preConcId, '03')
            return

        for df in [df_demand, df_qua, df_pend, df_orders, df_mapping]:
            if not df.empty:
                df.columns = [c.upper() for c in df.columns]

        # 【新增提取全局方案 ID】：因为同月所有设备的 GLOBAL_SCHEME_ID 一致，取第一个非空的即可
        global_scheme_id = None
        if not df_demand.empty and 'GLOBAL_SCHEME_ID' in df_demand.columns:
            first_valid = df_demand['GLOBAL_SCHEME_ID'].dropna()
            if not first_valid.empty:
                global_scheme_id = int(float(first_valid.iloc[0]))
        logging.info(f"获取到当月全局方案标识 GLOBAL_SCHEME_ID: {global_scheme_id}")

        # 建立装箱数快速查询字典
        box_mapping = {}
        if not df_mapping.empty:
            for _, r in df_mapping.iterrows():
                code = str(r.get('DEV_CODE') or r.get('DEV_CODE_NO', ''))
                pack = r.get('PACK_BOX_NUM', r.get('UNITPERBOX', 5))
                try:
                    box_mapping[code] = int(pack)
                except:
                    box_mapping[code] = 5

        logging.info("计算 1.25倍 净缺口...")
        df_demand.set_index('DEV_CODE', inplace=True)

        if not df_qua.empty:
            df_demand = df_demand.join(df_qua.set_index('DEV_CODE'), how='left')
        else:
            df_demand['QUA_STOCK'] = 0

        if not df_pend.empty:
            df_demand = df_demand.join(df_pend.set_index('DEV_CODE'), how='left')
        else:
            df_demand['UNQUA_STOCK'] = 0

        df_demand.fillna({'QUA_STOCK': 0, 'UNQUA_STOCK': 0}, inplace=True)
        df_demand['RAW_REQ'] = df_demand['REQ_NUM'] - df_demand['QUA_STOCK'] - df_demand['UNQUA_STOCK']
        df_net_demand = df_demand[df_demand['RAW_REQ'] > 0].copy()

        lot_list_data = []
        month_plan_data = []

        for dev_code, row in df_net_demand.iterrows():
            raw_req = int(row['RAW_REQ'])
            target_req = math.ceil(raw_req * 1.25)

            if df_orders.empty:
                continue

            dev_orders = df_orders[df_orders['DEV_CODE'] == dev_code].sort_values(by='ORDER_NUM', ascending=False)
            if dev_orders.empty:
                continue

            M = int(dev_orders['ORDER_NUM'].min())
            box_cap = box_mapping.get(dev_code, 5)
            max_pieces_per_batch = 2500 * box_cap  # 绝对不可逾越的单日物理红线

            if M > max_pieces_per_batch:
                logging.error(
                    f"❌ [业务报错] 设备 {dev_code} 的最小订单量(M={M}只) 超过仓库单日上限({max_pieces_per_batch}只)！已跳过。")
                continue

            actual_assigned_qty = 0
            dev_lot_list = []

            for _, order in dev_orders.iterrows():
                if target_req <= 0:
                    break

                batch_qty = int(order['ORDER_NUM'])
                if batch_qty <= 0:
                    continue

                valid_split = True

                while batch_qty > max_pieces_per_batch:
                    chunk = max_pieces_per_batch
                    remainder = batch_qty - chunk

                    if 0 < remainder < M:
                        chunk = batch_qty - M
                        if chunk < M:
                            logging.error(
                                f"❌ [业务报错] 设备 {dev_code} 无法在满足上限和起订量前提下完成拆分！")
                            valid_split = False
                            break

                    dev_lot_list.append({
                        'DEV_CODE_NO': dev_code,
                        'PLAN_ARR_NUM': chunk,
                        'PLAN_ARR_DATE': target_dt.replace(day=1).strftime('%Y-%m-%d %H:%M:%S')
                    })
                    batch_qty -= chunk
                    target_req -= chunk
                    actual_assigned_qty += chunk

                if not valid_split:
                    continue

                if batch_qty > 0:
                    dev_lot_list.append({
                        'DEV_CODE_NO': dev_code,
                        'PLAN_ARR_NUM': batch_qty,
                        'PLAN_ARR_DATE': target_dt.replace(day=1).strftime('%Y-%m-%d %H:%M:%S')
                    })
                    target_req -= batch_qty
                    actual_assigned_qty += batch_qty

            if actual_assigned_qty > 0:
                lot_list_data.extend(dev_lot_list)
                month_plan_data.append({
                    "MONTH_PLAN_ARR_ID": generate_safe_id(),
                    "PLAN_ARR_NO": f"MP-{preMonth}-{dev_code}",
                    "PRE_YEAR": target_dt.strftime('%Y'),
                    "PRE_MONTH": target_dt.strftime('%m'),
                    "DEV_CODE": dev_code,
                    "PLAN_ARR_NUM": actual_assigned_qty,
                    "UPDATE_TIME": current_time_str,
                    "GLOBAL_SCHEME_ID": global_scheme_id  # 【新增透传】
                })

        LotList = pd.DataFrame(lot_list_data)

        if LotList.empty:
            logging.warning("匹配不到可用订单，排程结束。")
            update_pre_conc_status(preConcId, '03')
            return

        logging.info(f"开始排程算法，共生成 {len(LotList)} 个有效批次...")
        work_days = Getworkday(int(preMonth))
        ARR_PLAN_RESULT = GetArrPlan(LotList, df_mapping, work_days)

        logging.info("回写数据库...")
        execute_batch("gk-adam-insert_month_plan_arr_batch", month_plan_data)

        day_plan_data = []
        for idx, row in ARR_PLAN_RESULT.iterrows():
            plan_date = row['PLAN_ARR_DATE']
            if pd.notnull(plan_date):
                plan_date_str = plan_date.strftime('%Y-%m-%d %H:%M:%S') if not isinstance(plan_date, str) else plan_date
            else:
                plan_date_str = current_time_str

            day_plan_data.append({
                "DAY_PLAN_ARR_PRE_ID": generate_safe_id(),
                "PLAN_ARR_NO": f"DP-{preMonth}-{row['DEV_CODE_NO']}-{idx}",
                "DEV_CODE": row['DEV_CODE_NO'],
                "PLAN_ARR_NUM": int(row['PLAN_ARR_NUM']),
                "PLAN_ARR_DATE": plan_date_str,
                "UPDATE_TIME": current_time_str,
                "PLAN_STAT": "01",
                "GLOBAL_SCHEME_ID": global_scheme_id  # 【新增透传】
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
