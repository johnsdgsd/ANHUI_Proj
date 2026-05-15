"""
库存优化业务API蓝图
提供库存优化相关的业务接口
"""

from flask import Blueprint, request, jsonify
from backend.inventory_optimization.RunOptimize import run_optimization_from_api
from backend.inventory_optimization.GetWeeklyThreshold import GenerateWeeklyThreshold
from backend.inventory_optimization.DailyReplenishmentPlan import AdjustDaliyDelivery,DailyReplenishmentPlan
# 创建蓝图
inventory_opti_bp = Blueprint('inventory_opti', __name__, url_prefix='/inventory')

@inventory_opti_bp.route('/optimize', methods=['POST'])
def optimize():
    """
    库存优化接口
    """
    try:
        data = request.get_json() or {}
        
        # 获取必需参数
        init_stock_month = data.get('init_stock_month')
        install_start_month = data.get('install_start_month')
        install_end_month = data.get('install_end_month')
        central_warehouse_name = data.get('central_warehouse_name')
        
        # 参数校验
        if not all([init_stock_month, install_start_month, install_end_month, central_warehouse_name]):
            return jsonify({
                "success": False,
                "error": "缺少必需参数: init_stock_month, install_start_month, install_end_month, central_warehouse_name"
            }), 400
        
        # 获取可选参数
        n_iter = data.get('n_iter', 100)
        pop_size = data.get('pop_size', 200)
        target_service_level = data.get('target_service_level', 0.95)
        n_processor = data.get('n_processor', 1)
        
        # 运行优化
        result = run_optimization_from_api(
            init_stock_month=init_stock_month,
            install_start_month=install_start_month,
            install_end_month=install_end_month,
            central_warehouse_name=central_warehouse_name,
            n_iter=n_iter,
            pop_size=pop_size,
            target_service_level=target_service_level,
            n_processor=n_processor
        )
        
        return jsonify(result)
        
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@inventory_opti_bp.route('/generate-weekly-threshold', methods=['POST'])
def GenerateWeeklyThresholdRoute():
    """生成周度库存阈值并插入数据库"""
    try:
        data = request.get_json() or {}
        year = data.get('year')
        month = data.get('month')

        if not year or not month:
            return jsonify({"success": False, "error": "缺少必需参数: year, month"}), 400

        dfThreshold,result = GenerateWeeklyThreshold(year, month)

        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@inventory_opti_bp.route('/adjust-daily-delivery', methods=['POST'])
def AdjustDailyDeliveryPlan():
    """
    调整日补库计划接口
    请求参数（JSON）:
        adjustDate: 调整日期，如 "2026-05-06"
    """
    from backend.api.data_api.fetch_data import insert_into_adam_dist_scheme,insert_into_adam_dist_scheme_det
    try:
        Data = request.get_json() or {}
        AdjustDate = Data.get('adjustDate')

        if not AdjustDate:
            return jsonify({
                "success": False,
                "error": "缺少必需参数: adjustDate"
            }), 400

        MainScheme, DetailScheme = AdjustDaliyDelivery(AdjustDate)

        # 插入配送主表
        MainResult = insert_into_adam_dist_scheme(MainScheme)
        # 插入配送明细表
        DetailResult = insert_into_adam_dist_scheme_det(DetailScheme)

        TotalSuccess = MainResult.get('success_count', 0) + DetailResult.get('success_count', 0)
        TotalFailed = MainResult.get('failed_count', 0) + DetailResult.get('failed_count', 0)

        return jsonify({
            "success": TotalFailed == 0,
            "message": f"日补库计划调整完成",
            "mainResult": MainResult,
            "detailResult": DetailResult
        })

    except Exception as E:
        return jsonify({
            "success": False,
            "error": str(E)
        }), 500


@inventory_opti_bp.route('/generate-daily-replenishment', methods=['POST'])
def GenerateDailyReplenishmentPlan():
    """
    生成日度补库计划接口
    请求参数（JSON）:
        startDate: 计划起始日期，如 "2026-05-01"
        endDate: 计划结束日期，如 "2026-05-31"
    """
    try:
        Data = request.get_json() or {}
        StartDate = Data.get('start_date')
        EndDate = Data.get('end_date')

        if not StartDate or not EndDate:
            return jsonify({
                "success": False,
                "error": "缺少必需参数: start_date, end_date"
            }), 400

        # 生成日度补库计划
        DaliyReplPlan, result = DailyReplenishmentPlan(StartDate, EndDate)

        TotalSuccess = result.get('success_count', 0)
        TotalFailed = result.get('failed_count', 0)

        return jsonify({
            "success": TotalFailed == 0,
            "message": f"日度补库计划生成完成",
            "result": result
        })

    except Exception as E:
        return jsonify({
            "success": False,
            "error": str(E)
        }), 500