"""B站动态监测 - 通过 RSSHub 获取用户动态（原创 & 视频投稿）"""

import feedparser
from typing import Optional

from httpx import AsyncClient

from config import config
from .base import Item, SourceBase


# B站动态类型 ID 映射
# https://github.com/SocialSisterYi/bilibili-API-collect
DYNAMIC_TYPE_ORIGINAL = {2, 4}       # 图片动态, 文字动态
DYNAMIC_TYPE_VIDEO = {8}             # 视频投稿
DYNAMIC_TYPE_REPOST = {1}            # 转发动态（需要过滤掉）


class BilibiliDynamic(SourceBase):
    """B站动态监测源"""

    @property
    def platform(self) -> str:
        return "bilibili"

    @property
    def source_type(self) -> str:
        return "dynamic"

    async def _fetch_feed(self) -> Optional[str]:
        """从 RSSHub 获取原始 RSS XML"""
        url = f"{config.rsshub_base_url}/bilibili/user/dynamic/{self.target_id}"
        async with AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers={"User-Agent": "QQ_Monitor_Bot/1.0"})
            resp.raise_for_status()
            return resp.text

    async def fetch(self) -> list[Item]:
        """拉取 B站用户原创动态和视频投稿

        过滤规则：只保留原创（文字/图片/视频），过滤转发动态
        """
        xml = await self._fetch_feed()
        if not xml:
            return []

        feed = feedparser.parse(xml)
        items: list[Item] = []

        for entry in feed.entries:
            # 解析动态类型（RSSHub 的 category 字段）
            categories = entry.get("category", [])
            if isinstance(categories, str):
                categories = [categories]

            # 判断动态类型
            dyn_type = None
            for cat in categories:
                try:
                    dyn_type = int(cat)
                except (ValueError, TypeError):
                    continue

            # 过滤：跳过转发动态（类型 1）
            if dyn_type == 1:
                continue

            # 只保留原创动态（2, 4）和视频投稿（8）
            if dyn_type not in DYNAMIC_TYPE_ORIGINAL | DYNAMIC_TYPE_VIDEO:
                continue

            # RSSHub 的 guid 格式: bilibili://user/dynamic/{dynamic_id}
            guid = entry.get("id", "")
            dynamic_id = guid.split("/")[-1] if "/" in guid else guid

            # 提取封面
            cover_url = None
            summary = entry.get("summary", "")
            if summary:
                # 尝试从 summary 中提取第一张图片
                import re
                img_match = re.search(r'<img[^>]+src="([^"]+)"', summary)
                if img_match:
                    cover_url = img_match.group(1)

            # 提取纯文本正文（去掉 HTML 标签）
            import re
            clean_content = re.sub(r'<[^>]+>', '', summary).strip()
            # 限制长度避免消息过长
            if len(clean_content) > 500:
                clean_content = clean_content[:500] + "..."

            item = Item(
                id=dynamic_id,
                platform=self.platform,
                source_type=self.source_type,
                target_id=self.target_id,
                title=entry.get("title", ""),
                nickname=feed.feed.get("title", "").replace("的动态", ""),
                content=clean_content or entry.get("title", ""),
                link=entry.get("link", ""),
                cover_url=cover_url,
                extra={"dynamic_type": dyn_type},
            )
            items.append(item)

        return items

    async def get_display_name(self) -> str:
        """获取用户昵称"""
        try:
            xml = await self._fetch_feed()
            if xml:
                feed = feedparser.parse(xml)
                title = feed.feed.get("title", "")
                # 格式通常是 "XXX 的动态"
                if "的动态" in title:
                    return title.replace("的动态", "").strip()
                return title
        except Exception:
            pass
        return self.target_id
