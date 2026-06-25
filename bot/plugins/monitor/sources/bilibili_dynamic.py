"""B站动态监测 - 直连 B站 API（仅原创动态 & 视频投稿）"""

import re
from typing import Optional

from httpx import AsyncClient

from config import config
from utils.wbi import sign_params
from .base import Item, SourceBase

# ─── B站 API 端点 ──────────────────────────────────────────
BILIBILI_DYNAMIC_API = (
    "https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space"
    "?host_mid={uid}&features=itemOpusStyle,opusCard&offset="
)


def _make_headers() -> dict:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": "https://space.bilibili.com/",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Origin": "https://space.bilibili.com",
    }
    if config.bilibili_cookie:
        headers["Cookie"] = config.bilibili_cookie
    return headers


class BilibiliDynamic(SourceBase):
    """B站动态监测源 — 仅推送原创内容"""

    _WANTED_TYPES = frozenset({
        "DYNAMIC_TYPE_DRAW",   # 图文 / 纯文字 / 转发（OPUS 统一归类）
        "DYNAMIC_TYPE_AV",     # 视频投稿
    })

    @property
    def platform(self) -> str:
        return "bilibili"

    @property
    def source_type(self) -> str:
        return "dynamic"

    # ─── 主入口 ──────────────────────────────────────────

    async def fetch(self) -> list[Item]:
        url = BILIBILI_DYNAMIC_API.format(uid=self.target_id)
        try:
            async with AsyncClient(timeout=15) as client:
                resp = await client.get(url, headers=_make_headers())
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            return []

        if data.get("code") != 0:
            return []

        items_list = data.get("data", {}).get("items", [])
        return [
            item
            for dyn in items_list
            if (item := self._parse_dynamic(dyn)) is not None
        ]

    # ─── 解析 ────────────────────────────────────────────

    def _parse_dynamic(self, dyn: dict) -> Optional[Item]:
        modules = dyn.get("modules", {})
        desc = modules.get("module_dynamic", {}).get("desc") or {}
        author = modules.get("module_author", {})
        major = modules.get("module_dynamic", {}).get("major") or {}

        # type：新版在顶层，旧版在 desc 中
        dyn_type = dyn.get("type") or desc.get("type", "")
        if dyn_type not in self._WANTED_TYPES:
            return None

        dyn_id = dyn.get("id_str", str(dyn.get("id", "")))
        nickname = author.get("name", "")
        # 发布时间戳（用于过滤重启前的旧动态）
        pub_ts = float(author.get("pub_ts", 0))

        # ── 文字：优先 OPUS，其次 desc ──
        opus = major.get("opus") or {}
        opus_title = opus.get("title", "")
        opus_text = (opus.get("summary") or {}).get("text", "")

        if opus_title or opus_text:
            # OPUS 格式：标题 + 正文
            parts = []
            if opus_title:
                parts.append(opus_title)
            if opus_text and opus_text != opus_title:
                parts.append(opus_text)
            raw_text = "\n".join(parts)
        else:
            # 旧格式：desc.text
            raw_text = desc.get("text", "") if desc else ""

        clean_content = re.sub(r'<[^>]+>', '', raw_text).strip()

        # 标题取前 50 字
        title = clean_content[:50] if clean_content else f"{nickname}的动态"

        # ── 图片：OPUS 用 pics，旧版用 draw ──
        cover_url, cover_urls = self._extract_images(opus, major, dyn_type)

        # ── 视频投稿：提取 AV/BV 信息 ──
        extra = {}
        item_link = f"https://t.bilibili.com/{dyn_id}"
        if dyn_type == "DYNAMIC_TYPE_AV":
            archive = major.get("archive") or {}
            bvid = archive.get("bvid", "")
            aid = archive.get("aid", "")
            if bvid:
                item_link = f"https://www.bilibili.com/video/{bvid}"
            elif aid:
                item_link = f"https://www.bilibili.com/video/av{aid}"
            extra["is_video"] = True
            extra["video_title"] = archive.get("title", "")  # 视频标题
            extra["video_desc"] = re.sub(r'<[^>]+>', '', archive.get("desc", "")).strip()  # 视频简介

        return Item(
            id=dyn_id,
            platform=self.platform,
            source_type=self.source_type,
            target_id=self.target_id,
            title=title,
            nickname=nickname,
            content=clean_content,
            link=item_link,
            cover_url=cover_url,
            cover_urls=cover_urls,
            pub_ts=pub_ts,
            extra=extra,
        )

    # ─── 图片提取 ─────────────────────────────────────────

    @staticmethod
    def _extract_images(opus: dict, major: dict,
                        dyn_type: str) -> tuple[Optional[str], list[str]]:
        """提取图片：OPUS.pics > major.draw > major.archive"""
        # OPUS 格式
        pics = opus.get("pics") or []
        if pics:
            urls = [p.get("url", "") for p in pics if p.get("url")]
            return (urls[0] if urls else None), urls

        # major.draw（新旧通用）
        major_type = major.get("type", "") or dyn_type
        if major_type in ("MAJOR_TYPE_DRAW", "DYNAMIC_TYPE_DRAW"):
            items_list = (major.get("draw") or {}).get("items", [])
            urls = [d.get("src") for d in items_list if d.get("src")]
            return (urls[0] if urls else None), urls

        # major.archive（视频）
        if major_type in ("MAJOR_TYPE_ARCHIVE", "DYNAMIC_TYPE_AV"):
            archive = major.get("archive") or {}
            cover = archive.get("cover")
            return cover, [cover] if cover else []

        return None, []

    # ─── 用户昵称 ─────────────────────────────────────────

    async def get_display_name(self) -> str:
        try:
            params = await sign_params({"mid": self.target_id})
            async with AsyncClient(timeout=10) as client:
                resp = await client.get(
                    "https://api.bilibili.com/x/space/wbi/acc/info",
                    params=params, headers=_make_headers(),
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("code") == 0:
                        return data["data"].get("name", self.target_id)
        except Exception:
            pass
        return self.target_id
