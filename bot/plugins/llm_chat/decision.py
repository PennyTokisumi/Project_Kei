"""LLM Chat 插件 — 发言决策"""

from .client import llm_client
from .memory import memory


async def should_speak(group_id: int, current_msg: str,
                       sender_name: str = "某人") -> bool:
    """由 LLM 决定是否应该回复当前消息（YES/NO 模式，不依赖 function calling）

    Returns:
        True: 应该发言
        False: 不发言
    """
    # 构建上下文（截取少量消息用于决策，节省 token）
    msgs = memory.build_context(group_id, current_msg, sender_name)
    if len(msgs) > 6:
        msgs = msgs[:1] + msgs[-5:]

    msgs.append({
        "role": "system",
        "content": (
            "根据以上对话，判断你是否应该插话回复。\n"
            "如果你觉得话题有趣、与你相关、或者你想表达看法，就回复 YES。\n"
            "如果完全不想参与，才回复 NO。\n"
            "只回复一个词: YES 或 NO。"
        )
    })

    result = await llm_client.chat(
        messages=msgs,
        temperature=0.5,
        max_tokens=8,
    )

    content = result.get("content", "").strip().upper()
    return "YES" in content[:20]
