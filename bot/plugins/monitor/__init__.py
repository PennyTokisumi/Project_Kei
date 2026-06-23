"""QQ_Monitor_Bot - B站动态/直播 + 斗鱼直播 监测推送插件

NoneBot2 插件入口，通过 driver.on_startup / on_shutdown 管理调度器生命周期。
"""

import logging

from nonebot import get_driver

from .scheduler import start, stop
from tray import tray, update_status

logger = logging.getLogger("monitor")
logger.setLevel(logging.INFO)

driver = get_driver()


@driver.on_startup
async def _on_startup():
    """Bot 启动时：初始化数据库 + 启动调度器 + 托盘图标"""
    logger.info("QQ_Monitor_Bot 启动中...")

    # 启动托盘图标（独立线程，失败不影响核心功能）
    try:
        tray.start()
    except Exception as e:
        logger.warning(f"托盘图标启动失败: {e}")
    update_status(alive=True)

    await start()
    logger.info("QQ_Monitor_Bot 启动完成")


@driver.on_shutdown
async def _on_shutdown():
    """Bot 关闭时：停止调度器 + 托盘图标"""
    logger.info("QQ_Monitor_Bot 关闭中...")
    update_status(alive=False)
    await stop()
    tray.stop()
    logger.info("QQ_Monitor_Bot 已关闭")
