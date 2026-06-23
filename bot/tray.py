"""系统托盘图标 - 显示机器人运行状态，右键菜单管理"""

import logging
import os
import threading
from pathlib import Path

from PIL import Image, ImageDraw

try:
    import pystray
except ImportError:
    pystray = None

logger = logging.getLogger("tray")

# ─── Windows 开机自启动 ────────────────────────────────────────
STARTUP_NAME = "QQ_Monitor_Bot.lnk"


def _get_startup_dir() -> Path:
    """获取 Windows 启动文件夹路径"""
    return Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / \
        "Start Menu" / "Programs" / "Startup"


def _get_shortcut_target() -> Path:
    """获取项目 start.bat 的绝对路径"""
    # tray.py 在 bot/ 下，项目根目录在上级
    return Path(__file__).resolve().parent.parent / "start.bat"


def is_autostart_enabled() -> bool:
    """检查是否已设置开机自启"""
    shortcut = _get_startup_dir() / STARTUP_NAME
    return shortcut.exists()


def set_autostart(enable: bool):
    """设置或取消 Windows 开机自启

    在启动文件夹创建/删除 start.bat 的快捷方式。
    """
    shortcut = _get_startup_dir() / STARTUP_NAME

    if enable:
        _create_shortcut(shortcut, _get_shortcut_target())
        logger.info(f"已设置开机自启: {shortcut}")
    else:
        if shortcut.exists():
            shortcut.unlink()
            logger.info(f"已取消开机自启: {shortcut}")


def _create_shortcut(shortcut_path: Path, target: Path):
    """通过 VBScript 创建 Windows .lnk 快捷方式"""
    vbs = f'''
Set WshShell = WScript.CreateObject("WScript.Shell")
Set link = WshShell.CreateShortcut("{shortcut_path}")
link.TargetPath = "{target}"
link.WorkingDirectory = "{target.parent}"
link.WindowStyle = 7
link.Description = "QQ_Monitor_Bot 启动"
link.Save
'''
    # 写入临时 vbs 并执行
    vbs_path = shortcut_path.with_suffix(".vbs.tmp")
    vbs_path.write_text(vbs, encoding="gbk")
    os.system(f'cscript //nologo "{vbs_path}"')
    vbs_path.unlink(missing_ok=True)


# ─── 状态信息 ──────────────────────────────────────────────────

_status = {
    "targets_total": 0,
    "alive": True,
}
_lock = threading.Lock()


def update_status(*, targets_total: int = None, alive: bool = None):
    """更新托盘状态信息（线程安全）"""
    with _lock:
        if targets_total is not None:
            _status["targets_total"] = targets_total
        if alive is not None:
            _status["alive"] = alive


def _get_tooltip() -> str:
    """生成悬停提示文字"""
    with _lock:
        total = _status["targets_total"]
        alive = _status["alive"]
    if alive:
        if total == 0:
            return "QQ_Monitor_Bot | 运行中 | 暂无监测目标"
        return f"QQ_Monitor_Bot | 运行中 | 监测: {total}个目标"
    else:
        return "QQ_Monitor_Bot | 异常 | 请检查日志"


def _create_icon(healthy: bool = True) -> Image.Image:
    """用 Pillow 生成托盘图标（绿点/黄点）

    绿点 = 一切正常，黄点 = 有异常
    """
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 绿点 或 黄点（警告色）
    color = (76, 175, 80, 255) if healthy else (255, 193, 7, 255)
    draw.ellipse([6, 6, size - 6, size - 6], fill=color)
    return img


# ─── 托盘图标 ──────────────────────────────────────────────────

class TrayIcon:
    """系统托盘图标管理器"""

    def __init__(self):
        self._icon: "pystray.Icon | None" = None
        self._thread: threading.Thread | None = None

    def _build_menu(self) -> "pystray.Menu":
        """动态构建右键菜单"""
        return pystray.Menu(
            pystray.MenuItem(
                "开机自启动",
                self._toggle_autostart,
                checked=lambda item: is_autostart_enabled(),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("关闭机器人", self._on_shutdown),
        )

    def start(self):
        """启动托盘图标（独立线程）"""
        if pystray is None:
            logger.warning("pystray 未安装，托盘图标不可用")
            return

        icon_image = _create_icon(healthy=True)
        self._icon = pystray.Icon(
            "QQ_Monitor_Bot",
            icon_image,
            title=_get_tooltip(),
        )
        self._icon.menu = self._build_menu()

        # 定时刷新菜单（响应自启状态变化）
        def run():
            import time as _time
            self._icon.run()

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()
        logger.info("托盘图标已启动")

    def stop(self):
        """停止托盘图标"""
        if self._icon:
            self._icon.stop()
            logger.info("托盘图标已停止")

    def _toggle_autostart(self, icon: "pystray.Icon", item):
        """切换开机自启"""
        new_state = not is_autostart_enabled()
        set_autostart(new_state)
        # 刷新菜单
        icon.menu = self._build_menu()

    def _on_shutdown(self, icon: "pystray.Icon", item):
        """右键关闭机器人"""
        icon.stop()
        # 发送 SIGINT 让 NoneBot2 优雅退出
        os._exit(0)


# 全局单例
tray = TrayIcon()
