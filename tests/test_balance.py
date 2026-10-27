"""DeepSeek 余额播报测试 — 解析 / 事件判定 / 播报文本 / 状态机 / DB 往返 / 轮询编排

全部脱网：
- HTTP 走 pytest-httpx 拦截，不打真实接口；
- DB 由 conftest 的 ``temp_db`` 提供，每个用例一个独立临时库；
- 群消息用假的 Message / MessageSegment + 假 bot 捕获。
"""

import httpx
import pytest

from config import SENSEI_QQ, config
from plugins.monitor import balance as balance_mod
from plugins.monitor.balance import (
    KIND_GRANT,
    KIND_LOW,
    KIND_TOPUP,
    DEFAULT_BALANCE_URL,
    BalanceSnapshot,
    _build_alert_message,
    detect_events,
    fetch_balance,
    format_alert_text,
    needs_fast_poll,
    parse_balance_payload,
    poll_balance,
    reset_state_for_tests,
)
from plugins.monitor.database import (
    cleanup_balance_snapshots,
    get_last_balance_snapshot,
    init_db,
    save_balance_snapshot,
)
from utils.db import get_conn

# 官方文档里的响应样例（金额是字符串）
CNY_PAYLOAD = {
    "is_available": True,
    "balance_infos": [
        {
            "currency": "CNY",
            "total_balance": "33.23",
            "granted_balance": "0.00",
            "topped_up_balance": "33.23",
        }
    ],
}


# ─── 夹具 ──────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_balance_state(_patch_db_path):
    """每个用例：建表 + 清空模块级内存状态（显式依赖 _patch_db_path，确保用的是临时库）"""
    init_db()
    reset_state_for_tests()
    yield
    reset_state_for_tests()


def snap(total: float = 100.0, granted: float = 0.0,
         topped_up: float = 100.0, available: bool = True,
         currency: str = "CNY") -> BalanceSnapshot:
    """快速构造快照"""
    return BalanceSnapshot(
        currency=currency, total=total, granted=granted,
        topped_up=topped_up, is_available=available,
    )


class _FakeSeg:
    """假的 MessageSegment —— 只记录类型和数据"""

    def __init__(self, seg_type: str, data):
        self.type = seg_type
        self.data = data

    def __repr__(self):
        return f"_FakeSeg({self.type!r}, {self.data!r})"


class _FakeMessageSegment:
    @staticmethod
    def at(qq):
        return _FakeSeg("at", qq)

    @staticmethod
    def text(text):
        return _FakeSeg("text", text)


class _FakeMessage(list):
    """假的 Message（list 子类）"""

    @property
    def plain_text(self) -> str:
        return "".join(str(seg.data) for seg in self if seg.type == "text")


class _FakeBot:
    """只实现 send_group_msg，记录每次发送"""

    def __init__(self):
        self.sent: list[tuple[int, _FakeMessage]] = []

    async def send_group_msg(self, group_id, message):
        self.sent.append((group_id, message))


class _FailingBot:
    """发送必失败的 bot —— 验证播报失败不影响落库"""

    async def send_group_msg(self, group_id, message):
        raise RuntimeError("QQ 掉线了")


@pytest.fixture
def fake_messaging(monkeypatch):
    """替换 balance 模块里的适配器类型，断言更方便"""
    monkeypatch.setattr(balance_mod, "Message", _FakeMessage)
    monkeypatch.setattr(balance_mod, "MessageSegment", _FakeMessageSegment)


# ─── 1. 解析 ────────────────────────────────────────────


class TestParseBalancePayload:
    def test_normal(self):
        """官方样例：字段全部就位"""
        s = parse_balance_payload(CNY_PAYLOAD)
        assert s is not None
        assert s.currency == "CNY"
        assert s.total == 33.23
        assert s.granted == 0.0
        assert s.topped_up == 33.23
        assert s.is_available is True
        assert s.created_at is None

    def test_string_amounts_converted_and_rounded(self):
        """字符串金额 → float，并保留两位小数"""
        s = parse_balance_payload({
            "is_available": True,
            "balance_infos": [{
                "currency": "CNY",
                "total_balance": "33.239",
                "granted_balance": "1.005",
                "topped_up_balance": "32.234",
            }],
        })
        assert s is not None
        assert isinstance(s.total, float)
        assert s.total == pytest.approx(33.24)
        assert s.granted == pytest.approx(1.0)   # round(1.005, 2) 受二进制影响可能是 1.0
        assert s.topped_up == pytest.approx(32.23)

    def test_multi_currency_picks_cny(self):
        """多币种只取 CNY（与它在列表中的位置无关）"""
        payload = {
            "is_available": True,
            "balance_infos": [
                {"currency": "USD", "total_balance": "4.99",
                 "granted_balance": "0.00", "topped_up_balance": "4.99"},
                {"currency": "CNY", "total_balance": "33.23",
                 "granted_balance": "0.00", "topped_up_balance": "33.23"},
            ],
        }
        s = parse_balance_payload(payload)
        assert s is not None
        assert s.currency == "CNY"
        assert s.total == 33.23

    def test_missing_currency_returns_none(self):
        """只有 USD、找不到 CNY → None"""
        payload = {
            "is_available": True,
            "balance_infos": [
                {"currency": "USD", "total_balance": "4.99",
                 "granted_balance": "0.00", "topped_up_balance": "4.99"},
            ],
        }
        assert parse_balance_payload(payload) is None
        # 指定币种存在时能取到
        assert parse_balance_payload(payload, currency="USD").total == 4.99

    def test_bad_structure_returns_none(self):
        """结构缺失 / 类型不对 / 金额不是数字 → 一律 None，不抛异常"""
        assert parse_balance_payload(None) is None
        assert parse_balance_payload({}) is None
        assert parse_balance_payload({"balance_infos": "oops"}) is None
        # 缺 total_balance
        assert parse_balance_payload({
            "is_available": True,
            "balance_infos": [{"currency": "CNY",
                               "granted_balance": "0.00",
                               "topped_up_balance": "0.00"}],
        }) is None
        # 金额是空串
        assert parse_balance_payload({
            "is_available": True,
            "balance_infos": [{"currency": "CNY",
                               "total_balance": "",
                               "granted_balance": "0.00",
                               "topped_up_balance": "0.00"}],
        }) is None


# ─── 2. 事件判定 ────────────────────────────────────────


class TestDetectEvents:
    def test_first_run_baseline_no_topup_or_grant(self):
        """首次启动（prev=None）只建基线，不补报历史充值/赠送"""
        curr = snap(total=100.0, granted=50.0, topped_up=50.0)
        assert detect_events(None, curr, 10.0, True) == []

    def test_first_run_low_reports_low_only(self):
        """首次启动且余额已低于阈值 → 只报 LOW"""
        curr = snap(total=5.0, granted=0.0, topped_up=5.0)
        assert detect_events(None, curr, 10.0, True) == [(KIND_LOW, 5.0)]

    def test_first_run_low_respects_armed(self):
        """首次启动但未武装（重启前已提醒过）→ 不报"""
        curr = snap(total=5.0)
        assert detect_events(None, curr, 10.0, False) == []

    def test_topup_detected(self):
        """充值增量"""
        prev = snap(total=23.23, granted=0.0, topped_up=23.23)
        curr = snap(total=133.23, granted=0.0, topped_up=133.23)
        assert detect_events(prev, curr, 10.0, True) == [(KIND_TOPUP, 110.0)]

    def test_grant_detected(self):
        """赠送额度增量"""
        prev = snap(total=100.0, granted=0.0, topped_up=100.0)
        curr = snap(total=150.0, granted=50.0, topped_up=100.0)
        assert detect_events(prev, curr, 10.0, True) == [(KIND_GRANT, 50.0)]

    def test_topup_and_grant_order(self):
        """同时到账 → 两个事件，顺序固定 TOPUP → GRANT"""
        prev = snap(total=100.0, granted=0.0, topped_up=100.0)
        curr = snap(total=250.0, granted=50.0, topped_up=200.0)
        assert detect_events(prev, curr, 10.0, True) == [
            (KIND_TOPUP, 100.0),
            (KIND_GRANT, 50.0),
        ]

    def test_tiny_delta_ignored(self):
        """一分钱以内视为浮点噪声"""
        prev = snap(total=100.0, granted=0.0, topped_up=100.0)
        curr = snap(total=100.004, granted=0.004, topped_up=100.004)
        assert detect_events(prev, curr, 10.0, True) == []

    def test_no_change_no_events(self):
        """余额没变 → 无事件"""
        prev = snap(total=33.23, granted=0.0, topped_up=33.23)
        curr = snap(total=33.23, granted=0.0, topped_up=33.23)
        assert detect_events(prev, curr, 10.0, True) == []

    @pytest.mark.parametrize("armed,expected", [
        (True, [(KIND_LOW, 8.32)]),
        (False, []),
    ])
    def test_cross_low_threshold(self, armed, expected):
        """跌破阈值：只有武装状态才报 LOW"""
        prev = snap(total=50.0, topped_up=50.0)
        curr = snap(total=8.32, topped_up=8.32)
        assert detect_events(prev, curr, 10.0, armed) == expected

    def test_unavailable_reports_low_without_duplicate(self):
        """账户不可用 → 报 LOW（与武装/阈值无关），且不会重复报两条"""
        prev = snap(total=100.0)
        curr = snap(total=100.0, available=False)
        events = detect_events(prev, curr, 10.0, False)
        assert events == [(KIND_LOW, 100.0)]

        # 低余额 + 不可用 → 仍然只有一条 LOW
        curr_low = snap(total=5.0, topped_up=5.0, available=False)
        events = detect_events(None, curr_low, 10.0, True)
        assert [k for k, _ in events] == [KIND_LOW]


# ─── 3. 播报文本 ────────────────────────────────────────


class TestFormatAlertText:
    def test_topup_text(self):
        assert format_alert_text(KIND_TOPUP, 100.0, 133.23) == (
            "DeepSeek 账户充值额度增加 ¥100.00，当前余额 ¥133.23。"
        )

    def test_grant_text(self):
        assert format_alert_text(KIND_GRANT, 50.0, 183.23) == (
            "DeepSeek 账户收到赠送额度 ¥50.00，当前余额 ¥183.23。"
        )

    def test_low_text(self, monkeypatch):
        """LOW 的阈值取 config，当前余额取参数，金额两位小数"""
        monkeypatch.setattr(config, "balance_low_threshold", 10.0)
        assert format_alert_text(KIND_LOW, 8.32, 8.32) == (
            "DeepSeek 账户余额不足 ¥10.00，当前仅剩 ¥8.32，请及时充值。"
        )

    def test_amounts_rounded_to_two_decimals(self):
        """显示层也要两位小数（不是只依赖 parse 的 round）"""
        assert format_alert_text(KIND_TOPUP, 99.999, 133.234) == (
            "DeepSeek 账户充值额度增加 ¥100.00，当前余额 ¥133.23。"
        )


# ─── 4. 数据库往返 ──────────────────────────────────────


class TestBalanceDatabase:
    def test_save_and_get_roundtrip(self):
        assert get_last_balance_snapshot("CNY") is None

        row_id = save_balance_snapshot("CNY", 33.23, 0.0, 33.23, True)
        assert row_id > 0

        row = get_last_balance_snapshot("CNY")
        assert row is not None
        assert row["currency"] == "CNY"
        assert row["total_balance"] == pytest.approx(33.23)
        assert row["granted_balance"] == pytest.approx(0.0)
        assert row["topped_up_balance"] == pytest.approx(33.23)
        assert row["is_available"] == 1
        assert row["created_at"]

    def test_get_last_returns_latest(self):
        """按 id 倒序取最近一条，且不同币种互不干扰"""
        save_balance_snapshot("CNY", 10.0, 0.0, 10.0, True)
        save_balance_snapshot("CNY", 20.0, 0.0, 20.0, True)
        save_balance_snapshot("USD", 5.0, 0.0, 5.0, True)

        assert get_last_balance_snapshot("CNY")["total_balance"] == 20.0
        assert get_last_balance_snapshot("USD")["total_balance"] == 5.0

    def test_cleanup_removes_old_keeps_new(self):
        """删旧留新，返回删除行数"""
        conn = get_conn()
        conn.execute(
            "INSERT INTO deepseek_balance "
            "(currency, total_balance, granted_balance, topped_up_balance, "
            " is_available, created_at) "
            "VALUES ('CNY', 1.0, 0.0, 1.0, 1, datetime('now', '-100 days'))"
        )
        conn.commit()
        save_balance_snapshot("CNY", 50.0, 0.0, 50.0, True)

        assert cleanup_balance_snapshots(keep_days=90) == 1

        rows = conn.execute(
            "SELECT total_balance FROM deepseek_balance ORDER BY id"
        ).fetchall()
        assert [r["total_balance"] for r in rows] == [50.0]


# ─── 5. 抓取 ────────────────────────────────────────────


class TestFetchBalance:
    @pytest.mark.asyncio
    async def test_success(self, httpx_mock, monkeypatch):
        monkeypatch.setattr(config, "llm_provider", "deepseek")
        monkeypatch.setattr(config, "deepseek_api_key", "sk-test")
        httpx_mock.add_response(url=DEFAULT_BALANCE_URL, json=CNY_PAYLOAD)

        s = await fetch_balance()
        assert s is not None
        assert s.total == 33.23
        assert s.is_available is True
        assert s.created_at  # 抓取时填上时间戳

    @pytest.mark.asyncio
    async def test_no_api_key(self, monkeypatch):
        """没配 key → None，且不发请求"""
        monkeypatch.setattr(config, "llm_provider", "deepseek")
        monkeypatch.setattr(config, "deepseek_api_key", "")
        assert await fetch_balance() is None

    @pytest.mark.asyncio
    async def test_gemini_provider(self, monkeypatch):
        """Gemini 没有余额接口 → None，且不发请求"""
        monkeypatch.setattr(config, "llm_provider", "gemini")
        monkeypatch.setattr(config, "gemini_api_key", "g-test")
        assert await fetch_balance() is None

    @pytest.mark.asyncio
    async def test_http_error(self, httpx_mock, monkeypatch):
        monkeypatch.setattr(config, "llm_provider", "deepseek")
        monkeypatch.setattr(config, "deepseek_api_key", "sk-test")
        httpx_mock.add_response(url=DEFAULT_BALANCE_URL, status_code=500)
        assert await fetch_balance() is None

    @pytest.mark.asyncio
    async def test_network_error(self, httpx_mock, monkeypatch):
        """网络异常也返回 None，不抛"""
        monkeypatch.setattr(config, "llm_provider", "deepseek")
        monkeypatch.setattr(config, "deepseek_api_key", "sk-test")
        httpx_mock.add_exception(
            httpx.ConnectError("连不上"), url=DEFAULT_BALANCE_URL
        )
        assert await fetch_balance() is None


# ─── 6. 等待充值状态 ────────────────────────────────────


class TestNeedsFastPoll:
    def test_no_history_is_false(self):
        """库里没有历史快照 → 不进入等待充值状态"""
        assert needs_fast_poll() is False

    def test_low_history_is_true(self):
        """上次快照就已低于阈值 ⇒ 视为已提醒过 → 进入等待充值状态"""
        save_balance_snapshot("CNY", 5.0, 0.0, 5.0, True)
        reset_state_for_tests()
        assert needs_fast_poll() is True

    def test_high_history_is_false(self):
        save_balance_snapshot("CNY", 100.0, 0.0, 100.0, True)
        reset_state_for_tests()
        assert needs_fast_poll() is False

    def test_reset_clears_memory_state(self):
        """reset 后状态重新按 DB 判定；库清空则回到初始值"""
        save_balance_snapshot("CNY", 5.0, 0.0, 5.0, True)
        reset_state_for_tests()
        assert needs_fast_poll() is True

        get_conn().execute("DELETE FROM deepseek_balance")
        get_conn().commit()
        reset_state_for_tests()
        assert needs_fast_poll() is False


# ─── 7. 轮询编排 ────────────────────────────────────────


class TestPollBalance:
    @pytest.mark.asyncio
    async def test_disabled_does_nothing(self, monkeypatch, fake_messaging):
        """开关关闭 → 连接口都不打"""
        monkeypatch.setattr(config, "balance_alert_enabled", False)
        called = []

        async def fake_fetch():
            called.append(1)
            return snap()

        monkeypatch.setattr(balance_mod, "fetch_balance", fake_fetch)

        bot = _FakeBot()
        await poll_balance(bot)
        assert called == []
        assert bot.sent == []

    @pytest.mark.asyncio
    async def test_fetch_failure_never_alerts(self, monkeypatch, fake_messaging):
        """取不到余额 → 只记日志，不播报、不落库（宁可没消息也不报假消息）"""
        monkeypatch.setattr(config, "balance_alert_enabled", True)

        async def fake_fetch():
            return None

        monkeypatch.setattr(balance_mod, "fetch_balance", fake_fetch)

        bot = _FakeBot()
        await poll_balance(bot)
        assert bot.sent == []
        assert get_last_balance_snapshot("CNY") is None

    @pytest.mark.asyncio
    async def test_topup_alerts_and_saves(self, monkeypatch, fake_messaging):
        """充值事件：@Sensei + 固定文本，然后落库"""
        monkeypatch.setattr(config, "balance_alert_enabled", True)
        monkeypatch.setattr(config, "balance_kei_comment", False)
        monkeypatch.setattr(config, "balance_alert_group_id", 123456)
        monkeypatch.setattr(config, "balance_low_threshold", 10.0)

        save_balance_snapshot("CNY", 23.23, 0.0, 23.23, True)
        reset_state_for_tests()

        async def fake_fetch():
            return snap(total=133.23, granted=0.0, topped_up=133.23)

        monkeypatch.setattr(balance_mod, "fetch_balance", fake_fetch)

        bot = _FakeBot()
        await poll_balance(bot)

        assert len(bot.sent) == 1
        group_id, message = bot.sent[0]
        assert group_id == 123456
        # 第一条段是 @Sensei，随后是固定文本（前面有一个空格）
        assert message[0].type == "at"
        assert message[0].data == SENSEI_QQ
        assert message.plain_text == "\nDeepSeek 账户充值额度增加 ¥110.00，当前余额 ¥133.23。"

        row = get_last_balance_snapshot("CNY")
        assert row["total_balance"] == pytest.approx(133.23)
        assert needs_fast_poll() is False

    @pytest.mark.asyncio
    async def test_low_alerted_once_and_fast_poll(self, monkeypatch, fake_messaging):
        """跌破阈值只报一次；之后 needs_fast_poll 为真"""
        monkeypatch.setattr(config, "balance_alert_enabled", True)
        monkeypatch.setattr(config, "balance_kei_comment", False)
        monkeypatch.setattr(config, "balance_alert_group_id", 123456)
        monkeypatch.setattr(config, "balance_low_threshold", 10.0)
        monkeypatch.setattr(balance_mod, "ALERT_GAP_SECONDS", 0)

        save_balance_snapshot("CNY", 50.0, 0.0, 50.0, True)
        reset_state_for_tests()

        async def fake_fetch():
            return snap(total=8.32, granted=0.0, topped_up=8.32)

        monkeypatch.setattr(balance_mod, "fetch_balance", fake_fetch)

        bot = _FakeBot()
        await poll_balance(bot)
        assert len(bot.sent) == 1
        assert "余额不足 ¥10.00" in bot.sent[0][1].plain_text
        assert needs_fast_poll() is True

        # 余额没变 → 不重复提醒
        await poll_balance(bot)
        assert len(bot.sent) == 1

    @pytest.mark.asyncio
    async def test_topup_after_low_rearms(self, monkeypatch, fake_messaging):
        """从低余额充值回来 → 报 TOPUP + 解除等待充值状态"""
        monkeypatch.setattr(config, "balance_alert_enabled", True)
        monkeypatch.setattr(config, "balance_kei_comment", False)
        monkeypatch.setattr(config, "balance_low_threshold", 10.0)

        save_balance_snapshot("CNY", 8.32, 0.0, 8.32, True)
        reset_state_for_tests()

        async def fake_fetch():
            return snap(total=108.32, granted=0.0, topped_up=108.32)

        monkeypatch.setattr(balance_mod, "fetch_balance", fake_fetch)

        bot = _FakeBot()
        await poll_balance(bot)

        assert len(bot.sent) == 1
        assert "充值额度增加 ¥100.00" in bot.sent[0][1].plain_text
        assert needs_fast_poll() is False

    @pytest.mark.asyncio
    async def test_comment_is_sent_separately(self, monkeypatch, fake_messaging):
        """Kei 的点评单独一条、不带 @"""
        monkeypatch.setattr(config, "balance_alert_enabled", True)
        monkeypatch.setattr(config, "balance_kei_comment", True)
        monkeypatch.setattr(config, "balance_low_threshold", 10.0)

        save_balance_snapshot("CNY", 100.0, 0.0, 100.0, True)
        reset_state_for_tests()

        async def fake_fetch():
            return snap(total=150.0, granted=0.0, topped_up=150.0)

        monkeypatch.setattr(balance_mod, "fetch_balance", fake_fetch)

        calls = []

        async def comment_fn(kind, amount, balance):
            calls.append((kind, amount, balance))
            return "老师，钱到账了哦。"

        bot = _FakeBot()
        await poll_balance(bot, comment_fn=comment_fn)

        assert calls == [(KIND_TOPUP, 50.0, 150.0)]
        assert len(bot.sent) == 2
        # 第一条带 @，第二条纯文本
        assert bot.sent[0][1][0].type == "at"
        assert bot.sent[1][1][0].type == "text"
        assert bot.sent[1][1].plain_text == "老师，钱到账了哦。"

    @pytest.mark.asyncio
    async def test_comment_can_be_multiple_messages(self, monkeypatch, fake_messaging):
        """comment_fn 返回列表时逐条发送（她可以像聊天那样分两三句说）"""
        monkeypatch.setattr(config, "balance_alert_enabled", True)
        monkeypatch.setattr(config, "balance_kei_comment", True)
        monkeypatch.setattr(config, "balance_low_threshold", 10.0)
        # 测试里别真等 1.5 秒
        monkeypatch.setattr(balance_mod, "COMMENT_GAP_SECONDS", 0)

        save_balance_snapshot("CNY", 100.0, 0.0, 100.0, True)
        reset_state_for_tests()

        async def fake_fetch():
            return snap(total=150.0, granted=0.0, topped_up=150.0)

        monkeypatch.setattr(balance_mod, "fetch_balance", fake_fetch)

        async def comment_fn(kind, amount, balance):
            return ["诶？多了钱……", "是老师充的吗……谢谢"]

        bot = _FakeBot()
        await poll_balance(bot, comment_fn=comment_fn)

        # 1 条固定播报 + 2 条旁白
        assert len(bot.sent) == 3
        assert bot.sent[0][1][0].type == "at"
        assert bot.sent[1][1].plain_text == "诶？多了钱……"
        assert bot.sent[2][1].plain_text == "是老师充的吗……谢谢"
        # 旁白都不带 @
        assert bot.sent[1][1][0].type == "text"
        assert bot.sent[2][1][0].type == "text"

    @pytest.mark.asyncio
    async def test_comment_empty_list_sends_nothing(self, monkeypatch, fake_messaging):
        """返回空列表 = 这次不说话"""
        monkeypatch.setattr(config, "balance_alert_enabled", True)
        monkeypatch.setattr(config, "balance_kei_comment", True)
        monkeypatch.setattr(config, "balance_low_threshold", 10.0)

        save_balance_snapshot("CNY", 100.0, 0.0, 100.0, True)
        reset_state_for_tests()

        async def fake_fetch():
            return snap(total=150.0, granted=0.0, topped_up=150.0)

        monkeypatch.setattr(balance_mod, "fetch_balance", fake_fetch)

        async def comment_fn(kind, amount, balance):
            return []

        bot = _FakeBot()
        await poll_balance(bot, comment_fn=comment_fn)

        assert len(bot.sent) == 1     # 只有固定播报
        assert bot.sent[0][1][0].type == "at"

    @pytest.mark.asyncio
    async def test_comment_switch_off(self, monkeypatch, fake_messaging):
        """config.balance_kei_comment 为假 → 不调用 comment_fn"""
        monkeypatch.setattr(config, "balance_alert_enabled", True)
        monkeypatch.setattr(config, "balance_kei_comment", False)

        save_balance_snapshot("CNY", 100.0, 0.0, 100.0, True)
        reset_state_for_tests()

        async def fake_fetch():
            return snap(total=150.0, granted=0.0, topped_up=150.0)

        monkeypatch.setattr(balance_mod, "fetch_balance", fake_fetch)

        called = []

        async def comment_fn(kind, amount, balance):
            called.append(1)
            return "不该被调用"

        bot = _FakeBot()
        await poll_balance(bot, comment_fn=comment_fn)
        assert called == []
        assert len(bot.sent) == 1

    @pytest.mark.asyncio
    async def test_never_raises(self, monkeypatch, fake_messaging):
        """内部异常绝不外抛（fetch 抛错 / 发送失败都要吞掉）"""
        monkeypatch.setattr(config, "balance_alert_enabled", True)
        monkeypatch.setattr(config, "balance_kei_comment", False)

        async def boom():
            raise RuntimeError("接口炸了")

        monkeypatch.setattr(balance_mod, "fetch_balance", boom)
        await poll_balance(_FakeBot())  # 不抛异常即通过

        # 发送失败也不能中断 —— 快照照样要落库
        save_balance_snapshot("CNY", 50.0, 0.0, 50.0, True)
        reset_state_for_tests()

        async def low_fetch():
            return snap(total=8.32, granted=0.0, topped_up=8.32)

        monkeypatch.setattr(balance_mod, "fetch_balance", low_fetch)
        monkeypatch.setattr(config, "balance_low_threshold", 10.0)
        await poll_balance(_FailingBot())

        assert get_last_balance_snapshot("CNY")["total_balance"] == pytest.approx(8.32)


class TestAlertMessageShape:
    """播报消息的形态：@ 之后换行，正文独占一行"""

    def test_newline_after_at(self, fake_messaging):
        """@ 段 + 文本段，文本段以换行开头

        只断言可观察的结果（段类型、纯文本以换行开头），不碰 segment 的 data 结构 ——
        测试里的 _FakeMessageSegment 和真实适配器字段形状不完全一致。
        """
        msg = _build_alert_message("测试正文。")
        assert len(msg) == 2
        assert [s.type for s in msg] == ["at", "text"]
        assert msg[0].data == str(SENSEI_QQ)
        # 关键：换行而不是空格 —— 挤在一行像系统通知
        assert msg[1].data == "\n测试正文。"
        assert msg.plain_text.startswith("\n")

    def test_text_body_is_untouched(self, fake_messaging):
        """format_alert_text 不带 @，也不带换行前缀（都由 _build_alert_message 加）"""
        text = format_alert_text(KIND_TOPUP, 9.99, 41.29)
        assert not text.startswith("\n")
        assert not text.startswith("@")
        assert " ¥9.99" in text
