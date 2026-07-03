"""QQ 群监测机器人 - NoneBot2 启动入口"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter


def _check_running() -> None:
    """重复启动检测"""
    import config, ctypes
    pid_file = config.DATA_DIR / ".pid"
    try:
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        if pid_file.exists():
            old_pid = pid_file.read_text().strip()
            try:
                handle = ctypes.windll.kernel32.OpenProcess(0x0400, False, int(old_pid))
                if handle:
                    ctypes.windll.kernel32.CloseHandle(handle)
                    print(f"Kei 已在运行中 (PID={old_pid})，拒绝重复启动。")
                    (config.DATA_DIR / ".startup_ok").write_text("DUPLICATE")
                    sys.exit(1)
            except (ValueError, OSError):
                pass
            # 旧进程已退出，清理残留
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

    # 配置文件日志（RotatingFileHandler，单文件 1MB，保留 3 个备份）
    _setup_log_file()

    # 加载插件
    nonebot.load_plugin("plugins.monitor")
    nonebot.load_plugin("plugins.admin")
    nonebot.load_plugin("plugins.llm_chat")
    nonebot.load_plugin("plugins.agent")

    nonebot.run()


def _setup_log_file():
    """配置文件日志输出，相对于 PROJECT_ROOT 解析路径"""
    import config
    if not config.config.log_file:
        return

    log_path = Path(config.config.log_file)
    if not log_path.is_absolute():
        log_path = config.PROJECT_ROOT / log_path
    log_path.parent.mkdir(parents=True, exist_ok=True)

    handler = RotatingFileHandler(
        str(log_path), maxBytes=1 * 1024 * 1024, backupCount=3,
        encoding="utf-8",
    )
    handler.setLevel(getattr(logging, config.config.log_level.upper(), logging.INFO))
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))

    # 加到根 logger，捕获所有模块的日志
    root = logging.getLogger()
    root.addHandler(handler)
    # nonebot logger 可能不 propagate，直接加 handler
    logging.getLogger("nonebot").addHandler(handler)
    print(f"日志文件: {log_path}")


if __name__ == "__main__":
    main()
