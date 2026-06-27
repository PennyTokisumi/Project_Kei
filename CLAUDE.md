# Project Kei — QQ Bot 监测推送

基于 **NapCatQQ** + **NoneBot2** 的 QQ 群机器人，监控 B站动态/直播 和 斗鱼直播，推送通知到 QQ 群。

## 项目路径

```
H:\Agent\Project\Project_Kei\
```

## 技术栈

| 组件 | 用途 |
|------|------|
| NapCatQQ | QQ 协议桥，OneBot WebSocket 服务端 (`:8080`) |
| NoneBot2 | Python 异步机器人框架，OneBot 客户端 |
| APScheduler | 定时轮询调度 |
| SQLite | 去重记录 + 直播状态 + 设置持久化 |
| pystray | Windows 系统托盘 |
| httpx | HTTP 客户端（B站 API、斗鱼 API） |
| pydantic-settings | 配置管理（`.env` → `Config`） |

## 目录结构

```
Project_Kei/
├── CLAUDE.md              # ← 本文件
├── start.bat              # 一键启动脚本（NapCat → NoneBot）
├── .gitignore
├── bot/                   # 主应用包
│   ├── bot.py             # NoneBot 入口
│   ├── cli.py             # CLI 管理工具（add/list/remove）
│   ├── config.py          # 配置 + 版本号锁定
│   ├── tray.py            # 系统托盘（绿点/黄点）
│   ├── pyproject.toml     # 项目元数据 + 依赖
│   ├── .env / .env.prod   # 环境变量
│   ├── plugins/
│   │   ├── monitor/       # 核心监控插件
│   │   │   ├── __init__.py     # 生命周期钩子 + 上线问候
│   │   │   ├── database.py     # SQLite CRUD + 去重表
│   │   │   ├── dedup.py        # 去重工具类
│   │   │   ├── formatter.py    # 消息格式化（直播/动态转发）
│   │   │   ├── scheduler.py    # 轮询调度器
│   │   │   └── sources/
│   │   │       ├── base.py           # Item 数据类 + SourceBase 抽象
│   │   │       ├── base_tracker.py   # LiveStatusTracker (off→on)
│   │   │       ├── bilibili_dynamic.py
│   │   │       ├── bilibili_live.py
│   │   │       └── douyu_live.py
│   │   └── admin/
│   │       ├── __init__.py
│   │       └── commands.py    # add/list/remove/status/hello/help
│   └── utils/
│       └── wbi.py             # B站 WBI 签名
├── tests/                     # pytest 测试套件（43 测试）
│   ├── conftest.py
│   ├── test_bilibili_dynamic.py
│   ├── test_dedup.py
│   ├── test_douyu_live.py
│   ├── test_formatter.py
│   └── test_wbi.py
└── napcat/                    # NapCatQQ 安装（不纳入 git）
```

## 关键设计决策

### 版本号锁定
`config.py` 在模块导入时一次性读取 `pyproject.toml` 中的 `version`，存入 `VERSION` 常量。运行期间不会重新读取文件——以什么版本启动就永远返回什么版本。

### B站动态类型（`_WANTED_TYPES`）
只推送 4 种类型：
- `DYNAMIC_TYPE_DRAW` — 图文 / 纯文字（OPUS 统一格式）
- `DYNAMIC_TYPE_AV` — 视频投稿
- `DYNAMIC_TYPE_ARTICLE` — 文章 / 专栏
- `DYNAMIC_TYPE_FORWARD` — 转发动态

`DYNAMIC_TYPE_WORD` 已废弃，新版 B站 统一为 OPUS (DRAW)。

### OPUS 格式
新版 B站 的 OPUS 系统中，文本在 `major.opus.title` + `major.opus.summary.text`，图片在 `major.opus.pics[].url`，`desc` 为 null。代码优先读取 OPUS，回退到旧 `desc` 格式。

### HTTP → HTTPS 转换
QQ 拒绝 HTTP 图片，所有图片 URL 通过 `_to_https()` 强制转换。

### 去重机制
- **动态**：`pushed_items` 表，`INSERT OR IGNORE` 按 `{platform}_{source_type}/{target_id}/{content_id}` 去重
- **直播**：`live_status` 表 + `LiveStatusTracker`，检测 off→on 状态变化才推送
- **启动同步**：首次轮询用 `pub_ts < _startup_ts` 过滤旧动态，避免重启后刷屏

### 多群去重
`remove_target()` 在最后一个群移除时清理 `pushed_items`，避免目标从 A 群移到 B 群后不推送。

### 每群独立设置
`settings` 表支持 `greeting_{group_id}` 键，`hello ON/OFF` 命令切换。启动问候检查此设置。

### 直播推送失败回滚
`LiveStatusTracker` 先设 `is_living=1` 再推送；推送失败时回滚状态。

### 消息格式
- **直播**：纯文本消息（封面图 + 标题/主播/链接）
- **B站动态**：合并转发消息（即使只有 1 条），视频/专栏有专用格式

## 常用命令

```bash
# 运行测试（从项目根目录）
cd H:\Agent\Project\Project_Kei
.venv\Scripts\python -m pytest tests/ -v

# 启动机器人
start.bat

# CLI 管理
cd bot
..\.venv\Scripts\python cli.py add <group_id> <platform> <target_id>
..\.venv\Scripts\python cli.py list
..\.venv\Scripts\python cli.py remove <id>
```

## Git 策略
- **commit**：自行决定，随时提交
- **tag / push**：必须先询问用户

## 角色设定
机器人名为 **Kei**，称呼用户为 "老师"（先生/Sensei），消息风格偏日语/汉语双语。上线问候："これから、先生のことを見守らせていただきますね。\n今后就让我来守护老师吧。"

## 当前状态
- **版本**：v1.6.8
- **测试**：43 passed, 全部通过
- **功能**：B站全类型动态 + B站直播 + 斗鱼直播，per-group greeting 开关
