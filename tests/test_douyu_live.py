"""斗鱼直播源测试 — 官方 API / 备用回退"""

import pytest

from plugins.monitor.sources.douyu_live import DouyuLive


class TestDouyuFetch:
    """斗鱼 fetch 双 API 测试"""

    @pytest.mark.asyncio
    async def test_fetch_official_success(self, httpx_mock):
        """官方 betard API 正常返回（直播中）"""
        from plugins.monitor.sources.douyu_live import DOUYU_API_OFFICIAL

        url = DOUYU_API_OFFICIAL.format(room_id="617916")
        httpx_mock.add_response(
            url=url,
            json={
                "room": {
                    "room_id": 617916,
                    "room_name": "精彩直播",
                    "owner_name": "大主播",
                    "show_status": 1,
                    "coverSrc": "https://rpic.douyucdn.cn/cover.jpg",
                    "second_lvl_name": "英雄联盟",
                },
            },
            status_code=200,
        )
        # mock 房间页面（fetch 成功后触发 vipId 反向解析）
        httpx_mock.add_response(
            url="https://www.douyu.com/617916",
            text='<html><head><meta property="og:url" content="https://www.douyu.com/617916"></head>'
                 '<body>window.__NUXT__={"room_id":617916}</body></html>',
            status_code=200,
        )

        source = DouyuLive("617916", 123456)
        items = await source.fetch()

        assert len(items) == 1
        item = items[0]
        assert item.title == "精彩直播"
        assert item.nickname == "大主播"
        assert item.cover_url == "https://rpic.douyucdn.cn/cover.jpg"
        assert item.extra["game_name"] == "英雄联盟"
        assert "douyu.com/617916" in item.link

    @pytest.mark.asyncio
    async def test_fetch_official_offline(self, httpx_mock):
        """官方 API 返回未开播状态 → 空列表（不走回退）"""
        from plugins.monitor.sources.douyu_live import DOUYU_API_OFFICIAL

        # 官方：未开播
        httpx_mock.add_response(
            url=DOUYU_API_OFFICIAL.format(room_id="617916"),
            json={"room": {"room_id": 617916, "show_status": 2}},
            status_code=200,
        )

        source = DouyuLive("617916", 123456)
        items = await source.fetch()
        assert items == []

    @pytest.mark.asyncio
    async def test_fetch_fallback_on_official_fail(self, httpx_mock):
        """官方 API 网络故障 → 不回退，返回空（兜底不走fallback）"""
        from plugins.monitor.sources.douyu_live import DOUYU_API_OFFICIAL

        httpx_mock.add_exception(
            url=DOUYU_API_OFFICIAL.format(room_id="617916"),
            exception=Exception("连接超时"),
        )

        source = DouyuLive("617916", 123456)
        items = await source.fetch()
        assert items == []

    @pytest.mark.asyncio
    async def test_fetch_both_fail(self, httpx_mock):
        """两个 API 都失败 → 空列表"""
        from plugins.monitor.sources.douyu_live import (
            DOUYU_API_OFFICIAL,
            DOUYU_API_FALLBACK,
        )

        httpx_mock.add_exception(
            url=DOUYU_API_OFFICIAL.format(room_id="617916"),
            exception=Exception("官方挂了"),
        )
        httpx_mock.add_exception(
            url=DOUYU_API_FALLBACK.format(room_id="617916"),
            exception=Exception("备用也挂了"),
        )

        source = DouyuLive("617916", 123456)
        items = await source.fetch()
        assert items == []

    @pytest.mark.asyncio
    async def test_fetch_fallback_offline(self, httpx_mock):
        """官方 API 故障 → 不回退，返回空"""
        from plugins.monitor.sources.douyu_live import DOUYU_API_OFFICIAL

        httpx_mock.add_exception(
            url=DOUYU_API_OFFICIAL.format(room_id="617916"),
            exception=Exception("超时"),
        )

        source = DouyuLive("617916", 123456)
        items = await source.fetch()
        assert items == []


class TestDouyuDisplayName:
    """获取主播名"""

    @pytest.mark.asyncio
    async def test_get_display_name_official(self, httpx_mock):
        """通过官方 API 获取"""
        from plugins.monitor.sources.douyu_live import DOUYU_API_OFFICIAL

        httpx_mock.add_response(
            url=DOUYU_API_OFFICIAL.format(room_id="617916"),
            json={
                "room": {
                    "owner_name": "大主播",
                },
            },
            status_code=200,
        )

        source = DouyuLive("617916", 123456)
        name = await source.get_display_name()
        assert name == "大主播"

    @pytest.mark.asyncio
    async def test_get_display_name_fallback(self, httpx_mock):
        """官方失败，回退到备用 API"""
        from plugins.monitor.sources.douyu_live import (
            DOUYU_API_OFFICIAL,
            DOUYU_API_FALLBACK,
        )

        httpx_mock.add_exception(
            url=DOUYU_API_OFFICIAL.format(room_id="617916"),
            exception=Exception("超时"),
        )
        httpx_mock.add_response(
            url=DOUYU_API_FALLBACK.format(room_id="617916"),
            json={
                "error": 0,
                "data": {"owner_name": "备用名字"},
            },
            status_code=200,
        )

        source = DouyuLive("617916", 123456)
        name = await source.get_display_name()
        assert name == "备用名字"
