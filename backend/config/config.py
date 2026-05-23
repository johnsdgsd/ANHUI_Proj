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

# 只在第一次导入时打印配置信息
if __name__ == '__main__' or not hasattr(__import__(__name__), '_CONFIG_PRINTED'):
    print("环境变量配置:")
    print(f"  DB_HOST: {API_CONFIG['database']['host'] if API_CONFIG['database']['host'] is not None else '未设置，使用默认值: localhost'}")
    print(f"  DB_PORT: {API_CONFIG['database']['port'] if API_CONFIG['database']['port'] is not None else '未设置，使用默认值: 8081'}")
    print(f"  SERVER_HOST: {API_CONFIG['server']['host'] if API_CONFIG['server']['host'] is not None else '未设置，使用默认值: 0.0.0.0'}")
    print(f"  SERVER_PORT: {API_CONFIG['server']['port'] if API_CONFIG['server']['port'] is not None else '未设置，使用默认值: 5000'}")
    # 设置标记，避免重复打印
    setattr(__import__(__name__), '_CONFIG_PRINTED', True)


