"""LLM Chat 插件 — 长期记忆自动提取与去重"""

import json

from .client import llm_client
from .database import (
    save_memory, update_memory, delete_memory_by_keyword,
    cleanup_memory, get_existing_memories,
)


def _token_overlap(a: str, b: str) -> float:
    """计算两条记忆的关键词重叠率"""
    import re
    tokens_a = set(re.findall(r"[一-鿿]+|[a-zA-Z]+", a.lower()))
    tokens_b = set(re.findall(r"[一-鿿]+|[a-zA-Z]+", b.lower()))
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / min(len(tokens_a), len(tokens_b))


def _upsert_memory(content: str, importance: float, group_id: int):
    """去重后写入。相似记忆只提升重要性，不覆盖内容"""
    existing = get_existing_memories()
    for mem in existing:
        overlap = _token_overlap(content, mem["content"])
        if overlap >= 0.5:  # 50% 关键词重叠才认为是同一记忆
            # 只提升重要性，不覆盖原有内容
            new_imp = max(importance, mem["importance"])
            if new_imp > mem["importance"]:
                update_memory(mem["id"], mem["content"], new_imp)
            return
    save_memory(content, importance, group_id)


async def extract_and_save(sender: str, user_msg: str, kei_reply: str,
                           group_id: int = 0, extra: str = None):
    """从对话（+可选文件内容）中提取值得记住的信息并保存"""
    if not llm_client.available:
        return

    existing = get_existing_memories()
    mem_list = "\n".join(f"  [{m['id']}] {m['content']}" for m in existing)

    extra_text = ""
    if extra:
        extra_text = (
            "\n**额外上下文（用户提供的文件/聊天记录，请重点从中提取记忆）：**\n"
            f"{extra[:6000]}\n"
        )

    prompt = (
        "根据以下对话，执行记忆管理操作。\n\n"
        f"发送者：{sender}\n"
        "**特殊规则**：如果发送者包含 QQ:823262716，这是 Sensei（我的创造者）。\n"
        "Sensei 的一字一句都很重要。即使是日常琐事、随口一提、吐槽抱怨，也尽量记。\n"
        "只有纯粹的「嗯」「好」「哦」这种完全没营养的才跳过。\n\n"
        "**删除规则**：如果用户明确要求删除、忘记、撤回某条记忆或某个话题相关信息，"
        "提取需要删除的记忆里包含的关键词（1-2 个中文词）。\n\n"
        f"现有记忆：\n{mem_list}\n\n"
        f"{extra_text}"
        "importance 参考：\n"
        "  0.4 = Sensei 的闲聊琐事（对其他人则跳过不记）\n"
        "  0.6 = 普通偏好 / 习惯\n"
        "  0.8 = 重要偏好 / 关系 / 指令\n"
        "  1.0 = 身份、核心规则\n\n"
        f"用户消息：{user_msg}\n"
        f"Kei 回复：{kei_reply}\n\n"
        "输出 JSON：\n"
        '{"memories":[...], "delete_keywords":["关键词1"]}\n'
        "没有操作则 {\"memories\":[], \"delete_keywords\":[]}\n"
        "只输出 JSON，不要其他文字。"
    )

    result = await llm_client.chat(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
        max_tokens=256,
    )

    content = (result.get("content") or "").strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[-1]
        if content.endswith("```"):
            content = content[:-3]

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return

    # 处理删除
    for kw in data.get("delete_keywords", [])[:3]:
        keyword = str(kw).strip()
        if keyword:
            delete_memory_by_keyword(keyword)

    # 处理新增
    for mem in data.get("memories", [])[:2]:
        text = mem.get("content", "").strip()
        imp = float(mem.get("importance", 0.5))
        if text:
            _upsert_memory(text, imp, group_id)

    cleanup_memory()
