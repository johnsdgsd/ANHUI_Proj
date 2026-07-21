"""Fix stock + demand DS_SQL: use EXISTS subquery instead of JOIN"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import dmPython

conn = dmPython.connect(user='NARI', password='Root1234', server='localhost', port=5236, autoCommit=False)
cursor = conn.cursor()

# Stock: filter by org + quality via subquery
stock_sql = """SELECT s.*
FROM ADAM_CITY_COUNTY_STOCK_SAMPLE s
WHERE s.DATA_DATE = #{data_date}
  AND s.DEV_STAT = '01'
  AND s.OLD_NEW_FLAG = '01'
  AND EXISTS (
    SELECT 1 FROM ADAM_Y_MGT_ORG o
    WHERE o.MGT_ORG_CODE = s.ORG_NO
      AND o.DIST_LV = '05'
      AND o.VALID_FLAG = '02'
  )"""

cursor.execute(
    "UPDATE NARI.DS_SQL SET SQL = ? WHERE SQL_ID = ?",
    (stock_sql.encode('utf-8'), 'gk-adam-query-adam-city-county-stock-sample')
)

# Demand: filter by org via subquery
demand_sql = """SELECT d.*
FROM ADAM_SUB_DMD_PRE d
WHERE d.PRE_TYPE = #{pre_type}
  AND d.PRE_DATE >= #{start_date}
  AND d.PRE_DATE <= #{end_date}
  AND EXISTS (
    SELECT 1 FROM ADAM_Y_MGT_ORG o
    WHERE o.MGT_ORG_CODE = d.ORG_NO
      AND o.DIST_LV = '05'
      AND o.VALID_FLAG = '02'
  )"""

cursor.execute(
    "UPDATE NARI.DS_SQL SET SQL = ? WHERE SQL_ID = ?",
    (demand_sql.encode('utf-8'), 'gk-adam-query-adam-sub-dmd-pre')
)

conn.commit()
print(f"Updated: {cursor.rowcount} row(s)")

# Verify both
for sid in ['gk-adam-query-adam-city-county-stock-sample', 'gk-adam-query-adam-sub-dmd-pre']:
    cursor.execute("""
        SELECT SQL_ID,
               UTL_RAW.CAST_TO_VARCHAR2(DBMS_LOB.SUBSTR(SQL, 4000, 1)) as SQL_TEXT
        FROM NARI.DS_SQL WHERE SQL_ID = ?
    """, (sid,))
    row = cursor.fetchone()
    if row and row[0]:
        print(f"\n{'='*60}")
        print(f"  {row[0]}")
        print(f"{'='*60}")
        print(row[1])

# Test SQL syntax via direct execution
print("\n\n=== 语法验证 ===")
print("\n[Stock SQL] 测试执行...")
try:
    cursor.execute("""
        SELECT s.*
        FROM ADAM_CITY_COUNTY_STOCK_SAMPLE s
        WHERE s.DATA_DATE = '2026-07-17'
          AND s.DEV_STAT = '01'
          AND s.OLD_NEW_FLAG = '01'
          AND EXISTS (
            SELECT 1 FROM ADAM_Y_MGT_ORG o
            WHERE o.MGT_ORG_CODE = s.ORG_NO
              AND o.DIST_LV = '05'
              AND o.VALID_FLAG = '02'
          )
    """)
    print(f"  OK — {len(cursor.fetchall())} rows")
except Exception as e:
    print(f"  FAIL: {e}")

print("\n[Demand SQL] 测试执行...")
try:
    cursor.execute("""
        SELECT d.*
        FROM ADAM_SUB_DMD_PRE d
        WHERE d.PRE_TYPE = '05'
          AND d.PRE_DATE >= '2026-07-19'
          AND d.PRE_DATE <= '2026-07-25'
          AND EXISTS (
            SELECT 1 FROM ADAM_Y_MGT_ORG o
            WHERE o.MGT_ORG_CODE = d.ORG_NO
              AND o.DIST_LV = '05'
              AND o.VALID_FLAG = '02'
          )
    """)
    print(f"  OK — {len(cursor.fetchall())} rows")
except Exception as e:
    print(f"  FAIL: {e}")

cursor.close()
conn.close()
print("\nDone!")
