from numbers import Number
import logging
import requests
import pandas as pd
from backend.config.config import API_CONFIG

session = requests.Session()


def query_device_install_data_by_month_range(start_month:int,end_month: int):
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-query-aps-device-install-by-month-range'
        url = f"http://{host}:{port}{endpoint}"
        json = {
            "start_month":start_month,
            "end_month":end_month
        }
        response = session.post(url, json=json)
        response.raise_for_status()
        
        data = response.json()
        
        if isinstance(data, list) and len(data) == 0:
            raise ValueError("返回数据为空")
        
        if isinstance(data, list):
            df = pd.DataFrame(data)
        else:
            df = pd.DataFrame([data])
        
        return df
    except requests.exceptions.RequestException as e:
        raise
    except Exception as e:
        raise

def query_aps_inventory_item_cost():
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-query-aps-inventory-item-cost'
        url = f"http://{host}:{port}{endpoint}"
        
        response = session.post(url, json={})
        response.raise_for_status()
        
        data = response.json()
        
        if isinstance(data, list) and len(data) == 0:
            raise ValueError("返回数据为空")
        
        if isinstance(data, list):
            df = pd.DataFrame(data)
        else:
            df = pd.DataFrame([data])
        
        return df
    except requests.exceptions.RequestException as e:
        raise
    except Exception as e:
        raise

def query_aps_inventory_init_stock_by_month(month: int):
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-query-aps-inventory-init-stock-by-month'
        url = f"http://{host}:{port}{endpoint}"
        json = {
            "month": month
        }
        response = session.post(url, json=json)
        response.raise_for_status()
        
        data = response.json()
        
        if isinstance(data, list) and len(data) == 0:
            raise ValueError("返回数据为空")
        
        if isinstance(data, list):
            df = pd.DataFrame(data)
        else:
            df = pd.DataFrame([data])
        
        return df
    except requests.exceptions.RequestException as e:
        raise
    except Exception as e:
        raise

def insert_into_aps_inventory_fulfill_rate(df: pd.DataFrame):
    """插入满足率数据到数据库
    
    Args:
        df: DataFrame，包含以下列：
            - STAT_MONTH: 时间（月份，格式：YYYYMM）
            - UNIT_CODE: 单位编码
            - UNIT_NAME: 单位名称
            - DEVICE_TYPE: 设备类型
            - DEVICE_CODE: 设备码
            - TAG: 标签
            - FULFILL_RATE: 满足率（百分比，如 95.50 表示 95.5%）
    
    Returns:
        dict: 插入结果
    """
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-insert-into-aps-inventory-fulfill-rate'
        url = f"http://{host}:{port}{endpoint}"
        
        # 将DataFrame转换为字典列表，列名转为小写
        records = df.rename(columns=str.lower).to_dict('records')
        
        for record in records:
            response = session.post(url, json=record)
            response.raise_for_status()
        
        return {
            "success": True,
            "message": "数据插入成功",
            "inserted_count": len(records)
        }
    except requests.exceptions.RequestException as e:
        raise
    except Exception as e:
        raise

def insert_into_aps_inventory_replenish(df: pd.DataFrame):
    """插入基准库存数据到数据库
    
    Args:
        df: DataFrame，包含以下列：
            - STAT_MONTH: 时间（月份，格式：YYYYMM）
            - UNIT_CODE: 单位编码
            - UNIT_NAME: 单位名称
            - DEVICE_TYPE: 设备类型
            - DEVICE_CODE: 设备码
            - TAG: 标签
            - BASE_STOCK_NUM: 基准库存
    
    Returns:
        dict: 插入结果
    """
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-insert-into-aps-inventory-replenish'
        url = f"http://{host}:{port}{endpoint}"
        
        # 将DataFrame转换为字典列表，列名转为小写
        records = df.rename(columns=str.lower).to_dict('records')
        
        for record in records:
            response = session.post(url, json=record)
            response.raise_for_status()
        
        return {
            "success": True,
            "message": "数据插入成功",
            "inserted_count": len(records)
        }
    except requests.exceptions.RequestException as e:
        raise
    except Exception as e:
        raise

def insert_into_aps_inventory_replenish_qty(df: pd.DataFrame):
    """插入单位补货量数据到数据库
    
    Args:
        df: DataFrame，包含以下列：
            - STAT_MONTH: 时间（月份，格式：YYYYMM）
            - UNIT_CODE: 单位编码
            - UNIT_NAME: 单位名称
            - DEVICE_TYPE: 设备类型
            - DEVICE_CODE: 设备码
            - TAG: 标签
            - REPLENISH_NUM: 补货量
    
    Returns:
        dict: 插入结果
    """
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-insert-into-aps-inventory-replenish-qty'
        url = f"http://{host}:{port}{endpoint}"
        
        # 将DataFrame转换为字典列表，列名转为小写
        records = df.rename(columns=str.lower).to_dict('records')
        
        for record in records:
            response = session.post(url, json=record)
            response.raise_for_status()
        
        return {
            "success": True,
            "message": "数据插入成功",
            "inserted_count": len(records)
        }
    except requests.exceptions.RequestException as e:
        raise
    except Exception as e:
        raise

def insert_into_adam_plan_day_ias_pre(df: pd.DataFrame):
    """插入日补库计划数据到数据库

    Args:
        df: DataFrame，包含以下列：
            - PLAN_MONTH_IAS_PRE_ID: 唯一标识
            - PRE_DATE: 补库日期
            - REC_ORG_NO: 接收单位编码（市/县）
            - DEV_CLS: 设备分类
            - DEV_CATEG: 设备类别
            - DEV_CODE: 设备码
            - PLAN_IAS_NUM: 计划补库数量
            - EST_STOCK_NUM: 预计库存
            - GLOBAL_SCHEME_ID: 全局方案标识

    Returns:
        dict: 插入结果
    """
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-insert_into_adam_plan_day_ias_pre'
        url = f"http://{host}:{port}{endpoint}"

        # 将DataFrame转换为字典列表，列名转为小写
        records = df.rename(columns=str.lower).to_dict('records')

        # 逐条插入数据
        success_count = 0
        failed_count = 0
        errors = []

        for record in records:
            try:
                response = session.post(url, json=record)
                response.raise_for_status()
                success_count += 1
            except Exception as e:
                failed_count += 1
                errors.append({
                    "record": record,
                    "error": str(e)
                })

        return {
            "success": failed_count == 0,
            "message": f"数据插入完成，成功 {success_count} 条，失败 {failed_count} 条",
            "success_count": success_count,
            "failed_count": failed_count,
            "errors": errors if errors else None
        }
    except requests.exceptions.RequestException as e:
        raise
    except Exception as e:
        raise

def query_aps_qua_sto_by_month(rele_month:int):
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-query-aps-qua-sto-by-month'
        url = f"http://{host}:{port}{endpoint}"
        json = {
            "rele_month":rele_month
        }
        response = session.post(url, json=json)
        response.raise_for_status()
        
        data = response.json()
        
        if isinstance(data, list) and len(data) == 0:
            raise ValueError("返回数据为空")
        
        if isinstance(data, list):
            df = pd.DataFrame(data)
        else:
            df = pd.DataFrame([data])
        
        return df
    except requests.exceptions.RequestException as e:
        raise
    except Exception as e:
        raise

def query_aps_unqua_sto_by_month(rele_month:int):
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-query-aps-unqua-sto-by-month'
        url = f"http://{host}:{port}{endpoint}"
        json = {
            "rele_month":rele_month
        }
        response = session.post(url, json=json)
        response.raise_for_status()
        
        data = response.json()
        
        if isinstance(data, list) and len(data) == 0:
            raise ValueError("返回数据为空")
        
        if isinstance(data, list):
            df = pd.DataFrame(data)
        else:
            df = pd.DataFrame([data])
        
        return df
    except requests.exceptions.RequestException as e:
        raise
    except Exception as e:
        raise

def query_adam_dist_scheme_by_date_range(start_date:str,end_date:str):
    '''
    根据日期范围查询配送方案表
    '''
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-query_adam_dist_scheme_by_date_range'
        url = f"http://{host}:{port}{endpoint}"
        json = {
            "start_date":start_date,
            "end_date":end_date
        }
        response = session.post(url, json=json)
        response.raise_for_status()
        
        data = response.json()
        
        if isinstance(data, list) and len(data) == 0:
            raise ValueError("按照时间范围查询配送方案返回数据为空")
        
        if isinstance(data, list):
            df = pd.DataFrame(data)
        else:
            df = pd.DataFrame([data])
        
        return df
    except requests.exceptions.RequestException as e:
        raise
    except Exception as e:
        raise

def query_adam_dist_scheme_det_by_distschemeid(id:Number):
    '''
    根据配送计划id查询配送计划明细表
    '''
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-query_adam_dist_scheme_det_by_distschemeid'
        url = f"http://{host}:{port}{endpoint}"
        json = {
            "id":id
        }
        response = session.post(url, json=json)
        response.raise_for_status()
        
        data = response.json()
        
        if isinstance(data, list) and len(data) == 0:
            raise ValueError("根据配送id查询配送明细返回数据为空")
        
        if isinstance(data, list):
            df = pd.DataFrame(data)
        else:
            df = pd.DataFrame([data])
        
        return df
    except requests.exceptions.RequestException as e:
        raise
    except Exception as e:
        raise

def query_adam_pre_range_info():
    '''
    查询预测范围数据
    '''
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-query_adam_pre_range_info'
        url = f"http://{host}:{port}{endpoint}"
        json = {}
        response = session.post(url, json=json)
        response.raise_for_status()
        
        data = response.json()
        
        if isinstance(data, list) and len(data) == 0:
            raise ValueError("查询预测范围表返回数据为空")
        
        if isinstance(data, list):
            df = pd.DataFrame(data)
        else:
            df = pd.DataFrame([data])

        df.rename(columns={'AVG_PRICE': 'TAX_UP'}, inplace=True)
        
        return df
    except requests.exceptions.RequestException as e:
        raise
    except Exception as e:
        raise

def query_adam_qua_stock_sample_by_year_month(year:str, month:str):
    """
    通过年月时间查询合格品库存数据
    """
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-query_adam_qua_stock_sample_by_year_month'
        url = f"http://{host}:{port}{endpoint}"
        json = {
            "year":year,
            "month":month
        }
        response = session.post(url, json=json)
        response.raise_for_status()
        
        data = response.json()
        
        if isinstance(data, list) and len(data) == 0:
            raise ValueError("查询合格品数据返回数据为空")
        
        if isinstance(data, list):
            df = pd.DataFrame(data)
        else:
            df = pd.DataFrame([data])
        
        return df
    except requests.exceptions.RequestException as e:
        raise
    except Exception as e:
        raise

def query_adam_pend_stock_sample_by_year_month(year:str, month:str):
    """
    通过年月时间查询待检库存数据
    """
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-query_adam_pend_stock_sample_by_year_month'
        url = f"http://{host}:{port}{endpoint}"
        json = {
            "year":year,
            "month":month
        }
        response = session.post(url, json=json)
        response.raise_for_status()
        
        data = response.json()
        
        if isinstance(data, list) and len(data) == 0:
            raise ValueError("查询待检库数据返回数据为空")
        
        if isinstance(data, list):
            df = pd.DataFrame(data)
        else:
            df = pd.DataFrame([data])
        
        return df
    except requests.exceptions.RequestException as e:
        raise
    except Exception as e:
        raise

def query_adam_wd_dmd_pre_by_year_month_and_pretype(year:str, month:str, pre_type:str):
    """
    通过年月以及预测类型查询周/日市县需求预测结果
    """
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-query_adam_wd_dmd_pre_by_year_month_and_pretype'
        url = f"http://{host}:{port}{endpoint}"
        json = {
            "year":year,
            "month":month,
            "pre_type":pre_type
        }
        response = session.post(url, json=json)
        response.raise_for_status()
        
        data = response.json()
        
        if isinstance(data, list) and len(data) == 0:
            raise ValueError("查询周/日需求预测返回数据为空")
        
        if isinstance(data, list):
            df = pd.DataFrame(data)
        else:
            df = pd.DataFrame([data])
        
        return df
    except requests.exceptions.RequestException as e:
        raise
    except Exception as e:
        raise

def query_adam_y_mgt_org():
    '''
    查询组织架构信息
    '''
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-query_adam_y_mgt_org'
        url = f"http://{host}:{port}{endpoint}"
        json = {}
        response = session.post(url, json=json)
        response.raise_for_status()
        
        data = response.json()
        
        if isinstance(data, list) and len(data) == 0:
            raise ValueError("查询组织架构信息返回数据为空")
        
        if isinstance(data, list):
            df = pd.DataFrame(data)
        else:
            df = pd.DataFrame([data])
        
        return df
    except requests.exceptions.RequestException as e:
        raise
    except Exception as e:
        raise

def query_adam_spec_code_config():
    '''
    查询规格设备码信息
    '''
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-query_adam_spec_code_config'
        url = f"http://{host}:{port}{endpoint}"
        json = {}
        response = session.post(url, json=json)
        response.raise_for_status()
        
        data = response.json()
        
        if isinstance(data, list) and len(data) == 0:
            raise ValueError("查询规格设备码配置返回数据为空")
        
        if isinstance(data, list):
            df = pd.DataFrame(data)
        else:
            df = pd.DataFrame([data])
        
        return df
    except requests.exceptions.RequestException as e:
        raise
    except Exception as e:
        raise

def query_adam_del_site_conf():
    '''
    查询配送站点信息
    '''
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-query_adam_del_site_conf'
        url = f"http://{host}:{port}{endpoint}"
        json = {}
        response = session.post(url, json=json)
        response.raise_for_status()
        
        data = response.json()
        
        if isinstance(data, list) and len(data) == 0:
            raise ValueError("查询站点信息返回数据为空")
        
        if isinstance(data, list):
            df = pd.DataFrame(data)
        else:
            df = pd.DataFrame([data])
        
        return df
    except requests.exceptions.RequestException as e:
        raise
    except Exception as e:
        raise

def query_adam_plan_day_ias_pre_by_date(date:str):
    '''
    根据日期查询日补库计划
    '''
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-query_adam_plan_day_ias_pre_by_date'
        url = f"http://{host}:{port}{endpoint}"
        json = {
            "date":date
        }
        response = session.post(url, json=json)
        response.raise_for_status()
        
        data = response.json()
        
        if isinstance(data, list) and len(data) == 0:
            print("查询日补库返回数据为空")
            return pd.DataFrame()
        
        if isinstance(data, list):
            df = pd.DataFrame(data)
        else:
            df = pd.DataFrame([data])
        
        return df
    except requests.exceptions.RequestException as e:
        raise
    except Exception as e:
        raise

def insert_into_adam_dist_scheme(df: pd.DataFrame):
    """插入配送方案主表数据到数据库

    Args:
        df: DataFrame，包含以下列：
            - DIST_SCHEME_ID: 唯一标识
            - CAR_TYPE: 车辆类型
            - PLAN_DIST_DATE: 计划配送日期
            - DIST_FLAG: 是否配送
            - LATE_FLAG: 是否逾期配送
            - LOAD_RATE: 装载率
            - CREATE_DATE: 创建时间
            - UPDATE_DATE: 更新时间
            - GLOBAL_SCHEME_ID: 全局方案标识

    Returns:
        dict: 插入结果
    """
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-insert_into_adam_dist_scheme'
        url = f"http://{host}:{port}{endpoint}"

        # 将DataFrame转换为字典列表，列名转为小写
        records = df.rename(columns=str.lower).to_dict('records')

        # 逐条插入数据
        success_count = 0
        failed_count = 0
        errors = []

        for record in records:
            try:
                response = session.post(url, json=record)
                response.raise_for_status()
                success_count += 1
            except Exception as e:
                failed_count += 1
                errors.append({
                    "record": record,
                    "error": str(e)
                })

        return {
            "success": failed_count == 0,
            "message": f"数据插入完成，成功 {success_count} 条，失败 {failed_count} 条",
            "success_count": success_count,
            "failed_count": failed_count,
            "errors": errors if errors else None
        }
    except requests.exceptions.RequestException as e:
        raise
    except Exception as e:
        raise

def insert_into_adam_dist_scheme_det(df: pd.DataFrame):
    """插入配送方案明细表数据到数据库

    Args:
        df: DataFrame，包含以下列：
            - DIST_SCHEME_DET_ID: 方案明细唯一标识
            - DIST_SCHEME_ID: 方案唯一标识
            - REC_ORG_NO: 接收单位
            - DEV_CODE: 设备码
            - DEV_CLS: 设备分类
            - DEV_CATEG: 设备类别
            - DIST_SEQ: 配送顺序
            - LOAD_SEQ: 装车顺序
            - PLAN_DIST_NUM: 计划配送数量
            - EST_TOT_DIST_MIST: 预计配送里程
            - DIST_EXP: 配送费用
            - GLOBAL_SCHEME_ID: 全局方案标识

    Returns:
        dict: 插入结果
    """
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-insert_into_adam_dist_scheme_det'
        url = f"http://{host}:{port}{endpoint}"

        # 将DataFrame转换为字典列表，列名转为小写
        records = df.rename(columns=str.lower).to_dict('records')

        # 逐条插入数据
        success_count = 0
        failed_count = 0
        errors = []

        for record in records:
            try:
                response = session.post(url, json=record)
                response.raise_for_status()
                success_count += 1
            except Exception as e:
                failed_count += 1
                errors.append({
                    "record": record,
                    "error": str(e)
                })

        return {
            "success": failed_count == 0,
            "message": f"数据插入完成，成功 {success_count} 条，失败 {failed_count} 条",
            "success_count": success_count,
            "failed_count": failed_count,
            "errors": errors if errors else None
        }
    except requests.exceptions.RequestException as e:
        raise
    except Exception as e:
        raise

def insert_into_adam_stock_week_limt_pre(df: pd.DataFrame):
    """插入库存阈值预测表（周）数据到数据库

    Args:
        df: DataFrame，包含以下列：
            - STOCK_WEEK_LIMT_PRE_ID: 唯一标识
            - PRE_YEAR: 年份
            - PRE_QUARTER: 季度
            - PRE_MONTH: 月份
            - PRE_WEEK: 周次
            - DEV_CLS: 设备分类
            - DEV_CATEG: 设备类别
            - DEV_CODE: 设备码
            - PRE_UP: 预测上限
            - PRE_DOWN: 预测下限
            - BASE_LIMT: 基准库存
            - PRE_TIME: 预测时间
            - GLOBAL_SCHEME_ID: 全局方案标识

    Returns:
        dict: 插入结果
    """
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-insert_into_adam_stock_week_limt_pre'
        url = f"http://{host}:{port}{endpoint}"

        # 将DataFrame转换为字典列表，列名转为小写
        records = df.rename(columns=str.lower).to_dict('records')

        # 逐条插入数据
        success_count = 0
        failed_count = 0
        errors = []

        for record in records:
            try:
                response = session.post(url, json=record)
                response.raise_for_status()
                success_count += 1
            except Exception as e:
                failed_count += 1
                errors.append({
                    "record": record,
                    "error": str(e)
                })

        return {
            "success": failed_count == 0,
            "message": f"数据插入完成，成功 {success_count} 条，失败 {failed_count} 条",
            "success_count": success_count,
            "failed_count": failed_count,
            "errors": errors if errors else None
        }
    except requests.exceptions.RequestException as e:
        raise
    except Exception as e:
        raise

def query_adam_org_stock_sample_by_month(month: str):
    '''
    按年月查询库存快照,属于预测当月，查询上月
    '''
    try:
        # ===================== 【核心修改：自动计算上月】 =====================
        from datetime import datetime
        # 把传入的 YYYYMM 转成日期，再减一个月，得到上月
        current_date = datetime.strptime(month, "%Y%m")
        # 计算往前推 2 个月
        if current_date.month in (1, 2):
            last_month = f"{current_date.year - 1}{12 + current_date.month - 2:02d}"
        else:
            last_month = f"{current_date.year}{current_date.month - 2:02d}"
        # ====================================================================
        print(f'准备查询{last_month}的库存快照')
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-query_adam_org_stock_sample_by_month'
        url = f"http://{host}:{port}{endpoint}"
        
        json = {
            "stock_month": last_month  # 对应SQL里的 #{month}
        }
        
        response = session.post(url, json=json)
        response.raise_for_status()

        data = response.json()
        print(f'获取日期{last_month}的初始库存，数据量{len(data)}条')
        
        if isinstance(data, list) and len(data) == 0:
            print()
            raise ValueError(f"查询{last_month}库存快照返回数据为空")
        
        if isinstance(data, list):
            df = pd.DataFrame(data)
        else:
            df = pd.DataFrame([data])
        
        return df
    
    except requests.exceptions.RequestException as e:
        raise
    except Exception as e:
        raise


def query_adam_realtime_stocknum():
    '''
    查询地市实时库存。

    说明:
        - 已包含下级单位库存，按87家地市仓库汇总
        - 包含87家单位和启用设备码维度
        - 按仓库(ORG_NO) × 设备码(DEV_CODE) 维度返回
        - 已完成新旧设备码转换

    Returns:
        pd.DataFrame: 列 ORG_NO, ORG_NAME, DEV_CODE, STOCK_NUM
    '''
    import logging
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-query-realtime-stocknum'
        url = f"http://{host}:{port}{endpoint}"

        response = session.post(url, json={})
        response.raise_for_status()

        data = response.json()

        if not isinstance(data, list):
            raise ValueError(f"地市实时库存接口返回格式异常，期望 list，实际为 {type(data).__name__}")

        if len(data) == 0:
            raise ValueError("地市实时库存查询返回空数据")

        df = pd.DataFrame(data)
        logging.info(f'获取地市实时库存成功，数据量{len(df)}条')
        return df

    except requests.exceptions.RequestException:
        logging.exception('地市实时库存查询网络异常')
        raise
    except Exception:
        logging.exception('地市实时库存查询失败')
        raise


def query_adam_yqm_dmd_pre_by_year(year: str):
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        # endpoint 与 sql_id 完全对应
        endpoint = '/exec/gk-adam-query_adam_yqm_dmd_pre_by_year'
        url = f"http://{host}:{port}{endpoint}"
        
        json = {
            "year": year  # 仅按年份查询
        }
        
        response = session.post(url, json=json)
        response.raise_for_status()
        
        data = response.json()
        
        if isinstance(data, list) and len(data) == 0:
            raise ValueError("需求预测查询结果为空")
        
        if isinstance(data, list):
            df = pd.DataFrame(data)
        else:
            df = pd.DataFrame([data])
        
        return df
    
    except requests.exceptions.RequestException as e:
        raise
    except Exception as e:
        raise

def insert_into_adam_plan_month_ias_pre(df: pd.DataFrame):
    """插入计划补库数量（月度）表数据到数据库

    Args:
        df: DataFrame，包含以下列：
            - PLAN_MONTH_IAS_PRE_ID: 唯一标识
            - PRE_YEAR: 年份
            - PRE_MONTH: 月份
            - REC_ORG_NO: 接收单位编码
            - DEV_CLS: 设备分类
            - DEV_CATEG: 设备类别
            - DEV_CODE: 设备码
            - PLAN_IAS_NUM: 计划补库数量
            - GLOBAL_SCHEME_ID: 全局方案标识

    Returns:
        dict: 插入结果
    """
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-insert_into_adam_plan_month_ias_pre'
        url = f"http://{host}:{port}{endpoint}"

        # 将DataFrame转换为字典列表，列名转为小写
        records = df.rename(columns=str.lower).to_dict('records')

        # 逐条插入数据
        success_count = 0
        failed_count = 0
        errors = []

        for record in records:
            try:
                response = session.post(url, json=record)
                response.raise_for_status()
                success_count += 1
            except Exception as e:
                failed_count += 1
                errors.append({
                    "record": record,
                    "error": str(e)
                })

        return {
            "success": failed_count == 0,
            "message": f"数据插入完成，成功 {success_count} 条，失败 {failed_count} 条",
            "success_count": success_count,
            "failed_count": failed_count,
            "errors": errors if errors else None
        }
    except requests.exceptions.RequestException as e:
        raise
    except Exception as e:
        raise

def insert_into_adam_stock_month_limit_pre(df: pd.DataFrame):
    """插入库存阈值预测表（月）数据到数据库

    Args:
        df: DataFrame，包含以下列：
            - STOCK_MONTH_LIMIT_PRE_ID: 唯一标识
            - PRE_YEAR: 年份
            - PRE_MONTH: 月份
            - ORG_NO: 单位编码
            - DEV_CLS: 设备分类
            - DEV_CATEG: 设备类别
            - DEV_CODE: 设备码
            - BASE_LIMIT: 基准库存(预测阈值)
            - PRE_TIME: 预测时间
            - GLOBAL_SCHEME_ID: 全局方案标识

    Returns:
        dict: 插入结果
    """
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-insert_into_adam_stock_month_limit_pre'
        url = f"http://{host}:{port}{endpoint}"

        # 将DataFrame转换为字典列表，列名转为小写
        records = df.rename(columns=str.lower).to_dict('records')

        # 逐条插入数据
        success_count = 0
        failed_count = 0
        errors = []

        for record in records:
            try:
                response = session.post(url, json=record)
                response.raise_for_status()
                success_count += 1
            except Exception as e:
                failed_count += 1
                errors.append({
                    "record": record,
                    "error": str(e)
                })

        return {
            "success": failed_count == 0,
            "message": f"数据插入完成，成功 {success_count} 条，失败 {failed_count} 条",
            "success_count": success_count,
            "failed_count": failed_count,
            "errors": errors if errors else None
        }
    except requests.exceptions.RequestException as e:
        raise
    except Exception as e:
        raise


def query_adam_glob_strategy_scheme_by_month(yearmonth: str):
    """根据年月查询全局策略方案

    Args:
        month: 年月字符串，格式：YYYYMM 或 YYYY-MM

    Returns:
        pd.DataFrame: 查询结果数据集
    """
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-query_adam_glob_strategy_scheme_by_month'
        url = f"http://{host}:{port}{endpoint}"

        json_data = {
            "month": yearmonth
        }

        response = session.post(url, json=json_data)
        response.raise_for_status()

        data = response.json()

        if isinstance(data, list) and len(data) == 0:
            print("查询全局策略主表返回数据为空")
            return pd.DataFrame()

        if isinstance(data, list):
            df = pd.DataFrame(data)
        else:
            df = pd.DataFrame([data])

        return df

    except requests.exceptions.RequestException as e:
        raise
    except Exception as e:
        raise


def query_adam_glob_strategy_scheme_itt_by_schemeid(scheme_id: int):
    """根据方案标识查询全局策略方案周转明细

    Args:
        scheme_id: 方案标识

    Returns:
        pd.DataFrame: 查询结果数据集
    """
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-query_adam_glob_strategy_scheme_itt_by_schemeid'
        url = f"http://{host}:{port}{endpoint}"

        json_data = {
            "scheme_id": scheme_id
        }

        response = session.post(url, json=json_data)
        response.raise_for_status()

        data = response.json()

        if isinstance(data, list) and len(data) == 0:
            print("查询全局方案周转明细返回数据为空")
            return pd.DataFrame()

        if isinstance(data, list):
            df = pd.DataFrame(data)
        else:
            df = pd.DataFrame([data])

        return df

    except requests.exceptions.RequestException as e:
        raise
    except Exception as e:
        raise


def query_adam_yqm_dmd_pre_by_year_month(year: str, month: str):
    """根据年份和月份查询年季月度需求预测结果

    Args:
        year: 年份，格式：YYYY
        month: 月份，格式：MM

    Returns:
        pd.DataFrame: 查询结果数据集
    """
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-query_adam_yqm_dmd_pre_by_year_month'
        url = f"http://{host}:{port}{endpoint}"
        print('按照年月获取年季月需求预测数据')
        print('sql_id:gk-adam-query_adam_yqm_dmd_pre_by_year_month')
        
        json_data = {
            "year": year,
            "month": month
        }
        
        response = session.post(url, json=json_data)
        response.raise_for_status()
        
        data = response.json()
        
        if isinstance(data, list) and len(data) == 0:
            raise ValueError("查询年季月需求预测返回数据为空")
        
        if isinstance(data, list):
            df = pd.DataFrame(data)
        else:
            df = pd.DataFrame([data])
        
        return df
    
    except requests.exceptions.RequestException as e:
        raise
    except Exception as e:
        raise


def query_adam_veri_config_all():
    """查询检定线信息表全量数据

    Returns:
        pd.DataFrame: 查询结果数据集
    """
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-query_adam_veri_config_all'
        url = f"http://{host}:{port}{endpoint}"
        
        response = session.post(url, json={})
        response.raise_for_status()
        
        data = response.json()
        
        if isinstance(data, list) and len(data) == 0:
            raise ValueError("查询检定线配置返回数据为空")
        
        if isinstance(data, list):
            df = pd.DataFrame(data)
        else:
            df = pd.DataFrame([data])
        
        return df
    
    except requests.exceptions.RequestException as e:
        raise
    except Exception as e:
        raise


def query_adam_single_cost_config_all():
    """查询单资产成本基础数据配置全量数据

    Returns:
        pd.DataFrame: 全表查询结果
    """
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-query_adam_single_cost_config_all'
        url = f"http://{host}:{port}{endpoint}"

        # 全量查询无需参数
        response = session.post(url, json={})
        response.raise_for_status()

        data = response.json()
        if isinstance(data, list) and len(data) == 0:
            raise ValueError("查询资产配置表返回数据为空")

        if isinstance(data, list):
            df = pd.DataFrame(data)
        else:
            df = pd.DataFrame([data])

        return df

    except requests.exceptions.RequestException as e:
        raise
    except Exception as e:
        raise


def query_adam_glob_strategy_scheme_cost_by_schemeid(scheme_id: int):
    """根据方案标识查询全局策略方案成本明细

    Args:
        scheme_id: 方案标识

    Returns:
        pd.DataFrame: 成本明细查询结果
    """
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-query_adam_glob_strategy_scheme_cost_by_schemeid'
        url = f"http://{host}:{port}{endpoint}"

        json_data = {
            "scheme_id": scheme_id
        }

        response = session.post(url, json=json_data)
        response.raise_for_status()

        data = response.json()
        if isinstance(data, list) and len(data) == 0:
            print("查询全局方案成本明细返回数据为空")
            return pd.DataFrame()

        if isinstance(data, list):
            df = pd.DataFrame(data)
        else:
            df = pd.DataFrame([data])

        return df

    except requests.exceptions.RequestException as e:
        raise
    except Exception as e:
        raise

def insert_into_adam_glob_strategy_scheme_cost(df: pd.DataFrame):
    """插入全局策略方案成本明细数据到数据库

    Args:
        df: DataFrame，包含以下列：
            - COST_DET_ID: 方案成本明细标识
            - SCHEME_ID: 方案标识
            - LINK_TYPE: 环节类型
            - COST_TYPE: 成本类型
            - ORG_NO: 管理单位
            - DEV_CLS: 设备分类
            - DEV_CATEG: 设备类别
            - PRE_STAT_COST: 预期总成本
            - PRE_SINGLE_COST: 预期平均单只成本
            - PRE_COST_YOY: 预期成本同比变化率
            - PRE_COST_TR: 预期成本环比变化率
            - INCUR_STAT_COST: 当前产生总成本
            - INCUR_SINGLE_COST: 当前平均单只成本
            - INCUR_COST_YOY: 当前成本同比变化率
            - INCUR_COST_TR: 当前成本环比变化率
            - MADE_DATE: 生成时间
            - UPDATE_DATE: 更新时间

    Returns:
        dict: 插入结果
    """
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-insert_into_adam_glob_strategy_scheme_cost'
        url = f"http://{host}:{port}{endpoint}"

        # 将DataFrame转换为字典列表，列名转为小写
        records = df.rename(columns=str.lower).to_dict('records')

        # 逐条插入数据
        success_count = 0
        failed_count = 0
        errors = []

        for record in records:
            try:
                response = session.post(url, json=record)
                response.raise_for_status()
                success_count += 1
            except Exception as e:
                failed_count += 1
                errors.append({
                    "record": record,
                    "error": str(e)
                })

        return {
            "success": failed_count == 0,
            "message": f"数据插入完成，成功 {success_count} 条，失败 {failed_count} 条",
            "success_count": success_count,
            "failed_count": failed_count,
            "errors": errors if errors else None
        }
    except requests.exceptions.RequestException as e:
        raise
    except Exception as e:
        raise

def batch_insert_adam_glob_strategy_scheme_cost(df: pd.DataFrame, batch_size: int = 100):
    """批量插入全局策略方案成本明细数据

    Args:
        df: DataFrame，包含以下列：
            - COST_DET_ID: 方案成本明细标识
            - SCHEME_ID: 方案标识
            - LINK_TYPE: 环节类型
            - COST_TYPE: 成本类型
            - ORG_NO: 管理单位
            - DEV_CLS: 设备分类
            - DEV_CATEG: 设备类别
            - PRE_STAT_COST: 预期总成本
            - PRE_SINGLE_COST: 预期平均单只成本
            - PRE_COST_YOY: 预期成本同比变化率
            - PRE_COST_TR: 预期成本环比变化率
            - INCUR_STAT_COST: 当前产生总成本
            - INCUR_SINGLE_COST: 当前平均单只成本
            - INCUR_COST_YOY: 当前成本同比变化率
            - INCUR_COST_TR: 当前成本环比变化率
            - MADE_DATE: 生成时间
            - UPDATE_DATE: 更新时间
        batch_size: 每批插入条数，默认 100

    Returns:
        dict: 插入结果
    """
    import math
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-insert_into_adam_glob_strategy_scheme_cost'
        url = f"http://{host}:{port}{endpoint}"

        if 'PRE_SINGLE_COST' in df.columns:
            df['PRE_SINGLE_COST'] = df['PRE_SINGLE_COST'].fillna(0)
        df = df.astype(object).where(df.notna(), None)
        records = df.rename(columns=str.lower).to_dict('records')
        import math
        for r in records:
            for k, v in r.items():
                if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                    r[k] = None
        total = len(records)
        batches = math.ceil(total / batch_size)
        logging.info(f'批量插入 COST: 共 {total} 条, 分 {batches} 批(每批 {batch_size} 条)')

        success_count = 0
        failed_count = 0
        errors = []

        for i in range(0, total, batch_size):
            chunk = records[i:i + batch_size]
            batch_no = i // batch_size + 1
            try:
                response = session.post(url, json=chunk)
                response.raise_for_status()
                success_count += len(chunk)
                logging.info(f'批量插入 COST 第 {batch_no}/{batches} 批成功, {len(chunk)} 条')
            except Exception as e:
                failed_count += len(chunk)
                errors.append({"batch": batch_no, "count": len(chunk), "error": str(e)})
                logging.error(f'批量插入 COST 第 {batch_no}/{batches} 批失败: {e}')
                raise

        return {
            "success": failed_count == 0,
            "message": f"批量插入完成, 成功 {success_count} 条, 失败 {failed_count} 条",
            "success_count": success_count,
            "failed_count": failed_count,
            "batches": batches,
            "errors": errors if errors else None
        }
    except requests.exceptions.RequestException as e:
        raise
    except Exception as e:
        raise


def insert_into_adam_glob_strategy_scheme_lps(df: pd.DataFrame):
    """插入全局策略方案环节计划汇总明细数据到数据库

    Args:
        df: DataFrame，包含以下列：
            - ITT_DET_ID: 方案周转明细标识
            - SCHEME_ID: 方案标识
            - LINK_TYPE: 环节类型
            - ORG_NO: 管理单位
            - DEV_CLS: 设备分类
            - DEV_CATEG: 设备类别
            - PRE_STAT_NUM: 预期计划总量
            - INCUR_STAT_NUM: 当前已完成总量
            - MADE_DATE: 生成时间
            - UPDATE_DATE: 更新时间

    Returns:
        dict: 插入结果
    """
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-insert_into_adam_glob_strategy_scheme_lps'
        url = f"http://{host}:{port}{endpoint}"

        # 将DataFrame转换为字典列表，列名转为小写
        records = df.rename(columns=str.lower).to_dict('records')

        # 逐条插入数据
        success_count = 0
        failed_count = 0
        errors = []

        for record in records:
            try:
                response = session.post(url, json=record)
                response.raise_for_status()
                success_count += 1
            except Exception as e:
                failed_count += 1
                errors.append({
                    "record": record,
                    "error": str(e)
                })

        return {
            "success": failed_count == 0,
            "message": f"数据插入完成，成功 {success_count} 条，失败 {failed_count} 条",
            "success_count": success_count,
            "failed_count": failed_count,
            "errors": errors if errors else None
        }
    except requests.exceptions.RequestException as e:
        raise
    except Exception as e:
        raise


def batch_insert_adam_glob_strategy_scheme_lps(df: pd.DataFrame, batch_size: int = 100):
    """批量插入全局策略方案环节计划汇总明细数据

    Args:
        df: DataFrame，包含以下列：
            - ITT_DET_ID: 方案周转明细标识
            - SCHEME_ID: 方案标识
            - LINK_TYPE: 环节类型
            - ORG_NO: 管理单位
            - DEV_CLS: 设备分类
            - DEV_CATEG: 设备类别
            - PRE_STAT_NUM: 预期计划总量
            - INCUR_STAT_NUM: 当前已完成总量
            - MADE_DATE: 生成时间
            - UPDATE_DATE: 更新时间
        batch_size: 每批插入条数，默认 100

    Returns:
        dict: 插入结果
    """
    import math
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-insert_into_adam_glob_strategy_scheme_lps'
        url = f"http://{host}:{port}{endpoint}"

        records = df.rename(columns=str.lower).to_dict('records')
        total = len(records)
        batches = math.ceil(total / batch_size)
        logging.info(f'批量插入 LPS: 共 {total} 条, 分 {batches} 批(每批 {batch_size} 条)')

        success_count = 0
        failed_count = 0
        errors = []

        for i in range(0, total, batch_size):
            chunk = records[i:i + batch_size]
            batch_no = i // batch_size + 1
            try:
                response = session.post(url, json=chunk)
                response.raise_for_status()
                success_count += len(chunk)
                logging.info(f'批量插入 LPS 第 {batch_no}/{batches} 批成功, {len(chunk)} 条')
            except Exception as e:
                failed_count += len(chunk)
                errors.append({"batch": batch_no, "count": len(chunk), "error": str(e)})
                logging.error(f'批量插入 LPS 第 {batch_no}/{batches} 批失败: {e}')
                raise

        return {
            "success": failed_count == 0,
            "message": f"批量插入完成, 成功 {success_count} 条, 失败 {failed_count} 条",
            "success_count": success_count,
            "failed_count": failed_count,
            "batches": batches,
            "errors": errors if errors else None
        }
    except requests.exceptions.RequestException as e:
        raise
    except Exception as e:
        raise


def insert_into_adam_glob_strategy_scheme(df: pd.DataFrame):
    """插入全局策略方案主表数据到数据库

    Args:
        df: DataFrame，包含以下列：
            - SCHEME_ID: 方案标识
            - SCHEME_NO: 方案编号
            - SCHEME_NAME: 方案名称
            - SCHEME_FOCUS: 方案侧重(01资金入账优先,02库存周转优先,03均衡分布)
            - EXEC_YM: 方案执行年月
            - PRE_STAT_COST: 预期综合总成本
            - PRE_SINGLE_COST: 预期平均单只成本
            - COST_YOY: 成本同比变化率%
            - COST_TR: 成本环比变化率%
            - PRE_ITR: 预期库存运行比%
            - ITR_YOY: 运行比同比变化率%
            - ITR_TR: 运行比环比变化率%
            - PRE_ITT: 预期库存周转次数
            - ITT_YOY: 周转同比变化率%
            - ITT_TR: 周转环比变化率%
            - MADE_DATE: 方案生成时间
            - COM_INDEX: 综合指标评价
            - SCHEME_DESC: 方案说明
            - APPR_DATE: 审批时间
            - APPR_RSLT: 审批结果(00待审批,01审批通过,02审批不通过)
            - APPR_REMARK: 审批意见
            - APPR_USER: 审批人
            - APPR_ORG: 审批单位

    Returns:
        dict: 插入结果
    """
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-insert_into_adam_glob_strategy_scheme'
        url = f"http://{host}:{port}{endpoint}"

        # 将DataFrame转换为字典列表，列名转为小写
        records = df.rename(columns=str.lower).to_dict('records')

        # 逐条插入数据
        success_count = 0
        failed_count = 0
        errors = []

        for record in records:
            try:
                response = session.post(url, json=record)
                response.raise_for_status()
                success_count += 1
            except Exception as e:
                failed_count += 1
                errors.append({
                    "record": record,
                    "error": str(e)
                })

        return {
            "success": failed_count == 0,
            "message": f"数据插入完成，成功 {success_count} 条，失败 {failed_count} 条",
            "success_count": success_count,
            "failed_count": failed_count,
            "errors": errors if errors else None
        }
    except requests.exceptions.RequestException as e:
        raise
    except Exception as e:
        raise

def batch_insert_adam_glob_strategy_scheme_itt(df: pd.DataFrame, batch_size: int = 100):
    """批量插入全局策略方案周转明细数据

    Args:
        df: DataFrame，包含以下列：
            - ITT_DET_ID: 方案周转明细标识
            - SCHEME_ID: 方案标识
            - ORG_NO: 管理单位
            - START_STOCK_NUM: 月初库存总量
            - END_STOCK_NUM: 月末库存总量
            - DEV_CLS: 设备分类
            - DEV_CATEG: 设备类别
            - PRE_ITR: 预期库存运行比%
            - ITR_YOY: 运行比同比变化率%
            - ITR_TR: 运行比环比变化率%
            - INCUR_ITR: 当前库存运行比%
            - PRE_ITT: 预期库存周转次数
            - ITT_YOY: 周转同比变化率%
            - ITT_TR: 周转环比变化率%
            - INCUR_ITT: 当前库存周转次数
            - MADE_DATE: 生成时间
            - UPDATE_DATE: 更新时间
        batch_size: 每批插入条数，默认 100

    Returns:
        dict: 插入结果
    """
    import math
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-insert_into_adam_glob_strategy_scheme_itt'
        url = f"http://{host}:{port}{endpoint}"

        df = df.astype(object).where(df.notna(), None)
        records = df.rename(columns=str.lower).to_dict('records')
        import math
        for r in records:
            for k, v in r.items():
                if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                    r[k] = None

        total = len(records)
        batches = math.ceil(total / batch_size)
        logging.info(f'批量插入 ITT: 共 {total} 条, 分 {batches} 批(每批 {batch_size} 条)')

        success_count = 0
        failed_count = 0
        errors = []

        for i in range(0, total, batch_size):
            chunk = records[i:i + batch_size]
            batch_no = i // batch_size + 1
            try:
                response = session.post(url, json=chunk)
                response.raise_for_status()
                success_count += len(chunk)
                logging.info(f'批量插入 ITT 第 {batch_no}/{batches} 批成功, {len(chunk)} 条')
            except Exception as e:
                failed_count += len(chunk)
                errors.append({"batch": batch_no, "count": len(chunk), "error": str(e)})
                logging.error(f'批量插入 ITT 第 {batch_no}/{batches} 批失败: {e}')
                raise

        return {
            "success": failed_count == 0,
            "message": f"批量插入完成, 成功 {success_count} 条, 失败 {failed_count} 条",
            "success_count": success_count,
            "failed_count": failed_count,
            "batches": batches,
            "errors": errors if errors else None
        }
    except requests.exceptions.RequestException as e:
        raise
    except Exception as e:
        raise


def insert_into_adam_glob_strategy_scheme_itt(df: pd.DataFrame):
    """插入全局策略方案周转明细数据到数据库

    Args:
        df: DataFrame，包含以下列：
            - ITT_DET_ID: 方案周转明细标识
            - SCHEME_ID: 方案标识
            - ORG_NO: 管理单位
            - START_STOCK_NUM: 月初库存总量
            - END_STOCK_NUM: 月末库存总量
            - DEV_CLS: 设备分类
            - DEV_CATEG: 设备类别
            - PRE_ITR: 预期库存运行比%
            - ITR_YOY: 运行比同比变化率%
            - ITR_TR: 运行比环比变化率%
            - INCUR_ITR: 当前库存运行比%
            - PRE_ITT: 预期库存周转次数
            - ITT_YOY: 周转同比变化率%
            - ITT_TR: 周转环比变化率%
            - INCUR_ITT: 当前库存周转次数
            - MADE_DATE: 生成时间
            - UPDATE_DATE: 更新时间

    Returns:
        dict: 插入结果
    """
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-insert_into_adam_glob_strategy_scheme_itt'
        url = f"http://{host}:{port}{endpoint}"

        # 将DataFrame转换为字典列表，列名转为小写
        records = df.rename(columns=str.lower).to_dict('records')

        # 逐条插入数据
        success_count = 0
        failed_count = 0
        errors = []

        for record in records:
            try:
                response = session.post(url, json=record)
                response.raise_for_status()
                success_count += 1
            except Exception as e:
                failed_count += 1
                errors.append({
                    "record": record,
                    "error": str(e)
                })

        return {
            "success": failed_count == 0,
            "message": f"数据插入完成，成功 {success_count} 条，失败 {failed_count} 条",
            "success_count": success_count,
            "failed_count": failed_count,
            "errors": errors if errors else None
        }
    except requests.exceptions.RequestException as e:
        raise
    except Exception as e:
        raise

def query_adam_stock_count_sample_all():
    """
    全量查询库存信息表（无参数）
    """
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-query_adam_stock_count_sample_all'
        url = f"http://{host}:{port}{endpoint}"

        # 全量查询不需要传参数
        response = session.post(url, json={})
        response.raise_for_status()
        data = response.json()

        # 空数据报错（按你之前的要求）
        if not data:
            raise ValueError("库存信息表全量查询结果为空")

        return pd.DataFrame(data)

    except requests.exceptions.RequestException as e:
        raise
    except Exception as e:
        raise

def update_adam_pre_conc_stat(pre_conc_id, stat):
    """
    根据预测结论ID更新状态
    """
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-update_adam_pre_conc_stat_by_id'  # 对应你刚才的sql_id
        url = f"http://{host}:{port}{endpoint}"

        # 参数：预测结论ID + 状态
        params = {
            "pre_conc_id": pre_conc_id,
            "stat": stat
        }

        response = session.post(url, json=params)
        response.raise_for_status()
        data = response.json()

        # 可选：根据返回判断是否成功
        return data

    except requests.exceptions.RequestException as e:
        raise
    except Exception as e:
        raise



def insert_into_adam_allot_day_plan_pre(df: pd.DataFrame):
    """插入调拨计划（日）表数据到数据库

    Args:
        df: DataFrame，包含以下列：
            - allot_day_plan_pre_id: 唯一标识
            - allot_date: 日期
            - send_org_no: 调出单位
            - rec_org_no: 接收单位
            - dev_cls: 设备分类
            - dev_categ: 设备类别
            - dev_code: 设备码
            - send_num: 调拨数量
            - send_stock_num: 调出单位预计库存量
            - rec_stock_num: 接收单位预计库存量
            - global_scheme_id: 全局方案标识
            - send_reason: 调拨原因

    Returns:
        dict: 插入结果
    """
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-insert_into_adam_allot_day_plan_pre'
        url = f"http://{host}:{port}{endpoint}"

        # 将DataFrame转换为字典列表，列名转为小写
        records = df.rename(columns=str.lower).to_dict('records')

        # 逐条插入数据
        success_count = 0
        failed_count = 0
        errors = []

        for record in records:
            try:
                response = session.post(url, json=record)
                response.raise_for_status()
                success_count += 1
            except Exception as e:
                failed_count += 1
                errors.append({
                    "record": record,
                    "error": str(e)
                })

        return {
            "success": failed_count == 0,
            "message": f"数据插入完成，成功 {success_count} 条，失败 {failed_count} 条",
            "success_count": success_count,
            "failed_count": failed_count,
            "errors": errors if errors else None
        }
    except requests.exceptions.RequestException as e:
        raise
    except Exception as e:
        raise

def delete_adam_glob_strategy_scheme_cost(scheme_id):
    """
    根据方案ID删除方案成本明细
    :param scheme_id: 方案ID
    """
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-delete_adam_glob_strategy_scheme_cost'
        url = f"http://{host}:{port}{endpoint}"

        params = {"scheme_id": scheme_id}
        response = session.post(url, json=params)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        raise


def delete_adam_glob_strategy_scheme_itt(scheme_id):
    """
    根据方案ID删除方案周转明细数据
    :param scheme_id: 方案ID
    :return: 接口返回结果
    """
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-delete_adam_glob_strategy_scheme_itt'
        url = f"http://{host}:{port}{endpoint}"

        # 构造参数
        params = {"scheme_id": scheme_id}

        # 发送删除请求
        response = session.post(url, json=params)
        response.raise_for_status()

        return response.json()

    except requests.exceptions.RequestException as e:
        raise
    except Exception as e:
        raise

def delete_adam_glob_strategy_scheme_lps(scheme_id):
    """
    根据方案ID删除方案环节计划汇总明细数据
    :param scheme_id: 方案ID
    :return: 接口返回结果
    """
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-delete_adam_glob_strategy_scheme_lps'
        url = f"http://{host}:{port}{endpoint}"

        params = {"scheme_id": scheme_id}
        response = session.post(url, json=params)
        response.raise_for_status()

        return response.json()

    except requests.exceptions.RequestException as e:
        raise
    except Exception as e:
        raise

def delete_adam_glob_strategy_scheme_by_ym(exec_ym: str):
    """
    根据执行年月删除全局策略方案主表数据
    :param exec_ym: 执行年月，格式：YYYYMM
    :return: 接口返回结果
    """
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-delete_adam_glob_strategy_scheme_by_ym'
        url = f"http://{host}:{port}{endpoint}"

        # 构造参数
        params = {"exec_ym": exec_ym}

        # 发送请求
        response = session.post(url, json=params)
        response.raise_for_status()

        return response.json()

    except requests.exceptions.RequestException as e:
        raise
    except Exception as e:
        raise

def query_unused_pur_orders():
    """
    查询未使用的采购订单批次规格。
    返回 DEV_CODE 和 ORDER_NUM（每个采购批次的只数）。
    """
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-query_unused_pur_orders'
        url = f"http://{host}:{port}{endpoint}"

        response = session.post(url, json={})
        response.raise_for_status()

        data = response.json()

        if isinstance(data, list) and len(data) == 0:
            return pd.DataFrame(columns=['DEV_CODE', 'ORDER_NUM'])

        if isinstance(data, list):
            df = pd.DataFrame(data)
        else:
            df = pd.DataFrame([data])

        return df
    except requests.exceptions.RequestException as e:
        raise
    except Exception as e:
        raise


def deleteScheme(yearMonth:str):
    '''
    删除当前年月所有方案
    '''
    logger = logging.getLogger(__name__)
    global_scheme = query_adam_glob_strategy_scheme_by_month(yearMonth)

    if global_scheme is None or global_scheme.empty:
        logger.info(f'[删除方案] 年月 {yearMonth} 无历史方案数据，无需删除')
        return

    scheme_id_list = global_scheme['SCHEME_ID'].tolist()
    logger.info(f'[删除方案] 年月 {yearMonth} 找到 {len(scheme_id_list)} 条历史方案，开始删除: {scheme_id_list}')

    for sid in scheme_id_list:
        delete_adam_glob_strategy_scheme_itt(sid)
        delete_adam_glob_strategy_scheme_lps(sid)
        delete_adam_glob_strategy_scheme_cost(sid)
        logger.info(f'[删除方案] SCHEME_ID={sid} 明细已删除')

    delete_adam_glob_strategy_scheme_by_ym(yearMonth)
    logger.info(f'[删除方案] 年月 {yearMonth} 主表已删除，共清理 {len(scheme_id_list)} 条方案')


def query_adam_run_dur_sample_by_org_no(org_no: str):
    """
    根据单位编码查询运行年限表数据
    """
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-query_adam_run_dur_sample_by_org_no'
        url = f"http://{host}:{port}{endpoint}"

        params = {"org_no": org_no}
        response = session.post(url, json=params)
        response.raise_for_status()
        data = response.json()

        if not data:
            return pd.DataFrame()

        df = pd.DataFrame(data)
        return df

    except requests.exceptions.RequestException as e:
        raise
    except Exception as e:
        raise

def query_adam_run_dur_sample_all():
    """
    查询运行年限表按照单位汇总后的所有数据
    """
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-query_adam_run_dur_sample_all'
        url = f"http://{host}:{port}{endpoint}"

        # params = {"org_no": org_no}
        params= {}
        response = session.post(url, json=params)
        response.raise_for_status()
        data = response.json()

        if not data:
            return pd.DataFrame()

        df = pd.DataFrame(data)
        return df

    except requests.exceptions.RequestException as e:
        print('获取运行年限数据失败')
        raise
    except Exception as e:
        print('获取运行年限数据失败')
        raise

def query_adam_plan_month_ias_pre(pre_year: str, pre_month: str):
    """
    根据年度、月份查询月度计划预估表
    :param pre_year: 年度，如 2025
    :param pre_month: 月份，如 05
    :return: 查询结果 DataFrame
    """
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-query_adam_plan_month_ias_pre_by_ym'
        url = f"http://{host}:{port}{endpoint}"

        params = {
            "pre_year": pre_year,
            "pre_month": pre_month
        }

        response = session.post(url, json=params)
        response.raise_for_status()
        data = response.json()

        if not data:
            return pd.DataFrame()

        df = pd.DataFrame(data)
        return df

    except Exception as e:

        raise

def query_adam_plan_day_ias_pre_by_month(data_month: str):
    """
    根据年月查询日计划预估表整月数据
    :param data_month: 格式 YYYYMM  例如 202605
    :return: 当月所有 PRE_DATE 数据
    """
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-query_adam_plan_day_ias_pre_by_month'
        url = f"http://{host}:{port}{endpoint}"

        params = {"data_month": data_month}
        response = session.post(url, json=params)
        response.raise_for_status()
        data = response.json()

        if not data:
            return pd.DataFrame()

        df = pd.DataFrame(data)
        return df

    except Exception as e:
        raise


def _query_carrier_by_date(date_str: str) -> int:
    """根据当前日期查找物流承运商编号。
    调用 gk-adam_query_log_carrier_by_curr_date，无数据时抛出异常。
    """
    host = API_CONFIG["database"]["host"]
    port = API_CONFIG["database"]["port"]
    endpoint = '/exec/gk-adam_query_log_carrier_by_curr_date'
    url = f"http://{host}:{port}{endpoint}"

    response = session.post(url, json={"query_date": date_str})
    response.raise_for_status()

    data = response.json()
    if not data or (isinstance(data, list) and len(data) == 0):
        raise ValueError(f"给定日期 {date_str} 找不到承运商")

    # 返回数据格式: 列表含单行字典，取第一行的第一个值即为承运商编号
    if isinstance(data, list):
        row = data[0]
        if isinstance(row, dict):
            return int(list(row.values())[0])
        return int(row)
    if isinstance(data, dict):
        return int(list(data.values())[0])
    return int(data)


def _query_carrier_vehicle_conf(lcc_id: int):
    """根据承运商编号查询车型配置。
    返回格式与旧 query_vehicle_conf 一致：VeCap, VNums, VeUnitPrice, VeTypeNum, VeType
    """
    import numpy as np

    host = API_CONFIG["database"]["host"]
    port = API_CONFIG["database"]["port"]
    endpoint = '/exec/gk-adam_query_log_car_van_conf_by_lccid'
    url = f"http://{host}:{port}{endpoint}"

    response = session.post(url, json={"lcc_id": lcc_id})
    response.raise_for_status()

    data = response.json()
    if not data or (isinstance(data, list) and len(data) == 0):
        raise ValueError(f"承运商 {lcc_id} 无车型配置数据")

    if isinstance(data, list):
        df = pd.DataFrame(data)
    else:
        df = pd.DataFrame([data])

    df.columns = [c.upper() for c in df.columns]
    VeCap = df['VEHICLE_CAP'].astype(int).values
    VNums = df['VEHICLE_NUM'].astype(int).values
    VeUnitPrice = df['VEHICLE_CARRI'].astype(float).values
    VeType = df['CAR_TYPE']
    VeTypeNum = len(df)

    comp_name = df['COMP_NAME'].iloc[0] if 'COMP_NAME' in df.columns else '未知'
    logging.info(f"[承运商] LCC_ID={lcc_id}, 名称={comp_name}, 车型数={VeTypeNum}")
    for i in range(VeTypeNum):
        logging.info(f"  车型={VeType.iloc[i]}, 容量={VeCap[i]}, 数量={VNums[i]}, 运价={VeUnitPrice[i]}")

    return VeCap, VNums, VeUnitPrice, VeTypeNum, VeType


def query_vehicle_conf(date_str: str = None):
    """查询车型配置信息。
    按当前日期确定承运商 → 查询承运商车型容量和运价。

    Args:
        date_str: 日期字符串 'YYYY-MM-DD'，默认今天

    Returns:
        tuple: (VeCap, VNums, VeUnitPrice, VeTypeNum, VeType)
    """
    import datetime

    if date_str is None:
        date_str = datetime.datetime.now().strftime('%Y-%m-%d')

    lcc_id = _query_carrier_by_date(date_str)
    return _query_carrier_vehicle_conf(lcc_id)


def delete_adam_dist_scheme_det_by_scheme_id(scheme_id):
    """根据配送方案ID删除配送方案明细"""
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        url = f"http://{host}:{port}/exec/gk-adam-delete_adam_dist_scheme_det_by_scheme_id"
        response = session.post(url, json={"scheme_id": scheme_id})
        response.raise_for_status()
        return response.json()
    except Exception as e:
        raise


def delete_adam_dist_scheme_by_id(scheme_id):
    """根据配送方案ID删除配送方案主表"""
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        url = f"http://{host}:{port}/exec/gk-adam-delete_adam_dist_scheme_by_id"
        response = session.post(url, json={"scheme_id": scheme_id})
        response.raise_for_status()
        return response.json()
    except Exception as e:
        raise


def delete_adam_stock_month_limit_pre_by_ym(year: str, month: str):
    """按年月删除月度库存阈值预测表"""
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        url = f"http://{host}:{port}/exec/gk-adam-delete_adam_stock_month_limit_pre_by_ym"
        response = session.post(url, json={"pre_year": year, "pre_month": month})
        response.raise_for_status()
        return response.json()
    except Exception as e:
        raise


def delete_adam_plan_month_ias_pre_by_ym(year: str, month: str):
    """按年月删除月度补库计划表"""
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        url = f"http://{host}:{port}/exec/gk-adam-delete_adam_plan_month_ias_pre_by_ym"
        response = session.post(url, json={"pre_year": year, "pre_month": month})
        response.raise_for_status()
        return response.json()
    except Exception as e:
        raise


def delete_adam_stock_week_limt_pre_by_ym(year: str, month: str):
    """按年月删除周度库存阈值预测表"""
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        url = f"http://{host}:{port}/exec/gk-adam-delete_adam_stock_week_limt_pre_by_ym"
        response = session.post(url, json={"pre_year": year, "pre_month": month})
        response.raise_for_status()
        return response.json()
    except Exception as e:
        raise


def delete_adam_plan_day_ias_pre_by_month(year_month: str):
    """按年月删除日补库计划表"""
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        url = f"http://{host}:{port}/exec/gk-adam-delete_adam_plan_day_ias_pre_by_month"
        response = session.post(url, json={"year_month": year_month})
        response.raise_for_status()
        return response.json()
    except Exception as e:
        raise


def delete_adam_allot_day_plan_pre_by_date(allot_date: str):
    """按日期删除调拨计划表"""
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        url = f"http://{host}:{port}/exec/gk-adam-delete_adam_allot_day_plan_pre_by_date"
        response = session.post(url, json={"allot_date": allot_date})
        response.raise_for_status()
        return response.json()
    except Exception as e:
        raise


def query_pk_next(pkCode: str, num: int) -> list:
    """
    查询 PK 下一批数据
    :param pkCode: PK 编码（字符串）
    :param num:    查询数量（数字）
    :return:       id 字符串列表
    """
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        url = f"http://{host}:{port}/pk/next"

        response = session.post(url, json={"pkCode": pkCode, "num": num})
        response.raise_for_status()
        data = response.json()

        if not isinstance(data, list):
            data = []
        return data

    except Exception as e:
        raise


def query_adam_realtime_pend_stock():
    """查询实时待检库存（不合格品）"""
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-query_realtime_pend_stock'
        url = f"http://{host}:{port}{endpoint}"
        response = session.post(url, json={})
        response.raise_for_status()
        data = response.json()
        if isinstance(data, list) and len(data) == 0:
            return pd.DataFrame()
        if isinstance(data, list):
            return pd.DataFrame(data)
        else:
            return pd.DataFrame([data])
    except requests.exceptions.RequestException as e:
        raise


def query_adam_realtime_qua_stock():
    """查询实时合格品库存"""
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-query_realtime_qua_stock'
        url = f"http://{host}:{port}{endpoint}"
        response = session.post(url, json={})
        response.raise_for_status()
        data = response.json()
        if isinstance(data, list) and len(data) == 0:
            return pd.DataFrame()
        if isinstance(data, list):
            return pd.DataFrame(data)
        else:
            return pd.DataFrame([data])
    except requests.exceptions.RequestException as e:
        raise


def query_adam_future_arrivals(start_bound: str, end_bound: str):
    """查询区间内未来到货量"""
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-query_future_arrivals'
        url = f"http://{host}:{port}{endpoint}"
        response = session.post(url, json={"start_bound": start_bound, "end_bound": end_bound})
        response.raise_for_status()
        data = response.json()
        if isinstance(data, list) and len(data) == 0:
            return pd.DataFrame()
        if isinstance(data, list):
            return pd.DataFrame(data)
        else:
            return pd.DataFrame([data])
    except requests.exceptions.RequestException as e:
        raise


def query_adam_future_detections(start_bound: str, end_bound: str):
    """查询区间内未来检定完成量"""
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-query_future_detections'
        url = f"http://{host}:{port}{endpoint}"
        response = session.post(url, json={"start_bound": start_bound, "end_bound": end_bound})
        response.raise_for_status()
        data = response.json()
        if isinstance(data, list) and len(data) == 0:
            return pd.DataFrame()
        if isinstance(data, list):
            return pd.DataFrame(data)
        else:
            return pd.DataFrame([data])
    except requests.exceptions.RequestException as e:
        raise


def query_adam_future_deliveries(start_bound: str, end_bound: str):
    """查询区间内已配送出库量（期间配送）"""
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-query_future_deliveries'
        url = f"http://{host}:{port}{endpoint}"
        response = session.post(url, json={"start_bound": start_bound, "end_bound": end_bound})
        response.raise_for_status()
        data = response.json()
        if isinstance(data, list) and len(data) == 0:
            return pd.DataFrame()
        if isinstance(data, list):
            return pd.DataFrame(data)
        else:
            return pd.DataFrame([data])
    except requests.exceptions.RequestException as e:
        raise


def query_adam_completed_inspections(target_month: str):
    """查询当月已检定完工量

    Args:
        target_month: 目标月份, 格式 'YYYYMM'

    Returns:
        pd.DataFrame: 包含 DEV_CODE, INSPECTED_NUM 等列
    """
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-query_completed_inspections'
        url = f"http://{host}:{port}{endpoint}"
        response = session.post(url, json={"target_month": target_month})
        response.raise_for_status()
        data = response.json()
        if isinstance(data, list) and len(data) == 0:
            return pd.DataFrame()
        if isinstance(data, list):
            return pd.DataFrame(data)
        else:
            return pd.DataFrame([data])
    except requests.exceptions.RequestException as e:
        raise


def query_adam_org_stock_sample_estimated(target_month: str):
    """
    推算目标月初库存。

    逻辑:
        输入 target_month（如 202608 = 8月初），取上月（202607 = 7月），
        从当前实时库存出发，扣除 7月剩余需求，加上 7月待配送，得到 7月底库存，
        即 8月初库存。

    公式:
        目标月初库存 = 实时库存 + 上月未来待配送 − 上月剩余需求
        上月剩余需求 = 上月需求预测 × (上月剩余天数 / 上月总天数)

    参数:
        target_month: 目标月份，格式 YYYYMM，上月必须 >= 当前月份

    Returns:
        pd.DataFrame: 列 ORG_NO, ORG_NAME, DEV_CODE, STOCK_NUM
    """
    import logging
    from datetime import datetime
    from dateutil.relativedelta import relativedelta
    import calendar

    # 0. 入参校验
    if not isinstance(target_month, str) or len(target_month) != 6 or not target_month.isdigit():
        raise ValueError(f"target_month 格式错误，需为 YYYYMM，实际: {target_month}")

    target_dt = datetime.strptime(target_month, '%Y%m')
    prev_dt = target_dt - relativedelta(months=1)
    prev_month = prev_dt.strftime('%Y%m')
    today = datetime.now()
    current_month = today.strftime('%Y%m')

    if prev_month < current_month:
        raise ValueError(
            f"无法推算: 目标月={target_month}, 上月={prev_month}, "
            f"上月早于当前月={current_month}，无法用实时库存推算"
        )

    logging.info(f'推算目标月初库存: target={target_month}, 上月={prev_month}, 当前={current_month}')

    # 1. 获取地市实时库存
    df_realtime = query_adam_realtime_stocknum()
    rt = df_realtime[['ORG_NO', 'ORG_NAME', 'DEV_CODE', 'STOCK_NUM']].copy()
    rt.rename(columns={'STOCK_NUM': 'RT_STOCK'}, inplace=True)

    # 2. 获取上月需求预测（SQL 已按 ORG_NO+DEV_CODE 聚合所有业务类型，无需再 groupby）
    year = prev_month[:4]
    month = prev_month[4:6]
    demand = query_adam_yqm_dmd_pre_by_year_month(year, month)[['ORG_NO', 'DEV_CODE', 'PRE_NUM']].copy()
    demand.rename(columns={'PRE_NUM': 'MONTHLY_DEMAND'}, inplace=True)

    # 3. 计算上月剩余需求 = 上月需求 × (上月剩余天数 / 上月总天数)
    days_in_prev = calendar.monthrange(prev_dt.year, prev_dt.month)[1]
    if today.year == prev_dt.year and today.month == prev_dt.month:
        remaining_days = days_in_prev - today.day + 1  # 含当天
    elif today > prev_dt:
        remaining_days = 0  # 上月已过完
    else:
        remaining_days = days_in_prev  # 上月还没到，全月算
    ratio = remaining_days / days_in_prev
    demand['REMAIN_DEMAND'] = demand['MONTHLY_DEMAND'] * ratio

    # 4. 获取上月未来待配送 = 上月剩余日期的日补库计划汇总
    df_plan = query_adam_plan_day_ias_pre_by_month(prev_month)
    if df_plan.empty:
        df_delivery = pd.DataFrame(columns=['ORG_NO', 'DEV_CODE', 'PENDING_DELIVERY'])
    else:
        df_plan['PRE_DATE'] = pd.to_datetime(df_plan['PRE_DATE'], errors='coerce')
        # 兼容时区：带时区则去掉，不带则不动
        if df_plan['PRE_DATE'].dt.tz is not None:
            df_plan['PRE_DATE'] = df_plan['PRE_DATE'].dt.tz_convert(None)
        today_date = pd.Timestamp(today.date())
        last_day_of_prev = pd.Timestamp(prev_dt.year, prev_dt.month, days_in_prev)
        mask_future = (df_plan['PRE_DATE'] >= today_date) & (df_plan['PRE_DATE'] <= last_day_of_prev)
        df_future = df_plan[mask_future]
        if df_future.empty:
            df_delivery = pd.DataFrame(columns=['ORG_NO', 'DEV_CODE', 'PENDING_DELIVERY'])
        else:
            df_delivery = df_future.groupby(['REC_ORG_NO', 'DEV_CODE'], as_index=False)['PLAN_IAS_NUM'].sum()
            df_delivery.rename(columns={'REC_ORG_NO': 'ORG_NO', 'PLAN_IAS_NUM': 'PENDING_DELIVERY'}, inplace=True)
            n_dates = df_future['PRE_DATE'].nunique()
            n_dup = len(df_future) - len(df_delivery)
            logging.info(f'未来待配送: {len(df_future)} 条日补库(跨{n_dates}天) → {len(df_delivery)} 条 (ORG,DEV_CODE)'
                         f'({n_dup}条同ORG+DEV合并)')

    # 5. 合并三表，计算推算月末库存 = 下月初库存
    logging.info(f'MERGE前维度: '
                 f'RT(单位={rt["ORG_NO"].nunique()},设备={rt["DEV_CODE"].nunique()},行={len(rt)}), '
                 f'需求(单位={demand["ORG_NO"].nunique()},设备={demand["DEV_CODE"].nunique()},行={len(demand)}), '
                 f'配送(单位={df_delivery["ORG_NO"].nunique() if not df_delivery.empty else 0},'
                 f'设备={df_delivery["DEV_CODE"].nunique() if not df_delivery.empty else 0},'
                 f'行={len(df_delivery)})')
    logging.info(f'RT设备码: {sorted(rt["DEV_CODE"].unique())}')
    logging.info(f'需求设备码: {sorted(demand["DEV_CODE"].unique())}')
    logging.info(f'配送设备码: {sorted(df_delivery["DEV_CODE"].unique()) if not df_delivery.empty else []}')

    result = rt.merge(demand, on=['ORG_NO', 'DEV_CODE'], how='outer') \
               .merge(df_delivery, on=['ORG_NO', 'DEV_CODE'], how='left')
    # 仅数值列填0，避免 ORG_NAME 等字符串列被填充
    num_cols = ['RT_STOCK', 'MONTHLY_DEMAND', 'REMAIN_DEMAND', 'PENDING_DELIVERY']
    for c in num_cols:
        if c in result.columns:
            result[c] = result[c].fillna(0)
    logging.info(f'MERGE后维度: 单位={result["ORG_NO"].nunique()}, 设备={result["DEV_CODE"].nunique()}, 行={len(result)}')


    result['STOCK_NUM'] = (result['RT_STOCK'] + result['PENDING_DELIVERY']
                           - result['REMAIN_DEMAND']).clip(lower=0).round(0)

    logging.info(f'推算目标月初库存完成: 实时={len(rt)}条, 需求={len(demand)}条, '
                 f'配送={len(df_delivery)}条, 结果={len(result)}条, '
                 f'上月={prev_month}, 剩余{remaining_days}/{days_in_prev}天')

    return result[['ORG_NO', 'ORG_NAME', 'DEV_CODE', 'STOCK_NUM']]


def query_aio_supply_quota_by_month(month: str):
    """查询年度剩余补库量（设备类别口径）。

    AIO_SUPPLY_QUOTA 的 DEV_CODE 列实际存的是设备类别；DS_SQL 已按业务口径
    将设备码列命名为 DEV_CATEG（设备类别）返回。

    Args:
        month: 月份，格式 YYYYMM

    Returns:
        pd.DataFrame: 列 ORG_NO, DEV_CATEG(设备类别), QUOTA(年度剩余量),
                      Y_PRE_NUM, MONTH, SUPPLY_QUOTA_ID
    """
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-query-aio-supply-quota-by-month'
        url = f"http://{host}:{port}{endpoint}"
        response = session.post(url, json={"month": month})
        response.raise_for_status()
        data = response.json()
        if isinstance(data, list):
            return pd.DataFrame(data)
        return pd.DataFrame([data]) if data else pd.DataFrame()
    except requests.exceptions.RequestException:
        logging.exception('年度剩余补库量查询网络异常')
        raise
    except Exception:
        logging.exception('年度剩余补库量查询失败')
        raise


# ============================================================
# 二阶段 (R,S) 补货算法 — 数据访问函数
# ============================================================

logger = logging.getLogger(__name__)


def query_adam_ais_sys_param():
    """查询供电所补库系统参数（ADAM_AIS_SYS_PARAM）

    Returns:
        pd.DataFrame: 列 PARAM_ID, REC_ORG_NO, REPLEISHMENT_CYCLE,
                      TARGET_CYCLE_SERVICE_LEVEL, CYCLE_BASE_START_DATE
    """
    logger.info("[RS] 查询 ADAM_AIS_SYS_PARAM...")
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-query-adam-ais-sys-param'
        url = f"http://{host}:{port}{endpoint}"
        response = session.post(url, json={})
        response.raise_for_status()
        data = response.json()
        if isinstance(data, list) and len(data) == 0:
            raise ValueError("ADAM_AIS_SYS_PARAM 返回数据为空")
        if isinstance(data, list):
            df = pd.DataFrame(data)
        else:
            df = pd.DataFrame([data])
        logger.info(f"[RS] ADAM_AIS_SYS_PARAM 查询成功: {len(df)} 条, "
                    f"供电所 {len(df[df['REC_ORG_NO'] != '0000'])} 个 + 默认 1 条")
        return df
    except requests.exceptions.RequestException:
        logger.exception("[RS] ADAM_AIS_SYS_PARAM 查询网络异常")
        raise
    except Exception:
        logger.exception("[RS] ADAM_AIS_SYS_PARAM 查询失败")
        raise


def query_adam_city_county_stock_sample(data_date: str):
    """根据数据日期查询市县公司库存快照（ADAM_CITY_COUNTY_STOCK_SAMPLE）

    Args:
        data_date: 数据日期，格式 'YYYY-MM-DD'

    Returns:
        pd.DataFrame
    """
    logger.info(f"[RS] 查询 ADAM_CITY_COUNTY_STOCK_SAMPLE (DATA_DATE={data_date})...")
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-query-adam-city-county-stock-sample'
        url = f"http://{host}:{port}{endpoint}"
        json_data = {"data_date": data_date}
        response = session.post(url, json=json_data)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, list) and len(data) == 0:
            logger.warning(f"[RS] 市县库存快照返回数据为空 (DATA_DATE={data_date})")
            return pd.DataFrame()
        if isinstance(data, list):
            df = pd.DataFrame(data)
        else:
            df = pd.DataFrame([data])
        logger.info(f"[RS] 库存快照查询成功: {len(df)} 条, "
                    f"供电所 {df['ORG_NO'].nunique()} 个")
        return df
    except requests.exceptions.RequestException:
        logger.exception(f"[RS] 库存快照查询网络异常 (DATA_DATE={data_date})")
        raise
    except Exception:
        logger.exception(f"[RS] 库存快照查询失败 (DATA_DATE={data_date})")
        raise


def query_adam_sub_dmd_pre(pre_type: str, start_date: str, end_date: str):
    """查询供电所日需求预测（ADAM_SUB_DMD_PRE），按日期范围 + 预测类型筛选

    Args:
        pre_type: 预测类型，'05' 为日预测
        start_date: 起始日期，格式 'YYYY-MM-DD'
        end_date: 截止日期，格式 'YYYY-MM-DD'

    Returns:
        pd.DataFrame: 列 SUB_DMD_PRE_ID, PRE_TYPE, PRE_DATE, BUS_TYPE, ORG_NO, DEV_CODE, PRE_NUM 等
    """
    logger.info(f"[RS] 查询 ADAM_SUB_DMD_PRE (PRE_TYPE={pre_type}, {start_date} ~ {end_date})...")
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-query-adam-sub-dmd-pre'
        url = f"http://{host}:{port}{endpoint}"
        json_data = {
            "pre_type": pre_type,
            "start_date": start_date,
            "end_date": end_date,
        }
        response = session.post(url, json=json_data)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, list) and len(data) == 0:
            logger.warning(f"[RS] 供电所需求预测返回数据为空 (PRE_TYPE={pre_type}, {start_date} ~ {end_date})")
            return pd.DataFrame()
        if isinstance(data, list):
            df = pd.DataFrame(data)
        else:
            df = pd.DataFrame([data])
        logger.info(f"[RS] 需求预测查询成功: {len(df)} 条, "
                    f"供电所 {df['ORG_NO'].nunique()} 个, "
                    f"日期 {df['PRE_DATE'].nunique()} 天")
        return df
    except requests.exceptions.RequestException:
        logger.exception(f"[RS] 需求预测查询网络异常 (PRE_TYPE={pre_type}, {start_date} ~ {end_date})")
        raise
    except Exception:
        logger.exception(f"[RS] 需求预测查询失败 (PRE_TYPE={pre_type}, {start_date} ~ {end_date})")
        raise


def query_adam_sub_dmd_pre_grid(pre_type: str, start_date: str, end_date: str):
    """查询供电所日需求预测（全网格版），对齐月度补库口径

    返回 有效供电所(DIST_LV=05) × 启用设备码(PRE_RANGE_INFO STAT=01) × 日期 的全组合，
    LEFT JOIN 实际需求，缺失补 0。这样算法维度 = 供电所数 × 启用设备码数，
    而非只遍历需求表里实际存在的组合。

    Args:
        pre_type: 预测类型，'05' 为日预测
        start_date: 起始日期，格式 'YYYY-MM-DD'
        end_date: 截止日期，格式 'YYYY-MM-DD'

    Returns:
        pd.DataFrame: 列 ORG_NO, DEV_CODE, PRE_DATE, PRE_NUM
    """
    logger.info(f"[RS] 查询 ADAM_SUB_DMD_PRE 全网格 (PRE_TYPE={pre_type}, {start_date} ~ {end_date})...")
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-query-adam-sub-dmd-pre-grid'
        url = f"http://{host}:{port}{endpoint}"
        json_data = {
            "pre_type": pre_type,
            "start_date": start_date,
            "end_date": end_date,
        }
        response = session.post(url, json=json_data)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, list) and len(data) == 0:
            logger.warning(f"[RS] 供电所需求预测全网格返回数据为空 (PRE_TYPE={pre_type}, {start_date} ~ {end_date})")
            return pd.DataFrame()
        if isinstance(data, list):
            df = pd.DataFrame(data)
        else:
            df = pd.DataFrame([data])
        df['PRE_NUM'] = df['PRE_NUM'].fillna(0).astype(float)
        logger.info(f"[RS] 需求预测全网格查询成功: {len(df)} 条, "
                    f"供电所 {df['ORG_NO'].nunique()} 个, "
                    f"设备码 {df['DEV_CODE'].nunique()} 个, "
                    f"日期 {df['PRE_DATE'].nunique()} 天")
        return df
    except requests.exceptions.RequestException:
        logger.exception(f"[RS] 需求预测全网格查询网络异常 (PRE_TYPE={pre_type}, {start_date} ~ {end_date})")
        raise
    except Exception:
        logger.exception(f"[RS] 需求预测全网格查询失败 (PRE_TYPE={pre_type}, {start_date} ~ {end_date})")
        raise


def insert_into_adam_replenish_order(df: pd.DataFrame):
    """批量插入补货建议数据到 ADAM_REPLENISH_ORDER 表

    Args:
        df: DataFrame，包含以下列：
            - ORDER_ID: 主键
            - ORG_NO: 供电所编码
            - DEV_CLS: 设备分类
            - DEV_CATEG: 设备类别
            - DEV_CODE: 设备码
            - REPLENISH_QTY: 建议补货量
            - TARGET_STOCK_S: 基准库存 S
            - CAL_DATE: 计算日期
            - CREATE_TIME: 记录创建时间

    Returns:
        dict: 插入结果
    """
    import math
    batch_size = 100
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-insert-into-adam-replenish-order'
        url = f"http://{host}:{port}{endpoint}"

        # NaN/Inf / 日期序列化处理
        import datetime as _dt
        df = df.astype(object).where(df.notna(), None)
        records = df.rename(columns=str.lower).to_dict('records')
        for r in records:
            for k, v in r.items():
                if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                    r[k] = None
                elif isinstance(v, (_dt.datetime, pd.Timestamp)):
                    # datetime/Timestamp → 'YYYY-MM-DD HH:MM:SS'（中间件 TIMESTAMP 无法解析 ISO T 格式）
                    r[k] = v.strftime('%Y-%m-%d %H:%M:%S')
                elif isinstance(v, _dt.date):
                    # 纯日期 → 'YYYY-MM-DD'
                    r[k] = v.isoformat()

        total = len(records)
        batches = math.ceil(total / batch_size)
        logger.info(f"[RS] 批量插入 ADAM_REPLENISH_ORDER: 共 {total} 条, 分 {batches} 批(每批 {batch_size} 条)")

        success_count = 0
        failed_count = 0
        errors = []

        for i in range(0, total, batch_size):
            chunk = records[i:i + batch_size]
            batch_no = i // batch_size + 1
            try:
                response = session.post(url, json=chunk)
                response.raise_for_status()
                success_count += len(chunk)
                logger.info(f"[RS] 批量插入 ADAM_REPLENISH_ORDER 第 {batch_no}/{batches} 批成功, {len(chunk)} 条")
            except Exception as e:
                failed_count += len(chunk)
                errors.append({"batch": batch_no, "count": len(chunk), "error": str(e)})
                logger.error(f"[RS] 批量插入 ADAM_REPLENISH_ORDER 第 {batch_no}/{batches} 批失败: {e}")

        result = {
            "success": failed_count == 0,
            "message": f"补货建议批量插入完成, 成功 {success_count} 条, 失败 {failed_count} 条",
            "success_count": success_count,
            "failed_count": failed_count,
            "batches": batches,
            "errors": errors if errors else None
        }
        logger.info(f"[RS] 补货建议插入完毕: 成功 {success_count}/{total} 条")
        return result
    except requests.exceptions.RequestException:
        logger.exception("[RS] 批量插入 ADAM_REPLENISH_ORDER 网络异常")
        raise
    except Exception:
        logger.exception("[RS] 批量插入 ADAM_REPLENISH_ORDER 失败")
        raise


def delete_adam_replenish_order_by_date(cal_date: str):
    """按补货日(CAL_DATE)删除旧的补货建议记录，防止重复插入

    Args:
        cal_date: 补货日期字符串，如 '2026-08-05'
    """
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-delete-adam-replenish-order-by-date'
        url = f"http://{host}:{port}{endpoint}"
        response = session.post(url, json={"cal_date": cal_date})
        response.raise_for_status()
        data = response.json()
        logger.info(f"供电所补货-删除补货日({cal_date})旧建议: {data}")
        return data
    except requests.exceptions.RequestException:
        logger.exception("供电所补货-删除旧建议网络异常")
        raise
    except Exception:
        logger.exception("供电所补货-删除旧建议失败")
        raise


def insert_into_adam_model_think_log(model_think_log_id: int, pre_conc_id: int,
                                     model_no: str, think_log: str):
    """插入单条模型思考日志到 ADAM_MODEL_THINK_LOG 表，自动补齐创建时间

    Args:
        model_think_log_id: 主键，模型思考日志唯一标识
        pre_conc_id: 预测结论唯一标识（外键）
        model_no: 模型编号
        think_log: 思考日志内容（max 4000）

    Returns:
        dict: 插入结果
    """
    from datetime import datetime

    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-insert-into-adam-model-think-log'
        url = f"http://{host}:{port}{endpoint}"

        json_data = {
            "model_think_log_id": model_think_log_id,
            "pre_conc_id": pre_conc_id,
            "model_no": model_no,
            "think_log": think_log,
            "create_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        logger.info(f"插入 ADAM_MODEL_THINK_LOG: ID={model_think_log_id}, MODEL={model_no}")
        response = session.post(url, json=json_data)
        response.raise_for_status()
        logger.info(f"ADAM_MODEL_THINK_LOG 插入成功: ID={model_think_log_id}")
        return {"success": True, "message": f"模型思考日志插入成功, ID={model_think_log_id}"}

    except requests.exceptions.RequestException:
        logger.exception(f"插入 ADAM_MODEL_THINK_LOG 网络异常: ID={model_think_log_id}")
        raise
    except Exception:
        logger.exception(f"插入 ADAM_MODEL_THINK_LOG 失败: ID={model_think_log_id}")
        raise


# ============================================================
# 仓网布局优化 — 数据访问函数
# ============================================================


def query_adam_station_demand_mapped():
    """查询供电所年需求（含新旧码映射、有效设备筛选、单价、规格）

    Returns:
        pd.DataFrame: STATION_ORG_CODE, DEV_CODE, DEV_CLS, DEV_CATEG,
                      DEV_CODE_DESC, AVG_PRICE, ANNUAL_DEMAND
    """
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-query-adam-station-demand-mapped'
        url = f"http://{host}:{port}{endpoint}"
        response = session.post(url, json={})
        response.raise_for_status()
        data = response.json()
        if isinstance(data, list) and len(data) == 0:
            raise ValueError("年需求查询返回数据为空")
        df = pd.DataFrame(data) if isinstance(data, list) else pd.DataFrame([data])
        logger.info(f"仓网布局-年需求(映射后): {len(df)} 行, "
                     f"{df['DEV_CODE'].nunique()} 设备码, {df['STATION_ORG_CODE'].nunique()} 供电所")
        return df
    except requests.exceptions.RequestException:
        logger.exception("仓网布局-年需求查询网络异常")
        raise
    except Exception:
        logger.exception("仓网布局-年需求查询失败")
        raise


def query_adam_warehouse_candidate():
    """查询候选库房列表

    Returns:
        pd.DataFrame: WH_ID, ORG_NO, WH_NAME, WH_LON, WH_LAT,
                      WH_ADDR, FIXED_COST_F, TRANS_DIST, IS_ACTIVE
    """
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-query-adam-warehouse-candidate'
        url = f"http://{host}:{port}{endpoint}"
        response = session.post(url, json={})
        response.raise_for_status()
        data = response.json()
        if isinstance(data, list) and len(data) == 0:
            raise ValueError("候选库房查询返回数据为空")
        df = pd.DataFrame(data) if isinstance(data, list) else pd.DataFrame([data])
        logger.info(f"仓网布局-候选库房: {len(df)} 个")
        return df
    except requests.exceptions.RequestException:
        logger.exception("仓网布局-候选库房查询网络异常")
        raise
    except Exception:
        logger.exception("仓网布局-候选库房查询失败")
        raise


def query_adam_power_station_active():
    """查询活跃供电所列表（不按组织结构表过滤，只查活跃供电所）

    Returns:
        pd.DataFrame: STATION_ID, STATION_ORG_CODE, STATION_NAME, WH_ID,
                      STATION_ADDR, STATION_LON, STATION_LAT, ORG_NO, IS_ACTIVE
        WH_ID: 供电所服务点唯一键（存在重复 STATION_ORG_CODE 时用于区分）
    """
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-query-adam-power-station-active'
        url = f"http://{host}:{port}{endpoint}"
        response = session.post(url, json={})
        response.raise_for_status()
        data = response.json()
        if isinstance(data, list) and len(data) == 0:
            raise ValueError("供电所查询返回数据为空")
        df = pd.DataFrame(data) if isinstance(data, list) else pd.DataFrame([data])
        unique = df['STATION_ORG_CODE'].nunique()
        logger.info(f"仓网布局-活跃供电所: {len(df)} 行, {unique} 个唯一编码")
        return df
    except requests.exceptions.RequestException:
        logger.exception("仓网布局-供电所查询网络异常")
        raise
    except Exception:
        logger.exception("仓网布局-供电所查询失败")
        raise


def query_adam_station_dist_mist():
    """查询供电所与各市县距离矩阵

    Returns:
        pd.DataFrame: STATION_MIST_ID, ORG_NO, STATION_ORG_CODE, WH_ID, DISTANCE
        WH_ID: 供电所服务点唯一键（与 ADAM_POWER_STATION.WH_ID 对应）
    """
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-query-adam-station-dist-mist'
        url = f"http://{host}:{port}{endpoint}"
        response = session.post(url, json={})
        response.raise_for_status()
        data = response.json()
        if isinstance(data, list) and len(data) == 0:
            raise ValueError("距离矩阵查询返回数据为空")
        df = pd.DataFrame(data) if isinstance(data, list) else pd.DataFrame([data])
        logger.info(f"仓网布局-距离矩阵: {len(df)} 行, "
                     f"{df['ORG_NO'].nunique()} 库房, {df['STATION_ORG_CODE'].nunique()} 供电所")
        return df
    except requests.exceptions.RequestException:
        logger.exception("仓网布局-距离矩阵查询网络异常")
        raise
    except Exception:
        logger.exception("仓网布局-距离矩阵查询失败")
        raise


def insert_adam_layout_result(df: pd.DataFrame):
    """插入仓网布局结果主表

    Args:
        df: DataFrame, 列 RESULT_ID, SCENARIO_CODE, WEIGHT,
            OBJECTIVE_COST, OBJECTIVE_DIST, CREATE_TIME

    Returns:
        dict: 插入结果
    """
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-insert-adam-layout-result'
        url = f"http://{host}:{port}{endpoint}"
        records = df.rename(columns=str.lower).to_dict('records')
        success_count = 0
        failed_count = 0
        for record in records:
            try:
                response = session.post(url, json=record)
                response.raise_for_status()
                success_count += 1
            except Exception as e:
                failed_count += 1
                logger.error(f"仓网布局-结果主表插入失败: {e}")
        logger.info(f"仓网布局-结果主表插入完成: 成功 {success_count}, 失败 {failed_count}")
        return {
            "success": failed_count == 0,
            "message": f"结果主表插入完成, 成功 {success_count} 条, 失败 {failed_count} 条",
            "success_count": success_count,
            "failed_count": failed_count,
        }
    except requests.exceptions.RequestException:
        logger.exception("仓网布局-结果主表插入网络异常")
        raise
    except Exception:
        logger.exception("仓网布局-结果主表插入失败")
        raise


def insert_adam_layout_result_det(df: pd.DataFrame):
    """插入仓网布局结果明细表

    Args:
        df: DataFrame, 列 RESULT_DET_ID, RESULT_ID, SCENARIO_CODE,
            ORG_NO, STATION_ORG_CODE, CREATE_TIME

    Returns:
        dict: 插入结果
    """
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-insert-adam-layout-result-det'
        url = f"http://{host}:{port}{endpoint}"
        records = df.rename(columns=str.lower).to_dict('records')
        success_count = 0
        failed_count = 0
        for record in records:
            try:
                response = session.post(url, json=record)
                response.raise_for_status()
                success_count += 1
            except Exception as e:
                failed_count += 1
                logger.error(f"仓网布局-结果明细插入失败: {e}")
        logger.info(f"仓网布局-结果明细插入完成: 成功 {success_count}, 失败 {failed_count}")
        return {
            "success": failed_count == 0,
            "message": f"结果明细插入完成, 成功 {success_count} 条, 失败 {failed_count} 条",
            "success_count": success_count,
            "failed_count": failed_count,
        }
    except requests.exceptions.RequestException:
        logger.exception("仓网布局-结果明细插入网络异常")
        raise
    except Exception:
        logger.exception("仓网布局-结果明细插入失败")
        raise


def query_adam_sys_param() -> dict:
    """查询仓网布局系统参数

    Returns:
        dict: {PARAM_CODE: PARAM_VALUE}
    """
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-query-adam-sys-param'
        url = f"http://{host}:{port}{endpoint}"
        response = session.post(url, json={})
        response.raise_for_status()
        data = response.json()
        if isinstance(data, list):
            params = {}
            for row in data:
                code = row.get('PARAM_CODE', '')
                value = row.get('PARAM_VALUE', '')
                if code.startswith('WL_'):
                    params[code] = value
            logger.info(f"仓网布局-系统参数: 读取 {len(params)} 项")
            return params
        return {}
    except requests.exceptions.RequestException:
        logger.exception("仓网布局-系统参数查询网络异常")
        raise
    except Exception:
        logger.exception("仓网布局-系统参数查询失败")
        raise


def delete_adam_layout_result_by_date(today: str):
    """按日期删除旧的仓网布局方案主表

    Args:
        today: 日期字符串，如 '2026-08-04'
    """
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-delete-adam-layout-result-by-date'
        url = f"http://{host}:{port}{endpoint}"
        response = session.post(url, json={"today": today})
        response.raise_for_status()
        data = response.json()
        logger.info(f"仓网布局-删除旧方案主表 (>= {today}): {data}")
        return data
    except requests.exceptions.RequestException:
        logger.exception("仓网布局-删除方案主表网络异常")
        raise
    except Exception:
        logger.exception("仓网布局-删除方案主表失败")
        raise


def delete_adam_layout_result_det_by_date(today: str):
    """按日期删除旧的仓网布局方案明细表

    Args:
        today: 日期字符串，如 '2026-08-04'
    """
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-delete-adam-layout-result-det-by-date'
        url = f"http://{host}:{port}{endpoint}"
        response = session.post(url, json={"today": today})
        response.raise_for_status()
        data = response.json()
        logger.info(f"仓网布局-删除旧方案明细表 (>= {today}): {data}")
        return data
    except requests.exceptions.RequestException:
        logger.exception("仓网布局-删除方案明细表网络异常")
        raise
    except Exception:
        logger.exception("仓网布局-删除方案明细表失败")
        raise
