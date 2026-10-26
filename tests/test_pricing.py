"""LLM 费用估算测试

覆盖三件容易错的事：
1. 峰谷时段判断（UTC、周一至周五、两个窗口、左闭右开）
2. 缓存命中比未命中便宜 50 倍 —— 不能再用"prompt × 单一单价"
3. 迁移前的旧记录没有缓存字段，必须兜底成全价而不是算成 0
"""

from datetime import datetime

import pytest

from plugins.llm_chat.pricing import (
    PEAK_WINDOWS_UTC,
    format_cost,
    is_peak,
    parse_created_at,
    row_cost,
    total_cost,
)


class TestIsPeak:
    """高峰：UTC 周一至周五 01:00-04:00 与 06:00-10:00"""

    def test_peak_window_inside(self):
        # 2026-10-26 是周一
        assert is_peak(datetime(2026, 10, 26, 2, 30)) is True   # 01-04 窗口内
        assert is_peak(datetime(2026, 10, 26, 8, 30)) is True   # 06-10 窗口内

    def test_peak_window_boundaries_are_half_open(self):
        """窗口左闭右开：04:00 已经不算高峰"""
        assert is_peak(datetime(2026, 10, 26, 1, 0)) is True
        assert is_peak(datetime(2026, 10, 26, 3, 59)) is True
        assert is_peak(datetime(2026, 10, 26, 4, 0)) is False
        assert is_peak(datetime(2026, 10, 26, 6, 0)) is True
        assert is_peak(datetime(2026, 10, 26, 10, 0)) is False

    def test_gap_between_windows_is_offpeak(self):
        assert is_peak(datetime(2026, 10, 26, 4, 30)) is False
        assert is_peak(datetime(2026, 10, 26, 5, 59)) is False

    def test_weekend_is_always_offpeak(self):
        # 2026-10-24 周六 / 10-25 周日
        assert is_peak(datetime(2026, 10, 24, 2, 30)) is False
        assert is_peak(datetime(2026, 10, 25, 8, 30)) is False

    def test_night_is_offpeak(self):
        assert is_peak(datetime(2026, 10, 26, 0, 0)) is False
        assert is_peak(datetime(2026, 10, 26, 23, 0)) is False

    def test_aware_datetime_converted_to_utc(self):
        """带时区的输入要先换算成 UTC 再判断"""
        from datetime import timezone, timedelta
        # 北京时间 2026-10-26 10:30 = UTC 02:30 → 高峰
        bj = timezone(timedelta(hours=8))
        assert is_peak(datetime(2026, 10, 26, 10, 30, tzinfo=bj)) is True

    def test_windows_constant_shape(self):
        assert PEAK_WINDOWS_UTC == ((1, 4), (6, 10))


class TestParseCreatedAt:
    def test_parses_sqlite_format(self):
        assert parse_created_at("2026-10-26 08:37:05") == datetime(2026, 10, 26, 8, 37, 5)

    def test_bad_value_returns_none(self):
        assert parse_created_at(None) is None
        assert parse_created_at("") is None
        assert parse_created_at("not-a-time") is None


class TestRowCost:
    def test_cache_hit_is_far_cheaper_than_miss(self):
        """同样的 prompt token 数，命中 vs 未命中差 50 倍"""
        peak = "2026-10-26 08:00:00"
        all_hit = row_cost(3000, 0, cache_hit_tokens=3000, cache_miss_tokens=0,
                           created_at=peak)
        all_miss = row_cost(3000, 0, cache_hit_tokens=0, cache_miss_tokens=3000,
                            created_at=peak)
        assert all_hit > 0
        assert all_miss / all_hit == pytest.approx(50, rel=0.05)

    def test_offpeak_is_half_of_peak(self):
        peak = row_cost(3000, 0, 0, 3000, "2026-10-26 08:00:00")     # 周一 08 点
        off = row_cost(3000, 0, 0, 3000, "2026-10-26 12:00:00")      # 周一 12 点
        assert peak / off == pytest.approx(2.0, rel=0.001)

    def test_legacy_row_without_cache_fields_uses_miss_price(self):
        """迁移前的记录两列都是 0 → 全部按未命中价算，不能算成 0 元"""
        legacy = row_cost(1000, 0, cache_hit_tokens=0, cache_miss_tokens=0,
                          created_at="2026-10-26 08:00:00")
        explicit = row_cost(1000, 0, cache_hit_tokens=0, cache_miss_tokens=1000,
                            created_at="2026-10-26 08:00:00")
        assert legacy > 0
        assert legacy == pytest.approx(explicit)

    def test_real_world_request(self):
        """实测那次：prompt 2873（命中 2688/未命中 185）+ 输出 11

        旧的"全价乘 prompt"算法算出来是 0.0058 元，真实约 0.0006 元 —— 差近 10 倍。
        """
        cost = row_cost(2873, 11, 2688, 185, "2026-10-26 08:18:44")
        old_style = (2873 * 2.02 + 11 * 3.02) / 1_000_000
        assert 0 < cost < old_style / 5

    def test_zero_tokens_cost_zero(self):
        assert row_cost(0, 0, 0, 0, "2026-10-26 08:00:00") == 0.0

    def test_missing_created_at_falls_back_without_crashing(self):
        assert row_cost(100, 10, 0, 100, None) > 0


class TestTotalCost:
    def test_sums_rows_of_dict(self):
        rows = [
            {"prompt_tokens": 1000, "completion_tokens": 10,
             "cache_hit_tokens": 800, "cache_miss_tokens": 200,
             "created_at": "2026-10-26 08:00:00"},
            {"prompt_tokens": 500, "completion_tokens": 5,
             "cache_hit_tokens": 0, "cache_miss_tokens": 500,
             "created_at": "2026-10-26 12:00:00"},
        ]
        total = total_cost(rows)
        assert total == pytest.approx(rows[0]["prompt_tokens"] * 0 + row_cost(
            1000, 10, 800, 200, "2026-10-26 08:00:00"
        ) + row_cost(500, 5, 0, 500, "2026-10-26 12:00:00"))

    def test_empty_rows(self):
        assert total_cost([]) == 0.0

    def test_rows_missing_optional_columns(self):
        """缺 cache 字段的 dict 也不能炸"""
        rows = [{"prompt_tokens": 100, "completion_tokens": 1}]
        assert total_cost(rows) > 0

    def test_none_values_treated_as_zero(self):
        rows = [{"prompt_tokens": None, "completion_tokens": None,
                 "cache_hit_tokens": None, "cache_miss_tokens": None,
                 "created_at": None}]
        assert total_cost(rows) == 0.0


class TestFormatCost:
    def test_large_amount_two_decimals(self):
        assert format_cost(12.3456) == "¥12.35"

    def test_small_amount_keeps_precision(self):
        assert format_cost(0.112) == "¥0.112"
        assert format_cost(0.0006) == "¥0.0006"

    def test_zero(self):
        assert format_cost(0.0) == "¥0.0000"
