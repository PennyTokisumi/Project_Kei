"""消息格式化测试"""

import pytest
from unittest.mock import patch

from plugins.monitor.sources.base import Item


# ─── 构造 fake Message / MessageSegment 用于测试 ────────

class FakeMessageSegment:
    """可被 isinstance 检查，有 type 和 data 属性"""
    def __init__(self, seg_type: str, data: str = ""):
        self.type = seg_type
        self.data = data

    def __eq__(self, other):
        if isinstance(other, FakeMessageSegment):
            return self.type == other.type and self.data == other.data
        return False

    @staticmethod
    def text(text: str):
        return FakeMessageSegment("text", text)

    @staticmethod
    def image(url: str):
        return FakeMessageSegment("image", url)


class FakeMessage(list):
    """假的 Message，行为像 list"""
    def extract_plain_text(self) -> str:
        return "".join(
            seg.data for seg in self
            if isinstance(seg, FakeMessageSegment) and seg.type == "text"
        )


@pytest.fixture(autouse=True)
def _patch_formatter():
    """自动 patch formatter 的 Message / MessageSegment 引用"""
    with (
        patch("plugins.monitor.formatter.Message", FakeMessage),
        patch("plugins.monitor.formatter.MessageSegment", FakeMessageSegment),
    ):
        yield


class TestLiveMessage:
    """直播开播消息"""

    def test_basic_live_message(self):
        from plugins.monitor.formatter import build_live_message

        item = Item(
            id="live_123",
            platform="douyu",
            source_type="live",
            target_id="123",
            title="精彩赛事",
            nickname="大主播",
            content="精彩赛事",
            link="https://www.douyu.com/123",
            cover_url="https://example.com/cover.jpg",
        )
        msg = build_live_message(item)
        text = msg.extract_plain_text()
        assert "精彩赛事" in text
        assert "大主播" in text
        assert "douyu.com/123" in text

    def test_live_message_with_game(self):
        """斗鱼直播带游戏分类"""
        from plugins.monitor.formatter import build_live_message

        item = Item(
            id="live_456",
            platform="douyu",
            source_type="live",
            target_id="456",
            title="LOL排位",
            nickname="玩家",
            content="LOL排位",
            link="https://www.douyu.com/456",
            extra={"game_name": "英雄联盟"},
        )
        msg = build_live_message(item)
        text = msg.extract_plain_text()
        assert "LOL排位" in text
        assert "分类" not in text

    def test_live_message_with_area(self):
        """B站直播带分区（但不再显示分类）"""
        from plugins.monitor.formatter import build_live_message

        item = Item(
            id="live_789",
            platform="bilibili",
            source_type="live",
            target_id="789",
            title="歌回",
            nickname="唱见",
            content="歌回",
            link="https://live.bilibili.com/789",
            extra={"area_name": "娱乐"},
        )
        msg = build_live_message(item)
        text = msg.extract_plain_text()
        assert "歌回" in text
        assert "分类" not in text

    def test_live_message_no_category(self):
        """没有分类信息时不出分类行"""
        from plugins.monitor.formatter import build_live_message

        item = Item(
            id="live_999",
            platform="douyu",
            source_type="live",
            target_id="999",
            title="直播",
            nickname="主播",
            content="直播",
            link="https://www.douyu.com/999",
            extra={},
        )
        msg = build_live_message(item)
        text = msg.extract_plain_text()
        assert "分类" not in text

    def test_live_message_no_cover(self):
        """无封面图时只显示文字"""
        from plugins.monitor.formatter import build_live_message

        item = Item(
            id="live_000",
            platform="bilibili",
            source_type="live",
            target_id="000",
            title="无图直播",
            nickname="主播",
            content="无图直播",
            link="https://live.bilibili.com/000",
        )
        msg = build_live_message(item)
        assert "无图直播" in msg.extract_plain_text()


class TestDynamicForwardMessage:
    """动态合并转发消息"""

    def test_single_item(self):
        from plugins.monitor.formatter import build_dynamic_forward_msg

        items = [
            Item(
                id="1",
                platform="bilibili",
                source_type="dynamic",
                target_id="436742",
                title="测试动态",
                nickname="UP主",
                content="分享图片",
                link="https://t.bilibili.com/1",
                cover_url="https://example.com/img.jpg",
                cover_urls=["https://example.com/img.jpg"],
            ),
        ]
        nodes = build_dynamic_forward_msg(items)
        assert len(nodes) == 1
        assert nodes[0]["type"] == "node"
        assert nodes[0]["data"]["name"] == "UP主"

    def test_multi_images(self):
        """多图动态应生成多个 image segment"""
        from plugins.monitor.formatter import build_dynamic_forward_msg

        items = [
            Item(
                id="2",
                platform="bilibili",
                source_type="dynamic",
                target_id="123",
                title="九宫格",
                nickname="摄影师",
                content="九宫格来啦",
                link="https://t.bilibili.com/2",
                cover_urls=[
                    "https://example.com/1.jpg",
                    "https://example.com/2.jpg",
                    "https://example.com/3.jpg",
                ],
            ),
        ]
        nodes = build_dynamic_forward_msg(items)
        content = nodes[0]["data"]["content"]
        image_count = sum(
            1 for seg in content
            if isinstance(seg, FakeMessageSegment) and seg.type == "image"
        )
        assert image_count == 3

    def test_fallback_single_cover(self):
        """cover_urls 为空时使用 cover_url"""
        from plugins.monitor.formatter import build_dynamic_forward_msg

        items = [
            Item(
                id="3",
                platform="bilibili",
                source_type="dynamic",
                target_id="456",
                title="视频投稿",
                nickname="UP主",
                content="新视频",
                link="https://t.bilibili.com/3",
                cover_url="https://example.com/cover.jpg",
                cover_urls=[],
            ),
        ]
        nodes = build_dynamic_forward_msg(items)
        content = nodes[0]["data"]["content"]
        image_count = sum(
            1 for seg in content
            if isinstance(seg, FakeMessageSegment) and seg.type == "image"
        )
        assert image_count == 1

    def test_multiple_items(self):
        """多条动态 → 多个节点"""
        from plugins.monitor.formatter import build_dynamic_forward_msg

        items = [
            Item(
                id=str(i),
                platform="bilibili",
                source_type="dynamic",
                target_id="1",
                title=f"动态{i}",
                nickname="UP主",
                content=f"内容{i}",
                link=f"https://t.bilibili.com/{i}",
            )
            for i in range(5)
        ]
        nodes = build_dynamic_forward_msg(items)
        assert len(nodes) == 5
        for node in nodes:
            assert node["type"] == "node"


class TestForwardPreview:
    """合并转发卡片外观参数（source / news / summary）

    这些字段的行为是在 QQ 桌面客户端上实测出来的：
    source 是标题行、news 是逐条预览行、summary 是底部计行、prompt 无效。
    """

    @staticmethod
    def _item(**kw):
        base = dict(
            id="1", platform="bilibili", source_type="dynamic",
            target_id="436742", title="标题", nickname="谷雨_CSU",
            content="正文", link="https://t.bilibili.com/1",
        )
        base.update(kw)
        return Item(**base)

    def test_source_and_summary(self):
        from plugins.monitor.formatter import build_forward_preview

        preview = build_forward_preview([self._item(), self._item()])
        assert preview["source"] == "B站动态"
        assert preview["summary"] == "查看 2 条转发消息"
        # prompt 实测无效，不应再传
        assert "prompt" not in preview

    def test_news_aligned_with_items(self):
        """news 必须与节点一一对应"""
        from plugins.monitor.formatter import build_forward_preview

        preview = build_forward_preview([self._item() for _ in range(3)])
        assert len(preview["news"]) == 3
        for entry in preview["news"]:
            assert set(entry) == {"text"}

    def test_video_line(self):
        from plugins.monitor.formatter import build_forward_preview

        item = self._item(extra={"is_video": True, "video_title": "【MMD】测试视频"})
        text = build_forward_preview([item])["news"][0]["text"]
        assert text == "谷雨_CSU 投稿了视频 · 【MMD】测试视频"

    def test_article_line(self):
        from plugins.monitor.formatter import build_forward_preview

        item = self._item(extra={"is_article": True, "article_title": "周年庆专栏"})
        text = build_forward_preview([item])["news"][0]["text"]
        assert text == "谷雨_CSU 投稿了专栏 · 周年庆专栏"

    def test_draw_line_uses_first_line(self):
        """图文动态取正文第一行"""
        from plugins.monitor.formatter import build_forward_preview

        item = self._item(content="今天画了新的图\n第二行不需要")
        text = build_forward_preview([item])["news"][0]["text"]
        assert text == "谷雨_CSU：今天画了新的图"

    def test_forward_line_strips_duplicate_nickname(self):
        """转发动态正文自带「昵称：」前缀，预览行不应重复昵称"""
        from plugins.monitor.formatter import build_forward_preview

        item = self._item(
            content="谷雨_CSU：说点什么\n----------\n原作者的正文",
            extra={"is_forward": True},
        )
        text = build_forward_preview([item])["news"][0]["text"]
        assert text == "谷雨_CSU 转发了动态 · 说点什么"
        assert text.count("谷雨_CSU") == 1

    def test_empty_content_fallback(self):
        """空正文时给兜底文案，不能出现空的预览行"""
        from plugins.monitor.formatter import build_forward_preview

        text = build_forward_preview([self._item(content="")])["news"][0]["text"]
        assert text == "谷雨_CSU 发布了动态"

    def test_long_text_clipped(self):
        """超长正文会截断，避免在客户端里被随机位置切断"""
        from plugins.monitor.formatter import build_forward_preview

        item = self._item(content="字" * 200)
        text = build_forward_preview([item])["news"][0]["text"]
        assert text.endswith("…")
        assert len(text) <= 41

    def test_whitespace_collapsed(self):
        """正文里的换行/多空格压成单空格"""
        from plugins.monitor.formatter import build_forward_preview

        item = self._item(content="第一段   多余空格\n\n第二段")
        text = build_forward_preview([item])["news"][0]["text"]
        assert "\n" not in text
        assert "  " not in text
