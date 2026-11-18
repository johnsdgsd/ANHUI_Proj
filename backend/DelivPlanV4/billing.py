# -*- coding: utf-8 -*-
"""V4 计费里程规则。

与 Scheduling/router.py 的 billing_leg_distances 保持一致：
- 第 i>0 段真实里程 < gk_dist_mist 时，该段计费里程沿用第 i-1 段*计费*里程；
- 连续短腿链式传递；
- 首段（中心→首站）没有前一段，保持真实里程；
- gk_dist_mist <= 0 时关闭规则，原样返回。

只影响成本/MIP 目标/DIST_EXP；真实距离仍用于 750km 上限和里程展示。
"""


def billing_leg_distances(legs, gk_dist_mist):
    """把真实分段里程换算为计费分段里程。

    Args:
        legs: [中心→首站, 首站→次站, ...] 的真实里程列表
        gk_dist_mist: 阈值 km；<=0 表示关闭规则

    Returns:
        list[float]: 与 legs 等长的计费里程列表
    """
    if gk_dist_mist is None or float(gk_dist_mist) <= 0 or not legs:
        return list(legs)

    threshold = float(gk_dist_mist)
    billed = []
    for i, d in enumerate(legs):
        if i > 0 and d < threshold:
            billed.append(billed[-1])
        else:
            billed.append(d)
    return billed
