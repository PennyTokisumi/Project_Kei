"""LLM Chat 插件 — 长期记忆自动提取与去重"""

import json
import httpx

from config import config as _cfg
from .database import save_memory, update_memory, cleanup_memory, get_existing_memories


async def _extract_call(prompt: str, max_tokens: int = 300) -> str:
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            r = await http.post(
                "https://api.deepseek.com/chat/completions",
                headers={
                    "Authorization": f"Bearer {_cfg.deepseek_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": _cfg.deepseek_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": max_tokens,
                    "temperature": 0.2,
                    "stream": False,
                },
            )
            return r.json()["choices"][0]["message"]["content"]
    except Exception:
        return ""


def _token_overlap(a: str, b: str) -> float:
    import re
    tokens_a = set(re.findall(r"[一-鿿]+|[a-zA-Z]+", a.lower()))
    tokens_b = set(re.findall(r"[一-鿿]+|[a-zA-Z]+", b.lower()))
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / min(len(tokens_a), len(tokens_b))


def _upsert_memory(content: str, importance: float):
    existing = get_existing_memories()
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

    if not _cfg.deepseek_api_key:
        return

    existing = get_existing_memories()
    mem_list = "\n".join(f"  [{m['id']}] {m['content']}" for m in existing[:5])

    extra_text = ""
    if extra:
        extra_text = f"\n用户提供了文件内容（请从中提取重要信息）:\n{extra[:4000]}\n"

    prompt = (
        f"发送者：{sender}\n"
        "- QQ:823262716 = Sensei，每句话都记。称呼为 Sensei 不是用户\n"
        "- 用户说「记住/记下」→ 必须提取\n\n"
        f"现有记忆:\n{mem_list}\n\n"
        f"{extra_text}"
        f"用户消息: {user_msg}\n"
        f"Kei 回复: {kei_reply}\n\n"
        "输出 JSON（只添加记忆，不要修改删除）:\n"
        '{{"memories":[{{"content":"...","importance":0.6}}]}}\n'
        "只输出 JSON。"
    )

    raw = await _extract_call(prompt, max_tokens=300)
    content = raw.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[-1]
        if content.endswith("```"):
            content = content[:-3]

    # 诊断
    from pathlib import Path
    from config import DATA_DIR
    diag = DATA_DIR / ".mem_diag.txt"
    try:
        diag.write_text(f"RAW={raw[:300]}\n", encoding="utf-8")
    except Exception:
        pass

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
