"""
路径距离计算工具模块
"""

import numpy as np
from itertools import combinations, permutations


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
