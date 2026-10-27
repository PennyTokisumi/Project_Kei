"""帮助清单一致性：Sensei 专用指令不能出现在公开的 `help` 里

背景：`balance` 是仅 Sensei 可用的指令，曾经被写进了公开 `help`，
普通人也能看到、尝试后发现没权限，体验很差。

这条规则靠人自觉很容易再犯，所以直接用测试锁死：
- 公开 `help` 里不能出现任何 Sensei 专用指令
- 公开指令必须出现在 `help` 里
- Sensei 专用指令必须出现在 `sensei` 的隐藏清单里
- 公开指令在 `sensei` 里也应完整可见（那是"全部指令"）

实现方式：直接解析两个 handler 里的字面量列表（不 import，避免插件加载副作用）。
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ADMIN = ROOT / "bot/plugins/admin/commands.py"
LLM_CHAT = ROOT / "bot/plugins/llm_chat/__init__.py"

# 仅 Sensei 可用的指令（改权限时必须同步改这里）
SENSEI_ONLY = {
    "balance", "Claude", "KEI", "LLM", "read", "history",
    "memory", "remember", "edit", "imp", "forget",
}

# 全部公开指令
PUBLIC = {
    "help", "status", "chat", "kawaii", "hello",
    "list", "pixivlist", "remove", "add", "remind", "daily",
}


def _extract_list(src_path: Path, start_marker: str, end_marker: str) -> str:
    """截出 handler 里 `lines = [...]` 那段源码"""
    src = src_path.read_text(encoding="utf-8")
    start = src.index(start_marker)
    # 用最后一次出现作为收尾，避免命中函数开头的守卫语句
    end = src.rindex(end_marker)
    return src[start:end]


def _commands(text: str) -> set[str]:
    """取出清单里每行开头的指令名（形如 `"status  -  ..."`）"""
    return {m.group(1) for m in re.finditer(r'^\s*"([A-Za-z]+)', text, re.M)}


@pytest.fixture(scope="module")
def public_help() -> set[str]:
    return _commands(_extract_list(ADMIN, "async def handle_help", "await help_cmd.finish"))


@pytest.fixture(scope="module")
def sensei_help() -> set[str]:
    src = LLM_CHAT.read_text(encoding="utf-8")
    start = src.index("async def handle_sensei")
    end = src.rindex("await sensei_cmd.finish")
    return _commands(src[start:end])


class TestPublicHelp:
    def test_no_sensei_only_commands(self, public_help):
        """公开 help 里不能出现任何 Sensei 专用指令"""
        leaked = public_help & SENSEI_ONLY
        assert not leaked, f"这些 Sensei 专用指令泄漏到了公开 help：{sorted(leaked)}"

    def test_balance_is_not_public(self, public_help):
        """balance 是仅 Sensei 可用的，绝不能出现在公开 help"""
        assert "balance" not in public_help

    def test_public_commands_all_listed(self, public_help):
        """公开指令必须都能在 help 里查到"""
        missing = PUBLIC - public_help
        assert not missing, f"这些公开指令没有出现在 help 里：{sorted(missing)}"

    def test_remove_notes_admin_permission(self):
        """remove 需要群主/管理员，help 里要写清楚，否则普通人会白试一次"""
        text = _extract_list(ADMIN, "async def handle_help", "await help_cmd.finish")
        assert text.count("群主/管理员") >= 2


class TestSenseiHelp:
    def test_contains_all_sensei_only(self, sensei_help):
        """Sensei 的隐藏清单要覆盖全部专用指令（漏了等于没法查）"""
        missing = SENSEI_ONLY - sensei_help
        assert not missing, f"这些 Sensei 专用指令没出现在 sensei 清单里：{sorted(missing)}"

    def test_balance_is_listed(self, sensei_help):
        assert "balance" in sensei_help

    def test_contains_all_public(self, sensei_help):
        """sensei 展示的是全部指令，公开指令也应在"""
        missing = PUBLIC - sensei_help
        assert not missing, f"这些公开指令没出现在 sensei 清单里：{sorted(missing)}"
