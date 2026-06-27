"""LLM Chat 插件 — 群聊历史拉取"""

from pathlib import Path
from datetime import datetime

from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message
from nonebot.rule import Rule, to_me

from config import DATA_DIR
from .utils import extract_text

CHATLOG_DIR = DATA_DIR / "chatlogs"


async def fetch_and_save(bot: Bot, group_id: int, count: int = 100,
                         message_seq: int = 0) -> tuple[int, str]:
    """拉取历史消息并保存为 txt"""
    CHATLOG_DIR.mkdir(parents=True, exist_ok=True)

    result = await bot.call_api(
        "get_group_msg_history",
        group_id=group_id,
        count=count,
        message_seq=message_seq,
    )

    messages = result.get("messages", [])
    if not messages:
        return 0, ""

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"chatlog_{group_id}_{timestamp}.txt"
    filepath = CHATLOG_DIR / filename

    lines = []
    for msg in messages:
        # 消息格式: [时间] 昵称(QQ号): 内容
        t = datetime.fromtimestamp(msg.get("time", 0)).strftime("%m-%d %H:%M")
        sender = msg.get("sender", {})
        nickname = sender.get("nickname", "") or sender.get("card", "") or str(sender.get("user_id", "?"))
        user_id = sender.get("user_id", "")
        # 获取消息文本
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


# ─── 指令 ────────────────────────────────────────────

history_cmd = on_message(
    rule=to_me() & Rule(lambda e: extract_text(e).strip().startswith("history")),
    priority=5,
)


@history_cmd.handle()
async def handle_history(event: GroupMessageEvent, bot: Bot):
    """@Kei history — 拉取历史消息并保存"""
    if str(event.user_id) != "823262716":
        return  # 非 Sensei 静默，交给 unknown_cmd

    # 解析数量参数: history 200 → 200 条
    text = extract_text(event).strip()
    parts = text.split()
    msg_count = 100
    if len(parts) >= 2 and parts[1].isdigit():
        msg_count = min(int(parts[1]), 500)  # 最多 500 条

    await history_cmd.send(Message(f"正在拉取 {msg_count} 条历史消息..."))
    count = 0
    path = ""
    error_msg = None
    try:
        count, path = await fetch_and_save(bot, event.group_id, count=msg_count)
    except Exception as e:
        error_msg = str(e)

    if error_msg:
        await history_cmd.finish(Message(f"\n拉取失败: {error_msg}"))
    elif count == 0:
        await history_cmd.finish(Message("\n未拉取到历史消息。"))
    else:
        await history_cmd.finish(Message(f"已保存 {count} 条消息到:\n{path}"))
