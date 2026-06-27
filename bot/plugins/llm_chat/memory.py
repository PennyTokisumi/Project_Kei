"""LLM Chat 插件 — 记忆管理"""

from collections import deque
from typing import Optional

from .database import get_all_memories, save_memory, search_memory
from .persona import PERSONA_PROMPT

# {group_id: deque(maxlen=30)}
_short_term: dict[int, deque] = {}

# 最近一次 AI 发言时间，用于冷却控制 {group_id: timestamp}
_last_speak_time: dict[int, float] = {}

# 冷却时间（秒），避免刷屏
COOLDOWN_SECONDS = 3


class MemoryManager:
    """三层记忆管理"""

    # ─── 短期记忆 ────────────────────────────────────

    @classmethod
    def add_message(cls, group_id: int, sender: str, content: str):
        if group_id not in _short_term:
            _short_term[group_id] = deque(maxlen=30)
        _short_term[group_id].append(f"{sender}: {content}")

    @classmethod
    def get_short_term(cls, group_id: int) -> list[str]:
        return list(_short_term.get(group_id, []))

    @classmethod
    def can_speak(cls, group_id: int) -> bool:
        """检查冷却时间是否允许发言"""
        import time
        last = _last_speak_time.get(group_id, 0)
        return (time.time() - last) >= COOLDOWN_SECONDS

    @classmethod
    def mark_spoke(cls, group_id: int):
        import time
        _last_speak_time[group_id] = time.time()

    # ─── 长期记忆 ────────────────────────────────────

    @classmethod
    def remember(cls, content: str, importance: float = 0.5):
        """AI 主动记忆"""
        save_memory(content, importance)

    @classmethod
    def recall(cls, query: str, limit: int = 5) -> list[str]:
        """关键词检索记忆"""
        return search_memory(query, limit)

    @classmethod
    def get_recent(cls, limit: int = 10) -> list[str]:
        """获取最近的重要记忆"""
        return get_all_memories(limit)

    # ─── 上下文构建 ──────────────────────────────────

    @classmethod
    def build_context(cls, group_id: int, current_msg: str,
                      sender_name: str = "某人") -> list[dict]:
        """构建发给 LLM 的完整 messages 数组"""
        messages = [{"role": "system", "content": PERSONA_PROMPT}]

        # 长期记忆
        memories = cls.get_recent(10)
        if memories:
            mem_text = "【重要记忆】\n" + "\n".join(f"· {m}" for m in memories)
            messages.append({"role": "system", "content": mem_text})

        # 短期记忆
        recent = cls.get_short_term(group_id)
        for msg in recent[-15:]:  # 最近 15 条
            messages.append({"role": "user", "content": msg})

        # 当前消息
        messages.append({"role": "user", "content": f"{sender_name}: {current_msg}"})

        return messages


# 全局单例
memory = MemoryManager()
