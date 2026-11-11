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


def _setup_logging() -> None:
    """把日志同时写进 data/logs/bot.log

    start.bat 用 VBS 隐藏窗口启动，控制台日志等于没有 —— 出问题只能靠猜。
    这也是性能排查的第一步：没有时间戳就分不清慢在哪一段。

    ⚠️ NoneBot 的 `logger` 是 **loguru**，不是标准库 logging：
    用 `logger.add(...)` 而不是 `addHandler(...)`（后者根本没有这个方法）。
    """
    import config

    try:
        log_dir = config.DATA_DIR / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        nonebot.logger.add(
            str(log_dir / "bot.log"),
            level="INFO",
            encoding="utf-8",
            rotation="5 MB",
            retention=3,
            enqueue=True,  # 后台线程写入，不阻塞事件循环
            format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <7} | {name}:{function}:{line} - {message}",
        )
        nonebot.logger.info("日志已落盘: " + str(log_dir / "bot.log"))
    except Exception as exc:  # noqa: BLE001 — 日志失败不能拖垮启动
        print(f"初始化文件日志失败: {exc!r}")


def _setup_latency_probe() -> None:
    """记录每个事件进入 NoneBot 的时刻

    用途：和 SnowLuma 日志里的 `[Event]` / `[Bridge.Action]` 时间戳对齐，
    判断「命令→回复」的几秒钟到底花在哪个环节。

    loguru 用 `{}` 占位而不是 `%s`，所以这里统一用 f-string，别混着写。
    """
    import time

    from nonebot.message import event_preprocessor

    @event_preprocessor
    async def _log_event_arrival(event) -> None:
        try:
            plain = ""
            getter = getattr(event, "get_plaintext", None)
            if callable(getter):
                plain = getter()[:40]
            nonebot.logger.info(
                f"[latency] recv mono={time.monotonic():.3f} "
                f"type={event.get_type()} plain={plain!r}"
            )
        except Exception:  # noqa: BLE001 — 探针绝不影响主流程
            pass


def _setup_loop_watchdog(interval: float = 0.2, threshold: float = 0.3) -> None:
    """事件循环卡顿探针

    每 200ms 醒一次，记录「实际间隔 − 预期间隔」。asyncio 里 await 网络不会阻塞循环，
    所以这个差值一旦变大，就说明有**同步代码**占住了线程 —— 那才是让所有指令一起变慢的元凶。

    只打印超过 300ms 的卡顿，平时完全安静。
    """
    import asyncio
    import time

    driver = nonebot.get_driver()

    @driver.on_startup
    async def _start_watchdog() -> None:
        async def _beat() -> None:
            last = time.monotonic()
            while True:
                await asyncio.sleep(interval)
                now = time.monotonic()
                lag = now - last - interval
                if lag >= threshold:
                    nonebot.logger.warning(
                        f"[watchdog] 事件循环卡顿 {lag * 1000:.0f} ms"
                    )
                last = now

        asyncio.create_task(_beat())
        nonebot.logger.info(
            f"事件循环探针已启动（每 {interval * 1000:.0f} ms，超过 "
            f"{threshold * 1000:.0f} ms 才报）"
        )


def main():
    """初始化 NoneBot 并运行"""
    _check_running()
    nonebot.init()
    _setup_logging()
    _setup_latency_probe()
    _setup_loop_watchdog()

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
