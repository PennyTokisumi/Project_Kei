"""共享数据库连接管理 — 全项目统一使用此模块获取 SQLite 连接"""

import sqlite3
import threading

from config import DATA_DIR, config

_local = threading.local()


def db_file() -> str:
    """当前数据库文件的绝对路径

    每次调用都从 config 现读，而不是在导入时固化成常量 ——
    否则改写 ``config.db_path``（测试切临时库、运行时改配置）不会生效。
    """
    return str(DATA_DIR / config.db_path)


def get_conn() -> sqlite3.Connection:
    """获取当前线程的数据库连接（线程安全）

    同一线程、同一数据库路径多次调用返回同一连接，不同线程返回不同连接。
    数据库路径变化时会关闭旧连接并重新打开。

    连接启用 WAL 模式 + 外键约束。
    """
    path = db_file()
    conn = getattr(_local, "conn", None)

    if conn is not None and getattr(_local, "path", None) == path:
        return conn

    # 路径变了：先释放旧连接，避免句柄泄漏
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
        _local.conn = None

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    _local.conn = conn
    _local.path = path
    return conn


def close_conn() -> None:
    """关闭当前线程的连接（测试清理、或需要释放数据库文件时调用）"""
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
    _local.conn = None
    _local.path = None
