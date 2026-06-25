"""B站动态源测试 — 解析逻辑 & API 调用"""

import re

import pytest
from httpx import Response

from plugins.monitor.sources.bilibili_dynamic import BilibiliDynamic, BILIBILI_DYNAMIC_API


class TestDynamicParsing:
    """动态解析逻辑（不涉及网络）"""

    def test_parse_draw_single_image(self, sample_dynamic_draw):
        """图文动态：单张图片"""
        source = BilibiliDynamic("436742", 123456)
        item = source._parse_dynamic(sample_dynamic_draw)

        assert item is not None
        assert item.id == "123456789"
        assert item.nickname == "测试用户"
        assert "分享一张好看的图" in item.content
        assert item.link == "https://t.bilibili.com/123456789"
        assert item.cover_url == "https://i0.hdslb.com/bfs/archive/abc123.jpg"
        assert len(item.cover_urls) == 1

    def test_parse_draw_multi_image(self, sample_dynamic_draw_multi):
        """图文动态：多张图片"""
        source = BilibiliDynamic("12345", 123456)
        item = source._parse_dynamic(sample_dynamic_draw_multi)

        assert item is not None
        assert len(item.cover_urls) == 4
        assert item.cover_url == "https://i0.hdslb.com/bfs/archive/img1.jpg"
        assert item.cover_urls[3] == "https://i0.hdslb.com/bfs/archive/img4.jpg"

    def test_parse_av(self, sample_dynamic_av):
        """视频投稿动态"""
        source = BilibiliDynamic("111", 123456)
        item = source._parse_dynamic(sample_dynamic_av)

        assert item is not None
        assert "新视频来啦" in item.content
        assert item.cover_url == "https://i0.hdslb.com/bfs/archive/video_cover.jpg"
        assert len(item.cover_urls) == 1

    def test_parse_forward_filtered(self, sample_dynamic_forward):
        """转发动态应被过滤"""
        source = BilibiliDynamic("999", 123456)
        item = source._parse_dynamic(sample_dynamic_forward)
        assert item is None

    def test_parse_word_deprecated(self, sample_dynamic_word):
        """WORD 类型已废弃，新版 OPUS 统一为 DRAW"""
        source = BilibiliDynamic("555", 123456)
        item = source._parse_dynamic(sample_dynamic_word)
        assert item is None

    def test_content_kept_full(self):
        """正文保留完整，不截断"""
        long_text = "测" * 600
        dyn = {
            "id_str": "1",
            "type": "DYNAMIC_TYPE_DRAW",
            "modules": {
                "module_author": {"name": "长文"},
                "module_dynamic": {
                    "major": {"opus": {"title": long_text}},
                },
            },
        }
        source = BilibiliDynamic("1", 123456)
        item = source._parse_dynamic(dyn)
        assert item is not None
        assert item.content == long_text

    def test_html_tags_stripped(self):
        """HTML 标签应被清除"""
        dyn = {
            "id_str": "1",
            "type": "DYNAMIC_TYPE_DRAW",
            "modules": {
                "module_author": {"name": "HTML"},
                "module_dynamic": {
                    "desc": {
                        "text": '点<a href="https://example.com">这里</a>查看',
                    },
                },
            },
        }
        source = BilibiliDynamic("1", 123456)
        item = source._parse_dynamic(dyn)
        assert item is not None
        assert "<a href" not in item.content
        assert "这里" in item.content


class TestFetchAPI:
    """API 调用测试（使用 httpx mock）"""

    @pytest.mark.asyncio
    async def test_fetch_success(self, httpx_mock):
        """正常拉取动态"""
        mock_response = {
            "code": 0,
            "data": {
                "items": [
                    {
                        "id_str": "111",
                        "type": "DYNAMIC_TYPE_DRAW",
                        "modules": {
                            "module_author": {"name": "用户A"},
                            "module_dynamic": {
                                "major": {"opus": {"title": "hello"}},
                            },
                        },
                    },
                    {
                        "id_str": "222",
                        "modules": {
                            "module_author": {"name": "用户A"},
                            "module_dynamic": {
                                "desc": {"type": "DYNAMIC_TYPE_DRAW", "text": "图片动态"},
                                "major": {
                                    "draw": {
                                        "items": [
                                            {"src": "https://example.com/1.jpg"},
                                            {"src": "https://example.com/2.jpg"},
                                        ],
                                    },
                                },
                            },
                        },
                    },
                ],
            },
        }

        # BILIBILI_DYNAMIC_API 末尾已有 &offset=，不必再拼接
        url = BILIBILI_DYNAMIC_API.format(uid="436742")
        httpx_mock.add_response(url=url, json=mock_response, status_code=200)

        source = BilibiliDynamic("436742", 123456)
        items = await source.fetch()

        assert len(items) == 2
        assert items[0].id == "111"
        assert items[1].id == "222"
        assert len(items[1].cover_urls) == 2

    @pytest.mark.asyncio
    async def test_fetch_code_error(self, httpx_mock):
        """API 返回非 0 code"""
        url = BILIBILI_DYNAMIC_API.format(uid="436742")
        httpx_mock.add_response(
            url=url,
            json={"code": -412, "message": "访问被限流"},
            status_code=200,
        )

        source = BilibiliDynamic("436742", 123456)
        items = await source.fetch()
        assert items == []

    @pytest.mark.asyncio
    async def test_fetch_network_error(self, httpx_mock):
        """网络错误返回空列表"""
        url = BILIBILI_DYNAMIC_API.format(uid="436742")
        httpx_mock.add_exception(url=url, exception=Exception("连接超时"))

        source = BilibiliDynamic("436742", 123456)
        items = await source.fetch()
        assert items == []

    @pytest.mark.asyncio
    async def test_fetch_filters_forward(self, httpx_mock):
        """转发动态应被过滤，只保留原创"""
        url = BILIBILI_DYNAMIC_API.format(uid="436742")
        httpx_mock.add_response(
            url=url,
            json={
                "code": 0,
                "data": {
                    "items": [
                        {
                            "id_str": "333",
                            "modules": {
                                "module_author": {"name": "用户A"},
                                "module_dynamic": {
                                    "desc": {
                                        "type": "DYNAMIC_TYPE_FORWARD",
                                        "text": "转发一条",
                                    },
                                },
                            },
                        },
                    ],
                },
            },
            status_code=200,
        )

        source = BilibiliDynamic("436742", 123456)
        items = await source.fetch()
        assert items == []


class TestGetDisplayName:
    """获取用户显示名"""

    @pytest.mark.asyncio
    async def test_get_display_name_success(self, httpx_mock, monkeypatch):
        """通过 WBI 签名接口获取用户名"""
        # 固定签名参数，方便 URL 精确匹配
        async def mock_sign(params):
            params["wts"] = "1234567890"
            params["w_rid"] = "a" * 32
            return params

        monkeypatch.setattr(
            "plugins.monitor.sources.bilibili_dynamic.sign_params",
            mock_sign,
        )

        # 签名后 URL 带参数，需精确匹配
        httpx_mock.add_response(
            url="https://api.bilibili.com/x/space/wbi/acc/info"
                "?mid=436742&wts=1234567890&w_rid=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            json={"code": 0, "data": {"name": "测试UP主"}},
            status_code=200,
        )

        source = BilibiliDynamic("436742", 123456)
        name = await source.get_display_name()
        assert name == "测试UP主"

    @pytest.mark.asyncio
    async def test_get_display_name_fallback(self, httpx_mock, monkeypatch):
        """API 失败时返回 target_id"""
        async def mock_sign(params):
            params["wts"] = "1234567890"
            params["w_rid"] = "b" * 32
            return params

        monkeypatch.setattr(
            "plugins.monitor.sources.bilibili_dynamic.sign_params",
            mock_sign,
        )

        httpx_mock.add_exception(
            url="https://api.bilibili.com/x/space/wbi/acc/info"
                "?mid=436742&wts=1234567890&w_rid=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            exception=Exception("网络错误"),
        )

        source = BilibiliDynamic("436742", 123456)
        name = await source.get_display_name()
        assert name == "436742"
