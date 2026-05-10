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
        "VeUnitPrice":[0.7,0.7,0.7]
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
    DeNum = [None] * 1000  # 存放每次配送的数量
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


    for i in range(LocationNum):
        LI = LocationInds[i]  # 获取当前排序的路径索引
        while DemandsBoxs[LI-1] >= VeCap[1] + MinDeliverNum:  # 确定是否可以继续配送
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
    XNum1 = LocationNum * VeTypeNum * PathNum
    XNum2 = VeTypeNum * PathNum
    prob = pulp.LpProblem("deleivplan", pulp.LpMinimize)
    x = pulp.LpVariable.dicts("x", range(1, XNum1+XNum2 +1),  cat='Integer')
    lb = np.zeros(XNum1+XNum2)
    ub = np.ones(XNum1+XNum2)

    '''变量取值范围'''
    for i in range(1, LocationNum + 1):
        ub[(i - 1) * XNum2 : i * XNum2 ] = DemandsBoxs[i - 1]
    for i in range(1, XNum1+XNum2 +1):
        x[i].lowBound = lb[i - 1]
        x[i].upBound = ub[i - 1]


    '''目标函数'''
    c_vl = np.zeros(XNum2)
    for l in range(1, PathNum + 1):
        for v in range(1, VeTypeNum + 1):
            Ind = (v - 1) * PathNum+l-1
            c_vl[Ind] = VeUnitPrice[v - 1] * PathInfo.loc[l - 1,'PathDis']

    f = np.zeros(XNum1 + XNum2)
    f[XNum1:] = c_vl
    prob += pulp.lpSum(f[i - 1] * x[i] for i in range(1, XNum1 + XNum2 + 1))



    '''去掉前面一部分为0的变量'''
    xInds = np.zeros((XNum1, 1), dtype=bool)
    for l in range(PathNum):
        nL = PathInfo.loc[l,'Path']  # 假设 PathInfo 是一个包含 Path 属性的对象列表
        for i in range(len(nL)):
            nI = nL[i]  # nI 包含在路径中
            Ind1 = (nI - 1) * XNum2
            Ind2s = l
            Ind3s=PathNum + l
            xInds[Ind1 + Ind2s] = True
            xInds[Ind1 + Ind3s] = True
    xInds= np.where(xInds)[0]+1
    xInds = np.append(xInds, np.arange(XNum1+1, XNum1+XNum2+1))


    logging.info("等式约束，保证各地配送数量等于需求数量")
    Aeq = np.zeros((LocationNum, XNum1 + XNum2))
    for l in range(1, PathNum + 1):
        nL = PathInfo.loc[l - 1, 'Path']  # 获取路径 l 所经过的位置
        for i in range(len(nL)):  # 遍历路径中的每个位置
            nI = nL[i]  # 获取路径中的位置 nI
            Ind1 = (nI - 1) * XNum2
            Ind2s = [(v - 1) * PathNum + l - 1 for v in range(1, VeTypeNum + 1)]
            for Ind2 in Ind2s:
                Aeq[nI - 1, Ind1 + Ind2] = 1


    for i in range(1, LocationNum + 1):
        #prob += pulp.lpSum(Aeq[i - 1, j-1] * x[j] for j in range(1, XNum1 + XNum2 + 1)) ==DemandsBoxs[i - 1]
        prob += pulp.lpSum(Aeq[i - 1, j - 1] * x[j] for j in xInds) == DemandsBoxs[i - 1]
    '''不等式约束-车次和容量约束，每次配送不得超过车辆容量，总配送车次不能超过车次上限'''

    A1 = np.zeros((XNum2 + VeTypeNum, XNum1 + XNum2))
    b1 = np.zeros(XNum2 + VeTypeNum)


    for l in range(1, PathNum + 1):
        nL = PathInfo.loc[l - 1, 'Path']
        for v in range(1, VeTypeNum + 1):
            RowInd = (v - 1) * PathNum + l - 1
            Pv = VeCap[v - 1]  # 获取车辆容量
            A1[RowInd, XNum1 + RowInd] = -Pv

            # 遍历路径中的每个位置
            for i in range(len(nL)):
                n = nL[i]
                A1[RowInd, (n - 1) * XNum2 + RowInd] = 1

    # 增加车次约束
    b1[XNum2:] = VTypeTimes

    for v in range(1, VeTypeNum + 1):
        A1[XNum2 + v - 1, XNum1 + (v - 1) * PathNum:XNum1 + v * PathNum] = 1


    for i in range(1, XNum2 + VeTypeNum+1):
       #prob += pulp.lpSum(A1[i-1, j-1] * x[j] for j in range(1, XNum1 + XNum2 + 1))<= b1[i-1]
       prob += pulp.lpSum(A1[i - 1, j - 1] * x[j] for j in xInds) <= b1[i - 1]

    logging.info("最小配送数量约束，数值最小配送数量为20，如果某地本身的需求数量小于20，那么该地最小配送数量设置为该地的需求数量")
    mindeliverind = np.where(DemandsBoxs < MinDeliverNum)[0]
    mindeliver = DemandsBoxs[mindeliverind]
    A2 = np.zeros((XNum2, XNum1 + XNum2))
    b2 = np.zeros(XNum2)

    for l in range(1,PathNum+1):
        nL = PathInfo.loc[l - 1, 'Path']
        for v in range(1,VeTypeNum+1):
            RowInd = (v - 1) * PathNum + l
            n = nL[0]
            if n-1 in mindeliverind:
                A2[RowInd-1, XNum1 + RowInd-1] = DemandsBoxs[n-1]
            else:
                A2[RowInd-1, XNum1 + RowInd-1] = MinDeliverNum
            A2[RowInd-1, (n - 1) * XNum2 + RowInd-1] = -1
    for i in range(1, XNum2+1):
      # prob += pulp.lpSum(A2[i-1, j-1] * x[j] for j in range(1, XNum1 + XNum2 + 1))<= b2[i-1]
       prob += pulp.lpSum(A2[i - 1, j - 1] * x[j] for j in xInds) <= b2[i - 1]


    A3 = np.zeros((XNum2, XNum1 + XNum2))
    b3 = np.zeros(XNum2)

    for l in range(1,PathNum+1):
        nL = PathInfo.loc[l - 1, 'Path']
        for v in range(1,VeTypeNum+1):
            RowInd = (v - 1) * PathNum + l
            n = nL[-1]
            if n-1 in mindeliverind:
                A3[RowInd-1, XNum1 + RowInd-1] = DemandsBoxs[n-1]
            else:
                A3[RowInd-1, XNum1 + RowInd-1] = MinDeliverNum
            A3[RowInd-1, (n - 1) * XNum2 + RowInd-1] = -1
    for i in range(1, XNum2+1):
       #prob += pulp.lpSum(A3[i-1, j-1] * x[j] for j in range(1, XNum1 + XNum2 + 1))<= b3[i-1]
       prob += pulp.lpSum(A3[i-1, j-1] * x[j] for j in xInds)<= b3[i-1]


    logging.info("开始求解路径-箱数规划")
    solver = pulp.PULP_CBC_CMD(
            msg=True,
            options=['ratioGap=0.01', 'sec=60']  # 1% 的相对差距和60秒的时间限制
        )
    prob.solve(solver)
    logging.info(f"求解路径-箱数规划完成,目标函数值为:{pulp.value(prob.objective)}")
    
    x_values = {i: (x[i].value() if x[i].value() is not None else 0) for i in range(1, XNum1 + XNum2 + 1)}

    '''对所求x进行解析，生成路径-箱数配送计划'''
    x= np.array(list(x_values.values()))

    vlInds = np.where(np.abs(x[XNum1:] - 1) < 0.1)[0] #从0开始索引的找出Ivl位置
    vlInds =vlInds +1
    for i in vlInds:
        VeTypeI = (i // PathNum) + 1
        PathIndI = (i % PathNum) if (i % PathNum) != 0 else PathNum
        PlanPath[PlanInd-1]=PathInfo.loc[PathIndI - 1, 'Path']  # PathInfo 是 pandas DataFrame，获取路径
        Price[PlanInd-1]=VeUnitPrice[VeTypeI - 1] * PathInfo.loc[PathIndI - 1, 'PathDis']
        VeType[PlanInd-1]=VeTypeI
        PathInd[PlanInd-1]=PathIndI
        DeNum[PlanInd-1]=np.zeros(len(PlanPath[PlanInd-1]))
        for j in range(len(PlanPath[PlanInd-1])):
            nIndJ = PlanPath[PlanInd-1][j]
            DeNum[PlanInd-1][j] = round(x[(nIndJ - 1) * XNum2 + i-1])
        PlanInd += 1

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

    DelivPlan= pd.DataFrame(DelivPlan)
    return DelivPlan