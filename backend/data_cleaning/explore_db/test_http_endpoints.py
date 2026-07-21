"""Test Phase 2 HTTP endpoints with new DB-side filtering SQL."""
import sys, io, os, time, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import requests

EXEC_URL = "http://localhost:8081/exec"
session = requests.Session()

def post_exec(endpoint, json_data, label=""):
    url = f"{EXEC_URL}/{endpoint}"
    try:
        resp = session.post(url, json=json_data, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            count = len(data) if isinstance(data, list) else 1
            print(f"  [{label or endpoint}] 200 OK — {count} rows")
            return data
        else:
            print(f"  [{label or endpoint}] {resp.status_code} — {resp.text[:200]}")
            return None
    except Exception as e:
        print(f"  [{label or endpoint}] ERROR: {e}")
        return None

print("=" * 60)
print("Phase 2 HTTP Endpoint Tests (DB-side filtering)")
print("=" * 60)

# Test 1: Query sys params
print("\n--- Test 1: ADAM_SYS_PARAM ---")
data = post_exec("gk-adam-query-adam-sys-param", {}, "sys-param")
if data:
    for row in (data if isinstance(data, list) else [data]):
        print(f"    REC_ORG_NO={row.get('REC_ORG_NO')}, T={row.get('REPLEISHMENT_CYCLE')}, alpha={row.get('TARGET_CYCLE_SERVICE_LEVEL')}, D0={row.get('CYCLE_BASE_START_DATE')}")

# Test 2: Query stock with the new EXISTS filter
print("\n--- Test 2: Stock (EXISTS filter) ---")
data = post_exec("gk-adam-query-adam-city-county-stock-sample", {"data_date": "2026-07-17"}, "stock")
if data and isinstance(data, list):
    for row in data[:3]:
        print(f"    ORG_NO={row.get('ORG_NO')}, DEV_CODE={row.get('DEV_CODE')}, STOCK_NUM={row.get('STOCK_NUM')}, DEV_STAT={row.get('DEV_STAT')}, OLD_NEW_FLAG={row.get('OLD_NEW_FLAG')}")

# Test 3: Query demand with the new EXISTS filter
print("\n--- Test 3: Demand (EXISTS filter) ---")
data = post_exec("gk-adam-query-adam-sub-dmd-pre", {
    "pre_type": "05",
    "start_date": "2026-07-19",
    "end_date": "2026-07-25"
}, "demand")
if data and isinstance(data, list):
    for row in data[:3]:
        print(f"    ORG_NO={row.get('ORG_NO')}, DEV_CODE={row.get('DEV_CODE')}, PRE_DATE={row.get('PRE_DATE')}, PRE_NUM={row.get('PRE_NUM')}")

# Test 4: Insert replenish order (batch)
print("\n--- Test 4: Insert replenish order (batch) ---")
test_records = [
    {"order_id": 88801, "org_no": "344010107", "dev_cls": "01", "dev_categ": "01_01",
     "dev_code": "34000196", "replenish_qty": 100.0, "target_stock_s": 150.0,
     "cal_date": "2026-07-19", "create_time": "2026-07-18 18:00:00"},
    {"order_id": 88802, "org_no": "344010109", "dev_cls": "01", "dev_categ": "01_01",
     "dev_code": "34000196", "replenish_qty": 80.0, "target_stock_s": 120.0,
     "cal_date": "2026-07-19", "create_time": "2026-07-18 18:00:00"},
]
data = post_exec("gk-adam-insert-into-adam-replenish-order", test_records, "insert")
if data:
    print(f"    Response: {json.dumps(data, ensure_ascii=False, default=str)[:200]}")

# Cleanup test insert
print("\n--- Cleanup test records ---")
import dmPython
conn = dmPython.connect(user='NARI', password='Root1234', server='localhost', port=5236, autoCommit=True)
cursor = conn.cursor()
cursor.execute("DELETE FROM ADAM_REPLENISH_ORDER WHERE ORDER_ID IN (88801, 88802)")
print(f"    Deleted {cursor.rowcount} test records")
cursor.close()
conn.close()

print("\n" + "=" * 60)
print("Tests complete! Check results above.")
