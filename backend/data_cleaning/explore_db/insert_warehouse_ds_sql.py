"""
Insert 6 DS_SQL entries for Warehouse Layout Optimization.
EXEC_TYPE: 1=insert, 4=query (SELECT)
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import dmPython

conn = dmPython.connect(user='NARI', password='Root1234', server='localhost', port=5236, autoCommit=False)
cursor = conn.cursor()

entries = [
    # ---- 1. Query ADAM_STATION_YEAR_DEMAND (with device mapping) ----
    {
        'SQL_ID': 'gk-adam-query-adam-station-demand-mapped',
        'SQL_DESC': '仓网布局-查询供电所年需求（含新旧设备码映射、有效设备筛选、单价、规格）',
        'EXEC_TYPE': '4',
        'SQL_TAG': 'smcp',
        'EXEC_USER': 'smcp_dm',
        'SQL': """WITH
TARGET_DEV AS (
    SELECT DISTINCT DEV_CODE
    FROM ADAM_PRE_RANGE_INFO
    WHERE STAT = '01'
),
RAW_DEMAND AS (
    SELECT STATION_ORG_CODE, DEV_CODE, ANNUAL_DEMAND
    FROM ADAM_STATION_YEAR_DEMAND
),
MAPPED_DEMAND AS (
    SELECT
        d.STATION_ORG_CODE,
        NVL(m.NEW_DEV_CODE, d.DEV_CODE) AS DEV_CODE,
        SUM(d.ANNUAL_DEMAND) AS ANNUAL_DEMAND
    FROM RAW_DEMAND d
    LEFT JOIN ADAM_NEW_OLD_DEVCODE_LINK m
        ON d.DEV_CODE = m.OLD_DEV_CODE
    WHERE NVL(m.NEW_DEV_CODE, d.DEV_CODE) IN (SELECT DEV_CODE FROM TARGET_DEV)
    GROUP BY d.STATION_ORG_CODE, NVL(m.NEW_DEV_CODE, d.DEV_CODE)
)
SELECT
    md.STATION_ORG_CODE,
    md.DEV_CODE,
    s.DEV_CLS,
    s.DEV_CATEG,
    s.DEV_CODE_DESC,
    p.AVG_PRICE,
    md.ANNUAL_DEMAND
FROM MAPPED_DEMAND md
LEFT JOIN ADAM_SPEC_CODE_CONFIG s ON md.DEV_CODE = s.DEV_CODE
LEFT JOIN ADAM_PRE_RANGE_INFO p ON md.DEV_CODE = p.DEV_CODE
ORDER BY md.STATION_ORG_CODE, md.DEV_CODE""",
    },

    # ---- 2. Query ADAM_WAREHOUSE_CANDIDATE ----
    {
        'SQL_ID': 'gk-adam-query-adam-warehouse-candidate',
        'SQL_DESC': '仓网布局-查询候选库房',
        'EXEC_TYPE': '4',
        'SQL_TAG': 'smcp',
        'EXEC_USER': 'smcp_dm',
        'SQL': 'SELECT WH_ID, ORG_NO, WH_NAME, WH_LON, WH_LAT, WH_ADDR, FIXED_COST_F, TRANS_DIST, IS_ACTIVE FROM ADAM_WAREHOUSE_CANDIDATE WHERE IS_ACTIVE = 1',
    },

    # ---- 3. Query ADAM_POWER_STATION (active) ----
    {
        'SQL_ID': 'gk-adam-query-adam-power-station-active',
        'SQL_DESC': '仓网布局-查询活跃供电所',
        'EXEC_TYPE': '4',
        'SQL_TAG': 'smcp',
        'EXEC_USER': 'smcp_dm',
        'SQL': 'SELECT STATION_ID, STATION_ORG_CODE, STATION_NAME, STATION_ADDR, STATION_LON, STATION_LAT, ORG_NO, IS_ACTIVE FROM ADAM_POWER_STATION WHERE IS_ACTIVE = 1',
    },

    # ---- 4. Query ADAM_STATION_DIST_MIST ----
    {
        'SQL_ID': 'gk-adam-query-adam-station-dist-mist',
        'SQL_DESC': '仓网布局-查询供电所与各市县距离矩阵',
        'EXEC_TYPE': '4',
        'SQL_TAG': 'smcp',
        'EXEC_USER': 'smcp_dm',
        'SQL': 'SELECT STATION_MIST_ID, ORG_NO, STATION_ORG_CODE, DISTANCE FROM ADAM_STATION_DIST_MIST',
    },

    # ---- 5. Insert ADAM_LAYOUT_RESULT ----
    {
        'SQL_ID': 'gk-adam-insert-adam-layout-result',
        'SQL_DESC': '仓网布局-插入结果主表',
        'EXEC_TYPE': '1',
        'SQL_TAG': 'smcp',
        'EXEC_USER': 'smcp_dm',
        'SQL': """INSERT INTO ADAM_LAYOUT_RESULT (
    "RESULT_ID", "SCENARIO_CODE", "WEIGHT",
    "OBJECTIVE_COST", "OBJECTIVE_DIST", "CREATE_TIME"
) VALUES (
    #{result_id}, #{scenario_code}, #{weight},
    #{objective_cost}, #{objective_dist}, #{create_time}
)""",
    },

    # ---- 6. Insert ADAM_LAYOUT_RESULT_DET ----
    {
        'SQL_ID': 'gk-adam-insert-adam-layout-result-det',
        'SQL_DESC': '仓网布局-插入结果明细表',
        'EXEC_TYPE': '1',
        'SQL_TAG': 'smcp',
        'EXEC_USER': 'smcp_dm',
        'SQL': """INSERT INTO ADAM_LAYOUT_RESULT_DET (
    "RESULT_DET_ID", "RESULT_ID", "SCENARIO_CODE",
    "ORG_NO", "STATION_ORG_CODE", "CREATE_TIME"
) VALUES (
    #{result_det_id}, #{result_id}, #{scenario_code},
    #{org_no}, #{station_org_code}, #{create_time}
)""",
    },
]

for entry in entries:
    cursor.execute("SELECT 1 FROM NARI.DS_SQL WHERE SQL_ID = ?", (entry['SQL_ID'],))
    if cursor.fetchone():
        print(f"DELETE OLD: {entry['SQL_ID']}")
        cursor.execute("DELETE FROM NARI.DS_SQL WHERE SQL_ID = ?", (entry['SQL_ID'],))

    sql_bytes = entry['SQL'].encode('utf-8')
    cursor.execute("""
        INSERT INTO NARI.DS_SQL (SQL_ID, SQL_DESC, EXEC_TYPE, SQL, EXEC_USER, SQL_TAG)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (entry['SQL_ID'], entry['SQL_DESC'], entry['EXEC_TYPE'],
          sql_bytes, entry['EXEC_USER'], entry['SQL_TAG']))
    print(f"INSERT OK: {entry['SQL_ID']}")

conn.commit()

# Verify
print("\n=== 验证 ===")
cursor.execute("""
    SELECT SQL_ID, EXEC_TYPE, LENGTH(SQL) as slen
    FROM NARI.DS_SQL
    WHERE SQL_ID IN (
        'gk-adam-query-adam-station-demand-mapped',
        'gk-adam-query-adam-warehouse-candidate',
        'gk-adam-query-adam-power-station-active',
        'gk-adam-query-adam-station-dist-mist',
        'gk-adam-insert-adam-layout-result',
        'gk-adam-insert-adam-layout-result-det'
    )
    ORDER BY SQL_ID
""")
for row in cursor.fetchall():
    print(f"  {row[0]:<55} TYPE={row[2]} LEN={row[2]}")

cursor.close()
conn.close()
print("\n完成！请重启中间件使新条目生效。")
