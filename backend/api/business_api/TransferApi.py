"""
调拨业务API蓝图
提供调拨方案生成、查询等功能
"""
from flask import Blueprint, request, jsonify
from backend.Transfer.GetTransferScheme import GetTransferSchemeAndInsert
from backend.global_optimization.logger import logger
from datetime import datetime

# 创建蓝图
transfer_bp = Blueprint('transfer', __name__, url_prefix='/transfer')


@transfer_bp.route('/run', methods=['POST'])
def generate_transfer_scheme():
    """
    生成调拨方案
    """
    try:
        res = GetTransferSchemeAndInsert()
        return jsonify(res)
        
    except Exception as e:
        logger.error(f"生成调拨方案失败: {str(e)}", exc_info=True)
        return jsonify({
            "code": 500,
            "message": f"生成调拨方案失败: {str(e)}"
        }), 500

