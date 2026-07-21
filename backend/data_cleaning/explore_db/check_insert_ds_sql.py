"""查看已有的 INSERT 类型 DS_SQL 条目"""
import dmPython

conn = dmPython.connect(user='NARI', password='Root1234', server='localhost', port=5236, autoCommit=True)
cursor = conn.cursor()

# 查看已有的 INSERT 类型 DS_SQL 条目（EXEC_TYPE=1）
cursor.execute("SELECT SQL_ID, SQL_DESC, EXEC_TYPE, EXEC_USER, SQL_TAG, SQL_DETAIL FROM NARI.ADAM_DS_SQL WHERE EXEC_TYPE = '1' AND SQL_ID LIKE 'gk-adam%'")
rows = cursor.fetchall()
for row in rows:
    print('='*80)
    print(f'SQL_ID: {row[0]}')
    print(f'SQL_DESC: {row[1]}')
    print(f'EXEC_TYPE: {row[2]}')
    print(f'EXEC_USER: {row[3]}')
    print(f'SQL_TAG: {row[4]}')
    detail = row[5]
    if detail:
        print(f'SQL_DETAIL (len={len(detail)}):')
        print(detail[:1000])
    print()

# 也看看二阶段相关的 EXEC_TYPE=4 的条目，了解 SQL_ID 命名规范
print("\n\n=== 二阶段相关 DS_SQL 条目 ===")
cursor.execute("SELECT SQL_ID, SQL_DESC, EXEC_TYPE, EXEC_USER, SQL_TAG FROM NARI.ADAM_DS_SQL WHERE SQL_ID LIKE 'gk-adam%' AND SQL_TAG = 'smcp'")
rows = cursor.fetchall()
for row in rows:
    print(f'{row[0]} | {row[1]} | EXEC_TYPE={row[2]} | USER={row[3]} | TAG={row[4]}')

# 查看 ADAM_DS_SQL 表结构
print("\n\n=== ADAM_DS_SQL 表结构 ===")
cursor.execute("SELECT COLUMN_NAME, DATA_TYPE, DATA_LENGTH, NULLABLE FROM USER_TAB_COLUMNS WHERE TABLE_NAME = 'ADAM_DS_SQL' ORDER BY COLUMN_ID")
for row in cursor.fetchall():
    print(f'{row[0]:30s} {row[1]:20s} len={row[2]} nullable={row[3]}')

conn.close()
