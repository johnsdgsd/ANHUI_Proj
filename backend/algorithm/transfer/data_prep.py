"""
调拨数据准备 — 单位列表 + 距离矩阵构建

口径（对齐《调拨算法设计方案最新版.md》:
    调拨单位 = 站点配置(ADAM_DEL_SITE_CONF) 中排除营销服务中心的站点，当前 87 家
    距离矩阵 = ADAM_DIST_MIST 实际运输距离（gk-adam-query_distance_matrix），
              全连接对称；缺失方向用反向距离补全

参考: DelivPlanV4（inventory_optimization/SchedulingDeliveryAdapter._v3_load_deliv_data）
      的距离矩阵构建逻辑 —— 内联 HTTP 请求，不新增 fetch_data 查询函数。
"""
import logging

import numpy as np
import pandas as pd
import requests

from backend.config.config import API_CONFIG
from backend.api.data_api.fetch_data import query_adam_del_site_conf

logger = logging.getLogger(__name__)


def load_units():
    """加载调拨单位列表（站点配置中排除营销服务中心）。

    Returns:
        pd.DataFrame: ORG_NO, STAT_NAME 等，按 ORG_NO 排序
    """
    tb = query_adam_del_site_conf()
    marketing = tb[tb['STAT_NAME'] == '营销服务中心']
    if not marketing.empty:
        logger.info(f"调拨单位: 排除营销服务中心 {marketing['ORG_NO'].tolist()}")
    units = (tb[tb['STAT_NAME'] != '营销服务中心']
             .sort_values('ORG_NO').reset_index(drop=True).copy())
    logger.info(f"调拨单位: {len(units)} 家 (共 {len(tb)} 个站点)")
    return units


def _build_cost_matrix(org_ids, dist_df):
    """纯逻辑：由距离表 DataFrame 构建 (n×n) 距离矩阵。

    Args:
        org_ids: 调拨单位编码列表 (n,)
        dist_df: 距离表，列 DIST_ORG_NO / RECEIVE_ORG_NO / DIST_MIST

    Returns:
        np.ndarray (n,n): cost[i,j] = org_ids[i] → org_ids[j] 距离(km)
    """
    org_ids = [str(o).strip() for o in org_ids]
    n = len(org_ids)
    idx = {o: i for i, o in enumerate(org_ids)}
    cost = np.zeros((n, n))

    df = dist_df.copy()
    df.columns = [c.upper() for c in df.columns]

    matched = 0
    unmatched = []
    for _, r in df.iterrows():
        fi = idx.get(str(r['DIST_ORG_NO']).strip())
        ti = idx.get(str(r['RECEIVE_ORG_NO']).strip())
        d = float(r['DIST_MIST'])
        if fi is not None and ti is not None and d > 0:
            cost[fi, ti] = d
            matched += 1
        elif d > 0:
            if fi is None:
                unmatched.append(str(r['DIST_ORG_NO']))
            if ti is None:
                unmatched.append(str(r['RECEIVE_ORG_NO']))
    logger.info(f"调拨距离矩阵: 距离表 {len(df)} 行, 匹配 {matched} 对, 单位 {n}")
    if unmatched:
        logger.warning(f"距离表中未匹配的 ORG_NO: {sorted(set(unmatched))[:10]}")

    # 对称补缺: 反向有数据而正向缺失时用反向距离填充（与 DelivPlanV4 一致）
    mask_zero = (cost == 0)
    mask_rev_has = (cost.T > 0)
    cost[mask_zero & mask_rev_has] = cost.T[mask_zero & mask_rev_has]

    return cost


def build_distance_matrix(org_ids):
    """构建调拨单位间距离矩阵（内联请求 ADAM_DIST_MIST）。

    Args:
        org_ids: 调拨单位编码列表

    Returns:
        np.ndarray (n,n) 距离矩阵(km)
    """
    url = (f"http://{API_CONFIG['database']['host']}:"
           f"{API_CONFIG['database']['port']}/exec/gk-adam-query_distance_matrix")
    logger.info(f"调拨距离矩阵: 请求 {url} ...")
    resp = requests.post(url, json={}, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if not data:
        raise ValueError("距离矩阵(ADAM_DIST_MIST)返回数据为空")
    df = pd.DataFrame(data) if isinstance(data, list) else pd.DataFrame([data])
    return _build_cost_matrix(org_ids, df)


def prepare_transfer_network():
    """调拨数据准备主入口。

    Returns:
        dict:
            org_ids:  87 家调拨单位编码
            org_names: 单位名称（STAT_NAME）
            cost:     (87,87) 距离矩阵(km)
            stats:    构建统计
    """
    units = load_units()
    org_ids = [str(o).strip() for o in units['ORG_NO']]
    org_names = [str(x) for x in units['STAT_NAME']]
    cost = build_distance_matrix(org_ids)

    # ---- 质量检查: 不可达(NaN/Inf/非对角0)必须报错（完整性校验, 用户确认口径） ----
    n = len(org_ids)
    off_diag = cost[~np.eye(n, dtype=bool)]
    if np.isnan(cost).any() or np.isinf(cost).any():
        bad = int(np.isnan(cost).sum() + np.isinf(cost).sum())
        raise ValueError(f"距离矩阵含 NaN/Inf（不可达对），共 {bad} 个，数据准备失败")
    zeros_off = int((off_diag <= 0.001).sum())
    total_off = int(off_diag.size)
    if zeros_off > 0:
        mask = ~np.eye(n, dtype=bool) & (cost <= 0.001)
        pairs = [(org_ids[i], org_ids[j]) for i, j in np.argwhere(mask)[:10]]
        raise ValueError(
            f"距离矩阵存在 {zeros_off}/{total_off} 个非对角 0（不可达对），"
            f"示例: {pairs}，数据完整性校验失败，禁止进入求解")

    stats = {
        'n_units': n,
        'n_pairs': total_off,
        'zero_off_diag': zeros_off,
        'reachable_ratio': round(1 - zeros_off / total_off, 4) if total_off else 1.0,
    }
    logger.info(f"调拨数据准备完成: {n} 家单位, 非对角 {total_off} 对, "
                f"缺失 {zeros_off}, 可达率 {stats['reachable_ratio']:.1%}")
    return {
        'org_ids': org_ids,
        'org_names': org_names,
        'cost': cost,
        'stats': stats,
    }


if __name__ == '__main__':
    # 独立运行: 打印调拨网络概览（依赖后端 API 运行）
    import io
    import sys
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s', stream=sys.stdout)
    net = prepare_transfer_network()
    print(f"\n单位数: {len(net['org_ids'])}")
    print(f"可达率: {net['stats']['reachable_ratio']:.1%} (缺失 {net['stats']['zero_off_diag']} 对)")
    print(f"成本矩阵 shape: {net['cost'].shape}")
    print(f"前 5 家单位: {list(zip(net['org_ids'][:5], net['org_names'][:5]))}")
