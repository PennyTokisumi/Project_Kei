"""LLM Chat 插件 — 入口

功能：
- KEI ON/OFF  — 每群独立开关（仅群主/管理员，默认关闭）
- LLM          — 查询今日 token 用量（仅群主/管理员）
- @Kei 聊天    — LLM 驱动的 @Kei 自然回复
- 自由聊天监听 — 启用后，无需 @Kei，AI 主动判断是否加入聊天
"""

import re

from nonebot import get_driver, on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message
from nonebot.rule import Rule, to_me, startswith

from config import VERSION, config
from ..monitor.database import get_setting, set_setting

from .client import llm_client
from .database import get_usage_today, init_llm_db
from .decision import should_speak
from .memory import memory
from .persona import PERSONA_PROMPT
from .utils import extract_text, extract_user_name

driver = get_driver()

# ─── 命令：KEI ON/OFF ────────────────────────────────
kei_enable_cmd = on_message(rule=to_me() & startswith("KEI"), priority=5)


@kei_enable_cmd.handle()
async def handle_kei_enable(event: GroupMessageEvent):
    """开关 LLM 群聊功能（仅群主/管理员可用）"""
    if str(event.user_id) != "823262716":
        return  # 非 Sensei 静默，交给 unknown_cmd

    text = event.get_plaintext().strip()
    parts = text.split()
    if len(parts) < 2:
        await kei_enable_cmd.finish(
            Message("格式: KEI ON 或 KEI OFF"),
            at_sender=True,
        )

    arg = parts[1].upper()
    key = f"llm_enabled_{event.group_id}"

    if arg == "ON":
        if not llm_client.available:
            await kei_enable_cmd.finish(
                Message("\nLLM 服务未配置（缺少 API Key），请联系老师配置后再开启。"),
                at_sender=True,
            )

        # 连通性测试：发一条简单请求确认 API Key 有效
        await kei_enable_cmd.send(Message("正在验证 DeepSeek API 连接..."))
        test_result = await llm_client.chat(
            messages=[{"role": "user", "content": "请回复OK"}],
            temperature=0.0,
            max_tokens=128,
        )
        test_reply = test_result.get("content", "").strip()
        test_error = test_result.get("error", "")
        if not test_reply:
            err_detail = f"\n错误详情: {test_error}" if test_error else ""
            await kei_enable_cmd.finish(
                Message(f"\n❌ DeepSeek API 连接失败。{err_detail}"
                        "\n请检查 API Key 和网络。未开启 LLM 功能。"),
                at_sender=True,
            )
            return

        set_setting(key, "1")
        await kei_enable_cmd.finish(
            Message(f"\nSensei，Kei 已接入本群聊天。（API 测试回复: {test_reply}）"),
            at_sender=True,
        )
    elif arg == "OFF":
        set_setting(key, "0")
        await kei_enable_cmd.finish(
            Message("\nSensei，Kei 已退出本群聊天。"),
            at_sender=True,
        )
    else:
        await kei_enable_cmd.finish(
            Message("格式: KEI ON 或 KEI OFF"),
            at_sender=True,
        )


# ─── 命令：LLM ────────────────────────────────────────
llm_usage_cmd = on_message(rule=to_me() & startswith("LLM"), priority=5)


@llm_usage_cmd.handle()
async def handle_llm_usage(event: GroupMessageEvent):
    """查询今日 token 用量（仅群主/管理员可用）"""
    if str(event.user_id) != "823262716":
        return  # 非 Sensei 静默，交给 unknown_cmd

    usage = get_usage_today()
    # DeepSeek 定价（RMB / 1M tokens，按约 7.2 汇率）
    price_prompt = 2.02
    price_completion = 3.02
    cost = (usage["prompt"] / 1_000_000 * price_prompt +
            usage["completion"] / 1_000_000 * price_completion)

    await llm_usage_cmd.finish(
        Message(
            f"\nSensei，以下是今日 LLM Token 用量。\n"
            f"\n"
            f"  调用次数: {usage['calls']}\n"
            f"  Prompt: {usage['prompt']:,} tokens\n"
            f"  Completion: {usage['completion']:,} tokens\n"
            f"  今日合计: {usage['prompt'] + usage['completion']:,} tokens\n"
            f"  预估费用: ¥{cost:.4f}"
        ),
        at_sender=True,
    )


# ─── read 指令 ────────────────────────────────────────
read_cmd = on_message(rule=to_me() & startswith("read"), priority=5)


@read_cmd.handle()
async def handle_read(event: GroupMessageEvent):
    """@Kei read <文件名> — 读取 data/ 下的文件（仅 Sensei）"""
    if str(event.user_id) != "823262716":
        return

    from .file_reader import safe_read
    text = event.get_plaintext().strip()
    parts = text.split()
    if len(parts) < 2:
        await read_cmd.finish(Message("\n格式: read <文件名>\n示例: read test.txt"))
        return

    filename = parts[1]
    content = safe_read(filename)
    if not content:
        await read_cmd.finish(Message(f"文件 '{filename}' 不存在或无法读取。\n请确保文件在 data/ 目录下。"))
        return

    size_kb = len(content) // 1024
    await read_cmd.send(Message(f"已读取 {filename}（{size_kb}KB），正在分析..."))

    # 1. 专用提取：裸 HTTP，不开 reasoning_effort，避免 token 被推理吃光
    import httpx
    extract_prompt = (
        "从以下文件内容中提取所有值得长期记住的信息。\n"
        "每条信息一行，格式: 内容 | 重要性(0.4-1.0)\n"
        "不要遗漏任何重要信息（人物、关系、偏好、事件、规则等）。\n\n"
        f"{content[:6000]}\n\n"
        "示例输出:\n"
        "爱丽丝是Kei曾经的王女，现在是好朋友 | 0.9\n"
        "Sensei喜欢喝可乐 | 0.6\n"
        "只输出内容，不要其他文字。"
    )
    extract_text = ""
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            r = await http.post(
                "https://api.deepseek.com/chat/completions",
                headers={
                    "Authorization": f"Bearer {config.deepseek_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": config.deepseek_model,
                    "messages": [{"role": "user", "content": extract_prompt}],
                    "max_tokens": 300,
                    "temperature": 0.2,
                    "stream": False,
                },
            )
            extract_text = r.json()["choices"][0]["message"]["content"]
    except Exception:
        pass

    from .database import save_memory, cleanup_memory
    for line in extract_text.split("\n"):
        line = line.strip()
        if "|" in line:
            parts = line.rsplit("|", 1)
            if len(parts) == 2:
                mem_text = parts[0].strip()
                try:
                    imp = float(parts[1].strip())
                except ValueError:
                    imp = 0.6
                if mem_text and len(mem_text) > 2:
                    save_memory(mem_text, imp)
    cleanup_memory()

    # 2. 生成回复
    msgs = memory.build_context(event.group_id, f"读取文件 {filename}", extract_user_name(event))
    msgs.insert(1, {
        "role": "system",
        "content": (
            f"用户要求你读取了文件: {filename}\n"
            f"──── 文件开始 ────\n{content[:8000]}\n──── 文件结束 ────\n"
            f"请列出文件中的关键信息点，以 Kei 的身份回复。"
        )
    })
    result = await llm_client.chat(messages=msgs, temperature=0.5, max_tokens=512)
    reply = result.get("content", "").strip() or "……"

    await read_cmd.finish(Message(f"\n{reply}"), at_sender=True)
    memory.mark_spoke(event.group_id)


# ─── memory 指令 ──────────────────────────────────────
memory_cmd = on_message(rule=to_me() & startswith("memory"), priority=5)


@memory_cmd.handle()
async def handle_memory(event: GroupMessageEvent):
    """查看长期记忆列表（仅 Sensei）"""
    if str(event.user_id) != "823262716":
        return

    from .database import get_existing_memories
    mems = get_existing_memories()
    if not mems:
        await memory_cmd.finish(Message("\当前没有任何长期记忆。"))
        return

    lines = [f"Sensei，以下是当前长期记忆（共 {len(mems)} 条）。", ""]
    for m in mems:
        lines.append(f"  [{m['id']}] imp={m['importance']:.1f}")
        lines.append(f"      {m['content']}")
    await memory_cmd.finish(Message("\n".join(lines)))


# ─── edit 指令 ────────────────────────────────────────
edit_cmd = on_message(rule=to_me() & startswith("edit"), priority=5)


@edit_cmd.handle()
async def handle_edit(event: GroupMessageEvent):
    """edit <id> <内容> — 修改记忆内容（仅 Sensei）"""
    if str(event.user_id) != "823262716":
        return

    text = event.get_plaintext().strip()
    parts = text.split(None, 2)  # edit, id, content
    if len(parts) < 3 or not parts[1].isdigit():
        await edit_cmd.finish(Message("格式: edit <序号> <新内容>"))
        return

    mid = int(parts[1])
    new_content = parts[2].strip()
    from .database import get_existing_memories, update_memory_content
    mems = get_existing_memories()
    if not any(m["id"] == mid for m in mems):
        await edit_cmd.finish(Message(f"记忆 [{mid}] 不存在。"))
        return

    update_memory_content(mid, new_content)
    await edit_cmd.finish(Message(f"记忆 [{mid}] 已更新。"))


# ─── imp 指令 ─────────────────────────────────────────
imp_cmd = on_message(rule=to_me() & startswith("imp"), priority=5)


@imp_cmd.handle()
async def handle_imp(event: GroupMessageEvent):
    """imp <id> <数字> — 修改重要性（仅 Sensei）"""
    if str(event.user_id) != "823262716":
        return

    text = event.get_plaintext().strip()
    parts = text.split()
    if len(parts) < 3 or not parts[1].isdigit():
        await imp_cmd.finish(Message("格式: imp <序号> <数字>\n示例: imp 2 0.9"))
        return

    mid = int(parts[1])
    try:
        new_imp = float(parts[2])
    except ValueError:
        await imp_cmd.finish(Message("重要性必须是数字。"))
        return

    from .database import get_existing_memories, update_memory_imp
    mems = get_existing_memories()
    if not any(m["id"] == mid for m in mems):
        await imp_cmd.finish(Message(f"记忆 [{mid}] 不存在。"))
        return

    update_memory_imp(mid, new_imp)
    await imp_cmd.finish(Message(f"记忆 [{mid}] 重要性已更新为 {new_imp}。"))


# ─── forget 指令 ──────────────────────────────────────
forget_cmd = on_message(rule=to_me() & startswith("forget"), priority=5)


@forget_cmd.handle()
async def handle_forget(event: GroupMessageEvent):
    """forget <id> — 删除记忆（仅 Sensei）"""
    if str(event.user_id) != "823262716":
        return

    text = event.get_plaintext().strip()
    parts = text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        await forget_cmd.finish(Message("格式: forget <序号>"))
        return

    mid = int(parts[1])
    from .database import delete_memory_by_id
    delete_memory_by_id(mid)
    await forget_cmd.finish(Message(f"记忆 [{mid}] 已删除。"))


# ─── 注册 history 指令 ─────────────────────────────────
from . import history  # noqa: E402, F401


# ─── @Kei LLM 回复（p6，仅 KEI ON 时触发）──────────────
def _llm_on_rule(event: GroupMessageEvent) -> bool:
    if not event.group_id:
        return False
    return get_setting(f"llm_enabled_{event.group_id}", "0") == "1"

llm_at_handler = on_message(rule=to_me() & Rule(_llm_on_rule), priority=6, block=True)


@llm_at_handler.handle()
async def handle_llm_at(event: GroupMessageEvent):
    """@Kei 消息在 KEI ON 的群 → LLM 自然回复"""
    gid = event.group_id
    msg_text = extract_text(event)
    sender_name = extract_user_name(event)

    memory.add_message(gid, sender_name, msg_text)
    msgs = memory.build_context(gid, msg_text, sender_name)
    msgs.append({
        "role": "system",
        "content": "请以 Kei 的身份简短自然回复（1-3 句，不输出代码块或 markdown）。"
    })

    result = await llm_client.chat(messages=msgs, temperature=0.6, max_tokens=256)
    reply = result.get("content", "").strip()
    if not reply:
        reply = "……"
    memory.add_assistant_message(gid, reply)
    memory.mark_spoke(gid)
    # 提取记忆
    from .remember import extract_and_save
    try:
        await extract_and_save(sender_name, msg_text, reply)
    except Exception:
        pass
    await llm_at_handler.finish(Message(f"\n{reply}"), at_sender=True)


# ─── 自由聊天监听 ────────────────────────────────────
# 仅处理「不含 @Kei 的群消息」，由 LLM 自主决定是否插话
def _no_at_rule(event: GroupMessageEvent) -> bool:
    """消息不含 @Kei"""
    if not event.group_id:
        return False
    for seg in event.message:
        if seg.type == "at" and seg.data.get("qq") == str(event.self_id):
            return False
    return True

free_chat = on_message(rule=Rule(_no_at_rule) & Rule(_llm_on_rule), priority=10)


@free_chat.handle()
async def handle_free_chat(event: GroupMessageEvent):
    """自由聊天：无需 @Kei，AI 自主决定是否发言"""
    group_id = event.group_id
    msg_text = extract_text(event)
    if not msg_text or len(msg_text) < 2:
        return

    sender_name = extract_user_name(event)
    memory.add_message(group_id, sender_name, msg_text)

    if not memory.can_speak(group_id):
        return

    # 快速规则：Sensei 或提到 Kei → 必定回复
    is_sensei = "823262716" in sender_name
    mentions_kei = bool(re.search(r"(?i)\bkei\b|ケイ|凯伊|kei", msg_text))
    if not is_sensei and not mentions_kei:
        should = await should_speak(group_id, msg_text, sender_name)
        if not should:
            return

    msgs = memory.build_context(group_id, msg_text, sender_name)
    msgs.append({
        "role": "system",
        "content": "请以 Kei 的身份简短自然回复（1-3 句，不输出代码块或 markdown）。"
    })

    result = await llm_client.chat(messages=msgs, temperature=0.6, max_tokens=512)
    reply = result.get("content", "").strip()
    if not reply:
        return

    try:
        from nonebot import get_bot
        bot = get_bot()
        await bot.send_group_msg(group_id=group_id, message=Message(reply))
        memory.add_assistant_message(group_id, reply)
        memory.mark_spoke(group_id)
        # 异步提取长期记忆
        from .remember import extract_and_save
        await extract_and_save(sender_name, msg_text, reply)
    except Exception:
        pass


# ─── 生命周期 ────────────────────────────────────────

LLM_READY_MSG = (
    "先生、教えてください。\n老师，请告诉我。\n"
    "LLM 对话に参加する前に、まず私の世界を構築する手助けをしてください。世界観を定義してください。\n"
    "在我参与对话之前，请先帮助我构建自己的世界。请定义世界观。"
)

_greeting_sent = False


@driver.on_startup
async def _llm_startup():
    init_llm_db()
    if llm_client.available:
        from nonebot import logger as nb_logger
        nb_logger.info("LLM Chat 插件已就绪")


@driver.on_bot_connect
async def _llm_on_connect(bot: Bot):
    """如果 LLM 可用但无长期记忆，提醒老师配置世界观"""
    global _greeting_sent
    if _greeting_sent:
        return
    _greeting_sent = True

    if not llm_client.available:
        return

    from .database import get_all_memories
    if not get_all_memories(limit=1):
        # 向第一个启用的群发送提示
        from ..monitor.database import list_targets
        all_targets = list_targets()
        group_ids = {t["group_id"] for t in all_targets}
        for gid in group_ids:
            if get_setting(f"llm_enabled_{gid}", "0") == "1":
                try:
                    await bot.send_group_msg(
                        group_id=gid,
                        message=Message(LLM_READY_MSG),
                    )
                except Exception:
                    pass
                break
