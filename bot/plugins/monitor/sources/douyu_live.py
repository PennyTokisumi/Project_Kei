"""斗鱼直播监测 - 直连斗鱼公开 API"""

from httpx import AsyncClient

from .base import Item, SourceBase

DOUYU_API_URL = "https://open.douyucdn.cn/api/RoomApi/room/{room_id}"


class DouyuLive(SourceBase):
    """斗鱼直播监测源"""

    @property
    def platform(self) -> str:
        return "douyu"

    @property
    def source_type(self) -> str:
        return "live"

    async def fetch(self) -> list[Item]:
        """拉取斗鱼直播间状态

        返回当前直播状态（如果有），由调用方做 off→on 检测。
        """
        url = DOUYU_API_URL.format(room_id=self.target_id)
        try:
            async with AsyncClient(timeout=10) as client:
                resp = await client.get(
                    url,
                    headers={"User-Agent": "QQ_Monitor_Bot/1.0"},
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            return []

        if data.get("error") != 0:
            return []

        room = data.get("data", {})

        # room_status: "1" = 直播中, "2" = 未开播
        if room.get("room_status") != "1":
            return []

        item = Item(
            id=f"live_{self.target_id}",
            platform=self.platform,
            source_type=self.source_type,
            target_id=self.target_id,
            title=room.get("room_name", ""),
            nickname=room.get("owner_name", ""),
            content=room.get("room_name", ""),
            link=f"https://www.douyu.com/{self.target_id}",
            cover_url=room.get("room_thumb"),
            extra={
                "game_name": room.get("game_name", ""),
            },
        )
        return [item]

    async def get_display_name(self) -> str:
        """获取主播名"""
        try:
            async with AsyncClient(timeout=10) as client:
                resp = await client.get(
                    DOUYU_API_URL.format(room_id=self.target_id),
                    headers={"User-Agent": "QQ_Monitor_Bot/1.0"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("error") == 0:
                        return data["data"].get("owner_name", self.target_id)
        except Exception:
            pass
        return self.target_id
