"""
紧急补库业务API蓝图
提供紧急补库方案生成、查询等功能
"""
from flask import Blueprint, request, jsonify
from backend.EmergReplenish.EmergReplenish import run_emergency_replenishment
from backend.global_optimization.logger import logger
from datetime import datetime

# 创建蓝图
emergency_bp = Blueprint('emergency', __name__, url_prefix='/emergency')


@emergency_bp.route('/run', methods=['POST'])
def generate_emergency_scheme():
    """
    生成紧急补库方案
    """
    try:
        res = run_emergency_replenishment()
        return jsonify(res)

    except Exception as e:
        logger.error(f"生成紧急补库方案失败: {str(e)}", exc_info=True)
        return jsonify({
            "code": 500,
            "message": f"生成紧急补库方案失败: {str(e)}"
        }), 500