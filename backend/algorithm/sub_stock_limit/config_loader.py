"""
供电所库存阈值预测 — 参数解析

- 服务水平 alpha: 仅来自请求参数，缺省 0.95（不读 ADAM_AIS_SYS_PARAM 配置表）
- 窗口天数: down_days / up_days 来自请求参数，缺省 7 / 14
"""
import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# 默认窗口天数（天）
DEFAULT_DOWN_DAYS = 7    # 下限窗口: 1 周
DEFAULT_UP_DAYS = 14     # 上限窗口: 2 周

# 允许的窗口天数范围（防御非法入参）
MIN_WINDOW_DAYS = 1
MAX_WINDOW_DAYS = 90

# alpha 合法区间（开区间内，闭区间边界无实际意义）
MIN_ALPHA = 0.01
MAX_ALPHA = 0.9999

DEFAULT_ALPHA = 0.95


def resolve_alpha(alpha_override: Optional[float]) -> float:
    """
    确定本次计算使用的服务水平 alpha。

    仅两个来源: 请求参数 > 硬编码默认 0.95（本模块不读参数配置表）。

    Args:
        alpha_override: parse_alpha 的返回值，None 表示请求未指定

    Returns:
        float: 服务水平
    """
    return float(alpha_override) if alpha_override is not None else DEFAULT_ALPHA


def parse_window_days(down_days, up_days) -> Tuple[int, int]:
    """
    解析并校验窗口天数。

    Args:
        down_days: 下限窗口天数，None 时取默认 7
        up_days: 上限窗口天数，None 时取默认 14

    Returns:
        (down_days, up_days)

    Raises:
        ValueError: 非整数、超范围、或 down_days > up_days
    """
    def _parse(value, default, name):
        if value is None or value == '':
            return default
        try:
            v = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"{name} 必须为整数，当前值: {value!r}")
        if v < MIN_WINDOW_DAYS or v > MAX_WINDOW_DAYS:
            raise ValueError(
                f"{name} 超出允许范围 [{MIN_WINDOW_DAYS}, {MAX_WINDOW_DAYS}]，当前值: {v}"
            )
        return v

    d = _parse(down_days, DEFAULT_DOWN_DAYS, 'downDays')
    u = _parse(up_days, DEFAULT_UP_DAYS, 'upDays')
    if d > u:
        raise ValueError(f"下限窗口天数({d})不能大于上限窗口天数({u})")
    return d, u


def parse_alpha(alpha) -> Optional[float]:
    """
    解析并校验服务水平 alpha。

    Args:
        alpha: 请求参数，None / '' 表示使用默认值

    Returns:
        float 或 None（None 表示走默认 0.95，由 resolve_alpha 落定）

    Raises:
        ValueError: 非数值或超出 (0, 1)
    """
    if alpha is None or alpha == '':
        return None
    try:
        a = float(alpha)
    except (TypeError, ValueError):
        raise ValueError(f"alpha 必须为数值，当前值: {alpha!r}")
    if a <= 0 or a >= 1:
        raise ValueError(f"alpha 必须在 (0, 1) 区间内，当前值: {a}")
    return min(max(a, MIN_ALPHA), MAX_ALPHA)


def parse_org_code(org_code) -> str:
    """
    解析可选的范围组织编码。

    Returns:
        str: 组织编码；全省时返回 'ALL'
    """
    if org_code is None:
        return 'ALL'
    code = str(org_code).strip()
    return code if code else 'ALL'
