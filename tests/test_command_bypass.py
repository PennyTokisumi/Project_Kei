"""指令与 LLM 的边界守卫

两条不变量：
1. **指令不进 LLM**：`COMMAND_PREFIXES` 必须覆盖所有实际注册的指令前缀，
   而且不能有冗余项 —— 新增指令忘了登记时这里会红。
2. **thinking 只作用于聊天**：非聊天调用点必须显式 `enable_thinking=False`
   （见 `test_thinking_scoping.py`）。

这里只管第 1 条。
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# 注册指令的 3 个文件（utils.py 里也有 startswith("http")，是误报，不扫）
COMMAND_FILES = [
    ROOT / "bot/plugins/admin/commands.py",
    ROOT / "bot/plugins/llm_chat/__init__.py",
    ROOT / "bot/plugins/agent/__init__.py",
]

_PREFIX_RE = re.compile(r'startswith\(\s*"([A-Za-z]+)"')


def registered_prefixes() -> set[str]:
    found = set()
    for path in COMMAND_FILES:
        for m in _PREFIX_RE.finditer(path.read_text(encoding="utf-8")):
            found.add(m.group(1).lower())
    return found


ALL_PREFIXES = registered_prefixes()


def test_scan_found_a_reasonable_number():
    """扫描兜底：解析一旦失效（比如注册方式改了），先在这里红，别悄悄变空"""
    assert len(ALL_PREFIXES) >= 15, f"只扫到 {len(ALL_PREFIXES)} 个前缀，解析可能失效"


def test_every_registered_command_is_gated():
    """所有注册的指令都必须在 COMMAND_PREFIXES 里 —— 否则会被 LLM 抢答"""
    from plugins.llm_chat import COMMAND_PREFIXES

    gated = {p.lower() for p in COMMAND_PREFIXES}
    missing = ALL_PREFIXES - gated
    assert not missing, (
        f"这些指令没登记进 COMMAND_PREFIXES，会被 LLM 处理器接走：{sorted(missing)}"
    )


def test_no_stale_entries_in_command_prefixes():
    """反向检查：清单里不能有已经不存在的指令（避免误伤正常聊天）"""
    from plugins.llm_chat import COMMAND_PREFIXES

    gated = {p.lower() for p in COMMAND_PREFIXES}
    stale = gated - ALL_PREFIXES
    assert not stale, f"COMMAND_PREFIXES 里有过期条目：{sorted(stale)}"


class TestLooksLikeCommand:
    @pytest.mark.parametrize("text", [
        "status", "status 看看", "balance", "help", "list", "pixivlist 2",
        "add bilibili_live 123", "remove 3", "hello ON", "chat", "kawaii",
        "KEI ON", "kei off", "LLM", "memory", "remember 0.9 某事",
        "edit 1 新内容", "imp 2 0.8", "forget 3", "sensei", "history 50",
        "Claude 帮我看看", "claude 帮我看看", "remind 2200 睡觉", "daily 0900 早",
    ])
    def test_commands_are_gated(self, text):
        from plugins.llm_chat import _looks_like_command
        assert _looks_like_command(text) is True

    @pytest.mark.parametrize("text", [
        "今天好累啊", "你觉得我今天加班正常吗", "提醒我明天早起",
        "statusbar 这个词算不算指令",  # 前缀式匹配的既有行为：算
    ])
    def test_prefix_semantics(self, text):
        """`startswith` 是前缀匹配，不是单词边界 —— 与各插件注册口径一致"""
        from plugins.llm_chat import _looks_like_command
        expected = not text.startswith("今天") and not text.startswith("你觉得") \
            and not text.startswith("提醒我")
        assert _looks_like_command(text) is expected
