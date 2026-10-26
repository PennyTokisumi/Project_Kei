"""llm_token_usage 缓存命中字段测试 — 建表 / 迁移 / 记录 / 汇总

覆盖：
1. 新建库后 llm_token_usage 含 cache_hit_tokens / cache_miss_tokens
2. 旧库（只有旧列）迁移：列被补上、原有数据不丢、重复初始化幂等
3. log_token_usage 写入后，今日/累计汇总能读回正确的 cache 合计
4. usage 里缺缓存字段时按 0 记录（直接调记录函数 + 假的 API 响应）
"""

import asyncio
import sys

from utils.db import get_conn

from plugins.llm_chat import client as client_module
from plugins.llm_chat.database import (
    get_usage_today,
    get_usage_total,
    get_usage_yesterday,
    init_llm_db,
    log_token_usage,
)

CACHE_HIT_COL = "cache_hit_tokens"
CACHE_MISS_COL = "cache_miss_tokens"


def _table_columns(conn, table: str = "llm_token_usage") -> set[str]:
    """读取表的列名集合"""
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _create_old_token_usage_table(conn):
    """手工造一个旧版本（没有缓存列）的 llm_token_usage"""
    conn.execute("""
        CREATE TABLE llm_token_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER,
            model TEXT NOT NULL,
            prompt_tokens INTEGER DEFAULT 0,
            completion_tokens INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute(
        "INSERT INTO llm_token_usage "
        "(group_id, model, prompt_tokens, completion_tokens) VALUES (?, ?, ?, ?)",
        (123456, "deepseek-chat", 1000, 80),
    )
    conn.commit()


# ─── 1. 新建库建表 ─────────────────────────────────────

def test_new_db_has_cache_columns():
    """新建库后表里应包含两个缓存列"""
    init_llm_db()
    columns = _table_columns(get_conn())
    assert CACHE_HIT_COL in columns
    assert CACHE_MISS_COL in columns


# ─── 2. 旧库迁移 ───────────────────────────────────────

def test_migration_adds_columns_and_keeps_data():
    """旧库迁移：补列、保留数据、重复执行不报错"""
    conn = get_conn()
    _create_old_token_usage_table(conn)
    assert CACHE_HIT_COL not in _table_columns(conn)

    init_llm_db()

    columns = _table_columns(conn)
    assert {CACHE_HIT_COL, CACHE_MISS_COL} <= columns

    # 原有数据不丢，新列默认 0
    row = conn.execute(
        "SELECT group_id, model, prompt_tokens, completion_tokens, "
        "cache_hit_tokens, cache_miss_tokens FROM llm_token_usage"
    ).fetchone()
    assert row["group_id"] == 123456
    assert row["model"] == "deepseek-chat"
    assert row["prompt_tokens"] == 1000
    assert row["completion_tokens"] == 80
    assert row["cache_hit_tokens"] == 0
    assert row["cache_miss_tokens"] == 0

    # 幂等：再初始化两次也不应报 duplicate column，列与数据保持不变
    init_llm_db()
    init_llm_db()
    assert _table_columns(conn) == columns
    assert conn.execute("SELECT COUNT(*) FROM llm_token_usage").fetchone()[0] == 1


def test_migration_is_noop_on_new_schema():
    """新建库后重复初始化同样幂等（CREATE + 迁移都不应出错）"""
    init_llm_db()
    conn = get_conn()
    columns = _table_columns(conn)
    init_llm_db()
    assert _table_columns(conn) == columns


# ─── 3. 写入 + 汇总 ────────────────────────────────────

def test_log_usage_sums_cache_tokens():
    """写入两条后，今日/累计汇总返回 cache 合计"""
    init_llm_db()
    log_token_usage("deepseek-chat", 1000, 50,
                    cache_hit_tokens=800, cache_miss_tokens=200)
    log_token_usage("deepseek-chat", 500, 30,
                    cache_hit_tokens=300, cache_miss_tokens=200)

    # created_at 默认是 UTC，而 get_usage_today 按本地日期过滤；
    # 这里改成当前本地时间，避免测试恰好跑在跨日时段时误判
    conn = get_conn()
    conn.execute("UPDATE llm_token_usage SET created_at = datetime('now','localtime')")
    conn.commit()

    today = get_usage_today()
    assert today["prompt"] == 1500
    assert today["completion"] == 80
    assert today["cache_hit"] == 1100
    assert today["cache_miss"] == 400
    assert today["calls"] == 2

    total = get_usage_total()
    assert total["prompt"] == 1500
    assert total["completion"] == 80
    assert total["cache_hit"] == 1100
    assert total["cache_miss"] == 400
    assert total["calls"] == 2

    # 昨日查询同样返回这两个字段（今日写入不计入昨日）
    yesterday = get_usage_yesterday()
    assert yesterday["cache_hit"] == 0
    assert yesterday["cache_miss"] == 0


def test_log_usage_without_cache_defaults_to_zero():
    """旧的 3 参数调用方式仍可用，缓存列按 0 写入"""
    init_llm_db()
    log_token_usage("deepseek-chat", 120, 12)

    row = get_conn().execute(
        "SELECT cache_hit_tokens, cache_miss_tokens FROM llm_token_usage"
    ).fetchone()
    assert row["cache_hit_tokens"] == 0
    assert row["cache_miss_tokens"] == 0

    total = get_usage_total()
    assert total["cache_hit"] == 0
    assert total["cache_miss"] == 0
    assert total["prompt"] == 120
    assert total["completion"] == 12


# ─── 4. client 解析 usage（不依赖真实 API）────────────

class _FakeResponse:
    """替身 httpx.Response"""

    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeAsyncClient:
    """替身 httpx.AsyncClient，post 直接返回预设 payload"""

    response_payload: dict = {}

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, *args, **kwargs):
        return _FakeResponse(self.response_payload)


def _fake_chat(monkeypatch, usage: dict) -> dict:
    """用假响应跑一次 LLMClient.chat()"""
    _FakeAsyncClient.response_payload = {
        "choices": [{"message": {"content": "你好"}}],
        "usage": usage,
    }
    monkeypatch.setattr(client_module.httpx, "AsyncClient", _FakeAsyncClient)
    client = client_module.LLMClient()
    client._api_key = "test-key"  # 绕过 available 检查
    return asyncio.run(client.chat([{"role": "user", "content": "hi"}]))


def test_client_records_cache_usage(monkeypatch):
    """响应带缓存字段时：写库正确、日志带 cache=命中/未命中"""
    init_llm_db()
    nb_logger = sys.modules["nonebot"].logger
    nb_logger.info.reset_mock()

    result = _fake_chat(monkeypatch, {
        "prompt_tokens": 3257,
        "completion_tokens": 28,
        "prompt_cache_hit_tokens": 2950,
        "prompt_cache_miss_tokens": 307,
    })

    assert result["usage"]["prompt_cache_hit_tokens"] == 2950
    assert result["usage"]["prompt_cache_miss_tokens"] == 307

    total = get_usage_total()
    assert total["prompt"] == 3257
    assert total["completion"] == 28
    assert total["cache_hit"] == 2950
    assert total["cache_miss"] == 307

    # 日志行格式：cache=2950/307
    assert "cache=2950/307" in str(nb_logger.info.call_args)


def test_client_missing_cache_usage_defaults_to_zero(monkeypatch):
    """响应的 usage 没有缓存字段时按 0 记录"""
    init_llm_db()
    result = _fake_chat(monkeypatch, {
        "prompt_tokens": 120,
        "completion_tokens": 12,
    })

    assert result["usage"]["prompt_cache_hit_tokens"] == 0
    assert result["usage"]["prompt_cache_miss_tokens"] == 0

    total = get_usage_total()
    assert total["prompt"] == 120
    assert total["completion"] == 12
    assert total["cache_hit"] == 0
    assert total["cache_miss"] == 0


# ─── 5. LLM 指令的缓存文案 ────────────────────────────

def test_cache_rate_text_handles_zero_denominator():
    """分母为 0 时不除零，显示「无数据」；正常时显示命中率"""
    from plugins.llm_chat import _cache_rate_text

    assert _cache_rate_text({"cache_hit": 0, "cache_miss": 0}) == "缓存命中 无数据"
    assert _cache_rate_text({}) == "缓存命中 无数据"
    assert _cache_rate_text({"cache_hit": 2950, "cache_miss": 307}) == (
        "缓存命中 2950 / 3257（91%）"
    )
