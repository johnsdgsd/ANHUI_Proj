"""Fix DEV_STAT value: '01' = 合格在库"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import dmPython

conn = dmPython.connect(user='NARI', password='Root1234', server='localhost', port=5236, autoCommit=False)
cursor = conn.cursor()

stock_sql = """SELECT s.*
FROM ADAM_CITY_COUNTY_STOCK_SAMPLE s
INNER JOIN ADAM_Y_MGT_ORG o ON s.ORG_NO = o.MGT_ORG_CODE
WHERE s.DATA_DATE = #{data_date}
  AND o.DIST_LV = '05'
  AND o.VALID_FLAG = '02'
  AND s.DEV_STAT = '01'
  AND s.OLD_NEW_FLAG = '01'"""

cursor.execute(
    "UPDATE NARI.DS_SQL SET SQL = ? WHERE SQL_ID = ?",
    (stock_sql.encode('utf-8'), 'gk-adam-query-adam-city-county-stock-sample')
)
conn.commit()
print(f"Updated: {cursor.rowcount} row(s)")

# Verify
cursor.execute("""
    SELECT UTL_RAW.CAST_TO_VARCHAR2(DBMS_LOB.SUBSTR(SQL, 4000, 1)) as SQL_TEXT
    FROM NARI.DS_SQL
    WHERE SQL_ID = 'gk-adam-query-adam-city-county-stock-sample'
""")
row = cursor.fetchone()
if row and row[0]:
    print("\n=== Updated SQL ===")
    print(row[0])

cursor.close()
conn.close()
print("\nDone!")
