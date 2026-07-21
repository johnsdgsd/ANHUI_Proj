"""Fix DS_SQL entries - remove NARI. schema prefix from SQL"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import dmPython

conn = dmPython.connect(user='NARI', password='Root1234', server='localhost', port=5236, autoCommit=False)
cursor = conn.cursor()

# Delete old
for sid in [
    'gk-adam-query-adam-sys-param',
    'gk-adam-query-adam-city-county-stock-sample',
    'gk-adam-query-adam-sub-dmd-pre',
    'gk-adam-insert-into-adam-replenish-order',
]:
    cursor.execute("DELETE FROM NARI.DS_SQL WHERE SQL_ID = ?", (sid,))

# Re-insert WITHOUT NARI. schema prefix
entries = [
    {
        'SQL_ID': 'gk-adam-query-adam-sys-param',
        'SQL_DESC': '查询供电所补库系统参数',
        'EXEC_TYPE': '4',
        'SQL_TAG': 'smcp',
        'SQL': 'SELECT * FROM ADAM_SYS_PARAM',
    },
    {
        'SQL_ID': 'gk-adam-query-adam-city-county-stock-sample',
        'SQL_DESC': '根据数据日期查询市县公司库存快照',
        'EXEC_TYPE': '4',
        'SQL_TAG': 'smcp',
        'SQL': 'SELECT * FROM ADAM_CITY_COUNTY_STOCK_SAMPLE WHERE DATA_DATE = #{data_date}',
    },
    {
        'SQL_ID': 'gk-adam-query-adam-sub-dmd-pre',
        'SQL_DESC': '根据预测类型和日期范围查询供电所日需求预测',
        'EXEC_TYPE': '4',
        'SQL_TAG': 'smcp',
        'SQL': 'SELECT * FROM ADAM_SUB_DMD_PRE WHERE PRE_TYPE = #{pre_type} AND PRE_DATE >= #{start_date} AND PRE_DATE <= #{end_date}',
    },
    {
        'SQL_ID': 'gk-adam-insert-into-adam-replenish-order',
        'SQL_DESC': '插入补货建议记录到ADAM_REPLENISH_ORDER表',
        'EXEC_TYPE': '1',
        'SQL_TAG': 'smcp',
        'SQL': 'INSERT INTO ADAM_REPLENISH_ORDER (ORDER_ID, ORG_NO, DEV_CLS, DEV_CATEG, DEV_CODE, REPLENISH_QTY, TARGET_STOCK_S, CAL_DATE, CREATE_TIME) VALUES (#{order_id}, #{org_no}, #{dev_cls}, #{dev_categ}, #{dev_code}, #{replenish_qty}, #{target_stock_s}, #{cal_date}, #{create_time})',
    },
]

for entry in entries:
    sql_bytes = entry['SQL'].encode('utf-8')
    cursor.execute(
        "INSERT INTO NARI.DS_SQL (SQL_ID, SQL_DESC, EXEC_TYPE, SQL, SQL_TAG) VALUES (?, ?, ?, ?, ?)",
        (entry['SQL_ID'], entry['SQL_DESC'], entry['EXEC_TYPE'], sql_bytes, entry['SQL_TAG'])
    )
    print(f"  OK: {entry['SQL_ID']}")

conn.commit()
cursor.close()
conn.close()
print("\nDone - SQL without schema prefix!")
