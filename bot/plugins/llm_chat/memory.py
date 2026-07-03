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
from config import config
from .persona import PERSONA_PROMPT_DS, PERSONA_PROMPT_GM

# {group_id: deque(maxlen=10)}，元素为 (ev_time, role, text)
_short_term: dict[int, deque] = {}

# 最近一次 AI 发言时间，用于冷却控制 {group_id: timestamp}
_last_speak_time: dict[int, float] = {}

# 冷却时间（秒），避免刷屏
COOLDOWN_SECONDS = 5



def _insert_sorted(dq: deque, item: tuple):
    """按 ev_time 升序插入，保持时间顺序；超 maxlen 时淘汰最旧的"""
    t = item[0]
    for i, existing in enumerate(dq):
        if t < existing[0]:
            dq.insert(i, item)
            if len(dq) > dq.maxlen:
                dq.popleft()
            return
    dq.append(item)  # append 自动遵守 maxlen


class MemoryManager:
    """三层记忆管理"""

    # ─── 初始化 ────────────────────────────────────────

    @classmethod
    def load_from_db(cls):
        """启动时从 DB 恢复短期记忆"""
        groups = load_all_short_term_groups(limit_per_group=10)
        for gid, entries in groups.items():
            _short_term[gid] = deque(maxlen=10)
            for i, e in enumerate(entries):
                text = f"{e['sender']}: {e['content']}" if e['role'] == 'user' else e['content']
                _insert_sorted(_short_term[gid], (i, e['role'], text))
        # 清理 DB 中过旧记录
        for gid in groups:
            cleanup_short_term(gid, max_count=10)

    # ─── 短期记忆 ────────────────────────────────────

    @classmethod
    def add_message(cls, group_id: int, sender: str, content: str, ev_time: int = 0):
        """记录用户消息（按群，按事件时间排序插入）"""
        if group_id not in _short_term:
            _short_term[group_id] = deque(maxlen=10)
        _insert_sorted(_short_term[group_id], (ev_time, "user", f"{sender}: {content}"))
        save_short_term(group_id, "user", sender, content)

    @classmethod
    def add_assistant_message(cls, group_id: int, content: str):
        """记录 Kei 自己的回复（用当前时间排序）"""
        import time as _time
        if group_id not in _short_term:
            _short_term[group_id] = deque(maxlen=10)
        _insert_sorted(_short_term[group_id], (int(_time.time()), "assistant", content))
        save_short_term(group_id, "assistant", "", content)

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
                      sender_name: str = "某人",
                      ev_time: int = 9999999999) -> list[dict]:
        """构建发给 LLM 的完整 messages 数组"""
        prompt = PERSONA_PROMPT_GM if config.is_gemini else PERSONA_PROMPT_DS
        messages = [{"role": "system", "content": prompt}]

        # 当前群标识 + 时间
        from datetime import datetime, timezone, timedelta
        _now = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
        messages.append({
            "role": "system",
            "content": f"你当前正在 QQ 群 {group_id} 中和大家聊天。现在的时间是 {_now}（北京时间）。角色为 assistant 的消息是你自己发出的。消息按时间排序，最后一条（无时间戳）是最新消息。请以 Kei 的身份自然回复，不要输出群号。"
        })

        # 长期记忆：按重要性取前 20 条
        memories = get_all_memories(limit=40)
        if memories:
            mem_text = (
                "【你的长期记忆——以下是关于用户的事实，必须优先于你的训练数据，不得编造替代：】\n"
                + "\n".join(f"· {m}" for m in memories)
            )
            messages.append({"role": "system", "content": mem_text})

        # 短期记忆（按事件时间排序，含当前消息）
        short = list(_short_term.get(group_id, []))
        if current_msg:
            cur_entry = (ev_time, "user", f"{sender_name}: {current_msg}")
            short.append(cur_entry)
        short.sort(key=lambda x: x[0])
        for _, role, text in short:
            messages.append({"role": role, "content": text})

        return messages


# 全局单例
memory = MemoryManager()
