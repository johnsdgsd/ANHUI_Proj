import pandas as pd
import numpy as np
from geopy.distance import geodesic
import logging
import sys
from datetime import datetime, timedelta
import calendar
from dateutil.relativedelta import relativedelta


def query_future_14day_dmd_pre(start_date: str, end_date: str):
    """
    查询未来14天日预测需求合计（pretype='05'）。

    从 ADAM_WD_DMD_PRE 表取指定日期区间内 pretype='05' 的日预测值之和，
    按 ORG_NO + DEV_CODE 聚合。

    参数:
        start_date: 起始日期，格式 YYYYMMDD
        end_date:   结束日期，格式 YYYYMMDD

    Returns:
        pd.DataFrame: 列 ORG_NO, DEV_CODE, PRE_14DAY_NUM（空 DataFrame 若无数据）
    """
    from backend.Scheduling.Service_CheckDeliver import fetch_data

    df = fetch_data("gk-adam-query_future_14day_dmd_pre", {
        "pre_type": "05",
        "start_date": start_date,
        "end_date": end_date
    })
    if not df.empty:
        df.columns = [c.upper() for c in df.columns]
    return df


def _get_realtime_stocknum():
    """
    获取地市实时库存（按 ORG_NO × DEV_CODE 维度）。

    调用 SQL 模板 gk-adam-query-realtime-stocknum，
    复用 Service_CheckDeliver.fetch_data。

    Returns:
        pd.DataFrame: 列 ORG_NO, ORG_NAME, DEV_CODE, STOCK_NUM
    """
    from backend.Scheduling.Service_CheckDeliver import fetch_data

    df = fetch_data("gk-adam-query-realtime-stocknum")
    if not df.empty:
        df.columns = [c.upper() for c in df.columns]
    return df


def _get_estimated_stock(target_month: str):
    """
    推算目标月初库存（模仿 fetch_data.py 的 query_adam_org_stock_sample_estimated）。

    公式:
        目标月初库存 = 实时库存 + 上月未来待配送 − 上月剩余需求
        上月剩余需求 = 上月需求预测 × (上月剩余天数 / 上月总天数)

    参数:
        target_month: 目标月份，格式 YYYYMM，上月必须 >= 当前月份

    Returns:
        pd.DataFrame: 列 ORG_NO, ORG_NAME, DEV_CODE, STOCK_NUM
    """
    from backend.Scheduling.Service_CheckDeliver import fetch_data

    if not isinstance(target_month, str) or len(target_month) != 6 or not target_month.isdigit():
        raise ValueError(f"target_month 格式错误，需为 YYYYMM，实际: {target_month}")

    target_dt = datetime.strptime(target_month, '%Y%m')
    prev_dt = target_dt - relativedelta(months=1)
    prev_month = prev_dt.strftime('%Y%m')
    today = datetime.now()
    current_month = today.strftime('%Y%m')

    if prev_month < current_month:
        raise ValueError(
            f"无法推算: 目标月={target_month}, 上月={prev_month}, "
            f"上月早于当前月={current_month}，无法用实时库存推算"
        )

    logging.info(f'推算目标月初库存: target={target_month}, 上月={prev_month}, 当前={current_month}')

    # 1. 获取地市实时库存
    df_realtime = _get_realtime_stocknum()
    rt = df_realtime[['ORG_NO', 'ORG_NAME', 'DEV_CODE', 'STOCK_NUM']].copy()
    rt.rename(columns={'STOCK_NUM': 'RT_STOCK'}, inplace=True)

    # 2. 获取上月需求预测
    year = prev_month[:4]
    month = prev_month[4:6]
    demand = fetch_data("gk-adam-query_adam_yqm_dmd_pre_by_year_month", {"year": year, "month": month})
    if demand.empty:
        demand = pd.DataFrame(columns=['ORG_NO', 'DEV_CODE', 'PRE_NUM'])
    else:
        demand.columns = [c.upper() for c in demand.columns]
    demand = demand[['ORG_NO', 'DEV_CODE', 'PRE_NUM']].copy()
    demand.rename(columns={'PRE_NUM': 'MONTHLY_DEMAND'}, inplace=True)

    # 3. 计算上月剩余需求
    days_in_prev = calendar.monthrange(prev_dt.year, prev_dt.month)[1]
    if today.year == prev_dt.year and today.month == prev_dt.month:
        remaining_days = days_in_prev - today.day + 1
    elif today > prev_dt:
        remaining_days = 0
    else:
        remaining_days = days_in_prev
    ratio = remaining_days / days_in_prev
    demand['REMAIN_DEMAND'] = demand['MONTHLY_DEMAND'] * ratio

    # 4. 获取上月未来待配送
    df_plan = fetch_data("gk-adam-query_adam_plan_day_ias_pre_by_month", {"data_month": prev_month})
    if df_plan.empty:
        df_delivery = pd.DataFrame(columns=['ORG_NO', 'DEV_CODE', 'PENDING_DELIVERY'])
    else:
        df_plan.columns = [c.upper() for c in df_plan.columns]
        df_plan['PRE_DATE'] = pd.to_datetime(df_plan['PRE_DATE'], errors='coerce')
        if df_plan['PRE_DATE'].dt.tz is not None:
            df_plan['PRE_DATE'] = df_plan['PRE_DATE'].dt.tz_convert(None)
        today_date = pd.Timestamp(today.date())
        last_day_of_prev = pd.Timestamp(prev_dt.year, prev_dt.month, days_in_prev)
        mask_future = (df_plan['PRE_DATE'] >= today_date) & (df_plan['PRE_DATE'] <= last_day_of_prev)
        df_future = df_plan[mask_future]
        if df_future.empty:
            df_delivery = pd.DataFrame(columns=['ORG_NO', 'DEV_CODE', 'PENDING_DELIVERY'])
        else:
            df_delivery = df_future.groupby(['REC_ORG_NO', 'DEV_CODE'], as_index=False)['PLAN_IAS_NUM'].sum()
            df_delivery.rename(columns={'REC_ORG_NO': 'ORG_NO', 'PLAN_IAS_NUM': 'PENDING_DELIVERY'}, inplace=True)

    # 5. 合并三表，计算推算月初库存
    result = rt.merge(demand, on=['ORG_NO', 'DEV_CODE'], how='outer') \
               .merge(df_delivery, on=['ORG_NO', 'DEV_CODE'], how='left')
    num_cols = ['RT_STOCK', 'MONTHLY_DEMAND', 'REMAIN_DEMAND', 'PENDING_DELIVERY']
    for c in num_cols:
        if c in result.columns:
            result[c] = result[c].fillna(0)

    result['STOCK_NUM'] = (result['RT_STOCK'] + result['PENDING_DELIVERY']
                           - result['REMAIN_DEMAND']).clip(lower=0).round(0)

    logging.info(f'推算目标月初库存完成: 实时={len(rt)}条, 需求={len(demand)}条, '
                 f'配送={len(df_delivery)}条, 结果={len(result)}条, '
                 f'上月={prev_month}, 剩余{remaining_days}/{days_in_prev}天')

    return result[['ORG_NO', 'ORG_NAME', 'DEV_CODE', 'STOCK_NUM']]


def LoadDeliChcekData(target_month, start_date_str, is_mid_month=False):
    from backend.Scheduling.Service_CheckDeliver import fetch_data
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s", stream=sys.stdout)

    # ================= 0. 基础网点与设备属性初始化 =================
    df_demand = fetch_data("gk-adam-query_remain_demand", {"stat_month": target_month})
    if df_demand.empty: raise ValueError("当月无需求数据")
    df_demand.columns = [c.upper() for c in df_demand.columns]

    # 【新增】：提取 GLOBAL_SCHEME_ID
    global_scheme_id = None
    if 'GLOBAL_SCHEME_ID' in df_demand.columns:
        first_valid = df_demand['GLOBAL_SCHEME_ID'].dropna()
        if not first_valid.empty:
            global_scheme_id = int(float(first_valid.iloc[0]))
    logging.info(f"提取到检定/配送全局方案标识 GLOBAL_SCHEME_ID: {global_scheme_id}")

    locations = df_demand[['ORG_NO', 'ORG_NAME', 'LAT', 'LON']].drop_duplicates().reset_index(drop=True)
    LocationNum = len(locations)

    center_loc = pd.DataFrame([{'ORG_NO': '34101', 'ORG_NAME': '省级总库', 'LAT': 31.87, 'LON': 117.18}])
    locations = pd.concat([center_loc, locations], ignore_index=True)

    df_mapping = fetch_data("gk-adam-query_aps_pro_dev_mapping")
    if df_mapping.empty:
        logging.error("[致命错误] 设备映射表为空，无法继续排程！请检查数据库接口 gk-adam-query_aps_pro_dev_mapping 是否可用。")
        raise RuntimeError("设备映射表(gk-adam-query_aps_pro_dev_mapping)查询结果为空，排程无法继续")
    df_mapping.columns = [c.upper() for c in df_mapping.columns]

    TypeList = df_mapping[['DEV_CODE_NO', 'PACK_BOX_NUM']].drop_duplicates().reset_index(drop=True)
    TypeList.rename(columns={'PACK_BOX_NUM': 'UnitPerBox'}, inplace=True)

    SubTypeList = df_mapping.drop_duplicates(subset='DEV_CODE_NO').reset_index(drop=True)

    # 智能补全 DEV_CODE_DESC
    if 'DEV_CODE_DESC' not in SubTypeList.columns:
        if 'DEV_CODE_DESC' in df_demand.columns:
            desc_map = dict(zip(df_demand['DEV_CODE_NO'], df_demand['DEV_CODE_DESC']))
            SubTypeList['DEV_CODE_DESC'] = SubTypeList['DEV_CODE_NO'].map(desc_map).fillna('')
        else:
            SubTypeList['DEV_CODE_DESC'] = ''

    SubTypeNum = len(SubTypeList)

    # 构建哈希索引字典，提升查找效率
    org_idx_map = {locations.loc[i + 1, 'ORG_NO']: i for i in range(LocationNum)}
    dev_idx_map = {SubTypeList.loc[j, 'DEV_CODE_NO']: j for j in range(SubTypeNum)}

    # ================= 1. 盘点需求与扣减 =================
    logging.info(">>> 开始盘点发货需求与扣减已配送明细...")
    Demands = np.zeros((LocationNum, SubTypeNum))

    for _, r in df_demand.iterrows():
        i = org_idx_map.get(r['ORG_NO'])
        j = dev_idx_map.get(r['DEV_CODE_NO'])
        if i is not None and j is not None:
            Demands[i, j] += r['REQ_NUM']

    df_delivered = fetch_data("gk-adam-query_delivered_details", {"target_month": target_month})
    if not df_delivered.empty:
        df_delivered.columns = [c.upper() for c in df_delivered.columns]
        for _, r in df_delivered.iterrows():
            i = org_idx_map.get(r['REC_ORG_NO'])
            j = dev_idx_map.get(r['DEV_CODE'])
            if i is not None and j is not None:
                Demands[i, j] = max(0, Demands[i, j] - int(r['DELIVERED_NUM']))

    # ================= 2. 盘点合格库存与在途检定 =================
    logging.info(">>> 开始合并现有合格库存与检定完工/在途库存...")
    InitQuaStock = np.zeros(SubTypeNum)

    df_qua = fetch_data("gk-adam-query_realtime_qua_stock")
    if not df_qua.empty:
        df_qua.columns = [c.upper() for c in df_qua.columns]
        for _, r in df_qua.iterrows():
            j = dev_idx_map.get(r['DEV_CODE_NO'])
            if j is not None:
                InitQuaStock[j] += r['QUA_STOCK_NUM']

    df_inspected = fetch_data("gk-adam-query_completed_inspections", {"target_month": target_month})
    if not df_inspected.empty:
        df_inspected.columns = [c.upper() for c in df_inspected.columns]
        for _, r in df_inspected.iterrows():
            j = dev_idx_map.get(r['DEV_CODE'])
            if j is not None:
                InitQuaStock[j] += int(r['INSPECTED_NUM'])

    # ================= 3. 获取混合待检批次 =================
    logging.info(">>> 获取检定池任务 (含现存待检与未来到货)...")
    LotList = fetch_data("gk-adam-query_future_arr_plan", {"start_date": start_date_str})
    if not LotList.empty:
        LotList.columns = [c.upper() for c in LotList.columns]
        LotList['PLAN_DATE'] = pd.to_datetime(LotList['PLAN_DATE'])
        LotList['RemNum'] = LotList['PLAN_ARR_NUM'].astype(int)

        # 1. 优先按日期和数据源排序，确保 REALTIME (现存待检) 排在 FUTURE (计划到货) 的前面
        LotList = LotList.sort_values(by=['PLAN_DATE', 'SOURCE_TYPE'], ascending=[True, False]).reset_index(drop=True)

        # 2. 【核心新增：基于 BATCH_PLAN_ARR_ID 强制去重】
        if 'BATCH_PLAN_ARR_ID' in LotList.columns:
            # 暴力清洗空值，统一转为空字符串
            LotList['BATCH_PLAN_ARR_ID'] = LotList['BATCH_PLAN_ARR_ID'].fillna('').astype(str).str.strip()
            LotList['BATCH_PLAN_ARR_ID'] = LotList['BATCH_PLAN_ARR_ID'].replace(
                {'nan': '', 'None': '', '<NA>': '', '0.0': '', '0': ''})

            # 分离出有 ID 和 无 ID 的批次
            mask_has_id = LotList['BATCH_PLAN_ARR_ID'] != ''

            # 对有 ID 的批次执行去重：因为刚才排序过了，这里只会保留 REALTIME 的那条记录！
            lot_with_id = LotList[mask_has_id].drop_duplicates(subset=['BATCH_PLAN_ARR_ID'], keep='first')
            lot_no_id = LotList[~mask_has_id]

            # 重新拼装（此时重叠的 FUTURE 数据已经被抹除）
            LotList = pd.concat([lot_with_id, lot_no_id], ignore_index=True)
            LotList = LotList.sort_values(by=['PLAN_DATE', 'SOURCE_TYPE'], ascending=[True, False]).reset_index(
                drop=True)
        logging.info(f"LotList去重后: {len(LotList)}行")

    # ================= 3.5 【跨月扣减】始终扣除前月已排检定计划 =================
    if not LotList.empty:
        target_dt = datetime.strptime(target_month, '%Y%m')
        prev_dt = target_dt - relativedelta(months=1)
        prev_month = prev_dt.strftime('%Y%m')
        logging.info(f">>> [跨月扣减] 查询前月({prev_month})已排检定计划...")

        df_prev_plan = fetch_data("gk-adam-query_pending_detect_plans", {"target_month": prev_month})
        if not df_prev_plan.empty:
            df_prev_plan.columns = [c.upper() for c in df_prev_plan.columns]
            # 确保关键列存在
            if 'BATCH_PLAN_ARR_ID' in df_prev_plan.columns and 'REMNUM' in df_prev_plan.columns:
                # 清洗 BATCH_PLAN_ARR_ID
                df_prev_plan['BATCH_PLAN_ARR_ID'] = df_prev_plan['BATCH_PLAN_ARR_ID'].fillna('').astype(str).str.strip()
                df_prev_plan['BATCH_PLAN_ARR_ID'] = df_prev_plan['BATCH_PLAN_ARR_ID'].replace(
                    {'nan': '', 'None': '', '<NA>': '', '0.0': '', '0': ''})
                df_prev_plan = df_prev_plan[df_prev_plan['BATCH_PLAN_ARR_ID'] != '']

                if not df_prev_plan.empty:
                    prev_plan_ids = set(df_prev_plan['BATCH_PLAN_ARR_ID'].unique())
                    # 统计扣减量：按 DEV_CODE 汇总（列名可能是 DEV_CODE_NO 或 DEV_CODE）
                    dev_col = 'DEV_CODE_NO' if 'DEV_CODE_NO' in df_prev_plan.columns else 'DEV_CODE'
                    prev_plan_dev_sum = df_prev_plan.groupby(dev_col)['REMNUM'].sum()

                    # 从 LotList 中过滤掉前月已排的批次
                    before_count = len(LotList)
                    before_remnum = LotList['RemNum'].sum()
                    LotList = LotList[~LotList['BATCH_PLAN_ARR_ID'].isin(prev_plan_ids)]
                    after_count = len(LotList)
                    after_remnum = LotList['RemNum'].sum()

                    # 将被扣减的量加入 InitQuaStock（前月检完后变成合格品）
                    added_to_stock = 0
                    for dev_code, remnum in prev_plan_dev_sum.items():
                        j = dev_idx_map.get(str(dev_code).replace('.0', '').strip())
                        if j is not None:
                            InitQuaStock[j] += int(remnum)
                            added_to_stock += int(remnum)

                    logging.info(f"[跨月扣减] 前月{prev_month}已排{len(prev_plan_ids)}个批次, "
                               f"LotList: {before_count}→{after_count}条({before_remnum}→{after_remnum}只), "
                               f"InitQuaStock补充{added_to_stock}只")
            else:
                logging.warning(f"[跨月扣减] 前月计划缺少 BATCH_PLAN_ARR_ID 或 REMNUM 列，跳过")
        else:
            logging.info(f"[跨月扣减] 前月({prev_month})无已排检定计划，无需扣减")

    # ================= 4. 读取产线产能及距离矩阵 =================
    DeviceCaps = fetch_data("gk-adam-query_check_line")
    if not DeviceCaps.empty:
        DeviceCaps.columns = [c.upper() for c in DeviceCaps.columns]

    logging.info(">>> 从数据库加载网点实际运输距离矩阵...")
    num_nodes = LocationNum + 1
    DMAT = np.zeros((num_nodes, num_nodes))
    df_dist = fetch_data("gk-adam-query_distance_matrix")
    if not df_dist.empty:
        df_dist.columns = [c.upper() for c in df_dist.columns]
        # 构建 ORG_NO → 矩阵索引的映射 (两边统一转str避免类型不匹配)
        org_to_idx = {str(locations.loc[i, 'ORG_NO']).strip(): i for i in range(num_nodes)}
        matched = 0
        for _, r in df_dist.iterrows():
            from_org = str(r['DIST_ORG_NO']).strip()
            to_org = str(r['RECEIVE_ORG_NO']).strip()
            dist_val = float(r['DIST_MIST'])
            fi = org_to_idx.get(from_org)
            ti = org_to_idx.get(to_org)
            if fi is not None and ti is not None and dist_val > 0:
                DMAT[fi, ti] = dist_val
                matched += 1
        logging.info(f"距离矩阵: {len(df_dist)}条记录, 成功匹配{matched}对")

    else:
        logging.warning("未获取到实际距离数据，矩阵全为0！")

    # ================= 5. 【核心重构】：通过 ds_sql 动态拉取车队参数 =================
    logging.info(">>> 从 ds_sql 动态引擎读取车队运力及单价配置...")
    df_van_conf = fetch_data("gk-adam-query_vehicle_conf")

    if not df_van_conf.empty:
        df_van_conf.columns = [c.upper() for c in df_van_conf.columns]
        df_van_conf = df_van_conf.sort_values(by='CAR_TYPE').reset_index(drop=True)

        VeCap = df_van_conf['VEHICLE_CAP'].astype(int).values
        VNums = df_van_conf['VEHICLE_NUM'].astype(int).values
        VeUnitPrice = df_van_conf['VEHICLE_CARRI'].astype(float).values
        VeTypeNum = len(df_van_conf)
        logging.info(f"✅ 成功通过 HTTP 接口拉取 {VeTypeNum} 种车型配置。")
    else:
        logging.warning("⚠️ 未能从gk-adam-query_vehicle_conf 接口获取到数据，启用默认兜底配置！")
        VeCap = np.array([459, 901, 1071])
        VNums = np.array([9, 10, 6])
        VeUnitPrice = np.array([0.0695, 0.0695, 0.0695])
        VeTypeNum = 3

    # ================= 6. 计算网点缺货优先级 =================
    logging.info(">>> 开始计算各网点缺货优先级...")

    # 6.1 计算 14 天预测窗口
    start_dt = datetime.strptime(start_date_str, '%Y-%m-%d')
    _, last_day = calendar.monthrange(start_dt.year, start_dt.month)
    month_end_dt = datetime(start_dt.year, start_dt.month, last_day)
    remaining_days = (month_end_dt - start_dt).days + 1

    if remaining_days >= 14:
        window_start = start_dt
        window_end = start_dt + timedelta(days=13)
    else:
        window_end = month_end_dt
        window_start = month_end_dt - timedelta(days=13)

    window_start_str = window_start.strftime('%Y%m%d')
    window_end_str = window_end.strftime('%Y%m%d')
    logging.info(f"14天预测窗口: {window_start_str} ~ {window_end_str} (当月剩余{remaining_days}天)")

    # 6.2 根据模式读取库存
    if is_mid_month:
        logging.info("库存模式: 重排 → 读实时库存")
        df_stock = _get_realtime_stocknum()
    else:
        logging.info(f"库存模式: 初排 → 推算{target_month}月初库存")
        df_stock = _get_estimated_stock(target_month)

    if df_stock.empty:
        logging.warning("库存数据为空，所有网点缺货概率设为 1.0")
        raw_stock = pd.DataFrame(columns=['ORG_NO', 'ORG_NAME', 'DEV_CODE', 'STOCK_NUM'])
    else:
        raw_stock = df_stock[['ORG_NO', 'ORG_NAME', 'DEV_CODE', 'STOCK_NUM']].copy()

    # 6.3 读取 14 天日预测
    df_forecast = query_future_14day_dmd_pre(window_start_str, window_end_str)
    if df_forecast.empty:
        logging.warning("14天日预测数据为空，所有缺货概率设为 1.0")
        raw_forecast = pd.DataFrame(columns=['ORG_NO', 'DEV_CODE', 'PRE_14DAY_NUM'])
    else:
        raw_forecast = df_forecast[['ORG_NO', 'DEV_CODE', 'PRE_14DAY_NUM']].copy()

    # 6.4 库存 LEFT JOIN 预测 → 缺货概率逐(网点,设备码)计算
    if raw_stock.empty:
        merged = raw_forecast.copy()
        merged['STOCK_NUM'] = 0.0
    else:
        merged = raw_stock.merge(raw_forecast, on=['ORG_NO', 'DEV_CODE'], how='left')

    merged['PRE_14DAY_NUM'] = merged['PRE_14DAY_NUM'].fillna(0.0)
    merged['STOCK_NUM'] = merged['STOCK_NUM'].fillna(0.0)

    # 14天需求 < 5 的设备码跳过，不参与缺货概率计算
    low_demand_mask = merged['PRE_14DAY_NUM'] < 5
    low_demand_count = low_demand_mask.sum()
    if low_demand_count > 0:
        logging.info(f"过滤14天需求<5的设备码: {low_demand_count} 条")
    merged = merged[~low_demand_mask].copy()

    if merged.empty:
        logging.warning("过滤低需求后无有效设备码，所有网点缺货概率设为默认值")
        org_priority = {}
        dev_stock = {}
        dev_forecast = {}
    else:
        def calc_prob(stock, forecast):
            if forecast <= 0:
                return 1.0
            return max(0.0, 1.0 - stock / forecast)

        merged['PROB'] = merged.apply(lambda r: calc_prob(r['STOCK_NUM'], r['PRE_14DAY_NUM']), axis=1)

        # 6.5 提取设备码级数据（供装箱优先级排序使用）
        dev_stock = {}
        dev_forecast = {}
        for _, r in merged.iterrows():
            key = (str(r['ORG_NO']).strip(), str(r['DEV_CODE']).strip())
            dev_stock[key] = float(r['STOCK_NUM'])
            dev_forecast[key] = float(r['PRE_14DAY_NUM'])
        logging.info(f"设备码级数据: {len(dev_stock)} 条 (STOCK) + {len(dev_forecast)} 条 (FORECAST)")

        # 6.6 网点聚合：取该网点所有设备码的最大缺货概率
        org_max_prob = merged.groupby('ORG_NO')['PROB'].max().reset_index()
        org_priority = dict(zip(org_max_prob['ORG_NO'], org_max_prob['PROB']))

    logging.info(f"缺货优先级计算完成: {len(org_priority)} 个网点")
    if org_priority:
        top5 = sorted(org_priority.items(), key=lambda x: x[1], reverse=True)[:5]
        logging.info(f"缺货概率 TOP5 网点: {top5}")

        # ---- 打印每个缺货概率TOP5网点的最缺设备码 ----
        logging.info("=" * 70)
        logging.info(f"{'排名':<4} {'网点编码':<12} {'网点名称':<16} {'缺货概率':>8} {'最缺设备码':<12} {'库存':>8} {'14天预测':>10} {'设备码缺货概率':>12}")
        logging.info("-" * 70)
        for rank, (org_no, org_prob) in enumerate(top5, 1):
            # 取该网点缺货概率最高的设备码
            node_rows = merged[merged['ORG_NO'] == org_no]
            if not node_rows.empty:
                best_row = node_rows.loc[node_rows['PROB'].idxmax()]
                best_dev = str(best_row['DEV_CODE']).strip()
                best_stock = float(best_row['STOCK_NUM'])
                best_forecast = float(best_row['PRE_14DAY_NUM'])
                best_prob = float(best_row['PROB'])
                org_name = str(best_row.get('ORG_NAME', ''))
            else:
                best_dev = '-'
                best_stock = 0
                best_forecast = 0
                best_prob = 0
                org_name = ''
            logging.info(f"{rank:<4} {str(org_no):<12} {org_name:<16} {org_prob:>8.4f} {best_dev:<12} {best_stock:>8.0f} {best_forecast:>10.0f} {best_prob:>12.4f}")
        logging.info("=" * 70)

    # 【核心】：将 global_scheme_id、org_priority、设备码级数据作为最后参数返回
    return Demands, InitQuaStock, LotList, DeviceCaps, SubTypeList, TypeList, DMAT, LocationNum, VeCap, VNums, VeUnitPrice, VeTypeNum, locations, global_scheme_id, org_priority, dev_stock, dev_forecast