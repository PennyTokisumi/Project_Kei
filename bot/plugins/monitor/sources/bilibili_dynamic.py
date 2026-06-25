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


def _to_https(url: str) -> str:
    if url.startswith("http://"):
        return "https://" + url[7:]
    return url


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
        "DYNAMIC_TYPE_DRAW",     # 图文 / 纯文字（OPUS）
        "DYNAMIC_TYPE_AV",       # 视频投稿
        "DYNAMIC_TYPE_ARTICLE",  # 文章 / 专栏
        "DYNAMIC_TYPE_FORWARD",  # 转发动态
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

        # ── 转发动态：合并原文文字和图片 ──
        if dyn_type == "DYNAMIC_TYPE_FORWARD":
            fwd_text, fwd_urls = self._extract_forward_full(dyn)
            if fwd_text or fwd_urls:
                prefix = f"{nickname}：{clean_content}\n" if clean_content else ""
                clean_content = f"{prefix}----------\n{fwd_text}" if fwd_text else prefix.rstrip()
                cover_urls = cover_urls + fwd_urls
                if not cover_url and fwd_urls:
                    cover_url = fwd_urls[0]

        # ── 视频/专栏：提取专属信息 ──
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
            extra["video_title"] = archive.get("title", "")
            extra["video_desc"] = re.sub(r'<[^>]+>', '', archive.get("desc", "")).strip()
        elif dyn_type == "DYNAMIC_TYPE_ARTICLE":
            extra["is_article"] = True
            extra["article_title"] = opus_title or title
            # 专栏只推标题和封面，不推正文
            clean_content = ""

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
            urls = [_to_https(p.get("url", "")) for p in pics if p.get("url")]
            return (urls[0] if urls else None), urls

        # major.draw（新旧通用）
        major_type = major.get("type", "") or dyn_type
        if major_type in ("MAJOR_TYPE_DRAW", "DYNAMIC_TYPE_DRAW"):
            items_list = (major.get("draw") or {}).get("items", [])
            urls = [_to_https(d.get("src", "")) for d in items_list if d.get("src")]
            return (urls[0] if urls else None), urls

        # major.archive（视频）
        if major_type in ("MAJOR_TYPE_ARCHIVE", "DYNAMIC_TYPE_AV"):
            archive = major.get("archive") or {}
            cover = _to_https(archive.get("cover", ""))
            return cover, [cover] if cover else []

        # major.article（文章/专栏）
        if major_type in ("MAJOR_TYPE_ARTICLE", "DYNAMIC_TYPE_ARTICLE"):
            article = major.get("article") or {}
            covers = article.get("covers") or []
            urls = [_to_https(c) for c in covers if c]
            return (urls[0] if urls else None), urls

        return None, []

    # ─── 转发原文提取 ──────────────────────────────────────

    @staticmethod
    def _extract_forward_full(dyn: dict) -> tuple[str, list[str]]:
        """提取转发动态的原文：返回 (文字, 图片URL列表)"""
        try:
            orig = dyn.get("orig")
            if not orig:
                return "", []
            orig_modules = orig.get("modules", {})
            orig_author = orig_modules.get("module_author", {})
            orig_nick = orig_author.get("name", "")
            orig_md = orig_modules.get("module_dynamic", {})
            orig_desc = orig_md.get("desc") or {}
            orig_major = orig_md.get("major") or {}
            orig_opus = orig_major.get("opus") or {}
            orig_type = orig.get("type", "")

            # ── 文字（专栏只取标题，其他类型取全文）──
            if orig_type == "DYNAMIC_TYPE_ARTICLE":
                art_title = orig_opus.get("title", "") or orig_desc.get("text", "")
                text = f"{orig_nick}：{art_title}" if orig_nick else art_title
            else:
                if orig_opus:
                    parts = []
                    t = orig_opus.get("title", "")
                    s = (orig_opus.get("summary") or {}).get("text", "")
                    if t:
                        parts.append(t)
                    if s and s != t:
                        parts.append(s)
                    text = "\n".join(parts)
                else:
                    text = orig_desc.get("text", "")
                text = re.sub(r'<[^>]+>', '', text).strip()

                # 视频转发：加视频标题
                if orig_type == "DYNAMIC_TYPE_AV":
                    archive = orig_major.get("archive") or {}
                    vid_title = archive.get("title", "")
                    if vid_title:
                        text = f"[视频] {vid_title}\n{text}" if text else f"[视频] {vid_title}"

                if orig_nick:
                    text = f"@{orig_nick}：\n{text}" if text else f"@{orig_nick}"

            # ── 图片 ──
            urls: list[str] = []
            pics = orig_opus.get("pics") or []
            urls.extend(_to_https(p.get("url", "")) for p in pics if p.get("url"))
            major_type = orig_major.get("type", "") or orig_type
            if major_type in ("MAJOR_TYPE_DRAW", "DYNAMIC_TYPE_DRAW"):
                items_list = (orig_major.get("draw") or {}).get("items", [])
                urls.extend(_to_https(d.get("src", "")) for d in items_list if d.get("src"))
            if major_type in ("MAJOR_TYPE_ARCHIVE", "DYNAMIC_TYPE_AV"):
                archive = orig_major.get("archive") or {}
                cover = _to_https(archive.get("cover", ""))
                if cover:
                    urls.append(cover)
            if major_type in ("MAJOR_TYPE_ARTICLE", "DYNAMIC_TYPE_ARTICLE"):
                article = orig_major.get("article") or {}
                for c in (article.get("covers") or []):
                    if c:
                        urls.append(_to_https(c))

            return text[:500], urls
        except Exception:
            return "", []

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
