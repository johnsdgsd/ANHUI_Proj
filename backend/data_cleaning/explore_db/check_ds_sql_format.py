"""
查询 DS_SQL 表中 gk-adam 开头的样例数据，了解插入格式
"""
import dmPython

conn = dmPython.connect(
    user='NARI', password='Root1234',
    server='localhost', port=5236, autoCommit=False
)
cursor = conn.cursor()

# 查看 DS_SQL 表结构
print("=" * 80)
print("DS_SQL 表结构:")
cursor.execute("""
    SELECT COLUMN_NAME, DATA_TYPE, DATA_LENGTH, NULLABLE, COLUMN_ID
    FROM USER_TAB_COLUMNS
    WHERE TABLE_NAME = 'DS_SQL'
    ORDER BY COLUMN_ID
""")
for c in cursor.fetchall():
    print(f"  {c[0]:<25} {str(c[1]):<15} len={c[2]}  nullable={c[3]}")

# 查看列注释
cursor.execute("""
    SELECT COLUMN_NAME, COMMENTS
    FROM USER_COL_COMMENTS
    WHERE TABLE_NAME = 'DS_SQL'
""")
for c in cursor.fetchall():
    if c[1]:
        print(f"  [{c[0]}] {c[1]}")

# 查几条 gk-adam 样例
print("\n" + "=" * 80)
print("gk-adam 样例 (前 5 条):")
cursor.execute("""
    SELECT SQL_ID, SQL_DESC, EXEC_TYPE, BIZ_TYPE, SQL_TAG, IS_FLAG
    FROM NARI.DS_SQL
    WHERE SQL_ID LIKE 'gk-adam%'
    FETCH FIRST 5 ROWS ONLY
""")
cols = [d[0] for d in cursor.description]
for row in cursor.fetchall():
    print(f"\n  SQL_ID: {row[0]}")
    for i, c in enumerate(cols[1:], 1):
        if row[i]:
            val = str(row[i])[:80]
            print(f"  {c}: {val}")

# 查一条完整的（含 BLOB 大小）
print("\n" + "=" * 80)
print("完整样例 (SQL_ID, SQL 长度, SQL_DM 长度):")
cursor.execute("""
    SELECT SQL_ID, LENGTH(SQL) as sql_len, LENGTH(SQL_DM) as dm_len
    FROM NARI.DS_SQL
    WHERE SQL_ID LIKE 'gk-adam%'
    FETCH FIRST 10 ROWS ONLY
""")
for row in cursor.fetchall():
    print(f"  {row[0]:<55} SQL={row[1]}  SQL_DM={row[2]}")

# 查一条 insert 类型的样例
print("\n" + "=" * 80)
print("insert 类型样例:")
cursor.execute("""
    SELECT SQL_ID, SQL_DESC, EXEC_TYPE, BIZ_TYPE
    FROM NARI.DS_SQL
    WHERE SQL_ID LIKE 'gk-adam-insert%'
    FETCH FIRST 5 ROWS ONLY
""")
for row in cursor.fetchall():
    print(f"  {row[0]:<55} TYPE={row[2]} BIZ={row[3]} DESC={row[1]}")

# 查询类型的样例
print("\n" + "=" * 80)
print("query 类型样例:")
cursor.execute("""
    SELECT SQL_ID, SQL_DESC, EXEC_TYPE, BIZ_TYPE
    FROM NARI.DS_SQL
    WHERE SQL_ID LIKE 'gk-adam-query%'
    FETCH FIRST 10 ROWS ONLY
""")
for row in cursor.fetchall():
    print(f"  {row[0]:<55} TYPE={row[2]} BIZ={row[3]} DESC={row[1]}")

# 看 BLOB 中 SQL 文本的样例（取一条 query 和一条 insert）
print("\n" + "=" * 80)
print("实际 SQL 内容样例:")
for sql_id in ['gk-adam-query-aps-device-install-by-month-range', 'gk-adam-insert-into-adam-plan-day-ias-pre']:
    try:
        cursor.execute("""
            SELECT SQL_ID,
                   UTL_RAW.CAST_TO_VARCHAR2(DBMS_LOB.SUBSTR(SQL, 4000, 1)) as SQL_TEXT
            FROM NARI.DS_SQL
            WHERE SQL_ID = ?
        """, (sql_id,))
        row = cursor.fetchone()
        if row and row[1]:
            print(f"\n  [{row[0]}]:")
            print(f"  {row[1][:500]}")
    except Exception as e:
        print(f"  {sql_id}: 读取失败 ({e})")

cursor.close()
conn.close()
