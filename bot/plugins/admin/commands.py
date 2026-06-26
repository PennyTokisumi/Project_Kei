"""群管理命令 - add / list / remove / status"""

import random
from pathlib import Path

from nonebot import on_message, on_notice, get_bot
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment, PokeNotifyEvent
from nonebot.rule import to_me, startswith

from config import config, VERSION
from ..monitor.database import (
    add_target, remove_target, list_targets,
    get_setting, set_setting,
)
from ..monitor.scheduler import reload_targets

# ─── 命令规则：@机器人 + 命令前缀 ─────────────────────────────
add_cmd = on_message(rule=to_me() & startswith("add"), priority=5)
list_cmd = on_message(rule=to_me() & startswith("list"), priority=5)
remove_cmd = on_message(rule=to_me() & startswith("remove"), priority=5)
status_cmd = on_message(rule=to_me() & startswith("status"), priority=5)
help_cmd = on_message(rule=to_me() & startswith("help"), priority=5)
hello_cmd = on_message(rule=to_me() & startswith("hello"), priority=5)
chat_cmd = on_message(rule=to_me() & startswith("chat"), priority=5)
suicide_cmd = on_message(rule=to_me() & startswith("kawaii"), priority=5)
# 兜底：被 @ 但无匹配指令
unknown_cmd = on_message(rule=to_me(), priority=99)


@add_cmd.handle()
async def handle_add(event: GroupMessageEvent):
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
    add_target(group_id, platform, str(target_id_int), "")
    await add_cmd.send(
        Message(f"\nSensei，已添加监测目标。[{platform}] ID: {target_id_int}"),
        at_sender=True,
    )

    # 刷新调度器
    await reload_targets()


@list_cmd.handle()
async def handle_list(event: GroupMessageEvent):
    """列出本群所有监测目标"""
    group_id = event.group_id
    targets = [t for t in list_targets() if t["group_id"] == group_id]

    if not targets:
        await list_cmd.finish(
            Message("\nSensei，目前暂无监测目标。"),
            at_sender=True,
        )

    lines = ["\nSensei，以下是正在监测的目标。"]
    for idx, t in enumerate(targets, 1):
        name = t.get("target_name") or t["target_id"]
        lines.append(f"  {idx}. [{t['platform']}] {name}")

    await list_cmd.finish(
        Message("\n".join(lines)),
        at_sender=True,
    )


@remove_cmd.handle()
async def handle_remove(event: GroupMessageEvent):
    """移除监测目标（仅群主/管理员可用）

    格式: remove <id>
    id 通过 list 命令查看
    """
    # 权限校验：仅群主或管理员可用
    if event.sender.role not in ("owner", "admin"):
        await remove_cmd.finish(
            Message("\n正因如此，你没有资格啊。"),
            at_sender=True,
        )

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
        Message(f"\nSensei，已移除监测目标。[{target['platform']}] {target['target_id']}"),
        at_sender=True,
    )

    # 刷新调度器
    await reload_targets()


CHAT_MSGS = [
    "抵抗するためには前に進まないと。今まではそれも、一人でやらなければと思っていたのですが……結局、人は一人では生きていけないのだと理解しました。\n为了抵抗，必须继续前行。此前我总觉得这些事只能独自承担……但终究还是明白了，人是无法独自活下去的。",
    "な、何ですか？特に言うことはありませんが……？っ……分かりました……。せ、先生のこと、嫌いではありません……もう、いいですか！？\n怎，怎么了？有没有什么想说的话……？呃……行吧……我、我其实并不讨厌老师……这下可以了吧！？",
    "こういう言葉は、滅多に言いませんから、ちゃんと聞いてくださいね！？……あまり、危険なことはしないでください。先生が居なくなるのは……私も嫌ですから。\n听好了，我很少说这种话的！？……请你尽量不要做危险的事。因为老师要是不在了……我也会很难过的。",
    "遠き地の星明かり……という意味です。それだけ昔のことを言っているのだと思います。座標によると、過去と未来は同時に存在するとも言いますから。\n意思是……远方的星光。感觉像是在说久远的往事一样。毕竟根据坐标，过去与未来是同时存在的。",
    "でもまあ……世の理を知ってしまえば。怒らないでいるのは難しい、と思っているのですが。\n不过嘛……一旦知晓了世间真理。我觉得，想保持不生气实在很难呢。",
    "食事はちゃんと取っていますか？適度な運動も必要です。早寝早起きが良いのは、大人にも当てはまることなんですよ。\n你有好好吃饭吗？适当的运动也是必要的。早睡早起的好处，对大人也同样适用哦。",
    "仕事はほどほどに。とはいえ怠けるのもほどほどに。……本当にもう、手が焼けるんですから。\n……真是的，你实在太让人操心啦。工作要适度，不过偷懒也得适可而止。",
    "……何をニヤニヤしてるんですか！\n……你在那儿偷偷笑什么呢！",
    "先生がこの世界を見捨てないというのなら。私だって最後まで、絶対に諦めたりしません。\n如果老师不抛弃这个世界的话。那我到最后也不会放弃的。",
    "私は――先生のこと、嫌いじゃありませんから。\n我——并不讨厌老师呢。",
    "絶対、大丈夫です。私はずっとここにいます。\n绝对，没问题的。我会一直在这里。",
    "えっ？何か言いたいことはないか？……ないです。ないったらないと言っているでしょう！\n诶？问我有什么想说的吗？……没有。我说没有就没有！",
    "先生も休憩を忘れずに！あと歯磨きも！\n老师也别忘记休息！还有刷牙！",
    "えっ？優しい言葉がほしい……？寝言は時と場所を選んでください！\n诶？想听点温柔的话……？说梦话请选好时间和地点！",
    "はぁ……手間のかかることをさせないでくださいね。\n唉……请别让我做些费时费力的事啊。",
]

@chat_cmd.handle()
async def handle_chat(event: GroupMessageEvent):
    """闲聊模式"""
    await chat_cmd.finish(
        Message(f"\n{random.choice(CHAT_MSGS)}"),
        at_sender=True,
    )

SUICIDE_GIF = Path(__file__).resolve().parent.parent.parent / "kei_suicide.gif"


@suicide_cmd.handle()
async def handle_suicide(event: GroupMessageEvent):
    """自杀指令：禁言指令者 1 分钟 + 发送 GIF"""
    # 先发文字消息
    await suicide_cmd.send(
        Message("呜……今天必须要跟老师同归于尽！我要先杀了老师再自杀！"),
        at_sender=True,
    )

    # 禁言指令者 1 分钟
    try:
        bot = get_bot()
        await bot.call_api(
            "set_group_ban",
            group_id=event.group_id,
            user_id=event.user_id,
            duration=60,
        )
    except Exception:
        pass  # 禁言失败不阻断（bot 可能没有管理员权限）

    # 发送 GIF
    if SUICIDE_GIF.exists():
        await suicide_cmd.send(
            MessageSegment.image(str(SUICIDE_GIF.resolve())),
        )

STATUS_MSGS = [
    "えっ？私がちゃんといるのか確認するのが仕事？\n诶？确认我是否好好待着就是你的工作内容吗？",
    "心配しないでください。私が消えることはありません。\n别担心。我是不会消失的。",
    "この身体……結構よくできた気がします。\n这个身体……感觉做的相当不错呢。",
    "自分の身体を持つのは……不便なことでもあるんですね……\n拥有自己的身体……也有不方便的地方呢……",
]

@status_cmd.handle()
async def handle_status(event: GroupMessageEvent):
    """查看机器人运行状态"""
    all_targets = list_targets()
    group_targets = [t for t in all_targets if t["group_id"] == event.group_id]

    lines = [
        "\nSensei，以下是监测系统状态。",
        "",
        f"系统内核: v{VERSION}",
        f"总监测目标: {len(all_targets)} 个",
        f"本群目标: {len(group_targets)} 个",
        f"轮询间隔: {config.poll_interval} 秒",
        "",
        random.choice(STATUS_MSGS),
    ]
    await status_cmd.finish(
        Message("\n".join(lines)),
        at_sender=True,
    )


@hello_cmd.handle()
async def handle_hello(event: GroupMessageEvent):
    """开关启动问候"""
    text = event.get_plaintext().strip()
    parts = text.split()
    if len(parts) < 2:
        await hello_cmd.finish(
            Message("格式: hello ON 或 hello OFF"),
            at_sender=True,
        )
    arg = parts[1].upper()
    key = f"greeting_{event.group_id}"
    if arg == "ON":
        set_setting(key, "1")
        await hello_cmd.finish(
            Message("\nSensei，监测系统启动确认已开启。"),
            at_sender=True,
        )
    elif arg == "OFF":
        set_setting(key, "0")
        await hello_cmd.finish(
            Message("\nSensei，监测系统启动确认已关闭。"),
            at_sender=True,
        )
    else:
        await hello_cmd.finish(
            Message("格式: hello ON 或 hello OFF"),
            at_sender=True,
        )


@help_cmd.handle()
async def handle_help(event: GroupMessageEvent):
    """显示帮助信息"""
    lines = [
        "\n先生の頼みなら……仕方ありませんね。\n既然是老师的请求……那就没办法了呢。",
        "",
        "help  -  显示帮助信息",
        "status  -  显示系统运行状态",
        "chat  -  和Kei聊天",
        "kawaii  -  夸一夸Kei",
        "hello ON/OFF  -  开关启动问候",
        "list  -  显示本群监测列表",
        "remove <序号>  -  移除监测目标",
        "add bilibili_live <房间号>  -  添加B站直播监测",
        "add bilibili_dynamic <UID>  -  添加B站动态监测",
        "add douyu_live <房间号>  -  添加斗鱼直播监测",
        "",
        "私は力になれましたか？\n我能帮上忙吗？",
    ]
    await help_cmd.finish(
        Message("\n".join(lines)),
        at_sender=True,
    )


# 拍一拍处理
poke_handler = on_notice()


@poke_handler.handle()
async def handle_poke(event: PokeNotifyEvent):
    """群内被拍一拍时回复"""
    if not event.group_id:
        return
    await poke_handler.finish(
        Message(f"{MessageSegment.at(event.user_id)}\n{random.choice(UNKNOWN_MSGS)}"),
    )


UNKNOWN_MSGS = [
    "何ですか？用がないなら呼ばないでください。\n什么事？如果没事的话请不要叫我。",
    "どうかしました？えっ？呼んでみただけ……ですか？\n怎么了？诶？只是喊我一下……是吗？",
    "特に用がないなら呼ばないでください！\n没什么特别的事就别喊我！",
    "な、なんですか！？何か言ってほしいんですか！？\n干什么！？想让我说点什么吗！？",
    "なんでいきなり撫でるんですか！？\n突然摸我干什么！？",
    "他に必要な物はありませんか？あまり悩む時間は残されていません。\n请问还要其他东西吗？我们还能犹豫的时间不多了。",
]


@unknown_cmd.handle()
async def handle_unknown(event: GroupMessageEvent):
    """兜底：被 @ 但无匹配指令"""
    await unknown_cmd.finish(
        Message(f"\n{random.choice(UNKNOWN_MSGS)}"),
        at_sender=True,
    )
