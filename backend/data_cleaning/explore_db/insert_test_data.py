"""Insert test data and test HTTP endpoints for Phase 2."""
import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# ---- Part 1: Find real substations via dmPython ----
import dmPython
conn = dmPython.connect(user='NARI', password='Root1234', server='localhost', port=5236, autoCommit=False)
cursor = conn.cursor()

print("=== 查找真实供电所 (DIST_LV='05', VALID_FLAG='02') ===")
cursor.execute("""
    SELECT o.MGT_ORG_CODE, o.MGT_ORG_NAME, o.PRNT_MGT_ORG_CODE
    FROM ADAM_Y_MGT_ORG o
    WHERE o.DIST_LV = '05' AND o.VALID_FLAG = '02'
    FETCH FIRST 5 ROWS ONLY
""")
substations = cursor.fetchall()
for row in substations:
    print(f"  {row[0]} | {row[1]} | parent={row[2]}")

if not substations:
    print("  ERROR: No substations found!")
    cursor.close(); conn.close(); exit(1)

# Use first 2 substations
org1, name1, parent1 = substations[0][0], substations[0][1], substations[0][2]
org2, name2, parent2 = substations[1][0], substations[1][1], substations[1][2]

# Find valid DEV_CODE from spec config
print("\n=== 查找有效 DEV_CODE ===")
cursor.execute("""
    SELECT DEV_CODE, DEV_CLS, DEV_CATEG
    FROM ADAM_SPEC_CODE_CONFIG
    FETCH FIRST 3 ROWS ONLY
""")
devices = cursor.fetchall()
for row in devices:
    print(f"  DEV_CODE={row[0]}, CLS={row[1]}, CATEG={row[2]}")

if not devices:
    print("  ERROR: No device codes found!")
    cursor.close(); conn.close(); exit(1)

dev_code = devices[0][0]
dev_cls = devices[0][1]

# ---- Part 2: Insert test stock data ----
print("\n=== 插入测试库存数据 ===")
# Get max PK
cursor.execute("SELECT NVL(MAX(CITY_COUNTY_STOCK_SAMPLE_ID), 0) FROM ADAM_CITY_COUNTY_STOCK_SAMPLE")
max_id = cursor.fetchone()[0]

test_stock = [
    (max_id + 1, org1, '2026-07-17', 50.0, dev_cls, dev_code, '01', '01', '2026-07-17'),
    (max_id + 2, org2, '2026-07-17', 30.0, dev_cls, dev_code, '01', '01', '2026-07-17'),
]
for row in test_stock:
    cursor.execute("""
        INSERT INTO ADAM_CITY_COUNTY_STOCK_SAMPLE
        (CITY_COUNTY_STOCK_SAMPLE_ID, ORG_NO, DATA_DATE, STOCK_NUM, DEV_CLS, DEV_CODE, DEV_STAT, OLD_NEW_FLAG, STATI_DATE)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, row)
print(f"  Inserted {len(test_stock)} stock rows: {org1}({50}件), {org2}({30}件)")

# ---- Part 3: Insert test demand data ----
print("\n=== 插入测试需求预测数据 ===")
cursor.execute("SELECT NVL(MAX(SUB_DMD_PRE_ID), 0) FROM ADAM_SUB_DMD_PRE")
max_dmd_id = cursor.fetchone()[0]

# 7 days of demand from 2026-07-19 (tomorrow = 2026-07-19)
test_demand = []
for day in range(7):
    d = f"2026-07-{19 + day}"
    test_demand.append((max_dmd_id + 1 + day, '05', d, '01', org1, dev_code, 3.0, '2026-07-18'))
    test_demand.append((max_dmd_id + 8 + day, '05', d, '02', org2, dev_code, 2.0, '2026-07-18'))

for row in test_demand:
    cursor.execute("""
        INSERT INTO ADAM_SUB_DMD_PRE
        (SUB_DMD_PRE_ID, PRE_TYPE, PRE_DATE, BUS_TYPE, ORG_NO, DEV_CODE, PRE_NUM, UPDATE_TIME)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, row)
print(f"  Inserted {len(test_demand)} demand rows: {org1}(3/day), {org2}(2/day) for 7 days")

# ---- Part 4: Ensure sys_params exist for these orgs ----
print("\n=== 检查/插入供电所参数 ===")
cursor.execute("SELECT COUNT(*) FROM ADAM_SYS_PARAM WHERE REC_ORG_NO = '0000'")
if cursor.fetchone()[0] == 0:
    cursor.execute("""
        INSERT INTO ADAM_SYS_PARAM (REC_ORG_NO, REPLEISHMENT_CYCLE, TARGET_CYCLE_SERVICE_LEVEL, CYCLE_BASE_START_DATE)
        VALUES ('0000', 5, 0.95, '2026-07-16')
    """)
    print("  Inserted default params ('0000': T=5, alpha=0.95, D0=2026-07-16)")

# Insert specific params for test orgs
for org, t_val in [(org1, 7), (org2, 3)]:
    cursor.execute("SELECT COUNT(*) FROM ADAM_SYS_PARAM WHERE REC_ORG_NO = ?", (org,))
    if cursor.fetchone()[0] == 0:
        cursor.execute("""
            INSERT INTO ADAM_SYS_PARAM (REC_ORG_NO, REPLEISHMENT_CYCLE, TARGET_CYCLE_SERVICE_LEVEL, CYCLE_BASE_START_DATE)
            VALUES (?, ?, ?, ?)
        """, (org, t_val, 0.95, '2026-07-16'))
        print(f"  Inserted params for {org}: T={t_val}")

conn.commit()

# ---- Part 5: Verify test data ----
print("\n=== 验证测试数据 ===")
cursor.execute("SELECT COUNT(*) FROM ADAM_CITY_COUNTY_STOCK_SAMPLE WHERE DATA_DATE = '2026-07-17' AND DEV_STAT = '01' AND OLD_NEW_FLAG = '01'")
print(f"  Stock rows (2026-07-17, 合格在库, 新): {cursor.fetchone()[0]}")

cursor.execute("SELECT COUNT(*) FROM ADAM_SUB_DMD_PRE WHERE PRE_TYPE = '05' AND PRE_DATE >= '2026-07-19'")
print(f"  Demand rows (PRE_TYPE=05, >= 2026-07-19): {cursor.fetchone()[0]}")

cursor.execute("SELECT COUNT(*) FROM ADAM_SYS_PARAM")
print(f"  Sys params: {cursor.fetchone()[0]} rows")

# Verify the SQL with EXISTS works
print("\n=== 验证 DS_SQL EXISTS 过滤 ===")
cursor.execute("""
    SELECT COUNT(*)
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
print(f"  Stock after EXISTS filter: {cursor.fetchone()[0]} rows")

cursor.execute("""
    SELECT COUNT(*)
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
print(f"  Demand after EXISTS filter: {cursor.fetchone()[0]} rows")

cursor.close()
conn.close()
print("\nTest data ready!")
