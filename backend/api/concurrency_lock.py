# -*- coding: utf-8 -*-
"""业务 API 并发保护模块

为每个暴露的业务接口提供一把【独立互斥锁】（每个接口一把锁），
保证同一接口同一时间只允许一次调用；若已有同接口任务在运行，
新的并发调用立即返回 409「系统正忙」，不排队、不等待。

用法：
  - 同步接口：用 @one_at_a_time(lock_key, desc) 装饰路由函数即可，
    整个请求处理期间持有该接口的锁，返回后自动释放。
  - 异步接口（立即返回、后台线程执行）：
      1. HTTP 处理函数里 try_acquire(lock_key, desc)，拿不到锁立即返回 409；
      2. 把 release(lock_key) 放到后台线程的 finally 中，
         线程 run 结束后才释放锁（锁的生命周期覆盖整个任务）。
"""
import threading
import logging
from functools import wraps

from flask import jsonify

_logger = logging.getLogger(__name__)

# 锁注册表：lock_key -> threading.Lock；当前占用任务描述：lock_key -> task_desc
_locks = {}
_locks_guard = threading.Lock()
_active = {}


def _get_lock(lock_key: str) -> threading.Lock:
    with _locks_guard:
        if lock_key not in _locks:
            _locks[lock_key] = threading.Lock()
            _active[lock_key] = None
        return _locks[lock_key]


def try_acquire(lock_key: str, task_desc: str = "未知任务") -> bool:
    """非阻塞尝试获取 lock_key 这把锁。

    成功返回 True（调用方必须保证最终在 finally 中 release）；
    已有同接口任务在跑时返回 False。
    """
    if _get_lock(lock_key).acquire(blocking=False):
        _active[lock_key] = task_desc
        _logger.info(f"[并发保护][{lock_key}] 任务开始: {task_desc}")
        return True
    cur = _active.get(lock_key) or "同接口的其他任务"
    _logger.warning(f"[并发保护][{lock_key}] 已有任务运行({cur})，拒绝并发调用: {task_desc}")
    return False


def release(lock_key: str):
    """释放 lock_key 这把锁（必须在 finally 中调用）。"""
    _active[lock_key] = None
    try:
        _get_lock(lock_key).release()
    except RuntimeError:
        _logger.warning(f"[并发保护][{lock_key}] release() 时锁未持有，已忽略")


def busy_json(lock_key: str) -> dict:
    """构造 409 忙碌响应体（状态码固定 409）。"""
    cur = _active.get(lock_key) or "同接口的其他任务"
    return {
        "success": False,
        "code": 409,
        "message": f"系统正忙：{cur} 正在执行。同一接口同一时间仅允许一次调用，请稍后再试。",
    }


def one_at_a_time(lock_key: str, task_desc: str = "未知任务"):
    """装饰器：用于【同步】接口，整个请求处理期间持有该接口的锁。

    若已有同接口任务在跑，立即返回 409，不进入业务逻辑。
    """
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not try_acquire(lock_key, task_desc):
                return jsonify(busy_json(lock_key)), 409
            try:
                return fn(*args, **kwargs)
            finally:
                release(lock_key)
        return wrapper
    return decorator
