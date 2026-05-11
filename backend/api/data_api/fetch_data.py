from numbers import Number
import requests
import pandas as pd
from backend.config.config import API_CONFIG


def query_device_install_data_by_month_range(start_month:int,end_month: int):
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/query-aps-device-install-by-month-range'
        url = f"http://{host}:{port}{endpoint}"
        json = {
            "start_month":start_month,
            "end_month":end_month
        }
        response = requests.post(url, json=json)
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
        endpoint = '/exec/query-aps-inventory-item-cost'
        url = f"http://{host}:{port}{endpoint}"
        
        response = requests.post(url, json={})
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
        endpoint = '/exec/query-aps-inventory-init-stock-by-month'
        url = f"http://{host}:{port}{endpoint}"
        json = {
            "month": month
        }
        response = requests.post(url, json=json)
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
        endpoint = '/exec/insert-into-aps-inventory-fulfill-rate'
        url = f"http://{host}:{port}{endpoint}"
        
        # 将DataFrame转换为字典列表，列名转为小写
        records = df.rename(columns=str.lower).to_dict('records')
        
        for record in records:
            response = requests.post(url, json=record)
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
        endpoint = '/exec/insert-into-aps-inventory-replenish'
        url = f"http://{host}:{port}{endpoint}"
        
        # 将DataFrame转换为字典列表，列名转为小写
        records = df.rename(columns=str.lower).to_dict('records')
        
        for record in records:
            response = requests.post(url, json=record)
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
        endpoint = '/exec/insert-into-aps-inventory-replenish-qty'
        url = f"http://{host}:{port}{endpoint}"
        
        # 将DataFrame转换为字典列表，列名转为小写
        records = df.rename(columns=str.lower).to_dict('records')
        
        for record in records:
            response = requests.post(url, json=record)
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
        endpoint = '/exec/insert_into_adam_plan_day_ias_pre'
        url = f"http://{host}:{port}{endpoint}"

        # 将DataFrame转换为字典列表，列名转为小写
        records = df.rename(columns=str.lower).to_dict('records')

        # 逐条插入数据
        success_count = 0
        failed_count = 0
        errors = []

        for record in records:
            try:
                response = requests.post(url, json=record)
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
        endpoint = '/exec/query-aps-qua-sto-by-month'
        url = f"http://{host}:{port}{endpoint}"
        json = {
            "rele_month":rele_month
        }
        response = requests.post(url, json=json)
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
        endpoint = '/exec/query-aps-unqua-sto-by-month'
        url = f"http://{host}:{port}{endpoint}"
        json = {
            "rele_month":rele_month
        }
        response = requests.post(url, json=json)
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
        endpoint = '/exec/query_adam_dist_scheme_by_date_range'
        url = f"http://{host}:{port}{endpoint}"
        json = {
            "start_date":start_date,
            "end_date":end_date
        }
        response = requests.post(url, json=json)
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

def query_adam_dist_scheme_det_by_distschemeid(id:Number):
    '''
    根据配送计划id查询配送计划明细表
    '''
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/query_adam_dist_scheme_det_by_distschemeid'
        url = f"http://{host}:{port}{endpoint}"
        json = {
            "id":id
        }
        response = requests.post(url, json=json)
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

def query_adam_pre_range_info():
    '''
    查询预测范围数据
    '''
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/query_adam_pre_range_info'
        url = f"http://{host}:{port}{endpoint}"
        json = {}
        response = requests.post(url, json=json)
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

def query_adam_qua_stock_sample_by_year_month(year:str, month:str):
    """
    通过年月时间查询合格品库存数据
    """
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/query_adam_qua_stock_sample_by_year_month'
        url = f"http://{host}:{port}{endpoint}"
        json = {
            "year":year,
            "month":month
        }
        response = requests.post(url, json=json)
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

def query_adam_pend_stock_sample_by_year_month(year:str, month:str):
    """
    通过年月时间查询待检库存数据
    """
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/query_adam_pend_stock_sample_by_year_month'
        url = f"http://{host}:{port}{endpoint}"
        json = {
            "year":year,
            "month":month
        }
        response = requests.post(url, json=json)
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


def query_adam_wd_dmd_pre_by_year_month_and_pretype(year:str, month:str, pre_type:str):
    """
    通过年月以及预测类型查询周/日市县需求预测结果
    """
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/query_adam_wd_dmd_pre_by_year_month_and_pretype'
        url = f"http://{host}:{port}{endpoint}"
        json = {
            "year":year,
            "month":month,
            "pre_type":pre_type
        }
        response = requests.post(url, json=json)
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

def query_adam_y_mgt_org():
    '''
    查询组织架构信息
    '''
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/query_adam_y_mgt_org'
        url = f"http://{host}:{port}{endpoint}"
        json = {}
        response = requests.post(url, json=json)
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

def query_adam_spec_code_config():
    '''
    查询规格设备码信息
    '''
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/query_adam_spec_code_config'
        url = f"http://{host}:{port}{endpoint}"
        json = {}
        response = requests.post(url, json=json)
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



def query_adam_del_site_conf():
    '''
    查询配送站点信息
    '''
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/query_adam_del_site_conf'
        url = f"http://{host}:{port}{endpoint}"
        json = {}
        response = requests.post(url, json=json)
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


def query_adam_plan_day_ias_pre_by_date(date:str):
    '''
    根据日期查询日补库计划
    '''
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/query_adam_plan_day_ias_pre_by_date'
        url = f"http://{host}:{port}{endpoint}"
        json = {
            "date":date
        }
        response = requests.post(url, json=json)
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
        endpoint = '/exec/insert_into_adam_dist_scheme'
        url = f"http://{host}:{port}{endpoint}"

        # 将DataFrame转换为字典列表，列名转为小写
        records = df.rename(columns=str.lower).to_dict('records')

        # 逐条插入数据
        success_count = 0
        failed_count = 0
        errors = []

        for record in records:
            try:
                response = requests.post(url, json=record)
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
        endpoint = '/exec/insert_into_adam_dist_scheme_det'
        url = f"http://{host}:{port}{endpoint}"

        # 将DataFrame转换为字典列表，列名转为小写
        records = df.rename(columns=str.lower).to_dict('records')

        # 逐条插入数据
        success_count = 0
        failed_count = 0
        errors = []

        for record in records:
            try:
                response = requests.post(url, json=record)
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