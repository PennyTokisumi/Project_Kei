"""LLM Chat 插件 — 发言决策"""

from .client import llm_client
from .memory import memory


async def should_speak(group_id: int, current_msg: str,
                       sender_name: str = "某人") -> bool:
    """发言决策（临时：先验证回复质量，回头再打分调优）"""
    return True
