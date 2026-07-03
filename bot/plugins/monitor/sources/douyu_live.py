"""斗鱼直播监测 - 官方 API 为主，第三方镜像为备用"""

from httpx import AsyncClient

from .base import Item, SourceBase

# 官方 API（推荐）
DOUYU_API_OFFICIAL = "https://www.douyu.com/betard/{room_id}"

# 第三方镜像（备用）
DOUYU_API_FALLBACK = "https://open.douyucdn.cn/api/RoomApi/room/{room_id}"

def _fix_cover(url: str) -> str:
    """验证并补全封面 URL，处理协议相对路径"""
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("http"):
        return url
    return ""

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.douyu.com/",
}


class DouyuLive(SourceBase):
    """斗鱼直播监测源

    优先使用斗鱼官方 betard 接口，失败时回退到第三方镜像。
    """

    @property
    def platform(self) -> str:
        return "douyu"

    @property
    def source_type(self) -> str:
        return "live"

    # ─── 主入口 ──────────────────────────────────────────

    async def fetch(self) -> list[Item]:
        """拉取斗鱼直播间状态，默认使用官方 API"""
        item = await self._fetch_official()
        if item is None:
            item = await self._fetch_fallback()
        return [item] if item else []

    # ─── 官方 API ────────────────────────────────────────

    async def _fetch_official(self) -> Item | None:
        """斗鱼官方 betard 接口"""
        url = DOUYU_API_OFFICIAL.format(room_id=self.target_id)
        try:
            async with AsyncClient(timeout=10) as client:
                resp = await client.get(url, headers=HEADERS)
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            return None

        room = data.get("room", {})
        if not room:
            return None

        # show_status: 1=直播中, 2=未开播
        if room.get("show_status") != 1:
            return None

        room_id = str(room.get("room_id", self.target_id))
        # room_src 是相对路径，用 coverSrc / room_pic 才是完整 URL
        cover = room.get("coverSrc") or room.get("room_pic") or ""
        return Item(
            id=f"live_{room_id}",
            platform=self.platform,
            source_type=self.source_type,
            target_id=self.target_id,
            title=room.get("room_name", ""),
            nickname=room.get("owner_name", ""),
            content=room.get("room_name", ""),
            link=f"https://www.douyu.com/{room_id}",
            cover_url=_fix_cover(cover),
            extra={
                "game_name": room.get("second_lvl_name", ""),
            },
        )

    # ─── 第三方镜像（备用）────────────────────────────────

    async def _fetch_fallback(self) -> Item | None:
        """第三方 open.douyucdn.cn 接口"""
        url = DOUYU_API_FALLBACK.format(room_id=self.target_id)
        try:
            async with AsyncClient(timeout=10) as client:
                resp = await client.get(
                    url,
                    headers={"User-Agent": "Project_Kei/1.0"},
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            return None

        if data.get("error") != 0:
            return None

        room = data.get("data", {})

        # room_status: "1"=直播中, "2"=未开播
        if room.get("room_status") != "1":
            return None

        cover = room.get("room_thumb", "")
        return Item(
            id=f"live_{self.target_id}",
            platform=self.platform,
            source_type=self.source_type,
            target_id=self.target_id,
            title=room.get("room_name", ""),
            nickname=room.get("owner_name", ""),
            content=room.get("room_name", ""),
            link=f"https://www.douyu.com/{self.target_id}",
            cover_url=_fix_cover(cover),
            extra={
                "game_name": room.get("game_name", ""),
            },
        )

    # ─── 显示名 ──────────────────────────────────────────

    async def get_display_name(self) -> str:
        """获取主播名（官方 API 优先）"""
        # 尝试官方
        try:
            url = DOUYU_API_OFFICIAL.format(room_id=self.target_id)
            async with AsyncClient(timeout=10) as client:
                resp = await client.get(url, headers=HEADERS)
                if resp.status_code == 200:
                    data = resp.json()
                    name = data.get("room", {}).get("owner_name", "")
                    if name:
                        return name
        except Exception:
            pass

        # 回退
        try:
            url = DOUYU_API_FALLBACK.format(room_id=self.target_id)
            async with AsyncClient(timeout=10) as client:
                resp = await client.get(
                    url,
                    headers={"User-Agent": "Project_Kei/1.0"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("error") == 0:
                        return data["data"].get("owner_name", self.target_id)
        except Exception:
            pass

        return self.target_id
