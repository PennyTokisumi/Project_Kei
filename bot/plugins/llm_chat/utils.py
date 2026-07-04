"""LLM Chat 插件 — 消息解析工具"""

import json as _json
from pathlib import Path as _Path

from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageSegment


def __json_dumps(obj) -> str:
    try:
        return _json.dumps(obj, ensure_ascii=False, indent=2, default=str)
    except Exception:
        return str(obj)


def _write_fwd_debug(text: str):
    try:
        (_Path("data") / ".fwd_debug").write_text(text, encoding="utf-8")
    except Exception:
        pass


def has_media(event: GroupMessageEvent) -> bool:
    """检查消息是否包含图片或视频"""
    for seg in event.message:
        if seg.type in ("image", "video"):
            return True
    return False


def _segments_to_text(segments, get_type, get_data) -> str:
    """将消息段列表转为纯文本，兼容 SnowLuma (attr) 和 OneBot (dict)。"""
    parts = []
    for s in segments:
        t = get_type(s)
        d = get_data(s)
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
            if isinstance(msg, list):
                text = _segments_to_text(msg, lambda s: getattr(s, "type", ""), lambda s: getattr(s, "data", {}) or {})
            elif isinstance(msg, str):
                text = msg
            else:
                text = ""

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
                    if isinstance(msg, list):
                        text = _segments_to_text(msg, lambda s: s.get("type", ""), lambda s: s.get("data", {}) or {})
                    elif isinstance(msg, str):
                        text = msg
                    else:
                        text = ""

                    if sender_name and text:
                        return f"{sender_name}: {text}"
                    return text or ""
                except Exception:
                    pass
    return ""


async def get_forward_text(event: GroupMessageEvent, bot: Bot | None = None) -> str:
    """获取转发/合并消息的内容文本。

    路径（按优先级）：
    1. event.message 中找 forward 段 → get_forward_msg
    2. get_msg 完整消息中找 forward 段 → get_forward_msg
    3. SnowLuma: 合并转发消息显示为 [聊天记录]，直接用 message_id 调 get_forward_msg
    """
    if bot is None:
        return ""

    # 1. event.message 中的 forward 段
    for seg in event.message:
        if seg.type == "forward":
            fwd_id = seg.data.get("id", "") if hasattr(seg, "data") else ""
            if fwd_id:
                try:
                    resp = await bot.call_api("get_forward_msg", id=fwd_id)
                    return _parse_forward_response(resp)
                except Exception:
                    pass

    # 2. get_msg 完整消息中找 forward 段
    try:
        raw = await bot.call_api("get_msg", message_id=int(event.message_id))
        raw_msg = raw.get("message", []) if isinstance(raw, dict) else []
        for seg in raw_msg:
            if isinstance(seg, dict) and seg.get("type") == "forward":
                fwd_id = seg.get("data", {}).get("id", "")
                if fwd_id:
                    resp = await bot.call_api("get_forward_msg", id=fwd_id)
                    return _parse_forward_response(resp)
    except Exception:
        pass

    # 3. SnowLuma: 合并转发消息 content 显示为 [聊天记录]，直接用 message_id
    msg_text = ""
    for seg in event.message:
        t = getattr(seg, "type", "")
        if t == "text":
            msg_text += getattr(seg, "data", {}).get("text", "") if hasattr(seg, "data") else ""
    if "聊天记录" in msg_text or "合并" in msg_text:
        try:
            resp = await bot.call_api("get_forward_msg", id=str(event.message_id))
            _write_fwd_debug(f"get_forward_msg OK: {_json_dumps(resp)[:2000]}")
            result = _parse_forward_response(resp)
            if result:
                return result
        except Exception as e:
            _write_fwd_debug(f"get_forward_msg FAIL: {e}")

    return ""


def _parse_forward_response(resp: dict) -> str:
    """解析 get_forward_msg 返回的 messages 数组"""
    messages = resp.get("messages", []) if isinstance(resp, dict) else []
    parts = []
    for msg in messages:
        sender = msg.get("sender", {}) or {}
        name = sender.get("nickname", "") or sender.get("card", "") or ""
        user_id = str(sender.get("user_id", ""))
        # SnowLuma bug: 合并转发中 nickname/card 可能为空，用 QQ 号兜底
        if not name or name == "QQ用户":
            name = f"QQ{user_id}"
        content = msg.get("content", "") or msg.get("message", "")
        if isinstance(content, list):
            text = _segments_to_text(
                content,
                lambda s: s.get("type", "") if isinstance(s, dict) else getattr(s, "type", ""),
                lambda s: s.get("data", {}) if isinstance(s, dict) else (getattr(s, "data", {}) or {}),
            )
        else:
            text = str(content)
        if name and text:
            parts.append(f"{name}: {text}")
        elif text:
            parts.append(text)
    return "\n".join(parts)


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


