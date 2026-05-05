"""
库存优化业务API蓝图
提供库存优化相关的业务接口
"""

from flask import Blueprint, request, jsonify
from backend.inventory_optimization.RunOptimize import run_optimization_from_api

# 创建蓝图
inventory_opti_bp = Blueprint('inventory_opti', __name__, url_prefix='/inventory')

@inventory_opti_bp.route('/optimize', methods=['POST'])
def optimize():
    """库存优化接口
    
    Request Body:
        {
            "init_stock_month": 202605,      // 初始库存月份
            "install_start_month": 202204,   // 安装量起始月份
            "install_end_month": 202604,     // 安装量结束月份
            "central_warehouse_name": "合肥供电公司",  // 中心库名称
            "n_iter": 100,                   // 遗传算法迭代次数 (可选，默认100)
            "pop_size": 200,                 // 种群大小 (可选，默认200)
            "target_service_level": 0.95,    // 目标满足率 (可选，默认0.95)
            "n_processor": 10                // 并行处理器数量 (可选，默认1)
        }
    
    Returns:
        {
            "success": true,
            "best_solution": [...],
            "best_cost": 12345.67,
            "central_warehouse": "合肥供电公司",
            "n_iter": 100,
            "pop_size": 200,
            "target_service_level": 0.95
        }
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


@inventory_opti_bp.route('/health', methods=['GET'])
def health():
    """健康检查接口"""
    return jsonify({"status": "ok", "service": "inventory_optimization"})
