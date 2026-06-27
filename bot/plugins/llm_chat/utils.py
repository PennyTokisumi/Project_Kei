"""LLM Chat 插件 — 消息解析工具"""

from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageSegment


def extract_text(event: GroupMessageEvent) -> str:
    """从 GroupMessageEvent 提取纯文本内容"""
    text_parts = []
    for seg in event.message:
        if seg.type == "text":
            text_parts.append(seg.data.get("text", ""))
        elif seg.type == "at":
            qq = seg.data.get("qq", "")
            # 排除 @bot 自身
            if qq and qq != str(event.self_id):
                text_parts.append(f"[@用户{qq}]")
        elif seg.type == "image":
            text_parts.append("[图片]")
        elif seg.type == "face":
            text_parts.append("[表情]")
    return "".join(text_parts)


def extract_user_name(event: GroupMessageEvent) -> str:
    """获取发送者昵称（含 QQ 号，供 Kei 识别 Sensei）"""
    name = event.sender.card or event.sender.nickname or f"用户{event.user_id}"
    return f"{name}(QQ:{event.user_id})"
