"""
更新 DS_SQL: ADAM_MODEL_THINK_LOG 改为单条插入（非批量）。
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import dmPython

conn = dmPython.connect(user='NARI', password='Root1234', server='localhost', port=5236, autoCommit=False)
cursor = conn.cursor()

SQL_ID = 'gk-adam-insert-into-adam-model-think-log'

SQL = """INSERT INTO ADAM_MODEL_THINK_LOG (
    "MODEL_THINK_LOG_ID", "PRE_CONC_ID", "MODEL_NO", "THINK_LOG", "CREATE_TIME"
) VALUES (
    #{model_think_log_id},
    #{pre_conc_id},
    #{model_no},
    #{think_log},
    #{create_time}
)"""

cursor.execute("UPDATE NARI.DS_SQL SET SQL = ? WHERE SQL_ID = ?", (SQL.encode('utf-8'), SQL_ID))
conn.commit()

# 验证
cursor.execute("SELECT SQL FROM NARI.DS_SQL WHERE SQL_ID = ?", (SQL_ID,))
row = cursor.fetchone()
if row and row[0]:
    s = row[0] if isinstance(row[0], str) else row[0].decode('utf-8')
    print(s)

cursor.close()
conn.close()
print("\n更新完成！")
