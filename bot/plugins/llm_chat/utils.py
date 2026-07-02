"""LLM Chat 插件 — 消息解析工具"""

from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageSegment


async def get_reply_text(event: GroupMessageEvent, bot: Bot | None = None) -> str:
    """获取被回复消息的文本内容。bot 为 None 时仅返回提示占位。"""
    for seg in event.message:
        if seg.type == "reply":
            msg_id = seg.data.get("id", "")
            if msg_id and bot is not None:
                try:
                    resp = await bot.call_api("get_msg", message_id=int(msg_id))
                    msg = resp.get("message", "")
                    if isinstance(msg, list):
                        parts = []
                        for s in msg:
                            t = s.get("type", "")
                            d = s.get("data", {}) or {}
                            if t == "text":
                                parts.append(d.get("text", ""))
                            elif t == "image":
                                parts.append("[图片]")
                            elif t == "at":
                                parts.append(f"[@用户{d.get('qq','')}]")
                            elif t == "face":
                                parts.append("[表情]")
                            else:
                                parts.append(f"[{t}]")
                        return "".join(parts)
                    elif isinstance(msg, str):
                        return msg
                except Exception:
                    pass
    return ""


def _extract_reply_hint(event: GroupMessageEvent) -> str:
    """从 event 中提取 reply 段的提示占位（无 bot 时用）"""
    for seg in event.message:
        if seg.type == "reply":
            return "[回复了一条消息]"
    return ""


def extract_text(event: GroupMessageEvent) -> str:
    """从 GroupMessageEvent 提取纯文本内容"""
    text_parts = []
    for seg in event.message:
        if seg.type == "text":
            text_parts.append(seg.data.get("text", ""))
        elif seg.type == "at":
            qq = seg.data.get("qq", "")
            if qq and qq != str(event.self_id):
                text_parts.append(f"[@用户{qq}]")
        elif seg.type == "image":
            text_parts.append("[图片]")
        elif seg.type == "face":
            text_parts.append("[表情]")
    return "".join(text_parts)


def extract_images(event: GroupMessageEvent) -> list[str]:
    """提取消息中的图片 URL 列表"""
    urls = []
    for seg in event.message:
        if seg.type == "image":
            url = seg.data.get("url", "")
            if url:
                urls.append(url)
    return urls


def extract_user_name(event: GroupMessageEvent) -> str:
    """获取发送者标识（QQ 号在前，供 Kei 区分不同人）"""
    name = event.sender.card or event.sender.nickname or f"用户{event.user_id}"
    return f"QQ{event.user_id}({name})"


