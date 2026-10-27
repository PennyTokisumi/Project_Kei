"""余额变化「Kei 自己跟一句」测试 — 全部脱网

LLM 调用一律用 monkeypatch 替换 ``llm_client.chat``，不会产生任何真实网络请求。
每个用例沿用 conftest 的独立临时库。
"""

import asyncio
from datetime import datetime

import pytest

from config import config
from plugins.llm_chat import balance_comment as bc
from plugins.llm_chat.persona import PERSONA_PROMPT

GROUP_ID = 123456789
NOW = datetime(2026, 10, 27, 20, 30, 0)

# 与模块内保持一致的固定文案（被测试钉住，改动时应同步改测试）
FACT_PREFIX = "【你刚注意到的变化】"
FORMAT_RULE = (
    "这是你在群里自己说的话，不是回复谁。可以说一到三句短话，也可以分成两三条发出去"
    "（用 [SEP] 隔开，最多 3 条）。不要 @ 任何人，不要解释你在做什么。"
)
STAGE_DIRECTIONS = {
    "topup": "（你合上账户页面，抬起头。）",
    "grant": "（你盯着那笔额度看了一会儿。）",
    "low": "（你盯着账户余额，沉默了一下。）",
}


# ─── 辅助 ──────────────────────────────────────────────

def _run(coro):
    """在同步用例里跑一次异步接口"""
    return asyncio.run(coro)


@pytest.fixture
def llm_on(monkeypatch):
    """临时库建表、给测试群打开 LLM，并固定相关配置"""
    from plugins.monitor.database import init_db, set_setting

    init_db()
    set_setting(f"llm_enabled_{GROUP_ID}", "1")
    monkeypatch.setattr(config, "balance_kei_comment", True)
    monkeypatch.setattr(config, "balance_low_threshold", 10.0)
    yield


def _install_chat(monkeypatch, *, result=None, exc=None, calls=None):
    """把 llm_client.chat 换成假的；calls 传入列表时记录调用参数"""

    async def fake_chat(**kwargs):
        if calls is not None:
            calls.append(kwargs)
        if exc is not None:
            raise exc
        return result

    monkeypatch.setattr(bc.llm_client, "chat", fake_chat)


# ─── build_messages：结构与措辞 ─────────────────────────


class TestBuildMessages:
    @pytest.mark.parametrize("kind", ["topup", "grant", "low"])
    def test_roles_and_persona_first(self, kind):
        """persona 必须是第一条 system，最后一条是对应的舞台指示"""
        messages = bc.build_messages(kind, 10.0, 20.0, GROUP_ID, now=NOW)

        assert [m["role"] for m in messages] == ["system"] * 4 + ["user"]
        assert messages[0]["content"] == PERSONA_PROMPT
        assert messages[-1] == {"role": "user", "content": STAGE_DIRECTIONS[kind]}

    def test_topup_fact(self):
        """充值：金额、充值前余额（总额 - 本次）、变化后余额都要对得上"""
        messages = bc.build_messages("topup", 10.0, 15.0, GROUP_ID, now=NOW)
        assert messages[2]["content"] == (
            FACT_PREFIX + "\n"
            + "你看了眼自己的 DeepSeek 账户：充值额度多了 ¥10.00。"
              "总余额从 ¥5.00 变成 ¥15.00。"
        )

    def test_grant_fact(self):
        """赠送额度：说明来源是 DeepSeek 官方，并给出当前总额

        「梁文锋」只作为**事实**出现（DeepSeek 创始人），不是「要感谢他」这种指令 ——
        实测这样她有约 2/3 概率自然提到他，写成指令则每句都硬塞。
        """
        messages = bc.build_messages("grant", 20.0, 35.5, GROUP_ID, now=NOW)
        assert messages[2]["content"] == (
            FACT_PREFIX + "\n"
            + "你看了眼自己的 DeepSeek 账户：账户里多了一笔 ¥20.00 的赠送额度，"
              "是 DeepSeek 官方发的（DeepSeek 的创始人是梁文锋）。"
              "总余额现在是 ¥35.50。"
        )

    def test_low_fact(self):
        """余额不足：余额与阈值的数字都要出现在事实块里"""
        messages = bc.build_messages("low", 0.0, 3.5, GROUP_ID, now=NOW, threshold=10.0)
        assert messages[2]["content"] == (
            FACT_PREFIX + "\n"
            + "你看了眼自己的 DeepSeek 账户：总余额只剩 ¥3.50，已经低于 ¥10.00 了。"
        )

    def test_low_threshold_defaults_to_config(self, monkeypatch):
        """不传 threshold 时取 config.balance_low_threshold"""
        monkeypatch.setattr(config, "balance_low_threshold", 8.8)
        messages = bc.build_messages("low", 0.0, 3.0, GROUP_ID, now=NOW)
        assert "¥8.80" in messages[2]["content"]

    def test_env_block_and_fact_prefix(self):
        """环境块带群号 + 时间；事实块以「你刚注意到的变化」开头"""
        messages = bc.build_messages("topup", 10.0, 15.0, GROUP_ID, now=NOW)
        assert str(GROUP_ID) in messages[1]["content"]
        assert "2026-10-27 20:30:00" in messages[1]["content"]
        assert messages[2]["content"].startswith(FACT_PREFIX)

    def test_format_rule_exact(self):
        """第 [3] 条是纯格式约束，三个场景共用一个字符串"""
        for kind in ("topup", "grant", "low"):
            messages = bc.build_messages(kind, 10.0, 20.0, GROUP_ID, now=NOW)
            assert messages[3] == {"role": "system", "content": FORMAT_RULE}

    def test_no_directive_words_in_prompts(self):
        """事实块 / 舞台指示里不许出现指令性词汇

        Kei 的情绪必须由 persona.py 的人格自然产生；一旦提示词里出现
        「感谢」「幽默」「嘴硬」这类词，人设就变成了被指挥的表演。
        这条断言是防止以后有人把它们改回去。
        """
        forbidden = ("感谢", "感恩", "谢谢", "催", "幽默", "嘴硬", "傲娇",
                     "卖萌", "撒娇", "表演", "表现出", "表现得")
        for kind in ("topup", "grant", "low"):
            messages = bc.build_messages(kind, 10.0, 20.0, GROUP_ID, now=NOW)
            # [1:] 跳过 persona —— 那里本来就该有性格描写
            text = "\n".join(m["content"] for m in messages[1:])
            for word in forbidden:
                assert word not in text, f"{kind} 的提示词里出现了指令性词汇：{word}"

    def test_unknown_kind_raises(self):
        with pytest.raises(ValueError):
            bc.build_messages("refund", 1.0, 2.0, GROUP_ID, now=NOW)


# ─── kei_balance_comment：兜底与清洗 ─────────────────────


class TestKeiBalanceComment:
    def test_success_and_call_options(self, monkeypatch, llm_on):
        """成功路径：返回清洗后的文本，且请求参数符合约定"""
        calls = []
        _install_chat(monkeypatch, result={"content": "  原来到账了。  "}, calls=calls)

        text = _run(bc.kei_balance_comment("topup", 10.0, 15.0, GROUP_ID))

        assert text == ["原来到账了。"]
        assert len(calls) == 1
        assert calls[0]["max_tokens"] == 200
        assert calls[0]["temperature"] == pytest.approx(0.7)
        assert calls[0]["enable_thinking"] is False
        assert [m["role"] for m in calls[0]["messages"]] == ["system"] * 4 + ["user"]
        assert calls[0]["messages"][0]["content"] == PERSONA_PROMPT

    def test_disabled_by_config(self, monkeypatch):
        """config.balance_kei_comment=False → 不说话，也不调用 LLM"""
        calls = []
        monkeypatch.setattr(config, "balance_kei_comment", False)
        _install_chat(monkeypatch, result={"content": "不该出现"}, calls=calls)

        assert _run(bc.kei_balance_comment("topup", 10.0, 15.0, GROUP_ID)) == []
        assert calls == []

    def test_group_llm_disabled(self, monkeypatch):
        """该群没有 KEI ON → 返回空串，同样不调用 LLM"""
        from plugins.monitor.database import init_db, set_setting

        calls = []
        init_db()
        set_setting(f"llm_enabled_{GROUP_ID}", "0")
        monkeypatch.setattr(config, "balance_kei_comment", True)
        _install_chat(monkeypatch, result={"content": "不该出现"}, calls=calls)

        assert _run(bc.kei_balance_comment("topup", 10.0, 15.0, GROUP_ID)) == []
        assert calls == []

    def test_group_id_defaults_to_alert_group(self, monkeypatch, llm_on):
        """播报器只传 (kind, amount, balance) 三个参数 → 按播报群判断 KEI ON"""
        monkeypatch.setattr(config, "balance_alert_group_id", GROUP_ID)
        _install_chat(monkeypatch, result={"content": "原来到账了。"})

        assert _run(bc.kei_balance_comment("topup", 10.0, 15.0)) == ["原来到账了。"]

    def test_default_group_without_llm(self, monkeypatch, llm_on):
        """缺省群就是播报群；它没 KEI ON 时同样不说话"""
        calls = []
        monkeypatch.setattr(config, "balance_alert_group_id", 999999)
        _install_chat(monkeypatch, result={"content": "不该出现"}, calls=calls)

        assert _run(bc.kei_balance_comment("topup", 10.0, 15.0)) == []
        assert calls == []

    def test_pass_returns_empty(self, monkeypatch, llm_on):
        """模型判断此刻不该说话 → [PASS] 不上屏"""
        _install_chat(monkeypatch, result={"content": "[PASS]"})
        assert _run(bc.kei_balance_comment("low", 0.0, 3.0, GROUP_ID)) == []

    def test_quotes_are_stripped(self, monkeypatch, llm_on):
        """模型爱把台词包在引号里，成对出现就剥掉"""
        for raw, expected in (
            ("「原来到账了。」", "原来到账了。"),
            ("“原来到账了。”", "原来到账了。"),
            ('"原来到账了。"', "原来到账了。"),
        ):
            _install_chat(monkeypatch, result={"content": raw})
            assert _run(bc.kei_balance_comment("topup", 10.0, 15.0, GROUP_ID)) == [expected]

    def test_at_prefix_is_stripped(self, monkeypatch, llm_on):
        """模型模仿群聊格式去 @ 人 → 去掉 @ 前缀（不能吞掉正文）"""
        # 有空白分隔：整个「@昵称」前缀都去掉
        # 没有空白：只去掉 @ 符号，后面的正文必须保留
        for raw, expected in (
            ("@老师 原来到账了", "原来到账了"),
            ("@老师原来到账了", "老师原来到账了"),
        ):
            _install_chat(monkeypatch, result={"content": raw})
            assert _run(bc.kei_balance_comment("topup", 10.0, 15.0, GROUP_ID)) == [expected]

    def test_exception_returns_empty(self, monkeypatch, llm_on):
        """LLM 抛异常也不能冒泡，否则会打断播报主流程"""
        _install_chat(monkeypatch, exc=RuntimeError("网络炸了"))
        assert _run(bc.kei_balance_comment("topup", 10.0, 15.0, GROUP_ID)) == []

    def test_timeout_returns_empty(self, monkeypatch, llm_on):
        """超过 15 秒（测试里缩短）就放弃，返回空串"""
        monkeypatch.setattr(bc, "_LLM_TIMEOUT", 0.05)

        async def slow_chat(**kwargs):
            await asyncio.sleep(0.5)
            return {"content": "太迟了"}

        monkeypatch.setattr(bc.llm_client, "chat", slow_chat)
        assert _run(bc.kei_balance_comment("topup", 10.0, 15.0, GROUP_ID)) == []

    def test_long_output_is_truncated(self, monkeypatch, llm_on):
        """单段超过 80 字截断到 80 字（允许两三句，所以上限比早先的 60 宽松）"""
        _install_chat(monkeypatch, result={"content": "啊" * 200})
        segments = _run(bc.kei_balance_comment("grant", 20.0, 35.0, GROUP_ID))
        assert segments == ["啊" * 80]
        assert len(segments[0]) == 80

    def test_blank_or_missing_content(self, monkeypatch, llm_on):
        """空 / 纯空白 / 缺 content 字段都当作不说话"""
        for result in ({"content": ""}, {"content": "   \n\t "}, {}, {"content": None}):
            _install_chat(monkeypatch, result=result)
            assert _run(bc.kei_balance_comment("grant", 20.0, 35.0, GROUP_ID)) == []

    def test_llm_error_dict(self, monkeypatch, llm_on):
        """供应商未配置 / 请求失败时 client 返回 error + 空 content → 空串"""
        _install_chat(monkeypatch, result={"content": "", "error": "API Key 未配置"})
        assert _run(bc.kei_balance_comment("topup", 10.0, 15.0, GROUP_ID)) == []

    def test_unknown_kind(self, monkeypatch, llm_on):
        """未知事件类型直接返回空串，不浪费一次请求"""
        calls = []
        _install_chat(monkeypatch, result={"content": "不该出现"}, calls=calls)

        assert _run(bc.kei_balance_comment("refund", 1.0, 2.0, GROUP_ID)) == []
        assert calls == []

class TestSegments:
    """她可以像正常聊天那样把话分成两三条发出去

    复用聊天通道的 _split_reply：认 [SEP]、空行、单换行（≤3 行），封顶 3 段。
    """

    def test_sep_two_segments(self, monkeypatch, llm_on):
        _install_chat(monkeypatch, result={"content": "诶？多了钱……[SEP]是老师充的吗……谢谢"})
        segs = _run(bc.kei_balance_comment("topup", 100.0, 133.0, GROUP_ID))
        assert segs == ["诶？多了钱……", "是老师充的吗……谢谢"]

    def test_blank_line_two_segments(self, monkeypatch, llm_on):
        """模型常用空行断句，不认的话会变成一条消息里夹大空隙"""
        _install_chat(monkeypatch, result={"content": "诶？多了钱……\n\n是老师充的吗"})
        segs = _run(bc.kei_balance_comment("topup", 100.0, 133.0, GROUP_ID))
        assert segs == ["诶？多了钱……", "是老师充的吗"]

    def test_single_message_stays_one(self, monkeypatch, llm_on):
        """两句话写在一条里也可以，不该被硬拆"""
        raw = "DeepSeek 官方突然送了 50 块额度……老师知道这件事吗？梁文锋先生还挺大方的。"
        _install_chat(monkeypatch, result={"content": raw})
        assert _run(bc.kei_balance_comment("grant", 50.0, 183.0, GROUP_ID)) == [raw]

    def test_capped_at_three(self, monkeypatch, llm_on):
        _install_chat(monkeypatch, result={"content": "一[SEP]二[SEP]三[SEP]四"})
        segs = _run(bc.kei_balance_comment("topup", 10.0, 20.0, GROUP_ID))
        assert len(segs) == 3
        assert segs == ["一", "二", "三"]

    def test_empty_segments_dropped(self, monkeypatch, llm_on):
        _install_chat(monkeypatch, result={"content": "诶？[SEP]   [SEP]谢谢"})
        assert _run(bc.kei_balance_comment("topup", 10.0, 20.0, GROUP_ID)) == ["诶？", "谢谢"]
