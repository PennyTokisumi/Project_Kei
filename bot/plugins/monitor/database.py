"""SQLite 数据库 - 建表与 CRUD 操作"""

import sqlite3
import threading
from pathlib import Path
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
    """)
    conn.commit()


# ─── 监测目标 CRUD ──────────────────────────────────────────────


def add_target(group_id: int, platform: str, target_id: str,
               target_name: Optional[str] = None) -> int:
    """添加监测目标，返回 id"""
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO monitor_targets (group_id, platform, target_id, target_name) "
            "VALUES (?, ?, ?, ?)",
            (group_id, platform, target_id, target_name or ""),
        )
        conn.commit()
        return cur.lastrowid
    except sqlite3.IntegrityError:
        # 已存在 → 启用
        cur = conn.execute(
            "UPDATE monitor_targets SET enabled=1 WHERE group_id=? AND platform=? AND target_id=?",
            (group_id, platform, target_id),
        )
        conn.commit()
        cur = conn.execute(
            "SELECT id FROM monitor_targets WHERE group_id=? AND platform=? AND target_id=?",
            (group_id, platform, target_id),
        )
        return cur.fetchone()["id"]


def remove_target(target_id: int) -> bool:
    """软删除（设置 enabled=0）"""
    conn = get_conn()
    cur = conn.execute(
        "UPDATE monitor_targets SET enabled=0 WHERE id=?", (target_id,))
    conn.commit()
    return cur.rowcount > 0


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
