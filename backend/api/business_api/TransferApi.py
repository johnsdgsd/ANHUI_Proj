"""
调拨业务API蓝图
提供调拨方案生成、查询等功能
"""
from flask import Blueprint, request, jsonify
from backend.Transfer.GetTransferScheme import GetTransferSchemeAndInsert
from backend.api.data_api.fetch_data import update_adam_pre_conc_stat
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
        data = request.get_json() or {}
        preConcId = data.get('preConcId')

        if not preConcId:
            return jsonify({
                "code": 400,
                "message": "参数错误：缺少 preConcId"
            }), 400

        update_adam_pre_conc_stat(int(preConcId), '02')

        res = GetTransferSchemeAndInsert()

        update_adam_pre_conc_stat(int(preConcId), '03')

        return jsonify(res)

    except Exception as e:
        logger.error(f"生成调拨方案失败: {str(e)}", exc_info=True)
        try:
            data = request.get_json() or {}
            preConcId = data.get('preConcId')
            if preConcId:
                update_adam_pre_conc_stat(int(preConcId), '04')
        except Exception:
            pass
        return jsonify({
            "code": 500,
            "message": f"生成调拨方案失败: {str(e)}"
        }), 500

