import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, request, jsonify
from backend.inventory_optimization.optimizer import InventoryOptimizer

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(BASE_DIR, "data")

CITY_MAPPING_FILE = os.path.join(DATA_DIR, "地市映射表.xlsx")
DEV_COST_FILE = os.path.join(DATA_DIR, "物资价格.xlsx")
DEMAND_DATA_FILE = os.path.join(DATA_DIR, "处理后数据.xlsx")

optimizer = None

def get_optimizer():
    global optimizer
    if optimizer is None:
        optimizer = InventoryOptimizer(CITY_MAPPING_FILE, DEMAND_DATA_FILE)
        optimizer.set_local_warehouses_from_dataframe(DEMAND_DATA_FILE)
        optimizer.set_item_costs_from_dataframe(DEV_COST_FILE)
    return optimizer


@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"})


@app.route('/optimize', methods=['POST'])
def optimize():
    try:
        data = request.get_json() or {}
        n_iter = data.get('n_iter', 100)
        pop_size = data.get('pop_size', 200)
        
        opt = get_optimizer()
        best_solution, best_cost = opt.optimize_alpha(n_iter=n_iter, pop_size=pop_size)
        
        return jsonify({
            "success": True,
            "best_solution": best_solution.tolist() if hasattr(best_solution, 'tolist') else list(best_solution),
            "best_cost": float(best_cost)
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route('/simulate', methods=['POST'])
def simulate():
    try:
        data = request.get_json() or {}
        start_year_month = data.get('start_year_month', 202501)
        end_year_month = data.get('end_year_month', 202512)
        opt = get_optimizer()
        alpha_dict = opt.generate_alpha_dict()
        opt.set_alpha(alpha_dict)
        opt.simulate(start_year_month, end_year_month)
        
        costs = opt.calculate_costs()
        
        return jsonify({
            "success": True,
            "costs": costs
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route('/set_alpha', methods=['POST'])
def set_alpha():
    try:
        data = request.get_json()
        alpha_dict = data.get('alpha_dict')
        
        if not alpha_dict:
            return jsonify({
                "success": False,
                "error": "alpha_dict is required"
            }), 400
        
        opt = get_optimizer()
        opt.set_alpha(alpha_dict)
        
        return jsonify({
            "success": True,
            "message": "Alpha set successfully"
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
