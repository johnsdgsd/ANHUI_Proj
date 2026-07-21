"""查看仓网布局各表数据样本"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import dmPython

conn = dmPython.connect(user='NARI', password='Root1234', server='localhost', port=5236, autoCommit=True)
cursor = conn.cursor()

print("=== ADAM_WAREHOUSE_CANDIDATE (前3行) ===")
cursor.execute("SELECT * FROM ADAM_WAREHOUSE_CANDIDATE WHERE ROWNUM <= 3")
cols = [d[0] for d in cursor.description]
print(f"列: {cols}")
for r in cursor.fetchall():
    print(f"  {r}")

print("\n=== ADAM_POWER_STATION (前3行) ===")
cursor.execute("SELECT * FROM ADAM_POWER_STATION WHERE ROWNUM <= 3")
for r in cursor.fetchall():
    print(f"  {r}")

print("\n=== ADAM_STATION_YEAR_DEMAND (前3行) ===")
cursor.execute("SELECT * FROM ADAM_STATION_YEAR_DEMAND WHERE ROWNUM <= 3")
cols = [d[0] for d in cursor.description]
print(f"列: {cols}")
for r in cursor.fetchall():
    print(f"  {r}")
# DISTINCT DEV_CODE and FORECAST_YEAR
cursor.execute("SELECT DISTINCT FORECAST_YEAR FROM ADAM_STATION_YEAR_DEMAND")
print(f"FORECAST_YEAR: {[r[0] for r in cursor.fetchall()]}")
cursor.execute("SELECT COUNT(DISTINCT DEV_CODE) FROM ADAM_STATION_YEAR_DEMAND")
print(f"DEV_CODE 种类: {cursor.fetchone()[0]}")
cursor.execute("SELECT COUNT(DISTINCT STATION_ORG_CODE) FROM ADAM_STATION_YEAR_DEMAND")
print(f"STATION_ORG_CODE 数量: {cursor.fetchone()[0]}")

print("\n=== ADAM_STATION_DIST_MIST (前3行) ===")
cursor.execute("SELECT * FROM ADAM_STATION_DIST_MIST WHERE ROWNUM <= 3")
cols = [d[0] for d in cursor.description]
print(f"列: {cols}")
for r in cursor.fetchall():
    print(f"  {r}")
cursor.execute("SELECT COUNT(DISTINCT ORG_NO), COUNT(DISTINCT STATION_ORG_CODE) FROM ADAM_STATION_DIST_MIST")
print(f"ORG_NO 数量: {cursor.fetchone()}")
# 看 DISTANCE 范围
cursor.execute("SELECT MIN(DISTANCE), MAX(DISTANCE), AVG(DISTANCE) FROM ADAM_STATION_DIST_MIST")
print(f"DISTANCE: {cursor.fetchone()}")

print("\n=== 候选库房 FIXED_COST_F, TRANS_DIST 分布 ===")
cursor.execute("SELECT MIN(FIXED_COST_F), MAX(FIXED_COST_F), AVG(FIXED_COST_F) FROM ADAM_WAREHOUSE_CANDIDATE")
print(f"FIXED_COST_F: {cursor.fetchone()}")
cursor.execute("SELECT MIN(TRANS_DIST), MAX(TRANS_DIST), AVG(TRANS_DIST) FROM ADAM_WAREHOUSE_CANDIDATE WHERE IS_ACTIVE = 1")
print(f"TRANS_DIST (active): {cursor.fetchone()}")

# 看候选库房的 ORG_NO 是否与距离表的 ORG_NO 对应
cursor.execute("SELECT DISTINCT w.ORG_NO FROM ADAM_WAREHOUSE_CANDIDATE w WHERE w.IS_ACTIVE = 1 AND ROWNUM <= 5")
print(f"\n候选库房 ORG_NO 示例: {[r[0] for r in cursor.fetchall()]}")

conn.close()
