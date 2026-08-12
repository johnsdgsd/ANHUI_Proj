"""
全局优化业务API蓝图
提供多方案生成、成本计算等全局优化功能
"""
from flask import Blueprint, request, jsonify
from backend.global_optimization.multi_plan_generator import GenerateMutiOrderScheme
from backend.global_optimization.logger import logger
from datetime import datetime
import threading
from backend.api.concurrency_lock import try_acquire, release, busy_json, one_at_a_time
# 创建蓝图
global_optimization_bp = Blueprint('global_optimization', __name__, url_prefix='/global-optimization')


@global_optimization_bp.route('/run-async', methods=['POST'])
def generate_multi_scheme_async():
    """
    生成多套方案
    """
    from backend.api.data_api.fetch_data import update_adam_pre_conc_stat
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

        # 并发保护：同一接口同一时间只允许一次调用，拿不到锁立即 409
        LOCK_KEY = 'global-optimization-async'
        if not try_acquire(LOCK_KEY, f"多方案生成(异步) {preMonth} preConcId={preConcId}"):
            return jsonify(busy_json(LOCK_KEY)), 409

        # 使用线程异步执行方案生成
        # 注意：args参数需要是元组，单个参数后面要加逗号
        try:
            update_adam_pre_conc_stat(int(preConcId), '02')

            def _wrapped():
                try:
                    GenerateMutiOrderScheme(preMonth)
                finally:
                    release(LOCK_KEY)

            thread = threading.Thread(target=_wrapped, daemon=True)
            # 启动线程
            thread.start()
            update_adam_pre_conc_stat(int(preConcId), '04')
        except Exception:
            # 起线程/更新状态失败：释放锁，交由外层统一处理
            release(LOCK_KEY)
            raise

        # 立即返回响应
        response = {
            "code": 200,
            "message": "正在生成..."
        }

        return jsonify(response)

    except Exception as e:
        update_adam_pre_conc_stat(int(preConcId), '04')
        return jsonify({
            "code": 500,
            "message": f"生成多套方案失败: {str(e)}"
        }), 500

@global_optimization_bp.route('/run', methods=['POST'])
@one_at_a_time('global-optimization', '多方案生成(同步)')
def generate_multi_scheme():
    """
    生成多套方案，同步执行
    """
    from  backend.api.data_api.fetch_data import update_adam_pre_conc_stat
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
        update_adam_pre_conc_stat(int(preConcId), '02')
        res = GenerateMutiOrderScheme(preMonth)
        # 立即返回响应
        response = {
            "code": 200,
            "message": "方案生成成功"
        }
        update_adam_pre_conc_stat(int(preConcId), '03')
        
        return jsonify(response)
        
    except Exception as e:
        logger.error("执行全局优化失败", exc_info=True)
        update_adam_pre_conc_stat(int(preConcId), '04')
        return jsonify({
            "code": 500,
            "message": f"生成多套方案失败: {str(e)}"
        }), 500
