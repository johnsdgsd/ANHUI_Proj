"""
将 ADAM_POWER_STATION.xlsx 数据插入数据库 ADAM_POWER_STATION 表。
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import dmPython
import pandas as pd
import os

# 读取 Excel
base = r"D:\WYJ\库存优化与检定排程\Proj"
for root, dirs, files in os.walk(base):
    for f in files:
        if f == 'ADAM_POWER_STATION.xlsx':
            path = os.path.join(root, f)
            break

print(f"读取: {path}")
df = pd.read_excel(path)
print(f"共 {len(df)} 行")

conn = dmPython.connect(user='NARI', password='Root1234', server='localhost', port=5236, autoCommit=False)
cursor = conn.cursor()

# 清空已有数据
cursor.execute("SELECT COUNT(*) FROM ADAM_POWER_STATION")
existing = cursor.fetchone()[0]
print(f"表中已有 {existing} 条数据")
if existing > 0:
    print("清空已有数据...")
    cursor.execute("DELETE FROM ADAM_POWER_STATION")
    conn.commit()


def safe_int(val):
    if pd.isna(val):
        return None
    return int(val)


def safe_float(val):
    if pd.isna(val):
        return None
    return float(val)


def safe_str_int(val):
    if pd.isna(val):
        return None
    return str(int(val))


def safe_str_gbk(val):
    """安全字符串：过滤 GBK 无法编码的字符"""
    if pd.isna(val):
        return None
    s = str(val)
    clean = []
    for c in s:
        try:
            c.encode('gbk')
            clean.append(c)
        except UnicodeEncodeError:
            clean.append('?')  # 替换为问号
    return ''.join(clean)


sql = """
    INSERT INTO ADAM_POWER_STATION (
        STATION_ID, STATION_ORG_CODE, STATION_NAME, STATION_ADDR,
        STATION_LON, STATION_LAT, ORG_NO, DISTANCE, STATION_COST, IS_ACTIVE
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

batch_size = 200
total = len(df)
success = 0
failed = 0
skipped_chars = []

for start in range(0, total, batch_size):
    end = min(start + batch_size, total)
    batch = df.iloc[start:end]

    for i, (_, row) in enumerate(batch.iterrows()):
        station_name = safe_str_gbk(row['STATION_NAME'])
        station_addr = safe_str_gbk(row['STATION_ADDR'])

        # 记录被替换的字符
        orig_name = str(row['STATION_NAME']) if pd.notna(row['STATION_NAME']) else ''
        if station_name and station_name != orig_name:
            skipped_chars.append((row['STATION_ID'], 'STATION_NAME', orig_name, station_name))
        orig_addr = str(row['STATION_ADDR']) if pd.notna(row['STATION_ADDR']) else ''
        if station_addr and station_addr != orig_addr:
            skipped_chars.append((row['STATION_ID'], 'STATION_ADDR', orig_addr, station_addr))

        try:
            cursor.execute(sql, (
                safe_int(row['STATION_ID']),
                safe_str_int(row['STATION_ORG_CODE']),
                station_name,
                station_addr,
                safe_float(row['STATION_LON']),
                safe_float(row['STATION_LAT']),
                safe_str_int(row['ORG_NO']),
                safe_float(row['DISTANCE']),
                safe_float(row['STATION_COST']),
                safe_int(row['IS_ACTIVE']) if pd.notna(row['IS_ACTIVE']) else 1,
            ))
            success += 1
        except Exception as e:
            failed += 1
            if failed <= 5:
                print(f"  失败 STATION_ID={row['STATION_ID']}: {e}")

    conn.commit()
    batch_no = start // batch_size + 1
    print(f"  批次 {batch_no}: {start+1}-{end} 完成, 成功 {success}/{total}")

# 验证
cursor.execute("SELECT COUNT(*) FROM ADAM_POWER_STATION")
final_count = cursor.fetchone()[0]
print(f"\n完成！表中 {final_count} 条数据 (成功 {success}, 失败 {failed})")

if skipped_chars:
    print(f"\n因 GBK 编码限制，以下 {len(skipped_chars)} 处非标字符被替换为 '?':")
    for sid, col, orig, clean in skipped_chars:
        print(f"  STATION_ID={sid} {col}: {repr(orig)} -> {repr(clean)}")

# 抽样
cursor.execute("SELECT STATION_ID, STATION_ORG_CODE, STATION_NAME, ORG_NO FROM ADAM_POWER_STATION WHERE ROWNUM <= 3")
for row in cursor.fetchall():
    print(f"  {row}")

cursor.close()
conn.close()
