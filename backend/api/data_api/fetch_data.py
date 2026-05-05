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