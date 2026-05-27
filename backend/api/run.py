import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask
from backend.api.business_api import inventory_opti_bp,global_optimization_bp, transfer_bp,emergency_bp
from backend.Scheduling import aps_scheduling_bp
from backend.config.config import API_CONFIG

app = Flask(__name__)

# 注册所有蓝图
app.register_blueprint(inventory_opti_bp)
app.register_blueprint(aps_scheduling_bp)
app.register_blueprint(global_optimization_bp)
app.register_blueprint(transfer_bp)
app.register_blueprint(emergency_bp)
# 健康检查路由
@app.route('/health', methods=['GET'])
def health():
    return {"status": "ok"}

host = API_CONFIG['server']['host']
port = API_CONFIG['server']['port']
if __name__ == '__main__':
    app.run(host=host, port=port, debug=True)
