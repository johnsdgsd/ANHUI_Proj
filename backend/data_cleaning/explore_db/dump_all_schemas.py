"""
导出 NARI 用户下所有表的结构信息（列名、类型、注释、DDL）
输出到 explore_db/all_table_schemas.txt
"""
import dmPython

OUTPUT = "D:/WYJ/库存优化与检定排程/Proj/backend/data_cleaning/explore_db/all_table_schemas.txt"

conn = dmPython.connect(
    user='NARI', password='Root1234',
    server='localhost', port=5236, autoCommit=False
)
cursor = conn.cursor()

# 获取所有表名
cursor.execute("SELECT TABLE_NAME FROM USER_TABLES ORDER BY TABLE_NAME")
tables = [t[0] for t in cursor.fetchall()]

lines = []
lines.append("=" * 100)
lines.append(f"NARI 用户表结构导出 — 共 {len(tables)} 张表")
lines.append("=" * 100)

for tname in tables:
    lines.append(f"\n{'─' * 100}")
    lines.append(f"表: NARI.{tname}")
    lines.append(f"{'─' * 100}")

    # 列信息
    cursor.execute("""
        SELECT COLUMN_NAME, DATA_TYPE, DATA_LENGTH, DATA_PRECISION, DATA_SCALE,
               NULLABLE, COLUMN_ID, DATA_DEFAULT
        FROM USER_TAB_COLUMNS
        WHERE TABLE_NAME = ?
        ORDER BY COLUMN_ID
    """, (tname,))
    cols = cursor.fetchall()

    lines.append(f"{'列名':<35} {'类型':<25} {'可空':<6} {'默认值':<30}")
    lines.append("-" * 100)
    for c in cols:
        col_name, data_type, data_len, data_prec, data_scale, nullable, col_id, data_default = c
        # 构建完整类型描述
        if data_type in ('VARCHAR2', 'VARCHAR', 'CHAR', 'NVARCHAR2'):
            type_str = f"{data_type}({data_len})"
        elif data_type == 'NUMBER' and data_prec is not None:
            if data_scale and data_scale > 0:
                type_str = f"NUMBER({data_prec},{data_scale})"
            else:
                type_str = f"NUMBER({data_prec})"
        else:
            type_str = data_type or ''
        nullable_str = 'Y' if nullable == 'Y' else 'N'
        default_str = str(data_default)[:28] if data_default else ''
        lines.append(f"  {col_name:<33} {type_str:<23} {nullable_str:<4} {default_str:<30}")

    # 列注释
    cursor.execute("""
        SELECT COLUMN_NAME, COMMENTS
        FROM USER_COL_COMMENTS
        WHERE TABLE_NAME = ?
        ORDER BY COLUMN_NAME
    """, (tname,))
    comments = cursor.fetchall()
    if comments:
        lines.append(f"\n  注释:")
        for cc in comments:
            if cc[1]:
                lines.append(f"    {cc[0]:<33} — {cc[1]}")

    # 表注释
    cursor.execute("SELECT COMMENTS FROM USER_TAB_COMMENTS WHERE TABLE_NAME = ?", (tname,))
    tab_comment = cursor.fetchone()
    if tab_comment and tab_comment[0]:
        lines.append(f"\n  表注释: {tab_comment[0]}")

    # 行数统计
    try:
        cursor.execute(f"SELECT COUNT(*) FROM NARI.{tname}")
        cnt = cursor.fetchone()[0]
        lines.append(f"\n  行数: {cnt:,}")
    except Exception as e:
        lines.append(f"\n  行数: (查询失败: {e})")

# 写入文件
with open(OUTPUT, 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines))

cursor.close()
conn.close()
print(f"已导出 {len(tables)} 张表的结构到: {OUTPUT}")
