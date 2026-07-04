"""QQ 群监测机器人 - NoneBot2 启动入口"""

import os
import sys

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

    # 加载插件
    nonebot.load_plugin("plugins.monitor")
    nonebot.load_plugin("plugins.admin")
    nonebot.load_plugin("plugins.llm_chat")
    nonebot.load_plugin("plugins.agent")

    nonebot.run()


if __name__ == "__main__":
    main()
