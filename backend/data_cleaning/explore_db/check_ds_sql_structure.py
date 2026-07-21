"""查看 DS_SQL 表结构和已有的 gk-adam INSERT 条目"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import dmPython

conn = dmPython.connect(user='NARI', password='Root1234', server='localhost', port=5236, autoCommit=True)
cursor = conn.cursor()

# DS_SQL 表结构
print("=== DS_SQL 表结构 ===")
cursor.execute("SELECT COLUMN_NAME, DATA_TYPE, DATA_LENGTH, NULLABLE FROM USER_TAB_COLUMNS WHERE TABLE_NAME = 'DS_SQL' ORDER BY COLUMN_ID")
for row in cursor.fetchall():
    print(f"  {row[0]:30s} {row[1]:20s} len={row[2]} nullable={row[3]}")

# 已有的 INSERT 类型条目
print("\n=== 已有的 gk-adam INSERT 条目 ===")
cursor.execute("SELECT SQL_ID, SQL_DESC, EXEC_TYPE, EXEC_USER, SQL_TAG, LENGTH(SQL) as sql_len FROM NARI.DS_SQL WHERE EXEC_TYPE = '1' AND SQL_ID LIKE 'gk-adam%'")
for row in cursor.fetchall():
    print(f"  {row[0]:<55} DESC={row[1]:<40} USER={row[3]:<12} TAG={row[4]:<8} LEN={row[5]}")

# 查看一个 INSERT 的 SQL 详情
print("\n=== gk-adam-insert-into-adam-replenish-order SQL ===")
cursor.execute("SELECT SQL FROM NARI.DS_SQL WHERE SQL_ID = 'gk-adam-insert-into-adam-replenish-order'")
row = cursor.fetchone()
if row and row[0]:
    sql = row[0]
    if isinstance(sql, bytes):
        sql = sql.decode('utf-8')
    print(f"  {sql}")

# 检查 ADAM_MODEL_THINK_LOG 表是否存在
print("\n=== 检查 ADAM_MODEL_THINK_LOG 表 ===")
cursor.execute("SELECT COLUMN_NAME, DATA_TYPE, DATA_LENGTH, NULLABLE FROM USER_TAB_COLUMNS WHERE TABLE_NAME = 'ADAM_MODEL_THINK_LOG' ORDER BY COLUMN_ID")
cols = cursor.fetchall()
if cols:
    for row in cols:
        print(f"  {row[0]:30s} {row[1]:20s} len={row[2]} nullable={row[3]}")
else:
    print("  表不存在或无权访问")

# 也查看 PRE_CONC_ID 相关表
print("\n=== PRE_CONC_ID 相关表 ===")
cursor.execute("SELECT TABLE_NAME FROM USER_TAB_COLUMNS WHERE COLUMN_NAME = 'PRE_CONC_ID'")
for row in cursor.fetchall():
    print(f"  {row[0]}")

conn.close()
