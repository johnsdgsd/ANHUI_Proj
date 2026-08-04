"""
供电所补库业务API蓝图
提供 (R,S) 联合补货建议生成功能
"""
import threading

from flask import Blueprint, request, jsonify

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
    异步执行，立即返回 202，通过 ADAM_PRE_CONC 状态表跟踪进度。
    """
    from backend.api.data_api.fetch_data import update_adam_pre_conc_stat

    data = request.get_json(silent=True) or {}
    preConcId = data.get('preConcId')
    if not preConcId:
        return jsonify({
            "success": False,
            "error": "缺少必需参数: preConcId",
        }), 400

    try:
        from backend.algorithm.substation.run_replenishment import (
            run_substation_replenishment,
        )

        # 更新状态为处理中
        update_adam_pre_conc_stat(int(preConcId), '02')

        def _run():
            try:
                # 删除补货日旧建议（算法算的是明天的建议），再全量写入，避免重复
                from datetime import datetime, timedelta
                from backend.api.data_api.fetch_data import (
                    delete_adam_replenish_order_by_date,
                )
                tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
                try:
                    delete_adam_replenish_order_by_date(tomorrow)
                    logger.info(f"[API] 已删除补货日({tomorrow})旧建议")
                except Exception as e:
                    logger.warning(f"[API] 删除旧补货建议失败（继续执行）: {e}")

                result = run_substation_replenishment()
                if result.get('success'):
                    update_adam_pre_conc_stat(int(preConcId), '03')
                else:
                    update_adam_pre_conc_stat(int(preConcId), '04')
                logger.info(f"[API] 补货计算完成: {result.get('message', '')}")
            except Exception as e:
                logger.error(f"[API] 补货计算异常: {e}", exc_info=True)
                try:
                    update_adam_pre_conc_stat(int(preConcId), '04')
                except Exception:
                    pass

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()

        return jsonify({
            "success": True,
            "message": "(R,S)补货计算已启动",
        }), 202

    except Exception as e:
        logger.error(f"[API] 启动补货计算失败: {e}", exc_info=True)
        try:
            update_adam_pre_conc_stat(int(preConcId), '04')
        except Exception:
            pass
        return jsonify({
            "success": False,
            "error": str(e),
        }), 500
