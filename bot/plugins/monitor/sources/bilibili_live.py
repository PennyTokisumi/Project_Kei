"""B站直播监测 - 直连 B站直播 API"""

from httpx import AsyncClient

from utils.bilibili import make_headers
from .base import Item, SourceBase

BILIBILI_LIVE_API = (
    "https://api.live.bilibili.com/room/v1/Room/get_info?room_id={room_id}"
)
BILIBILI_USER_API = "https://api.bilibili.com/x/web-interface/card?mid={uid}"


class BilibiliLive(SourceBase):
    """B站直播监测源"""

    @property
    def platform(self) -> str:
        return "bilibili"

    @property
    def source_type(self) -> str:
        return "live"

    async def _get_username(self, uid: int) -> str:
        """通过 UID 获取用户名"""
        try:
            async with AsyncClient(timeout=10) as client:
                resp = await client.get(
                    BILIBILI_USER_API.format(uid=uid),
                    headers=make_headers("https://www.bilibili.com/"),
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("code") == 0:
                        return data["data"]["card"].get("name", "")
        except Exception:
            pass
        return ""

    async def fetch(self) -> list[Item]:
        """拉取 B站直播间当前状态"""
        url = BILIBILI_LIVE_API.format(room_id=self.target_id)
        try:
            async with AsyncClient(timeout=10) as client:
                resp = await client.get(url, headers=make_headers())
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            return []

        if data.get("code") != 0:
            return []

        room = data.get("data", {})
        if not room:
            return []

        # live_status: 1=直播中, 0=未开播
        live_status = room.get("live_status", 0)
        if live_status != 1:
            return []

        # 获取主播名
        uid = room.get("uid", 0)
        nickname = await self._get_username(uid) if uid else ""

        item = Item(
            id=f"live_{room.get('room_id', self.target_id)}",
            platform=self.platform,
            source_type=self.source_type,
            target_id=self.target_id,
            title=room.get("title", ""),
            nickname=nickname or f"房间{self.target_id}",
            content=room.get("title", ""),
            link=f"https://live.bilibili.com/{self.target_id}",
            cover_url=room.get("user_cover"),
            extra={
                "area_name": room.get("area_name", ""),
            },
        )
        return [item]

    async def get_display_name(self) -> str:
        """获取主播名（独立请求，不依赖直播状态）"""
        try:
            # 先通过直播间 API 获取 UID，再查用户名
            url = BILIBILI_LIVE_API.format(room_id=self.target_id)
            async with AsyncClient(timeout=10) as client:
                resp = await client.get(url, headers=make_headers())
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("code") == 0:
                        uid = data["data"].get("uid", 0)
                        if uid:
                            name = await self._get_username(uid)
                            if name:
                                return name
        except Exception:
            pass
        return self.target_id
