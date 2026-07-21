"""Check ADAM_Y_MGT_ORG DIST_LV and VALID_FLAG values"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import dmPython

conn = dmPython.connect(user='NARI', password='Root1234', server='localhost', port=5236, autoCommit=False)
cursor = conn.cursor()

# DIST_LV distribution
print("=== DIST_LV values ===")
cursor.execute("""
    SELECT DIST_LV, COUNT(*), MIN(MGT_ORG_CODE), MAX(MGT_ORG_CODE)
    FROM NARI.ADAM_Y_MGT_ORG
    GROUP BY DIST_LV
    ORDER BY DIST_LV
""")
for row in cursor.fetchall():
    print(f"  DIST_LV={row[0]}: {row[1]} orgs, codes {row[2]} ~ {row[3]}")

# VALID_FLAG distribution
print("\n=== VALID_FLAG values ===")
cursor.execute("""
    SELECT VALID_FLAG, COUNT(*)
    FROM NARI.ADAM_Y_MGT_ORG
    GROUP BY VALID_FLAG
    ORDER BY VALID_FLAG
""")
for row in cursor.fetchall():
    print(f"  VALID_FLAG={row[0]}: {row[1]} orgs")

# Code length distribution by DIST_LV
print("\n=== Code length by DIST_LV ===")
cursor.execute("""
    SELECT DIST_LV, LENGTH(MGT_ORG_CODE) as code_len, COUNT(*)
    FROM NARI.ADAM_Y_MGT_ORG
    WHERE VALID_FLAG = '02'
    GROUP BY DIST_LV, LENGTH(MGT_ORG_CODE)
    ORDER BY DIST_LV, code_len
""")
for row in cursor.fetchall():
    print(f"  DIST_LV={row[0]}, len={row[1]}: {row[2]} orgs")

# Sample substation-level orgs (VALID_FLAG=02)
print("\n=== Sample substations (DIST_LV, code length 9, VALID_FLAG=02) ===")
cursor.execute("""
    SELECT MGT_ORG_CODE, MGT_ORG_NAME, DIST_LV, PRNT_MGT_ORG_CODE
    FROM NARI.ADAM_Y_MGT_ORG
    WHERE VALID_FLAG = '02' AND LENGTH(MGT_ORG_CODE) >= 7
    FETCH FIRST 15 ROWS ONLY
""")
for row in cursor.fetchall():
    print(f"  {row[0]:<12} {row[1]:<25} LV={row[2]} PARENT={row[3]}")

# County-level orgs
print("\n=== Sample counties (VALID_FLAG=02, code length 7) ===")
cursor.execute("""
    SELECT MGT_ORG_CODE, MGT_ORG_NAME, DIST_LV, PRNT_MGT_ORG_CODE
    FROM NARI.ADAM_Y_MGT_ORG
    WHERE VALID_FLAG = '02' AND LENGTH(MGT_ORG_CODE) = 7
    FETCH FIRST 15 ROWS ONLY
""")
for row in cursor.fetchall():
    print(f"  {row[0]:<12} {row[1]:<25} LV={row[2]} PARENT={row[3]}")

# Check if there are 9-digit orgs and their DIST_LV
print("\n=== 9-digit orgs ===")
cursor.execute("""
    SELECT DIST_LV, COUNT(*)
    FROM NARI.ADAM_Y_MGT_ORG
    WHERE LENGTH(MGT_ORG_CODE) = 9 AND VALID_FLAG = '02'
    GROUP BY DIST_LV
""")
for row in cursor.fetchall():
    print(f"  DIST_LV={row[0]}: {row[1]} orgs")

cursor.close()
conn.close()
