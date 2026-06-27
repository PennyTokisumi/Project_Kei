"""LLM Chat 插件 — 数据库管理"""

import sqlite3
import threading
import time

from config import DB_PATH

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
            group_id INTEGER DEFAULT 0,
            content TEXT NOT NULL,
            importance REAL DEFAULT 0.5,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # 兼容旧表
    try:
        conn.execute("ALTER TABLE llm_memory ADD COLUMN group_id INTEGER DEFAULT 0")
    except Exception:
        pass
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
    """保存长期记忆（全全局）"""
    conn = _get_conn()
    conn.execute(
        "INSERT INTO llm_memory (content, importance) VALUES (?, ?)",
        (content, importance),
    )
    conn.commit()


def search_memory(query: str, limit: int = 8) -> list[str]:
    """关键词搜索"""
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


def get_existing_memories() -> list[dict]:
    """获取所有现有记忆（供去重用）"""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, group_id, content, importance FROM llm_memory"
    ).fetchall()
    return [{"id": r["id"], "group_id": r["group_id"],
             "content": r["content"], "importance": r["importance"]} for r in rows]


def update_memory(memory_id: int, content: str, importance: float):
    """更新记忆内容和重要性"""
    conn = _get_conn()
    conn.execute(
        "UPDATE llm_memory SET content=?, importance=? WHERE id=?",
        (content, importance, memory_id),
    )
    conn.commit()


def delete_memory_by_keyword(keyword: str):
    """按关键词删除记忆（imp >= 1.0 的保护）"""
    conn = _get_conn()
    conn.execute(
        "DELETE FROM llm_memory WHERE content LIKE ? AND importance < 1.0",
        (f"%{keyword}%",),
    )
    conn.commit()
    _renumber_memories(conn)


def delete_memory_by_id(mid: int):
    """按 ID 删除记忆（无保护，仅指令使用）"""
    conn = _get_conn()
    conn.execute("DELETE FROM llm_memory WHERE id=?", (mid,))
    conn.commit()
    _renumber_memories(conn)


def update_memory_content(mid: int, content: str):
    """更新记忆内容"""
    conn = _get_conn()
    conn.execute("UPDATE llm_memory SET content=? WHERE id=?", (content, mid))
    conn.commit()


def update_memory_imp(mid: int, imp: float):
    """更新记忆重要性"""
    conn = _get_conn()
    conn.execute("UPDATE llm_memory SET importance=? WHERE id=?", (imp, mid))
    conn.commit()


def _renumber_memories(conn=None):
    """重整 ID 为连续编号"""
    if conn is None:
        conn = _get_conn()
    rows = conn.execute("SELECT id FROM llm_memory ORDER BY id").fetchall()
    for i, row in enumerate(rows, 1):
        if row["id"] != i:
            conn.execute("UPDATE llm_memory SET id=? WHERE id=?", (i, row["id"]))
    conn.commit()
    # 重置自增起点
    max_id = conn.execute("SELECT MAX(id) FROM llm_memory").fetchone()[0] or 0
    conn.execute("UPDATE sqlite_sequence SET seq=? WHERE name='llm_memory'", (max_id,))


def cleanup_memory(max_count: int = 500):
    """清理过旧记忆"""
    conn = _get_conn()
    count = conn.execute("SELECT COUNT(*) FROM llm_memory").fetchone()[0]
    if count > max_count:
        conn.execute(
            "DELETE FROM llm_memory WHERE id NOT IN "
            "(SELECT id FROM llm_memory ORDER BY id DESC LIMIT ?)",
            (max_count,),
        )
        conn.commit()
        _renumber_memories(conn)


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
