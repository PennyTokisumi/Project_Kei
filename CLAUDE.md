# Project Kei — QQ Bot

基于 **SnowLuma** (OneBot v11) + **NoneBot2** 的 QQ 群机器人：监控 B站动态/直播 和 斗鱼直播并推送到 QQ 群，
接入 LLM 实现 Kei 的角色扮演对话。

> 接手背景与变更记录见 [`DEEPSEEK.md`](DEEPSEEK.md)。本文件只描述**当前事实**，不含历史。

## 项目路径

```
H:\Agent\Project\Project_Kei\
```

## 技术栈

| 组件 | 用途 |
|------|------|
| **SnowLuma 2.0.3** | QQ 协议框架。OneBot v11 WS 服务端 `127.0.0.1:8080`、HTTP `:3000`、WebUI `:5099` |
| NoneBot2 | Python 异步机器人框架，作为 OneBot 客户端反连 SnowLuma |
| APScheduler | 轮询调度 + 定时消息 + 每日公告 |
| SQLite | 去重记录 / 直播状态 / 设置 / 记忆 / 用量 / 定时消息 |
| pystray + Pillow | Windows 系统托盘 |
| httpx | HTTP 客户端（B站 API、斗鱼 API、LLM API） |
| pydantic-settings | 配置管理（`.env` → `Config`） |
| **Node.js ≥ 24** | SnowLuma 2.x 强制要求（`check-node-version.cjs` 硬闸，当前 v24.17.0） |

## 目录结构

```
Project_Kei/
├── CLAUDE.md              # ← 本文件：项目事实说明
├── DEEPSEEK.md            # 接手说明 + 变更记录 + 遗留事项
├── start.bat              # 一键启动（SnowLuma → 等 .startup_ok → 关窗）
├── .gitignore
├── kei_bot_icon.ico       # 托盘图标（tray.py 直接加载此文件）
├── bot/                   # 主应用包
│   ├── bot.py             # 入口：_check_running() 双开检测 + 加载 4 个插件
│   ├── cli.py             # CLI 管理工具（add/list/remove）
│   ├── config.py          # Config + 版本号锁定 + SENSEI_QQ
│   ├── tray.py            # 系统托盘（图标/自启动/关闭机器人）
│   ├── pyproject.toml     # 版本号唯一来源 + 依赖
│   ├── .env / .env.prod   # 环境变量（含凭据，不入 git）
│   ├── kei_suicide.gif    # kawaii 指令用的图
│   ├── plugins/
│   │   ├── monitor/            # 功能①：监控推送
│   │   │   ├── __init__.py     # 生命周期钩子 + 上线问候
│   │   │   ├── database.py     # 建表 + CRUD（含 remove_target 清理逻辑）
│   │   │   ├── dedup.py        # Deduplicator
│   │   │   ├── formatter.py    # 直播消息 / 动态合并转发
│   │   │   ├── scheduler.py    # APScheduler 轮询 + reload_targets
│   │   │   └── sources/
│   │   │       ├── base.py           # Item 数据类 + SourceBase ABC
│   │   │       ├── base_tracker.py   # LiveStatusTracker（off→on + 抖动容错）
│   │   │       ├── bilibili_dynamic.py
│   │   │       ├── bilibili_live.py
│   │   │       └── douyu_live.py
│   │   ├── admin/              # 功能②：群指令
│   │   │   └── commands.py
│   │   ├── llm_chat/           # 功能③：LLM 对话 / 记忆
│   │   │   ├── __init__.py     # 指令 + @Kei 回复 + 自由聊天缓冲 + 记忆整理
│   │   │   ├── client.py       # DeepSeek / Gemini 双供应商客户端
│   │   │   ├── persona.py      # PERSONA_PROMPT
│   │   │   ├── memory.py       # 三层记忆 + build_context
│   │   │   ├── database.py     # 记忆/用量/短期表
│   │   │   ├── remember.py     # 自动提取长期记忆
│   │   │   ├── utils.py        # 消息解析（引用/转发/图片）
│   │   │   └── file_reader.py  # data/ 下文件安全读取
│   │   └── agent/              # 功能④：Claude 集成 + 定时
│   │       ├── __init__.py     # @Kei Claude / remind / daily
│   │       └── database.py     # scheduled_messages
│   └── utils/
│       ├── bilibili.py    # 共享 headers（注入 Cookie）
│       ├── db.py          # 全项目唯一 SQLite 连接入口
│       └── wbi.py         # B站 WBI 签名
├── tests/                 # pytest（60 个用例）
│   ├── conftest.py        # mock nonebot + 每例独立临时库
│   ├── test_bilibili_dynamic.py
│   ├── test_dedup.py
│   ├── test_douyu_live.py
│   ├── test_formatter.py
│   └── test_wbi.py
├── data/                  # 运行时状态（整体不入 git）
│   ├── db.sqlite3         # 主库
│   ├── .pid / .startup_ok # 双开检测 / 启动完成信号
│   ├── chatlogs/          # @Kei history 导出的群聊记录
│   ├── kei_claude/        # Kei↔Claude 会话（session.json + chat.md）
│   ├── logs/ seen/        # 日志 / 已读档
│   └── voice.txt?         # 注意：实际读取 data/seen/voice.txt 的位置不一致，见 DEEPSEEK.md
├── snowluma/              # SnowLuma 2.0.3（不纳入 git）
├── snowluma_v*/           # 升级时的旧版本备份（不纳入 git）
└── downloads/             # 安装包（不纳入 git）
```

## 关键设计决策

### 版本号锁定
`config.py` 在**模块导入时**一次性读取 `pyproject.toml` 的 `version` 存入 `VERSION`。
运行期间不重读 —— **以什么版本启动就永远返回什么版本**。
所以 `@status` 显示的版本反映的是*进程启动时*的磁盘版本，不是当前磁盘版本。改版本必须重启才可见。

### 数据库路径必须动态读
`utils/db.py` 的 `db_file()` 每次现算 `DATA_DIR / config.db_path`，`get_conn()` 按路径缓存连接
（路径变化会自动关旧连接重开）。**不要**把路径固化成模块级常量 —— 那样测试切临时库和运行时改配置都会失效，
而且测试会直接读写生产库。另有 `close_conn()` 供测试清理时释放句柄（Windows 上不释放就删不掉文件）。

### 两套 platform 命名（容易踩）
| 表 | `platform` 列的值 |
|---|---|
| `monitor_targets` | 完整 key：`bilibili_dynamic` / `bilibili_live` / `douyu_live` |
| `pushed_items` / `live_status` | `SourceBase.platform`：`bilibili` / `douyu`（type 另存 `item_type`） |

因此 `remove_target()` 用 `rpartition("_")` 把 `bilibili_live` 拆成 `bilibili` + `live`，
按 `platform + item_type` 两列精确删除，避免误删同 target_id 的其它平台记录。

### B站动态类型（`_WANTED_TYPES`）
只推送 4 种：
- `DYNAMIC_TYPE_DRAW` — 图文 / 纯文字（OPUS 统一格式）
- `DYNAMIC_TYPE_AV` — 视频投稿
- `DYNAMIC_TYPE_ARTICLE` — 文章 / 专栏（只推标题+封面，正文清空）
- `DYNAMIC_TYPE_FORWARD` — 转发动态（合并原文文字与图片，中间加 `----------`）

`DYNAMIC_TYPE_WORD` 已废弃，新版 B站 统一为 OPUS (DRAW)。

### OPUS 格式
文本在 `major.opus.title` + `major.opus.summary.text`，图片在 `major.opus.pics[].url`，`desc` 为 null。
代码优先读 OPUS，回退旧 `desc` 格式。

### HTTP → HTTPS 转换
QQ 拒绝 HTTP 图片，所有图片 URL 经 `_to_https()` 强制转换。

### 去重机制
- **动态**：`pushed_items` 表，ID 格式 `{platform}_{source_type}/{group_id}/{target_id}/{content_id}`，
  `INSERT OR IGNORE` 幂等。**含 group_id ⇒ 多群各自独立去重**。
- **直播**：`live_status` 表 + `LiveStatusTracker`，只在 off→on 时推送。
  `room_id` 存为 `{group_id}:{room_id}` 以支持多群独立追踪。
  连续 2 次空结果才确认下播（`_OFFLINE_CONFIRM`），容错 API 抖动。
- **启动同步**：首次轮询用 `pub_ts > _startup_ts` 过滤重启前的旧动态，避免刷屏；
  其余静默 `mark_pushed`。
- **推送顺序**：动态类**先 mark 后推送**，防止异常导致无限重推。

### 斗鱼的特殊处理
- 官方 `betard` API 为主，`_api_responded` 标志保证「官方有响应（哪怕报错）就不回退镜像」。
- `rst != 0` 视为轮播/重播，不算开播。
- 镜像接口无 `rst` 字段 ⇒ 标记 `is_fallback=True`，`poll_source` 直接跳过，不推送也不更新状态。
- vipId（自定义短链 `douyu.com/6657`）↔ 真实 room_id 双向解析。

### 每群独立设置
`settings` 表 `greeting_{group_id}`（`hello ON/OFF`）与 `llm_enabled_{group_id}`（`KEI ON/OFF`）。
**启动时 `llm_enabled_*` 会被全部清零**（`_llm_startup`）——这是有意的安全设计，每次重启需重新 `KEI ON`。

### 直播推送失败回滚
`LiveStatusTracker` 先设 `is_living=1` 再推送；推送失败回滚为 offline，下次轮询可重试。

### 消息格式
- **直播**：普通图文消息（封面图 + 标题/主播/链接）
- **B站动态**：合并转发（`send_group_forward_msg`，即使只有 1 条）。
  节点 `uin` 必须替换成 `bot.self_id`，否则 SnowLuma 拒发。

### 调度器共享陷阱
`monitor` 和 `agent` **共用同一个全局 APScheduler 实例**。
`reload_targets()` 删除任务时必须限定 `job.func is poll_source`，
否则一次 `add`/`remove` 会把 `agent` 注册的 `daily_*` / `scheduled_msg_*` 一起抹掉。

### 三层记忆
1. `PERSONA_PROMPT`（persona.py）—— **人设的唯一真源**。
   身份、性格、说话风格细则、输出格式全在这里，可审查 / 可 diff / 可跟代码一起回滚。
2. **长期** `llm_memory`：只存**事实类**记忆（关系、生日、往事、偏好）。
   `importance == 1.0` 视为锁定，自动整理（`get_tidyable_memories` / `replace_memories`）不会动它。
   风格/性格条目**不再进数据库**（v2.1.8 前是 15 条 `[说话风格]`/`[性格特点]`，已合并进 persona.py）。
3. **短期** `llm_short_term` + 内存 `deque(maxlen=10)`，按群隔离。

**记忆预算分两层计，互不挤占**（`memory.py`）：
`MEMORY_LOCKED_LIMIT = 20`（imp ≥ 1.0）+ `MEMORY_FACT_LIMIT = 30`（imp < 1.0）。
v2.1.8 前是统一 `ORDER BY importance DESC LIMIT 40` —— 锁定条目一多就会把近期事实类记忆全部挤出上下文。

**人设自动学习默认关闭**（`config.persona_autolearn = False`）。
`_learn_voice()` / `_learn_from_voice()` 会把 `[说话风格]` / `[性格特点]` 写回 `llm_memory`，
与 persona.py 形成两个真源。要重新蒸馏人设时才临时打开。

`build_context()` 会插入 `── MM-DD HH:MM ──` 时间分隔符（间隔 > 300s 才分隔）并标记 `（刚刚）`。
写记忆的路径（`replace_memories` / `cleanup_memory` / `delete_memory_by_keyword`）**必须调 `_invalidate_cache()`**，
否则 `_mem_cache` 会返回旧数据。

### 两条对话通道
| 通道 | 规则 | 行为 |
|---|---|---|
| `@Kei` | priority 6，`block=True`，要求 `llm_enabled` | 即时回复，注入引用原文 |
| 自由聊天 | priority 10，排除带 @ 的消息 | 进缓冲区，`IDLE_TIMEOUT 5s / MAX_WAIT 10s / MAX_BATCH 10`，由 LLM 自行判断 `[PASS]` / `[SEP]` 分段 |

缓冲区用**版本号防竞态**：每条新消息重建 task 并递增 `_buffer_version`，旧 task 检测到版本不符即自行退出。

### `event.reply` 的真实来源（重要）
`event.reply` **不是 SnowLuma 给的**，而是 `nonebot-adapter-onebot` 的 `_check_reply()` 造成的：
发现 `type=="reply"` 段 → 调 `get_msg` → 用 `Reply` 模型校验 → 成功才赋值，**并顺手删掉该 reply 段**。
失败时只打一条 WARNING 就 return，`event.reply` 保持未设置。

`llm_chat/utils.py::get_reply_text()` 的两个分支即由此而来：
`event.reply` 有值 = 正常路径；为空 = 适配器失败，此时 reply 段还在消息里，自己再 `get_msg` 补救。
**该函数返回空串不代表「用户没引用」**，也可能是取了但失败（debug 日志有记录）。

## 指令全表

按 priority 由高到低匹配，全部要求 `@机器人`。

| P | 指令 | 权限 | 说明 |
|---|---|---|---|
| 3 | `status` | 所有人 | 版本/目标数/运行时长 |
| 3 | `claude <任务>` | Sensei | 经 Kei 转述问 Claude CLI（需 `KEI ON`） |
| 3 | `remind <时间> <内容>` | 所有人 | 一次性定时消息 |
| 3 | `daily <HHMM> <内容>` / `daily off` | 所有人 | 每群每日公告 |
| 4 | `KEI ON/OFF` | Sensei | 开关 LLM 群聊（ON 会先验证 API） |
| 4 | `LLM` | Sensei | 今日/昨日/累计 token 用量与成本 |
| 4 | `read <文件>` | Sensei | 读 `data/` 下文件并提取记忆 |
| 4 | `memory` | Sensei | 列出长期记忆 |
| 4 | `remember <IMP> <内容>` | Sensei | 添加记忆 |
| 4 | `edit <序号> <内容>` | Sensei | 改记忆内容 |
| 4 | `imp <序号> <数字>` | Sensei | 改重要性 |
| 4 | `forget <序号>` | Sensei | 删记忆 |
| 4 | `sensei` | Sensei | 含隐藏指令的完整帮助 |
| 4 | `history [N]` | Sensei | 拉群聊历史存 `data/chatlogs/` |
| 5 | `add <平台> <ID>` | 所有人 | 加监测目标（自动解析显示名） |
| 5 | `list` | 所有人 | 本群目标列表 |
| 5 | `remove <序号>` | **群主/管理员** | 移除目标 |
| 5 | `help` | 所有人 | 帮助 |
| 5 | `hello ON/OFF` | 所有人 | 开关启动问候 |
| 5 | `chat` | 所有人 | 随机台词 |
| 5 | `kawaii` | 所有人 | 禁言自己 60s + 发 gif |
| 6 | `@Kei <文本>` | 所有人 | LLM 自然回复（需 `KEI ON`） |
| 10 | 任意群消息 | — | 自由聊天缓冲（需 `KEI ON`） |
| 99 | `@Kei <未知>` | 所有人 | 兜底随机台词 |
| — | 拍一拍 | 所有人 | 被拍时回拍 + 回复 |

`add` 支持的平台：`bilibili_dynamic` / `bilibili_live` / `douyu_live`。

> 注意 `startswith` 是**大小写敏感**的，`@Kei kei on` 不生效，必须 `KEI`。

## 常用命令

```bash
# 测试（项目根目录）
.venv\Scripts\python -m pytest tests/ -q

# 启动
start.bat

# CLI 管理（注意：CLI 加的目标准入需重启才生效）
cd bot
..\.venv\Scripts\python cli.py add <group_id> <platform> <target_id>
..\.venv\Scripts\python cli.py list
..\.venv\Scripts\python cli.py remove <id>
```

## Git 策略
- **commit**：自行决定，随时提交
- **tag / push**：必须先询问用户
- 已打 tag：`v2.1.2`（基线）… `v2.1.7`。回滚用 `git reset --hard v2.1.2`

## 角色设定
机器人名为 **Kei**，原型是游戏《蔚蓝档案》的天童ケイ，也叫做 kei、KEI、凯伊，称呼用户为"老师"（Sensei）。
消息风格偏中文/日语双语，以简体中文为主。可夹杂部分简单常用的日语（二次元动画游戏里常见的、或日常会话常用的），
但主体使用简体中文。

> 完整的角色提示词见 [`bot/plugins/llm_chat/persona.py`](bot/plugins/llm_chat/persona.py)，那里才是人设的唯一真源。

## 当前状态

- **版本**：v2.1.8
- **测试**：60 passed
- **SnowLuma**：2.0.3（`2.0.3-node` / protocol v11），Node v24.17.0
- **长期记忆**：26 条（锁定 10 / 可整理 16）
- **运行中群**：2 个（`Schale Secret Cafe`、`【FGO】Changing Seasons`）
- **功能**：B站全类型动态 + B站直播 + 斗鱼直播监控推送；群管理指令；LLM 角色扮演
  （DeepSeek/Gemini 双供应商）；人设文件 + 事实记忆 + 短期记忆；自由聊天批量判断；
  Claude CLI 集成；定时提醒与每日公告。
