"""
调拨场景二 — 【独立】调出单位 + 调出数量判定模块

输入: 库存快照(stock_df) + 库存上限(upper_map)
输出: 供应点可调出量 {(org, dev): surplus} + 高库龄优先级集合

口径（用户确认 2026-08-07）:
    ① 高库龄非必要条件，调出单位该设备码库存高于库存上限即可；
    ② 可调出量 = STOCK_NUM − 库存上限（保证调出后仍满足两周服务水平）；
    ③ 高库龄(HIGH_NUM>0) 仅作供应点排序优先级，不作为资格限制。

本模块与缺货判定/紧急补库/调拨完全解耦：只回答「谁可以调出、调出多少」。
更换库存上限口径（如改读 ADAM_STOCK_MONTH_LIMIT_PRE.BASE_LIMIT）只需替换 common._compute_upper_limit。
"""
import logging

logger = logging.getLogger(__name__)


def build_supply_map(stock_df, upper_map, org2i):
    """供应点可调出量判定。

    Args:
        stock_df: 库存快照（列 MGT_ORG_CODE / DEV_CODE_NO / STOCK_NUM / HIGH_NUM）
        upper_map: {(org, dev): Upper}，由 common._compute_upper_limit 提供
        org2i: 调拨网络单位编码 → 索引（限制在 87 家内）

    Returns:
        dict: {(org, dev): surplus = STOCK_NUM − Upper}，仅保留 surplus > 0
    """
    supply = {}
    for _, r in stock_df.iterrows():
        org = str(r['MGT_ORG_CODE']).strip()
        if org not in org2i:
            continue
        dev = r['DEV_CODE_NO']
        try:
            stock_num = float(r['STOCK_NUM'] or 0.0)
        except (TypeError, ValueError):
            continue
        upper = upper_map.get((org, dev), 0.0)
        # 向下取整: 保证调出后库存 ≥ 上限（STOCK 整数, upper 浮点, 取整避免调出过量）
        surplus = int(stock_num - upper)
        if surplus > 0:
            supply[(org, dev)] = surplus
        elif surplus <= 0:
            logger.debug(
                f"调出判定 {org}/{dev}: STOCK={stock_num:.0f} ≤ 上限={upper:.0f}, 不可调出")
    n_orgs = len({o for o, _ in supply})
    total = sum(supply.values())
    logger.info(f"调出判定: {len(supply)} 个供应点, {n_orgs} 家单位, Σ可调出 {total:.0f}")
    return supply


def high_stock_orgs(stock_df):
    """高库龄(HIGH_NUM>0) 的单位集合（贪心排序优先级键用）。

    Returns:
        set: 有高库龄资产的单位编码集合
    """
    high = set()
    for _, r in stock_df.iterrows():
        try:
            if float(r['HIGH_NUM'] or 0.0) > 0:
                high.add(str(r['MGT_ORG_CODE']).strip())
        except (TypeError, ValueError):
            continue
    return high
