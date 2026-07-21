"""
Insert 4 DS_SQL entries for Phase 2 (R,S) replenishment algorithm.
EXEC_TYPE: 1=insert, 4=query (SELECT)
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import dmPython

conn = dmPython.connect(user='NARI', password='Root1234', server='localhost', port=5236, autoCommit=False)
cursor = conn.cursor()

entries = [
    # ---- 1. Query ADAM_SYS_PARAM ----
    {
        'SQL_ID': 'gk-adam-query-adam-sys-param',
        'SQL_DESC': 'Query substation replenishment system parameters',
        'EXEC_TYPE': '4',
        'SQL_TAG': 'smcp',
        'EXEC_USER': 'NARI',
        'SQL': 'SELECT * FROM NARI.ADAM_SYS_PARAM',
    },
    # ---- 2. Query ADAM_CITY_COUNTY_STOCK_SAMPLE ----
    {
        'SQL_ID': 'gk-adam-query-adam-city-county-stock-sample',
        'SQL_DESC': 'Query city/county stock snapshot by data date',
        'EXEC_TYPE': '4',
        'SQL_TAG': 'smcp',
        'EXEC_USER': 'NARI',
        'SQL': 'SELECT * FROM NARI.ADAM_CITY_COUNTY_STOCK_SAMPLE WHERE DATA_DATE = #{data_date}',
    },
    # ---- 3. Query ADAM_SUB_DMD_PRE ----
    {
        'SQL_ID': 'gk-adam-query-adam-sub-dmd-pre',
        'SQL_DESC': 'Query substation daily demand forecast by date range',
        'EXEC_TYPE': '4',
        'SQL_TAG': 'smcp',
        'EXEC_USER': 'NARI',
        'SQL': 'SELECT * FROM NARI.ADAM_SUB_DMD_PRE WHERE PRE_TYPE = #{pre_type} AND PRE_DATE >= #{start_date} AND PRE_DATE <= #{end_date}',
    },
    # ---- 4. Insert into ADAM_REPLENISH_ORDER ----
    {
        'SQL_ID': 'gk-adam-insert-into-adam-replenish-order',
        'SQL_DESC': 'Insert replenishment suggestion record',
        'EXEC_TYPE': '1',
        'SQL_TAG': 'smcp',
        'EXEC_USER': 'NARI',
        'SQL': 'INSERT INTO NARI.ADAM_REPLENISH_ORDER (ORDER_ID, ORG_NO, DEV_CLS, DEV_CATEG, DEV_CODE, REPLENISH_QTY, TARGET_STOCK_S, CAL_DATE, CREATE_TIME) VALUES (#{order_id}, #{org_no}, #{dev_cls}, #{dev_categ}, #{dev_code}, #{replenish_qty}, #{target_stock_s}, #{cal_date}, #{create_time})',
    },
]

for entry in entries:
    # Check if already exists
    cursor.execute("SELECT 1 FROM NARI.DS_SQL WHERE SQL_ID = ?", (entry['SQL_ID'],))
    rows = cursor.fetchall()
    if rows:
        print(f"SKIP (exists): {entry['SQL_ID']}")
        continue

    sql_bytes = entry['SQL'].encode('utf-8')
    cursor.execute("""
        INSERT INTO NARI.DS_SQL (SQL_ID, SQL_DESC, EXEC_TYPE, SQL, EXEC_USER, SQL_TAG)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (entry['SQL_ID'], entry['SQL_DESC'], entry['EXEC_TYPE'],
          sql_bytes, entry['EXEC_USER'], entry['SQL_TAG']))
    print(f"INSERT OK: {entry['SQL_ID']}")

conn.commit()

# Verify
print("\n" + "=" * 60)
print("Verification - new DS_SQL entries:")
cursor.execute("""
    SELECT SQL_ID, SQL_DESC, EXEC_TYPE, LENGTH(SQL) as sql_len
    FROM NARI.DS_SQL
    WHERE SQL_ID IN (
        'gk-adam-query-adam-sys-param',
        'gk-adam-query-adam-city-county-stock-sample',
        'gk-adam-query-adam-sub-dmd-pre',
        'gk-adam-insert-into-adam-replenish-order'
    )
    ORDER BY SQL_ID
""")
for row in cursor.fetchall():
    print(f"  {row[0]:<55} TYPE={row[2]} LEN={row[3]} DESC={row[1]}")

cursor.close()
conn.close()
print("\nDS_SQL entries inserted successfully!")
