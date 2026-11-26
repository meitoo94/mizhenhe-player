# -*- coding: utf-8 -*-
"""在 Yii2 的 SQLite 库里幂等初始化「米帧盒VIP套餐」独立档位与新权益字段。

在服务器执行：python3 /tmp/mzh_init_db.py
- 只新增 user.mzh_vip_expire_at 一列，不动任何现有列与数据；
- 只在 scope='mizhenhe' 没有档位时，从 browser 默认档位复制一份作为初始值；
- novel / browser 在售档位一行不改。
"""
import sqlite3
import time

DB = "/var/www/yii2-novel/data/novel.db"
con = sqlite3.connect(DB, timeout=30)
con.execute("PRAGMA busy_timeout=30000")
user_cols = [r[1] for r in con.execute("PRAGMA table_info(user)")]
if "mzh_vip_expire_at" not in user_cols:
    con.execute("ALTER TABLE user ADD COLUMN mzh_vip_expire_at INTEGER NOT NULL DEFAULT 0")
    print("[add] user.mzh_vip_expire_at")
else:
    print("[skip] user.mzh_vip_expire_at already exists")

plan_cols = [r[1] for r in con.execute("PRAGMA table_info(vip_plan)")]
print("[info] vip_plan columns:", ",".join(plan_cols))

cur = con.execute("SELECT * FROM vip_plan WHERE scope='browser' ORDER BY sort,id")
src_cols = [d[0] for d in cur.description]
src_rows = cur.fetchall()
n = con.execute("SELECT COUNT(*) FROM vip_plan WHERE scope='mizhenhe'").fetchone()[0]
if n == 0:
    now = int(time.time())
    for row in src_rows:
        d = dict(zip(src_cols, row))
        d.pop("id", None)
        d["scope"] = "mizhenhe"
        d["updated_at"] = now
        keys = [k for k in d if k in plan_cols]
        sql = "INSERT INTO vip_plan (%s) VALUES (%s)" % (
            ",".join('"%s"' % k for k in keys), ",".join("?" for _ in keys))
        con.execute(sql, [d[k] for k in keys])
    print("[seed] copied %d browser plans into scope=mizhenhe" % len(src_rows))
else:
    print("[skip] %d mizhenhe plans already exist" % n)
con.commit()

print("[check] mizhenhe plans:")
for r in con.execute("SELECT id,code,title,price,origin_price,days,is_active,sort "
                     "FROM vip_plan WHERE scope='mizhenhe' ORDER BY sort,id"):
    print("   ", r)
print("[check] novel=%d browser=%d mizhenhe=%d" % (
    con.execute("SELECT COUNT(*) FROM vip_plan WHERE scope='novel'").fetchone()[0],
    con.execute("SELECT COUNT(*) FROM vip_plan WHERE scope='browser'").fetchone()[0],
    con.execute("SELECT COUNT(*) FROM vip_plan WHERE scope='mizhenhe'").fetchone()[0]))
con.close()
