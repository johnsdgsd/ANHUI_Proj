"""
仓网布局优化业务API蓝图
提供区县库房选址与供电所分配优化功能
"""
import threading

from flask import Blueprint, jsonify

from backend.global_optimization.logger import logger

warehouse_layout_bp = Blueprint(
    'warehouse_layout', __name__,
    url_prefix='/warehouse-layout'
)


@warehouse_layout_bp.route('/run', methods=['POST'])
def run_warehouse_layout_optimization():
    """
    执行仓网布局优化算法。
    异步执行，立即返回 202。
    """
    try:
        from backend.algorithm.warehouse_layout.run_optimization import (
            run_warehouse_optimization,
        )

        def _run():
            try:
                # 删除当日旧方案
                from datetime import datetime
                from backend.api.data_api.fetch_data import (
                    delete_adam_layout_result_by_date,
                    delete_adam_layout_result_det_by_date,
                )
                today = datetime.now().strftime("%Y-%m-%d")
                try:
                    delete_adam_layout_result_det_by_date(today)
                    delete_adam_layout_result_by_date(today)
                    logger.info(f"[API] 已删除当日({today})旧方案")
                except Exception as e:
                    logger.warning(f"[API] 删除旧方案失败（继续执行）: {e}")

                result = run_warehouse_optimization()
                logger.info(f"[API] 仓网布局优化完成: {result.get('message', '')}")
            except Exception as e:
                logger.error(f"[API] 仓网布局优化异常: {e}", exc_info=True)

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()

        return jsonify({
            "success": True,
            "message": "仓网布局优化已启动",
        }), 202

    except Exception as e:
        logger.error(f"[API] 启动仓网布局优化失败: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "error": str(e),
        }), 500
