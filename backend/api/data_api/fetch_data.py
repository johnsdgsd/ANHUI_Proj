from numbers import Number
import requests
import pandas as pd
from backend.config.config import API_CONFIG


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
        endpoint = '/exec/gk-adam-query-aps-inventory-item-cost'
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
        endpoint = '/exec/gk-adam-query-aps-inventory-init-stock-by-month'
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
        endpoint = '/exec/gk-adam-insert-into-aps-inventory-fulfill-rate'
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
        endpoint = '/exec/gk-adam-insert-into-aps-inventory-replenish'
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
        endpoint = '/exec/gk-adam-insert-into-aps-inventory-replenish-qty'
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
        endpoint = '/exec/gk-adam-query-aps-qua-sto-by-month'
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
        endpoint = '/exec/gk-adam-query-aps-unqua-sto-by-month'
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
        endpoint = '/exec/gk-adam-query_adam_dist_scheme_by_date_range'
        url = f"http://{host}:{port}{endpoint}"
        json = {
            "start_date":start_date,
            "end_date":end_date
        }
        response = requests.post(url, json=json)
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
        response = requests.post(url, json=json)
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
        response = requests.post(url, json=json)
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
        response = requests.post(url, json=json)
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
        response = requests.post(url, json=json)
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
        response = requests.post(url, json=json)
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
        response = requests.post(url, json=json)
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
        response = requests.post(url, json=json)
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
        response = requests.post(url, json=json)
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
        response = requests.post(url, json=json)
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
        
        response = requests.post(url, json=json)
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
        
        response = requests.post(url, json=json)
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

        response = requests.post(url, json=json_data)
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

        response = requests.post(url, json=json_data)
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
        
        response = requests.post(url, json=json_data)
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
        
        response = requests.post(url, json={})
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
        response = requests.post(url, json={})
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

        response = requests.post(url, json=json_data)
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

def insert_into_adam_glob_strategy_scheme(df: pd.DataFrame):
    """插入全局策略方案主表数据到数据库

    Args:
        df: DataFrame，包含以下列：
            - SCHEME_ID: 方案标识
            - SCHEME_NO: 方案编号
            - SCHEME_NAME: 方案名称
            - SCHEME_FOCUS: 方案侧重(01成本优先,02库存周转优先,03均衡分布)
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
        response = requests.post(url, json={})
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

        response = requests.post(url, json=params)
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
        response = requests.post(url, json=params)
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
        response = requests.post(url, json=params)
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
        response = requests.post(url, json=params)
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
        response = requests.post(url, json=params)
        response.raise_for_status()

        return response.json()

    except requests.exceptions.RequestException as e:
        raise
    except Exception as e:
        raise

def deleteScheme(yearMonth:str):
    '''
    删除当前年月所有方案
    '''
    global_scheme = query_adam_glob_strategy_scheme_by_month(yearMonth)

    if global_scheme is None or global_scheme.empty :
        print('数据为空无需删除')
        return

    scheme_id_list = global_scheme['SCHEME_ID'].tolist()

    for id in scheme_id_list:
        delete_adam_glob_strategy_scheme_itt(id)
        delete_adam_glob_strategy_scheme_lps(id)
        delete_adam_glob_strategy_scheme_cost(id)

    delete_adam_glob_strategy_scheme_by_ym(yearMonth)
    print('方案数据删除成功')


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
        response = requests.post(url, json=params)
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
        response = requests.post(url, json=params)
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

        response = requests.post(url, json=params)
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
        response = requests.post(url, json=params)
        response.raise_for_status()
        data = response.json()

        if not data:
            return pd.DataFrame()

        df = pd.DataFrame(data)
        return df

    except Exception as e:
        raise


def query_vehicle_conf():
    """查询车型配置信息，返回 VeCap, VNums, VeUnitPrice, VeTypeNum。
    数据库查询为空时使用默认配置。

    Returns:
        tuple: (VeCap, VNums, VeUnitPrice, VeTypeNum)
    """
    import numpy as np

    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        endpoint = '/exec/gk-adam-query_vehicle_conf'
        url = f"http://{host}:{port}{endpoint}"

        response = requests.post(url, json={})
        response.raise_for_status()

        data = response.json()

        if not data or (isinstance(data, list) and len(data) == 0):
            VeCap = np.array([459, 901, 1071])
            VNums = np.array([9, 10, 6])
            VeUnitPrice = np.array([0.0695, 0.0695, 0.0695])
            VeTypeNum = 3
            VeType = ['03','02','01']
        else:
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

        return VeCap, VNums, VeUnitPrice, VeTypeNum,VeType

    except requests.exceptions.RequestException as e:
        raise
    except Exception as e:
        raise


def delete_adam_dist_scheme_det_by_scheme_id(scheme_id):
    """根据配送方案ID删除配送方案明细"""
    try:
        host = API_CONFIG["database"]["host"]
        port = API_CONFIG["database"]["port"]
        url = f"http://{host}:{port}/exec/gk-adam-delete_adam_dist_scheme_det_by_scheme_id"
        response = requests.post(url, json={"scheme_id": scheme_id})
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
        response = requests.post(url, json={"scheme_id": scheme_id})
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
        response = requests.post(url, json={"pre_year": year, "pre_month": month})
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
        response = requests.post(url, json={"pre_year": year, "pre_month": month})
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
        response = requests.post(url, json={"pre_year": year, "pre_month": month})
        response.raise_for_status()
        return response.json()
    except Exception as e:
        raise
