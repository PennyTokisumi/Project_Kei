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


def _extract_vip_id(html: str) -> str | None:
    """从房间页面 HTML 中提取 vipId（自定义短 URL）

    斗鱼允许主播设置自定义短 URL（如 douyu.com/6657）。
    当访问真实房间号页面时，从 og:url / canonical 元数据反向查找。
    """
    for pattern in [
        r'property="og:url"\s+content="[^"]*douyu\.com/(\d+)"',
        r'rel="canonical"\s+href="[^"]*douyu\.com/(\d+)"',
    ]:
        m = re.search(pattern, html)
        if m and m.group(1):
            return m.group(1)
    return None


class DouyuLive(SourceBase):
    """斗鱼直播监测源

    优先使用斗鱼官方 betard 接口。部分房间使用自定义 URL（vipId），
    此时会自动双向解析（vipId↔真实 room_id）。最后回退到第三方镜像。
    """

    _real_room_id: str | None = None
    _display_id: str | None = None  # vipId（用于链接展示），懒加载

    @property
    def platform(self) -> str:
        return "douyu"

    @property
    def source_type(self) -> str:
        return "live"

    @property
    def display_id(self) -> str:
        """对外展示的房间号（优先 vipId）"""
        return self._display_id or self.target_id

    # ─── 主入口 ──────────────────────────────────────────

    async def fetch(self) -> list[Item]:
        """拉取斗鱼直播间状态"""
        self._api_responded = False
        item = await self._fetch_official()
        if item is None and not self._api_responded:
            # 官方 API 无响应才回退到第三方
            item = await self._fetch_fallback()
        # 成功获取后，尝试反向解析 vipId（如目标配置为真实房间号）
        if item is not None and self._display_id is None and self._real_room_id is None:
            await self._resolve_real_room_id()
        return [item] if item else []

    # ─── 房间 ID 解析 ────────────────────────────────────

    async def _resolve_real_room_id(self) -> str | None:
        """从斗鱼网页提取真实 room_id，同时尝试反向查找 vipId

        正向（vipId→real）：目标 ID 是自定义短 URL，页面含真实 room_id
        反向（real→vipId）：目标 ID 是真实 room_id，从 og:url 反向提取
        """
        url = f"https://www.douyu.com/{self.target_id}"
        try:
            async with AsyncClient(timeout=10, follow_redirects=True) as client:
                resp = await client.get(url, headers=HEADERS)
                html = resp.text
        except Exception:
            return None

        m = re.search(r'room_id\D+(\d{5,})', html)
        if not m:
            return None

        real_id = m.group(1)

        if real_id != str(self.target_id):
            # 正向：target_id 是 vipId
            self._display_id = self.target_id
            return real_id
        else:
            # 反向：target_id 是真实 room_id，尝试从页面 og:url 找 vipId
            vip = _extract_vip_id(html)
            if vip and vip != self.target_id:
                self._display_id = vip
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
                        # 非 vipId 且 JSON 解析失败 → 放弃
                        self._api_responded = True
                    nb_logger.debug(
                        f"斗鱼 betard API 返回非 JSON (房间 {rid})"
                    )
                    return None
                self._api_responded = True
        except Exception:
            self._api_responded = True
            return None

        room = data.get("room", {})
        if not room:
            return None

        # show_status: 1=直播中, 2=未开播
        if room.get("show_status") != 1:
            return None

        # rst=3 表示自动轮播/录像重播，不是真直播
        if room.get("rst", 0) != 0:
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
            link=f"https://www.douyu.com/{self.display_id}",
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
            link=f"https://www.douyu.com/{self.display_id}",
            cover_url=_fix_cover(cover),
            extra={
                "game_name": room.get("game_name", ""),
            },
        )

    # ─── 显示名 ──────────────────────────────────────────

    async def get_display_name(self) -> str:
        """获取主播名（官方 API 优先）。首次调用时解析真实 room_id。"""
        # 确保 real_room_id 已解析（vipId 需要转为真实 ID 才能调 API）
        if self._real_room_id is None:
            await self._resolve_real_room_id()

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
                    elif self._real_room_id is None:
                        # 返回非 JSON 且无 real_room_id → 可能是 vipId 解析失败
                        # 不回退，避免 fallback 接受 vipId 返回垃圾数据
                        nb_logger.debug(
                            f"斗鱼房间 {self.target_id} betard 返回非 JSON"
                            "（可能为 vipId），跳过 API 获取名称"
                        )
                        return self.display_id
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

        return self.display_id
