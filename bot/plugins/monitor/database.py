"""SQLite 数据库 - 建表与 CRUD 操作"""

from typing import Optional

from utils.db import get_conn


def init_db():
    """建表（幂等）"""
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS monitor_targets (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id    INTEGER NOT NULL,
            platform    TEXT NOT NULL,
            target_id   TEXT NOT NULL,
            target_name TEXT,
            enabled     INTEGER DEFAULT 1,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(group_id, platform, target_id)
        );

        CREATE TABLE IF NOT EXISTS pushed_items (
            id          TEXT PRIMARY KEY,
            platform    TEXT NOT NULL,
            target_id   TEXT NOT NULL,
            item_type   TEXT NOT NULL,
            title       TEXT,
            link        TEXT,
            pushed_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS live_status (
            room_id     TEXT NOT NULL,
            platform    TEXT NOT NULL,
            is_living   INTEGER DEFAULT 0,
            last_title  TEXT,
            offline_count INTEGER DEFAULT 0,
            checked_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (room_id, platform)
        );

        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS deepseek_balance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            currency TEXT NOT NULL,
            total_balance REAL NOT NULL,
            granted_balance REAL NOT NULL,
            topped_up_balance REAL NOT NULL,
            is_available INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_balance_ts ON deepseek_balance(created_at);
    """)
    # 迁移：旧表可能无 offline_count 列
    try:
        conn.execute("ALTER TABLE live_status ADD COLUMN offline_count INTEGER DEFAULT 0")
    except Exception:
        pass
    conn.commit()


def get_setting(key: str, default: str = "") -> str:
    """读取设置项"""
    conn = get_conn()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str):
    """写入设置项"""
    conn = get_conn()
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()


# ─── 监测目标 CRUD ──────────────────────────────────────────────


def add_target(group_id: int, platform: str, target_id: str,
               target_name: Optional[str] = None) -> int:
    """添加监测目标，返回 id"""
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO monitor_targets (group_id, platform, target_id, target_name, enabled) "
        "VALUES (?, ?, ?, ?, 1) "
        "ON CONFLICT(group_id, platform, target_id) DO UPDATE SET target_name=excluded.target_name, enabled=1",
        (group_id, platform, target_id, target_name or ""),
    )
    conn.commit()
    return cur.lastrowid


def remove_target(target_id: int) -> bool:
    """软删除（设置 enabled=0），若所有群都已移除则清空去重记录和直播状态"""
    conn = get_conn()
    # 获取要删除的目标信息
    row = conn.execute(
        "SELECT platform, target_id FROM monitor_targets WHERE id=?", (target_id,)
    ).fetchone()
    if not row:
        return False

    # 软删除
    conn.execute("UPDATE monitor_targets SET enabled=0 WHERE id=?", (target_id,))
    conn.commit()

    # 检查是否还有其他群仍在监测此目标
    remaining = conn.execute(
        "SELECT 1 FROM monitor_targets WHERE platform=? AND target_id=? AND enabled=1 LIMIT 1",
        (row["platform"], row["target_id"]),
    ).fetchone()

    if not remaining:
        # 所有群都移除了，清空去重记录和直播状态。
        # monitor_targets.platform 存的是 "{SourceBase.platform}_{source_type}"
        # （如 bilibili_live），而 pushed_items 把它拆成两列：
        # platform=bilibili、item_type=live。所以这里要拆开按两列精确匹配，
        # 否则 target_id 相同的其它平台/类型记录会被一起删掉
        # （例如 B站直播间 123 与 B站动态 UID 123）。
        base_platform, _, source_type = row["platform"].rpartition("_")
        conn.execute(
            "DELETE FROM pushed_items "
            "WHERE target_id=? AND platform=? AND item_type=?",
            (row["target_id"], base_platform, source_type),
        )
        conn.execute(
            "DELETE FROM live_status "
            "WHERE platform=? AND (room_id=? OR room_id LIKE '%:' || ?)",
            (base_platform, row["target_id"], row["target_id"]),
        )
        conn.commit()

    return True


def list_targets(group_id: Optional[int] = None) -> list[dict]:
    """列出监测目标，可选按群过滤"""
    conn = get_conn()
    if group_id:
        cur = conn.execute(
            "SELECT * FROM monitor_targets WHERE group_id=? AND enabled=1 ORDER BY id",
            (group_id,),
        )
    else:
        cur = conn.execute(
            "SELECT * FROM monitor_targets WHERE enabled=1 ORDER BY id")
    return [dict(row) for row in cur.fetchall()]


def get_target(target_id: int) -> Optional[dict]:
    """获取单个监测目标"""
    conn = get_conn()
    cur = conn.execute(
        "SELECT * FROM monitor_targets WHERE id=?", (target_id,))
    row = cur.fetchone()
    return dict(row) if row else None


def update_target_name(db_id: int, name: str):
    """更新监测目标的显示名称"""
    conn = get_conn()
    conn.execute("UPDATE monitor_targets SET target_name=? WHERE id=?", (name, db_id))
    conn.commit()


def cleanup_old_pushed(days: int = 30):
    """清理 N 天前的推送记录，防止 DB 无限膨胀"""
    conn = get_conn()
    cur = conn.execute(
        "DELETE FROM pushed_items WHERE pushed_at < datetime('now', ?)",
        (f"-{days} days",),
    )
    conn.commit()
    return cur.rowcount


# ─── 去重 ────────────────────────────────────────────────────────


def is_pushed(item_id: str) -> bool:
    """检查是否已推送"""
    conn = get_conn()
    cur = conn.execute(
        "SELECT 1 FROM pushed_items WHERE id=?", (item_id,))
    return cur.fetchone() is not None


def mark_pushed(item_id: str, platform: str, target_id: str,
                item_type: str, title: str = "", link: str = ""):
    """标记为已推送"""
    conn = get_conn()
    conn.execute(
        "INSERT OR IGNORE INTO pushed_items (id, platform, target_id, item_type, title, link) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (item_id, platform, target_id, item_type, title, link),
    )
    conn.commit()


def has_pushed_items(platform: str, target_id: str) -> bool:
    """检查该目标是否有过推送记录（用于判断是否首次抓取）"""
    conn = get_conn()
    cur = conn.execute(
        "SELECT 1 FROM pushed_items WHERE platform=? AND target_id=? LIMIT 1",
        (platform, target_id),
    )
    return cur.fetchone() is not None


# ─── 直播状态 ────────────────────────────────────────────────────


def get_live_status(room_id: str, platform: str) -> Optional[dict]:
    """获取上次记录的直播状态"""
    conn = get_conn()
    cur = conn.execute(
        "SELECT * FROM live_status WHERE room_id=? AND platform=?",
        (room_id, platform),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def set_live_status(room_id: str, platform: str, is_living: bool,
                    last_title: str = "", offline_count: int = 0):
    """更新直播状态"""
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO live_status (room_id, platform, is_living, last_title, offline_count, checked_at) "
        "VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
        (room_id, platform, 1 if is_living else 0, last_title, offline_count),
    )
    conn.commit()


# ─── DeepSeek 余额快照 ──────────────────────────────────────────
#
# 官方余额接口没有 webhook，只能轮询。每轮落一行快照，
# 用来：① 跨重启对比出「充值 / 赠送」增量；② 推算 low_armed 初值。


def save_balance_snapshot(currency: str, total: float, granted: float,
                          topped_up: float, is_available: bool) -> int:
    """插入一条余额快照，返回自增 id"""
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO deepseek_balance "
        "(currency, total_balance, granted_balance, topped_up_balance, is_available) "
        "VALUES (?, ?, ?, ?, ?)",
        (currency, float(total), float(granted), float(topped_up),
         1 if is_available else 0),
    )
    conn.commit()
    return cur.lastrowid


def get_last_balance_snapshot(currency: str = "CNY") -> Optional[dict]:
    """取该币种最近一条快照（按 id 倒序），没有则返回 None"""
    conn = get_conn()
    cur = conn.execute(
        "SELECT * FROM deepseek_balance WHERE currency=? ORDER BY id DESC LIMIT 1",
        (currency,),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def cleanup_balance_snapshots(keep_days: int = 90) -> int:
    """删除 N 天前的余额快照，返回删除行数"""
    conn = get_conn()
    cur = conn.execute(
        "DELETE FROM deepseek_balance WHERE created_at < datetime('now', ?)",
        (f"-{int(keep_days)} days",),
    )
    conn.commit()
    return cur.rowcount
