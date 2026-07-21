"""调试 ADAM_POWER_STATION 插入编码问题"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import dmPython

conn = dmPython.connect(user='NARI', password='Root1234', server='localhost', port=5236, autoCommit=False)
cursor = conn.cursor()

# 测试1: 不带中文
try:
    cursor.execute("""
        INSERT INTO ADAM_POWER_STATION (
            STATION_ID, STATION_ORG_CODE, STATION_LON, STATION_LAT, IS_ACTIVE
        ) VALUES (?, ?, ?, ?, ?)
    """, (9999, '344010107', 117.465, 31.886, 1))
    print("测试1 OK: 纯数字插入成功")
    cursor.execute("DELETE FROM ADAM_POWER_STATION WHERE STATION_ID = 9999")
    conn.commit()
except Exception as e:
    print(f"测试1 FAIL: {e}")

# 测试2: 加英文名称
try:
    cursor.execute("""
        INSERT INTO ADAM_POWER_STATION (
            STATION_ID, STATION_ORG_CODE, STATION_NAME, STATION_LON, STATION_LAT, IS_ACTIVE
        ) VALUES (?, ?, ?, ?, ?, ?)
    """, (9998, '344010107', 'Test Station', 117.465, 31.886, 1))
    print("测试2 OK: 英文名称插入成功")
    cursor.execute("DELETE FROM ADAM_POWER_STATION WHERE STATION_ID = 9998")
    conn.commit()
except Exception as e:
    print(f"测试2 FAIL: {e}")

# 测试3: 加中文名称
try:
    cursor.execute("""
        INSERT INTO ADAM_POWER_STATION (
            STATION_ID, STATION_ORG_CODE, STATION_NAME, STATION_LON, STATION_LAT, IS_ACTIVE
        ) VALUES (?, ?, ?, ?, ?, ?)
    """, (9997, '344010107', '城关中心供电所', 117.465, 31.886, 1))
    print("测试3 OK: 中文名称插入成功")
    cursor.execute("DELETE FROM ADAM_POWER_STATION WHERE STATION_ID = 9997")
    conn.commit()
except Exception as e:
    print(f"测试3 FAIL: {e}")

# 测试4: 中文名称 + 中文地址
try:
    cursor.execute("""
        INSERT INTO ADAM_POWER_STATION (
            STATION_ID, STATION_ORG_CODE, STATION_NAME, STATION_ADDR,
            STATION_LON, STATION_LAT, IS_ACTIVE
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (9996, '344010107', '城关中心供电所', '合肥市肥东县店埠镇龙泉路76号', 117.465, 31.886, 1))
    print("测试4 OK: 中文名称+地址插入成功")
    cursor.execute("DELETE FROM ADAM_POWER_STATION WHERE STATION_ID = 9996")
    conn.commit()
except Exception as e:
    print(f"测试4 FAIL: {e}")

# 测试5: 完整字段
import pandas as pd
import os

base = r"D:\WYJ\库存优化与检定排程\Proj"
for root, dirs, files in os.walk(base):
    for f in files:
        if f == 'ADAM_POWER_STATION.xlsx':
            path = os.path.join(root, f)
            break

df = pd.read_excel(path)
# 读第一行数据
row = df.iloc[0]
print(f"\n测试5: 插入第一条真实数据")
print(f"  STATION_ID={row['STATION_ID']} ({type(row['STATION_ID']).__name__})")
print(f"  STATION_ORG_CODE={row['STATION_ORG_CODE']} ({type(row['STATION_ORG_CODE']).__name__})")
print(f"  STATION_NAME={row['STATION_NAME']} ({type(row['STATION_NAME']).__name__})")
print(f"  STATION_ADDR={row['STATION_ADDR']} ({type(row['STATION_ADDR']).__name__}, len={len(str(row['STATION_ADDR']))})")
print(f"  STATION_LON={row['STATION_LON']} ({type(row['STATION_LON']).__name__})")
print(f"  STATION_LAT={row['STATION_LAT']} ({type(row['STATION_LAT']).__name__})")
print(f"  ORG_NO={row['ORG_NO']} ({type(row['ORG_NO']).__name__})")
print(f"  DISTANCE={row['DISTANCE']} ({type(row['DISTANCE']).__name__})")
print(f"  STATION_COST={row['STATION_COST']} ({type(row['STATION_COST']).__name__})")
print(f"  IS_ACTIVE={row['IS_ACTIVE']} ({type(row['IS_ACTIVE']).__name__})")

# 看 NaN 值
import math

station_org_code = row['STATION_ORG_CODE']
if pd.notna(station_org_code):
    station_org_code = str(int(station_org_code))
else:
    station_org_code = None

org_no = row['ORG_NO']
if pd.notna(org_no):
    org_no = str(int(org_no))
else:
    org_no = None

distance = row['DISTANCE']
if pd.notna(distance):
    distance = float(distance)
else:
    distance = None

# station_cost NaN → None
cost = None if pd.isna(row['STATION_COST']) else float(row['STATION_COST'])

try:
    cursor.execute("""
        INSERT INTO ADAM_POWER_STATION (
            STATION_ID, STATION_ORG_CODE, STATION_NAME, STATION_ADDR,
            STATION_LON, STATION_LAT, ORG_NO, DISTANCE, STATION_COST, IS_ACTIVE
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        int(row['STATION_ID']),
        station_org_code,
        str(row['STATION_NAME']),
        str(row['STATION_ADDR']),
        float(row['STATION_LON']),
        float(row['STATION_LAT']),
        org_no,
        distance,
        cost,
        int(row['IS_ACTIVE']),
    ))
    print("测试5 OK: 完整真实数据插入成功")
    conn.rollback()
except Exception as e:
    print(f"测试5 FAIL: {e}")
    conn.rollback()

# 测试6: 逐个字段排查
print("\n逐个字段排查...")
fields = [
    ('STATION_ID', lambda: int(row['STATION_ID'])),
    ('STATION_ORG_CODE', lambda: str(int(row['STATION_ORG_CODE'])) if pd.notna(row['STATION_ORG_CODE']) else None),
    ('STATION_NAME', lambda: str(row['STATION_NAME'])),
    ('STATION_ADDR', lambda: str(row['STATION_ADDR'])),
    ('STATION_LON', lambda: float(row['STATION_LON'])),
    ('STATION_LAT', lambda: float(row['STATION_LAT'])),
    ('ORG_NO', lambda: str(int(row['ORG_NO'])) if pd.notna(row['ORG_NO']) else None),
    ('DISTANCE', lambda: float(row['DISTANCE']) if pd.notna(row['DISTANCE']) else None),
    ('STATION_COST', lambda: None),
    ('IS_ACTIVE', lambda: int(row['IS_ACTIVE'])),
]

for name, val_fn in fields:
    # 只用已确认能插入的字段 + 当前字段
    vals = {}
    for n, vf in fields:
        vals[n] = vf()

    cols = ", ".join(f'"{f}"' for f in vals)
    placeholders = ", ".join(["?"] * len(vals))
    sql = f"INSERT INTO ADAM_POWER_STATION ({cols}) VALUES ({placeholders})"
    params = tuple(vals.values())
    try:
        cursor.execute(sql, params)
        print(f"  {name}: OK")
        conn.rollback()
    except Exception as e:
        print(f"  {name}: FAIL - {e}")
        conn.rollback()

conn.close()
