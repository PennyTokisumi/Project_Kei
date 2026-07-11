"""斗鱼直播监测 - 官方 API 为主，第三方镜像为备用"""

import re

from httpx import AsyncClient
from nonebot import logger as nb_logger

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

    优先使用斗鱼官方 betard 接口。部分房间使用自定义 URL（vipId），
    此时会自动从网页解析真实 room_id 再重试。最后回退到第三方镜像。
    """

    _real_room_id: str | None = None

    @property
    def platform(self) -> str:
        return "douyu"

    @property
    def source_type(self) -> str:
        return "live"

    # ─── 主入口 ──────────────────────────────────────────

    async def fetch(self) -> list[Item]:
        """拉取斗鱼直播间状态"""
        self._api_responded = False
        item = await self._fetch_official()
        if item is None and not self._api_responded:
            # 官方 API 无响应才回退到第三方
            item = await self._fetch_fallback()
        return [item] if item else []

    # ─── 房间 ID 解析 ────────────────────────────────────

    async def _resolve_real_room_id(self) -> str | None:
        """从斗鱼网页提取真实 room_id（处理 vipId 自定义 URL）

        斗鱼允许主播设置自定义 URL（如 douyu.com/6657），但 betard
        API 只认数字 room_id。网页 SSR 数据中包含真实 room_id。
        """
        url = f"https://www.douyu.com/{self.target_id}"
        try:
            async with AsyncClient(timeout=10, follow_redirects=True) as client:
                resp = await client.get(url, headers=HEADERS)
                html = resp.text
            m = re.search(r'room_id\D+(\d{5,})', html)
            if m:
                rid = m.group(1)
                if rid != str(self.target_id):
                    return rid
        except Exception:
            pass
        return None

    # ─── 官方 API ────────────────────────────────────────

    async def _fetch_official(self) -> Item | None:
        """斗鱼官方 betard 接口"""
        rid = self._real_room_id or self.target_id
        url = DOUYU_API_OFFICIAL.format(room_id=rid)
        try:
            async with AsyncClient(timeout=10) as client:
                resp = await client.get(url, headers=HEADERS)
                resp.raise_for_status()
                try:
                    data = resp.json()
                except Exception:
                    # JSON 解析失败 → 可能是 vipId 自定义 URL 返回了 HTML
                    if rid == self.target_id and self._real_room_id is None:
                        real = await self._resolve_real_room_id()
                        if real and real != self.target_id:
                            self._real_room_id = real
                            nb_logger.info(
                                f"斗鱼房间 {self.target_id} → 真实 room_id: {real}"
                            )
                            return await self._fetch_official()
                    nb_logger.debug(
                        f"斗鱼 betard API 返回非 JSON (房间 {rid})"
                    )
                    return None
                self._api_responded = True
        except Exception:
            return None

        room = data.get("room", {})
        if not room:
            return None

        # show_status: 1=直播中, 2=未开播
        if room.get("show_status") != 1:
            return None

        # rst=3 表示自动轮播/录像重播，不是真直播
        if room.get("rst", 0) != 0:
            nb_logger.debug(
                f"斗鱼房间 {self.target_id} rst={room.get('rst')}，"
                f"疑似轮播，跳过推送"
            )
            return None

        room_id = str(room.get("room_id", rid))
        cover = room.get("coverSrc") or room.get("room_pic") or ""
        return Item(
            id=f"live_{room_id}",
            platform=self.platform,
            source_type=self.source_type,
            target_id=self.target_id,
            title=room.get("room_name", ""),
            nickname=room.get("owner_name", ""),
            content=room.get("room_name", ""),
            link=f"https://www.douyu.com/{self.target_id}",
            cover_url=_fix_cover(cover),
            extra={
                "game_name": room.get("second_lvl_name", ""),
            },
        )

    # ─── 第三方镜像（备用）────────────────────────────────

    async def _fetch_fallback(self) -> Item | None:
        """第三方 open.douyucdn.cn 接口"""
        # 用真实 room_id（如有），因为第三方 API 也不认识 vipId
        rid = self._real_room_id or self.target_id
        url = DOUYU_API_FALLBACK.format(room_id=rid)
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
        rid = self._real_room_id or self.target_id
        # 尝试官方
        try:
            url = DOUYU_API_OFFICIAL.format(room_id=rid)
            async with AsyncClient(timeout=10) as client:
                resp = await client.get(url, headers=HEADERS)
                if resp.status_code == 200:
                    ct = resp.headers.get("content-type", "")
                    if "application/json" in ct:
                        data = resp.json()
                        name = data.get("room", {}).get("owner_name", "")
                        if name:
                            return name
        except Exception:
            pass

        # 回退
        try:
            url = DOUYU_API_FALLBACK.format(room_id=rid)
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
