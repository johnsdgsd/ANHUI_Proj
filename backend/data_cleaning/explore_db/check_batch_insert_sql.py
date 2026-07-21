"""Check batch insert SQL patterns in DS_SQL for global optimization"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import dmPython

conn = dmPython.connect(user='NARI', password='Root1234', server='localhost', port=5236, autoCommit=False)
cursor = conn.cursor()

# Check batch insert SQL content
for sql_id in [
    'gk-adam-insert_into_adam_glob_strategy_scheme_itt',
    'gk-adam-insert_into_adam_glob_strategy_scheme_cost',
    'gk-adam-insert_into_adam_glob_strategy_scheme_lps',
    'gk-adam-insert-into-aps-inventory-replenish',
    'gk-adam-insert_dist_scheme',
]:
    try:
        cursor.execute("""
            SELECT SQL_ID,
                   UTL_RAW.CAST_TO_VARCHAR2(DBMS_LOB.SUBSTR(SQL, 4000, 1)) as SQL_TEXT
            FROM NARI.DS_SQL
            WHERE SQL_ID = ?
        """, (sql_id,))
        row = cursor.fetchone()
        if row and row[1]:
            print(f"\n=== {row[0]} ===")
            print(row[1][:1000])
        else:
            print(f"\n=== {row[0]} === NOT FOUND or empty SQL")
    except Exception as e:
        print(f"\n{sql_id}: ERROR - {e}")

# Also check current ADAM_REPLENISH_ORDER SQL
print("\n\n=== Current ADAM_REPLENISH_ORDER SQL ===")
cursor.execute("""
    SELECT SQL_ID,
           UTL_RAW.CAST_TO_VARCHAR2(DBMS_LOB.SUBSTR(SQL, 4000, 1)) as SQL_TEXT
    FROM NARI.DS_SQL
    WHERE SQL_ID = 'gk-adam-insert-into-adam-replenish-order'
""")
row = cursor.fetchone()
if row and row[1]:
    print(row[1])

cursor.close()
conn.close()
