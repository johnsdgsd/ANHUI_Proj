"""
Verify Phase 2 implementation:
  - 4 tables exist in DB
  - DS_SQL entries correct
  - fetch_data functions importable
  - algorithm module importable
  - API blueprint importable
"""
import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Path setup — need parent-of-backend in sys.path for "import backend.xxx"
_BASE = os.path.dirname(os.path.abspath(__file__))
_PROJ = os.path.dirname(os.path.dirname(os.path.dirname(_BASE)))
sys.path.insert(0, _PROJ)

import dmPython

print("=" * 60)
print("1. Database tables verification")
print("=" * 60)
conn = dmPython.connect(user='NARI', password='Root1234', server='localhost', port=5236, autoCommit=False)
cursor = conn.cursor()

for table in ['ADAM_SYS_PARAM', 'ADAM_CITY_COUNTY_STOCK_SAMPLE', 'ADAM_SUB_DMD_PRE', 'ADAM_REPLENISH_ORDER']:
    cursor.execute(f"SELECT COUNT(*) FROM NARI.{table}")
    cnt = cursor.fetchone()[0]
    # Check columns
    cursor.execute(f"SELECT COUNT(*) FROM USER_TAB_COLUMNS WHERE TABLE_NAME = '{table}'")
    ncol = cursor.fetchone()[0]
    print(f"  NARI.{table}: {ncol} columns, {cnt} rows")

cursor.execute("SELECT * FROM NARI.ADAM_SYS_PARAM")
params = cursor.fetchall()
print(f"\n  ADAM_SYS_PARAM data ({len(params)} rows):")
for p in params:
    print(f"    REC_ORG_NO={p[0]}, T={p[1]}, alpha={p[2]}, D0={p[3]}")

# DS_SQL verification
print("\n" + "=" * 60)
print("2. DS_SQL entries verification")
print("=" * 60)
cursor.execute("""
    SELECT SQL_ID, SQL_DESC, EXEC_TYPE, SQL_TAG, LENGTH(SQL) as sql_len
    FROM NARI.DS_SQL
    WHERE SQL_ID LIKE 'gk-adam-%-adam-%' AND SQL_ID NOT LIKE 'gk-adam-%-adam-%dist%'
      AND SQL_ID NOT LIKE 'gk-adam-%-adam-%scheme%'
      AND SQL_ID NOT LIKE 'gk-adam-%-adam-%plan%'
      AND SQL_ID NOT LIKE 'gk-adam-%-adam-%stock%'
      AND SQL_ID NOT LIKE 'gk-adam-%-adam-%glob%'
      AND SQL_ID NOT LIKE 'gk-adam-%-adam-%allot%'
    ORDER BY SQL_ID
""")
# Simpler: just check our 4
cursor.execute("""
    SELECT SQL_ID, SQL_DESC, EXEC_TYPE, SQL_TAG, LENGTH(SQL) as sql_len
    FROM NARI.DS_SQL
    WHERE SQL_ID IN (
        'gk-adam-query-adam-sys-param',
        'gk-adam-query-adam-city-county-stock-sample',
        'gk-adam-query-adam-sub-dmd-pre',
        'gk-adam-insert-into-adam-replenish-order'
    )
""")
for row in cursor.fetchall():
    print(f"  {row[0]:<55} TYPE={row[2]} TAG={row[3]} DESC={row[1]}")

cursor.close()
conn.close()

print("\n" + "=" * 60)
print("3. Python import verification")
print("=" * 60)

# Test fetch_data imports
try:
    from backend.api.data_api.fetch_data import (
        query_adam_sys_param,
        query_adam_city_county_stock_sample,
        query_adam_sub_dmd_pre,
        insert_into_adam_replenish_order,
    )
    print("  fetch_data functions: OK")
except Exception as e:
    print(f"  fetch_data functions: FAIL - {e}")

# Test algorithm imports
try:
    from backend.algorithm.substation.config_loader import load_substation_params, get_substation_param
    from backend.algorithm.substation.algorithm import compute_rs_plan, is_replenishment_day, is_holiday
    from backend.algorithm.substation.run_replenishment import run_substation_replenishment
    print("  algorithm modules: OK")
except Exception as e:
    print(f"  algorithm modules: FAIL - {e}")

# Test API import
try:
    from backend.api.business_api.SubstationReplenishmentApi import substation_replenish_bp
    print("  API blueprint: OK")
    print(f"    blueprint name: {substation_replenish_bp.name}")
    print(f"    url_prefix: {substation_replenish_bp.url_prefix}")
except Exception as e:
    print(f"  API blueprint: FAIL - {e}")

print("\n" + "=" * 60)
print("Verification complete!")
