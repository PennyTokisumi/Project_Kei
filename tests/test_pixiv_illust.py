"""Pixiv 数据源与图片处理测试

重点覆盖：
- R18 过滤只认 xRestrict（sl 高不代表 R18）
- isMasked 作品跳过
- 分页图片数量裁剪（pixiv_max_images）
- 卡片外观参数（单条 vs 多条）
- 图片压缩：等比、长边上限、不放大
"""

import io

import pytest
from PIL import Image

from plugins.monitor.media import compress_to_jpeg
from plugins.monitor.sources.pixiv_illust import (
    ALL_AGES_RESTRICT,
    PixivIllust,
    parse_pub_ts,
)


# ─── 辅助 ──────────────────────────────────────────────────

def make_work(**overrides) -> dict:
    """构造一条 profile/top 里的作品记录"""
    work = {
        "id": "148404748",
        "title": "燐羽ちゃん!お姉ちゃんは渡さないよ!!",
        "userName": "にんげんまめ",
        "userId": "38297201",
        "xRestrict": 0,
        "sl": 2,
        "illustType": 0,
        "pageCount": 2,
        "createDate": "2026-08-14T01:50:00+00:00",
        "isMasked": False,
        "aiType": 0,
        # profile/top 的 tags 是字符串列表（详情接口才是 [{"tag": ...}]）
        "tags": ["ブルーアーカイブ", "伊落マリー"],
        "urls": {"regular": "https://i.pximg.net/x/master1200.jpg"},
    }
    work.update(overrides)
    return work


def make_source() -> PixivIllust:
    return PixivIllust("38297201", 795185048)


# ─── R18 过滤 ──────────────────────────────────────────────

class TestR18Filter:
    def test_all_ages_passes(self):
        item = make_source()._parse_work(make_work(xRestrict=0))
        assert item is not None

    def test_r18_rejected(self):
        assert make_source()._parse_work(make_work(xRestrict=1)) is None

    def test_r18g_rejected(self):
        assert make_source()._parse_work(make_work(xRestrict=2)) is None

    def test_high_sl_is_not_r18(self):
        """sl 高但 xRestrict=0 的作品必须放行

        实测见过全年龄作品 sl=6，项目约定只信 xRestrict。
        """
        item = make_source()._parse_work(make_work(xRestrict=0, sl=6))
        assert item is not None

    def test_masked_rejected(self):
        assert make_source()._parse_work(make_work(isMasked=True)) is None

    def test_empty_id_rejected(self):
        assert make_source()._parse_work(make_work(id="")) is None
        assert make_source()._parse_work(make_work(id=None)) is None

    def test_non_dict_rejected(self):
        assert make_source()._parse_work("not a dict") is None


# ─── 字段解析 ──────────────────────────────────────────────

class TestParseWork:
    def test_basic_fields(self):
        item = make_source()._parse_work(make_work())
        assert item is not None
        assert item.id == "148404748"
        assert item.platform == "pixiv"
        assert item.source_type == "illust"
        assert item.target_id == "38297201"
        assert item.nickname == "にんげんまめ"
        assert item.link == "https://www.pixiv.net/artworks/148404748"
        assert item.extra["page_count"] == 2

    def test_title_fallback(self):
        item = make_source()._parse_work(make_work(title="", id="999"))
        assert item is not None
        assert "999" in item.title

    def test_nickname_fallback(self):
        item = make_source()._parse_work(make_work(userName=""))
        assert item is not None
        assert item.nickname == "画师38297201"

    def test_illust_and_manga_both_parsed(self):
        """fetch 会把 illusts 和 manga 两个字典合并，两者结构一致"""
        for illust_type in (0, 1):
            item = make_source()._parse_work(make_work(illustType=illust_type))
            assert item is not None


class TestTagsParsing:
    """两种接口的 tags 结构不一样，必须都兼容

    profile/top → ["漫画", "C108"]（字符串列表）
    illust/{id} → [{"tag": "漫画"}]（对象列表）
    """

    def test_plain_string_list(self):
        item = make_source()._parse_work(make_work(tags=["漫画", "C108"]))
        assert item.extra["tags"] == ["漫画", "C108"]

    def test_object_list(self):
        item = make_source()._parse_work(
            make_work(tags=[{"tag": "漫画"}, {"tag": "C108"}])
        )
        assert item.extra["tags"] == ["漫画", "C108"]

    def test_missing_tags(self):
        item = make_source()._parse_work(make_work(tags=None))
        assert item.extra["tags"] == []

    def test_empty_and_bad_entries_dropped(self):
        item = make_source()._parse_work(make_work(tags=["", "  ", {"tag": ""}]))
        assert item.extra["tags"] == []


class TestParsePubTs:
    def test_valid(self):
        ts = parse_pub_ts("2026-08-14T01:50:00+00:00")
        assert ts > 0

    def test_invalid(self):
        assert parse_pub_ts("") == 0.0
        assert parse_pub_ts("not-a-date") == 0.0


class TestSourceIdentity:
    def test_platform_and_type(self):
        src = make_source()
        assert src.platform == "pixiv"
        assert src.source_type == "illust"

    def test_all_ages_constant(self):
        assert ALL_AGES_RESTRICT == 0


# ─── 卡片外观 ──────────────────────────────────────────────

class TestPixivPreview:
    def _items(self, n: int):
        src = make_source()
        items = []
        for i in range(n):
            items.append(src._parse_work(make_work(id=str(1000 + i), title=f"标题{i}")))
        return items

    def test_single_item(self):
        from plugins.monitor.formatter import build_pixiv_preview
        preview = build_pixiv_preview(self._items(1))
        assert preview["source"].endswith("投稿了P站")
        assert "にんげんまめ" in preview["source"]
        assert preview["news"] == [{"text": "标题0"}]
        assert preview["summary"] == "查看 1 条转发消息"

    def test_multi_items(self):
        from plugins.monitor.formatter import build_pixiv_preview
        preview = build_pixiv_preview(self._items(3))
        assert preview["source"] == "Pixiv · 3 条更新"
        assert len(preview["news"]) == 3
        assert preview["summary"] == "查看 3 条转发消息"


# ─── 图片压缩 ──────────────────────────────────────────────

def _png_bytes(size, color=(200, 30, 40)) -> bytes:
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class TestCompress:
    def test_long_side_capped(self):
        out = compress_to_jpeg(_png_bytes((2400, 1200)), max_side=1200)
        assert out
        with Image.open(io.BytesIO(out)) as im:
            assert max(im.size) <= 1200
            assert im.format == "JPEG"

    def test_aspect_ratio_kept(self):
        out = compress_to_jpeg(_png_bytes((2400, 1200)), max_side=1200)
        with Image.open(io.BytesIO(out)) as im:
            assert im.size == (1200, 600)

    def test_small_image_not_upscaled(self):
        out = compress_to_jpeg(_png_bytes((300, 200)), max_side=1200)
        with Image.open(io.BytesIO(out)) as im:
            assert im.size == (300, 200)

    def test_rgba_flattened(self):
        img = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        out = compress_to_jpeg(buf.getvalue())
        assert out
        with Image.open(io.BytesIO(out)) as im:
            assert im.mode == "RGB"

    def test_garbage_returns_none(self):
        assert compress_to_jpeg(b"not an image") is None


# ─── 轮询间隔 ──────────────────────────────────────────────

class TestPollInterval:
    def test_pixiv_uses_own_interval(self):
        from plugins.monitor.scheduler import poll_interval_for
        from config import config

        src = make_source()
        assert poll_interval_for(src) == max(60, config.pixiv_poll_interval)

    def test_pixiv_interval_has_floor(self):
        """即使配置成 1 秒也不允许，Pixiv 会被限流"""
        from plugins.monitor.scheduler import poll_interval_for
        from config import config

        original = config.pixiv_poll_interval
        try:
            config.pixiv_poll_interval = 1
            assert poll_interval_for(make_source()) >= 60
        finally:
            config.pixiv_poll_interval = original


# ─── 工厂注册 ──────────────────────────────────────────────

class TestFactory:
    def test_pixiv_registered(self):
        from plugins.monitor.scheduler import SOURCE_FACTORY
        assert "pixiv_illust" in SOURCE_FACTORY
        assert SOURCE_FACTORY["pixiv_illust"] is PixivIllust

    def test_alias_maps_to_full_key(self):
        from plugins.admin.commands import PLATFORM_ALIASES
        assert PLATFORM_ALIASES["pixiv"] == "pixiv_illust"


# ─── 静默同步 / 补推截断 ───────────────────────────────────

def _make_items(n: int) -> list:
    """构造 n 条 Item，pub_ts 递减（和 fetch 的排序一致：新的在前）"""
    src = make_source()
    items = []
    for i in range(n):
        work = make_work(id=str(2000 + i), title=f"作品{i}")
        item = src._parse_work(work)
        item.pub_ts = 1_800_000_000 - i * 3600
        items.append(item)
    return items


class TestSyncPolicy:
    """Pixiv 的同步策略：首次订阅静默，重启补推最多 3 条且合成一条转发"""

    def test_first_subscription_is_silent(self):
        """新订阅时一条都不推，但全部标记为已推送"""
        from plugins.monitor.database import has_pushed_items, init_db
        from plugins.monitor.scheduler import _sync_pixiv_new_items

        init_db()
        src = make_source()
        items = _make_items(8)

        assert has_pushed_items("pixiv", "38297201") is False
        result = _sync_pixiv_new_items(src, items)

        assert result == []
        assert has_pushed_items("pixiv", "38297201") is True

    def test_second_poll_pushes_new(self):
        """已有推送记录后，新作品正常推送"""
        from plugins.monitor.database import init_db
        from plugins.monitor.scheduler import _sync_pixiv_new_items

        init_db()
        src = make_source()
        _sync_pixiv_new_items(src, _make_items(5))          # 首次静默

        fresh = _make_items(1)
        result = _sync_pixiv_new_items(src, fresh)
        assert len(result) == 1
        assert result[0].id == fresh[0].id

    def test_backlog_truncated_to_limit(self):
        """停机很久后一次冒出很多，只推最新 3 条"""
        from plugins.monitor.database import init_db
        from plugins.monitor.scheduler import PIXIV_BACKLOG_LIMIT, _sync_pixiv_new_items

        init_db()
        src = make_source()
        _sync_pixiv_new_items(src, _make_items(1))          # 建立推送记录

        result = _sync_pixiv_new_items(src, _make_items(10))
        assert len(result) == PIXIV_BACKLOG_LIMIT
        # 保留的是最新的（列表头部）
        assert [it.id for it in result] == [str(2000 + i) for i in range(PIXIV_BACKLOG_LIMIT)]

    def test_small_backlog_passes_through(self):
        """不超过上限时全部推送"""
        from plugins.monitor.database import init_db
        from plugins.monitor.scheduler import _sync_pixiv_new_items

        init_db()
        src = make_source()
        _sync_pixiv_new_items(src, _make_items(1))

        result = _sync_pixiv_new_items(src, _make_items(2))
        assert len(result) == 2
