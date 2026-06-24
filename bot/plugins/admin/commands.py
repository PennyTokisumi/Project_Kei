"""群管理命令 - add / list / remove / status"""

from nonebot import on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Bot, Message
from nonebot.rule import to_me, startswith

from config import config
from ..monitor.database import add_target, remove_target, list_targets, get_target
from ..monitor.scheduler import reload_targets

# ─── 命令规则：@机器人 + 命令前缀 ─────────────────────────────
add_cmd = on_message(rule=to_me() & startswith("add"), priority=5)
list_cmd = on_message(rule=to_me() & startswith("list"), priority=5)
remove_cmd = on_message(rule=to_me() & startswith("remove"), priority=5)
status_cmd = on_message(rule=to_me() & startswith("status"), priority=5)
help_cmd = on_message(rule=to_me() & startswith("help"), priority=5)


@add_cmd.handle()
async def handle_add(bot: Bot, event: GroupMessageEvent):
    """添加监测目标

    格式: add <platform> <target_id>
    示例: add bilibili_dynamic 436742
          add bilibili_live 436742
          add douyu_live 617916
    """
    group_id = event.group_id
    text = event.get_plaintext().strip()

    parts = text.split()
    if len(parts) < 3:
        await add_cmd.finish(
            Message("格式: add <平台> <目标ID>\n"
                    "示例: add bilibili_dynamic 436742\n"
                    "      add bilibili_live 436742\n"
                    "      add douyu_live 617916"),
            at_sender=True,
        )

    platform = parts[1].lower()
    target_id = parts[2].strip()

    valid_platforms = {"bilibili_dynamic", "bilibili_live", "douyu_live"}
    if platform not in valid_platforms:
        await add_cmd.finish(
            Message(f"不支持的平台: {platform}\n"
                    f"支持: {', '.join(valid_platforms)}"),
            at_sender=True,
        )

    try:
        target_id_int = int(target_id)
    except ValueError:
        await add_cmd.finish(
            Message("目标ID必须是数字"),
            at_sender=True,
        )

    # 添加
    row_id = add_target(group_id, platform, str(target_id_int), "")
    await add_cmd.send(
        Message(f"✅ 已添加监测 [{platform}] ID: {target_id_int}"),
        at_sender=True,
    )

    # 刷新调度器
    await reload_targets()


@list_cmd.handle()
async def handle_list(bot: Bot, event: GroupMessageEvent):
    """列出本群所有监测目标"""
    group_id = event.group_id
    targets = [t for t in list_targets() if t["group_id"] == group_id]

    if not targets:
        await list_cmd.finish(
            Message("📭 本群暂无监测目标"),
            at_sender=True,
        )

    lines = ["📋 本群监测列表："]
    for idx, t in enumerate(targets, 1):
        name = t.get("target_name") or t["target_id"]
        lines.append(f"  {idx}. [{t['platform']}] {name}")

    await list_cmd.finish(
        Message("\n".join(lines)),
        at_sender=True,
    )


@remove_cmd.handle()
async def handle_remove(bot: Bot, event: GroupMessageEvent):
    """移除监测目标

    格式: remove <id>
    id 通过 list 命令查看
    """
    text = event.get_plaintext().strip()
    parts = text.split()

    if len(parts) < 2 or not parts[1].strip().isdigit():
        await remove_cmd.finish(
            Message("格式: remove <ID>\n使用 list 查看 ID"),
            at_sender=True,
        )

    idx = int(parts[1].strip())
    targets = [t for t in list_targets() if t["group_id"] == event.group_id]

    if idx < 1 or idx > len(targets):
        await remove_cmd.finish(
            Message(f"❌ 序号 {idx} 不存在，使用 list 查看有效序号"),
            at_sender=True,
        )

    target = targets[idx - 1]
    remove_target(target["id"])
    await remove_cmd.send(
        Message(f"✅ 已移除 [{target['platform']}] {target['target_id']}"),
        at_sender=True,
    )

    # 刷新调度器
    await reload_targets()


@status_cmd.handle()
async def handle_status(bot: Bot, event: GroupMessageEvent):
    """查看机器人运行状态"""
    all_targets = list_targets()
    group_targets = [t for t in all_targets if t["group_id"] == event.group_id]

    lines = [
        "🤖 QQ_Monitor_Bot 运行状态",
        f"  ├ 运行平台: Windows",
        f"  ├ 总监测目标: {len(all_targets)} 个",
        f"  ├ 本群目标: {len(group_targets)} 个",
        f"  ├ 轮询间隔: {config.poll_interval} 秒",
        f"  └ 数据源: B站动态/B站直播/斗鱼直播",
    ]
    await status_cmd.finish(
        Message("\n".join(lines)),
        at_sender=True,
    )


@help_cmd.handle()
async def handle_help(bot: Bot, event: GroupMessageEvent):
    """显示帮助信息"""
    lines = [
        "🤖 QQ_Monitor_Bot 指令",
        "",
        "add bilibili_dynamic <UID>     B站动态监测",
        "add bilibili_live <房间号>       B站直播监测",
        "add douyu_live <房间号>        斗鱼直播监测",
        "list                          本群监测列表",
        "remove <序号>                   移除监测",
        "status                        机器人状态",
        "help                          本帮助",
    ]
    await help_cmd.finish(
        Message("\n".join(lines)),
        at_sender=True,
    )
