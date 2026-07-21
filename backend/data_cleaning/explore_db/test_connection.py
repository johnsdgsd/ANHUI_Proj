"""
DM 数据库连接测试脚本
用途：辅助编程时查看数据库表结构和数据
注意：此脚本仅用于辅助编程，后端项目代码中禁止直连数据库！

dmPython 使用要点：
  - 连接：dmPython.connect(user=..., password=..., server=..., port=..., autoCommit=False)
  - DM 参数占位符用 ? (不是 %s)
  - 手动提交模式：conn.commit() / conn.rollback()
  - 关闭连接：conn.close()
"""
import dmPython

conn = dmPython.connect(
    user='NARI',
    password='Root1234',
    server='localhost',
    port=5236,
    autoCommit=False  # 手动提交，安全
)

cursor = conn.cursor()

# ---- 查看当前用户 ----
cursor.execute("SELECT USER FROM DUAL")
print(f"当前用户: {cursor.fetchone()[0]}")

# ---- 查看所有表 ----
cursor.execute("SELECT TABLE_NAME FROM USER_TABLES ORDER BY TABLE_NAME")
tables = cursor.fetchall()
print(f"\n用户 NARI 下的表 ({len(tables)} 个):")
for t in tables:
    print(f"  {t[0]}")

# ---- 查看所有 Schema ----
cursor.execute("SELECT DISTINCT OWNER FROM ALL_TABLES ORDER BY OWNER")
schemas = cursor.fetchall()
print(f"\n所有 Schema ({len(schemas)} 个):")
for s in schemas:
    print(f"  {s[0]}")

cursor.close()
conn.close()
print("\n连接测试完成")
