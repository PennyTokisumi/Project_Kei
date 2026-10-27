"""thinking 作用域守卫：**只有聊天路径**可以继承 config 的 thinking 开关

背景（v2.7.3）：v2.7.0 把 `client.chat()` 的 `enable_thinking` 默认值改成「取 config」，
于是所有没显式传参的调用点都被 thinking 接管了。其中 `KEI ON` 的连通性检查只给
`max_tokens=8`，思考一开口就吃光额度 → `content` 为空 → 报「API 连接失败」。

实测（同一请求 × 8 次）：
- 继承 config（思考开） → **8/8 次 content 为空**
- 显式 `enable_thinking=False` → 0/8 次为空

这类回归靠人记不住，所以用源码级测试锁死：
**除聊天路径外，每个 `llm_client.chat(...)` 都必须显式写 `enable_thinking=`**。

新增调用点时如果忘了写，这里会直接红。
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FILES = [
    ROOT / "bot/plugins/llm_chat/__init__.py",
    ROOT / "bot/plugins/llm_chat/remember.py",
    ROOT / "bot/plugins/llm_chat/balance_comment.py",
    ROOT / "bot/plugins/agent/__init__.py",
]

# 只有这两条是「真正的对话」，允许继承 config 的 thinking 设置
CHAT_FUNCTIONS = {"handle_llm_at", "_flush_buffer"}

_CALL_RE = re.compile(r"llm_client\.chat\(")
_DEF_RE = re.compile(r"^\s*(?:async\s+)?def\s+(\w+)")


def _enclosing_function(lines: list[str], index: int) -> str:
    for i in range(index, -1, -1):
        m = _DEF_RE.match(lines[i])
        if m:
            return m.group(1)
    return "<module>"


def _call_block(lines: list[str], start: int) -> str:
    """取出从 `llm_client.chat(` 开始到括号配平为止的整段调用

    不能用固定行数窗口 —— 之前的审计脚本就是因为窗口太小而误报。
    """
    depth = 0
    block = []
    for i in range(start, min(start + 60, len(lines))):
        block.append(lines[i])
        depth += lines[i].count("(") - lines[i].count(")")
        if i > start and depth <= 0:
            break
    return " ".join(block)


def _iter_calls():
    for path in FILES:
        lines = path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if _CALL_RE.search(line):
                yield path.name, i + 1, _enclosing_function(lines, i), _call_block(lines, i)


ALL_CALLS = list(_iter_calls())


def test_found_expected_number_of_calls():
    """调用点数量兜底：文件被重构后如果这个测试先红，说明守卫需要跟着更新"""
    assert len(ALL_CALLS) >= 10, f"只找到 {len(ALL_CALLS)} 个调用点，检查解析是否失效"


@pytest.mark.parametrize("fname,lineno,func,block", ALL_CALLS,
                         ids=[f"{f}:{n}:{fn}" for f, n, fn, _ in ALL_CALLS])
def test_non_chat_calls_pin_enable_thinking(fname, lineno, func, block):
    """非聊天路径必须显式声明 enable_thinking，否则会被全局 thinking 设置接管"""
    if func in CHAT_FUNCTIONS:
        pytest.skip(f"{func} 是聊天路径，允许继承 config")
    assert "enable_thinking=" in block, (
        f"{fname}:{lineno} 的 {func}() 没有显式 enable_thinking —— "
        f"开 thinking 后思考会吃光它的 max_tokens，表现为「接口有响应但没内容」"
    )


def test_kei_enable_verification_disables_thinking():
    """KEI ON 的连通性检查只有 8 个 token，必须是关闭思考的"""
    for fname, lineno, func, block in ALL_CALLS:
        if func == "handle_kei_enable":
            assert "enable_thinking=False" in block
            return
    pytest.fail("没找到 handle_kei_enable 里的 llm_client.chat 调用")
