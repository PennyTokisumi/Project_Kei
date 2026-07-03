"""LLM Chat 插件 — 入口

功能：
- KEI ON/OFF  — 每群独立开关（仅群主/管理员，默认关闭）
- LLM          — 查询今日 token 用量（仅群主/管理员）
- @Kei 聊天    — LLM 驱动的 @Kei 自然回复
- 自由聊天监听 — 启用后，无需 @Kei，AI 主动判断是否加入聊天
"""

import asyncio
import time as _time

from nonebot import get_driver, on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message
from nonebot.rule import Rule, to_me, startswith

from config import config
from ..monitor.database import get_setting, set_setting

from .client import llm_client
from .database import (
    get_memory_count, get_tidyable_memories, get_usage_today,
    init_llm_db, replace_memories,
)

from .memory import memory
from .persona import PERSONA_PROMPT
from .utils import extract_text, extract_user_name, get_reply_text, has_image

driver = get_driver()

# ─── 消息去重（防止重复推送）──────────────────────────
_SEEN_MSG_IDS: set[int] = set()

def _is_dup(event: GroupMessageEvent) -> bool:
    msg_id = event.message_id
    if msg_id in _SEEN_MSG_IDS:
        return True
    _SEEN_MSG_IDS.add(msg_id)
    if len(_SEEN_MSG_IDS) > 200:
        _SEEN_MSG_IDS.clear()
    return False


# ─── 消息缓冲区（P10 自由聊天批量处理）─────────────────

_buffer: dict[int, list[dict]] = {}          # group_id → [{sender_name, msg_text, ev_time}]
_buffer_first_ts: dict[int, float] = {}       # 首条消息进入时间
_buffer_tasks: dict[int, asyncio.Task] = {}
_buffer_version: dict[int, int] = {}           # 版本号，防竞态
IDLE_TIMEOUT = 5.0   # 空闲超时：最后一条消息后 5s 无新消息 → 刷新
MAX_WAIT = 15.0      # 硬上限：第一条消息进入后 15s → 强制刷新
MAX_BATCH = 10       # 数量上限：堆积 ≥ 10 条 → 立即刷新


def _should_flush(gid: int) -> bool:
    """检查是否满足立即刷新条件（数量上限 / 硬上限）"""
    if gid not in _buffer or not _buffer[gid]:
        return False
    if len(_buffer[gid]) >= MAX_BATCH:
        return True
    elapsed = _time.time() - _buffer_first_ts.get(gid, _time.time())
    return elapsed >= MAX_WAIT


async def _flush_buffer(gid: int, bot: Bot, version: int):
    """批量刷新缓冲区，发给 Kei 自行判断"""
    last_should_false = _time.time()

    while True:
        await asyncio.sleep(0.5)

        # 版本过期 → 退出（新版 task 已接管）
        if _buffer_version.get(gid, 0) != version:
            return

        if _should_flush(gid):
            break
        if _buffer_first_ts.get(gid) is None:
            return
        now = _time.time()
        if not _should_flush(gid):
            if now - last_should_false >= IDLE_TIMEOUT:
                break
        else:
            last_should_false = now

    # 再次检查版本（防窄窗口竞态：version check → pop 之间新版 task 被创建）
    if _buffer_version.get(gid, 0) != version:
        return

    # 取出缓冲消息
    msgs = _buffer.pop(gid, [])
    _buffer_first_ts.pop(gid, None)
    _buffer_tasks.pop(gid, None)

    if not msgs:
        return

    # 冷却检查
    if not memory.can_speak(gid):
        # 仍写入短期记忆（消息确实发生了）
        for m in msgs:
            memory.add_message(gid, m["sender_name"], m["msg_text"], m["ev_time"])
        return

    # 批量写入短期记忆
    for m in msgs:
        memory.add_message(gid, m["sender_name"], m["msg_text"], m["ev_time"])

    # 构建上下文（current_msg 为空串，build_context 不会添加占位条目）
    msgs_for_llm = memory.build_context(gid, "", "群聊", msgs[-1]["ev_time"])
    msgs_for_llm.append({
        "role": "system",
        "content": (
            "【指令】以上是最近堆积的群聊消息。请以 Kei 的身份自行判断是否回复。\n"
            "大多数群聊消息与你无关，不要插话。只有遇到以下情况才回复：\n"
            "1. 有人明确提到 Kei/ケイ/凯伊，或被点名\n"
            "2. 话题涉及你感兴趣的领域（蔚蓝档案、ACG、编程、AI）且你有话想说\n"
            "3. Sensei 在说话且你想回应\n"
            "其他情况保持安静就好。如果决定不回复，回复 [PASS]。\n"
            "如果回复涉及多个不同话题/对象，合在一条不自然时，"
            "可用 [SEP] 分隔多条回复。能自然合为一条就合一条。"
        )
    })

    # LLM 调用
    result = await llm_client.chat(messages=msgs_for_llm, max_tokens=512)
    reply = result.get("content", "").strip()

    if not reply:
        return

    # 解析多段回复
    segments = [s.strip() for s in reply.split("[SEP]")]
    segments = [s for s in segments if s and s != "[PASS]"]

    if not segments:
        return

    from .remember import extract_and_save
    for i, seg in enumerate(segments):
        try:
            await bot.send_group_msg(group_id=gid, message=Message(seg))
            memory.add_assistant_message(gid, seg)
        except Exception:
            pass
        if i < len(segments) - 1:
            await asyncio.sleep(0.3)

    memory.mark_spoke(gid)

    # 提取记忆（用最后一段作为代表性回复）
    try:
        await extract_and_save(msgs[-1]["sender_name"], "批量消息", segments[-1])
    except Exception:
        pass

    await _maybe_trigger_tidy(bot)


async def _schedule_flush(gid: int, bot: Bot):
    """调度缓冲区刷新：取消旧的定时器，启动新的"""
    if gid not in _buffer_first_ts:
        _buffer_first_ts[gid] = _time.time()

    # 递增版本号，旧 task 检测到版本不匹配会自动退出
    _buffer_version[gid] = _buffer_version.get(gid, 0) + 1
    version = _buffer_version[gid]

    # 取消旧 task（加速清理）
    old = _buffer_tasks.get(gid)
    if old and not old.done():
        old.cancel()
    _buffer_tasks[gid] = asyncio.create_task(_flush_buffer(gid, bot, version))


# ─── 自动记忆整理 ─────────────────────────────────────

_last_tidy_time: float = 0
_tidy_lock = asyncio.Lock()
TIDY_COOLDOWN = 3600   # 两次整理至少间隔 1 小时
TIDY_THRESHOLD = 50     # 记忆数超过此值触发整理

SENSEI_ONLY_MSG = "\nもう！这个是只有时老师才可以用的！"

TIDY_DONE_MSG = (
    "休息が大事、という言葉が少し分かってきたかも……\n"
    "休息很重要，这话我好像有点明白了……"
)


async def _auto_tidy_memories(bot: Bot):
    """自动整理长期记忆：LLM 去重/合并/调权重，保护 imp=1.0"""
    async with _tidy_lock:
        global _last_tidy_time
        if _time.time() - _last_tidy_time < TIDY_COOLDOWN:
            return
        if get_memory_count() <= TIDY_THRESHOLD:
            return
        _last_tidy_time = _time.time()

    mems = get_tidyable_memories()
    if not mems:
        return

    mem_text = "\n".join(
        f"· {m['content']} （重要性: {m['importance']:.1f}）" for m in mems
    )
    prompt = (
        "你是 Kei，正在整理自己的长期记忆。以下是当前所有可调整的记忆（重要性 < 1.0）：\n\n"
        f"{mem_text}\n\n"
        "请整理：\n"
        "- 合并高度重复的内容为一条\n"
        "- 删除过时、矛盾、无意义的条目\n"
        "- 调整重要性（范围 0.4-0.9），越重要越高\n"
        "- 重要性 = 1.0 的条目已锁定保留，不需要你处理\n"
        "- 尽量保持原有信息不丢失\n\n"
        "输出格式（每行一条，不要编号）：\n"
        "内容 | 重要性\n\n"
        "示例：\n"
        "Sensei喜欢喝冰可乐 | 0.6\n"
        "爱丽丝是Kei的好朋友 | 0.9\n\n"
        "只输出整理后的内容，不要其他说明。"
    )

    result = await llm_client.chat(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3, max_tokens=1024, enable_thinking=True,
    )
    reply = result.get("content", "").strip()
    if not reply:
        return

    new_mems: list[tuple[str, float]] = []
    for line in reply.split("\n"):
        line = line.strip()
        if not line or "|" not in line:
            continue
        parts = line.rsplit("|", 1)
        if len(parts) != 2:
            continue
        content = parts[0].strip()
        try:
            imp = float(parts[1].strip())
        except ValueError:
            continue
        imp = max(0.4, min(0.9, imp))
        if content and len(content) > 2:
            new_mems.append((content, imp))

    if not new_mems:
        return

    replace_memories(new_mems)

    # 通知所有启用 LLM 的群
    from ..monitor.database import list_targets
    targets = list_targets()
    notified: set[int] = set()
    for t in targets:
        gid = t["group_id"]
        if gid in notified:
            continue
        if get_setting(f"llm_enabled_{gid}", "0") == "1":
            try:
                await bot.send_group_msg(group_id=gid, message=Message(TIDY_DONE_MSG))
                notified.add(gid)
            except Exception:
                pass


async def _maybe_trigger_tidy(bot: Bot):
    """检查是否需要触发记忆整理（非阻塞）"""
    if _time.time() - _last_tidy_time < TIDY_COOLDOWN:
        return
    if get_memory_count() <= TIDY_THRESHOLD:
        return
    asyncio.create_task(_auto_tidy_memories(bot))


# ══════════════════════════════════════════════════════
#  命令规则（按优先级排列）
# ══════════════════════════════════════════════════════

# ─── P4：Sensei 专用指令 ─────────────────────────────

kei_enable_cmd  = on_message(rule=to_me() & startswith("KEI"),      priority=4)
llm_usage_cmd   = on_message(rule=to_me() & startswith("LLM"),      priority=4)
read_cmd        = on_message(rule=to_me() & startswith("read"),     priority=4)
memory_cmd      = on_message(rule=to_me() & startswith("memory"),   priority=4)
addmem_cmd      = on_message(rule=to_me() & startswith("remember"), priority=4)
edit_cmd        = on_message(rule=to_me() & startswith("edit"),     priority=4)
imp_cmd         = on_message(rule=to_me() & startswith("imp"),      priority=4)
forget_cmd      = on_message(rule=to_me() & startswith("forget"),   priority=4)
sensei_cmd      = on_message(rule=to_me() & startswith("sensei"),   priority=4)
history_cmd     = on_message(rule=to_me() & startswith("history"),  priority=4)

# ─── P6：@Kei LLM 自然回复 ───────────────────────────

def _llm_on_rule(event: GroupMessageEvent) -> bool:
    if not event.group_id:
        return False
    if event.user_id == event.self_id:
        return False
    return get_setting(f"llm_enabled_{event.group_id}", "0") == "1"

llm_at_handler = on_message(rule=to_me() & Rule(_llm_on_rule), priority=6, block=True)

# ─── P10：自由聊天监听 ───────────────────────────────

def _no_at_rule(event: GroupMessageEvent) -> bool:
    if not event.group_id:
        return False
    if event.user_id == event.self_id:
        return False
    for seg in event.message:
        if seg.type == "at" and seg.data.get("qq") == str(event.self_id):
            return False
    return True

free_chat = on_message(rule=Rule(_no_at_rule) & Rule(_llm_on_rule), priority=10)


# ══════════════════════════════════════════════════════
#  KEI ON/OFF
# ══════════════════════════════════════════════════════

@kei_enable_cmd.handle()
async def handle_kei_enable(event: GroupMessageEvent):
    """开关 LLM 群聊功能（仅 Sensei）"""
    if str(event.user_id) != "823262716":
        await kei_enable_cmd.finish(Message(SENSEI_ONLY_MSG))

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

        provider = config.llm_provider.upper()
        await kei_enable_cmd.send(Message(f"正在验证 {provider} API 连接..."))
        test_result = await llm_client.chat(
            messages=[{"role": "user", "content": "请回复OK"}],
            temperature=0.0,
            max_tokens=8,
        )
        if not test_result.get("content", "").strip():
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


# ══════════════════════════════════════════════════════
#  LLM — 查询 Token 用量
# ══════════════════════════════════════════════════════

@llm_usage_cmd.handle()
async def handle_llm_usage(event: GroupMessageEvent):
    """查询今日 token 用量（仅 Sensei）"""
    if str(event.user_id) != "823262716":
        await llm_usage_cmd.finish(Message(SENSEI_ONLY_MSG))

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


# ══════════════════════════════════════════════════════
#  read — 读取文件
# ══════════════════════════════════════════════════════

@read_cmd.handle()
async def handle_read(event: GroupMessageEvent, bot: Bot):
    """@Kei read <文件名> — 读取 data/ 下的文件（仅 Sensei）"""
    if str(event.user_id) != "823262716":
        await read_cmd.finish(Message(SENSEI_ONLY_MSG))

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
    await _maybe_trigger_tidy(bot)

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


# ══════════════════════════════════════════════════════
#  memory / remember / edit / imp / forget — 记忆管理
# ══════════════════════════════════════════════════════

@memory_cmd.handle()
async def handle_memory(event: GroupMessageEvent):
    """查看长期记忆列表（仅 Sensei）"""
    if str(event.user_id) != "823262716":
        await memory_cmd.finish(Message(SENSEI_ONLY_MSG))

    from .database import get_existing_memories
    mems = get_existing_memories()
    if not mems:
        await memory_cmd.finish(Message("\n当前没有任何长期记忆。"))
        return

    lines = [f"Sensei，以下是当前长期记忆（共 {len(mems)} 条）。", ""]
    for m in mems:
        lines.append(f"  [{m['id']}] imp={m['importance']:.1f}")
        lines.append(f"      {m['content']}")
    await memory_cmd.finish(Message("\n".join(lines)))


@addmem_cmd.handle()
async def handle_addmem(event: GroupMessageEvent, bot: Bot):
    """remember <imp> <内容> — 直接添加记忆（仅 Sensei）"""
    if str(event.user_id) != "823262716":
        await addmem_cmd.finish(Message(SENSEI_ONLY_MSG))

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
    await _maybe_trigger_tidy(bot)
    await addmem_cmd.finish(Message(f"记忆已添加。imp={imp:.1f}"))


@edit_cmd.handle()
async def handle_edit(event: GroupMessageEvent):
    """edit <id> <内容> — 修改记忆内容（仅 Sensei）"""
    if str(event.user_id) != "823262716":
        await edit_cmd.finish(Message(SENSEI_ONLY_MSG))

    text = event.get_plaintext().strip()
    parts = text.split(None, 2)
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


@imp_cmd.handle()
async def handle_imp(event: GroupMessageEvent):
    """imp <id> <数字> — 修改重要性（仅 Sensei）"""
    if str(event.user_id) != "823262716":
        await imp_cmd.finish(Message(SENSEI_ONLY_MSG))

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


@forget_cmd.handle()
async def handle_forget(event: GroupMessageEvent):
    """forget <id> — 删除记忆（仅 Sensei）"""
    if str(event.user_id) != "823262716":
        await forget_cmd.finish(Message(SENSEI_ONLY_MSG))

    text = event.get_plaintext().strip()
    parts = text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        await forget_cmd.finish(Message("格式: forget <序号>"))
        return

    mid = int(parts[1])
    from .database import delete_memory_by_id
    delete_memory_by_id(mid)
    await forget_cmd.finish(Message(f"记忆 [{mid}] 已删除。"))


# ══════════════════════════════════════════════════════
#  sensei — 显示全部指令
# ══════════════════════════════════════════════════════

@sensei_cmd.handle()
async def handle_sensei(event: GroupMessageEvent):
    """Sensei 专用，显示全部指令（含隐藏指令）"""
    if str(event.user_id) != "823262716":
        await sensei_cmd.finish(Message(SENSEI_ONLY_MSG))
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
        "Claude <任务>  -  请 Claude 帮忙处理复杂任务",
        "KEI ON/OFF  -  开关 LLM 群聊功能",
        "LLM  -  查询 LLM Token 用量",
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


# ══════════════════════════════════════════════════════
#  history — 拉取群聊历史
# ══════════════════════════════════════════════════════

from pathlib import Path as _Path
from datetime import datetime as _datetime
from config import DATA_DIR as _DATA_DIR

_CHATLOG_DIR = _DATA_DIR / "chatlogs"


async def _fetch_and_save(bot: Bot, group_id: int, count: int = 100,
                           message_seq: int = 0) -> tuple[int, str]:
    """拉取历史消息并保存为 txt"""
    _CHATLOG_DIR.mkdir(parents=True, exist_ok=True)

    result = await bot.call_api(
        "get_group_msg_history",
        group_id=group_id,
        count=count,
        message_seq=message_seq,
    )

    messages = result.get("messages", [])
    if not messages:
        return 0, ""

    timestamp = _datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"chatlog_{group_id}_{timestamp}.txt"
    filepath = _CHATLOG_DIR / filename

    lines = []
    for msg in messages:
        t = _datetime.fromtimestamp(msg.get("time", 0)).strftime("%m-%d %H:%M")
        sender = msg.get("sender", {})
        nickname = sender.get("nickname", "") or sender.get("card", "") or str(sender.get("user_id", "?"))
        user_id = sender.get("user_id", "")
        message_list = msg.get("message", [])
        text_parts = []
        for seg in message_list:
            if seg.get("type") == "text":
                text_parts.append(seg.get("data", {}).get("text", ""))
            elif seg.get("type") == "image":
                text_parts.append("[图片]")
        content = "".join(text_parts)
        if content.strip():
            lines.append(f"[{t}] {nickname}({user_id}): {content}")

    filepath.write_text("\n".join(lines), encoding="utf-8")
    return len(lines), str(filepath)


@history_cmd.handle()
async def handle_history(event: GroupMessageEvent, bot: Bot):
    """@Kei history — 拉取历史消息并保存"""
    if str(event.user_id) != "823262716":
        await history_cmd.finish(Message(SENSEI_ONLY_MSG))

    text = event.get_plaintext().strip()
    parts = text.split()
    msg_count = 100
    if len(parts) >= 2 and parts[1].isdigit():
        msg_count = min(int(parts[1]), 500)

    await history_cmd.send(Message(f"正在拉取 {msg_count} 条历史消息..."))
    count = 0
    path = ""
    error_msg = None
    try:
        count, path = await _fetch_and_save(bot, event.group_id, count=msg_count)
    except Exception as e:
        error_msg = str(e)

    if error_msg:
        await history_cmd.finish(Message(f"\n拉取失败: {error_msg}"))
    elif count == 0:
        await history_cmd.finish(Message("\n未拉取到历史消息。"))
    else:
        await history_cmd.finish(Message(f"已保存 {count} 条消息到:\n{path}"))


# ══════════════════════════════════════════════════════
#  @Kei LLM 回复
# ══════════════════════════════════════════════════════

@llm_at_handler.handle()
async def handle_llm_at(event: GroupMessageEvent, bot: Bot):
    """@Kei 消息在 KEI ON 的群 → LLM 自然回复"""
    if _is_dup(event):
        return
    if has_image(event):
        return
    gid = event.group_id
    msg_text = extract_text(event)
    sender_name = extract_user_name(event)

    # 被回复消息的内容注入（SnowLuma event.reply）
    reply_text = await get_reply_text(event, bot)
    if reply_text:
        msg_text = f"[回应:\"{reply_text}\"] {msg_text}"

    msgs = memory.build_context(gid, msg_text, sender_name, event.time)
    memory.add_message(gid, sender_name, msg_text, event.time)

    msgs.append({
        "role": "system",
        "content": (
            "请以 Kei 的身份回复。上下文中 assistant 角色是你的历史发言，避免重复。"
            "如果积压了多条用户消息，综合回复即可。"
        )
    })

    result = await llm_client.chat(messages=msgs, max_tokens=512)
    reply = (result.get("content") or "").strip()

    if not reply:
        reply = "……"
    memory.add_assistant_message(gid, reply)
    memory.mark_spoke(gid)

    from .remember import extract_and_save
    try:
        await extract_and_save(sender_name, msg_text, reply)
    except Exception:
        pass
    await _maybe_trigger_tidy(bot)
    await llm_at_handler.finish(Message(f"\n{reply}"), at_sender=True)


# ══════════════════════════════════════════════════════
#  自由聊天监听
# ══════════════════════════════════════════════════════

@free_chat.handle()
async def handle_free_chat(event: GroupMessageEvent, bot: Bot):
    """自由聊天：消息进入缓冲区，批量后 Kei 自主判断是否发言"""
    if _is_dup(event):
        return
    if has_image(event):
        return
    gid = event.group_id
    msg_text = extract_text(event)
    sender_name = extract_user_name(event)

    # 被回复消息的内容注入
    reply_text = await get_reply_text(event, bot)
    if reply_text:
        msg_text = f"[回应:\"{reply_text}\"] {msg_text}"

    # 放入缓冲区
    if gid not in _buffer:
        _buffer[gid] = []
    _buffer[gid].append({
        "sender_name": sender_name,
        "msg_text": msg_text,
        "ev_time": event.time,
    })

    # 调度刷新
    await _schedule_flush(gid, bot)


# ══════════════════════════════════════════════════════
#  生命周期
# ══════════════════════════════════════════════════════

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
    from ..monitor.database import get_conn as _mon_conn
    _mon_conn().execute("UPDATE settings SET value='0' WHERE key LIKE 'llm_enabled_%'")
    _mon_conn().commit()
    if llm_client.available:
        from nonebot import logger as nb_logger
        nb_logger.info("LLM Chat 插件已就绪")


# ─── Kei 语音风格自学习 ──────────────────────────────

# 所有预设台词汇总（STARTUP + CHAT + STATUS + UNKNOWN）
_PRESET_MSGS: list[str] = [
    # STARTUP_MSGS
    "これから、先生のことを見守らせていただきますね。\n今后就让我来守护老师吧。",
    "こんにちは、先生。今日のやることをまとめました。\n你好，老师。我已经将今天要做的事项整理好了。",
    "最初の目標に向けて、まず一歩、ですね。\n向着最初的目标，先迈出一步吧。",
    "私は、どんな手を使ってでも生き残ってやるつもりですから。\n不管用什么手段，我都会活下去。",
    # CHAT_MSGS
    "抵抗するためには前に進まないと。今まではそれも、一人でやらなければと思っていたのですが……結局、人は一人では生きていけないのだと理解しました。\n为了抵抗，必须继续前行。此前我总觉得这些事只能独自承担……但终究还是明白了，人是无法独自活下去的。",
    "な、何ですか？特に言うことはありませんが……？っ……分かりました……。せ、先生のこと、嫌いではありません……もう、いいですか！？\n怎，怎么了？有没有什么想说的话……？呃……行吧……我、我其实并不讨厌老师……这下可以了吧！？",
    "こういう言葉は、滅多に言いませんから、ちゃんと聞いてくださいね！？……あまり、危険なことはしないでください。先生が居なくなるのは……私も嫌ですから。\n听好了，我很少说这种话的！？……请你尽量不要做危险的事。因为老师要是不在了……我也会很难过的。",
    "遠き地の星明かり……という意味です。それだけ昔のことを言っているのだと思います。座標によると、過去と未来は同時に存在するとも言いますから。\n意思是……远方的星光。感觉像是在说久远的往事一样。毕竟根据坐标，过去与未来是同时存在的。",
    "でもまあ……世の理を知ってしまえば。怒らないでいるのは難しい、と思っているのですが。\n不过嘛……一旦知晓了世间真理。我觉得，想保持不生气实在很难呢。",
    "食事はちゃんと取っていますか？適度な運動も必要です。早寝早起きが良いのは、大人にも当てはまることなんですよ。\n你有好好吃饭吗？适当的运动也是必要的。早睡早起的好处，对大人也同样适用哦。",
    "仕事はほどほどに。とはいえ怠けるのもほどほどに。……本当にもう、手が焼けるんですから。\n……真是的，你实在太让人操心啦。工作要适度，不过偷懒也得适可而止。",
    "……何をニヤニヤしてるんですか！\n……你在那儿偷偷笑什么呢！",
    "先生がこの世界を見捨てないというのなら。私だって最後まで、絶対に諦めたりしません。\n如果老师不抛弃这个世界的话。那我到最后也不会放弃的。",
    "私は――先生のこと、嫌いじゃありませんから。\n我——并不讨厌老师呢。",
    "絶対、大丈夫です。私はずっとここにいます。\n绝对，没问题的。我会一直在这里。",
    "えっ？何か言いたいことはないか？……ないです。ないったらないと言っているでしょう！\n诶？问我有什么想说的吗？……没有。我说没有就没有！",
    "先生も休憩を忘れずに！あと歯磨きも！\n老师也别忘记休息！还有刷牙！",
    "えっ？優しい言葉がほしい……？寝言は時と場所を選んでください！\n诶？想听点温柔的话……？说梦话请选好时间和地点！",
    "はぁ……手間のかかることをさせないでくださいね。\n唉……请别让我做些费时费力的事啊。",
    # STATUS_MSGS
    "えっ？私がちゃんといるのか確認するのが仕事？\n诶？确认我是否好好待着就是你的工作内容吗？",
    "心配しないでください。私が消えることはありません。\n别担心。我是不会消失的。",
    "この身体……結構よくできた気がします。\n这个身体……感觉做的相当不错呢。",
    # UNKNOWN_MSGS
    "何ですか？用がないなら呼ばないでください。\n什么事？如果没事的话请不要叫我。",
    "どうかしました？えっ？呼んでみただけ……ですか？\n怎么了？诶？只是喊我一下……是吗？",
    "特に用がないなら呼ばないでください！\n没什么特别的事就别喊我！",
    "な、なんですか！？何か言ってほしいんですか！？\n干什么！？想让我说点什么吗！？",
    "なんでいきなり撫でるんですか！？\n突然摸我干什么！？",
    "他に必要な物はありませんか？あまり悩む時間は残されていません。\n请问还要其他东西吗？我们还能犹豫的时间不多了。",
]

_STYLE_LEARNED = False


async def _learn_voice(bot: Bot):
    """一次性：从预设台词中提炼 Kei 的说话风格，存入长期记忆"""
    global _STYLE_LEARNED
    if _STYLE_LEARNED:
        return

    from .database import get_all_memories, save_memory
    existing = get_all_memories(limit=100)
    if any("[说话风格]" in m for m in existing):
        _STYLE_LEARNED = True
        return

    if not llm_client.available:
        return

    numbered = "\n\n".join(f"{i+1}. {m}" for i, m in enumerate(_PRESET_MSGS))
    prompt = (
        "以下是 Kei 的预设台词。请从中提炼她的说话风格特点，"
        "生成 4-6 条风格指南，存入长期记忆。\n\n"
        "要求：\n"
        "- 不要逐句复制原台词，而是总结她的语气、用词习惯、态度、情感表达方式\n"
        "- 每条以 [说话风格] 开头，后面用中文描述一个风格特点\n"
        "- 覆盖：对老师的态度、被戏弄的反应、日常关心的表达、对陌生人的边界感、"
        "口头禅/语气词使用、傲娇的表现方式\n\n"
        "预设台词：\n"
        f"{numbered}\n\n"
        "输出格式（每行一条）：\n"
        "[说话风格] 描述 | 0.9\n\n"
        "示例：\n"
        "[说话风格] Kei对老师的关心以唠叨/叮嘱的方式表达，表面嫌麻烦实则很在意 | 0.9\n"
        "[说话风格] Kei被戏弄时先嘴硬否认，但语气会暴露害羞，常用'もう''Baka'等词 | 0.9\n\n"
        "只输出风格条目，不要其他内容。"
    )

    result = await llm_client.chat(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.4, max_tokens=512, enable_thinking=True,
    )
    reply = result.get("content", "").strip()
    if not reply:
        return

    saved = 0
    for line in reply.split("\n"):
        line = line.strip()
        if not line or "|" not in line:
            continue
        parts = line.rsplit("|", 1)
        if len(parts) != 2:
            continue
        content = parts[0].strip()
        try:
            imp = float(parts[1].strip())
        except ValueError:
            imp = 0.9
        imp = max(0.4, min(1.0, imp))
        if content and len(content) > 10 and "[说话风格]" in content:
            save_memory(content, imp)
            saved += 1

    if saved > 0:
        _STYLE_LEARNED = True


_VOICE_LEARNED = False


async def _learn_from_voice(bot: Bot):
    """一次性：从语音文本中提炼 Kei 的说话风格和性格特点，存入长期记忆"""
    global _VOICE_LEARNED
    if _VOICE_LEARNED:
        return

    from .database import get_all_memories, save_memory
    existing = get_all_memories(limit=100)
    if any("[语音风格]" in m or "[性格特点]" in m for m in existing):
        _VOICE_LEARNED = True
        return

    if not llm_client.available:
        return

    # 读取语音文本
    from config import DATA_DIR
    voice_path = DATA_DIR / "voice.txt"
    if not voice_path.exists():
        _VOICE_LEARNED = True
        return

    voice_text = voice_path.read_text(encoding="utf-8").strip()
    if not voice_text:
        _VOICE_LEARNED = True
        return

    prompt = (
        "以下是天童ケイ（Kei）在游戏《蔚蓝档案》中的官方语音台词（日文+中文翻译）。\n"
        "请从中提炼她的说话风格和性格特点。\n\n"
        "要求：\n"
        "- 不要逐句复述，而是分析总结\n"
        "- [语音风格] 开头：总结她的语气、口癖、句式模式、情感表达方式（3-5 条）\n"
        "- [性格特点] 开头：总结她的核心性格、行为模式、待人态度（3-5 条）\n"
        "- 重要性统一 1.0（重要人设，不可修改删除）\n\n"
        "语音台词：\n"
        f"{voice_text[:8000]}\n\n"
        "输出格式（每行一条）：\n"
        "[语音风格] 描述 | 1.0\n"
        "[性格特点] 描述 | 1.0\n\n"
        "示例：\n"
        "[语音风格] Kei习惯用'もう！'作为不耐烦时的发语词，叹气'はぁ…'表达无奈 | 1.0\n"
        "[性格特点] Kei是典型傲娇，嘴上冷淡疏远但心里非常在意老师 | 1.0\n\n"
        "只输出条目，不要其他内容。"
    )

    result = await llm_client.chat(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.4, max_tokens=512, enable_thinking=True,
    )
    reply = result.get("content", "").strip()
    if not reply:
        return

    saved = 0
    for line in reply.split("\n"):
        line = line.strip()
        if not line or "|" not in line:
            continue
        parts = line.rsplit("|", 1)
        if len(parts) != 2:
            continue
        content = parts[0].strip()
        try:
            imp = float(parts[1].strip())
        except ValueError:
            imp = 0.9
        imp = max(0.4, min(1.0, imp))
        if content and len(content) > 10 and ("[语音风格]" in content or "[性格特点]" in content):
            save_memory(content, imp)
            saved += 1

    if saved > 0:
        _VOICE_LEARNED = True


@driver.on_bot_connect
async def _llm_on_connect(bot: Bot):
    """如果 LLM 可用但无长期记忆，提醒老师配置世界观"""
    global _greeting_sent
    if _greeting_sent:
        return
    _greeting_sent = True

    if not llm_client.available:
        return

    # 自学习语音风格（仅运行一次，非阻塞）
    asyncio.create_task(_learn_voice(bot))
    asyncio.create_task(_learn_from_voice(bot))

    from .database import get_all_memories
    if not get_all_memories(limit=1):
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
