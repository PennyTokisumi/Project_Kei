"""消息格式化 - B站动态用合并转发，直播开播优先用 Ark 卡片（失败回退普通图文）"""

from nonebot import logger as nb_logger
from nonebot.adapters.onebot.v11 import MessageSegment, Message, Bot

from .sources.base import Item


def build_live_message(item: Item) -> Message:
    """直播开播 → 普通消息（带封面图）

    格式:
        [封面图]
        主播：{nickname}
        标题：{title}
        游戏：{game_name}（如有）
        🔗 {link}
    """
    segs = Message()

    if item.cover_url:
        segs.append(MessageSegment.image(item.cover_url))

    parts = [
        f"标题：{item.title}",
        f"主播：{item.nickname}",
        f"链接：{item.link}",
    ]

    segs.append(MessageSegment.text("\n".join(parts)))
    return segs


def build_dynamic_forward_msg(items: list[Item]) -> list[dict]:
    """B站动态 → 合并转发消息节点列表

    即使只有 1 条动态，也以合并转发形式推送。
    每条动态作为一个独立的消息节点（node）。
    """
    nodes = []
    for item in items:
        content_parts = []

        if item.extra.get("is_video"):
            # 视频投稿专用格式
            parts = [f"{item.nickname}投稿了视频"]
            # 动态正文（如有）
            if item.content:
                parts.append(item.content)
            content_parts.append(MessageSegment.text("\n".join(parts) + "\n"))
            # 封面图
            if item.cover_url:
                content_parts.append(MessageSegment.image(item.cover_url))
            # 视频标题
            vid_title = item.extra.get("video_title", "") or item.title
            content_parts.append(MessageSegment.text(f"\n标题：{vid_title}"))
            # 简介
            video_desc = item.extra.get("video_desc", "")
            content_parts.append(MessageSegment.text(f"\n简介：{video_desc}"))
            # 链接
            content_parts.append(MessageSegment.text(f"\n链接：{item.link}"))
        elif item.extra.get("is_article"):
            # 专栏投稿专用格式
            content_parts.append(MessageSegment.text(f"{item.nickname}投稿了文章\n"))
            if item.cover_url:
                content_parts.append(MessageSegment.image(item.cover_url))
            art_title = item.extra.get("article_title", "") or item.title
            content_parts.append(MessageSegment.text(f"\n标题：{art_title}"))
            if item.nickname:
                content_parts.append(MessageSegment.text(f"\n来源：{item.nickname}"))
            content_parts.append(MessageSegment.text(f"\n链接：{item.link}"))
        else:
            # 图文/文字/转发格式
            if item.content:
                content_parts.append(MessageSegment.text(item.content + "\n"))
            if item.cover_urls:
                for url in item.cover_urls:
                    content_parts.append(MessageSegment.image(url))
            elif item.cover_url:
                content_parts.append(MessageSegment.image(item.cover_url))
            if item.nickname:
                content_parts.append(MessageSegment.text(f"\n来源：{item.nickname}"))
            content_parts.append(MessageSegment.text(f"\n链接：{item.link}"))

        node = {
            "type": "node",
            "data": {
                "name": item.nickname or "动态更新",
                "uin": "0",
                "content": content_parts,
            },
        }
        nodes.append(node)

    return nodes


def build_live_ark_params(item: Item) -> dict:
    """直播开播 → 图文 Ark 卡片参数（SnowLuma 2.0+ 的 send_tuwen_ark）

    实际渲染（QQ 卡片模板固定，无法自定义）::

        ┌────────────────────────────┐
        │ {title}                    │  第 1 行：直播标题
        │ {nickname} 开播了          │  第 2 行：desc
        │                  [封面]    │  右侧：preview_url
        ├────────────────────────────┤
        │ 其他来源                    │  ← QQ 对未注册来源的固定标签，改不了
        └────────────────────────────┘
        气泡外预览：{nickname} 开播了   ← summary

    几个被实测确认的限制：
    - ``desc`` 是单段落纯文本，只能自然折行；``\\n`` 会被直接删掉，
      ``U+2028`` 只在桌面 QQ 断行、手机 QQ 会变成空格。所以这里只用单行。
    - ``summary`` 不是卡片内的标签，而是**气泡外**的消息预览文字。
    - 卡片底部的「其他来源」无接口可控：自拼 ark JSON 走 ``json`` 消息段会被
      QQ 拒收（显示「发送者版本过低」），只有 ``send_tuwen_ark`` 能正常渲染。
    """
    nickname = item.nickname or f"房间{item.target_id}"
    headline = f"{nickname} 开播了"

    params: dict = {
        "title": item.title or headline,
        "desc": headline,
        "summary": headline,
        "jump_url": item.link,
    }
    # 没有封面时不传，让 SnowLuma 用它自己的默认预览图
    if item.cover_url:
        params["preview_url"] = item.cover_url
    return params


async def send_live_notification(bot: Bot, group_id: int, item: Item):
    """发送直播开播提醒

    优先发 Ark 卡片；卡片发送失败（接口不可用、QQ 拒收等）时回退到普通图文消息，
    避免因为卡片问题丢掉开播通知。
    """
    try:
        await bot.call_api(
            "send_tuwen_ark", group_id=group_id, **build_live_ark_params(item)
        )
    except Exception as e:
        nb_logger.warning(f"开播 Ark 卡片发送失败，回退普通图文消息: {e!r}")
        msg = build_live_message(item)
        await bot.send_group_msg(group_id=group_id, message=msg)

    # 记录到短期记忆，让 Kei 能看到自己推送的内容
    try:
        from plugins.llm_chat.memory import memory
        summary = f"[Kei推送了直播: {item.nickname} — {item.title}]"
        memory.add_assistant_message(group_id, summary)
    except Exception:
        pass


# ─── 合并转发卡片外观 ──────────────────────────────────────
# QQ 客户端对预览行有长度上限（超出会自己补「…」），这里先主动收一收，
# 避免在半个词中间被截断。
_NEWS_LINE_LIMIT = 40


def _first_content_line(text: str) -> str:
    """取正文第一行非空内容（动态正文可能带换行）"""
    for line in (text or "").splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def _clip(text: str, limit: int = _NEWS_LINE_LIMIT) -> str:
    """压掉多余空白，并按长度截断"""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "…"


def _news_line(item: Item) -> str:
    """为一条动态生成合并转发的预览行文案"""
    nickname = item.nickname or "B站用户"

    if item.extra.get("is_video"):
        title = item.extra.get("video_title") or item.title
        return _clip(f"{nickname} 投稿了视频 · {title}")

    if item.extra.get("is_article"):
        title = item.extra.get("article_title") or item.title
        return _clip(f"{nickname} 投稿了专栏 · {title}")

    head = _first_content_line(item.content)
    # 转发动态的正文首行自带「{nickname}：」前缀，去掉避免和昵称重复
    prefix = f"{nickname}："
    if head.startswith(prefix):
        head = head[len(prefix):].strip()

    if item.extra.get("is_forward"):
        return _clip(f"{nickname} 转发了动态 · {head}" if head else f"{nickname} 转发了动态")

    return _clip(f"{nickname}：{head}" if head else f"{nickname} 发布了动态")


def build_forward_preview(items: list[Item]) -> dict:
    """合并转发卡片的三个外观参数

    以下是在 QQ 桌面客户端上逐条实测出来的（2026-10-19）：

    - ``source``  → 卡片标题行。不传则显示 QQ 默认的「群聊的聊天记录」
      （私聊里是「{节点名}的聊天记录」）。
    - ``news``    → 中间预览行，**逐条对应节点**，完全取代 QQ 自动拼接的
      「昵称：正文首行」。这同时修掉了原来视频投稿显示
      「昵称：昵称投稿了视频」的双昵称问题。
    - ``summary`` → 底部「查看 N 条转发消息」。
    - ``prompt``  → 实测**没有任何可见效果**，因此不使用。
    """
    return {
        "source": "B站动态",
        "news": [{"text": _news_line(item)} for item in items],
        "summary": f"查看 {len(items)} 条转发消息",
    }


async def send_dynamic_forward(bot: Bot, group_id: int, items: list[Item]):
    """发送B站动态合并转发（即使只有1条）"""
    if not items:
        return

    nodes = build_dynamic_forward_msg(items)
    # SnowLuma 要求有效的 user_id/uin，替换 uin: "0" → bot 自身 QQ
    for node in nodes:
        node["data"]["uin"] = str(bot.self_id)

    try:
        await bot.call_api(
            "send_group_forward_msg",
            group_id=group_id,
            messages=nodes,
            **build_forward_preview(items),
        )
    except Exception as e:
        # 预览参数出问题也不能丢推送：退回 QQ 默认拼接的卡片样式
        nb_logger.warning(f"带预览参数的合并转发失败，回退默认样式: {e!r}")
        await bot.call_api(
            "send_group_forward_msg",
            group_id=group_id,
            messages=nodes,
        )

    # 记录到短期记忆
    try:
        from plugins.llm_chat.memory import memory
        titles = "、".join(it.title[:30] for it in items)
        summary = f"[Kei推送了动态: {titles}]"
        memory.add_assistant_message(group_id, summary)
    except Exception:
        pass
