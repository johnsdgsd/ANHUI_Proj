import pandas as pd, os
base = r'D:\WYJ\库存优化与检定排程\Proj'
for root, dirs, files in os.walk(base):
    for f in files:
        if f == 'ADAM_POWER_STATION.xlsx':
            path = os.path.join(root, f)
df = pd.read_excel(path)
row = df[df['STATION_ID'] == 513]
print("各列值:")
for col in row.columns:
    val = row[col].values[0]
    print(f"  {col}: {repr(val)}")

addr = str(row['STATION_ADDR'].values[0])
print(f"\n地址: {addr}")
print(f"地址repr: {repr(addr)}")
for i, c in enumerate(addr):
    try:
        c.encode('gbk')
    except:
        print(f"  pos={i} U+{ord(c):04X} char={repr(c)}")
