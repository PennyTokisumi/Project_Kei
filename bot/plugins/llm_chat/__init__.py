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
        provider = config.llm_provider.upper()
        await kei_enable_cmd.send(Message(f"正在验证 {provider} API 连接..."))
        test_result = await llm_client.chat(
            messages=[{"role": "user", "content": "请回复OK"}],
            temperature=0.0,
            max_tokens=8,
        )
        if not test_result.get("content", "").strip():
            err = test_result.get("error", "")
            await kei_enable_cmd.finish(
                Message(f"\n❌ {provider} API 连接失败。"
                        "\n请检查 API Key 和网络。未开启 LLM 功能。"),
                at_sender=True,
            )
            return

        set_setting(key, "1")
        await kei_enable_cmd.finish(
            Message("\nSensei，Kei 已接入本群聊天。"),
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

    from .database import get_usage_yesterday, get_usage_total
    today = get_usage_today()
    yesterday = get_usage_yesterday()
    total = get_usage_total()
    price_prompt = 2.02
    price_completion = 3.02

    def _cost(p, c):
        return p / 1_000_000 * price_prompt + c / 1_000_000 * price_completion

    await llm_usage_cmd.finish(
        Message(
            f"\nSensei，以下是 LLM Token 用量。\n"
            f"\n"
            f"  [今日] 调用 {today['calls']} 次"
            f" | {today['prompt'] + today['completion']:,} tokens"
            f" | ¥{_cost(today['prompt'], today['completion']):.2f}\n"
            f"  [昨日] 调用 {yesterday['calls']} 次"
            f" | {yesterday['prompt'] + yesterday['completion']:,} tokens"
            f" | ¥{_cost(yesterday['prompt'], yesterday['completion']):.2f}\n"
            f"  [累计] 调用 {total['calls']} 次"
            f" | {total['prompt'] + total['completion']:,} tokens"
            f" | ¥{_cost(total['prompt'], total['completion']):.2f}"
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

    # 1. 提取文件中的长期记忆
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
    result = await llm_client.chat(
        messages=[{"role": "user", "content": extract_prompt}],
        temperature=0.2,
        max_tokens=256,
        enable_thinking=False,
    )
    extract_text = result.get("content", "")

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


# ─── remember 指令 ─────────────────────────────────────
addmem_cmd = on_message(rule=to_me() & startswith("remember"), priority=5)


@addmem_cmd.handle()
async def handle_addmem(event: GroupMessageEvent):
    """remember <imp> <内容> — 直接添加记忆（仅 Sensei）"""
    if str(event.user_id) != "823262716":
        return

    text = event.get_plaintext().strip()
    parts = text.split(None, 2)
    if len(parts) < 3:
        await addmem_cmd.finish(Message("格式: remember <重要度> <内容>\n示例: remember 0.8 Sensei喜欢喝可乐"))
        return

    try:
        imp = float(parts[1])
    except ValueError:
        await addmem_cmd.finish(Message("重要度必须是数字。"))
        return

    content = parts[2].strip()
    from .database import save_memory
    save_memory(content, imp)
    await addmem_cmd.finish(Message(f"记忆已添加。imp={imp:.1f}"))


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


# ─── sensei 指令 ──────────────────────────────────────
sensei_cmd = on_message(rule=to_me() & startswith("sensei"), priority=5)


@sensei_cmd.handle()
async def handle_sensei(event: GroupMessageEvent):
    """Sensei 专用，显示全部指令（含隐藏指令）"""
    if str(event.user_id) != "823262716":
        return
    lines = [
        "\n先生の頼みなら……仕方ありませんね。\n既然是老师的请求……那就没办法了呢。",
        "",
        "help  -  显示帮助信息",
        "status  -  显示系统运行状态",
        "chat  -  和Kei聊天",
        "kawaii  -  夸一夸Kei",
        "hello ON/OFF  -  开关启动问候",
        "list  -  显示本群监测列表",
        "remove <序号>  -  移除监测目标",
        "add bilibili_live <房间号>  -  添加B站直播监测",
        "add bilibili_dynamic <UID>  -  添加B站动态监测",
        "add douyu_live <房间号>  -  添加斗鱼直播监测",
        "",
        "── 以下为 Sensei 专用隐藏指令 ──",
        "",
        "KEI ON/OFF  -  开关 LLM 群聊功能",
        "LLM  -  查询今日 LLM Token 用量",
        "read <文件名>  -  读取 data/ 下的文件",
        "history  -  拉取群聊历史记录",
        "",
        "── 记忆管理 ──",
        "memory  -  查看长期记忆列表",
        "remember <IMP> <内容>  -  添加记忆",
        "edit <序号> <内容>  -  修改记忆内容",
        "imp <序号> <数字>  -  修改记忆重要性",
        "forget <序号>  -  删除记忆",
        "",
        "私は力になれましたか？\n我能帮上忙吗？",
    ]
    await sensei_cmd.finish(
        Message("\n".join(lines)),
        at_sender=True,
    )


# ─── 注册 history 指令 ─────────────────────────────────
from . import history  # noqa: E402, F401


# ─── @Kei LLM 回复（p6，仅 KEI ON 时触发）──────────────
def _llm_on_rule(event: GroupMessageEvent) -> bool:
    if not event.group_id:
        return False
    # 忽略 bot 自己的消息，防止自循环
    if event.user_id == event.self_id:
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
    _msgs = memory.build_context(gid, msg_text, sender_name)
    _msgs.append({
        "role": "system",
        "content": "请以 Kei 的身份简短自然回复。禁止用括号描述动作或心理（如（笑）（叹气）），直接说话即可。"
    })

    result = await llm_client.chat(messages=_msgs, max_tokens=512)
    reply = result.get("content", "").strip()

    if not reply:
        reply = "……"
    memory.add_assistant_message(gid, reply)
    memory.mark_spoke(gid)

    from .remember import extract_and_save
    try:
        await extract_and_save(sender_name, msg_text, reply)
    except Exception:
        pass
    await llm_at_handler.finish(Message(f"\n{reply}"), at_sender=True)


# ─── 自由聊天监听 ────────────────────────────────────
# 仅处理「不含 @Kei 的群消息」，由 LLM 自主决定是否插话
def _no_at_rule(event: GroupMessageEvent) -> bool:
    """消息不含 @Kei，且非 bot 自身消息"""
    if not event.group_id:
        return False
    # 忽略 bot 自己的消息，防止自循环
    if event.user_id == event.self_id:
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
    sender_name = extract_user_name(event)

    memory.add_message(group_id, sender_name, msg_text)

    # 提到 Kei → 无视冷却，必定回复
    mentions_kei = bool(re.search(r"(?i)(?<![a-z])kei(?![a-z])|ケイ|凯伊", msg_text))

    if not mentions_kei and not memory.can_speak(group_id):
        return

    if not mentions_kei:
        should = await should_speak(group_id, msg_text, sender_name)
        if not should:
            return

    msgs = memory.build_context(group_id, msg_text, sender_name)
    msgs.append({
        "role": "system",
        "content": "请以 Kei 的身份简短自然回复。禁止用括号描述动作或心理（如（笑）（叹气）），直接说话即可。"
    })

    result = await llm_client.chat(
        messages=msgs, max_tokens=512,
    )
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
    memory.load_from_db()
    # 启动时关闭所有群的 LLM 开关，需手动 KEI ON
    from ..monitor.database import get_conn as _mon_conn
    _mon_conn().execute("UPDATE settings SET value='0' WHERE key LIKE 'llm_enabled_%'")
    _mon_conn().commit()
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
