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

logger = logging.getLogger()
logger.setLevel(logging.INFO)
# 清理可能被 Flask 劫持的旧输出通道
for handler in logger.handlers[:]:
    logger.removeHandler(handler)
# 建立新的屏幕输出通道，并强制刷新
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
logger.addHandler(console_handler)

host = API_CONFIG["database"]["host"]
port = API_CONFIG["database"]["port"]
SQL_API_URL = f"http://{host}:{port}/exec"


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


def run_full_aps_process(target_month):
    try:
        logging.info(f">>> [开始] 执行 {target_month} 任务...")

        target_dt = datetime.strptime(target_month, '%Y%m')
        prev_month_dt = target_dt - relativedelta(months=1)
        prev_year = prev_month_dt.strftime('%Y')
        prev_month = prev_month_dt.strftime('%m')
        current_time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        logging.info("提取业务数据...")
        df_demand = fetch_data("query_replenish_demand", {"stat_month": target_month})
        df_qua = fetch_data("query_qua_stock", {"sam_year": prev_year, "sam_month": prev_month})
        df_pend = fetch_data("query_pend_stock", {"sam_year": prev_year, "sam_month": prev_month})
        df_orders = fetch_data("query_unused_pur_orders")
        df_mapping = fetch_data("query_aps_pro_dev_mapping")

        if df_demand.empty:
            logging.info("补货需求为空，无需排程。")
            return

        for df in [df_demand, df_qua, df_pend, df_orders, df_mapping]:
            if not df.empty:
                df.columns = [c.upper() for c in df.columns]

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

            # 【报错拦截 1】：如果供应商要求的最小发货量 M 直接干爆了仓库单日上限，直接报错！
            if M > max_pieces_per_batch:
                logging.error(
                    f"❌ [业务报错] 设备 {dev_code} 的最小订单量(M={M}只) 超过仓库单日2500箱的承载上限({max_pieces_per_batch}只)！已跳过该设备，请人工协调。")
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

                # 开始循环切分
                while batch_qty > max_pieces_per_batch:
                    chunk = max_pieces_per_batch
                    remainder = batch_qty - chunk

                    if 0 < remainder < M:
                        # 如果按 2500 箱切，剩下的尾数不满足 M
                        # 尝试把尾数补足到 M，看剩下的 chunk 还够不够 M
                        chunk = batch_qty - M

                        if chunk < M:
                            # 如果调整后，连切下来的这一块也小于 M 了，说明数学上绝对无解
                            logging.error(
                                f"❌ [业务报错] 设备 {dev_code} 订单数({int(order['ORDER_NUM'])}) 无法在死守 2500箱 且满足 最小起订量(M={M}) 的前提下完成拆分！已跳过。")
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

                # 如果上面切分过程报错无解，直接放弃这个订单
                if not valid_split:
                    continue

                # 收尾：把合法剩下的最后一点塞进去
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
                    "PLAN_ARR_NO": f"MP-{target_month}-{dev_code}",
                    "PRE_YEAR": target_dt.strftime('%Y'),
                    "PRE_MONTH": target_dt.strftime('%m'),
                    "DEV_CODE": dev_code,
                    "PLAN_ARR_NUM": actual_assigned_qty,
                    "UPDATE_TIME": current_time_str
                })

        LotList = pd.DataFrame(lot_list_data)

        if LotList.empty:
            logging.warning("匹配不到可用订单，排程结束。")
            return

        logging.info(f"开始排程算法，共按订单生成了 {len(LotList)} 个有效批次...")
        work_days = Getworkday(int(target_month))

        ARR_PLAN_RESULT = GetArrPlan(LotList, df_mapping, work_days)

        logging.info("回写数据库...")
        execute_batch("insert_month_plan_arr_batch", month_plan_data)

        day_plan_data = []
        for idx, row in ARR_PLAN_RESULT.iterrows():
            plan_date = row['PLAN_ARR_DATE']
            if pd.notnull(plan_date):
                plan_date_str = plan_date.strftime('%Y-%m-%d %H:%M:%S') if not isinstance(plan_date, str) else plan_date
            else:
                plan_date_str = current_time_str

            day_plan_data.append({
                "DAY_PLAN_ARR_PRE_ID": generate_safe_id(),
                "PLAN_ARR_NO": f"DP-{target_month}-{row['DEV_CODE_NO']}-{idx}",
                "DEV_CODE": row['DEV_CODE_NO'],
                "PLAN_ARR_NUM": int(row['PLAN_ARR_NUM']),
                "PLAN_ARR_DATE": plan_date_str,
                "UPDATE_TIME": current_time_str,
                "PLAN_STAT": "01"
            })

        execute_batch("insert_day_plan_arr_batch", day_plan_data)
        logging.info(f">>> [成功] {target_month} 任务结束。")

    except Exception as e:
        logging.error(f">>> [错误] {str(e)}", exc_info=True)


@bp.route('/plan/run', methods=['POST'])
def handle_run_request():
    try:
        data = request.get_json()
        if not data or 'month' not in data:
            return jsonify({"code": 400, "msg": "参数错误"}), 400

        month_param = str(data['month'])
        threading.Thread(target=run_full_aps_process, args=(month_param,)).start()

        return jsonify({"code": 200, "msg": f"排程生成中: {month_param}"}), 200
    except Exception as e:
        return jsonify({"code": 500, "msg": str(e)}), 500



@bp.route('/plan/check_deliver/run', methods=['POST'])
def handle_check_deliver_request():
    """
    接收前端传来的滚动排程请求，具体到日。
    请求体示例: {"start_date": "20260501"}
    """
    try:
        data = request.get_json()
        if not data or 'start_date' not in data:
            return jsonify({"code": 400, "msg": "参数错误，必须包含 start_date (例如: 20260501)"}), 400

        # 获取前端传来的纯数字字符串，例如 "20260501"
        raw_date_str = str(data['start_date']).strip()

        # 1. 按照 YYYYMMDD 格式解析
        dt_obj = datetime.strptime(raw_date_str, '%Y%m%d')

        # 2. 转换回底层算法需要的 YYYY-MM-DD 标准格式
        algorithm_date_str = dt_obj.strftime('%Y-%m-%d')

        # 开启后台线程执行，传入带有横杠的标准日期
        threading.Thread(target=run_check_deliver_process, args=(algorithm_date_str,)).start()

        return jsonify({"code": 200,
                        "msg": f"检定和配送排程后台生成中, 接收参数: {raw_date_str}, 算法已识别为: {algorithm_date_str}"}), 200

    except ValueError:
        return jsonify({"code": 400, "msg": "日期格式错误，请使用 8位数字 的 YYYYMMDD 格式，例如: 20260501"}), 400
    except Exception as e:
        return jsonify({"code": 500, "msg": str(e)}), 500


# # 添加蓝图导出
# __all__ = ['bp']

# if __name__ == '__main__':
#     # 用于单独测试
#     from flask import Flask
#     app = Flask(__name__)
#     app.register_blueprint(bp)
#     app.run(host='0.0.0.0', port=2500, debug=True)