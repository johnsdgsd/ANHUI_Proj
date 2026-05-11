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

def LoadDelivData(date:str):
    '''
    根据当日补库计划载入配送数据
    '''
    from backend.api.data_api.fetch_data import (
    query_adam_spec_code_config,query_adam_del_site_conf,
    query_adam_plan_day_ias_pre_by_date
    )
    #完整的规格设备码信息
    SubTypeList = query_adam_spec_code_config()
    SubTypeNum = len(SubTypeList)
    # 查询配送站点信息
    tb1 = query_adam_del_site_conf()
    marketing_center = tb1[tb1['STAT_NAME'] == '营销服务中心']
    tb1 = tb1[tb1['STAT_NAME'] != '营销服务中心']
    #减去省中心
    LocationNum = len(tb1)
    # 查询当日补库计划
    tb2 = query_adam_plan_day_ias_pre_by_date(date)
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
    
    #车辆信息
    VehicleTb = {
        "VeType":['大号','中号','小号'],
        "VeCap":[1100,900,410],
        "VNum":[12,35,34],
        "VeUnitPrice":[0.07,0.07,0.07]
    }
    VehicleTb = pd.DataFrame(VehicleTb)

    VeUnitPrice=VehicleTb['VeUnitPrice']#运费
    VeCap=VehicleTb['VeCap']#容量
    VNums=VehicleTb['VNum']#车辆数
    VeTypeNum=VehicleTb.shape[0]#车辆种类数

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
    DMat = np.zeros((numLocations, numLocations))
    for i in range(numLocations):
        for j in range(i+1, numLocations):
            # 使用 geopy 的 geodesic 方法计算两点间的距离（单位为公里）
            distance = geodesic((lats[i], lons[i]), (lats[j], lons[j])).km
            DMat[i][j] = 1.15 * distance
    DMat=pd.DataFrame(DMat)
    DMat.columns = range(1, numLocations+ 1)
    DMat.index = range(1, numLocations+ 1)

    return Demands,LocationNum,SubTypeList,VeUnitPrice,VeTypeNum,VNums,VeCap,DMat

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
        if not isinstance(DeNums, list):
            DeNums = list(DeNums)
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


def ExpandDeviceDetail(DelivPlan, SubTypeList):
    """
    将配送计划按设备码展开为明细表。
    
    参数:
        DelivPlan: 包含 PathNo, DevicePieces 的 DataFrame
        SubTypeList: 包含 DEV_CODE 的设备码表 (索引对应设备码编号 0-based)
    返回:
        明细 DataFrame，列：OrgNo, DevCode, Quantity
    """
    Rows = []
    for _, Plan in DelivPlan.iterrows():
        PathNo = Plan['PathNo']          # 例如 ['3441501', '34406']
        DevicePieces = Plan['DevicePieces']   # 列表的列表，内层为设备码件数数组
        # 遍历每个停靠点
        for StopIdx, (Org, Pieces) in enumerate(zip(PathNo, DevicePieces)):
            # 遍历设备码
            for DevIdx, Qty in enumerate(Pieces):
                if Qty > 0:
                    DevCode = SubTypeList.iloc[DevIdx]['DEV_CODE']
                    Rows.append({
                        'OrgNo': Org,
                        'DevCode': DevCode,
                        'Quantity': int(Qty)
                    })
    DetailDf = pd.DataFrame(Rows)
    return DetailDf


def DailyReplenishmentPlan(start_date: str, end_date: str) -> pd.DataFrame:
    """生成日补库计划
    根据指定日期范围的配送方案数据，生成日补库计划。

    Args:
        start_date: 开始日期，格式为 'YYYY-MM-DD'
        end_date: 结束日期，格式为 'YYYY-MM-DD'
    
    Returns:
        DaliyReplPlan :日度补库计划
    """
    from backend.api.data_api.fetch_data import (query_adam_dist_scheme_by_date_range,
    query_adam_dist_scheme_det_by_distschemeid,insert_into_adam_plan_day_ias_pre)
    
    DistSchemeDf = query_adam_dist_scheme_by_date_range(start_date , end_date)
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
    DaliyReplPlan['PLAN_MONTH_IAS_PRE_ID'] = range(10001,10001+len(DaliyReplPlan))
    DaliyReplPlan['EST_STOCK_NUM'] = None
    insert_into_adam_plan_day_ias_pre(DaliyReplPlan)
    return DaliyReplPlan


def AdjustDaliyDelivery(date:str):
    """
    根据日补库计划调整日配送
    """
    from backend.api.data_api.fetch_data import query_adam_del_site_conf
    logging.basicConfig(
        level=logging.INFO,  # 设置日志级别为 INFO
        format="%(asctime)s - %(levelname)s - %(message)s",  # 设置日志格式
        stream=sys.stdout  # 将日志输出到控制台
    )
    Demands,LocationNum,SubTypeList,VeUnitPrice,VeTypeNum,VNums,VeCap,DMAT=LoadDelivData(date)

    SubTypeNum = len(SubTypeList)
    DemandsBoxs = np.zeros((LocationNum, SubTypeNum))
    for i in range(SubTypeNum):
      UnitPerBoxI = SubTypeList.loc[i, 'PACK_BOX_NUM']
      DemandsBoxs[:, i] = np.ceil(Demands.loc[:, i].values / UnitPerBoxI)

    DemandsBoxs = np.sum(DemandsBoxs, axis=1)  # 将 DemandsBoxs 按行求和
    MinDeliverNum=20 #最小装箱数
    logging.info("计算路径数")
    DMAT = DMAT.values
    DMAT= DMAT + DMAT.T
    PathInfo, _ = GetPathDis(DMAT, 2)
    PathInfo=pd.DataFrame(PathInfo)

    SaveDis = np.zeros(len(PathInfo))

    for i in range(len(PathInfo)):
        path = PathInfo.loc[i, 'Path']
        if len(path) != 1:
            SaveDis[i] = (PathInfo.loc[path[0]-1, 'PathDis'] + PathInfo.loc[path[1]-1, 'PathDis']) - PathInfo.loc[i, 'PathDis']

    #
    logging.info("对 SaveDis 排序并获取索引")
    sorted_indices= np.argsort(SaveDis, kind='stable') + 1
    sorted_indices = sorted_indices[len(DMAT)-1:]
    # 删除前 50% 的行
    k = int(np.ceil(0.4 * len(sorted_indices)))
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
            Price[PlanInd-1] = VeUnitPrice[j] * PathInfo.loc[LI-1,'PathDis']  # 计算价格
            PlanInd += 1
    VTypeTimes=VNums_All

    logging.info("路径箱数分配：决策变量是x_n,v,l 整数三维（目的地，车辆种类，路径序号）；I_v,l,0,1变量")
    logging.info("构建压缩路径‑箱数规划模型")

    # ===================== 1. 提取活性节点并过滤可用路径 =====================
    active_nodes = np.where(DemandsBoxs > 0)[0]          # 0‑based 索引
    active_set = set(active_nodes + 1)                  # 1‑based 集合，便于判断

    # 只保留首尾节点都在活性集合中的路径
    keep_idx = []
    for i in range(len(PathInfo)):
        p = PathInfo.loc[i, 'Path']
        if p[0] in active_set and p[-1] in active_set:
            keep_idx.append(i)
    PathInfo_active = PathInfo.iloc[keep_idx].reset_index(drop=True)

    # 保存原始路径编号（用于最后映射回原始计划）
    orig_path_indices = [PathInfo.index[i] + 1 for i in keep_idx]   # 原始 Ind（1‑based）

    N_active = len(active_nodes)           # 活性网点数
    PathNum_new = len(PathInfo_active)     # 可用路径数
    demands_active = DemandsBoxs[active_nodes]   # 活性网点剩余需求

    # 最小配送量映射：需求 < 20 的活性网点
    mindeliver_map = {}
    for pos, orig_idx in enumerate(active_nodes):
        if DemandsBoxs[orig_idx] < MinDeliverNum:
            mindeliver_map[pos] = DemandsBoxs[orig_idx]   # pos 为 0‑based（在 active_nodes 中的下标）

    # ===================== 2. 构建压缩模型 =====================
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

    # 目标函数：最小化 Σ cost_{v,l} * I_{v,l}
    prob += pulp.lpSum(
        VeUnitPrice[v - 1] * PathInfo_active.loc[l - 1, 'PathDis'] * I_vl[(v, l)]
        for v in range(1, VeTypeNum + 1) for l in range(1, PathNum_new + 1)
    )

    # 约束1：需求等式约束
    for n in range(1, N_active + 1):
        # 找出包含该活性节点的所有 (v, l) 组合
        terms = []
        orig_node = active_nodes[n - 1] + 1   # 原始 1‑based 节点号
        for l in range(1, PathNum_new + 1):
            if orig_node in PathInfo_active.loc[l - 1, 'Path']:
                for v in range(1, VeTypeNum + 1):
                    terms.append(x_nvl[(n, v, l)])
        prob += pulp.lpSum(terms) == demands_active[n - 1]

    # 约束2 & 3：容量约束 + 最小配送量约束（合并循环）
    for v in range(1, VeTypeNum + 1):
        for l in range(1, PathNum_new + 1):
            path = PathInfo_active.loc[l - 1, 'Path']   # 1‑based 原始节点列表
            # 收集该路径上所有活性节点对应的变量
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

            # 容量约束
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

    # 约束4：每种车型的使用次数上限
    for v in range(1, VeTypeNum + 1):
        prob += pulp.lpSum(I_vl[(v, l)] for l in range(1, PathNum_new + 1)) <= VTypeTimes[v - 1]

    # ===================== 3. 求解 =====================
    logging.info("开始求解压缩路径‑箱数规划")
    solver = pulp.PULP_CBC_CMD(
        msg=True,
        options=['ratioGap=0.01', 'sec=60']
    )
    prob.solve(solver)
    logging.info(f"求解完成，目标值：{pulp.value(prob.objective)}")

    # ===================== 4. 解映射，追加到计划数组 =====================
    selected = []
    for v in range(1, VeTypeNum + 1):
        for l in range(1, PathNum_new + 1):
            if I_vl[(v, l)].value() is not None and abs(I_vl[(v, l)].value() - 1) < 0.1:
                selected.append((v, l))

    for v, l in selected:
        orig_path_idx = orig_path_indices[l - 1]          # 原始 PathInfo 编号（1‑based）
        VeTypeI = v
        PathIndI = orig_path_idx

        # 路径节点（原始 1‑based）
        path_nodes = PathInfo.loc[orig_path_idx - 1, 'Path'].copy()  # 列表
        PlanPath[PlanInd - 1] = path_nodes
        Price[PlanInd - 1] = VeUnitPrice[v - 1] * PathInfo.loc[orig_path_idx - 1, 'PathDis']
        VeType[PlanInd - 1] = VeTypeI
        PathInd[PlanInd - 1] = PathIndI

        # 计算每个节点的配送量（活性节点取整，非活性节点为 0）
        deliv_vec = np.zeros(len(path_nodes))
        for j, node in enumerate(path_nodes):
            if (node - 1) in active_nodes:
                pos = np.where(active_nodes == node - 1)[0][0] + 1   # 新索引 1‑based
                deliv_vec[j] = round(x_nvl[(pos, v, l)].value())
            # 其他节点保持为 0
        DeNum[PlanInd - 1] = deliv_vec
        PlanInd += 1

    # ===================== 5. 生成最终计划 DataFrame（与原格式完全一致） =====================
    DeNum = DeNum[:PlanInd - 1]
    VeType = VeType[:PlanInd - 1]
    PathInd = PathInd[:PlanInd - 1]
    Price = Price[:PlanInd - 1]
    PlanPath = PlanPath[:PlanInd - 1]

    DelivPlan = {
        'PathInd': PathInd,
        'VeType': VeType,
        'Price': Price,
        'PlanPath': PlanPath,
        'DeNum': DeNum
    }
    DelivPlan = pd.DataFrame(DelivPlan)

    #增加配送地点编号
    site_info = query_adam_del_site_conf()
    site_info = site_info[site_info['STAT_NAME'] != '营销服务中心']
    Path_no = []
    for planpath in DelivPlan['PlanPath']:
        p = []
        for idx in planpath:
            p.append(site_info.loc[idx-1,'ORG_NO'])
        Path_no.append(p)
    DelivPlan['PathNo'] = Path_no
    DelivPlan = GenerateDelivPlan(DelivPlan,Demands,SubTypeList)
    
    return ExpandDeviceDetail(DelivPlan,SubTypeList)