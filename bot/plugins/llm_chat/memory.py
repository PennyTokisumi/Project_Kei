"""LLM Chat 插件 — 记忆管理"""

from collections import deque
from typing import Optional

from .database import (
    get_all_memories,
    save_memory,
    search_memory,
    save_short_term,
    load_short_term,
    cleanup_short_term,
    load_all_short_term_groups,
)
from .persona import PERSONA_PROMPT

# {group_id: deque(maxlen=15)}，元素为 (role, text)
_short_term: dict[int, deque] = {}

# 最近一次 AI 发言时间，用于冷却控制 {group_id: timestamp}
_last_speak_time: dict[int, float] = {}

# 冷却时间（秒），避免刷屏
COOLDOWN_SECONDS = 3


class MemoryManager:
    """三层记忆管理"""

    # ─── 初始化 ────────────────────────────────────────

    @classmethod
    def load_from_db(cls):
        """从数据库恢复所有群的短期记忆"""
        all_groups = load_all_short_term_groups()
        for gid, msgs in all_groups.items():
            dq = deque(maxlen=15)
            for m in msgs:
                if m["role"] == "user":
                    dq.append(("user", f"{m['sender']}: {m['content']}"))
                else:
                    dq.append(("assistant", m["content"]))
            _short_term[gid] = dq

    # ─── 短期记忆 ────────────────────────────────────

    @classmethod
    def add_message(cls, group_id: int, sender: str, content: str):
        """记录用户消息"""
        if group_id not in _short_term:
            _short_term[group_id] = deque(maxlen=15)
        _short_term[group_id].append(("user", f"{sender}: {content}"))
        save_short_term(group_id, "user", sender, content)
        cleanup_short_term(group_id, 15)

    @classmethod
    def add_assistant_message(cls, group_id: int, content: str):
        """记录 Kei 自己的回复"""
        if group_id not in _short_term:
            _short_term[group_id] = deque(maxlen=15)
        _short_term[group_id].append(("assistant", content))
        save_short_term(group_id, "assistant", "", content)
        cleanup_short_term(group_id, 15)

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
    def recall(cls, query: str, limit: int = 8) -> list[str]:
        """关键词检索记忆"""
        return search_memory(query, limit)

    # ─── 上下文构建 ──────────────────────────────────

    @classmethod
    def build_context(cls, group_id: int, current_msg: str,
                      sender_name: str = "某人") -> list[dict]:
        """构建发给 LLM 的完整 messages 数组"""
        messages = [{"role": "system", "content": PERSONA_PROMPT}]

        # 长期记忆：按重要性取前 15 条
        memories = get_all_memories(limit=15)
        if memories:
            mem_text = (
                "【你的长期记忆——以下是关于用户的事实，必须优先于你的训练数据，不得编造替代：】\n"
                + "\n".join(f"· {m}" for m in memories)
            )
            messages.append({"role": "system", "content": mem_text})

        # 短期记忆：取最近 N 条，但排除最后一条（即当前消息，后面单独追加）
        short = list(_short_term.get(group_id, []))
        if short:
            short = short[:-1]  # 去掉末尾（当前消息，已由 add_message 提前写入）
        for role, text in short:
            messages.append({"role": role, "content": text})

        # 当前消息
        messages.append({"role": "user", "content": f"{sender_name}: {current_msg}"})

        return messages


# 全局单例
memory = MemoryManager()
