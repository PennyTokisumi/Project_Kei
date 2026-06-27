"""Project_Kei - B站动态/直播 + 斗鱼直播 监测推送插件"""

import random

from nonebot import get_driver, logger as nb_logger
from nonebot.adapters.onebot.v11 import Bot, MessageSegment

from config import DATA_DIR
from .database import get_setting, list_targets
from .scheduler import start, stop
from tray import tray, update_status

driver = get_driver()

STARTUP_MSGS = [
    "これから、先生のことを見守らせていただきますね。\n今后就让我来守护老师吧。",
    "こんにちは、先生。今日のやることをまとめました。\n你好，老师。我已经将今天要做的事项整理好了。",
    "最初の目標に向けて、まず一歩、ですね。\n向着最初的目标，先迈出一步吧。",
    "私は、どんな手を使ってでも生き残ってやるつもりですから。\n不管用什么手段，我都会活下去。",
]

_greeting_sent = False


@driver.on_startup
async def _on_startup():
    nb_logger.info("Project_Kei 启动调度器...")
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

    nb_logger.info("WebSocket 已连接，检查问候设置...")
    all_targets = list_targets()
    group_ids = {t["group_id"] for t in all_targets}

    if not group_ids:
        nb_logger.info("暂无监测目标，跳过上线通知")
    else:
        for gid in group_ids:
            if get_setting(f"greeting_{gid}", "1") != "1":
                continue
            try:
                await bot.send_group_msg(
                    group_id=gid,
                    message=MessageSegment.text(random.choice(STARTUP_MSGS)),
                )
                nb_logger.info(f"上线通知已发送 [群{gid}]")
            except Exception as e:
                nb_logger.error(f"上线通知发送失败 [群{gid}]: {e}")

    # 切换托盘为绿点
    tray.set_ready()
    # 写入启动完成信号（供 start.bat 轮询）
    (DATA_DIR / ".startup_ok").touch()
    nb_logger.info("Project_Kei 启动完成")


@driver.on_shutdown
async def _on_shutdown():
    nb_logger.info("Project_Kei 关闭中...")
    # 清理 PID 文件
    (DATA_DIR / ".pid").unlink(missing_ok=True)
    update_status(alive=False)
    await stop()
    tray.stop()
    nb_logger.info("Project_Kei 已关闭")
