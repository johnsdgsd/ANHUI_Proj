"""
Insert DS_SQL entry for ADAM_MODEL_THINK_LOG 模型思考日志表.
EXEC_TYPE=1 (insert), EXEC_USER=smcp_dm.
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import dmPython

conn = dmPython.connect(user='NARI', password='Root1234', server='localhost', port=5236, autoCommit=False)
cursor = conn.cursor()

SQL_ID = 'gk-adam-insert-into-adam-model-think-log'
SQL_DESC = '插入模型思考日志'

SQL = """INSERT INTO ADAM_MODEL_THINK_LOG (
    "MODEL_THINK_LOG_ID", "PRE_CONC_ID", "MODEL_NO", "THINK_LOG", "CREATE_TIME"
) VALUES
<foreach collection="list" item="item" separator=",">
    (
        #{item.model_think_log_id, jdbcType=NUMERIC},
        #{item.pre_conc_id, jdbcType=NUMERIC},
        #{item.model_no, jdbcType=VARCHAR},
        #{item.think_log, jdbcType=VARCHAR},
        #{item.create_time, jdbcType=TIMESTAMP}
    )
</foreach>"""

# Check if already exists
cursor.execute("SELECT 1 FROM NARI.DS_SQL WHERE SQL_ID = ?", (SQL_ID,))
if cursor.fetchone():
    print(f"已存在，先删除: {SQL_ID}")
    cursor.execute("DELETE FROM NARI.DS_SQL WHERE SQL_ID = ?", (SQL_ID,))

sql_bytes = SQL.encode('utf-8')
cursor.execute("""
    INSERT INTO NARI.DS_SQL (SQL_ID, SQL_DESC, EXEC_TYPE, SQL, EXEC_USER, SQL_TAG)
    VALUES (?, ?, ?, ?, ?, ?)
""", (SQL_ID, SQL_DESC, '1', sql_bytes, 'smcp_dm', 'smcp'))
print(f"INSERT OK: {SQL_ID}")

conn.commit()

# Verify
print("\n=== 验证 ===")
cursor.execute("""
    SELECT SQL_ID, SQL_DESC, EXEC_TYPE, EXEC_USER, SQL_TAG, LENGTH(SQL) as sql_len
    FROM NARI.DS_SQL
    WHERE SQL_ID = ?
""", (SQL_ID,))
row = cursor.fetchone()
if row:
    print(f"  SQL_ID: {row[0]}")
    print(f"  SQL_DESC: {row[1]}")
    print(f"  EXEC_TYPE: {row[2]}")
    print(f"  EXEC_USER: {row[3]}")
    print(f"  SQL_TAG: {row[4]}")
    print(f"  SQL_LEN: {row[5]}")
    print(f"  SQL:")
    cursor.execute("SELECT SQL FROM NARI.DS_SQL WHERE SQL_ID = ?", (SQL_ID,))
    r = cursor.fetchone()
    if r and r[0]:
        s = r[0] if isinstance(r[0], str) else r[0].decode('utf-8')
        print(s)

cursor.close()
conn.close()
print("\n完成！")
