"""消息格式化 - B站动态用合并转发，开播用普通图文消息"""

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
    ]
    # 游戏/分区（斗鱼 game_name / B站 area_name）
    category = item.extra.get("game_name") or item.extra.get("area_name", "")
    if category:
        parts.append(f"分类：{category}")
    parts.append(f"链接：{item.link}")

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

        # 动态正文
        if item.content:
            content_parts.append(MessageSegment.text(item.content + "\n"))

        # 图片（优先多图，否则单图）
        if item.cover_urls:
            for url in item.cover_urls:
                content_parts.append(MessageSegment.image(url))
        elif item.cover_url:
            content_parts.append(MessageSegment.image(item.cover_url))

        # 来源 + 原文链接
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


async def send_live_notification(bot: Bot, group_id: int, item: Item):
    """发送直播开播提醒"""
    msg = build_live_message(item)
    await bot.send_group_msg(group_id=group_id, message=msg)


async def send_dynamic_forward(bot: Bot, group_id: int,
                                nickname: str, items: list[Item]):
    """发送B站动态合并转发（即使只有1条）"""
    if not items:
        return

    nodes = build_dynamic_forward_msg(items)
    await bot.call_api(
        "send_group_forward_msg",
        group_id=group_id,
        messages=nodes,
    )
