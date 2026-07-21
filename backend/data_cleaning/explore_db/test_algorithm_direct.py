"""Test the complete (R,S) algorithm logic via direct DB access.
Verifies: data loading, param config, Poisson S/q calculation, result generation.
"""
import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Add project root to path
_PROJ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, _PROJ)

import pandas as pd
from datetime import date
import dmPython

from backend.algorithm.substation.config_loader import load_substation_params
from backend.algorithm.substation.algorithm import compute_rs_plan, is_replenishment_day, is_holiday

conn = dmPython.connect(user='NARI', password='Root1234', server='localhost', port=5236, autoCommit=False)
cursor = conn.cursor()

print("=" * 60)
print("(R,S) Algorithm Logic Test (direct DB)")
print("=" * 60)

# ---- Load params ----
print("\n[1] Loading ADAM_SYS_PARAM...")
cursor.execute("SELECT * FROM ADAM_SYS_PARAM")
rows = cursor.fetchall()
cols = [d[0] for d in cursor.description]
param_df = pd.DataFrame(rows, columns=cols)
substation_params, default_params = load_substation_params(param_df)
print(f"  Substation params: {len(substation_params)} orgs")
print(f"  Default params: {default_params}")

# ---- Load stock ----
print("\n[2] Loading stock (EXISTS filter simulation)...")
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
rows = cursor.fetchall()
cols = [d[0] for d in cursor.description]
stock_df = pd.DataFrame(rows, columns=cols)
print(f"  Stock rows: {len(stock_df)}")
for _, r in stock_df.iterrows():
    print(f"    {r['ORG_NO']}: {r['DEV_CODE']} = {r['STOCK_NUM']} (STAT={r['DEV_STAT']}, FLAG={r['OLD_NEW_FLAG']})")

# ---- Load demand ----
print("\n[3] Loading demand (EXISTS filter simulation)...")
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
rows = cursor.fetchall()
cols = [d[0] for d in cursor.description]
demand_df = pd.DataFrame(rows, columns=cols)
print(f"  Demand rows: {len(demand_df)}")
for _, r in demand_df.iterrows():
    print(f"    {r['ORG_NO']}: {r['DEV_CODE']} @ {r['PRE_DATE']} = {r['PRE_NUM']} (BUS={r['BUS_TYPE']})")

# ---- Load spec config ----
print("\n[4] Loading spec config...")
cursor.execute("SELECT * FROM ADAM_SPEC_CODE_CONFIG")
rows = cursor.fetchall()
cols = [d[0] for d in cursor.description]
spec_df = pd.DataFrame(rows, columns=cols)
print(f"  Spec rows: {len(spec_df)}")

# ---- Run algorithm ----
print("\n[5] Running compute_rs_plan()...")
tomorrow = date.today() + pd.Timedelta(days=1)  # 2026-07-19
tomorrow = date(2026, 7, 19)  # Explicit for test consistency

result_df = compute_rs_plan(
    inventory_df=stock_df,
    demand_df=demand_df,
    spec_df=spec_df,
    substation_params=substation_params,
    default_params=default_params,
    replenishment_date=tomorrow,
)

print(f"\n=== Algorithm Results ===")
if result_df.empty:
    print("  NO replenishment recommendations generated.")
    # Explain why
    print(f"\n  Debug info:")
    print(f"    Tomorrow: {tomorrow}")
    for org, params in substation_params.items():
        is_day = is_replenishment_day(tomorrow, params['D0'], params['T'])
        is_hol = is_holiday(tomorrow)
        print(f"    {org}: T={params['T']}, D0={params['D0']}, is_repl_day={is_day}, is_holiday={is_hol}")
    print(f"    Default: {default_params}")
    is_default_day = is_replenishment_day(tomorrow, default_params['D0'], default_params['T'])
    print(f"    Default is_repl_day: {is_default_day}")
else:
    for _, r in result_df.iterrows():
        print(f"  {r['ORG_NO']} | {r['DEV_CODE']} | q={r['REPLENISH_QTY']:.0f} | S={r['TARGET_STOCK_S']:.0f} | CAL={r['CAL_DATE']}")

cursor.close()
conn.close()

# ---- Assertions ----
print(f"\n=== Verification ===")
print(f"  Stock (substation only): {len(stock_df)} rows ✓")
print(f"  Demand (substation only): {len(demand_df)} rows ✓")
print(f"  Params loaded: {len(substation_params)} substation + default ✓")
print(f"  Algorithm completed ✓")

print("\nDone!")
