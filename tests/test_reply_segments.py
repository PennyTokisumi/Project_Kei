"""回复分段规则测试

Kei 自己决定要不要分段（提示词不强制），代码只负责把模型给的文本拆对：
- 认 `[SEP]` 显式标记
- 认空行（模型更自然会用这个断句）
- 认单换行，但只在 2~3 行时（超过 3 行是排版，强拆会丢内容）
- 一律封顶 3 段
"""

import pytest

from plugins.llm_chat import (
    MAX_REPLY_SEGMENTS,
    REPLY_SEGMENT_DELAY_MAX,
    REPLY_SEGMENT_DELAY_MIN,
    _segment_delay,
    _split_reply,
)


class TestSplitByMarker:
    def test_single_message_untouched(self):
        assert _split_reply("真是的。もう。早点睡吧。") == ["真是的。もう。早点睡吧。"]

    def test_sep_two_segments(self):
        out = _split_reply("えっ？[SEP]（叹气）算了，牛奶热好了。")
        assert out == ["えっ？", "（叹气）算了，牛奶热好了。"]

    def test_sep_strips_whitespace(self):
        out = _split_reply("  a  [SEP]  b  ")
        assert out == ["a", "b"]

    def test_pass_is_dropped(self):
        assert _split_reply("[PASS]") == []

    def test_empty(self):
        assert _split_reply("") == []
        assert _split_reply("   ") == []


class TestSplitByBlankLine:
    """空行是模型最自然的断句方式，不认的话会变成一步消息里夹大空隙"""

    def test_blank_line_two_segments(self):
        out = _split_reply("えっ？\n\n（叹气）算了，牛奶热好了。")
        assert out == ["えっ？", "（叹气）算了，牛奶热好了。"]

    def test_blank_line_capped(self):
        out = _split_reply("a\n\nb\n\nc\n\nd\n\ne")
        assert len(out) == MAX_REPLY_SEGMENTS
        assert out == ["a", "b", "c"]


class TestSplitBySingleNewline:
    def test_two_lines_split(self):
        """单换行 2 行 → 拆（实测常见：「先一句反应」换行「再补一句」）"""
        out = _split_reply("えっ……突然问这种问题。\n（别过头）不会的。")
        assert len(out) == 2
        assert out[0] == "えっ……突然问这种问题。"

    def test_three_lines_split(self):
        out = _split_reply("一\n二\n三")
        assert out == ["一", "二", "三"]

    def test_more_than_three_lines_not_split(self):
        """超过 3 行说明在排版，强行拆会丢掉封顶外的内容 → 保持整条"""
        out = _split_reply("一\n二\n三\n四")
        assert len(out) == 1
        assert "四" in out[0]

    def test_blank_line_takes_priority_over_single_newline(self):
        """有空行时不按单换行逐行拆，避免拆得过碎"""
        out = _split_reply("第一段\n同一段的第二行\n\n第二段")
        assert out == ["第一段\n同一段的第二行", "第二段"]


class TestSegmentAfterMarker:
    def test_inner_blank_preserved_as_single_newline(self):
        """用了 [SEP] 之后，段内空行只压成单换行，不再二次拆分"""
        out = _split_reply("a[SEP]b\n\nc")
        assert len(out) == 2
        assert out == ["a", "b\nc"]


class TestSegmentDelay:
    def test_delay_within_range(self):
        for _ in range(20):
            d = _segment_delay()
            assert REPLY_SEGMENT_DELAY_MIN <= d <= REPLY_SEGMENT_DELAY_MAX

    def test_delay_is_randomized(self):
        """固定延迟会显得像定时器，必须随机"""
        values = {round(_segment_delay(), 3) for _ in range(10)}
        assert len(values) > 1


class TestCapConstant:
    def test_cap_is_three(self):
        assert MAX_REPLY_SEGMENTS == 3


class TestTranscriptEcho:
    """模型偶尔把用户原话连同 `QQ号(昵称): ` 前缀一起复述，那种分条不能上屏

    实测 effort=high 下 9 次里出现 1 次，看起来像她在自言自语地复读。
    """

    def test_drops_qq_prefixed_segment(self):
        raw = "QQ823262716(朔夜時雨): 老师：我最近在想换工作[SEP]えっ？突然说这个……"
        assert _split_reply(raw) == ["えっ？突然说这个……"]

    def test_drops_fullwidth_colon(self):
        assert _split_reply("QQ123(某人)：你好[SEP]第二句") == ["第二句"]

    def test_keeps_normal_text_mentioning_qq(self):
        """正常回复里出现「QQ」两个字不能被误杀"""
        assert _split_reply("我可不是 QQ 机器人哦。") == ["我可不是 QQ 机器人哦。"]

    def test_all_echo_returns_empty(self):
        """整条回复都是转写行 → 不发言，交给上层处理"""
        assert _split_reply("QQ123(某人): 你好") == []
