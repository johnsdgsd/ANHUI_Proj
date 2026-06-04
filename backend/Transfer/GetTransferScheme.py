"""
调拨方案生成模块
用于生成库存调拨方案，平衡各仓库库存水平
"""
import pandas as pd
import time
from datetime import datetime
from itertools import product
from backend.global_optimization.logger import logger
from backend.api.data_api.fetch_data import (
    query_adam_stock_count_sample_all,
    query_adam_del_site_conf,
    query_adam_spec_code_config,
    insert_into_adam_allot_day_plan_pre
)
from geopy.distance import geodesic
import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def GetTransferSchemeAndInsert():
    priority_dict = build_priority_dict_from_distance()
    tb = GetTransferScheme(priority_dict)
    scheme = format_transfer_plan_for_db(tb)
    res = insert_into_adam_allot_day_plan_pre(scheme)
    return res



def GetTransferScheme(priority_dict: dict) -> pd.DataFrame:
    """
    生成高库龄设备调拨计划，将高库龄设备调拨给最近的无高库龄单位。

    参数:
        priority_dict: 字典格式 {org_code: [priority_org_list]}，
                      表示对于每个调出单位，按顺序列出优先调入的单位列表（近到远）。
                      例如: {'ORG_A': ['ORG_B', 'ORG_C']} 表示从ORG_A调出时优先调给ORG_B，若无则ORG_C。

    返回:
        DataFrame 调拨计划，列:
            SRC_ORG_CODE   (调出单位)
            TGT_ORG_CODE   (调入单位)
            DEV_CODE_NO    (设备码)
            TRANSFER_NUM   (调拨数量)
            CREATE_TIME    (生成时间)
    """
    logger.info("开始生成高库龄设备调拨计划")

    # 1. 获取有效站点和设备码
    site_df = query_adam_del_site_conf()
    valid_orgs = set(site_df['ORG_NO'].unique())
    logger.info(f"有效站点数量: {len(valid_orgs)}")

    spec_df = query_adam_spec_code_config()
    valid_devices = set(spec_df['DEV_CODE'].unique())
    logger.info(f"有效设备码数量: {len(valid_devices)}")

    # 2. 获取库存数据，筛选新表、高库龄>0、有效站点和设备码
    stock_df = query_adam_stock_count_sample_all()  #TODO 这里可能也是需要按照单位层级汇总的--不用汇总了
    logger.info(f"原始库存记录数: {len(stock_df)}")

    high_stock = stock_df[
        (stock_df['OLD_NEW_FLAG'] == '01') &
        (stock_df['HIGH_NUM'] > 0) &
        (stock_df['MGT_ORG_CODE'].isin(valid_orgs)) &
        (stock_df['DEV_CODE_NO'].isin(valid_devices))
    ].copy()
    high_stock = high_stock[['MGT_ORG_CODE', 'DEV_CODE_NO', 'HIGH_NUM', 'STOCK_NUM']]
    high_stock.rename(columns={'MGT_ORG_CODE': 'ORG_NO', 'HIGH_NUM': 'HIGH_QTY', 'STOCK_NUM': 'SRC_STOCK_NUM'}, inplace=True)
    # 检查是否存在同单位同设备多条记录
    dup_mask = high_stock.duplicated(subset=['ORG_NO', 'DEV_CODE_NO'], keep=False)
    dup_count = dup_mask.sum()
    if dup_count > 0:
        logger.warning(f"发现 {dup_count} 条重复(单位,设备码)记录，明细:\n{high_stock[dup_mask].to_string()}")
    # 检查原始数据：高库龄 > 库存的异常记录
    abnormal = high_stock[high_stock['HIGH_QTY'] > high_stock['SRC_STOCK_NUM']]
    if not abnormal.empty:
        logger.warning(f"发现 {len(abnormal)} 条 HIGH_NUM > STOCK_NUM 的异常记录:\n{abnormal.to_string()}")
    # 按单位-设备码汇总，确保 STOCK >= HIGH
    high_stock = high_stock.groupby(['ORG_NO', 'DEV_CODE_NO'], as_index=False).sum()

    # 构建新表库存字典（调拨仅针对新表）：按 (ORG_NO, DEV_CODE_NO) 汇总 STOCK_NUM
    stock_new = stock_df[stock_df['OLD_NEW_FLAG'] == '01']
    stock_lookup = stock_new.groupby(['MGT_ORG_CODE', 'DEV_CODE_NO'])['STOCK_NUM'].sum().to_dict()

    if high_stock.empty:
        logger.info("未发现需要调拨的高库龄设备")
        return pd.DataFrame(columns=['SRC_ORG_CODE', 'TGT_ORG_CODE', 'DEV_CODE_NO',
                                        'TRANSFER_NUM', 'CREATE_TIME',
                                        'SEND_STOCK_NUM', 'REC_STOCK_NUM', 'GLOBAL_SCHEME_ID'])

    logger.info(f"筛选后高库龄记录数: {len(high_stock)}")
    src_orgs = high_stock['ORG_NO'].nunique()
    src_devices = high_stock['DEV_CODE_NO'].nunique()
    logger.info(f"涉及调出单位数: {src_orgs}, 涉及设备码数: {src_devices}")
    logger.info(f"高库龄总数量: {high_stock['HIGH_QTY'].sum()}")

    # 3. 构建全量地市×设备码，标记是否有高库龄
    logger.info("构建全量组合 (地市×设备码)")
    all_combinations = pd.DataFrame(
        product(valid_orgs, valid_devices),
        columns=['ORG_NO', 'DEV_CODE_NO']
    )
    all_combinations = all_combinations.merge(
        high_stock[['ORG_NO', 'DEV_CODE_NO', 'HIGH_QTY', 'SRC_STOCK_NUM']],
        on=['ORG_NO', 'DEV_CODE_NO'],
        how='left'
    )
    all_combinations['HAS_HIGH'] = all_combinations['HIGH_QTY'].notna() & (all_combinations['HIGH_QTY'] > 0)
    all_combinations['HIGH_QTY'] = all_combinations['HIGH_QTY'].fillna(0)

    # 4. 按设备码生成调拨计划
    global_scheme_id = int(time.time())
    transfer_records = []
    device_groups = list(all_combinations.groupby('DEV_CODE_NO'))
    total_devices = len(device_groups)
    logger.info(f"开始处理 {total_devices} 个设备码的调拨")

    for idx, (dev_code, group) in enumerate(device_groups, 1):
        # 调出方：有高库龄的单位及其数量
        src_rows = group[group['HAS_HIGH']][['ORG_NO', 'HIGH_QTY', 'SRC_STOCK_NUM']].to_dict('records')
        # 调入方：无高库龄的单位列表
        tgt_orgs = group[~group['HAS_HIGH']]['ORG_NO'].tolist()

        if not src_rows or not tgt_orgs:
            if idx % 100 == 0:
                logger.debug(f"设备 {dev_code} 无调出或无可调入单位，跳过")
            continue

        # 对于每个调出单位，根据优先级字典选择调入单位
        for src in src_rows:
            src_org = src['ORG_NO']
            qty = src['HIGH_QTY']
            target_org = None
            if priority_dict and src_org in priority_dict:
                for candidate in priority_dict[src_org]:
                    if candidate in tgt_orgs:
                        target_org = candidate
                        break
            if target_org is None and tgt_orgs:
                target_org = tgt_orgs[0]   # 降级选择第一个
            if target_org is None:
                logger.warning(f"设备 {dev_code} 从 {src_org} 调拨无法找到合适的调入单位")
                continue

            transfer_records.append({
                'SRC_ORG_CODE': src_org,
                'TGT_ORG_CODE': target_org,
                'DEV_CODE_NO': dev_code,
                'TRANSFER_NUM': qty,
                'CREATE_TIME': datetime.now(),
                'SEND_STOCK_NUM': int(src['SRC_STOCK_NUM']) - qty,
                'REC_STOCK_NUM': int(stock_lookup.get((target_org, dev_code), 0)) + qty,
                'GLOBAL_SCHEME_ID': global_scheme_id
            })

        if idx % 20 == 0 or idx == total_devices:
            logger.info(f"已处理 {idx}/{total_devices} 个设备码，当前生成调拨记录 {len(transfer_records)} 条")

    if not transfer_records:
        logger.info("未生成有效调拨计划（可能所有设备码均无可调入单位）")
        return pd.DataFrame(columns=['SRC_ORG_CODE', 'TGT_ORG_CODE', 'DEV_CODE_NO',
                                        'TRANSFER_NUM', 'CREATE_TIME',
                                        'SEND_STOCK_NUM', 'REC_STOCK_NUM', 'GLOBAL_SCHEME_ID'])

    transfer_df = pd.DataFrame(transfer_records)
    neg_mask = transfer_df['SEND_STOCK_NUM'] < 0
    if neg_mask.any():
        logger.warning(f"发现 {neg_mask.sum()} 条 SEND_STOCK_NUM 为负的记录:\n{transfer_df[neg_mask].to_string()}")
    total_qty = transfer_df['TRANSFER_NUM'].sum()
    distinct_src = transfer_df['SRC_ORG_CODE'].nunique()
    distinct_tgt = transfer_df['TGT_ORG_CODE'].nunique()
    logger.info(f"调拨计划生成完成: 共 {len(transfer_df)} 条记录, 总调拨数量 {total_qty}")
    logger.info(f"涉及调出单位 {distinct_src} 个, 调入单位 {distinct_tgt} 个")
    return transfer_df


def build_priority_dict_from_distance() -> dict:
    """
    从站点配置表读取经纬度，计算每个站点到其他站点的距离，
    排除营销服务中心，返回优先级字典：{org_code: [其他org_code按距离由近到远排序]}
    """
    logger.info("开始构建调拨优先级字典（基于距离）")

    # 1. 获取站点配置
    site_df = query_adam_del_site_conf()
    logger.info(f"原始站点数量: {len(site_df)}")

    # 2. 过滤掉营销服务中心（假设表中 STAT_NAME 列为 '营销服务中心'）
    if 'STAT_NAME' in site_df.columns:
        before = len(site_df)
        site_df = site_df[site_df['STAT_NAME'] != '营销服务中心'].copy()
        after = len(site_df)
        logger.info(f"已排除营销服务中心，站点数量: {before} -> {after}")
    else:
        logger.warning("站点表缺少 STAT_NAME 列，无法排除营销服务中心，将使用全部站点")

    # 3. 过滤经纬度为空的行
    site_df = site_df.dropna(subset=['LONGITUDE', 'LATITUDE']).copy()
    logger.info(f"有效经纬度站点数量: {len(site_df)}")

    if site_df.empty:
        raise ValueError("无可用的站点经纬度数据")

    # 4. 构建坐标字典
    orgs = site_df['ORG_NO'].tolist()
    coord_dict = {row['ORG_NO']: (row['LATITUDE'], row['LONGITUDE']) for _, row in site_df.iterrows()}

    # 5. 计算距离并生成优先级列表
    priority_dict = {}
    total = len(orgs)
    for i, src_org in enumerate(orgs):
        src_coord = coord_dict[src_org]
        distances = []
        for tgt_org in orgs:
            if tgt_org == src_org:
                continue
            tgt_coord = coord_dict[tgt_org]
            dist = geodesic(src_coord, tgt_coord).km
            distances.append((tgt_org, dist))
        distances.sort(key=lambda x: x[1])  # 按距离升序
        priority_dict[src_org] = [org for org, _ in distances]
        if (i + 1) % 10 == 0 or i + 1 == total:
            logger.info(f"已计算 {i + 1}/{total} 个站点的优先级列表")

    logger.info(f"优先级字典构建完成，共 {len(priority_dict)} 个站点")
    return priority_dict


def format_transfer_plan_for_db(transfer_df: pd.DataFrame) -> pd.DataFrame:
    """
    将 GetTransferScheme 生成的调拨计划转换为目标表 ADAM_ALLOT_DAY_PLAN_PRE 的结构。

    参数:
        transfer_df: 包含列 SRC_ORG_CODE, TGT_ORG_CODE, DEV_CODE_NO, TRANSFER_NUM, CREATE_TIME
        global_scheme_id: 可选，全局方案标识

    返回:
        符合目标表结构的 DataFrame
    """
    from backend.api.data_api.fetch_data import query_adam_spec_code_config

    if transfer_df.empty:
        return pd.DataFrame(columns=[
            'ALLOT_DAY_PLAN_PRE_ID', 'ALLOT_DATE', 'SEND_ORG_NO', 'REC_ORG_NO',
            'DEV_CLS', 'DEV_CATEG', 'DEV_CODE', 'SEND_NUM', 'SEND_STOCK_NUM',
            'REC_STOCK_NUM', 'GLOBAL_SCHEME_ID', 'SEND_REASON'
        ])

    # 1. 获取设备码映射字典
    spec_df = query_adam_spec_code_config()
    # 假设 spec_df 包含 DEV_CODE, DEV_CLS, DEV_CATEG 三列，且无重复
    dev_cls_dict = spec_df.set_index('DEV_CODE')['DEV_CLS'].to_dict()
    dev_categ_dict = spec_df.set_index('DEV_CODE')['DEV_CATEG'].to_dict()

    # 2. 添加映射列
    transfer_df = transfer_df.copy()
    transfer_df['DEV_CLS'] = transfer_df['DEV_CODE_NO'].map(dev_cls_dict)
    transfer_df['DEV_CATEG'] = transfer_df['DEV_CODE_NO'].map(dev_categ_dict)

    # 3. 处理缺失映射（填充默认值并记录警告）
    missing_cls = transfer_df['DEV_CLS'].isna()
    if missing_cls.any():
        logger.warning(f"有 {missing_cls.sum()} 条记录未找到设备码映射，将填充默认分类和类别")
        transfer_df['DEV_CLS'] = transfer_df['DEV_CLS'].fillna('00')
        transfer_df['DEV_CATEG'] = transfer_df['DEV_CATEG'].fillna('00')

    # 4. 生成主键：秒级时间戳 + 行内序号
    base_ts = int(datetime.now().timestamp())  # 秒级时间戳
    transfer_df = transfer_df.reset_index(drop=True)
    transfer_df['ALLOT_DAY_PLAN_PRE_ID'] = [base_ts + i for i in range(len(transfer_df))]

    # 5. 添加其他字段
    transfer_df['ALLOT_DATE'] = transfer_df['CREATE_TIME'].dt.strftime('%Y-%m-%d').astype(str)
    transfer_df['SEND_ORG_NO'] = transfer_df['SRC_ORG_CODE']
    transfer_df['REC_ORG_NO'] = transfer_df['TGT_ORG_CODE']
    transfer_df['DEV_CODE'] = transfer_df['DEV_CODE_NO']
    transfer_df['SEND_NUM'] = transfer_df['TRANSFER_NUM']
    transfer_df['GLOBAL_SCHEME_ID'] = transfer_df['GLOBAL_SCHEME_ID'].astype(int)
    transfer_df['SEND_REASON'] = '高库龄'

    # 6. 选择目标列并排序
    result = transfer_df[[
        'ALLOT_DAY_PLAN_PRE_ID', 'ALLOT_DATE', 'SEND_ORG_NO', 'REC_ORG_NO',
        'DEV_CLS', 'DEV_CATEG', 'DEV_CODE', 'SEND_NUM', 'SEND_STOCK_NUM',
        'REC_STOCK_NUM', 'GLOBAL_SCHEME_ID', 'SEND_REASON'
    ]]
    return result

