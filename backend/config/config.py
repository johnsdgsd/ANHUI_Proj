import os
import yaml

# 从环境变量读取配置
API_CONFIG = {
    'database': {
        'host': os.environ.get('DB_HOST', 'localhost'),
        'port': int(os.environ.get('DB_PORT', 8081))
    },
    'server': {
        'host': os.environ.get('SERVER_HOST', '0.0.0.0'),
        'port': int(os.environ.get('SERVER_PORT', 5000))
    }
}
