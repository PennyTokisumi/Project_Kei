"""B站动态监测 - 直连 B站 API（原创 & 视频投稿）"""

import re
from typing import Optional

from httpx import AsyncClient

from .base import Item, SourceBase

BILIBILI_DYNAMIC_API = (
    "https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space"
    "?host_mid={uid}&offset="
)
BILIBILI_USER_API = "https://api.bilibili.com/x/space/wbi/acc/info?mid={uid}"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://space.bilibili.com/",
}


class BilibiliDynamic(SourceBase):
    """B站动态监测源"""

    @property
    def platform(self) -> str:
        return "bilibili"

    @property
    def source_type(self) -> str:
        return "dynamic"

    async def fetch(self) -> list[Item]:
        """拉取 B站用户最新动态（原创 & 视频投稿）

        过滤转发动态，只保留原创内容。
        """
        url = BILIBILI_DYNAMIC_API.format(uid=self.target_id)
        try:
            async with AsyncClient(timeout=15) as client:
                resp = await client.get(url, headers=HEADERS)
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            return []

        if data.get("code") != 0:
            return []

        items_list = data.get("data", {}).get("items", [])
        if not items_list:
            return []

        result: list[Item] = []
        for dyn in items_list:
            # 解析模块内容
            modules = dyn.get("modules", {})
            desc = modules.get("module_dynamic", {}).get("desc", {})
            author = modules.get("module_author", {})

            dyn_type = desc.get("type", "")

            # 只保留原创动态和视频投稿
            # DYNAMIC_TYPE_DRAW=2, DYNAMIC_TYPE_WORD=4, DYNAMIC_TYPE_AV=8
            if dyn_type not in ("DYNAMIC_TYPE_DRAW", "DYNAMIC_TYPE_WORD",
                                 "DYNAMIC_TYPE_AV"):
                continue

            dyn_id = dyn.get("id_str", str(dyn.get("id", "")))
            nickname = author.get("name", "")
            text = desc.get("text", "")
            link = f"https://t.bilibili.com/{dyn_id}"

            # 提取封面图
            cover_url = None
            if dyn_type == "DYNAMIC_TYPE_DRAW":
                major = modules.get("module_dynamic", {}).get("major")
                if major and major.get("draw"):
                    items_draw = major["draw"].get("items", [])
                    if items_draw:
                        cover_url = items_draw[0].get("src")

            # 清理文本
            clean_content = re.sub(r'<[^>]+>', '', text).strip()
            if len(clean_content) > 500:
                clean_content = clean_content[:500] + "..."

            result.append(Item(
                id=dyn_id,
                platform=self.platform,
                source_type=self.source_type,
                target_id=self.target_id,
                title=clean_content[:50] or f"{nickname}的动态",
                nickname=nickname,
                content=clean_content,
                link=link,
                cover_url=cover_url,
            ))

        return result

    async def get_display_name(self) -> str:
        """获取用户昵称"""
        try:
            async with AsyncClient(timeout=10) as client:
                resp = await client.get(
                    BILIBILI_USER_API.format(uid=self.target_id),
                    headers=HEADERS,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("code") == 0:
                        return data["data"].get("name", self.target_id)
        except Exception:
            pass
        return self.target_id
