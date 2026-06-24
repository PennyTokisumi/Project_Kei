"""去重模块测试"""

import pytest

from plugins.monitor.database import init_db, is_pushed
from plugins.monitor.dedup import Deduplicator


@pytest.fixture
def dedup():
    """返回新的去重器实例（每次测试使用临时DB）"""
    init_db()
    return Deduplicator()


class TestDeduplicator:
    """去重逻辑测试"""

    def test_make_id(self):
        """ID 格式正确"""
        dedup = Deduplicator()
        item_id = dedup.make_id("bilibili", "dynamic", "436742", "123456")
        assert item_id == "bilibili_dynamic/436742/123456"

    def test_is_new_first_time(self, dedup):
        """首次检查总是 True"""
        assert dedup.is_new("bilibili", "dynamic", "436742", "abc123")

    def test_is_new_after_mark(self, dedup):
        """标记后应返回 False"""
        dedup.mark_pushed("bilibili", "dynamic", "436742", "xyz789",
                          title="test", link="http://example.com")
        assert not dedup.is_new("bilibili", "dynamic", "436742", "xyz789")

    def test_different_platform_independent(self, dedup):
        """不同平台去重独立"""
        dedup.mark_pushed("bilibili", "dynamic", "436742", "item1")
        # 相同 content_id 但不同 target → 应视为 new
        assert dedup.is_new("bilibili", "dynamic", "999999", "item1")

    def test_different_source_type_independent(self, dedup):
        """不同内容类型去重独立"""
        dedup.mark_pushed("bilibili", "live", "123", "live_123")
        assert dedup.is_new("bilibili", "dynamic", "123", "live_123")

    def test_mark_pushed_persisted(self, dedup):
        """标记应持久化到 DB"""
        dedup.mark_pushed("douyu", "live", "617916", "item_live",
                          title="直播", link="http://douyu.com/617916")
        # 用 is_pushed 直接查 DB
        item_id = dedup.make_id("douyu", "live", "617916", "item_live")
        assert is_pushed(item_id)

    def test_multiple_mark_idempotent(self, dedup):
        """多次标记同一 ID 不报错"""
        for _ in range(3):
            dedup.mark_pushed("bilibili", "dynamic", "436742", "same_id")
        assert not dedup.is_new("bilibili", "dynamic", "436742", "same_id")

    def test_empty_title_link(self, dedup):
        """空标题和链接也能正常标记"""
        dedup.mark_pushed("bilibili", "dynamic", "436742", "no_title")
        assert not dedup.is_new("bilibili", "dynamic", "436742", "no_title")
