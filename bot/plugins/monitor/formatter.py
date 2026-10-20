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

    节点正文不再重复昵称 —— 昵称由卡片标题（``source``）和节点发送者名承担。
    各类顺序：
        视频投稿：标题 → 封面 → 简介 → （正文） → 链接
        专栏文章：标题 → 封面 → 链接
        图文/转发：正文 → 图片 → 链接
    """
    nodes = []
    for item in items:
        content_parts = []

        if item.extra.get("is_video"):
            vid_title = (item.extra.get("video_title") or item.title or "").strip()
            # 标题就是第一行正文，不加「标题：」前缀
            content_parts.append(MessageSegment.text(f"{vid_title}\n"))
            if item.cover_url:
                content_parts.append(MessageSegment.image(item.cover_url))
            # 紧跟图片的文本不能带前导 \n，否则图片后会多出空行
            video_desc = (item.extra.get("video_desc", "") or "").strip()
            if video_desc:
                content_parts.append(MessageSegment.text(f"简介：{video_desc}"))
            # 动态附带的正文（如有），放在简介之后
            if item.content:
                content_parts.append(MessageSegment.text(f"\n{item.content}"))
            # 这里前面是文本，需要两个 \n 才能空出一行（与其他三类对齐）
            content_parts.append(MessageSegment.text(f"\n\n链接：{item.link}"))
        elif item.extra.get("is_article"):
            art_title = (item.extra.get("article_title") or item.title or "").strip()
            content_parts.append(MessageSegment.text(f"{art_title}\n"))
            if item.cover_url:
                content_parts.append(MessageSegment.image(item.cover_url))
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


def _type_phrase(item: Item) -> str:
    """这条动态「做了什么」——用作卡片标题行的谓语"""
    if item.extra.get("is_video"):
        return "投稿了视频"
    if item.extra.get("is_article"):
        return "投稿了专栏"
    if item.extra.get("is_forward"):
        return "转发了动态"
    return "发布了动态"


def _headline(item: Item) -> str:
    """这条动态的标题或正文首行 —— 用作预览行（视频/专栏直接用标题本身）"""
    if item.extra.get("is_video"):
        return (item.extra.get("video_title") or item.title or "").strip()
    if item.extra.get("is_article"):
        return (item.extra.get("article_title") or item.title or "").strip()

    head = _first_content_line(item.content)
    # 转发动态的正文首行自带「{nickname}：」前缀，去掉避免和昵称重复
    prefix = f"{item.nickname}："
    if head.startswith(prefix):
        head = head[len(prefix):].strip()
    return head


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

    文案分工：**标题行说「谁做了什么」，预览行说「标题/正文」**，
    这样折叠状态下就能直接看到视频标题 / 文章标题 / 动态正文。
    只有 1 条时标题行带昵称；多条时标题行退化为汇总，昵称改由预览行承担。
    """
    if len(items) == 1:
        item = items[0]
        nickname = item.nickname or "B站用户"
        headline = _headline(item)
        return {
            "source": f"{nickname}{_type_phrase(item)}",
            "news": [{"text": _clip(headline or _type_phrase(item))}],
            "summary": "查看 1 条转发消息",
        }

    news = []
    for item in items:
        nickname = item.nickname or "B站用户"
        headline = _headline(item)
        line = f"{nickname} {_type_phrase(item)}"
        if headline:
            line = f"{line} · {headline}"
        news.append({"text": _clip(line)})

    return {
        "source": f"B站动态 · {len(items)} 条更新",
        "news": news,
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


# ─── Pixiv 作品推送 ────────────────────────────────────────
# 折叠卡片：  {画师}投稿了P站  /  {标题}  /  查看 N 条转发消息
# 点开后：    {标题} → 图片（最多 pixiv_max_images 张）→ [超出提示] → 空行 → 链接
#
# Pixiv 图片必须自己下载后转 base64 —— i.pximg.net 有 Referer 防盗链，
# 直接传 URL 给 QQ 会裂图。详见 media.py。

PIXIV_MORE_HINT = "（全部图片请查看链接）"


def build_pixiv_preview(items: list[Item]) -> dict:
    """Pixiv 合并转发的卡片外观参数（source / news / summary）

    单条时标题行带画师名，多条时退化为汇总（和 B站动态同一套逻辑）。
    """
    if len(items) == 1:
        item = items[0]
        return {
            "source": f"{item.nickname}投稿了P站",
            "news": [{"text": _clip(item.title)}],
            "summary": "查看 1 条转发消息",
        }

    news = [
        {"text": _clip(f"{it.nickname}：{it.title}")}
        for it in items
    ]
    return {
        "source": f"Pixiv · {len(items)} 条更新",
        "news": news,
        "summary": f"查看 {len(items)} 条转发消息",
    }


async def build_pixiv_forward_msg(items: list[Item]) -> list[dict]:
    """Pixiv 作品 → 合并转发节点列表（图片现下载现编码）

    每张图都要：取页地址 → 下载（带 Referer）→ 缩放 → base64。
    单条作品最多 `pixiv_max_images` 张，超出的在末尾提示去看原链接。
    """
    from config import config
    from .media import download_as_base64
    from .sources.pixiv_illust import fetch_page_urls, image_headers

    max_images = max(1, config.pixiv_max_images)
    max_side = config.pixiv_max_image_side

    nodes: list[dict] = []
    for item in items:
        content_parts: list = [MessageSegment.text(f"{item.title}\n")]

        urls = await fetch_page_urls(item.id, item.target_id)
        take = urls[:max_images]

        images_added = 0
        for url in take:
            b64 = await download_as_base64(
                url, image_headers(), max_side=max_side
            )
            if b64:
                content_parts.append(MessageSegment.image(b64))
                images_added += 1

        if not images_added:
            nb_logger.warning(f"Pixiv 作品图片全部下载失败 [{item.id}]，只发文字")

        page_count = int(item.extra.get("page_count") or len(urls) or 1)
        # 紧跟图片的文本不能带前导 \n，否则图片后会多出空行；
        # 紧跟文字的文本需要 \n\n 才能空出一行
        last_is_image = images_added > 0
        if page_count > max_images:
            content_parts.append(MessageSegment.text(PIXIV_MORE_HINT))
            last_is_image = False

        prefix = "\n" if last_is_image else "\n\n"
        content_parts.append(MessageSegment.text(f"{prefix}链接：{item.link}"))

        nodes.append({
            "type": "node",
            "data": {
                "name": item.nickname or "Pixiv",
                "uin": "0",
                "content": content_parts,
            },
        })

    return nodes


async def send_pixiv_forward(bot: Bot, group_id: int, items: list[Item]):
    """发送 Pixiv 作品更新（合并转发 + 卡片外观参数）"""
    if not items:
        return

    nodes = await build_pixiv_forward_msg(items)
    if not nodes:
        return
    for node in nodes:
        node["data"]["uin"] = str(bot.self_id)

    try:
        await bot.call_api(
            "send_group_forward_msg",
            group_id=group_id,
            messages=nodes,
            **build_pixiv_preview(items),
        )
    except Exception as e:
        nb_logger.warning(f"Pixiv 合并转发失败，回退默认样式: {e!r}")
        await bot.call_api(
            "send_group_forward_msg",
            group_id=group_id,
            messages=nodes,
        )

    # 记录到短期记忆
    try:
        from plugins.llm_chat.memory import memory
        titles = "、".join(it.title[:30] for it in items)
        memory.add_assistant_message(group_id, f"[Kei推送了Pixiv: {titles}]")
    except Exception:
        pass
