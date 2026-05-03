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



