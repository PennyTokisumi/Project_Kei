"""共享数据库连接管理 — 全项目统一使用此模块获取 SQLite 连接"""

import sqlite3
import threading

from config import DB_PATH

_local = threading.local()


def get_conn() -> sqlite3.Connection:
    """获取当前线程的数据库连接（线程安全）

    同一线程多次调用返回同一连接，不同线程返回不同连接。
    连接启用 WAL 模式 + 外键约束。
    """
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(str(DB_PATH))
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
    return _local.conn
