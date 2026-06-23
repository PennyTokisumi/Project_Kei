"""定时调度器 - 基于 APScheduler 管理所有轮询任务"""

import logging
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from nonebot import get_bot

from config import config
from .database import init_db, list_targets
from .dedup import dedup
from .formatter import send_live_notification, send_dynamic_forward
from .sources.base import SourceBase
from .sources.base_tracker import LiveStatusTracker
from .sources.bilibili_dynamic import BilibiliDynamic
from .sources.bilibili_live import BilibiliLive
from .sources.douyu_live import DouyuLive

logger = logging.getLogger("monitor.scheduler")

scheduler = AsyncIOScheduler()

# 工厂映射：platform_source_type → SourceBase 子类
SOURCE_FACTORY: dict[str, type[SourceBase]] = {
    "bilibili_dynamic": BilibiliDynamic,
    "bilibili_live": BilibiliLive,
    "douyu_live": DouyuLive,
}

async def poll_source(source: SourceBase):
    """轮询一个监测源：fetch → dedup → format → push"""
    try:
        bot = get_bot()
    except Exception:
        logger.warning("Bot 未就绪，跳过轮询")
        return

    try:
        items = await source.fetch()
    except Exception as e:
        logger.error(f"抓取失败 [{source.platform}/{source.target_id}]: {e}")
        return

    if not items:
        return

    # 判断类型
    is_live = source.source_type == "live"
    new_items = []

    for item in items:
        is_new = dedup.is_new(
            item.platform, item.source_type,
            item.target_id, item.id,
        )
        if not is_new:
            continue

        # 直播类需要额外做 off→on 检测
        if is_live:
            tracker = LiveStatusTracker(item.target_id, item.platform)
            detected = tracker.check_and_update(is_living=True, title=item.title)
            if not detected:
                continue  # 已经在直播中，不是刚开播

        new_items.append(item)
        dedup.mark_pushed(
            item.platform, item.source_type,
            item.target_id, item.id,
            item.title, item.link,
        )

    if not new_items:
        return

    # 推送
    try:
        if is_live:
            # 直播开播：每条单独推送
            for item in new_items:
                await send_live_notification(bot, source.group_id, item)
            logger.info(f"推送开播提醒 [{source.platform}/{source.target_id}]")
        else:
            # B站动态：合并转发
            nickname = new_items[0].nickname or source.target_id
            await send_dynamic_forward(bot, source.group_id, nickname, new_items)
            logger.info(f"推送动态 [{source.platform}/{source.target_id}] {len(new_items)} 条")
    except Exception as e:
        logger.error(f"推送失败 [{source.platform}/{source.target_id}]: {e}")


def _make_source(target: dict) -> Optional[SourceBase]:
    """从 DB 记录创建 Source 实例"""
    platform = target["platform"]
    target_id = target["target_id"]
    group_id = target["group_id"]

    cls = SOURCE_FACTORY.get(platform)
    if cls is None:
        logger.warning(f"不支持的平台: {platform}")
        return None
    return cls(target_id, group_id)


async def start():
    """启动调度器：初始化 DB → 加载所有目标 → 注册定时任务"""
    init_db()

    # 防止重复 start
    if scheduler.running:
        logger.warning("调度器已在运行，跳过")
        return

    targets = list_targets()
    for t in targets:
        source = _make_source(t)
        if source is None:
            continue

        interval = config.poll_interval
        job_id = f"{source.platform}/{source.target_id}"

        scheduler.add_job(
            poll_source,
            "interval",
            seconds=interval,
            id=job_id,
            args=[source],
            replace_existing=True,
            misfire_grace_time=30,
        )
        logger.info(f"注册轮询任务 [{job_id}] 间隔 {interval}s")

    if targets:
        scheduler.start()
        logger.info(f"调度器已启动，共 {len(targets)} 个监测目标")
    else:
        logger.info("暂无监测目标，调度器待机")


async def stop():
    """停止调度器"""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("调度器已停止")


async def reload_targets():
    """重新加载所有目标（add/remove 后调用）"""
    await stop()
    # 清空旧任务
    scheduler.remove_all_jobs()
    await start()
