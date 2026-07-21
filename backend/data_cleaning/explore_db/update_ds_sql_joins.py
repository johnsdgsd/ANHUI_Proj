"""Update DS_SQL entries with JOINs and database-side filtering.
- Stock: JOIN ADAM_Y_MGT_ORG, filter DIST_LV='05', VALID_FLAG='02', DEV_STAT, OLD_NEW_FLAG
- Demand: JOIN ADAM_Y_MGT_ORG, filter DIST_LV='05', VALID_FLAG='02'
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import dmPython

conn = dmPython.connect(user='NARI', password='Root1234', server='localhost', port=5236, autoCommit=False)
cursor = conn.cursor()

# ---- Step 1: Check DEV_STAT and OLD_NEW_FLAG values in stock table ----
print("=== DEV_STAT values in ADAM_CITY_COUNTY_STOCK_SAMPLE ===")
try:
    cursor.execute("SELECT DEV_STAT, COUNT(*) FROM ADAM_CITY_COUNTY_STOCK_SAMPLE GROUP BY DEV_STAT")
    for row in cursor.fetchall():
        print(f"  DEV_STAT='{row[0]}': {row[1]} rows")
except Exception as e:
    print(f"  (no data or error): {e}")

print("\n=== OLD_NEW_FLAG values in ADAM_CITY_COUNTY_STOCK_SAMPLE ===")
try:
    cursor.execute("SELECT OLD_NEW_FLAG, COUNT(*) FROM ADAM_CITY_COUNTY_STOCK_SAMPLE GROUP BY OLD_NEW_FLAG")
    for row in cursor.fetchall():
        print(f"  OLD_NEW_FLAG='{row[0]}': {row[1]} rows")
except Exception as e:
    print(f"  (no data or error): {e}")

# ---- Step 2: Verify JOIN works ----
print("\n=== Test JOIN: stock + org (DIST_LV='05', VALID_FLAG='02') ===")
test_sql = """
SELECT COUNT(*) as cnt
FROM ADAM_CITY_COUNTY_STOCK_SAMPLE s
INNER JOIN ADAM_Y_MGT_ORG o ON s.ORG_NO = o.MGT_ORG_CODE
WHERE o.DIST_LV = '05'
  AND o.VALID_FLAG = '02'
  AND s.DEV_STAT = '合格在库'
  AND s.OLD_NEW_FLAG = '01'
"""
try:
    cursor.execute(test_sql)
    row = cursor.fetchone()
    print(f"  Matching rows: {row[0] if row else 'None'}")
except Exception as e:
    print(f"  Error: {e}")

print("\n=== Test JOIN: demand + org (DIST_LV='05', VALID_FLAG='02') ===")
test_sql2 = """
SELECT COUNT(*) as cnt
FROM ADAM_SUB_DMD_PRE d
INNER JOIN ADAM_Y_MGT_ORG o ON d.ORG_NO = o.MGT_ORG_CODE
WHERE o.DIST_LV = '05'
  AND o.VALID_FLAG = '02'
"""
try:
    cursor.execute(test_sql2)
    row = cursor.fetchone()
    print(f"  Matching rows: {row[0] if row else 'None'}")
except Exception as e:
    print(f"  Error: {e}")

# ---- Step 3: Update DS_SQL entries ----
print("\n=== Updating DS_SQL entries ===")

# 3a. Stock query — JOIN with org, filter by substation + quality
stock_sql = """SELECT s.*
FROM ADAM_CITY_COUNTY_STOCK_SAMPLE s
INNER JOIN ADAM_Y_MGT_ORG o ON s.ORG_NO = o.MGT_ORG_CODE
WHERE s.DATA_DATE = #{data_date}
  AND o.DIST_LV = '05'
  AND o.VALID_FLAG = '02'
  AND s.DEV_STAT = '合格在库'
  AND s.OLD_NEW_FLAG = '01'"""

cursor.execute(
    "UPDATE NARI.DS_SQL SET SQL = ?, SQL_DESC = ? WHERE SQL_ID = ?",
    (stock_sql.encode('utf-8'),
     '查询供电所实时库存快照（仅供电所+合格在库+新设备）',
     'gk-adam-query-adam-city-county-stock-sample')
)
print(f"  Updated stock query: {cursor.rowcount} row(s)")

# 3b. Demand query — JOIN with org, filter by substation
demand_sql = """SELECT d.*
FROM ADAM_SUB_DMD_PRE d
INNER JOIN ADAM_Y_MGT_ORG o ON d.ORG_NO = o.MGT_ORG_CODE
WHERE d.PRE_TYPE = #{pre_type}
  AND d.PRE_DATE >= #{start_date}
  AND d.PRE_DATE <= #{end_date}
  AND o.DIST_LV = '05'
  AND o.VALID_FLAG = '02'"""

cursor.execute(
    "UPDATE NARI.DS_SQL SET SQL = ?, SQL_DESC = ? WHERE SQL_ID = ?",
    (demand_sql.encode('utf-8'),
     '查询供电所日需求预测（仅有效供电所）',
     'gk-adam-query-adam-sub-dmd-pre')
)
print(f"  Updated demand query: {cursor.rowcount} row(s)")

conn.commit()

# ---- Step 4: Verify updated entries ----
print("\n=== Verifying updated DS_SQL entries ===")
for sid in ['gk-adam-query-adam-city-county-stock-sample', 'gk-adam-query-adam-sub-dmd-pre']:
    cursor.execute("""
        SELECT SQL_ID, SQL_DESC, EXEC_TYPE, SQL_TAG,
               UTL_RAW.CAST_TO_VARCHAR2(DBMS_LOB.SUBSTR(SQL, 4000, 1)) as SQL_TEXT
        FROM NARI.DS_SQL
        WHERE SQL_ID = ?
    """, (sid,))
    row = cursor.fetchone()
    if row:
        print(f"\n--- {row[0]} ---")
        print(f"  DESC: {row[1]}")
        print(f"  TYPE: {row[2]}, TAG: {row[3]}")
        print(f"  SQL:")
        for line in row[4].split('\n'):
            print(f"    {line}")

cursor.close()
conn.close()
print("\nDone!")
