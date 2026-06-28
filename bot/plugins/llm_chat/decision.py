"""LLM Chat 插件 — 发言决策"""

from .client import llm_client
from .memory import memory


async def should_speak(group_id: int, current_msg: str,
                       sender_name: str = "某人") -> bool:
    """由 LLM 决定是否应该回复（1-5 打分，≥3 则发言）"""
    msgs = memory.build_context(group_id, current_msg, sender_name)
    if len(msgs) > 8:
        msgs = msgs[:1] + msgs[-6:]

    msgs.append({
        "role": "system",
        "content": (
            "给「你应该加入这个话题回复」打分（1-5）。\n"
            "- Sensei（QQ823262716）发言 → 保底 2 分\n"
            "- 话题你感兴趣 → 4~5\n"
            "- 普通闲聊 → 2~3\n"
            "- 完全无关 → 1\n"
            "只回复一个数字。"
        )
    })

    result = await llm_client.chat(messages=msgs, temperature=0.5, max_tokens=4)
    content = result.get("content", "").strip()

    import re
    match = re.search(r"\d+", content)
    if match:
        score = int(match.group())
        return score >= 3
    return False
