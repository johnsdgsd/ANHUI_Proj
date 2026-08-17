"""
调拨业务API蓝图
提供调拨方案生成、查询等功能
"""
from flask import Blueprint, request, jsonify
from backend.Transfer.GetTransferScheme import GetTransferSchemeAndInsert
from backend.api.data_api.fetch_data import update_adam_pre_conc_stat
from backend.global_optimization.logger import logger
from datetime import datetime
from backend.api.concurrency_lock import one_at_a_time

# 创建蓝图
transfer_bp = Blueprint('transfer', __name__, url_prefix='/transfer')


# 原 /transfer/run 路由已移至 generate_transfer_scheme_v2（二阶段场景一）；
# 本函数保留源文件，不再挂载路由。
def generate_transfer_scheme():
    """
    生成调拨方案（一阶段：按优先级就近调拨）
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


@transfer_bp.route('/run', methods=['POST'])
@one_at_a_time('transfer', '调拨方案生成(二阶段场景一)')
def generate_transfer_scheme_v2():
    """
    调拨方案（映射到二阶段场景一：月初高库龄调拨）。

    入参与原 /transfer/run 一致：preConcId 必填；yearMonth 可选，默认当月。
    """
    try:
        data = request.get_json() or {}
        preConcId = data.get('preConcId')

        if not preConcId:
            return jsonify({
                "code": 400,
                "message": "参数错误：缺少 preConcId"
            }), 400

        year_month = data.get('yearMonth') or data.get('year_month') \
            or datetime.now().strftime('%Y%m')

        update_adam_pre_conc_stat(int(preConcId), '02')

        # 延迟导入，避免循环导入
        from backend.algorithm.transfer.orchestrator import run_transfer_scenario1
        res = run_transfer_scenario1(year_month)

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


@transfer_bp.route('/run-scenario1', methods=['POST'])
@one_at_a_time('transfer-scenario1', '调拨场景一(月初高库龄)')
def generate_transfer_scenario1():
    """
    调拨场景一：月初高库龄调拨（二阶段 ILP 算法）

    入参:
        yearMonth: 业务年月 'YYYYMM'（必填，月度补库计划/需求预测所属年月）
        preConcId: 预测结论ID（必填，状态机 02处理中/03成功/04失败）
        allotDate: 调拨执行日(ALLOT_DATE)，可选，默认当天
        windowDays: 两周需求窗口天数，可选，默认14
    """
    try:
        data = request.get_json() or {}
        year_month = data.get('yearMonth') or data.get('year_month')
        preConcId = data.get('preConcId')

        if not year_month:
            return jsonify({
                "code": 400,
                "message": "参数错误：缺少 yearMonth (YYYYMM)"
            }), 400
        if not preConcId:
            return jsonify({
                "code": 400,
                "message": "参数错误：缺少 preConcId"
            }), 400

        update_adam_pre_conc_stat(int(preConcId), '02')

        # 延迟导入，避免 orchestrator → data_prep → fetch_data → api包 → TransferApi 循环导入
        from backend.algorithm.transfer.orchestrator import run_transfer_scenario1

        res = run_transfer_scenario1(
            year_month,
            allot_date=data.get('allotDate') or data.get('allot_date'),
            window_days=int(data.get('windowDays', 14)),
        )

        update_adam_pre_conc_stat(int(preConcId), '03')

        return jsonify(res)

    except Exception as e:
        logger.error(f"生成调拨场景一方案失败: {str(e)}", exc_info=True)
        try:
            data = request.get_json() or {}
            preConcId = data.get('preConcId')
            if preConcId:
                update_adam_pre_conc_stat(int(preConcId), '04')
        except Exception:
            pass
        return jsonify({
            "code": 500,
            "message": f"生成调拨场景一方案失败: {str(e)}"
        }), 500


@transfer_bp.route('/run-scenario2', methods=['POST'])
@one_at_a_time('transfer-scenario2', '调拨场景二(缺货调拨)')
def generate_transfer_scenario2():
    """
    调拨场景二：缺货调拨（含紧急补库分流，二阶段）

    入参:
        yearMonth: 业务年月 'YYYYMM'（必填，缺货检测/需求预测所属年月）
        preConcId: 预测结论ID（必填，状态机 02处理中/03成功/04失败）
        snapshotDate: 库存快照/调拨执行日 'YYYY-MM-DD'，可选，默认当天
        windowUpperDays: 库存上限窗口天数，可选，默认14
    """
    try:
        data = request.get_json() or {}
        year_month = data.get('yearMonth') or data.get('year_month')
        preConcId = data.get('preConcId')

        if not year_month:
            return jsonify({
                "code": 400,
                "message": "参数错误：缺少 yearMonth (YYYYMM)"
            }), 400
        if not preConcId:
            return jsonify({
                "code": 400,
                "message": "参数错误：缺少 preConcId"
            }), 400

        update_adam_pre_conc_stat(int(preConcId), '02')

        # 延迟导入，避免 api 包 → business_api → TransferApi → transfer_scenario2 循环导入
        from backend.algorithm.transfer_scenario2.orchestrator import run_transfer_scenario2

        res = run_transfer_scenario2(
            year_month,
            snapshot_date=data.get('snapshotDate') or data.get('snapshot_date'),
            window_upper_days=int(data.get('windowUpperDays', 14)),
        )

        update_adam_pre_conc_stat(int(preConcId), '03')

        return jsonify(res)

    except Exception as e:
        logger.error(f"生成调拨场景二方案失败: {str(e)}", exc_info=True)
        try:
            data = request.get_json() or {}
            preConcId = data.get('preConcId')
            if preConcId:
                update_adam_pre_conc_stat(int(preConcId), '04')
        except Exception:
            pass
        return jsonify({
            "code": 500,
            "message": f"生成调拨场景二方案失败: {str(e)}"
        }), 500

