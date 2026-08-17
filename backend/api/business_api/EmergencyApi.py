"""
紧急补库业务API蓝图
提供紧急补库方案生成、查询等功能
"""
from flask import Blueprint, request, jsonify
from backend.EmergReplenish.EmergReplenishV2 import run_emergency_replenishment_v2
from backend.global_optimization.logger import logger
from datetime import datetime
from backend.api.concurrency_lock import one_at_a_time

# 创建蓝图
emergency_bp = Blueprint('emergency', __name__, url_prefix='/emergency')


# 原 /emergency/run 路由已移至 generate_emergency_scheme_v2（二阶段场景二联动）；
# 本函数保留源文件，不再挂载路由。
def generate_emergency_scheme():
    """
    生成紧急补库方案
    """
    try:
        res = run_emergency_replenishment_v2()
        return jsonify(res)

    except Exception as e:
        logger.error(f"生成紧急补库方案失败: {str(e)}", exc_info=True)
        return jsonify({
            "code": 500,
            "message": f"生成紧急补库方案失败: {str(e)}"
        }), 500


@emergency_bp.route('/run', methods=['POST'])
@one_at_a_time('emergency', '紧急补库+调拨联动(二阶段场景二)')
def generate_emergency_scheme_v2():
    """
    紧急补库+调拨联动（映射到二阶段场景二：缺货调拨）。

    入参与原 /emergency/run 一致（无必填业务参数）；yearMonth 可选，默认当月。
    对每个缺货组合做省中心分流：中心有货→紧急补库，无货→调拨。
    """
    try:
        body = request.get_json() or {}
        year_month = body.get('yearMonth') or body.get('year_month') \
            or datetime.now().strftime('%Y%m')

        # 延迟导入，避免循环导入
        from backend.algorithm.transfer_scenario2.orchestrator import run_transfer_scenario2
        res = run_transfer_scenario2(year_month)
        return jsonify(res)

    except Exception as e:
        logger.error(f"生成紧急补库方案失败: {str(e)}", exc_info=True)
        return jsonify({
            "code": 500,
            "message": f"生成紧急补库方案失败: {str(e)}"
        }), 500