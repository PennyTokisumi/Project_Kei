"""QQ 群监测机器人 - NoneBot2 启动入口"""

import os
import sys

import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter


def _is_python_exe(path: str) -> bool:
    """映像路径是否是 Python 解释器（判断双开时用）"""
    name = os.path.basename(path or "").lower()
    return ".exe" in name and "python" in name


def _pid_is_running_python(pid: int) -> bool:
    """该 PID 是否**确实**是一个还在运行的 Python（也就是我们自己）

    只用 ``OpenProcess`` 判断"这个号码打开了"是不够的 —— Windows 会复用 PID。
    2026-11-08 就踩过：``.pid`` 里记的 28936 重启后被 Chrome 占用，
    于是 bot.py 永远认为「Kei 已在运行」，拒绝启动，而 SnowLuma 照常起来，
    表现成"只有 SL 起来了"。
    """
    import ctypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        size = ctypes.c_ulong(32768)
        buf = ctypes.create_unicode_buffer(size.value)
        ok = kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
        if not ok:
            # 查不到映像名（权限不足等）→ 保守当作「不确定」，按残留处理
            return False
        return _is_python_exe(buf.value)
    finally:
        kernel32.CloseHandle(handle)


def _check_running() -> None:
    """重复启动检测

    注意：判断的是「那个 PID 现在是不是**我们的 python**」，
    而不是「那个 PID 号码有没有被占用」—— 号码会被系统复用。
    """
    import config
    pid_file = config.DATA_DIR / ".pid"
    try:
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        if pid_file.exists():
            old_pid = pid_file.read_text().strip()
            if _pid_is_running_python(old_pid):
                print(f"Kei 已在运行中 (PID={old_pid})，拒绝重复启动。")
                (config.DATA_DIR / ".startup_ok").write_text("DUPLICATE")
                sys.exit(1)
            # 旧进程已退出，或 PID 被别的程序复用了 → 清理残留
            print(f"清理残留的 .pid（{old_pid} 已不是本程序的进程）")
            pid_file.unlink(missing_ok=True)
        pid_file.write_text(str(os.getpid()))
    except Exception:
        pass


def main():
    """初始化 NoneBot 并运行"""
    _check_running()
    nonebot.init()

    # 注册 OneBot v11 适配器
    driver = nonebot.get_driver()
    driver.register_adapter(OneBotV11Adapter)

    # 加载插件
    nonebot.load_plugin("plugins.monitor")
    nonebot.load_plugin("plugins.admin")
    nonebot.load_plugin("plugins.llm_chat")
    nonebot.load_plugin("plugins.agent")

    nonebot.run()


if __name__ == "__main__":
    main()
