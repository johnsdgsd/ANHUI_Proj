import logging
import random
import requests
import pandas as pd
from datetime import datetime
import calendar
from backend.config.config import API_CONFIG
from backend.Scheduling.LoadDeliChcekData import LoadDeliChcekData
from backend.Scheduling.GetCheckDeliverPlan import GetCheckDeliverPlan

host = API_CONFIG["database"]["host"]
port = API_CONFIG["database"]["port"]

SQL_API_URL = f"http://{host}:{port}/exec"

def generate_safe_id():
    return random.randint(100000000000000, 999999999999999)


def fetch_data(sql_id, params=None):
    url = f"{SQL_API_URL}/{sql_id}"
    try:
        response = requests.post(url, json=params or {})
        response.raise_for_status()
        data = response.json()
        if not data: return pd.DataFrame()
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
            # 【终极抓虫】：如果后端报错，直接把 Java 的错误信息打印在黑框框里！
            if response.status_code != 200:
                logging.error(f"[{sql_id}] 插入失败，被数据库拒绝！后端真实报错: {response.text}")
            else:
                success_count += 1
        except Exception as e:
            logging.error(f"[{sql_id}] 网络请求异常: {e}")

    logging.info(f"[{sql_id}] 成功插入 {success_count}/{len(data_list)} 条数据")


def run_check_deliver_process(start_date_str):
    try:
        start_dt = datetime.strptime(start_date_str, '%Y-%m-%d')
        target_month = start_dt.strftime('%Y%m')
        today_dt = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

        if start_dt.day == 1 and today_dt < start_dt:
            sim_start_dt = today_dt
            logging.info(
                f">>> [跨月引擎启动] 目标排程月份为 {target_month}，将从今日 {today_dt.strftime('%Y-%m-%d')} 开启静默预演...")
        else:
            sim_start_dt = start_dt
            logging.info(f">>> [实时排程启动] 起点: {start_date_str}")

        sim_start_date_str = sim_start_dt.strftime('%Y-%m-%d')
        _, last_day = calendar.monthrange(start_dt.year, start_dt.month)
        target_end_dt = datetime(start_dt.year, start_dt.month, last_day)
        total_sim_days = (target_end_dt - sim_start_dt).days + 1

        Demands, InitQuaStock, LotList, DeviceCaps, SubTypeList, TypeList, DMAT, LocationNum, VeCap, VNums, VeUnitPrice, VeTypeNum, locations = LoadDeliChcekData(
            target_month, sim_start_date_str)

        if LotList.empty:
            logging.warning("无待检及未来到货数据，排程结束。")
            return

        df_detect, GlobalDelivPlan = GetCheckDeliverPlan(
            Demands, InitQuaStock, LotList, DeviceCaps, SubTypeList, TypeList, DMAT,
            LocationNum, VeCap, VNums, VeUnitPrice, VeTypeNum, sim_start_date_str, total_sim_days, start_date_str,
            locations
        )

        # 3.1 日检定计划明细
        detect_db_list = []
        for idx, row in df_detect.iterrows():
            # 彻底清理设备码和批次号，防备 pandas 自动加 .0
            safe_batch_no = str(row['ARR_BATCH_NO']).replace('.0', '').strip()
            safe_dev_code = str(row['DEV_CODE']).replace('.0', '').strip()

            # 严格只取前 10 位纯日期
            safe_bgn_date = str(row['DETECT_BGN_DATE']).strip()[:10]
            safe_end_date = str(row['DETECT_END_DATE']).strip()[:10]

            detect_db_list.append({
                "day_detect_plan_pre_id": generate_safe_id(),
                "detect_plan_no": f"CDP-{target_month}-{idx}",
                "arr_batch_no": safe_batch_no,
                "dev_code": safe_dev_code,
                "dev_cls": str(row['DEV_CLS']),
                "dev_categ": str(row['DEV_CATEG']),
                "detect_plan_num": int(row['DETECT_PLAN_NUM']),
                "detect_bgn_date": safe_bgn_date,
                "detect_end_date": safe_end_date,
                "plan_status": "0"  # <--- 【修正】：字段名改为 plan_status
            })

        # 3.2 月度检定计划汇总
        month_detect_db_list = []
        if not df_detect.empty:
            df_month_summary = df_detect.groupby(['DEV_CODE', 'DEV_CLS', 'DEV_CATEG'])[
                'DETECT_PLAN_NUM'].sum().reset_index()
            for _, row in df_month_summary.iterrows():
                safe_dev_code = str(row['DEV_CODE']).replace('.0', '').strip()

                month_detect_db_list.append({
                    "month_detect_plan_pre_id": generate_safe_id(),
                    "detect_plan_no": f"MDP-{target_month}-{safe_dev_code}",
                    "pre_year": str(start_dt.year),
                    "pre_month": f"{start_dt.month:02d}",
                    "dev_code": safe_dev_code,
                    "dev_cls": str(row['DEV_CLS']),
                    "dev_categ": str(row['DEV_CATEG']),
                    "detect_plan_num": int(row['DETECT_PLAN_NUM'])
                })

        # 3.3 配送方案及明细
        dist_scheme_db_list = []
        dist_detail_db_list = []

        for plan in GlobalDelivPlan:
            scheme_id = generate_safe_id()
            master = plan['master']

            dist_scheme_db_list.append({
                "dist_scheme_id": scheme_id,
                "car_type": master['CAR_TYPE'],
                "plan_dist_date": master['PLAN_DIST_DATE'],
                "load_rate": master['LOAD_RATE']
            })

            details = plan['details']
            total_qty = sum(d['PLAN_DIST_NUM'] for d in details)

            for d in details:
                dist_exp = round(master['PRICE'] * (d['PLAN_DIST_NUM'] / total_qty), 2) if total_qty > 0 else 0
                dist_detail_db_list.append({
                    "dist_scheme_det_id": generate_safe_id(),
                    "dist_scheme_id": scheme_id,
                    "rec_org_no": d['REC_ORG_NO'],
                    "dev_code": str(d['DEV_CODE']).replace('.0', '').strip(),
                    "dev_cls": str(d['DEV_CLS']),
                    "dev_categ": str(d['DEV_CATEG']),
                    "dist_seq": d['DIST_SEQ'],
                    "load_seq": d['LOAD_SEQ'],
                    "plan_dist_num": int(d['PLAN_DIST_NUM']),
                    "plan_box_num": int(d.get('PLAN_BOX_NUM', 0)), # <--- 【新增字段传参】：将物理装箱数传回Java后端
                    "dist_exp": dist_exp
                })

        logging.info("开始回写数据库...")
        execute_batch("insert_detect_plan", detect_db_list)
        execute_batch("insert_month_detect_plan", month_detect_db_list)
        execute_batch("insert_dist_scheme", dist_scheme_db_list)
        execute_batch("insert_dist_scheme_det", dist_detail_db_list)

        logging.info(f">>> [成功] {target_month} 检定与配送联动排程完毕，数据已落库。")

    except Exception as e:
        logging.error(f">>> [排程错误] {str(e)}", exc_info=True)