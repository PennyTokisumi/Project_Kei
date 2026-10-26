"""build_context 消息顺序测试 — 前缀缓存友好顺序

约定：固定内容（persona / 长期记忆）必须排在变化内容（群号 + 当前时间）之前，
否则 DeepSeek 的前缀缓存会从「群号 + 当前时间」处断开，后面的长期记忆块全部 miss。
"""

import pytest

from plugins.llm_chat.database import init_llm_db, save_memory
from plugins.llm_chat.memory import MemoryManager, _short_term
from plugins.llm_chat.persona import PERSONA_PROMPT

GROUP_ID = 123456789

# 长期记忆块的开头（与 memory.py 中的文案保持一致）
MEM_PREFIX = "【你的长期记忆"
# 环境块的特征串
ENV_MARK = "现在的时间是"


def _text(msg: dict) -> str:
    """取一条 message 的文本（多模态时 content 是数组，拼接其中的 text 片段）"""
    content = msg["content"]
    if isinstance(content, str):
        return content
    return "\n".join(part["text"] for part in content if part.get("type") == "text")


def _index_of(messages: list[dict], predicate) -> int:
    """返回第一条满足条件的消息下标，不存在则断言失败"""
    for i, msg in enumerate(messages):
        if predicate(msg):
            return i
    raise AssertionError(f"没有找到满足条件的消息，messages={messages!r}")


@pytest.fixture
def llm_db():
    """初始化 LLM 表，并插入一条锁定记忆 + 一条普通事实记忆"""
    _short_term.clear()
    init_llm_db()
    save_memory("老师的生日是 3 月 14 日", 1.0)
    save_memory("老师喜欢喝咖啡", 0.5)
    yield
    _short_term.clear()


class TestMessageOrder:
    """三个 system 块的先后顺序"""

    def test_persona_is_first(self, llm_db):
        """messages[0] 是 persona，内容与 PERSONA_PROMPT 完全一致"""
        messages = MemoryManager.build_context(GROUP_ID, "今天好热")

        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == PERSONA_PROMPT

    def test_long_term_before_env(self, llm_db):
        """长期记忆块排在环境块（群号 + 当前时间）之前 —— 缓存前缀不被切断"""
        messages = MemoryManager.build_context(GROUP_ID, "今天好热")

        mem_idx = _index_of(messages, lambda m: _text(m).startswith(MEM_PREFIX))
        env_idx = _index_of(messages, lambda m: ENV_MARK in _text(m))

        assert mem_idx < env_idx
        # 两者都紧跟在 persona 之后
        assert mem_idx == 1
        assert env_idx == 2

    def test_env_block_still_has_group_and_time(self, llm_db):
        """调序不影响环境块内容：群号和时间仍在同一条 system 消息里"""
        messages = MemoryManager.build_context(GROUP_ID, "今天好热")

        env_idx = _index_of(messages, lambda m: ENV_MARK in _text(m))
        env_text = _text(messages[env_idx])

        assert str(GROUP_ID) in env_text
        assert "现在的时间是" in env_text
        assert "北京时间" in env_text

    def test_no_memory_env_is_second(self, llm_db):
        """没有任何长期记忆时，环境块退到 [1]，persona 仍是 [0]"""
        from utils.db import get_conn
        get_conn().execute("DELETE FROM llm_memory")
        get_conn().commit()

        messages = MemoryManager.build_context(GROUP_ID, "今天好热")

        assert messages[0]["content"] == PERSONA_PROMPT
        assert ENV_MARK in _text(messages[1])


class TestShortTermTail:
    """短期记忆与当前消息仍在最后"""

    def test_short_term_and_current_message_last(self, llm_db):
        """短期记忆在环境块之后，当前消息是最后一条"""
        MemoryManager.add_message(GROUP_ID, "老师", "早上好", ev_time=1000)
        MemoryManager.add_message(GROUP_ID, "老师", "在吗", ev_time=2000)
        messages = MemoryManager.build_context(GROUP_ID, "现在这句", sender_name="老师")

        env_idx = _index_of(messages, lambda m: ENV_MARK in _text(m))
        first_short_idx = _index_of(messages, lambda m: "早上好" in _text(m))

        assert env_idx < first_short_idx
        # 当前消息是最后一条
        assert messages[-1]["role"] == "user"
        assert messages[-1]["content"] == "老师: 现在这句"
        # 时间分隔符仍在短期记忆前
        assert _text(messages[first_short_idx - 1]).startswith("── ")

    def test_current_message_is_last_with_images(self, llm_db):
        """传 images 时最后一条是 content 数组（多模态）而不是字符串"""
        images = ["https://example.com/a.jpg", "https://example.com/b.jpg"]
        messages = MemoryManager.build_context(
            GROUP_ID, "看这个图", sender_name="老师", images=images
        )

        last = messages[-1]
        assert isinstance(last["content"], list)
        assert last["content"][0] == {"type": "text", "text": "老师: 看这个图"}
        assert [p["image_url"]["url"] for p in last["content"][1:]] == images
        # 历史消息（含环境块、记忆块）仍为字符串
        assert all(isinstance(m["content"], str) for m in messages[:-1])
