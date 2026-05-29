"""
路径距离计算工具模块
"""

import numpy as np
from itertools import combinations, permutations
from geopy.distance import geodesic

def GetPathDis(DMat, MaxLen):
    LocationNum = DMat.shape[0] - 1  # DMat 是一个 NumPy 数组
    PathNum = 0
    PathSet = [None] * MaxLen
    for i in range(1, MaxLen + 1):
        PathSet[i - 1] = np.array(list(combinations(range(1, LocationNum + 1), i)))  # 使用 combinations
        PathNum += PathSet[i - 1].shape[0]

    PathInds = np.arange(1, PathNum + 1)
    Paths = [None] * PathNum
    PathDis = np.zeros(PathNum)

    CurInd = 0

    # 处理 PathSet[1]
    for i in range(PathSet[0].shape[0]):
        Paths[CurInd] = PathSet[0][i]
        PathDis[CurInd] = 2 * DMat[0, PathSet[0][i, 0]]
        CurInd += 1

    # 处理 PathSet[2]
    for i in range(PathSet[1].shape[0]):
        Paths[CurInd] = PathSet[1][i]
        PathDis[CurInd] = DMat[0, PathSet[1][i, 0]] + DMat[0, PathSet[1][i, 1]] + DMat[
            PathSet[1][i, 0], PathSet[1][i, 1]]
        CurInd += 1

    # 处理 PathSet[3] 到 PathSet[MaxLen]
    for i in range(2, MaxLen):
        PathSetI = PathSet[i]
        for j in range(PathSetI.shape[0]):
            PathIJ = PathSetI[j]
            PathOrders = np.array(list(combinations(PathIJ, i+1)))
            PermNumIJ = PathOrders.shape[0]
            DisIJs = np.zeros(PermNumIJ)

            for u in range(PermNumIJ):  # 计算不同排列的距离
                DisIJs[u] = DMat[0, PathOrders[u, 0]] + DMat[0, PathOrders[u, -1]]
                for v in range(i - 1):
                    DisIJs[u] += DMat[PathOrders[u, v], PathOrders[u, v + 1]]

            # 获取最小的距离和对应的路径
            PathDis[CurInd] = np.min(DisIJs)
            IC = np.argmin(DisIJs)
            Paths[CurInd] = PathOrders[IC]
            CurInd += 1

    # 创建 PathInfo 字典
    PathInfo = {
        'Ind': PathInds,
        'Path': Paths,
        'PathDis': PathDis
    }

    return PathInfo, PathNum



def GetCenterToLocalDis() -> dict:
    """
    计算营销服务中心到各地市站点的距离（公里），并乘以 1.15 系数。
    返回字典: {ORG_NO: distance_km}
    """
    from backend.api.data_api.fetch_data import query_adam_del_site_conf
    # 查询所有配送站点配置（假设表中有 ORG_NO, LONGITUDE, LATITUDE, STAT_NAME）
    tb1 = query_adam_del_site_conf()

    # 获取营销服务中心（省中心）的经纬度
    marketing_center = tb1[tb1['STAT_NAME'] == '营销服务中心']
    if marketing_center.empty:
        raise ValueError("未找到营销服务中心站点")
    center_lon = marketing_center.iloc[0]['LONGITUDE']
    center_lat = marketing_center.iloc[0]['LATITUDE']

    # 筛选其他站点（地市）
    other_sites = tb1[tb1['STAT_NAME'] != '营销服务中心'].copy()

    distances = {}
    for _, row in other_sites.iterrows():
        org_no = row['ORG_NO']
        lon = row['LONGITUDE']
        lat = row['LATITUDE']
        # 计算球面距离（公里）
        dist = geodesic((center_lat, center_lon), (lat, lon)).km
        # 应用系数 1.15（如原代码所示）
        distances[org_no] = dist * 1.15

    return distances
