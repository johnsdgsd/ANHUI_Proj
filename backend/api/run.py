import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask
from backend.api.business_api import inventory_opti_bp
from backend.Scheduling import aps_scheduling_bp


app = Flask(__name__)

# 注册所有蓝图
app.register_blueprint(inventory_opti_bp)
app.register_blueprint(aps_scheduling_bp)
# 健康检查路由
@app.route('/health', methods=['GET'])
def health():
    return {"status": "ok"}


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
