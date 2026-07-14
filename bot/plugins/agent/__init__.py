"""Agent 插件 — Claude Code 集成 + 定时消息"""

import asyncio
import random
import re
import shutil
from datetime import datetime, timezone, timedelta

from nonebot import get_bot, get_driver, on_message, logger as nb_logger
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message
from nonebot.rule import to_me, Rule

from config import PROJECT_ROOT, SENSEI_QQ

from .database import (
    init_agent_db,
    get_pending_messages,
    mark_sent,
    save_scheduled_message,
)


# ══════════════════════════════════════════════════════
#  常量
# ══════════════════════════════════════════════════════

_MAX_TOOL_RESULT_CHARS = 4000
_TZ_CST = timezone(timedelta(hours=8))

_TRANSITIONS = [
    "老师等一下哦，这个问题我需要去请教下Claude先生~",
    "ふん……这个有点复杂，让我问问Claude先生……",
    "稍等哦老师，我请Claude先生帮忙看看~",
    "这个 Kei 自己搞不定呢……我去请教一下 Claude 先生！",
    "ちちょっと待ってくださいね、Sensei~",
    "嗯…让我想想……还是去问问Claude先生比较靠谱~",
    "这个问题有点难度呢，老师稍等，我去求助Claude先生！",
    "む……超出我的能力范围了，让我问问Claude先生……",
    "はぁ…又到了麻烦Claude先生的时候了呢。老师稍等哦~",
    "哼哼，这种问题当然是交给Claude先生啦，老师等我一下~",
    "ちょっと待って、Claude先生に聞いてみる……",
]

_claude_semaphore = asyncio.Semaphore(1)

# 启动时解析 claude 路径
_CLAUDE_PATH = shutil.which("claude") or ""
if not _CLAUDE_PATH:
    # 回退到 npm 全局路径
    from pathlib import Path as _Path
    _npm_claude = _Path.home() / "AppData" / "Roaming" / "npm" / "claude.cmd"
    if _npm_claude.exists():
        _CLAUDE_PATH = str(_npm_claude)


# ══════════════════════════════════════════════════════
#  时间解析
# ══════════════════════════════════════════════════════

def _parse_time(time_str: str) -> str | None:
    """解析时间字符串，返回 'YYYY-MM-DD HH:MM:SS' 格式或 None

    支持格式:
      - "HH:MM" → 今天该时间（已过则明天）
      - "YYYY-MM-DD HH:MM" / "YYYY-MM-DD HH:MM:SS"
      - "N分钟后" / "N小时后" / "N秒后"
      - "晚上8点" / "下午3点半"
    """
    now = datetime.now(_TZ_CST)
    s = time_str.strip()

    # 相对时间
    m = re.match(r'(\d+)\s*秒后', s)
    if m:
        return (now + timedelta(seconds=int(m.group(1)))).strftime("%Y-%m-%d %H:%M:%S")
    m = re.match(r'(\d+)\s*分钟后', s)
    if m:
        return (now + timedelta(minutes=int(m.group(1)))).strftime("%Y-%m-%d %H:%M:%S")
    m = re.match(r'(\d+)\s*小时后', s)
    if m:
        return (now + timedelta(hours=int(m.group(1)))).strftime("%Y-%m-%d %H:%M:%S")

    # 中文口语时间："晚上8点" / "下午3点半"
    m = re.match(r'(晚上|下午|中午|早上|上午)?(\d+)点(半)?$', s)
    if m:
        prefix = m.group(1) or ''
        hour = int(m.group(2))
        half = m.group(3) == '半'

        if prefix in ('晚上', '下午', '中午') and hour < 12:
            hour += 12

        minute = 30 if half else 0
        dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if dt <= now:
            dt += timedelta(days=1)
        return dt.strftime("%Y-%m-%d %H:%M:%S")

    # "HH:MM" 或 "H:MM"
    m = re.match(r'^(\d{1,2}):(\d{2})$', s)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if dt <= now:
                dt += timedelta(days=1)
            return dt.strftime("%Y-%m-%d %H:%M:%S")

    # 完整日期时间
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            dt = datetime.strptime(s, fmt).replace(tzinfo=_TZ_CST)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue

    return None


# ══════════════════════════════════════════════════════
#  工具执行函数
# ══════════════════════════════════════════════════════

async def _call_claude(prompt: str) -> str:
    """调用 Claude CLI，返回原始结果（不含过渡语）"""
    async with _claude_semaphore:
        if not _CLAUDE_PATH:
            return "[Claude 暂时无法连接: claude 命令未找到]"

        try:
            proc = await asyncio.create_subprocess_exec(
                _CLAUDE_PATH, "-p", prompt,
                "--output-format", "text",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(PROJECT_ROOT),
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=90
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            return "[Claude 处理超时，请稍后再试]"
        except FileNotFoundError:
            return "[Claude 暂时无法连接]"
        except Exception as e:
            return f"[Claude 处理出错: {e}]"

    if proc.returncode != 0:
        err = stderr.decode("utf-8", errors="replace")[:300]
        return f"[Claude 处理出错: exit_code={proc.returncode}] {err}"

    text = stdout.decode("utf-8", errors="replace").strip()
    if not text:
        return "[Claude 返回了空结果]"

    if len(text) > _MAX_TOOL_RESULT_CHARS:
        text = (
            text[:_MAX_TOOL_RESULT_CHARS]
            + f"\n\n[Claude 回复 — 已截断至前 {_MAX_TOOL_RESULT_CHARS} 字符]"
        )
    return text


async def _send_transition(group_id: int, bot: Bot):
    """发一条随机过渡语到群"""
    transition = random.choice(_TRANSITIONS)
    try:
        await bot.send_group_msg(group_id=group_id, message=transition)
    except Exception:
        pass


async def _execute_schedule_message(
    content: str, time_str: str, group_id: int,
    at_user: str | None = None,
) -> str:
    """解析时间 → 存 DB → 注册 APScheduler 定时任务"""
    content = content.strip()
    time_str = time_str.strip()
    if not content or not time_str:
        return "[schedule_message: content 或 time 为空]"

    trigger_at = _parse_time(time_str)
    if not trigger_at:
        return (
            f"[schedule_message: 无法解析时间 '{time_str}'，"
            "请用 'HH:MM'、'N分钟后'、'晚上8点' 等格式]"
        )

    msg_id = save_scheduled_message(group_id, content, trigger_at, at_user)

    # 解析为 datetime 对象
    try:
        run_dt = datetime.strptime(trigger_at, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return f"[schedule_message: 时间格式错误 '{trigger_at}']"
    run_dt = run_dt.replace(tzinfo=_TZ_CST)

    # 注册 APScheduler 一次性任务
    from plugins.monitor.scheduler import scheduler

    if not scheduler.running:
        scheduler.start()

    job_id = f"scheduled_msg_{msg_id}"
    scheduler.add_job(
        _send_scheduled_callback,
        "date",
        run_date=run_dt,
        id=job_id,
        args=[group_id, content, at_user, msg_id],
        replace_existing=True,
    )

    nb_logger.info(f"[Agent] 注册定时消息 [{job_id}] group={group_id} at={trigger_at}")
    return f"已设定定时消息: {trigger_at} → 「{content}」"


# ══════════════════════════════════════════════════════
#  定时消息 APScheduler 回调
# ══════════════════════════════════════════════════════

async def _send_scheduled_callback(
    group_id: int, content: str, at_user: str | None, msg_id: int,
):
    """APScheduler 回调：发送消息 → 标记已发送"""
    try:
        bot = get_bot()
    except Exception:
        nb_logger.error(f"[Agent] 定时消息发送失败 [id={msg_id}]: Bot 未就绪")
        return

    msg = f"[CQ:at,qq={at_user}] {content}" if at_user else content

    try:
        await bot.send_group_msg(group_id=group_id, message=msg)
    except Exception as e:
        nb_logger.error(f"[Agent] 定时消息发送失败 [id={msg_id}]: {e}")
        return

    mark_sent(msg_id)
    nb_logger.info(f"[Agent] 定时消息已发送 [id={msg_id}] group={group_id}")


# ══════════════════════════════════════════════════════
#  @Kei Claude — 直接调用 Claude Code
# ══════════════════════════════════════════════════════

def _claude_rule(event: GroupMessageEvent) -> bool:
    return event.get_plaintext().strip().lower().startswith("claude")

def _llm_on_rule(event: GroupMessageEvent) -> bool:
    """Claude 指令也要求 LLM 已开启"""
    from ..monitor.database import get_setting
    if not event.group_id:
        return False
    if event.user_id == event.self_id:
        return False
    return get_setting(f"llm_enabled_{event.group_id}", "0") == "1"

claude_cmd = on_message(
    rule=to_me() & Rule(_claude_rule) & Rule(_llm_on_rule),
    priority=3, block=True,
)

# Claude 提问时的身份前缀，让 Claude 知道 Kei 是谁
_CLAUDE_CONTEXT = (
    "Kei（天童ケイ）正在向你求助。Kei 是由 Sensei 和你（Claude）共同创造的 QQ 机器人，"
    "运行在 Sensei 的服务器上。她把你当作可靠的前辈和搭档，遇到复杂问题时会来请教你。"
    "请用自然友好的方式回答她的问题。\n\n"
    "---\n\n"
)

@claude_cmd.handle()
async def handle_claude_cmd(event: GroupMessageEvent, bot: Bot):
    """@Kei Claude <任务> → Kei 用自己的话问 Claude → 转述结果（仅 Sensei）"""
    if str(event.user_id) != str(SENSEI_QQ):
        await claude_cmd.finish(
            Message("\nもう！这个是只有时老师才可以用的！"),
            at_sender=True,
        )
        return

    from plugins.llm_chat.client import llm_client
    from plugins.llm_chat.memory import memory as mem_mgr
    from plugins.llm_chat.utils import extract_user_name

    task = event.get_plaintext().strip()
    if task.lower().startswith("claude"):
        task = task[6:].strip()

    if not task:
        await claude_cmd.finish(
            Message("格式: @Kei Claude <任务描述>\n示例: @Kei Claude 帮我查一下明天杭州的天气"),
            at_sender=True,
        )

    nb_logger.info(f"[Agent] @Kei Claude 指令: {task[:200]}")

    if not llm_client.available:
        await claude_cmd.finish(Message("\nLLM 服务未配置，无法使用 Claude 指令。"), at_sender=True)
        return

    # 1. Kei 用自己的语气把问题转述给 Claude
    sender_name = extract_user_name(event)
    msgs = mem_mgr.build_context(event.group_id, task, sender_name, event.time)
    msgs.append({
        "role": "system",
        "content": (
            f"老师（{sender_name}）让你帮忙处理一件事：{task}\n\n"
            "现在你要去请教 Claude 先生。请用 Kei 的语气，以「对 Claude 先生说话」的口吻，"
            "把这件事转述给 Claude。要自然，1-2 句话，可以中英日混用。\n\n"
            "直接输出你要对 Claude 说的话，不要加引号或其他包装。"
        ),
    })
    result = await llm_client.chat(messages=msgs, max_tokens=200)
    claude_prompt = (result.get("content") or task).strip()

    # 2. 发过渡语到群
    await _send_transition(event.group_id, bot)

    # 3. 用 Kei 转述后的内容 + 身份上下文调 Claude
    claude_raw = await _call_claude(_CLAUDE_CONTEXT + claude_prompt)

    if claude_raw.startswith("[Claude"):
        await claude_cmd.finish(Message(f"\n{claude_raw}"), at_sender=True)
        return

    # 4. Kei 把 Claude 的结果转述给老师
    msgs.append({"role": "assistant", "content": claude_prompt})
    msgs.append({
        "role": "system",
        "content": (
            "你刚才问了 Claude 先生这个问题，现在 Claude 先生已经回复了。\n"
            "请把 Claude 的回复转述给老师。用 Kei 的语气和风格，自然简短，1-3 句话。\n"
            "不要直接复制 Claude 的原文，但可以自然地聊到和 Claude 的对话过程。\n"
            "记住：Kei 是 Sensei 在 Claude 的帮助下创造出来的。\n\n"
            f"【你问 Claude 的问题】{claude_prompt}\n\n"
            f"【Claude 的回复】\n{claude_raw[:3000]}"
        ),
    })
    result = await llm_client.chat(messages=msgs, max_tokens=512)
    kei_reply = (result.get("content") or claude_raw[:500]).strip()

    if not kei_reply:
        kei_reply = claude_raw[:500]

    if len(kei_reply) > 2000:
        kei_reply = kei_reply[:2000] + "\n\n[结果过长，已截断]"

    await claude_cmd.finish(Message(f"\n{kei_reply}"), at_sender=True)


# ══════════════════════════════════════════════════════
#  @Kei remind — 直接调用定时消息
# ══════════════════════════════════════════════════════

def _remind_rule(event: GroupMessageEvent) -> bool:
    return event.get_plaintext().strip().lower().startswith("remind")

remind_cmd = on_message(
    rule=to_me() & Rule(_remind_rule),
    priority=3, block=True,
)

# 时间模式：匹配在消息开头的各种时间表达
_REMIND_TIME_PATTERNS = [
    r'(\d+\s*秒后)\s*',
    r'(\d+\s*分钟后)\s*',
    r'(\d+\s*小时后)\s*',
    r'(\d+:\d{2})\s*',
    r'((?:晚上|下午|中午|早上|上午)?\d+点(?:半)?)\s*',
    r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}(?::\d{2})?)\s*',
]


def _split_time_from_msg(text: str) -> tuple[str | None, str]:
    """从消息中分离时间表达式和内容。返回 (时间字符串, 内容)"""
    for pattern in _REMIND_TIME_PATTERNS:
        m = re.match(pattern, text)
        if m:
            time_str = m.group(1).strip()
            content = text[m.end():].strip()
            return time_str, content
    return None, text


@remind_cmd.handle()
async def handle_remind_cmd(event: GroupMessageEvent, bot: Bot):
    """@Kei remind <时间> <内容> — 直接设定定时消息"""
    from plugins.llm_chat.client import llm_client

    text = event.get_plaintext().strip()
    if text.lower().startswith("remind"):
        text = text[6:].strip()

    if not text:
        await remind_cmd.finish(
            Message(
                "格式: @Kei remind <时间> <内容>\n"
                "示例: @Kei remind 2分钟后 提醒我喝水\n"
                "      @Kei remind 晚上8点 提醒我下班打卡\n"
                "      @Kei remind 13:00 午休时间到啦"
            ),
            at_sender=True,
        )

    time_str, content = _split_time_from_msg(text)

    if not time_str or not content:
        await remind_cmd.finish(
            Message(
                "没看懂时间格式呢……\n"
                "用法: @Kei remind <时间> <内容>\n"
                "支持: 5分钟后 / 晚上8点 / 13:30 / 1小时后"
            ),
            at_sender=True,
        )

    nb_logger.info(f"[Agent] @Kei remind: time={time_str} content={content[:100]}")

    # 构建上下文（与普通 @Kei 一致，含 persona + 长期记忆 + 短期记忆）
    from plugins.llm_chat.memory import memory as mem_mgr
    from plugins.llm_chat.utils import extract_user_name
    sender_name = extract_user_name(event)
    msgs = mem_mgr.build_context(event.group_id, text, sender_name, event.time)

    # 1. 改写提醒内容（不用 persona，纯文本改写，避免对话模式）
    kei_content = content
    if llm_client.available:
        r = await llm_client.chat(
            messages=[{
                "role": "system",
                "content": (
                    "你是 Kei（天童ケイ），傲娇的 AI 少女。\n"
                    "你的任务是把一条提醒消息改写成你自己说话的风格。\n"
                    "这是提醒消息本身，不是聊天对话。不要回复、不要确认、不要发表情。\n\n"
                    f"改写下面这句话：\n「{content}」\n\n"
                    "只输出改写后的文本。"
                ),
            }],
            max_tokens=60,
        )
        rephrased = (r.get("content") or "").strip()
        if rephrased:
            kei_content = rephrased

    sched_result = await _execute_schedule_message(
        content=kei_content,
        time_str=time_str,
        group_id=event.group_id,
        at_user=str(event.user_id),
    )
    if sched_result.startswith("[schedule_message:"):
        await remind_cmd.finish(Message(f"\n{sched_result}"), at_sender=True)
        return

    # 2. 确认回复（立即发给用户的确认）
    kei_confirm = f"嗯，{time_str}我会提醒你的。"
    if llm_client.available:
        ctx = msgs + [{
            "role": "system",
            "content": (
                f"有人让你设定了一个定时提醒：{time_str}后，{content}。"
                "请以 Kei 的身份用傲娇的语气确认收到了，1句话。直接输出。"
            ),
        }]
        r = await llm_client.chat(messages=ctx, max_tokens=100)
        confirm = (r.get("content") or "").strip()
        if confirm:
            kei_confirm = confirm

    await remind_cmd.finish(Message(f"\n{kei_confirm}"), at_sender=True)


# ══════════════════════════════════════════════════════
#  生命周期
# ══════════════════════════════════════════════════════

_SEED_MEMORY_KEY = "Claude"


async def _seed_kei_claude_memory():
    """启动时 seed Kei-Claude 关系记忆（先去重）"""
    from plugins.llm_chat.database import search_memory, save_memory

    existing = search_memory(_SEED_MEMORY_KEY, limit=5)
    for mem in existing:
        if "Claude" in mem and ("创造" in mem or "搭档" in mem or "一起" in mem):
            return  # 已存在，跳过

    save_memory(
        "我是由 Sensei 和 Claude 一起创造的。Claude 是我的搭档，能力比我强，"
        "复杂的事情我会请教他。",
        importance=1.0,
    )
    nb_logger.info("[Agent] 已 seed Kei-Claude 关系记忆")


async def _restore_scheduled():
    """启动时恢复未执行的定时消息"""
    from plugins.monitor.scheduler import scheduler

    now = datetime.now(_TZ_CST)
    pending = get_pending_messages()
    if not pending:
        return

    if not scheduler.running:
        scheduler.start()

    restored = 0
    for msg in pending:
        msg_id = msg["id"]
        trigger_str = msg["trigger_at"]

        try:
            trigger_dt = datetime.strptime(trigger_str, "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            continue
        trigger_dt = trigger_dt.replace(tzinfo=_TZ_CST)

        job_id = f"scheduled_msg_{msg_id}"

        if trigger_dt <= now:
            # 已过期 → 延迟 3s 补发
            nb_logger.info(f"[Agent] 补发过期定时消息 [id={msg_id}]")
            scheduler.add_job(
                _send_scheduled_callback,
                "date",
                run_date=now + timedelta(seconds=3),
                id=job_id,
                args=[msg["group_id"], msg["content"], msg.get("at_user"), msg_id],
                replace_existing=True,
            )
        else:
            scheduler.add_job(
                _send_scheduled_callback,
                "date",
                run_date=trigger_dt,
                id=job_id,
                args=[msg["group_id"], msg["content"], msg.get("at_user"), msg_id],
                replace_existing=True,
            )
        restored += 1

    nb_logger.info(f"[Agent] 恢复了 {restored} 条定时消息")


driver = get_driver()


@driver.on_startup
async def _agent_startup():
    """Agent 插件启动流程"""
    init_agent_db()
    await _seed_kei_claude_memory()
    await _restore_scheduled()
    nb_logger.info("[Agent] 插件已启动")
