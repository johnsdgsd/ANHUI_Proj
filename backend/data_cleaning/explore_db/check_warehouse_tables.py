"""核查仓网布局相关表是否存在及数据量"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import dmPython

conn = dmPython.connect(user='NARI', password='Root1234', server='localhost', port=5236, autoCommit=True)
cursor = conn.cursor()

tables = [
    'ADAM_SYS_PARAM',
    'ADAM_WAREHOUSE_CANDIDATE',
    'ADAM_POWER_STATION',
    'ADAM_STATION_YEAR_DEMAND',
    'ADAM_STATION_DIST_MIST',
    'ADAM_LAYOUT_RESULT',
    'ADAM_LAYOUT_RESULT_DET',
]

for t in tables:
    try:
        cursor.execute(f"SELECT COUNT(*) FROM {t}")
        cnt = cursor.fetchone()[0]
        print(f"{t:35s} 存在, {cnt} 行")
        # 列结构
        cursor.execute(f"SELECT COLUMN_NAME, DATA_TYPE FROM USER_TAB_COLUMNS WHERE TABLE_NAME = '{t}' ORDER BY COLUMN_ID")
        cols = [f"{r[0]}({r[1]})" for r in cursor.fetchall()]
        print(f"  列: {', '.join(cols)}")
    except Exception as e:
        print(f"{t:35s} 不存在或无权访问: {e}")

# 查 SYS_PARAM 内容
print("\n=== ADAM_SYS_PARAM 内容 ===")
try:
    cursor.execute("SELECT * FROM ADAM_SYS_PARAM")
    for r in cursor.fetchall():
        print(f"  {r}")
except Exception as e:
    print(f"  查询失败: {e}")

conn.close()
