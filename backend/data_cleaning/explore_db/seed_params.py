"""Insert seed data into ADAM_SYS_PARAM"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import dmPython

conn = dmPython.connect(user='NARI', password='Root1234', server='localhost', port=5236, autoCommit=False)
cursor = conn.cursor()

# Default config (REC_ORG_NO='0000')
cursor.execute("""
    INSERT INTO NARI.ADAM_SYS_PARAM
    (REC_ORG_NO, REPLEISHMENT_CYCLE, TARGET_CYCLE_SERVICE_LEVEL, CYCLE_BASE_START_DATE)
    VALUES ('0000', 5, 0.95, DATE '2026-07-16')
""")
print("Default config inserted: REC_ORG_NO='0000', T=5, alpha=0.95, D0=2026-07-16")

conn.commit()

# Verify
cursor.execute("SELECT * FROM NARI.ADAM_SYS_PARAM ORDER BY REC_ORG_NO")
print("\nADAM_SYS_PARAM data:")
for row in cursor.fetchall():
    print(f"  REC_ORG_NO={row[0]}, T={row[1]}, alpha={row[2]}, D0={row[3]}")

cursor.close()
conn.close()
print("\nDone!")
