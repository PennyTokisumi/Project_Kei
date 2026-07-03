"""Agent 插件 — Claude Code 集成：agent_loop + 工具执行 + 生命周期"""

import asyncio
import json
import logging
import random
import re
from datetime import datetime, timezone, timedelta

from nonebot import get_bot, get_driver, on_message, logger as nb_logger
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message
from nonebot.rule import to_me, Rule

from config import config, PROJECT_ROOT

_logger = logging.getLogger(__name__)

from .database import (
    init_agent_db,
    get_pending_messages,
    mark_sent,
    save_scheduled_message,
)
from .tools import build_tools, SENSEI_QQ, PUBLIC_USER_SYSTEM_PROMPT


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
]

_claude_semaphore = asyncio.Semaphore(1)


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

        if prefix in ('晚上',) and hour < 12:
            hour += 12
        elif prefix in ('下午',) and hour < 12:
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

async def _execute_delegate_to_claude(task: str, group_id: int, bot: Bot) -> str:
    """发自然过渡语 → 调 claude -p → 返回截断后的结果"""
    # 1. 发自然过渡语
    transition = random.choice(_TRANSITIONS)
    try:
        await bot.send_group_msg(group_id=group_id, message=transition)
    except Exception:
        pass

    # 2. 串行化调用 Claude
    async with _claude_semaphore:
        try:
            proc = await asyncio.create_subprocess_exec(
                "claude", "-p", task,
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


async def _execute_remember(content: str, importance: float = 0.6) -> str:
    """写入长期记忆"""
    from plugins.llm_chat.database import save_memory as _save
    content = content.strip()
    if not content:
        return "[remember: 内容为空]"
    _save(content, importance)
    return f"已记住: {content}"


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
#  Agent Loop
# ══════════════════════════════════════════════════════

async def agent_loop(
    messages: list[dict],
    tools: list[dict],
    group_id: int,
    bot: Bot,
) -> str:
    """工具调用循环。返回最终文本回复。"""
    from plugins.llm_chat.client import llm_client

    # 诊断：直接写文件绕过日志系统
    try:
        (config.DATA_DIR / ".agent_diag").write_text(
            f"agent_loop called\nmessages={len(messages)}\ntools={len(tools)}\n"
            f"group={group_id}\ntool_names={[t['function']['name'] for t in tools]}\n"
        )
    except Exception:
        pass

    _logger.info(
        f"[Agent] agent_loop 被调用: messages={len(messages)}, "
        f"tools={len(tools)}, group={group_id}"
    )

    async def _dispatch(name: str, args: dict) -> str:
        if name == "delegate_to_claude":
            return await _execute_delegate_to_claude(
                args.get("task", ""), group_id, bot,
            )
        elif name == "remember":
            return await _execute_remember(
                args.get("content", ""),
                float(args.get("importance", 0.6)),
            )
        elif name == "schedule_message":
            return await _execute_schedule_message(
                args.get("content", ""),
                args.get("time", ""),
                group_id,
                args.get("at_user"),
            )
        else:
            return f"[未知工具: {name}]"

    max_iter = config.agent_max_iterations

    for _ in range(max_iter):
        result = await llm_client.chat(
            messages=messages,
            tools=tools,
            max_tokens=512,
        )

        if result.get("error"):
            nb_logger.error(f"[Agent] LLM 错误: {result['error']}")
            return (result.get("content") or "") or "……"

        tool_calls = result.get("tool_calls")
        if not tool_calls:
            _logger.info(
                f"[Agent] 无 tool_calls，直接返回文本回复: "
                f"{(result.get('content') or '')[:100]}"
            )
            return (result.get("content") or "").strip()

        # 追加 assistant 消息（含 tool_calls）
        messages.append({
            "role": "assistant",
            "content": result.get("content") or "",
            "tool_calls": tool_calls,
        })

        # 执行每个工具调用
        for tc in tool_calls:
            func_info = tc.get("function", {})
            tool_name = func_info.get("name", "")
            tool_args_str = func_info.get("arguments", "{}")

            try:
                tool_args = json.loads(tool_args_str)
            except json.JSONDecodeError:
                tool_result = f"[工具参数解析错误: {tool_args_str[:200]}]"
                nb_logger.warning(
                    f"[Agent] JSON 解析失败 [{tool_name}]: {tool_args_str[:200]}"
                )
            else:
                nb_logger.info(
                    f"[Agent] 调用工具 [{tool_name}]: {str(tool_args)[:200]}"
                )
                try:
                    tool_result = await _dispatch(tool_name, tool_args)
                except Exception as e:
                    nb_logger.error(f"[Agent] 工具执行错误 [{tool_name}]: {e}")
                    tool_result = f"[工具执行错误: {e}]"

            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id", ""),
                "content": tool_result[:_MAX_TOOL_RESULT_CHARS],
            })

    # 最大轮数耗尽 → 强制文本回复
    nb_logger.warning(f"[Agent] 达到最大轮数 {max_iter}，强制文本回复")
    result = await llm_client.chat(messages=messages, max_tokens=512)
    return (result.get("content") or "").strip()


# ══════════════════════════════════════════════════════
#  对外接口
# ══════════════════════════════════════════════════════

def get_tools(sender_qq: int) -> list[dict]:
    """根据发送者 QQ 号获取对应的工具列表"""
    return build_tools(sender_qq)


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

@claude_cmd.handle()
async def handle_claude_cmd(event: GroupMessageEvent, bot: Bot):
    """@Kei Claude <任务> — 直接调用 Claude Code，不经过 agent_loop"""
    from plugins.llm_chat.client import llm_client
    from plugins.llm_chat.memory import memory as mem_mgr
    from plugins.llm_chat.utils import extract_user_name

    task = event.get_plaintext().strip()
    # 去掉 "Claude" 前缀（大小写不敏感）
    if task.lower().startswith("claude"):
        task = task[6:].strip()

    if not task:
        await claude_cmd.finish(
            Message("格式: @Kei Claude <任务描述>\n示例: @Kei Claude 帮我查一下明天杭州的天气"),
            at_sender=True,
        )

    nb_logger.info(f"[Agent] @Kei Claude 指令: {task[:200]}")

    # 1. 获取 Claude 的原始结果
    claude_raw = await _execute_delegate_to_claude(task, event.group_id, bot)

    if claude_raw.startswith("[Claude"):
        # Claude 出错（超时/无法连接/错误），直接返回
        await claude_cmd.finish(Message(f"\n{claude_raw}"), at_sender=True)
        return

    # 2. Kei 分两步回复：先复述需求，再转述结果
    sender_name = extract_user_name(event)
    context = mem_mgr.build_context(event.group_id, task, sender_name, event.time)
    context.append({
        "role": "system",
        "content": (
            "刚才你请 Claude 先生帮忙处理了老师的一个任务。"
            "现在需要你向老师汇报结果。\n\n"
            "请分两步回复，用 [SEP] 分隔：\n"
            "第一步：用 Kei 的语气复述一下老师让你查了什么，让老师知道你理解了需求。"
            "要自然，不要像机器人一样复读。\n"
            "第二步：转述 Claude 查到的结果，用自己的语气，保持 Kei 的傲娇风格，"
            "不要直接复制粘贴 Claude 的原文。\n\n"
            f"【老师的需求】{task}\n\n"
            f"【Claude 的回复】\n{claude_raw[:3000]}"
        ),
    })

    if not llm_client.available:
        # LLM 不可用时直接发 Claude 原文
        if len(claude_raw) > 2000:
            claude_raw = claude_raw[:2000] + "\n\n[结果过长，已截断]"
        await claude_cmd.finish(Message(f"\n{claude_raw}"), at_sender=True)
        return

    result = await llm_client.chat(messages=context, max_tokens=512)
    kei_reply = (result.get("content") or "").strip()

    if not kei_reply:
        await claude_cmd.finish(Message("……"), at_sender=True)
        return

    # 按 [SEP] 分割，逐条发送
    segments = [s.strip() for s in kei_reply.split("[SEP]")]
    segments = [s for s in segments if s]
    for i, seg in enumerate(segments[:3]):  # 最多 3 段
        if len(seg) > 2000:
            seg = seg[:2000] + "\n\n[结果过长，已截断]"
        await bot.send_group_msg(group_id=event.group_id, message=Message(seg))
        if i < len(segments[:3]) - 1:
            await asyncio.sleep(0.5)

    return  # 消息已在上方逐条发出


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
