"""B站直播监测 - 通过 RSSHub 获取直播间状态"""

import feedparser
from typing import Optional

from httpx import AsyncClient

from config import config
from .base import Item, SourceBase


class BilibiliLive(SourceBase):
    """B站直播监测源"""

    @property
    def platform(self) -> str:
        return "bilibili"

    @property
    def source_type(self) -> str:
        return "live"

    async def _fetch_feed(self) -> Optional[bytes]:
        """从 RSSHub 获取直播间 RSS"""
        url = f"{config.rsshub_base_url}/bilibili/live/room/{self.target_id}"
        async with AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers={"User-Agent": "QQ_Monitor_Bot/1.0"})
            resp.raise_for_status()
            return resp.content

    async def fetch(self) -> list[Item]:
        """拉取 B站直播间当前状态

        当主播开播时 RSSHub 会有新的 item，否则 feed 为空或只有旧 item。
        注意这里返回的是当前直播状态（如果有），由调用方做 off→on 检测。
        """
        try:
            content = await self._fetch_feed()
            if not content:
                return []
        except Exception:
            return []

        feed = feedparser.parse(content)
        if not feed.entries:
            return []

        latest = feed.entries[0]
        title = latest.get("title", "")

        # 判断是否在直播中（RSSHub 标题格式通常是 "【正在直播】..."）
        is_living = title.startswith("【") or "直播" in title

        if not is_living:
            return []

        # 提取直播间信息
        link = latest.get("link", "") or f"https://live.bilibili.com/{self.target_id}"

        # 提取封面图（从 summary 中的 img 标签）
        cover_url = None
        summary = latest.get("summary", "")
        if summary:
            import re
            m = re.search(r'<img[^>]+src="([^"]+)"', summary)
            if m:
                cover_url = m.group(1)

        # 标题清理
        clean_title = title.replace("【正在直播】", "").strip()

        # 取昵称
        nickname = ""
        if " " in clean_title:
            parts = clean_title.split(" ", 1)
            nickname = parts[0].strip()
            clean_title = parts[1].strip() if len(parts) > 1 else clean_title
        else:
            nickname = feed.feed.get("title", "")

        item = Item(
            id=f"live_{latest.get('id', self.target_id)}",
            platform=self.platform,
            source_type=self.source_type,
            target_id=self.target_id,
            title=clean_title or title,
            nickname=nickname,
            content=clean_title or title,
            link=link,
            cover_url=cover_url,
        )
        return [item]

    async def get_display_name(self) -> str:
        """尝试获取主播名"""
        try:
            items = await self.fetch()
            if items:
                return items[0].nickname or items[0].title
        except Exception:
            pass
        return self.target_id
