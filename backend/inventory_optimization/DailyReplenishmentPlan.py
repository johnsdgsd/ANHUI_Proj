"""
日补库计划生成脚本
包括汇总配送方案结果得到日补库计划，根据日补库计划调整配送计划，获取配送计划数据等
"""
import pandas as pd
import numpy as np
from geopy.distance import geodesic
from backend.utils import GetPathDis
import pulp
import logging
import sys
from collections import defaultdict
import datetime
import time

def LoadDelivData(date: str, adjust_pack_box: bool = True):
    '''
    根据当日补库计划载入配送数据。

    参数:
        date:              配送日期，格式 'YYYY-MM-DD'
        adjust_pack_box:   是否对互感器(DEV_CLS='02')做 PACK_BOX_NUM/3 调整。
                           默认 True（兼容 V1/V2）；V3 传入 False，保留原始值供 GetDelivPlan 使用。
    '''
    logging.basicConfig(
        level=logging.INFO,  # 设置日志级别为 INFO
        format="%(asctime)s - %(levelname)s - %(message)s",  # 设置日志格式
        stream=sys.stdout  # 将日志输出到控制台
    )
    from backend.api.data_api.fetch_data import (
    query_adam_spec_code_config,query_adam_del_site_conf,
    query_adam_plan_day_ias_pre_by_date,query_vehicle_conf
    )
    #完整的规格设备码信息
    SubTypeList = query_adam_spec_code_config()
    # 互感器(DEV_CLS='02')按3倍体积折算（V1/V2 用），保留原始装箱数用于最终输出
    SubTypeList['PACK_BOX_NUM_ORIG'] = SubTypeList['PACK_BOX_NUM']
    if adjust_pack_box:
        mask_hgq = SubTypeList['DEV_CLS'] == '02'
        n_hgq = mask_hgq.sum()
        if n_hgq > 0:
            SubTypeList.loc[mask_hgq, 'PACK_BOX_NUM'] = (SubTypeList.loc[mask_hgq, 'PACK_BOX_NUM'] / 3).round().astype(int)
            logging.info(f'互感器体积折算(/3): {n_hgq} 种规格')
    else:
        logging.info(f'V3 模式：跳过互感器体积折算，保留原始 PACK_BOX_NUM')
    logging.info(f'载入配送数据：查询到{len(SubTypeList)}条规格设备码数据')
    SubTypeNum = len(SubTypeList)
    
    # 查询配送站点信息
    tb1 = query_adam_del_site_conf()
    logging.info(f'载入配送数据：查询到{len(tb1)}条配送站点信息')
    
    marketing_center = tb1[tb1['STAT_NAME'] == '营销服务中心']
    logging.info(f'载入配送数据：识别到{len(marketing_center)}个营销服务中心')
    
    tb1 = tb1[tb1['STAT_NAME'] != '营销服务中心'].sort_values('ORG_NO').reset_index(drop=True)
    LocationNum = len(tb1)
    logging.info(f'载入配送数据：筛选出{LocationNum}个非营销服务中心站点')
    
    # 查询当日补库计划
    tb2 = query_adam_plan_day_ias_pre_by_date(date)
    logging.info(f'载入配送数据：查询到{len(tb2)}条当日补库计划')
    if not tb2.empty and 'REPLE_TASK_TYPE' in tb2.columns:
        type_counts = tb2['REPLE_TASK_TYPE'].value_counts().to_dict()
        logging.info(f'日补库计划分类: 总数={len(tb2)}, '
                     f'01(临时补库)= {type_counts.get("01", 0)}, '
                     f'02(紧急补库)= {type_counts.get("02", 0)}, '
                     f'03(日常补库)= {type_counts.get("03", 0)}')
    if tb2.empty:
        logging.warning(f'当日 ({date}) 无补库计划，返回空需求')
        VeCap, VNums, VeUnitPrice, VeTypeNum, VeType = query_vehicle_conf()
        Demands = pd.DataFrame(np.zeros((LocationNum, SubTypeNum)))
        labels = ["中心"] + list(tb1['ORG_NO'])
        DMat = pd.DataFrame(np.zeros((LocationNum + 1, LocationNum + 1)), index=labels, columns=labels)
        lon_mc = marketing_center['LONGITUDE'].iloc[0]
        lat_mc = marketing_center['LATITUDE'].iloc[0]
        lons = [lon_mc] + list(tb1['LONGITUDE'])
        lats = [lat_mc] + list(tb1['LATITUDE'])
        return Demands, LocationNum, SubTypeList, VeUnitPrice, VeTypeNum, VNums, VeCap, DMat, 2, VeType, lons, lats

    Location = tb1['ORG_NO']
    LocationInd = tb2['REC_ORG_NO']
    SubTypeInd = tb2['DEV_CODE']
    Number = tb2['PLAN_IAS_NUM']
    SubType = SubTypeList['DEV_CODE']
    Demands = np.zeros((LocationNum, SubTypeNum))
    for i in range(LocationNum):
        for j in range(SubTypeNum):
            # 找到匹配 Location 和 SubType 的行
            idx = (LocationInd == Location[i]) & (SubTypeInd == SubType[j])
            if idx.any():
                Demands[i, j] = Number[idx].values[0]
    Demands=pd.DataFrame(Demands)
    # 最多三个地点
    MaxLen = 3 if len(set(LocationInd)) >=3 else 2
    #车辆信息（从数据库车型配置表读取）
    VeCap, VNums, VeUnitPrice, VeTypeNum,VeType = query_vehicle_conf()

    # 扣除已确认配送计划(DIST_FLAG='02')的需求和车辆
    from backend.api.data_api.fetch_data import (
        query_adam_dist_scheme_by_date_range,
        query_adam_dist_scheme_det_by_distschemeid)
    try:
        existing_schemes = query_adam_dist_scheme_by_date_range(date, date)
        confirmed = existing_schemes[existing_schemes['DIST_FLAG'] == '02']
        if not confirmed.empty and not tb2.empty:
            org_to_idx = {org: i for i, org in enumerate(Location)}
            dev_to_idx = {dev: j for j, dev in enumerate(SubType)}
            for _, crow in confirmed.iterrows():
                ct = crow['CAR_TYPE']
                for vi in range(VeTypeNum):
                    if VeType[vi] == ct and VNums[vi] > 0:
                        VNums[vi] -= 1
                        break
                sid = crow['DIST_SCHEME_ID']
                dets = query_adam_dist_scheme_det_by_distschemeid(sid)
                for _, drow in dets.iterrows():
                    oi = org_to_idx.get(drow['REC_ORG_NO'])
                    di = dev_to_idx.get(drow['DEV_CODE'])
                    if oi is not None and di is not None:
                        Demands.iloc[oi, di] = max(0, Demands.iloc[oi, di] - drow['PLAN_DIST_NUM'])
            logging.info(f'扣除已确认方案: {len(confirmed)} 条, 剩余可用车辆 {[int(n) for n in VNums]}')
    except ValueError:
        pass

    #计算网点间距离
    lons = tb1['LONGITUDE']
    lats = tb1['LATITUDE']
    #省中心经纬度
    lon = marketing_center['LONGITUDE'].iloc[0]
    lat = marketing_center['LATITUDE'].iloc[0]
    #插入到开头
    lons.loc[-1] = lon
    lons = lons.sort_index().reset_index(drop=True)
    lats.loc[-1] = lat
    lats = lats .sort_index().reset_index(drop=True)
    #构建距离矩阵
    numLocations = len(lons)
    DMat_np = np.zeros((numLocations, numLocations))
    for i in range(numLocations):
        for j in range(i+1, numLocations):
            # 使用 geopy 的 geodesic 方法计算两点间的距离（单位为公里）
            distance = geodesic((lats[i], lons[i]), (lats[j], lons[j])).km
            DMat_np[i][j] = 1.15 * distance
    DMat_np = DMat_np + DMat_np.T
    labels = ["中心"] + list(tb1['ORG_NO'])
    DMat = pd.DataFrame(DMat_np, index=labels, columns=labels)

    return Demands,LocationNum,SubTypeList,VeUnitPrice,VeTypeNum,VNums,VeCap,DMat,MaxLen,VeType,lons,lats

def GenerateDelivPlan(DelivPlan, Demands, SubTypeList):
    """
    后处理配送计划：考虑不能混装，将每个地点的总箱数按整数箱分配还原为各设备码件数。
    
    参数:
        DelivPlan: pd.DataFrame, 必须包含 'PlanPath' 和 'DeNum' 列
        Demands:   pd.DataFrame, 形状 (LocationNum, SubTypeNum), 每个地点各设备码的原始需求件数
        SubTypeList: pd.DataFrame, 必须包含 'PACK_BOX_NUM' 列
    
    返回:
        DelivPlan: pd.DataFrame, 新增 'DevicePieces' 列，记录每个停靠点每种设备码的实际配送件数
    """
    SubTypeNum = len(SubTypeList)
    LocationNum = Demands.shape[0]

    # 1. 将每个地点的需求转换为箱子列表（按设备顺序，满箱在前，尾箱在后）
    LocBoxes = {}  # key: 地点编号(1-based), value: list of (设备码索引, 箱内件数)
    for LocIdx in range(LocationNum):
        BoxList = []
        for DevIdx in range(SubTypeNum):
            DemandQty = Demands.iloc[LocIdx, DevIdx]
            if DemandQty == 0:
                continue
            BoxCap = SubTypeList.iloc[DevIdx]['PACK_BOX_NUM']
            FullBoxes = int(DemandQty // BoxCap)
            Remainder = DemandQty % BoxCap
            # 满箱
            for _ in range(FullBoxes):
                BoxList.append((DevIdx, BoxCap))
            # 尾箱（如果存在）
            if Remainder > 0:
                BoxList.append((DevIdx, Remainder))
        LocBoxes[LocIdx + 1] = BoxList

    # 2. 收集每个地点在哪些配送记录中出现，及分配的箱数
    NodeRecords = defaultdict(list)  # key: 地点编号, value: list of (行索引, 箱数, 路径中的位置)
    for RowIdx, Row in DelivPlan.iterrows():
        Path = Row['PlanPath']
        DeNums = Row['DeNum']
        for Pos, (Node, Box) in enumerate(zip(Path, DeNums)):
            NodeRecords[Node].append((RowIdx, int(round(Box)), Pos))

    # 3. 初始化新列 DevicePieces（每个元素是一个列表，长度等于停靠点数，每个停靠点存一个零数组）
    DelivPlan['DevicePieces'] = None
    for RowIdx, Row in DelivPlan.iterrows():
        StopCount = len(Row['PlanPath'])
        DelivPlan.at[RowIdx, 'DevicePieces'] = [
            np.zeros(SubTypeNum, dtype=int) for _ in range(StopCount)
        ]

    # 4. 对每个地点，按配送顺序依次分配箱子，并汇总各设备码件数
    for Node, Records in NodeRecords.items():
        BoxesTotal = LocBoxes.get(Node, [])
        if not BoxesTotal:
            continue

        # 按计划索引排序，确保分配顺序与配送顺序一致
        RecordsSorted = sorted(Records, key=lambda x: x[0])
        BoxCounts = [R[1] for R in RecordsSorted]

        if sum(BoxCounts) != len(BoxesTotal):
            logging.warning(
                f"地点 {Node} 的分配箱数总和 {sum(BoxCounts)} 与预计算箱子数 {len(BoxesTotal)} 不一致"
            )
            continue

        Start = 0
        for (RowIdx, _, Pos), Count in zip(RecordsSorted, BoxCounts):
            AssignedBoxes = BoxesTotal[Start:Start + Count]
            Start += Count
            # 汇总件数
            Pieces = np.zeros(SubTypeNum, dtype=int)
            for DevIdx, Qty in AssignedBoxes:
                Pieces[DevIdx] += Qty
            DelivPlan.at[RowIdx, 'DevicePieces'][Pos] = Pieces

    return DelivPlan

def GenerateSchemeTables(DelivPlan, PlanDate, SubTypeList, VeCap, CarTypeStrList, VeUnitPrice=None):
    """
    将配送计划解析为 ADAM_DIST_SCHEME（主表）和 ADAM_DIST_SCHEME_DET（明细表）
    参数：
        DelivPlan   : 包含 PathInd, VeType, Price, PlanPath, DeNum, PathNo, PathDis, DevicePieces
        PlanDate    : 计划日期字符串 (e.g., '2026-05-11')
        SubTypeList : 设备码配置，至少包含 DEV_CODE 列；可选 DEV_CLS, DEV_CATEG
        VeCap       : 各车型容量数组
    返回：
        MainDf   : 主表 DataFrame
        DetailDf : 明细表 DataFrame
    """
    # 生成全局方案标识：优先从审批方案查找，找不到则用日期
    year_month = PlanDate[:7].replace('-', '')
    fallback_id = int(PlanDate.replace('-', ''))
    try:
        from backend.config.scheme_config import get_approved_scheme_config
        global_scheme_id, _ = get_approved_scheme_config(year_month)
        # 未找到审批方案时返回时间戳(13位)作为默认值
        if global_scheme_id > 10 ** 10:
            print(f"未找到审批方案，使用日期 fallback: GLOBAL_SCHEME_ID={fallback_id}")
            GlobalSchemeId = fallback_id
        else:
            print(f"找到审批方案: GLOBAL_SCHEME_ID={global_scheme_id} (year_month={year_month})")
            GlobalSchemeId = global_scheme_id
    except Exception:
        print(f"获取审批方案异常，使用日期 fallback: GLOBAL_SCHEME_ID={fallback_id}")
        GlobalSchemeId = fallback_id
    CurrentDateStr = datetime.datetime.now().strftime('%Y-%m-%d')

    MainRows = []
    DetailRows = []

    # 基础时间戳（毫秒，13位）
    base_ts = int(time.time() * 1000)
    main_seq = 0
    det_seq = 0

    for RowIdx, Row in DelivPlan.iterrows():
        main_seq += 1
        scheme_id = base_ts * 1000 + main_seq  # 主表ID

        VeType = int(Row['VeType'])
        PathDis = Row['PathDis']
        Price = Row['Price']
        DeNum = Row['DeNum']
        PathNo = Row['PathNo']
        DevicePieces = Row['DevicePieces']
        SegDis = Row.get('SegDis', DeNum)  # 每站分段里程，兜底用箱数做近似

        TotalBoxes = sum(DeNum)
        TotalPieces = 0
        for StopPieces in DevicePieces:
            TotalPieces += sum(StopPieces)

        if VeType >= 1 and VeType <= len(VeCap):
            LoadRate = f"{TotalBoxes / VeCap[VeType - 1] * 100:.1f}%"
        else:
            LoadRate = "0%"

        MainRows.append({
            'DIST_SCHEME_ID': scheme_id,
            'CAR_TYPE': CarTypeStrList[VeType - 1],
            'PLAN_DIST_DATE': PlanDate,
            'DIST_FLAG': '01',
            'LATE_FLAG': '01',
            'LOAD_RATE': LoadRate,
            'CREATE_DATE': CurrentDateStr,
            'UPDATE_DATE': CurrentDateStr,
            'GLOBAL_SCHEME_ID': GlobalSchemeId
        })

        # 处理明细：同一站点共享分段里程
        for StopIdx, (OrgNo, StopPieces) in enumerate(zip(PathNo, DevicePieces)):
            DistSeq = StopIdx + 1
            LoadSeq = len(PathNo) - StopIdx
            stop_est_dist = SegDis[StopIdx] if StopIdx < len(SegDis) else 0
            stop_pieces = sum(StopPieces)
            for DevIdx, Qty in enumerate(StopPieces):
                if Qty == 0:
                    continue
                det_seq += 1
                det_id = base_ts * 1000 + det_seq  # 明细ID
                DevCode = SubTypeList.iloc[DevIdx]['DEV_CODE']
                DevCls = SubTypeList.iloc[DevIdx].get('DEV_CLS', '')
                DevCateg = SubTypeList.iloc[DevIdx].get('DEV_CATEG', '')

                box_cap_col = 'PACK_BOX_NUM_ORIG' if DevCls == '02' else 'PACK_BOX_NUM'
                BoxCap = SubTypeList.iloc[DevIdx][box_cap_col]
                plan_box_num = int(np.ceil(Qty / BoxCap))
                unit_price = VeUnitPrice[VeType - 1] if VeUnitPrice is not None else 0.0695
                DistExp = unit_price * plan_box_num * stop_est_dist
                DetailRows.append({
                    'DIST_SCHEME_DET_ID': det_id,
                    'DIST_SCHEME_ID': scheme_id,
                    'REC_ORG_NO': str(OrgNo),
                    'DEV_CODE': DevCode,
                    'DEV_CLS': DevCls,
                    'DEV_CATEG': DevCateg,
                    'DIST_SEQ': DistSeq,
                    'LOAD_SEQ': LoadSeq,
                    'PLAN_DIST_NUM': int(Qty),
                    'PLAN_BOX_NUM': plan_box_num,
                    'EST_TOT_DIST_MIST': round(stop_est_dist, 4),
                    'DIST_EXP': round(DistExp, 4),
                    'GLOBAL_SCHEME_ID': GlobalSchemeId
                })

    main_cols = ['DIST_SCHEME_ID', 'CAR_TYPE', 'PLAN_DIST_DATE', 'DIST_FLAG', 'LATE_FLAG',
                 'LOAD_RATE', 'CREATE_DATE', 'UPDATE_DATE', 'GLOBAL_SCHEME_ID']
    detail_cols = ['DIST_SCHEME_DET_ID', 'DIST_SCHEME_ID', 'REC_ORG_NO', 'DEV_CODE',
                   'DEV_CLS', 'DEV_CATEG', 'DIST_SEQ', 'LOAD_SEQ', 'PLAN_DIST_NUM',
                   'PLAN_BOX_NUM', 'EST_TOT_DIST_MIST', 'DIST_EXP', 'GLOBAL_SCHEME_ID']

    if not MainRows:
        logging.warning("GenerateSchemeTables: 无配送计划，返回空表")
        return pd.DataFrame(columns=main_cols), pd.DataFrame(columns=detail_cols)

    MainDf = pd.DataFrame(MainRows)[main_cols]
    DetailDf = pd.DataFrame(DetailRows)[detail_cols] if DetailRows else pd.DataFrame(columns=detail_cols)

    # 用序列生成真实ID，替换时间戳ID
    from backend.api.data_api.fetch_data import query_pk_next
    old_main_ids = MainDf['DIST_SCHEME_ID'].tolist()
    new_main_ids = [int(x) for x in query_pk_next("SEQ_ADAM_DIST_SCHEME", len(MainDf))]
    new_det_ids = [int(x) for x in query_pk_next("SEQ_ADAM_DIST_SCHEME_DET", len(DetailDf))]
    id_map = {old: new for old, new in zip(old_main_ids, new_main_ids)}
    MainDf['DIST_SCHEME_ID'] = new_main_ids
    DetailDf['DIST_SCHEME_ID'] = DetailDf['DIST_SCHEME_ID'].map(id_map)
    DetailDf['DIST_SCHEME_DET_ID'] = new_det_ids

    # 重算装载率：互感器箱数 ×2.5 计算真实体积装载率
    if not DetailDf.empty:
        detail_box = DetailDf.copy()
        detail_box['PLAN_BOX_NUM'] = detail_box['PLAN_BOX_NUM'].astype(float)
        mask_hgq_detail = detail_box['DEV_CLS'] == '02'
        detail_box.loc[mask_hgq_detail, 'PLAN_BOX_NUM'] *= 2.5
        real_boxes = detail_box.groupby('DIST_SCHEME_ID')['PLAN_BOX_NUM'].sum()
        car_type_to_idx = {ct: i for i, ct in enumerate(CarTypeStrList)}
        for i, row in MainDf.iterrows():
            sid = row['DIST_SCHEME_ID']
            if sid in real_boxes.index:
                ve_idx = car_type_to_idx.get(row['CAR_TYPE'], -1)
                if 0 <= ve_idx < len(VeCap):
                    rate = real_boxes[sid] / VeCap[ve_idx] * 100
                    MainDf.at[i, 'LOAD_RATE'] = f"{rate:.1f}%"

    return MainDf, DetailDf


def DailyReplenishmentPlan(start_date: str, end_date: str):
    """生成日补库计划
    根据指定日期范围的配送方案数据，生成日补库计划。

    Args:
        start_date: 开始日期，格式为 'YYYY-MM-DD'
        end_date: 结束日期，格式为 'YYYY-MM-DD'
    
    Returns:
        DaliyReplPlan :日度补库计划
    """
    from backend.api.data_api.fetch_data import (query_adam_dist_scheme_by_date_range,
    query_adam_dist_scheme_det_by_distschemeid,insert_into_adam_plan_day_ias_pre,
    delete_adam_plan_day_ias_pre_by_month)

    year_month = start_date[:4] + start_date[5:7]
    logging.info(f"[日补库] 删除 {year_month} 旧数据...")
    del_res = delete_adam_plan_day_ias_pre_by_month(year_month)
    logging.info(f"[日补库] 删除结果: {del_res}")

    DistSchemeDf = query_adam_dist_scheme_by_date_range(start_date , end_date)
    DistSchemeDf['PLAN_DIST_DATE'] = DistSchemeDf['PLAN_DIST_DATE'].str[:10]
    print(f"当前日期格式示例：{DistSchemeDf['PLAN_DIST_DATE'].iloc[0]}")
    Dist_Scheme_ID = DistSchemeDf['DIST_SCHEME_ID'].tolist()
    
    # 提取DIST_SCHEME_ID和PLAN_DIST_DATE作为字典映射
    scheme_date_map = DistSchemeDf.set_index('DIST_SCHEME_ID')['PLAN_DIST_DATE'].to_dict()
    
    #获取日期范围内的配送方案明细
    Dist_Scheme_Det = []
    for id in Dist_Scheme_ID:
        res = query_adam_dist_scheme_det_by_distschemeid(id)
        Dist_Scheme_Det.append(res)
    Dist_Scheme_Det = pd.concat(Dist_Scheme_Det, ignore_index=True)
    
    # 仅保留需要的列
    columns_to_keep = [
        'REC_ORG_NO',       # 接收单位（市/县）
        'DEV_CODE',         # 设备码
        'DEV_CLS',          # 设备分类
        'DEV_CATEG',        # 设备类别
        'PLAN_DIST_NUM',    # 计划配送数量
        'DIST_SCHEME_ID',   # 方案唯一标识
        'GLOBAL_SCHEME_ID'  # 全局方案标识
    ]
    #日补库计划结果（使用.copy()避免SettingWithCopyWarning）
    DaliyReplPlan = Dist_Scheme_Det[columns_to_keep].copy()
    
    # 新增PLAN_DIST_DATE列，根据DIST_SCHEME_ID从scheme_date_map中取值
    DaliyReplPlan['PLAN_DIST_DATE'] = DaliyReplPlan['DIST_SCHEME_ID'].map(scheme_date_map)
    
    # 重命名列名
    column_mapping = {
        'REC_ORG_NO': 'REC_ORG_NO',       # 接收单位编码（市/县）
        'DEV_CLS': 'DEV_CLS',             # 设备分类
        'DEV_CATEG': 'DEV_CATEG',         # 设备类别
        'DEV_CODE': 'DEV_CODE',           # 设备码
        'PLAN_DIST_NUM': 'PLAN_IAS_NUM',  # 计划补库数量
        'PLAN_DIST_DATE': 'PRE_DATE',     # 补库日期
        'GLOBAL_SCHEME_ID': 'GLOBAL_SCHEME_ID'  # 全局方案标识
    }
    #映射并过滤DIST_SCHEME_ID列
    DaliyReplPlan = DaliyReplPlan.rename(columns=column_mapping)[list[str](column_mapping.values())]
    # 按照日期从小到大排列，相同单位相同设备码的数据放在一起
    DaliyReplPlan = DaliyReplPlan.sort_values(
        by=['PRE_DATE', 'REC_ORG_NO', 'DEV_CODE'],
        ascending=[True, True, True]
    ).reset_index(drop=True)

    # 合并相同(REC_ORG_NO, DEV_CODE, PRE_DATE)的行，PLAN_IAS_NUM求和
    group_cols = ['PRE_DATE', 'REC_ORG_NO', 'DEV_CODE', 'DEV_CLS', 'DEV_CATEG', 'GLOBAL_SCHEME_ID']
    DaliyReplPlan = DaliyReplPlan.groupby(group_cols, as_index=False)['PLAN_IAS_NUM'].sum()
    DaliyReplPlan = DaliyReplPlan[['REC_ORG_NO', 'DEV_CLS', 'DEV_CATEG', 'DEV_CODE',
                                     'PLAN_IAS_NUM', 'PRE_DATE', 'GLOBAL_SCHEME_ID']]

    timestamp = int(time.time()*1000)
    DaliyReplPlan['EST_STOCK_NUM'] = None
    # 添加状态和类型列
    DaliyReplPlan['DAILY_PLAN_STATUS'] = '01'
    DaliyReplPlan['REPLE_TASK_TYPE'] = '03'
    DaliyReplPlan['TASK_SOURCE'] = '03'
    from backend.api.data_api.fetch_data import query_pk_next
    DaliyReplPlan['PLAN_MONTH_IAS_PRE_ID'] = [int(x) for x in query_pk_next("SEQ_ADAM_PLAN_DAY_IAS_PRE", len(DaliyReplPlan))]
    return DaliyReplPlan,insert_into_adam_plan_day_ias_pre(DaliyReplPlan)


def AdjustDaliyDelivery(date:str):
    """
    根据日补库计划调整日配送
    """
    from backend.api.data_api.fetch_data import (
        query_adam_dist_scheme_by_date_range,
        delete_adam_dist_scheme_det_by_scheme_id,
        delete_adam_dist_scheme_by_id)
    logging.basicConfig(
        level=logging.INFO,  # 设置日志级别为 INFO
        format="%(asctime)s - %(levelname)s - %(message)s",  # 设置日志格式
        stream=sys.stdout  # 将日志输出到控制台
    )
    # 删除当天未确认(DIST_FLAG!='02')的配送方案，保留已确认的
    try:
        existing = query_adam_dist_scheme_by_date_range(date, date)
        if not existing.empty:
            unconfirmed = existing[existing['DIST_FLAG'] != '02']
            for sid in unconfirmed['DIST_SCHEME_ID'].tolist():
                delete_adam_dist_scheme_det_by_scheme_id(sid)
                delete_adam_dist_scheme_by_id(sid)
            kept = len(existing) - len(unconfirmed)
            logging.info(f"已删除 {len(unconfirmed)} 条未确认方案" + (f"，保留 {kept} 条已确认方案" if kept > 0 else ""))
    except ValueError:
        logging.info(f"当天 ({date}) 无旧配送方案，跳过删除")

    Demands,LocationNum,SubTypeList,VeUnitPrice,VeTypeNum,VNums,VeCap,DMAT,MaxLen,CarTypeStrList,lons,lats=LoadDelivData(date)
    org_labels = DMAT.columns.tolist()  # ["中心", org1, org2, ...]
    EmptyPenalty = VeUnitPrice * 0.5  # 可根据实际调整
    SubTypeNum = len(SubTypeList)
    DemandsBoxs = np.zeros((LocationNum, SubTypeNum))
    for i in range(SubTypeNum):
      UnitPerBoxI = SubTypeList.loc[i, 'PACK_BOX_NUM']
      DemandsBoxs[:, i] = np.ceil(Demands.loc[:, i].values / UnitPerBoxI)

    DemandsBoxs = np.sum(DemandsBoxs, axis=1)  # 将 DemandsBoxs 按行求和
    MinDeliverNum=20 #最小装箱数
    if np.sum(DemandsBoxs) > 0 and np.sum(VNums) == 0:
        logging.warning("有配送需求但无可用车辆（全部被已确认方案占用），返回空方案")
        return pd.DataFrame(), pd.DataFrame()
    logging.info("计算路径数")
    DMAT = DMAT.values

    PathInfo, _ = GetPathDis(DMAT, MaxLen)
    PathInfo=pd.DataFrame(PathInfo)

    SaveDis = np.zeros(len(PathInfo))

    for i in range(len(PathInfo)):
        path = PathInfo.loc[i, 'Path']
        if len(path) != 1:
            SaveDis[i] = (PathInfo.loc[path[0]-1, 'PathDis'] + PathInfo.loc[path[1]-1, 'PathDis']) - PathInfo.loc[i, 'PathDis']

    logging.info("对 SaveDis 排序并获取索引")
    sorted_indices= np.argsort(SaveDis, kind='stable') + 1
    sorted_indices = sorted_indices[len(DMAT)-1:]
    # 仅保留节省距离最高的前5%路径（MaxLen=5时路径数量大，需大幅裁剪）
    k = int(np.ceil(0.05 * len(sorted_indices)))
    start_row = len(sorted_indices) - k
    index = sorted_indices[start_row:]
    index = np.concatenate([np.arange(1, len(DMAT)), index])

    indicesToDelete = []
    for i in range(len(PathInfo)):
        if i + 1 not in index:  
            indicesToDelete.append(i)


    # 删除对应的索引
    PathInfo = PathInfo.drop(indicesToDelete).reset_index(drop=True)
    ind = PathInfo['PathDis'] < 900
    ind[:LocationNum] = True  # 保证前 LocationNum 行为 True
    PathInfo = PathInfo[ind]
    PathNum = len(PathInfo)
    PathInfo['Ind'] = range(1, len(PathInfo) + 1)
    PathInfo.index= range(len(PathInfo))

    PlanInd = 1  # 配送计划编号
    DeNum = [None] * 1000  # 存放每次配送的装箱数量
    VeType = np.zeros(1000, dtype=int)  # 存放每次配送用的车辆种类
    PathInd = np.zeros(1000, dtype=int)  # 配送的路径编号
    Price = np.zeros(1000)  # 配送的价格
    PlanPath = [None] * 1000  # 配送路径，使用列表存储

    logging.info("首先用单一的配送车辆，进行整车配送,优先对距离远的进行整车配送")
    LocationDis = PathInfo.loc[:LocationNum-1, 'PathDis'].to_numpy()
    LocationInds = np.argsort(LocationDis)+1
    LocationInds=LocationInds[::-1]
    #单日配送规划
    DelivDay = 1
    VNums_All = VNums * DelivDay # 每种车辆可以派车的总次数

    # 由于箱数较少，基本上没有整车配送
    for i in range(LocationNum):
        LI = LocationInds[i]  # 获取当前排序的路径索引
        while DemandsBoxs[LI-1] >= VeCap[2] + MinDeliverNum:  # 确定是否可以继续配送，是否大于最小车的箱数
            # 确定需要使用的车辆种类
            for j in range(VeTypeNum):
                if DemandsBoxs[LI-1] >= VeCap[j] + MinDeliverNum and VNums_All[j] >= 1:
                    VeType[PlanInd-1] = j + 1
                    VNums_All[j] -= 1
                    break
            DemandsBoxs[LI-1] -= VeCap[j]
            DeNum[PlanInd-1] = VeCap[j]
            PathInd[PlanInd-1] = LI
            PlanPath[PlanInd-1] = LI
            PlanPath[PlanInd - 1]=[PlanPath[PlanInd-1]]
            Price[PlanInd - 1] = VeUnitPrice[j] * VeCap[j] * PathInfo.loc[LI - 1, 'PathDis'] # 计算价格
            PlanInd += 1
    VTypeTimes=VNums_All

    logging.info("路径箱数分配：决策变量是x_n,v,l 整数三维（目的地，车辆种类，路径序号）；I_v,l,0,1变量")
    logging.info("构建压缩路径‑箱数规划模型")

    # ===================== 1. 提取活性节点并过滤可用路径 =====================
    active_nodes = np.where(DemandsBoxs > 0)[0]   # 0‑based 索引
    active_set = set(active_nodes + 1)                  # 1‑based 集合，便于判断
    logging.info(f'只保留首尾节点都在活性集合中的路径,活性节点数量{len(active_set)}')

    keep_idx = []
    for i in range(len(PathInfo)):
        p = PathInfo.loc[i, 'Path']
        if p[0] in active_set and p[-1] in active_set:
            keep_idx.append(i)
    PathInfo_active = PathInfo.iloc[keep_idx].reset_index(drop=True)

    logging.info(f'保存原始路径编号（用于最后映射回原始计划）,活性节点中的路径数量{len(PathInfo_active)}')
    orig_path_indices = [PathInfo.index[i] + 1 for i in keep_idx]   # 原始 Ind（1‑based）

    N_active = len(active_nodes)           # 活性网点数
    PathNum_new = len(PathInfo_active)     # 可用路径数
    demands_active = DemandsBoxs[active_nodes]   # 活性网点剩余需求
    logging.info(f'活性网点数：{N_active},可用路径数量:{PathNum_new}')
    logging.info('最小配送量映射：需求 < 20 的活性网点')
    mindeliver_map = {}
    for pos, orig_idx in enumerate(active_nodes):
        if DemandsBoxs[orig_idx] < MinDeliverNum:
            mindeliver_map[pos] = DemandsBoxs[orig_idx]   # pos 为 0‑based（在 active_nodes 中的下标）

    logging.info('2. 构建压缩模型（含空载惩罚）')
    # ===================== 2. 构建压缩模型（含空载惩罚） =====================
    prob = pulp.LpProblem("deleivplan_compact", pulp.LpMinimize)

    # 变量字典
    x_nvl = {}   # (n, v, l) ，n 从 1 开始
    I_vl = {}    # (v, l)

    # 创建分配变量 x_{n,v,l}
    for n in range(1, N_active + 1):
        for v in range(1, VeTypeNum + 1):
            for l in range(1, PathNum_new + 1):
                x_nvl[(n, v, l)] = pulp.LpVariable(
                    f"x_n{n}_v{v}_l{l}", lowBound=0,
                    upBound=demands_active[n - 1], cat='Integer')

    # 创建选择变量 I_{v,l}
    for v in range(1, VeTypeNum + 1):
        for l in range(1, PathNum_new + 1):
            I_vl[(v, l)] = pulp.LpVariable(
                f"I_v{v}_l{l}", lowBound=0, upBound=1, cat='Integer')

    # ---------- 目标函数：距离 × [ 载箱量×单价 + (容量·I - 载箱量)×空载惩罚 ] ----------
    prob += pulp.lpSum(
        PathInfo_active.loc[l - 1, 'PathDis'] * (
            pulp.lpSum(
                VeUnitPrice[v - 1] * x_nvl[(n, v, l)]
                for n in range(1, N_active + 1)
            )
            + EmptyPenalty[v - 1] * (
                VeCap[v - 1] * I_vl[(v, l)]
                - pulp.lpSum(x_nvl[(n, v, l)] for n in range(1, N_active + 1))
            )
        )
        for v in range(1, VeTypeNum + 1)
        for l in range(1, PathNum_new + 1)
    )

    # ---------- 约束1：需求等式约束 ----------
    for n in range(1, N_active + 1):
        terms = []
        orig_node = active_nodes[n - 1] + 1   # 原始 1‑based 节点号
        for l in range(1, PathNum_new + 1):
            if orig_node in PathInfo_active.loc[l - 1, 'Path']:
                for v in range(1, VeTypeNum + 1):
                    terms.append(x_nvl[(n, v, l)])
        prob += pulp.lpSum(terms) == demands_active[n - 1]

    # ---------- 约束2 & 3：容量约束 + 最小配送量约束 ----------
    for v in range(1, VeTypeNum + 1):
        for l in range(1, PathNum_new + 1):
            path = PathInfo_active.loc[l - 1, 'Path']   # 1‑based 原始节点列表
            vars_in_path = []
            first_var = None
            last_var = None
            for raw_node in path:
                if (raw_node - 1) in active_nodes:
                    pos = np.where(active_nodes == raw_node - 1)[0][0] + 1   # 转换为 1‑based 新索引
                    var = x_nvl[(pos, v, l)]
                    vars_in_path.append(var)
                    if raw_node == path[0]:
                        first_var = var
                    if raw_node == path[-1]:
                        last_var = var

            # 容量约束：总配送量 ≤ 容量·I
            if vars_in_path:
                prob += pulp.lpSum(vars_in_path) <= VeCap[v - 1] * I_vl[(v, l)]

            # 最小配送量约束（首节点）
            if first_var is not None:
                first_node_raw = path[0]
                pos_first = np.where(active_nodes == first_node_raw - 1)[0][0]   # 0‑based
                min_q = mindeliver_map.get(pos_first, MinDeliverNum)
                prob += first_var >= min_q * I_vl[(v, l)]

            # 尾节点最小配送量（路径长度>1，且尾节点不同于首节点）
            if last_var is not None and len(path) > 1 and path[-1] != path[0]:
                last_node_raw = path[-1]
                pos_last = np.where(active_nodes == last_node_raw - 1)[0][0]
                min_q_last = mindeliver_map.get(pos_last, MinDeliverNum)
                prob += last_var >= min_q_last * I_vl[(v, l)]

    # ---------- 约束4：每种车型的使用次数上限 ----------
    for v in range(1, VeTypeNum + 1):
        prob += pulp.lpSum(I_vl[(v, l)] for l in range(1, PathNum_new + 1)) <= VTypeTimes[v - 1]

    # ===================== 3. 求解 =====================
    logging.info("开始求解压缩路径‑箱数规划")
    solver = pulp.PULP_CBC_CMD(
        msg=True,
        options=['ratioGap=0.01', 'sec=120']
    )
    prob.solve(solver)
    status_str = pulp.LpStatus[prob.status]
    logging.info(f"求解完成，状态：{status_str}，目标值：{pulp.value(prob.objective)}")

    # ===================== 4. 解映射，追加到计划数组 =====================
    logging.info(f"开始解映射: VeTypeNum={VeTypeNum}, PathNum_new={PathNum_new}, active_nodes={len(active_nodes)}, PlanInd起始={PlanInd}")
    selected = []
    for v in range(1, VeTypeNum + 1):
        for l in range(1, PathNum_new + 1):
            val = I_vl[(v, l)].value()
            if val is not None and abs(val - 1) < 0.1:
                selected.append((v, l))
    logging.info(f"严格阈值选中 {len(selected)} 条路径")

    # 若超时未找到整数解，按 I_vl 值从高到低取最大的路径
    if len(selected) == 0 and status_str != 'Optimal':
        logging.warning(f"求解状态={status_str}，未找到整数解，降级使用分数解")
        candidates = []
        for v in range(1, VeTypeNum + 1):
            for l in range(1, PathNum_new + 1):
                val = I_vl[(v, l)].value()
                if val is not None and val > 0.01:
                    candidates.append((val, v, l))
        candidates.sort(reverse=True)
        # 每种车型最多用 VTypeTimes 次
        v_used = {v: 0 for v in range(1, VeTypeNum + 1)}
        for val, v, l in candidates:
            if v_used[v] < VTypeTimes[v - 1]:
                selected.append((v, l))
                v_used[v] += 1
        logging.info(f"降级模式选中 {len(selected)} 条路径: {selected[:10]}{'...' if len(selected) > 10 else ''}")

    for idx, (v, l) in enumerate(selected):
        orig_path_idx = orig_path_indices[l - 1]          # 原始 PathInfo 编号（1‑based）
        VeTypeI = v
        PathIndI = orig_path_idx

        logging.info(f"[{idx+1}/{len(selected)}] v={v}, l={l}, orig_path_idx={orig_path_idx}, PlanInd={PlanInd}")
        # 路径节点（原始 1‑based）
        path_nodes = PathInfo.loc[orig_path_idx - 1, 'Path'].copy()  # 列表
        logging.info(f"[{idx+1}/{len(selected)}] path_nodes={path_nodes}, type={type(path_nodes)}")
        PlanPath[PlanInd - 1] = path_nodes

        # 计算每个节点的配送量（活性节点取整，非活性节点为 0）
        deliv_vec = np.zeros(len(path_nodes))
        for j, node in enumerate(path_nodes):
            if (node - 1) in active_nodes:
                pos = np.where(active_nodes == node - 1)[0][0] + 1   # 新索引 1‑based
                val = x_nvl[(pos, v, l)].value()
                logging.info(f"[{idx+1}/{len(selected)}] node={node}, pos={pos}, x_nvl.value={val}")
                deliv_vec[j] = round(val)
        total_boxes = np.sum(deliv_vec)
        logging.info(f"[{idx+1}/{len(selected)}] deliv_vec={deliv_vec}, total_boxes={total_boxes}")

        # ---------- 价格 = 距离 × (载箱成本 + 空载惩罚) ----------
        loaded_cost = VeUnitPrice[v - 1] * total_boxes
        empty_cost = EmptyPenalty[v - 1] * (VeCap[v - 1] - total_boxes)
        path_dis = PathInfo.loc[orig_path_idx - 1, 'PathDis']
        Price[PlanInd - 1] = (loaded_cost + empty_cost) * path_dis
        logging.info(f"[{idx+1}/{len(selected)}] loaded_cost={loaded_cost}, empty_cost={empty_cost}, path_dis={path_dis}, price={Price[PlanInd - 1]}")

        VeType[PlanInd - 1] = VeTypeI
        PathInd[PlanInd - 1] = PathIndI
        DeNum[PlanInd - 1] = deliv_vec
        PlanInd += 1

    logging.info(f"解映射完成，共 {len(selected)} 条路径, PlanInd最终={PlanInd}")

    # ===================== 5. 生成最终计划 DataFrame =====================
    logging.info(f"开始生成最终计划: DeNum长度={len(DeNum)}, PlanInd={PlanInd}")
    DeNum = DeNum[:PlanInd - 1]
    VeType = VeType[:PlanInd - 1]
    PathInd = PathInd[:PlanInd - 1]
    Price = Price[:PlanInd - 1]
    PlanPath = PlanPath[:PlanInd - 1]
    logging.info(f"切片后: DeNum={len(DeNum)}, VeType={len(VeType)}, PathInd={len(PathInd)}, Price={len(Price)}, PlanPath={len(PlanPath)}")

    DelivPlan = {
        'PathInd': PathInd,
        'VeType': VeType,
        'Price': Price,
        'PlanPath': PlanPath,
        'DeNum': DeNum
    }
    DelivPlan = pd.DataFrame(DelivPlan)
    # 规范化：确保 DeNum 和 PlanPath 列的元素都是 Python list（numpy 数组/标量会导致 sum() 等操作失败）
    DelivPlan['DeNum'] = DelivPlan['DeNum'].apply(lambda x: [int(x)] if isinstance(x, (np.integer, np.floating)) else list(x))
    DelivPlan['PlanPath'] = DelivPlan['PlanPath'].apply(lambda x: [int(x)] if isinstance(x, (np.integer, np.floating)) else list(x))
    logging.info(f"DelivPlan DataFrame: {len(DelivPlan)} 行, columns={list(DelivPlan.columns)}")

    # PathNo 映射：PlanPath 整数索引 (1-based) → DMAT 列标签中的 ORG_NO
    Path_no = []
    for planpath in DelivPlan['PlanPath']:
        p = [org_labels[idx] for idx in planpath]
        Path_no.append(p)
    DelivPlan['PathNo'] = Path_no
    logging.info(f"PathNo映射完成，示例: {Path_no[0] if Path_no else '空'}")
    DelivPlan['PathDis'] = DelivPlan['PathInd'].apply(lambda i: PathInfo.loc[i-1, 'PathDis'])
    logging.info("开始 GenerateDelivPlan...")
    DelivPlan = GenerateDelivPlan(DelivPlan,Demands,SubTypeList)
    logging.info(f"GenerateDelivPlan 完成, {len(DelivPlan)} 行")

    # 计算每站分段里程
    seg_dis_list = []
    for _, row in DelivPlan.iterrows():
        planpath = row['PlanPath']
        segs = [DMAT[0, planpath[0]]]
        for j in range(1, len(planpath)):
            segs.append(DMAT[planpath[j-1], planpath[j]])
        seg_dis_list.append(segs)
    DelivPlan['SegDis'] = seg_dis_list

    # 校验：日补库需求总量 vs 配送计划总量
    total_demand_pieces = Demands.values.sum()
    total_deliv_pieces = 0
    for _, row in DelivPlan.iterrows():
        for stop_pieces in row['DevicePieces']:
            total_deliv_pieces += sum(stop_pieces)
    total_deliv_boxes = sum(sum(d) if hasattr(d, '__iter__') else d for d in DelivPlan['DeNum'])
    logging.info(f"[校验] 日补库需求总件数: {int(total_demand_pieces)}, 配送计划总件数: {int(total_deliv_pieces)}, 差异: {int(total_demand_pieces - total_deliv_pieces)}")
    logging.info(f"[校验] 配送计划总箱数: {int(total_deliv_boxes)}")

    logging.info("开始 GenerateSchemeTables...")
    MainScheme , DetailScheme = GenerateSchemeTables(DelivPlan,date,SubTypeList, VeCap, CarTypeStrList, VeUnitPrice)
    logging.info(f"GenerateSchemeTables 完成: MainScheme={len(MainScheme)}行, DetailScheme={len(DetailScheme)}行")
    return MainScheme , DetailScheme