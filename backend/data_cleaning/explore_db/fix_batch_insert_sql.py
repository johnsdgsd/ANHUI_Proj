"""Fix ADAM_REPLENISH_ORDER DS_SQL to use batch foreach format"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import dmPython

conn = dmPython.connect(user='NARI', password='Root1234', server='localhost', port=5236, autoCommit=False)
cursor = conn.cursor()

# Delete old entry
cursor.execute("DELETE FROM NARI.DS_SQL WHERE SQL_ID = 'gk-adam-insert-into-adam-replenish-order'")
print("Deleted old entry")

# Insert with foreach batch format (matching global optimization pattern)
batch_sql = '''INSERT INTO ADAM_REPLENISH_ORDER (
    "ORDER_ID", "ORG_NO", "DEV_CLS", "DEV_CATEG", "DEV_CODE",
    "REPLENISH_QTY", "TARGET_STOCK_S", "CAL_DATE", "CREATE_TIME"
) VALUES
<foreach collection="list" item="item" separator=",">
    (
        #{item.order_id, jdbcType=NUMERIC},
        #{item.org_no, jdbcType=VARCHAR},
        #{item.dev_cls, jdbcType=VARCHAR},
        #{item.dev_categ, jdbcType=VARCHAR},
        #{item.dev_code, jdbcType=VARCHAR},
        #{item.replenish_qty, jdbcType=NUMERIC},
        #{item.target_stock_s, jdbcType=NUMERIC},
        #{item.cal_date, jdbcType=DATE},
        #{item.create_time, jdbcType=TIMESTAMP}
    )
</foreach>'''

cursor.execute(
    "INSERT INTO NARI.DS_SQL (SQL_ID, SQL_DESC, EXEC_TYPE, SQL, SQL_TAG) VALUES (?, ?, ?, ?, ?)",
    ('gk-adam-insert-into-adam-replenish-order',
     '批量插入补货建议记录到ADAM_REPLENISH_ORDER表',
     '1',
     batch_sql.encode('utf-8'),
     'smcp')
)
print("Inserted batch version")

conn.commit()

# Verify
cursor.execute("""
    SELECT UTL_RAW.CAST_TO_VARCHAR2(DBMS_LOB.SUBSTR(SQL, 2000, 1)) as SQL_TEXT
    FROM NARI.DS_SQL
    WHERE SQL_ID = 'gk-adam-insert-into-adam-replenish-order'
""")
row = cursor.fetchone()
if row and row[0]:
    print("\n=== Updated SQL ===")
    print(row[0])

cursor.close()
conn.close()
print("\nDone!")
