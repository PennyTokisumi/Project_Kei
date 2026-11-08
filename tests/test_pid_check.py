"""双开检测必须认「进程身份」，不能只认 PID 号码

2026-11-08 事故：`.pid` 里存的 28936 在重启电脑后被 Windows 复用给了 Chrome，
旧代码只看 `OpenProcess` 成功就认定「Kei 已在运行」→ bot.py 直接退出，
而 start.bat 里的 SnowLuma 照常起来，表现成「只有 SL 起来了」。

注意：不能写 `import bot` —— 那会解析到 `bot/` 包（__init__.py）而不是入口脚本，
必须按文件路径加载 `bot/bot.py`。
"""

import importlib.util
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def entry():
    """按路径加载 bot/bot.py（模块名另起，避免与 bot 包冲突）"""
    path = os.path.join(ROOT, "bot", "bot.py")
    spec = importlib.util.spec_from_file_location("kei_entry_module", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestIsPythonExe:
    @pytest.mark.parametrize("path", [
        r"C:\Python312\python.exe",
        r"H:\Agent\Project\Project_Kei\.venv\Scripts\python.exe",
        r"C:\x\pythonw.exe",
        r"C:\x\PYTHON.EXE",          # 大小写不敏感
    ])
    def test_accepts_python(self, entry, path):
        assert entry._is_python_exe(path) is True

    @pytest.mark.parametrize("path", [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\nodejs\node.exe",
        r"C:\x\python",              # 没有 .exe
        "",
        None,
    ])
    def test_rejects_others(self, entry, path):
        assert entry._is_python_exe(path) is False


class TestPidIsRunningPython:
    def test_own_process_is_python(self, entry):
        """当前测试进程就是 python，应判定为「在跑」"""
        assert entry._pid_is_running_python(os.getpid()) is True

    @pytest.mark.parametrize("pid", [0, -1, 999999, "abc", None])
    def test_invalid_pid_is_false(self, entry, pid):
        """非法 / 不存在的 PID 一律当作「没在跑」，绝不能因此拒绝启动"""
        assert entry._pid_is_running_python(pid) is False

    def test_alive_but_not_python_is_false(self, entry, monkeypatch):
        """模拟 PID 复用：进程活着，但不是 python → 必须判定为残留

        这是 28936 那次事故的核心场景：号码被 Chrome 占了。
        """
        monkeypatch.setattr(entry, "_is_python_exe", lambda _p: False)
        assert entry._pid_is_running_python(os.getpid()) is False
