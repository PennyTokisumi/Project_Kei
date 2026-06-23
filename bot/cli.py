"""CLI 管理工具 - 后台增删监测目标

用法（在 bot/ 目录下运行）：
  python cli.py add <group_id> <platform> <target_id>
  python cli.py list
  python cli.py remove <id>

示例：
  python cli.py add 123456789 bilibili_dynamic 436742
  python cli.py add 123456789 bilibili_live 7777
  python cli.py add 123456789 douyu_live 617916

注意：CLI 添加的目标不会立即被调度器识別，需重启 NoneBot2 后生效。
"""

import sys
sys.path.insert(0, ".")

from plugins.monitor.database import init_db, add_target, remove_target, list_targets, get_target

VALID_PLATFORMS = {"bilibili_dynamic", "bilibili_live", "douyu_live"}


def cmd_add(group_id: int, platform: str, target_id: str):
    init_db()
    rid = add_target(group_id=group_id, platform=platform, target_id=target_id)
    print(f"✅ [{rid}] 群 {group_id} → {platform} {target_id}")
    print("⚠️ 重启 NoneBot2 后生效")


def cmd_list():
    init_db()
    targets = list_targets()
    if not targets:
        print("(空)")
        return
    print(f"{'ID':<5} {'群号':<12} {'平台':<22} {'目标ID'}")
    print("-" * 50)
    for t in targets:
        name = t.get("target_name") or t["target_id"]
        print(f"[{t['id']:<3}] {t['group_id']:<12} {t['platform']:<22} {name}")


def cmd_remove(target_id: int):
    init_db()
    t = get_target(target_id)
    if t is None:
        print(f"❌ 未找到 ID={target_id}")
        return
    remove_target(target_id)
    print(f"✅ 已移除 [{t['platform']}] {t['target_id']}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)

    cmd = args[0].lower()
    if cmd == "add" and len(args) >= 4:
        gid = int(args[1])
        plat = args[2].lower()
        tid = args[3]
        if plat not in VALID_PLATFORMS:
            print(f"❌ 未知平台: {plat}, 支持: {', '.join(sorted(VALID_PLATFORMS))}")
            sys.exit(1)
        cmd_add(gid, plat, tid)
    elif cmd == "list":
        cmd_list()
    elif cmd == "remove" and len(args) >= 2:
        cmd_remove(int(args[1]))
    else:
        print(f"用法见文档。未知命令或参数不足: {args}")
        sys.exit(1)
