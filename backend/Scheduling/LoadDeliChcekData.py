import pandas as pd
import numpy as np
from geopy.distance import geodesic
import logging
import sys

def LoadDeliChcekData(target_month, start_date_str):
    from backend.Scheduling.Service_CheckDeliver import fetch_data
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s", stream=sys.stdout)

    df_demand = fetch_data("query_remain_demand", {"stat_month": target_month})
    if df_demand.empty: raise ValueError("当月无需求数据")
    df_demand.columns = [c.upper() for c in df_demand.columns]

    locations = df_demand[['ORG_NO', 'ORG_NAME', 'LAT', 'LON']].drop_duplicates().reset_index(drop=True)
    LocationNum = len(locations)

    center_loc = pd.DataFrame([{'ORG_NO': 'CENTER', 'ORG_NAME': '省级总库', 'LAT': 31.87, 'LON': 117.18}])
    locations = pd.concat([center_loc, locations], ignore_index=True)

    df_mapping = fetch_data("query_aps_pro_dev_mapping")
    df_mapping.columns = [c.upper() for c in df_mapping.columns]

    TypeList = df_mapping[['DEV_CODE_NO', 'PACK_BOX_NUM']].drop_duplicates().reset_index(drop=True)
    TypeList.rename(columns={'PACK_BOX_NUM': 'UnitPerBox'}, inplace=True)

    SubTypeList = df_mapping.drop_duplicates(subset='DEV_CODE_NO').reset_index(drop=True)

    # ====== 【核心修复】：智能补全 DEV_CODE_DESC ======
    if 'DEV_CODE_DESC' not in SubTypeList.columns:
        if 'DEV_CODE_DESC' in df_demand.columns:
            # 尝试从需求表(df_demand)里把描述“借”过来映射上
            desc_map = dict(zip(df_demand['DEV_CODE_NO'], df_demand['DEV_CODE_DESC']))
            SubTypeList['DEV_CODE_DESC'] = SubTypeList['DEV_CODE_NO'].map(desc_map).fillna('')
        else:
            # 如果 SQL 里完全没查出这个字段，安全兜底设为空字符串，绝不报错
            SubTypeList['DEV_CODE_DESC'] = ''
    # ===================================================

    SubTypeNum = len(SubTypeList)

    Demands = np.zeros((LocationNum, SubTypeNum))
    for i in range(LocationNum):
        org_no = locations.loc[i + 1, 'ORG_NO']
        org_demands = df_demand[df_demand['ORG_NO'] == org_no]
        for j in range(SubTypeNum):
            dev_code = SubTypeList.loc[j, 'DEV_CODE_NO']
            match = org_demands[org_demands['DEV_CODE_NO'] == dev_code]
            if not match.empty: Demands[i, j] = match['REQ_NUM'].sum()

    df_qua = fetch_data("query_realtime_qua_stock")
    InitQuaStock = np.zeros(SubTypeNum)
    if not df_qua.empty:
        df_qua.columns = [c.upper() for c in df_qua.columns]
        for j in range(SubTypeNum):
            dev_code = SubTypeList.loc[j, 'DEV_CODE_NO']
            match = df_qua[df_qua['DEV_CODE_NO'] == dev_code]
            if not match.empty: InitQuaStock[j] = match['QUA_STOCK_NUM'].sum()

    LotList = fetch_data("query_future_arr_plan", {"start_date": start_date_str})
    if not LotList.empty:
        LotList.columns = [c.upper() for c in LotList.columns]
        LotList['PLAN_DATE'] = pd.to_datetime(LotList['PLAN_DATE'])
        LotList['RemNum'] = LotList['PLAN_ARR_NUM'].astype(int)

    DeviceCaps = fetch_data("query_check_line")
    if not DeviceCaps.empty: DeviceCaps.columns = [c.upper() for c in DeviceCaps.columns]

    num_nodes = LocationNum + 1
    DMAT = np.zeros((num_nodes, num_nodes))
    lats = locations['LAT'].values
    lons = locations['LON'].values
    for i in range(num_nodes):
        for j in range(i + 1, num_nodes):
            if pd.notnull(lats[i]) and pd.notnull(lons[i]) and pd.notnull(lats[j]) and pd.notnull(lons[j]):
                dist = geodesic((lats[i], lons[i]), (lats[j], lons[j])).km
                DMAT[i, j] = DMAT[j, i] = 1.15 * dist

    VeCap = np.array([1100, 900, 410])
    VNums = np.array([6, 10, 9])
    VeUnitPrice = np.array([0, 0, 0])
    VeTypeNum = 3

    return Demands, InitQuaStock, LotList, DeviceCaps, SubTypeList, TypeList, DMAT, LocationNum, VeCap, VNums, VeUnitPrice, VeTypeNum, locations