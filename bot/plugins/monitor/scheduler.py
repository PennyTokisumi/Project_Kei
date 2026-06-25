"""定时调度器 - 基于 APScheduler 管理所有轮询任务"""

from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from nonebot import get_bot, logger as nb_logger

from config import config
from .database import cleanup_old_pushed, init_db, list_targets
from .dedup import dedup
from .formatter import send_live_notification, send_dynamic_forward
from .sources.base import SourceBase
from .sources.base_tracker import LiveStatusTracker
from .sources.bilibili_dynamic import BilibiliDynamic
from .sources.bilibili_live import BilibiliLive
from .sources.douyu_live import DouyuLive
from tray import update_status

scheduler = AsyncIOScheduler()

# 启动后已静默同步过的目标（避免重启后推送旧动态）
_synced_targets: set[tuple[str, str]] = set()

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
        nb_logger.warning("Bot 未就绪，跳过轮询")
        return

    try:
        items = await source.fetch()
    except Exception as e:
        nb_logger.error(f"抓取失败 [{source.platform}/{source.target_id}]: {e}")
        return

    is_live = source.source_type == "live"

    if not items:
        # 直播源下播时更新状态，确保下次开播能检测到 off→on
        if is_live:
            tracker = LiveStatusTracker(source.target_id, source.platform)
            tracker.check_and_update(is_living=False, title="")
        return

    new_items = []

    for item in items:
        if is_live:
            # 直播类：不做 dedup 去重，交给 LiveStatusTracker 做 off→on 检测
            tracker = LiveStatusTracker(item.target_id, item.platform)
            detected = tracker.check_and_update(is_living=True, title=item.title)
            if not detected:
                continue
        else:
            # 动态类：通过 dedup 去重，避免重复推送
            is_new = dedup.is_new(
                item.platform, item.source_type,
                item.target_id, item.id,
            )
            if not is_new:
                continue

        new_items.append(item)

    if not new_items:
        return

    # 动态类：启动后首次轮询静默标记现有动态，防止推送重启前旧内容
    if not is_live:
        key = (source.platform, source.target_id)
        if key not in _synced_targets:
            _synced_targets.add(key)
            for item in new_items:
                dedup.mark_pushed(
                    item.platform, item.source_type,
                    item.target_id, item.id,
                    item.title, item.link,
                )
            nb_logger.info(
                f"启动静默标记 [{key}] → {len(new_items)} 条"
            )
            return

    # 推送（成功后再标记已推送，防止推送失败丢失内容）
    try:
        if is_live:
            for item in new_items:
                await send_live_notification(bot, source.group_id, item)
            nb_logger.info(f"推送开播提醒 [{source.platform}/{source.target_id}]")
        else:
            await send_dynamic_forward(bot, source.group_id, new_items)
            nb_logger.info(f"推送动态 [{source.platform}/{source.target_id}] {len(new_items)} 条")
    except Exception as e:
        nb_logger.error(f"推送失败 [{source.platform}/{source.target_id}]: {e}")
        # 直播推送失败时回滚状态，下次轮询可重试
        if is_live:
            for item in new_items:
                tracker = LiveStatusTracker(item.target_id, item.platform)
                tracker.check_and_update(is_living=False, title="")
        return

    # 推送成功后标记（动态类才需要去重标记）
    for item in new_items:
        if not is_live:
            dedup.mark_pushed(
                item.platform, item.source_type,
                item.target_id, item.id,
                item.title, item.link,
            )


def _make_source(target: dict) -> Optional[SourceBase]:
    """从 DB 记录创建 Source 实例"""
    platform = target["platform"]
    target_id = target["target_id"]
    group_id = target["group_id"]

    cls = SOURCE_FACTORY.get(platform)
    if cls is None:
        nb_logger.warning(f"不支持的平台: {platform}")
        return None
    return cls(target_id, group_id)


async def start():
    """启动调度器：初始化 DB → 清理 → 加载所有目标 → 注册定时任务"""
    init_db()

    # 清理 30 天前的推送记录，防止 DB 无限膨胀
    cleaned = cleanup_old_pushed(days=30)
    if cleaned:
        nb_logger.info(f"清理了 {cleaned} 条过期推送记录")

    # 防止重复 start
    if scheduler.running:
        nb_logger.warning("调度器已在运行，跳过")
        return

    targets = list_targets()
    update_status(targets_total=len(targets), alive=True)

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
        nb_logger.info(f"注册轮询任务 [{job_id}] 间隔 {interval}s")

    if targets:
        scheduler.start()
        nb_logger.info(f"调度器已启动，共 {len(targets)} 个监测目标")
    else:
        nb_logger.info("暂无监测目标，调度器待机")


async def stop():
    """停止调度器"""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        nb_logger.info("调度器已停止")


async def reload_targets():
    """重新加载所有目标（add/remove 后调用）"""
    targets = list_targets()
    update_status(targets_total=len(targets), alive=True)

    current_jobs = {job.id for job in scheduler.get_jobs()}

    # 移除已删除的目标
    wanted_ids: set[str] = set()
    for t in targets:
        source = _make_source(t)
        if source is None:
            continue
        job_id = f"{source.platform}/{source.target_id}"
        wanted_ids.add(job_id)

        if job_id not in current_jobs:
            scheduler.add_job(
                poll_source,
                "interval",
                seconds=config.poll_interval,
                id=job_id,
                args=[source],
                replace_existing=True,
                misfire_grace_time=30,
            )
            nb_logger.info(f"新增轮询任务 [{job_id}]")

    # 移除已删除的任务
    for job_id in current_jobs - wanted_ids:
        scheduler.remove_job(job_id)
        nb_logger.info(f"移除轮询任务 [{job_id}]")

    # 确保调度器在运行
    if not scheduler.running and targets:
        scheduler.start()
        nb_logger.info(f"调度器已启动，共 {len(targets)} 个监测目标")
    elif scheduler.running:
        nb_logger.info(f"调度器已刷新，共 {len(targets)} 个监测目标")
