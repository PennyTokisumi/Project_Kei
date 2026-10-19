"""LLM Chat 插件 — 消息解析工具"""

from nonebot import logger as nb_logger
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageSegment

# 一次对话最多带几张图。图片会显著增加 prompt token（每张约 250+），
# 而且模型对同一条消息里的多图理解能力有限，超过 3 张收益很低。
MAX_IMAGES_PER_MESSAGE = 3


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

    ``event.reply`` **不是 SnowLuma 提供的**，而是 nonebot-adapter-onebot 自己造的：
    适配器 ``bot.py::_check_reply()`` 在 ``event.message`` 里发现 ``type == "reply"``
    的段后，调 ``get_msg`` 取回原消息并用 ``Reply`` 模型校验，成功才赋值给
    ``event.reply``；成功时还会顺手把这段 reply 段从 ``event.message`` 里删掉。

    因此下面两个分支的真实分工是：
      1. ``event.reply`` 有值 —— 适配器已经取好了，直接用（正常路径）；
      2. ``event.reply`` 为空 —— 说明适配器那一步失败了。它在 ``except`` 里只打一条
         WARNING 就 return，不会中断事件；此时 reply 段还留在 ``event.message`` 里，
         我们自己再调一次 ``get_msg`` 补救。

    注意分支 1 取不到内容时本函数只会返回空串（Kei 会把引用当成不存在），
    所以分支 2 是唯一能救回来的机会 —— 因此被调用方拿到的空串不能一律当成
    「用户没有引用」。
    """
    # 分支 1：适配器已解析好的引用（正常路径）
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
        except Exception as e:
            nb_logger.debug(f"[引用] 解析 event.reply 失败: {e!r}")
        return ""

    # 分支 2：适配器解析失败，reply 段仍在 event.message 里 → 自己调 get_msg
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
                except Exception as e:
                    nb_logger.debug(f"[引用] get_msg 补取失败 message_id={msg_id}: {e!r}")
    return ""


async def get_forward_text(event: GroupMessageEvent, bot: Bot | None = None) -> str:
    """获取转发/合并消息的内容文本。

    event.message 中的 forward 段（NoneBot 解析后）→ get_forward_msg，
    或通过 get_msg 获取原始消息 → 找 forward 段 → get_forward_msg。
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
        if isinstance(raw, dict):
            raw_msg = raw.get("message") or raw.get("data", {}).get("message", [])
        else:
            raw_msg = []
        for seg in raw_msg:
            if isinstance(seg, dict) and seg.get("type") == "forward":
                fwd_id = seg.get("data", {}).get("id", "")
                if fwd_id:
                    resp = await bot.call_api("get_forward_msg", id=fwd_id)
                    result = _parse_forward_response(resp)
                    if result:
                        return result
    except Exception:
        pass

    return ""


def _parse_forward_response(resp: dict) -> str:
    """解析 get_forward_msg 返回的 messages 数组。

    兼容两种格式：
    - NoneBot 解包: { messages: [...] }
    - SnowLuma 原始: { data: { messages: [...] } }
    """
    if not isinstance(resp, dict):
        return ""
    messages = resp.get("messages") or resp.get("data", {}).get("messages", [])
    parts = []
    for msg in messages:
        sender = msg.get("sender", {}) or {}
        name = sender.get("nickname", "") or sender.get("card", "") or ""
        user_id = str(sender.get("user_id", ""))
        # SnowLuma bug: 合并转发中 nickname/card 可能为空，用 QQ 号兜底
        if not name or name == "QQ用户":
            if user_id:
                name = f"QQ{user_id}"
            else:
                name = "未知用户"
        content = msg.get("content", "") or msg.get("message", "")
        if isinstance(content, list):
            text = _segments_to_text(
                content,
                lambda s: s.get("type", "") if isinstance(s, dict) else getattr(s, "type", ""),
                lambda s: s.get("data", {}) if isinstance(s, dict) else (getattr(s, "data", {}) or {}),
            )
        else:
            text = str(content)
        if text:
            if name:
                parts.append(f"{name}: {text}")
            else:
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
    """提取消息中的图片 URL（最多 MAX_IMAGES_PER_MESSAGE 张）

    SnowLuma 的 image 段把地址放在 `url`，标准 OneBot 放 `file`，两个都认。
    QQ 图片 URL 带有效期，所以必须尽快交给模型，不能缓存复用。
    """
    urls: list[str] = []
    for seg in event.message:
        if seg.type != "image":
            continue
        data = seg.data or {}
        url = data.get("url") or data.get("file") or ""
        if not url.startswith("http"):
            continue
        if url not in urls:
            urls.append(url)
        if len(urls) >= MAX_IMAGES_PER_MESSAGE:
            break
    return urls


def extract_user_name(event: GroupMessageEvent) -> str:
    """获取发送者标识（QQ 号在前，供 Kei 区分不同人）"""
    name = event.sender.card or event.sender.nickname or f"用户{event.user_id}"
    return f"QQ{event.user_id}({name})"


