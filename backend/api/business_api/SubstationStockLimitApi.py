"""
供电所库存阈值预测业务API蓝图
提供基于供电所需求预测的库存阈值上下限计算功能
"""
import threading

from flask import Blueprint, request, jsonify

from backend.global_optimization.logger import logger
from backend.api.concurrency_lock import try_acquire, release, busy_json

# 创建蓝图
substation_stock_limit_bp = Blueprint(
    'substation_stock_limit', __name__,
    url_prefix='/substation-stock-limit'
)


@substation_stock_limit_bp.route('/run', methods=['POST'])
def run_substation_stock_limit():
    """
    生成供电所库存阈值（泊松分位数法）

    异步执行，立即返回 202，通过 ADAM_PRE_CONC 状态表跟踪进度。

    入参:
        preConcId  必填，预测结论ID，用于状态跟踪
        orgCode    可选，范围组织编码（5位/7位市县码 或 9位供电所码），缺省全省
                   传市县码时只预测该市县名下的供电所
        alpha      可选，服务水平，缺省 0.95
        downDays   可选，下限窗口天数，默认 7（1周）
        upDays     可选，上限窗口天数，默认 14（2周）
    """
    from backend.api.data_api.fetch_data import update_adam_pre_conc_stat

    data = request.get_json(silent=True) or {}
    preConcId = data.get('preConcId')
    if not preConcId:
        return jsonify({
            "success": False,
            "error": "缺少必需参数: preConcId",
        }), 400

    org_code = data.get('orgCode')
    alpha = data.get('alpha')
    down_days = data.get('downDays')
    up_days = data.get('upDays')

    # 入参即时校验（窗口天数 / alpha），非法直接 400，不起后台任务
    from backend.algorithm.sub_stock_limit.config_loader import (
        parse_alpha, parse_window_days,
    )
    try:
        parse_window_days(down_days, up_days)
        parse_alpha(alpha)
    except ValueError as e:
        logger.warning(f"[API] 阈值计算入参错误: {e}")
        return jsonify({"success": False, "error": str(e)}), 400

    try:
        from backend.algorithm.sub_stock_limit.run_threshold import (
            run_sub_stock_limit,
        )

        # 并发保护：同一接口同一时间只允许一次调用，拿不到锁立即 409
        LOCK_KEY = 'substation-stock-limit'
        if not try_acquire(
            LOCK_KEY,
            f"供电所库存阈值 orgCode={org_code or 'ALL'} preConcId={preConcId}"
        ):
            return jsonify(busy_json(LOCK_KEY)), 409

        try:
            # 更新状态为处理中
            update_adam_pre_conc_stat(int(preConcId), '02')

            def _run():
                try:
                    # 先删后插在算法侧按范围执行（orgCode 决定删除范围）
                    result = run_sub_stock_limit(
                        org_code=org_code,
                        alpha=alpha,
                        down_days=down_days,
                        up_days=up_days,
                    )
                    if result.get('success'):
                        update_adam_pre_conc_stat(int(preConcId), '03')
                    else:
                        update_adam_pre_conc_stat(int(preConcId), '04')
                    logger.info(f"[API] 阈值计算完成: {result.get('message', '')}")
                except Exception as e:
                    logger.error(f"[API] 阈值计算异常: {e}", exc_info=True)
                    try:
                        update_adam_pre_conc_stat(int(preConcId), '04')
                    except Exception:
                        pass
                finally:
                    release(LOCK_KEY)

            thread = threading.Thread(target=_run, daemon=True)
            thread.start()
        except Exception:
            # update('02') 或起线程失败：释放锁，交由外层统一处理状态
            release(LOCK_KEY)
            raise

        return jsonify({
            "success": True,
            "message": "供电所库存阈值计算已启动",
        }), 202

    except Exception as e:
        logger.error(f"[API] 启动阈值计算失败: {e}", exc_info=True)
        try:
            update_adam_pre_conc_stat(int(preConcId), '04')
        except Exception:
            pass
        return jsonify({
            "success": False,
            "error": str(e),
        }), 500
