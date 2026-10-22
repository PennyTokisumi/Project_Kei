"""定时调度器 - 基于 APScheduler 管理所有轮询任务"""

from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from nonebot import get_bot, logger as nb_logger

from config import config
from .database import (
    cleanup_old_pushed, has_pushed_items, init_db, list_targets, update_target_name,
)
from .dedup import dedup
from .formatter import send_live_notification, send_dynamic_forward, send_pixiv_forward
from .sources.base import SourceBase
from .sources.base_tracker import LiveStatusTracker
from .sources.bilibili_dynamic import BilibiliDynamic
from .sources.bilibili_live import BilibiliLive
from .sources.douyu_live import DouyuLive
from .sources.pixiv_illust import PixivIllust
from tray import update_status

import time as _time_module
import random
from datetime import datetime, timedelta

scheduler = AsyncIOScheduler()

# 启动时间戳（用于过滤重启前的旧动态）
_startup_ts = _time_module.time()

# 启动后已静默同步过的目标
_synced_targets: set[tuple[str, str]] = set()

# 新添加的监测目标延迟多久跑第一次（秒）
NEW_TARGET_FIRST_POLL_DELAY = 10

# Pixiv：每个订阅平均占用多少秒的轮询间隔（见 pixiv_interval_for）
PIXIV_MIN_SECONDS_PER_TARGET = 6

# 重启后最多补推几条 Pixiv 作品（多出来的静默标记，避免停机久了刷屏）
# 注意：补推的内容会合并成**一条**转发消息，不是多条
PIXIV_BACKLOG_LIMIT = 3

# 工厂映射：platform_source_type → SourceBase 子类
SOURCE_FACTORY: dict[str, type[SourceBase]] = {
    "bilibili_dynamic": BilibiliDynamic,
    "bilibili_live": BilibiliLive,
    "douyu_live": DouyuLive,
    "pixiv_illust": PixivIllust,
}


def poll_interval_for(source: SourceBase, pixiv_count: Optional[int] = None) -> int:
    """按平台取轮询间隔

    Pixiv 反爬比 B站严，而且每轮都要打画师主页，间隔必须拉长；
    其余平台沿用全局 poll_interval。

    ``pixiv_count`` 是当前 Pixiv 订阅总数。传 None 时自己查一次库 ——
    批量循环里请在外面算好再传进来，免得每个目标都查一遍。
    """
    if source.platform == "pixiv":
        if pixiv_count is None:
            try:
                pixiv_count = sum(
                    1 for t in list_targets() if t["platform"] == "pixiv_illust"
                )
            except Exception:
                # 库还没建表（例如单元测试）时退化为下限，不要因此抛异常
                pixiv_count = 0
        return pixiv_interval_for(pixiv_count)
    return max(10, config.poll_interval)


def pixiv_interval_for(target_count: int) -> int:
    """Pixiv 轮询间隔自适应：订阅越多，单目标间隔越长

    目标是把**总请求速率**压在常数上，而不是让每个目标都死守 300 秒 ——
    100 个订阅按 300 秒一轮就是约 0.33 次/秒，持续不断，很容易被限流。

    每个订阅平均占 6 秒：20 个以内仍是 300 秒（下限），100 个自动变 600 秒。
    """
    floor = max(60, config.pixiv_poll_interval)
    return max(floor, int(target_count) * PIXIV_MIN_SECONDS_PER_TARGET)


def jitter_for(source: SourceBase) -> int:
    """轮询抖动（秒）

    Pixiv 用大得多的抖动，把请求摊开，削掉「整齐划一」的机器特征。
    """
    return 60 if source.platform == "pixiv" else 5


def _sync_pixiv_new_items(source: SourceBase, new_items: list) -> list:
    """Pixiv 的同步策略（和 B站动态的启动过滤不同）

    - **首次订阅**（该目标还没有任何推送记录）→ 全部静默标记，一条都不发，
      避免一 add 就把画师历史上所有作品刷出来。
    - **其余情况**（含重启后）→ 最多补推 PIXIV_BACKLOG_LIMIT 条最新的，
      多出来的静默标记。补推的内容会合并成一条转发，不会刷屏。

    这里用数据库判断而不是内存标记，所以**重启期间画师更新也能补推**
    （B站动态用的是启动时间戳，会把停机期间的内容静默吞掉）。
    """
    if not has_pushed_items(source.platform, source.target_id):
        for item in new_items:
            dedup.mark_pushed(
                item.platform, item.source_type,
                item.target_id, item.id,
                item.title, item.link,
                group_id=source.group_id,
            )
        nb_logger.info(
            f"Pixiv 首次订阅静默同步 [{source.target_id}/{source.group_id}] "
            f"标记 {len(new_items)} 条，不推送"
        )
        return []

    if len(new_items) > PIXIV_BACKLOG_LIMIT:
        # fetch() 已按 createDate 倒序，保留最新的几条
        keep = new_items[:PIXIV_BACKLOG_LIMIT]
        for item in new_items[PIXIV_BACKLOG_LIMIT:]:
            dedup.mark_pushed(
                item.platform, item.source_type,
                item.target_id, item.id,
                item.title, item.link,
                group_id=source.group_id,
            )
        nb_logger.info(
            f"Pixiv 补推截断 [{source.target_id}] "
            f"推送最新 {len(keep)} 条，其余静默标记"
        )
        return keep

    return new_items

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
            if item.extra.get("is_fallback"):
                # fallback 接口无 rst/videoLoop 字段，无法区分真直播和录播轮播。
                # 跳过推送与状态更新，避免下播转轮播时误报开播；
                # 官方 API 恢复后状态自然延续（真开播 rst=0 → off→on → 推送）。
                nb_logger.debug(f"fallback 结果跳过 (可能为轮播): {source.platform}/{item.target_id}")
                continue
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
        if source.platform == "pixiv":
            # Pixiv 有自己的一套：首次订阅静默、重启补推按数据库判断
            new_items = _sync_pixiv_new_items(source, new_items)
            if not new_items:
                return
        else:
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
            if source.platform == "pixiv":
                await send_pixiv_forward(bot, source.group_id, new_items)
                nb_logger.info(
                    f"推送 Pixiv [{source.target_id}] {len(new_items)} 条"
                )
            else:
                await send_dynamic_forward(bot, source.group_id, new_items)
                nb_logger.info(f"推送动态 [{source.platform}/{source.target_id}] {len(new_items)} 条")
        except Exception as e:
            nb_logger.error(f"推送失败 [{source.platform}/{source.target_id}]: {e}")


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


async def _backfill_names():
    """回填空白的 target_name（逐个解析，失败不影响其他）"""
    targets = list_targets()
    for t in targets:
        if t.get("target_name"):
            continue
        source = _make_source(t)
        if source is None:
            continue
        try:
            name = await source.get_display_name()
            if name and name != t["target_id"]:
                update_target_name(t["id"], name)
                nb_logger.info(f"回填名称: {t['platform']}/{t['target_id']} → {name}")
        except Exception:
            pass


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

    # 回填空白的显示名称
    await _backfill_names()

    targets = list_targets()
    update_status(targets_total=len(targets), alive=True)

    pixiv_count = sum(1 for t in targets if t["platform"] == "pixiv_illust")
    if pixiv_count:
        nb_logger.info(
            f"Pixiv 订阅 {pixiv_count} 个 → 轮询间隔 {pixiv_interval_for(pixiv_count)}s"
        )

    for t in targets:
        source = _make_source(t)
        if source is None:
            continue

        interval = poll_interval_for(source, pixiv_count)
        job_id = f"{source.platform}/{source.target_id}/{source.group_id}"

        # 启动错峰：把首轮随机散布在 [10s, interval] 内。
        # 否则所有任务都在「开机 + interval」那一刻同时触发 ——
        # 100 个 Pixiv 订阅就是 100 个请求挤在几秒里，必被限流。
        spread = max(NEW_TARGET_FIRST_POLL_DELAY + 1, interval)
        first_run = datetime.now() + timedelta(
            seconds=random.uniform(NEW_TARGET_FIRST_POLL_DELAY, spread)
        )

        scheduler.add_job(
            poll_source,
            "interval",
            seconds=interval,
            id=job_id,
            args=[source],
            next_run_time=first_run,
            replace_existing=True,
            misfire_grace_time=30,
            jitter=jitter_for(source),
        )
        nb_logger.info(
            f"注册轮询任务 [{job_id}] 间隔 {interval}s "
            f"抖动 {jitter_for(source)}s 首轮 {first_run:%H:%M:%S}"
        )

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
    pixiv_count = sum(1 for t in targets if t["platform"] == "pixiv_illust")

    # 移除已删除的目标
    wanted_ids: set[str] = set()
    for t in targets:
        source = _make_source(t)
        if source is None:
            continue
        job_id = f"{source.platform}/{source.target_id}/{source.group_id}"
        wanted_ids.add(job_id)

        if job_id not in current_jobs:
            interval = poll_interval_for(source, pixiv_count)
            scheduler.add_job(
                poll_source,
                "interval",
                seconds=interval,
                id=job_id,
                args=[source],
                # 新加的目标立刻跑一次，不用干等一个完整周期。
                # Pixiv 周期是 300 秒起，等满会让人以为没生效。
                # 留 10 秒缓冲，避免和 add 指令的回复撞在一起。
                next_run_time=datetime.now() + timedelta(seconds=NEW_TARGET_FIRST_POLL_DELAY),
                replace_existing=True,
                misfire_grace_time=30,
                jitter=jitter_for(source),
            )
            nb_logger.info(
                f"新增轮询任务 [{job_id}] 间隔 {interval}s（{NEW_TARGET_FIRST_POLL_DELAY}s 后先跑一次）"
            )

    # 移除已删除的任务。
    # 注意：只处理本插件注册的轮询任务 —— 同一个 scheduler 上还挂着
    # agent 插件的 scheduled_msg_* / daily_* 任务，若按 id 差集无差别删除，
    # 一次 add/remove 就会把它们全部抹掉。
    for job in scheduler.get_jobs():
        if job.id not in wanted_ids and job.func is poll_source:
            scheduler.remove_job(job.id)
            nb_logger.info(f"移除轮询任务 [{job.id}]")

    # 确保调度器在运行
    if not scheduler.running and targets:
        scheduler.start()
        nb_logger.info(f"调度器已启动，共 {len(targets)} 个监测目标")
    elif scheduler.running:
        nb_logger.info(f"调度器已刷新，共 {len(targets)} 个监测目标")
