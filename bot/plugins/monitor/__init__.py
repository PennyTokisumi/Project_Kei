"""QQ_Monitor_Bot - B站动态/直播 + 斗鱼直播 监测推送插件"""

from nonebot import get_driver, logger as nb_logger

from .scheduler import start, stop
from tray import tray, update_status

driver = get_driver()


@driver.on_startup
async def _on_startup():
    nb_logger.info("QQ_Monitor_Bot 启动调度器...")
    try:
        tray.start()
    except Exception as e:
        nb_logger.warning(f"托盘失败: {e}")
    update_status(alive=True)
    await start()
    nb_logger.info("QQ_Monitor_Bot 启动完成")


@driver.on_shutdown
async def _on_shutdown():
    nb_logger.info("QQ_Monitor_Bot 关闭中...")
    update_status(alive=False)
    await stop()
    tray.stop()
    nb_logger.info("QQ_Monitor_Bot 已关闭")
