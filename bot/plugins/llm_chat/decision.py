"""LLM Chat 插件 — 发言决策"""

from .client import llm_client
from .memory import memory


async def should_speak(group_id: int, current_msg: str,
                       sender_name: str = "某人") -> bool:
    """由 LLM 决定是否应该回复（1-5 打分，≥3 则发言）"""
    msgs = memory.build_context(group_id, current_msg, sender_name)
    # 移除 Kei 人格消息 + 长期记忆（避免角色扮演）
    msgs = [m for m in msgs if m["role"] != "assistant"]
    msgs = [m for m in msgs if "长期记忆" not in m.get("content", "")]
    # 只保留群标识 + 最近对话
    if len(msgs) > 8:
        msgs = [msgs[1]] + msgs[-7:]  # 群标识 + 最近 7 条

    msgs.append({
        "role": "system",
        "content": (
            "你是分析助手，不是 Kei。给当前对话的回复必要性打分。\n"
            "基础分 2。根据以下规则加减：\n"
            "@Kei → +2 | 提到 Kei/ケイ/凯伊 → +1\n"
            "QQ823262716（朔夜時雨/Sensei）发言 → +1\n"
            "话题涉及蔚蓝档案/动画/编程/二次元/ACG/科幻/游戏/AI → +1\n"
            "话题完全和 Kei 无关或不感兴趣 → -1\n"
            "最终分 ≥3 回复。只回复最终分数。"
        )
    })

    result = await llm_client.chat(messages=msgs, temperature=0.5, max_tokens=256, enable_thinking=True, thinking_effort="high")
    content = result.get("content", "").strip()

    import re
    match = re.search(r"\d+", content)
    if match:
        score = int(match.group())
        return score >= 3
    return False
