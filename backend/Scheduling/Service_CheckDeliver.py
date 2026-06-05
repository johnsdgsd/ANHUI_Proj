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
    url = f"{SQL_API_URL}/gk-adam-update_pre_conc_status"
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



def run_check_deliver_process(preTime, start_date,end_date,preConcId=None):
    try:
        from backend.inventory_optimization.DailyReplenishmentPlan import DailyReplenishmentPlan
        update_pre_conc_status(preConcId, '02')

        start_dt = datetime.strptime(preTime, '%Y-%m-%d')
        target_month = start_dt.strftime('%Y%m')
        sim_start_date_str = start_dt.strftime('%Y-%m-%d')
        _, last_day = calendar.monthrange(start_dt.year, start_dt.month)
        target_end_dt = datetime(start_dt.year, start_dt.month, last_day)
        total_sim_days = (target_end_dt - start_dt).days + 1

        current_month_str = datetime.now().strftime('%Y%m')
        is_mid_month = (target_month <= current_month_str)

        if is_mid_month:
            logging.info(f">>> [当月滚动重排启动] 目标月份: {target_month}。将提取真实到货时间并追加旧主键...")
        else:
            logging.info(f">>> [下月全局初始排程启动] 目标月份: {target_month}。将执行先删后增...")

        Demands, InitQuaStock, LotList, DeviceCaps, SubTypeList, TypeList, DMAT, LocationNum, VeCap, VNums, VeUnitPrice, VeTypeNum, locations, global_scheme_id = LoadDeliChcekData(
            target_month, sim_start_date_str)

        if is_mid_month:
            pending_detect_df = fetch_data("gk-adam-query_pending_detect_plans", {"target_month": target_month})

            # =========================================================================
            # 【终极融合逻辑】：抛弃旧表里被污染的假日期，只提取它的主键ID (DAY_DETECT_PLAN_PRE_ID)
            # =========================================================================
            old_plan_map = {}
            if not pending_detect_df.empty:
                pending_detect_df.columns = [c.upper() for c in pending_detect_df.columns]
                for _, r in pending_detect_df.iterrows():
                    pid = str(r.get('BATCH_PLAN_ARR_ID', '')).strip()
                    if pid and pid not in ('nan', 'None', '<NA>', '0.0', '0'):
                        old_plan_map[pid] = r.get('DAY_DETECT_PLAN_PRE_ID')

            # 此时的 LotList 拥有从物理库存表里查出来的 100% 真实到达时间
            if not LotList.empty:
                LotList['DAY_DETECT_PLAN_PRE_ID'] = None
                for idx, row in LotList.iterrows():
                    pid = str(row.get('BATCH_PLAN_ARR_ID', '')).strip()
                    # 如果这个真实的批次之前被排过，我们就把旧主键像“盖章”一样印在它身上
                    if pid in old_plan_map:
                        LotList.at[idx, 'DAY_DETECT_PLAN_PRE_ID'] = old_plan_map[pid]

                LotList = LotList.sort_values(by=['PLAN_DATE']).reset_index(drop=True)
            else:
                logging.warning("当月无待检及未来到货数据。")

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

        for idx, row in df_detect.iterrows():
            safe_dev_code = str(row['DEV_CODE']).replace('.0', '').strip()
            safe_bgn_date = str(row['DETECT_BGN_DATE']).strip()[:10]
            safe_end_date = str(row['DETECT_END_DATE']).strip()[:10]

            if is_mid_month and 'DAY_DETECT_PLAN_PRE_ID' in row and pd.notnull(row['DAY_DETECT_PLAN_PRE_ID']) and str(
                    row['DAY_DETECT_PLAN_PRE_ID']).strip() not in ('', 'nan'):

                detect_update_list.append({
                    "day_detect_plan_pre_id": int(float(row['DAY_DETECT_PLAN_PRE_ID'])),
                    "detect_bgn_date": safe_bgn_date,
                    "detect_end_date": safe_end_date
                })
            else:
                safe_batch_id = int(float(row['BATCH_PLAN_ARR_ID'])) if pd.notnull(
                    row.get('BATCH_PLAN_ARR_ID')) and str(row.get('BATCH_PLAN_ARR_ID')).strip() not in (
                                                                            '', 'nan', '<NA>') else None

                safe_arr_no = str(row.get('ARR_BATCH_NO')) if pd.notnull(row.get('ARR_BATCH_NO')) and str(
                    row.get('ARR_BATCH_NO')).strip() not in ('', 'nan', '<NA>') else None

                detect_db_list.append({
                    "day_detect_plan_pre_id": generate_safe_id(), "detect_plan_no": f"CDP-{target_month}-{idx}",
                    "arr_batch_no": safe_arr_no, "batch_plan_arr_id": safe_batch_id,
                    "dev_code": safe_dev_code, "dev_cls": str(row['DEV_CLS']), "dev_categ": str(row['DEV_CATEG']),
                    "detect_plan_num": int(row['DETECT_PLAN_NUM']), "detect_bgn_date": safe_bgn_date,
                    "detect_end_date": safe_end_date, "plan_stat": "01", "cmp_type": "01",
                    "veri_type": str(row.get('VERI_TYPE', '01')), "global_scheme_id": global_scheme_id
                })

        if not df_detect.empty:
            df_month_summary = df_detect.groupby(['DEV_CODE', 'DEV_CLS', 'DEV_CATEG'])[
                'DETECT_PLAN_NUM'].sum().reset_index()

            # =========================================================================
            # 【核心修正】：加入精确到秒的时间戳，保证无论跑多少遍，编号绝对唯一！
            # 规则: 34 + YY + 17 + MMDD + HHMMSS + 4位序号
            # =========================================================================
            now_dt = datetime.now()
            month_plan_prefix = f"34{now_dt.strftime('%y')}17{now_dt.strftime('%m%d%H%M%S')}"
            month_plan_seq = 1

            for _, row in df_month_summary.iterrows():
                safe_dev_code = str(row['DEV_CODE']).replace('.0', '').strip()
                full_month_plan_no = f"{month_plan_prefix}{month_plan_seq:04d}"

                month_detect_db_list.append({
                    "month_detect_plan_pre_id": generate_safe_id(),
                    "detect_plan_no": full_month_plan_no,
                    "pre_year": str(start_dt.year), "pre_month": f"{start_dt.month:02d}", "dev_code": safe_dev_code,
                    "dev_cls": str(row['DEV_CLS']), "dev_categ": str(row['DEV_CATEG']),
                    "detect_plan_num": int(row['DETECT_PLAN_NUM']), "global_scheme_id": global_scheme_id
                })
                month_plan_seq += 1

        dist_scheme_db_list = []
        dist_detail_db_list = []
        for plan in GlobalDelivPlan:
            scheme_id = generate_safe_id()
            master = plan['master']
            dist_scheme_db_list.append({
                "dist_scheme_id": scheme_id, "car_type": master['CAR_TYPE'], "plan_dist_date": master['PLAN_DIST_DATE'],
                "load_rate": master['LOAD_RATE'], "global_scheme_id": global_scheme_id
            })
            for d in plan['details']:
                unit_price = master.get('UNIT_PRICE', 0.0695)
                real_box_num = int(d.get('PLAN_BOX_NUM', 0))
                dist_exp = round(real_box_num * d.get('DIST_SEGMENT', 0.0) * unit_price, 2)
                dist_detail_db_list.append({
                    "dist_scheme_det_id": generate_safe_id(), "dist_scheme_id": scheme_id,
                    "rec_org_no": d['REC_ORG_NO'],
                    "dev_code": str(d['DEV_CODE']).replace('.0', '').strip(), "dev_cls": str(d['DEV_CLS']),
                    "dev_categ": str(d['DEV_CATEG']), "dist_seq": d['DIST_SEQ'], "load_seq": d['LOAD_SEQ'],
                    "plan_dist_num": int(d['PLAN_DIST_NUM']), "plan_box_num": real_box_num, "dist_exp": dist_exp,
                    "est_tot_dist_mist": round(d.get('DIST_SEGMENT', 0.0), 2), "global_scheme_id": global_scheme_id
                })

        work_arrange_db_list = []
        if not df_work_arrange.empty:
            for _, row in df_work_arrange.iterrows():
                work_arrange_db_list.append({
                    "work_arrange_pre_id": generate_safe_id(), "veri_categ": str(row['VERI_CATEG']),
                    "work_date": str(row['WORK_DATE']), "work_flag": str(row['WORK_FLAG']),
                    "detect_dur": str(row['DETECT_DUR']), "capacity_num": int(row['CAPACITY_NUM']),
                    "global_scheme_id": global_scheme_id
                })

        logging.info("================ 开始回写数据库 ================")

        del_month_params = {
            "pre_year": str(start_dt.year),
            "pre_month": f"{start_dt.month:02d}"
        }

        if is_mid_month:
            if detect_update_list:
                execute_batch("gk-adam-update_day_detect_plan_dates", detect_update_list)
            if detect_db_list:
                execute_batch("gk-adam-insert_detect_plan", detect_db_list)
        else:
            execute_batch("gk-adam-delete_day_detect_plan", [{"target_month": target_month}])
            if detect_db_list:
                execute_batch("gk-adam-insert_detect_plan", detect_db_list)

        execute_batch("gk-adam-delete_month_detect_plan", [del_month_params])
        if month_detect_db_list:
            execute_batch("gk-adam-insert_month_detect_plan", month_detect_db_list)

        execute_batch("gk-adam-delete_undelivered_scheme_det", [{"target_month": target_month}])
        execute_batch("gk-adam-delete_undelivered_scheme", [{"target_month": target_month}])

        if dist_scheme_db_list:
            execute_batch("gk-adam-insert_dist_scheme", dist_scheme_db_list)
        if dist_detail_db_list:
            execute_batch("gk-adam-insert_dist_scheme_det", dist_detail_db_list)

        execute_batch("gk-adam-delete_work_arrange_by_date",
                      [{"start_date": sim_start_date_str, "target_month": target_month}])
        if work_arrange_db_list:
            execute_batch("gk-adam-insert_work_arrange_pre", work_arrange_db_list)

        logging.info(f">>> [成功] {target_month} 检定与配送联动排程完毕，数据已落库。")
        DailyReplenishmentPlan(start_date, end_date)
        update_pre_conc_status(preConcId, '03')

    except Exception as e:
        logging.error(f">>> [排程错误] {str(e)}", exc_info=True)
        update_pre_conc_status(preConcId, '04')