"""LLM Chat 插件 — 数据库管理"""

import sqlite3
import threading
import time

from config import DB_PATH

# 独立的数据库连接（线程本地）
_llm_conn = threading.local()


def _get_conn() -> sqlite3.Connection:
    if not hasattr(_llm_conn, "conn") or _llm_conn.conn is None:
        _llm_conn.conn = sqlite3.connect(str(DB_PATH))
        _llm_conn.conn.row_factory = sqlite3.Row
        _llm_conn.conn.execute("PRAGMA journal_mode=WAL")
    return _llm_conn.conn


def init_llm_db():
    """初始化 LLM 相关表"""
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS llm_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            importance REAL DEFAULT 0.5,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS llm_token_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER,
            model TEXT NOT NULL,
            prompt_tokens INTEGER DEFAULT 0,
            completion_tokens INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()


# ─── 长期记忆 ────────────────────────────────────────

def save_memory(content: str, importance: float = 0.5):
    """保存长期记忆"""
    conn = _get_conn()
    conn.execute(
        "INSERT INTO llm_memory (content, importance) VALUES (?, ?)",
        (content, importance),
    )
    conn.commit()


def search_memory(query: str, limit: int = 5) -> list[str]:
    """简单关键词搜索长期记忆"""
    conn = _get_conn()
    words = query.split()
    if not words:
        rows = conn.execute(
            "SELECT content FROM llm_memory ORDER BY importance DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    else:
        conditions = " OR ".join(["content LIKE ?" for _ in words])
        params = [f"%{w}%" for w in words]
        rows = conn.execute(
            f"SELECT content FROM llm_memory WHERE {conditions} "
            f"ORDER BY importance DESC, id DESC LIMIT ?",
            [*params, limit],
        ).fetchall()
    return [r["content"] for r in rows]


def get_all_memories(limit: int = 20) -> list[str]:
    """获取最近的重要记忆"""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT content FROM llm_memory ORDER BY importance DESC, id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [r["content"] for r in rows]


def cleanup_memory(max_count: int = 500):
    """清理过旧记忆，保留最近 max_count 条"""
    conn = _get_conn()
    count = conn.execute("SELECT COUNT(*) FROM llm_memory").fetchone()[0]
    if count > max_count:
        conn.execute(
            "DELETE FROM llm_memory WHERE id NOT IN "
            "(SELECT id FROM llm_memory ORDER BY id DESC LIMIT ?)",
            (max_count,),
        )
        conn.commit()


# ─── Token 用量 ──────────────────────────────────────

def log_token_usage(model: str, prompt_tokens: int, completion_tokens: int,
                    group_id: int = 0):
    """记录一次 API 调用的 token 用量"""
    conn = _get_conn()
    conn.execute(
        "INSERT INTO llm_token_usage (group_id, model, prompt_tokens, completion_tokens) "
        "VALUES (?, ?, ?, ?)",
        (group_id, model, prompt_tokens, completion_tokens),
    )
    conn.commit()


def get_usage_today() -> dict:
    """查询今日 token 用量汇总"""
    conn = _get_conn()
    today = time.strftime("%Y-%m-%d")
    row = conn.execute(
        "SELECT COALESCE(SUM(prompt_tokens),0) AS p, "
        "COALESCE(SUM(completion_tokens),0) AS c, "
        "COUNT(*) AS n "
        "FROM llm_token_usage WHERE date(created_at)=?",
        (today,),
    ).fetchone()
    return {"prompt": row["p"], "completion": row["c"], "calls": row["n"]}
