"""
供电所补库业务API蓝图
提供 (R,S) 联合补货建议生成功能
"""
import threading

from flask import Blueprint, jsonify

from backend.global_optimization.logger import logger

# 创建蓝图
substation_replenish_bp = Blueprint(
    'substation_replenish', __name__,
    url_prefix='/substation-replenish'
)


@substation_replenish_bp.route('/run', methods=['POST'])
def generate_substation_replenishment():
    """
    生成供电所补货建议（(R,S) 联合补货算法）
    异步执行，立即返回 202。
    """
    try:
        from backend.algorithm.substation.run_replenishment import (
            run_substation_replenishment,
        )

        def _run():
            try:
                result = run_substation_replenishment()
                logger.info(f"[API] 补货计算完成: {result.get('message', '')}")
            except Exception as e:
                logger.error(f"[API] 补货计算异常: {e}", exc_info=True)

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()

        return jsonify({
            "success": True,
            "message": "(R,S)补货计算已启动",
        }), 202

    except Exception as e:
        logger.error(f"[API] 启动补货计算失败: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "error": str(e),
        }), 500
