"""测试仓网布局 4 个查询 HTTP 接口"""
import requests
import json

DB_HOST = "localhost"
DB_PORT = 8081
BASE = f"http://{DB_HOST}:{DB_PORT}"

endpoints = [
    ("年需求(映射)", "/exec/gk-adam-query-adam-station-demand-mapped"),
    ("候选库房",    "/exec/gk-adam-query-adam-warehouse-candidate"),
    ("活跃供电所",  "/exec/gk-adam-query-adam-power-station-active"),
    ("距离矩阵",    "/exec/gk-adam-query-adam-station-dist-mist"),
]

all_ok = True
for name, ep in endpoints:
    url = f"{BASE}{ep}"
    try:
        r = requests.post(url, json={}, timeout=30)
        if r.status_code != 200:
            print(f"FAIL [{name}] HTTP {r.status_code}: {r.text[:200]}")
            all_ok = False
            continue
        data = r.json()
        if isinstance(data, list):
            print(f"OK   [{name}] {len(data)} 行, 状态码 {r.status_code}")
            if data:
                # 打印第一行键
                print(f"     列: {list(data[0].keys())}")
        else:
            print(f"OK   [{name}] 单对象, 状态码 {r.status_code}")
    except Exception as e:
        print(f"FAIL [{name}] {type(e).__name__}: {e}")
        all_ok = False

print()
if all_ok:
    print("全部 4 个接口测试通过!")
else:
    print("存在失败接口，请检查中间件是否已重启加载新 DS_SQL 条目")
