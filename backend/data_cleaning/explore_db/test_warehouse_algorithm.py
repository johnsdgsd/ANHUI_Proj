"""测试仓网布局算法（只算不写库）"""
import sys, os, logging
_BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, _BASE)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
                    datefmt="%H:%M:%S")

import requests, pandas as pd, time

DB = "http://localhost:8081"

# ---- Step 1: 加载数据 ----
print("=" * 60)
print("Step 1: 加载数据...")
t0 = time.time()

def fetch(ep, label):
    r = requests.post(f"{DB}{ep}", json={}, timeout=60)
    r.raise_for_status()
    df = pd.DataFrame(r.json())
    print(f"  {label}: {len(df)} 行")
    return df

demand_df    = fetch('/exec/gk-adam-query-adam-station-demand-mapped',     '年需求(映射)')
warehouse_df = fetch('/exec/gk-adam-query-adam-warehouse-candidate',       '候选库房')
station_df   = fetch('/exec/gk-adam-query-adam-power-station-active',      '活跃供电所')
dist_df      = fetch('/exec/gk-adam-query-adam-station-dist-mist',         '距离矩阵')

# ---- Step 2: 数据预处理 ----
print(f"\nStep 2: 数据预处理 (耗时 {time.time()-t0:.1f}s)...")
from backend.algorithm.warehouse_layout.algorithm import prepare_data, optimize_warehouse_layout
data = prepare_data(demand_df, warehouse_df, station_df, dist_df)
print(f"  供电所 S={len(data['station_codes'])}  库房 W={len(data['wh_codes'])}  设备码 D={len(data['dev_codes'])}")

# ---- Step 3: 执行优化 ----
print(f"\nStep 3: 执行 ε-约束 MILP 优化...")
t1 = time.time()
solutions = optimize_warehouse_layout(data)
elapsed = time.time() - t1

# ---- Step 4: 打印结果 ----
print(f"\n{'='*60}")
print(f"优化完成! 耗时 {elapsed/60:.1f} 分钟 ({elapsed:.0f} 秒)")
print(f"帕累托前沿 {len(solutions)} 组解:")
print(f"{'Label':<14} {'Z1(cost)':>14} {'Z2(dist)':>12} {'WH':>6} {'Assigns':>8}")
print("-" * 58)
for s in solutions:
    print(f"{s['label']:<14} {s['Z1']:>14,.0f} {s['Z2']:>10.1f} km {s['n_opened']:>6} {len(s['mapping']):>8}")
