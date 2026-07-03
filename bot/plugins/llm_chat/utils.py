"""LLM Chat 插件 — 消息解析工具"""

from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageSegment


def has_image(event: GroupMessageEvent) -> bool:
    """检查消息是否包含图片"""
    for seg in event.message:
        if seg.type == "image":
            return True
    return False


async def get_reply_text(event: GroupMessageEvent, bot: Bot | None = None) -> str:
    """获取被回复消息的发送者名和文本内容。

    SnowLuma 在 event.reply 中直接提供完整回复消息，无需 get_msg API。
    兼容标准 OneBot 的 reply message segment。
    """
    # SnowLuma: event.reply 对象
    ev_reply = getattr(event, "reply", None)
    if ev_reply is not None:
        try:
            sender = getattr(ev_reply, "sender", None)
            if sender:
                sender_name = getattr(sender, "nickname", "") or getattr(sender, "card", "") or f"QQ{getattr(sender, 'user_id', '')}"
            else:
                sender_name = ""

            msg = getattr(ev_reply, "message", "")
            text = ""
            if isinstance(msg, list):
                parts = []
                for s in msg:
                    t = getattr(s, "type", "")
                    d = getattr(s, "data", {}) or {}
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
                text = "".join(parts)
            elif isinstance(msg, str):
                text = msg

            if sender_name and text:
                return f"{sender_name}: {text}"
            return text or ""
        except Exception:
            pass
        return ""

    # 标准 OneBot: reply 段 + get_msg API
    for seg in event.message:
        if seg.type == "reply":
            msg_id = seg.data.get("id", "")
            if msg_id and bot is not None:
                try:
                    resp = await bot.call_api("get_msg", message_id=int(msg_id))
                    sender = resp.get("sender", {}) or {}
                    sender_name = sender.get("nickname", "") or sender.get("card", "") or f"QQ{sender.get('user_id','')}"

                    msg = resp.get("message", "")
                    text = ""
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
                        text = "".join(parts)
                    elif isinstance(msg, str):
                        text = msg

                    if sender_name and text:
                        return f"{sender_name}: {text}"
                    return text or ""
                except Exception:
                    pass
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


