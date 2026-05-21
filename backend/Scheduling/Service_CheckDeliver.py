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


def update_pre_conc_status(preConcId, stat):
    if not preConcId: return
    url = f"{SQL_API_URL}/update_pre_conc_status"
    try:
        response = requests.post(url, json={"pre_conc_id": preConcId, "stat": stat})
        response.raise_for_status()
        logging.info(f"状态更新成功: preConcId [{preConcId}] -> STAT [{stat}]")
    except Exception as e:
        logging.error(f"状态更新失败: preConcId [{preConcId}] 报错: {e}")


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
            if response.status_code != 200:
                logging.error(f"[{sql_id}] 操作失败，被数据库拒绝！后端报错: {response.text}")
            else:
                success_count += 1
        except Exception as e:
            logging.error(f"[{sql_id}] 网络请求异常: {e}")
    logging.info(f"[{sql_id}] 成功执行 {success_count}/{len(data_list)} 条指令")


def run_check_deliver_process(preTime, preConcId=None):
    try:
        update_pre_conc_status(preConcId, '02')

        start_dt = datetime.strptime(preTime, '%Y-%m-%d')
        target_month = start_dt.strftime('%Y%m')
        sim_start_date_str = start_dt.strftime('%Y-%m-%d')
        _, last_day = calendar.monthrange(start_dt.year, start_dt.month)
        target_end_dt = datetime(start_dt.year, start_dt.month, last_day)
        total_sim_days = (target_end_dt - start_dt).days + 1

        # 【核心判定】：1号算月初全排，其他全算月中重排
        is_mid_month = start_dt.day != 1

        if is_mid_month:
            logging.info(f">>> [月中滚动重排启动] 起点: {preTime}。将提取未完工计划进行续排...")
        else:
            logging.info(f">>> [月初全局排程启动] 起点: {preTime}。")

        # 读取基础数据 (Demands, QuaStock, 产能, 距离等)
        Demands, InitQuaStock, LotList, DeviceCaps, SubTypeList, TypeList, DMAT, LocationNum, VeCap, VNums, VeUnitPrice, VeTypeNum, locations = LoadDeliChcekData(
            target_month, sim_start_date_str)

        # 【重点替换】：月中重排时，强行替换掉 LotList，改为读取未完工的检定计划
        if is_mid_month:
            pending_detect_df = fetch_data("query_pending_detect_plans", {"target_month": target_month})
            if not pending_detect_df.empty:
                pending_detect_df.columns = [c.upper() for c in pending_detect_df.columns]
                pending_detect_df['PLAN_DATE'] = pd.to_datetime(pending_detect_df['PLAN_DATE'])
                pending_detect_df['REMNUM'] = pending_detect_df['REMNUM'].astype(int)
                pending_detect_df.rename(columns={'REMNUM': 'RemNum'}, inplace=True)
                # 根据到货日期排队
                LotList = pending_detect_df.sort_values(by=['PLAN_DATE', 'ARR_BATCH_NO']).reset_index(drop=True)
            else:
                logging.warning("月中无未完成检定计划(01/02/03)，本次重排无需安排检定。")
                LotList = pd.DataFrame()

        if LotList.empty and not is_mid_month:
            logging.warning("无待检及未来到货数据，排程结束。")
            update_pre_conc_status(preConcId, '03')
            return

        df_detect, GlobalDelivPlan, df_work_arrange = GetCheckDeliverPlan(
            Demands, InitQuaStock, LotList, DeviceCaps, SubTypeList, TypeList, DMAT,
            LocationNum, VeCap, VNums, VeUnitPrice, VeTypeNum, sim_start_date_str, total_sim_days, preTime, locations
        )

        detect_db_list = []
        detect_update_list = []
        month_detect_db_list = []

        # 分类处理检定计划 (重排走 Update，全排走 Insert)
        for idx, row in df_detect.iterrows():
            safe_dev_code = str(row['DEV_CODE']).replace('.0', '').strip()
            safe_bgn_date = str(row['DETECT_BGN_DATE']).strip()[:10]
            safe_end_date = str(row['DETECT_END_DATE']).strip()[:10]

            if is_mid_month and 'DAY_DETECT_PLAN_PRE_ID' in row and pd.notnull(row['DAY_DETECT_PLAN_PRE_ID']):
                detect_update_list.append({
                    # 【核心修正】：脱壳转为纯净的 Integer 格式，杜绝带有 .0 被 Java 拦截
                    "day_detect_plan_pre_id": int(float(row['DAY_DETECT_PLAN_PRE_ID'])),
                    "detect_bgn_date": safe_bgn_date,
                    "detect_end_date": safe_end_date
                })
            else:
                # 给 BATCH_PLAN_ARR_ID 加上纯净整型转换，规避偶尔出现的 .0
                safe_batch_id = int(float(row['BATCH_PLAN_ARR_ID'])) if pd.notnull(
                    row.get('BATCH_PLAN_ARR_ID')) and str(row.get('BATCH_PLAN_ARR_ID')).strip() != '' else None

                detect_db_list.append({
                    "day_detect_plan_pre_id": generate_safe_id(),
                    "detect_plan_no": f"CDP-{target_month}-{idx}",

                    "arr_batch_no": row.get('ARR_BATCH_NO'),
                    "batch_plan_arr_id": safe_batch_id,

                    "dev_code": safe_dev_code,
                    "dev_cls": str(row['DEV_CLS']),
                    "dev_categ": str(row['DEV_CATEG']),
                    "detect_plan_num": int(row['DETECT_PLAN_NUM']),
                    "detect_bgn_date": safe_bgn_date,
                    "detect_end_date": safe_end_date,
                    "plan_stat": "01"
                })

        if not is_mid_month and not df_detect.empty:
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

        # 配送方案明细
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
                    "plan_box_num": int(d.get('PLAN_BOX_NUM', 0)),
                    "dist_exp": dist_exp
                })

        # 工作安排明细
        work_arrange_db_list = []
        if not df_work_arrange.empty:
            for _, row in df_work_arrange.iterrows():
                work_arrange_db_list.append({
                    "work_arrange_pre_id": generate_safe_id(),
                    "veri_categ": str(row['VERI_CATEG']),
                    "work_date": str(row['WORK_DATE']),
                    "work_flag": str(row['WORK_FLAG']),
                    "detect_dur": str(row['DETECT_DUR']),
                    "capacity_num": int(row['CAPACITY_NUM'])
                })

        logging.info("================ 开始回写数据库 ================")

        if is_mid_month:
            logging.info(f"【月中重排触发】：更新 {len(detect_update_list)} 条旧检定计划起止时间。不再写入月计划。")
            execute_batch("update_day_detect_plan_dates", detect_update_list)
        else:
            logging.info(
                f"【月初排程触发】：全新插入 {len(detect_db_list)} 条日检定计划和 {len(month_detect_db_list)} 条月计划...")
            execute_batch("insert_detect_plan", detect_db_list)
            execute_batch("insert_month_detect_plan", month_detect_db_list)

        logging.info(f"清理当月 ({target_month}) 的历史未配送方案及明细...")
        execute_batch("delete_undelivered_scheme_det", [{"target_month": target_month}])
        execute_batch("delete_undelivered_scheme", [{"target_month": target_month}])
        execute_batch("insert_dist_scheme", dist_scheme_db_list)
        execute_batch("insert_dist_scheme_det", dist_detail_db_list)

        logging.info(f"清理 {sim_start_date_str} 起至月底的工作安排记录...")
        # 加上中括号 []，包装成单元素的列表！
        execute_batch("delete_work_arrange_by_date", [{"start_date": sim_start_date_str, "target_month": target_month}])
        execute_batch("insert_work_arrange_pre", work_arrange_db_list)

        logging.info(f">>> [成功] {target_month} 检定与配送联动排程完毕，数据已落库。")
        update_pre_conc_status(preConcId, '03')

    except Exception as e:
        logging.error(f">>> [排程错误] {str(e)}", exc_info=True)
        update_pre_conc_status(preConcId, '04')