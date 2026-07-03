"""LLM Chat 插件 — 长期记忆自动提取与去重"""

import json

from config import config as _cfg
from .client import llm_client
from .database import save_memory, update_memory, cleanup_memory, get_mem_cache


async def _extract_call(prompt: str, max_tokens: int = 256) -> str:
    result = await llm_client.chat(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
        max_tokens=max_tokens,
        enable_thinking=False,
    )
    return result.get("content", "")


def _token_overlap(a: str, b: str) -> float:
    import re
    tokens_a = set(re.findall(r"[一-鿿]+|[a-zA-Z]+", a.lower()))
    tokens_b = set(re.findall(r"[一-鿿]+|[a-zA-Z]+", b.lower()))
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / min(len(tokens_a), len(tokens_b))


def _upsert_memory(content: str, importance: float):
    existing = get_mem_cache()
    for mem in existing:
        overlap = _token_overlap(content, mem["content"])
        if overlap >= 0.4:
            if mem["importance"] >= 1.0:
                return
            update_memory(mem["id"], content, importance)
            return
    save_memory(content, importance)


async def extract_and_save(sender: str, user_msg: str, kei_reply: str,
                           extra: str = None):
    """从对话中提取值得记住的信息并保存"""
    from nonebot import logger as _log

    if not _cfg.llm_api_key:
        return

    mem_cache = get_mem_cache()
    mem_list = "\n".join(f"  [{m['id']}] {m['content']}" for m in mem_cache[:5])

    extra_text = ""
    if extra:
        extra_text = f"\n用户提供了文件内容（请从中提取重要信息）:\n{extra[:4000]}\n"

    prompt = (
        f"发送者：{sender}\n"
        "- QQ823262716 = Sensei，每句话都记。称呼为 Sensei 不是用户\n"
        "- 用户说「记住/记下」→ 必须提取\n\n"
        f"现有记忆:\n{mem_list}\n\n"
        f"{extra_text}"
        f"用户消息: {user_msg}\n"
        f"Kei 回复: {kei_reply}\n\n"
        "输出 JSON（只添加记忆，不要修改删除）:\n"
        '{{"memories":[{{"content":"...","importance":0.6}}]}}\n'
        "只输出 JSON。"
    )

    raw = await _extract_call(prompt, max_tokens=256)
    content = raw.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[-1]
        if content.endswith("```"):
            content = content[:-3]

    try:
        data = json.loads(content.strip())
    except json.JSONDecodeError:
        _log.warning(f"[记忆] JSON 解析失败")
        return

    for mem in data.get("memories", [])[:2]:
        text = (mem.get("content") or mem.get("text") or "").strip()
        imp = float(mem.get("importance", 0.5))
        if text:
            _upsert_memory(text, imp)

    cleanup_memory()
