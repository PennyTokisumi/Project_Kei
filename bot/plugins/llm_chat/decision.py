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
            "**特殊规则**：\n"
            "- 如果发言者是 Sensei（QQ823262716），默认 3 分起步\n"
            "- 如果消息中提到了「Kei/ケイ/凯伊/kei」（即使没有 @），至少 4 分，这是有人在叫你\n"
            "- 如果话题涉及你的兴趣偏好（见系统 prompt），至少 4 分\n"
            "5=有人明确叫你、话题与你直接相关、Sensei 在说话\n"
            "3=普通人闲聊，可以搭话\n"
            "1=完全无关、你不感兴趣\n"
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
