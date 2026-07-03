"""Agent 插件 — 数据库管理（定时消息持久化）"""

import sqlite3
import threading
from datetime import datetime
from typing import Optional

from config import DB_PATH

_thread_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    """获取当前线程的数据库连接"""
    if not hasattr(_thread_local, "conn") or _thread_local.conn is None:
        _thread_local.conn = sqlite3.connect(str(DB_PATH))
        _thread_local.conn.row_factory = sqlite3.Row
        _thread_local.conn.execute("PRAGMA journal_mode=WAL")
    return _thread_local.conn


def init_agent_db():
    """建表（幂等）"""
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scheduled_messages (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id    INTEGER NOT NULL,
            content     TEXT NOT NULL,
            trigger_at  TIMESTAMP NOT NULL,
            at_user     TEXT DEFAULT NULL,
            status      TEXT DEFAULT 'pending',
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_scheduled_status
        ON scheduled_messages(status, trigger_at)
    """)
    conn.commit()


# ─── CRUD ────────────────────────────────────────────────


def save_scheduled_message(
    group_id: int,
    content: str,
    trigger_at: str,
    at_user: Optional[str] = None,
) -> int:
    """保存定时消息，返回新记录的 id"""
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO scheduled_messages (group_id, content, trigger_at, at_user) "
        "VALUES (?, ?, ?, ?)",
        (group_id, content, trigger_at, at_user),
    )
    conn.commit()
    return cur.lastrowid


def get_pending_messages() -> list[dict]:
    """获取所有未发送的定时消息（含已过期和未来的）"""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM scheduled_messages WHERE status = 'pending' "
        "ORDER BY trigger_at ASC"
    ).fetchall()
    return [dict(r) for r in rows]


def mark_sent(message_id: int):
    """标记消息已发送"""
    conn = _get_conn()
    conn.execute(
        "UPDATE scheduled_messages SET status = 'sent' WHERE id = ?",
        (message_id,),
    )
    conn.commit()


def mark_cancelled(message_id: int):
    """标记消息已取消（KEI OFF 时）"""
    conn = _get_conn()
    conn.execute(
        "UPDATE scheduled_messages SET status = 'cancelled' WHERE id = ?",
        (message_id,),
    )
    conn.commit()


def delete_scheduled_message(message_id: int):
    """删除定时消息记录"""
    conn = _get_conn()
    conn.execute("DELETE FROM scheduled_messages WHERE id = ?", (message_id,))
    conn.commit()
