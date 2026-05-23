import pandas as pd
import numpy as np
from geopy.distance import geodesic
import logging
import sys


def LoadDeliChcekData(target_month, start_date_str):
    from Service_CheckDeliver import fetch_data
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

    center_loc = pd.DataFrame([{'ORG_NO': 'CENTER', 'ORG_NAME': '省级总库', 'LAT': 31.87, 'LON': 117.18}])
    locations = pd.concat([center_loc, locations], ignore_index=True)

    df_mapping = fetch_data("gk-adam-query_aps_pro_dev_mapping")
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
        LotList = LotList.sort_values(by=['PLAN_DATE', 'SOURCE_TYPE'], ascending=[True, False]).reset_index(drop=True)

    # ================= 4. 读取产线产能及距离矩阵 =================
    DeviceCaps = fetch_data("gk-adam-query_check_line")
    if not DeviceCaps.empty:
        DeviceCaps.columns = [c.upper() for c in DeviceCaps.columns]

    logging.info(">>> 计算网点距离矩阵...")
    num_nodes = LocationNum + 1
    DMAT = np.zeros((num_nodes, num_nodes))
    lats = locations['LAT'].values
    lons = locations['LON'].values
    for i in range(num_nodes):
        for j in range(i + 1, num_nodes):
            if pd.notnull(lats[i]) and pd.notnull(lons[i]) and pd.notnull(lats[j]) and pd.notnull(lons[j]):
                dist = geodesic((lats[i], lons[i]), (lats[j], lons[j])).km
                DMAT[i, j] = DMAT[j, i] = 1.15 * dist

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

    # 【核心】：将 global_scheme_id 作为最后一个参数返回
    return Demands, InitQuaStock, LotList, DeviceCaps, SubTypeList, TypeList, DMAT, LocationNum, VeCap, VNums, VeUnitPrice, VeTypeNum, locations, global_scheme_id