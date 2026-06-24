"""QQ_Monitor_Bot - B站动态/直播 + 斗鱼直播 监测推送插件"""

from nonebot import get_driver, logger as nb_logger
from nonebot.adapters.onebot.v11 import Bot, MessageSegment

from .database import list_targets
from .scheduler import start, stop
from tray import tray, update_status

driver = get_driver()

STARTUP_MSG = (
    "これから、先生のことを見守らせていただきますね。\n今后就让我来守护老师吧。"
)

_greeting_sent = False


@driver.on_startup
async def _on_startup():
    nb_logger.info("QQ_Monitor_Bot 启动调度器...")
    try:
        tray.start()
    except Exception as e:
        nb_logger.warning(f"托盘失败: {e}")
    update_status(alive=True)
    await start()
    nb_logger.info("调度器已启动，等待 WebSocket 连接...")


@driver.on_bot_connect
async def _on_connect(bot: Bot):
    """WebSocket 连接建立后发送上线通知"""
    global _greeting_sent
    if _greeting_sent:
        return
    _greeting_sent = True

    nb_logger.info("WebSocket 已连接，发送上线通知...")
    all_targets = list_targets()
    group_ids = {t["group_id"] for t in all_targets}

    if not group_ids:
        nb_logger.info("暂无监测目标，跳过上线通知")
    else:
        for gid in group_ids:
            try:
                await bot.send_group_msg(
                    group_id=gid,
                    message=MessageSegment.text(STARTUP_MSG),
                )
                nb_logger.info(f"上线通知已发送 [群{gid}]")
            except Exception as e:
                nb_logger.error(f"上线通知发送失败 [群{gid}]: {e}")

    # 切换托盘为绿点
    tray.set_ready()
    nb_logger.info("QQ_Monitor_Bot 启动完成")


@driver.on_shutdown
async def _on_shutdown():
    nb_logger.info("QQ_Monitor_Bot 关闭中...")
    update_status(alive=False)
    await stop()
    tray.stop()
    nb_logger.info("QQ_Monitor_Bot 已关闭")
