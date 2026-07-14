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

import time as _time_module

scheduler = AsyncIOScheduler()

# 启动时间戳（用于过滤重启前的旧动态）
_startup_ts = _time_module.time()

# 启动后已静默同步过的目标
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
            tracker = LiveStatusTracker(source.target_id, source.platform,
                                         group_id=source.group_id)
            tracker.check_and_update(is_living=False, title="")
        return

    new_items = []

    for item in items:
        if is_live:
            # 直播类：不做 dedup 去重，交给 LiveStatusTracker 做 off→on 检测
            tracker = LiveStatusTracker(item.target_id, item.platform,
                                        group_id=source.group_id)
            detected = tracker.check_and_update(is_living=True, title=item.title)
            if not detected:
                continue
        else:
            # 动态类：通过 dedup 去重，避免重复推送
            is_new = dedup.is_new(
                item.platform, item.source_type,
                item.target_id, item.id,
                group_id=source.group_id,
            )
            if not is_new:
                continue

        new_items.append(item)

    if not new_items:
        return

    # 动态类：启动后首次轮询，跳过重启前发布的旧动态
    if not is_live:
        key = (source.platform, source.target_id, source.group_id)
        if key not in _synced_targets:
            _synced_targets.add(key)
            truly_new = []
            for item in new_items:
                if item.pub_ts > _startup_ts:
                    # 启动后发布 → 正常推送
                    truly_new.append(item)
                else:
                    # 启动前发布 → 静默标记
                    dedup.mark_pushed(
                        item.platform, item.source_type,
                        item.target_id, item.id,
                        item.title, item.link,
                        group_id=source.group_id,
                    )
            skipped = len(new_items) - len(truly_new)
            if skipped:
                nb_logger.info(f"启动静默标记 [{key}] → 跳过 {skipped} 条旧动态")
            new_items = truly_new
            if not new_items:
                return

    # 推送（逐条处理，单个失败不影响其他）
    if is_live:
        for item in new_items:
            try:
                await send_live_notification(bot, source.group_id, item)
            except Exception as e:
                nb_logger.error(f"推送开播失败 [{source.platform}/{source.target_id}]: {e}")
                # 仅回滚本条，下次轮询可重试
                tracker = LiveStatusTracker(item.target_id, item.platform,
                                            group_id=source.group_id)
                tracker.check_and_update(is_living=False, title="")
        nb_logger.info(f"推送开播提醒 [{source.platform}/{source.target_id}]")
    else:
        # 先去重后推送——标记必须先于推送，防止异常导致无限重推
        for item in new_items:
            dedup.mark_pushed(
                item.platform, item.source_type,
                item.target_id, item.id,
                item.title, item.link,
                group_id=source.group_id,
            )
        try:
            await send_dynamic_forward(bot, source.group_id, new_items)
            nb_logger.info(f"推送动态 [{source.platform}/{source.target_id}] {len(new_items)} 条")
        except Exception as e:
            nb_logger.error(f"推送动态失败 [{source.platform}/{source.target_id}]: {e}")


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
        job_id = f"{source.platform}/{source.target_id}/{source.group_id}"

        scheduler.add_job(
            poll_source,
            "interval",
            seconds=interval,
            id=job_id,
            args=[source],
            replace_existing=True,
            misfire_grace_time=30,
            jitter=5,  # 随机偏移避免多群同时请求被限流
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
        job_id = f"{source.platform}/{source.target_id}/{source.group_id}"
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
                jitter=5,
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
