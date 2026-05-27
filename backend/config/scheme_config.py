"""
方案配置持久化模块
保存全局调优生成的方案ID与epsilon映射，供下游算法查找审批通过的方案。
"""
import json
import os

CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))


def _config_path(year_month: str) -> str:
    return os.path.join(CONFIG_DIR, f"scheme_epsilons_{year_month}.json")


def save_scheme_epsilons(year_month: str, scheme_map: dict):
    """保存 tag -> epsilon 映射到文件。scheme_map: {tag: epsilon}"""
    path = _config_path(year_month)
    data = {str(k): v for k, v in scheme_map.items()}
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"方案epsilon配置已保存: {path}, 共 {len(data)} 套方案")


def get_approved_scheme_config(year_month: str):
    """查找审批通过的方案，返回 (global_scheme_id, epsilon)。
    先从DB查APPR_RSLT='01'的SCHEME_ID，再从文件匹配epsilon。
    若未找到，返回默认值 (时间戳ID, 0.99)。
    """
    import time

    default_id = int(time.time() * 1000)
    default_epsilon = 0.99

    try:
        from backend.api.data_api.fetch_data import query_adam_glob_strategy_scheme_by_month

        df = query_adam_glob_strategy_scheme_by_month(year_month)
        if df is None or df.empty:
            print(f"未找到 {year_month} 的全局方案数据，使用默认ID={default_id}, epsilon={default_epsilon}")
            return default_id, default_epsilon

        approved = df[df['APPR_RSLT'] == '01']
        if approved.empty:
            print(f"{year_month} 尚无审批通过的方案，使用默认ID={default_id}, epsilon={default_epsilon}")
            return default_id, default_epsilon

        scheme_id = int(approved.iloc[0]['SCHEME_ID'])

        # 从文件匹配 epsilon
        path = _config_path(year_month)
        if not os.path.exists(path):
            print(f"epsilon配置文件不存在: {path}，使用默认epsilon={default_epsilon}")
            return scheme_id, default_epsilon

        with open(path, 'r', encoding='utf-8') as f:
            scheme_map = json.load(f)

        epsilon = scheme_map.get(str(scheme_id), default_epsilon)
        print(f"找到审批通过方案: SCHEME_ID={scheme_id}, epsilon={epsilon}")
        return scheme_id, epsilon

    except Exception as e:
        print(f"获取审批方案配置失败: {e}，使用默认ID={default_id}, epsilon={default_epsilon}")
        return default_id, default_epsilon
