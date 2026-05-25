"""
全局优化业务API蓝图
提供多方案生成、成本计算等全局优化功能
"""
from flask import Blueprint, request, jsonify
from backend.global_optimization.multi_plan_generator import GenerateMutiOrderScheme
from backend.global_optimization.logger import logger
from datetime import datetime
import threading
# 创建蓝图
global_optimization_bp = Blueprint('global_optimization', __name__, url_prefix='/global-optimization')


@global_optimization_bp.route('/run-async', methods=['POST'])
def generate_multi_scheme_async():
    """
    生成多套方案
    """
    try:
        data = request.get_json() or {}
        preMonth = data.get('preMonth')
        preConcId = data.get('preConcId')
        # 参数校验
        if not [preMonth,preConcId]:
            return jsonify({
                "code": 400,
                "message": "参数错误：缺少参数"
            }), 400

        # 使用线程异步执行方案生成
        # 注意：args参数需要是元组，单个参数后面要加逗号
        try:

            thread = threading.Thread(target=GenerateMutiOrderScheme, args=(preMonth,))
            # 启动线程
            thread.start()
        except Exception as e:
            response = {
                "code": 500,
                "message": '生成失败'+str(e)
            }
            return jsonify()
        # 立即返回响应
        response = {
            "code": 200,
            "message": "正在生成..."
        }
        
        return jsonify(response)
        
    except Exception as e:
        return jsonify({
            "code": 500,
            "message": f"生成多套方案失败: {str(e)}"
        }), 500

@global_optimization_bp.route('/run', methods=['POST'])
def generate_multi_scheme():
    """
    生成多套方案，同步执行
    """
    try:
        data = request.get_json() or {}
        preMonth = data.get('preMonth')
        preConcId = data.get('preConcId')
        # 参数校验
        if not [preMonth,preConcId]:
            return jsonify({
                "code": 400,
                "message": "参数错误：缺少参数"
            }), 400

        # 同步执行
        res = GenerateMutiOrderScheme(preMonth)
        # 立即返回响应
        response = {
            "code": 200,
            "message": "方案生成成功"
        }
        
        return jsonify(response)
        
    except Exception as e:
        logger.error("执行全局优化失败", exc_info=True)
        return jsonify({
            "code": 500,
            "message": f"生成多套方案失败: {str(e)}"
        }), 500
