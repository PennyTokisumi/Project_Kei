"""NapCatQQ 子进程管理 — 统一生命周期"""

import os
import sys
import subprocess
from pathlib import Path

_proc = None


def launch():
    """启动 NapCatQQ 子进程（由 bot.py 在启动时调用）"""
    global _proc
    # bot/napcat.py → bot/ → 项目根
    root = Path(__file__).resolve().parent.parent
    dirs = list((root / "napcat").glob("NapCat.*.Shell"))
    if not dirs:
        print("⚠ 未找到 NapCat 目录")
        return
    nc_dir = dirs[0]
    bat = nc_dir / "napcat.bat"
    if not bat.exists():
        print(f"⚠ 未找到 {bat}")
        return

    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    _proc = subprocess.Popen(
        ["cmd", "/c", str(bat)],
        cwd=str(nc_dir),
        creationflags=flags,
    )
    print(f"NapCatQQ 已启动 (PID={_proc.pid})")


def shutdown():
    """关闭 NapCatQQ 子进程（由托盘/关机流程调用）"""
    global _proc
    if _proc is None:
        return
    try:
        if _proc.poll() is not None:
            print("NapCatQQ 已退出")
            return

        if sys.platform == "win32":
            # Windows: taskkill /T 杀整个进程树
            subprocess.run(
                ["taskkill", "/PID", str(_proc.pid), "/T", "/F"],
                capture_output=True,
            )
        else:
            _proc.terminate()
            try:
                _proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _proc.kill()
                _proc.wait(timeout=3)
        print("NapCatQQ 已关闭")
    except Exception as e:
        print(f"关闭 NapCat 失败: {e}")
