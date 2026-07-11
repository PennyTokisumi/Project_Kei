"""SQLite 数据库 - 建表与 CRUD 操作"""

import sqlite3
import threading
from typing import Optional

from config import DB_PATH

# 线程本地存储，保证每个线程有自己的连接
_local = threading.local()


def get_conn() -> sqlite3.Connection:
    """获取当前线程的数据库连接"""
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(str(DB_PATH))
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
    return _local.conn


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
            checked_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (room_id, platform)
        );

        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)
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
        # 所有群都移除了，清空去重记录和直播状态
        # 注意：pushed_items/live_status 中 platform 使用 SourceBase.platform
        # 属性值（如 "douyu"、"bilibili"），而 monitor_targets 中 platform
        # 使用 source_type 完整的 key（如 "douyu_live"）。
        # 这里用 target_id 匹配以覆盖所有可能的 platform 值。
        conn.execute(
            "DELETE FROM pushed_items WHERE target_id=?",
            (row["target_id"],),
        )
        conn.execute(
            "DELETE FROM live_status WHERE room_id=? OR room_id LIKE '%:' || ?",
            (row["target_id"], row["target_id"]),
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
                    last_title: str = ""):
    """更新直播状态"""
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO live_status (room_id, platform, is_living, last_title, checked_at) "
        "VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)",
        (room_id, platform, 1 if is_living else 0, last_title),
    )
    conn.commit()
