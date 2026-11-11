"""SSL 上下文复用（性能回归防线）

背景：httpx 每建一个 AsyncClient 都会重新加载 certifi 的 CA 包，实测单次 400~500 ms，
且**同步执行**，会卡住整个事件循环。项目里有 17 处 AsyncClient，曾经让
`@Kei status` 慢到 0.5~1.5 秒、watchdog 报警 847~5191 ms。

这里锁死：`utils.http.ssl_context()` 必须复用同一个对象，
`new_client()` 必须把它传给 httpx。
"""

import ssl

import pytest


class TestSslContextCached:
    def test_same_object_returned(self):
        from utils.http import ssl_context

        a = ssl_context()
        b = ssl_context()
        assert a is b, "SSL 上下文没有被复用 —— 每次新建会拖慢事件循环几百毫秒"

    def test_is_ssl_context(self):
        from utils.http import ssl_context

        assert isinstance(ssl_context(), ssl.SSLContext)


class TestNewClientPassesVerify:
    def test_verify_is_shared_context(self, monkeypatch):
        import utils.http as http_mod

        seen = {}

        class _Recorder:
            def __init__(self, **kwargs):
                seen.update(kwargs)

        monkeypatch.setattr(http_mod.httpx, "AsyncClient", _Recorder)
        http_mod.new_client(timeout=10)

        assert "verify" in seen, "new_client 没有传 verify，等于没复用上下文"
        assert seen["verify"] is http_mod.ssl_context()
        assert seen["timeout"] == 10

    def test_explicit_verify_wins(self, monkeypatch):
        """调用方显式给了 verify 就用它的（不要被默认值覆盖）"""
        import utils.http as http_mod

        seen = {}

        class _Recorder:
            def __init__(self, **kwargs):
                seen.update(kwargs)

        monkeypatch.setattr(http_mod.httpx, "AsyncClient", _Recorder)
        marker = object()
        http_mod.new_client(verify=marker)
        assert seen["verify"] is marker


class TestNoStrayAsyncClient:
    """防止有人新写代码时又直接用 httpx.AsyncClient，绕回复用"""

    def test_only_http_module_constructs_clients(self):
        import pathlib

        root = pathlib.Path(__file__).resolve().parent.parent / "bot"
        offenders = []
        for path in root.rglob("*.py"):
            if path.name == "http.py":
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for i, line in enumerate(text.splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if "httpx.AsyncClient(" in line or (
                    "AsyncClient(" in line and "new_client(" not in line
                ):
                    offenders.append(f"{path.name}:{i}: {stripped[:70]}")

        assert not offenders, (
            "发现直接用 httpx.AsyncClient 的地方，应该改用 utils.http.new_client：\n  "
            + "\n  ".join(offenders)
        )
