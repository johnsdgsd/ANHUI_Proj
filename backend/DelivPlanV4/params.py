# -*- coding: utf-8 -*-
"""DelivPlanV4 运行时配送参数读取。

参数来源优先级：显式入参 > ADAM_SYS_PARAM > 模块默认值。
- GK_MAX_SITE_NUM -> max_stops（钳制到 1~4，默认 3）
- GK_DIST_MIST    -> gk_dist_mist（非负，默认 10.0 km）

只在每次 run_deliv_plan_v4 启动时读取一次，不做全局缓存，
保证运维修改参数表后下一次运行立即生效。
"""
import logging

from backend.DelivPlanV4.config import (
    DEFAULT_GK_DIST_MIST,
    DEFAULT_MAX_STOPS,
    MAX_STOPS_LIMIT,
)

logger = logging.getLogger(__name__)


def _to_param_dict(raw):
    """把 reader 返回值统一成 {PARAM_CODE: PARAM_VALUE}。"""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, list):
        return {
            str(r.get('PARAM_CODE', '')).strip(): r.get('PARAM_VALUE')
            for r in raw if isinstance(r, dict)
        }
    if hasattr(raw, 'iterrows'):
        return {
            str(r.get('PARAM_CODE', '')).strip(): r.get('PARAM_VALUE')
            for _, r in raw.iterrows()
        }
    return {}


def _clamp_max_stops(value, default=DEFAULT_MAX_STOPS):
    try:
        if value is None or str(value).strip() == '':
            return default
        return max(1, min(int(float(value)), MAX_STOPS_LIMIT))
    except (TypeError, ValueError, OverflowError):
        return default


def _clamp_dist(value, default=DEFAULT_GK_DIST_MIST):
    try:
        if value is None or str(value).strip() == '':
            return default
        return max(0.0, float(value))
    except (TypeError, ValueError, OverflowError):
        return default


def load_delivery_params(reader=None):
    """读取并钳制 V4 配送参数。

    Args:
        reader: 可选，返回 dict / list[dict] / DataFrame 的可调用对象；
                默认使用 fetch_data.query_adam_sys_param_gk。

    Returns:
        dict: max_stops / gk_dist_mist / max_stops_raw / gk_dist_mist_raw / source
    """
    if reader is None:
        from backend.api.data_api.fetch_data import query_adam_sys_param_gk
        reader = query_adam_sys_param_gk

    raw_max = None
    raw_dist = None
    source = 'ADAM_SYS_PARAM'
    try:
        params = _to_param_dict(reader())
        raw_max = params.get('GK_MAX_SITE_NUM')
        raw_dist = params.get('GK_DIST_MIST')
        if not params:
            source = '默认兜底(参数为空)'
    except Exception as e:
        source = '默认兜底(读取异常)'
        logger.warning(f"[V4参数] 读取 ADAM_SYS_PARAM 失败({e})，使用默认值 "
                       f"max_stops={DEFAULT_MAX_STOPS}, gk_dist_mist={DEFAULT_GK_DIST_MIST}")

    max_stops = _clamp_max_stops(raw_max)
    gk_dist_mist = _clamp_dist(raw_dist)
    if source == 'ADAM_SYS_PARAM' and (raw_max is None or raw_dist is None):
        source = '默认兜底(参数缺项)'
        logger.warning(f"[V4参数] 参数表缺项: GK_MAX_SITE_NUM={raw_max!r}, "
                       f"GK_DIST_MIST={raw_dist!r}")

    logger.info(f"[V4参数] max_stops={max_stops}(raw={raw_max!r}), "
                f"gk_dist_mist={gk_dist_mist}(raw={raw_dist!r}), source={source}")
    return {
        'max_stops': max_stops,
        'gk_dist_mist': gk_dist_mist,
        'max_stops_raw': raw_max,
        'gk_dist_mist_raw': raw_dist,
        'source': source,
    }


def resolve_runtime_params(max_stops=None, gk_dist_mist=None, reader=None):
    """显式入参 > ADAM_SYS_PARAM > 默认值，返回值同样经过钳制。"""
    loaded = None
    if max_stops is None or gk_dist_mist is None:
        loaded = load_delivery_params(reader=reader)

    final_max = _clamp_max_stops(
        max_stops if max_stops is not None else loaded['max_stops'])
    final_dist = _clamp_dist(
        gk_dist_mist if gk_dist_mist is not None else loaded['gk_dist_mist'])

    if loaded is None:
        source = '显式入参'
        logger.info(f"[V4参数] 显式覆盖: max_stops={final_max}, "
                    f"gk_dist_mist={final_dist}")
    else:
        source = loaded['source']
        if max_stops is not None or gk_dist_mist is not None:
            source = f"{source}+显式覆盖"
        logger.info(f"[V4参数] 生效值: max_stops={final_max}, "
                    f"gk_dist_mist={final_dist}, source={source}")

    return {
        'max_stops': final_max,
        'gk_dist_mist': final_dist,
        'max_stops_raw': loaded['max_stops_raw'] if loaded else None,
        'gk_dist_mist_raw': loaded['gk_dist_mist_raw'] if loaded else None,
        'source': source,
    }
