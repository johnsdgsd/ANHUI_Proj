"""
Phase 2 (R,S) Replenishment Algorithm - Create Tables Script
Creates 4 new tables + seed data for substation replenishment
"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import dmPython

conn = dmPython.connect(
    user='NARI', password='Root1234',
    server='localhost', port=5236, autoCommit=False
)
cursor = conn.cursor()

def table_exists(name):
    try:
        cursor.execute(f"SELECT 1 FROM NARI.{name} WHERE ROWNUM = 1")
        return True
    except:
        return False

# ============================================================
# 1. ADAM_SYS_PARAM
# ============================================================
print("Creating ADAM_SYS_PARAM...")
if table_exists('ADAM_SYS_PARAM'):
    print("  Already exists, skipping")
else:
    cursor.execute("""
        CREATE TABLE NARI.ADAM_SYS_PARAM (
            REC_ORG_NO                      VARCHAR2(32)    NOT NULL,
            REPLEISHMENT_CYCLE             NUMBER(16,0),
            TARGET_CYCLE_SERVICE_LEVEL     NUMBER(16,4),
            CYCLE_BASE_START_DATE          DATE,
            PRIMARY KEY (REC_ORG_NO)
        )
    """)
    cursor.execute("""
        INSERT INTO NARI.ADAM_SYS_PARAM
        (REC_ORG_NO, REPLEISHMENT_CYCLE, TARGET_CYCLE_SERVICE_LEVEL, CYCLE_BASE_START_DATE)
        VALUES ('0000', 5, 0.95, DATE '2026-07-16')
    """)
    cursor.execute("COMMENT ON TABLE NARI.ADAM_SYS_PARAM IS 'Substation replenishment params table'")
    conn.commit()
    print("  Created OK + default config inserted")

# ============================================================
# 2. ADAM_CITY_COUNTY_STOCK_SAMPLE
# ============================================================
print("Creating ADAM_CITY_COUNTY_STOCK_SAMPLE...")
if table_exists('ADAM_CITY_COUNTY_STOCK_SAMPLE'):
    print("  Already exists, skipping")
else:
    cursor.execute("""
        CREATE TABLE NARI.ADAM_CITY_COUNTY_STOCK_SAMPLE (
            CITY_COUNTY_STOCK_SAMPLE_ID     NUMBER(16,0)    NOT NULL,
            ORG_NO                          VARCHAR2(32),
            DATA_DATE                       DATE,
            STOCK_NUM                       NUMBER(16),
            DEV_CLS                         VARCHAR2(8),
            DEV_CODE                        VARCHAR2(32),
            DEV_STAT                        VARCHAR2(8),
            OLD_NEW_FLAG                    VARCHAR2(8),
            STATI_DATE                      DATE,
            PRIMARY KEY (CITY_COUNTY_STOCK_SAMPLE_ID)
        )
    """)
    cursor.execute("COMMENT ON TABLE NARI.ADAM_CITY_COUNTY_STOCK_SAMPLE IS 'City/County stock snapshot (87 cities + substations)'")
    conn.commit()
    print("  Created OK")

# ============================================================
# 3. ADAM_SUB_DMD_PRE
# ============================================================
print("Creating ADAM_SUB_DMD_PRE...")
if table_exists('ADAM_SUB_DMD_PRE'):
    print("  Already exists, skipping")
else:
    cursor.execute("""
        CREATE TABLE NARI.ADAM_SUB_DMD_PRE (
            SUB_DMD_PRE_ID                  NUMBER(16,0)    NOT NULL,
            PRE_TYPE                        VARCHAR2(8),
            PRE_YEAR                        VARCHAR2(8),
            PRE_QUARTER                     VARCHAR2(8),
            PRE_MONTH                       VARCHAR2(8),
            PRE_WEEK                        VARCHAR2(8),
            PRE_DATE                        DATE,
            BUS_TYPE                        VARCHAR2(8),
            ORG_NO                          VARCHAR2(32),
            DEV_CODE                        VARCHAR2(32),
            PRE_NUM                         NUMBER(16,0),
            UPDATE_TIME                     DATE,
            GLOBAL_SCHEME_ID                NUMBER(16,0),
            PRIMARY KEY (SUB_DMD_PRE_ID)
        )
    """)
    cursor.execute("COMMENT ON TABLE NARI.ADAM_SUB_DMD_PRE IS 'Substation demand forecast table'")
    conn.commit()
    print("  Created OK")

# ============================================================
# 4. ADAM_REPLENISH_ORDER
# ============================================================
print("Creating ADAM_REPLENISH_ORDER...")
if table_exists('ADAM_REPLENISH_ORDER'):
    print("  Already exists, skipping")
else:
    cursor.execute("""
        CREATE TABLE NARI.ADAM_REPLENISH_ORDER (
            ORDER_ID                        NUMBER(16,0)    NOT NULL,
            ORG_NO                          VARCHAR2(32),
            DEV_CLS                         VARCHAR2(8),
            DEV_CATEG                       VARCHAR2(8),
            DEV_CODE                        VARCHAR2(32),
            REPLENISH_QTY                   NUMBER(12,2),
            TARGET_STOCK_S                  NUMBER(12,2),
            CAL_DATE                        DATE,
            CREATE_TIME                     TIMESTAMP,
            PRIMARY KEY (ORDER_ID)
        )
    """)
    cursor.execute("COMMENT ON TABLE NARI.ADAM_REPLENISH_ORDER IS 'Replenishment suggestion table'")
    conn.commit()
    print("  Created OK")

# ============================================================
# Verify
# ============================================================
print("\n" + "=" * 60)
print("Verification:")
for table in ['ADAM_SYS_PARAM', 'ADAM_CITY_COUNTY_STOCK_SAMPLE',
              'ADAM_SUB_DMD_PRE', 'ADAM_REPLENISH_ORDER']:
    cursor.execute(f"SELECT COUNT(*) FROM NARI.{table}")
    cnt = cursor.fetchone()[0]
    print(f"  NARI.{table}: {cnt} rows")

# Show ADAM_SYS_PARAM data
cursor.execute("SELECT * FROM NARI.ADAM_SYS_PARAM ORDER BY REC_ORG_NO")
print("\nADAM_SYS_PARAM seed data:")
for row in cursor.fetchall():
    print(f"  REC_ORG_NO={row[0]}, T={row[1]}, alpha={row[2]}, D0={row[3]}")

cursor.close()
conn.close()
print("\nAll tables created successfully!")
