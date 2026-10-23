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
│   │   │   ├── formatter.py    # 直播消息 / 动态合并转发 / Pixiv 合并转发
│   │   │   ├── media.py        # 图片下载 + 压缩 + base64（Pixiv 专用）
│   │   │   ├── scheduler.py    # APScheduler 轮询 + reload_targets
│   │   │   └── sources/
│   │   │       ├── base.py           # Item 数据类 + SourceBase ABC
│   │   │       ├── base_tracker.py   # LiveStatusTracker（off→on + 抖动容错）
│   │   │       ├── bilibili_dynamic.py
│   │   │       ├── bilibili_live.py
│   │   │       ├── douyu_live.py
│   │   │       └── pixiv_illust.py   # Pixiv 画师作品监测
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
├── tests/                 # pytest（110 个用例）
│   ├── conftest.py        # mock nonebot + 每例独立临时库
│   ├── test_bilibili_dynamic.py
│   ├── test_dedup.py
│   ├── test_douyu_live.py
│   ├── test_formatter.py
│   ├── test_pixiv_illust.py
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

### 平台命名与别名
`monitor_targets.platform` 存**完整 key**（带下划线），因为 `remove_target()` 用 `rpartition("_")` 拆。
用户输入允许用别名，在 `admin/commands.py::PLATFORM_ALIASES` 和 `cli.py` 里统一映射：

| 用户输入 | 实际存储 |
|---|---|
| `pixiv` / `px` | `pixiv_illust` |

### 消息格式
- **直播**：普通图文消息（封面图 + 标题/主播/链接）
- **B站动态**：合并转发（`send_group_forward_msg`，即使只有 1 条）。
  节点 `uin` 必须替换成 `bot.self_id`，否则 SnowLuma 拒发。
- **Pixiv**：合并转发，`source` = `{画师}投稿了P站`，`news` = `[标题]`，
  节点内是 `标题 → 图片(≤9) → [超限提示] → 空行 → 链接`。

### Pixiv 监测的特殊处理（踩过坑，别乱改）
- **接口**：`ajax/user/{uid}/profile/top` 一次请求拿到近期作品，每条自带
  `xRestrict`（R18 标记）和 `createDate`。**不需要登录**，但 UA + Referer 必须带上。
- **R18 判定只看 `xRestrict`**（0=全年龄 1=R18 2=R18G）。**不要用 `sl` 判断** ——
  实测全年龄作品上 `sl` 可能是 2 也可能是 6，它表示的是别的分级维度。
- **`Profile/top` 的 `tags` 是字符串列表**，而 `ajax/illust/{id}` 是 `[{"tag": ...}]` 对象列表。
  `_parse_tags()` 两种都兼容 —— 只写一种会在真机上直接 AttributeError。
- **图片必须自己下载**：`i.pximg.net` 有防盗链，无 Referer 返回 403。
  所以走 `media.py` 的「下载 → 等比缩放到长边 ≤1200 → JPEG q85 → base64」，
  再用 `MessageSegment.image("base64://...")` 发（SnowLuma 的 `inlineBase64Payload` 认这个）。
  **直接传 URL 给 QQ 一定是裂图。**
- **首次订阅静默同步**：`_sync_pixiv_new_items()` 用 `has_pushed_items()` 判断 ——
  该目标还没有任何推送记录时，把当前所有作品标记为已推送但**一条都不发**。
- **重启补推**：和 B站不同，Pixiv 用数据库判断而非启动时间戳，所以停机期间画师更新能补推；
  但最多补 3 条（`PIXIV_BACKLOG_LIMIT`），多出来的静默标记。
  补推内容全部塞进**同一个合并转发**，不会刷屏。
- **轮询间隔**：`pixiv_poll_interval`（默认 300s），由 `poll_interval_for()` 按平台分派。
  和 B站的 30s 分开，别写成同一个值。
- **新目标立即首轮**：`reload_targets()` 给新增任务设 `next_run_time = now + 10s`
  （`NEW_TARGET_FIRST_POLL_DELAY`），否则 Pixiv 要干等 5 分钟才第一次抓取，用户会以为没生效。
  `start()` 里不加这个 —— 启动时让它们按各自间隔散开，避免开机瞬间一串请求。
- **代理**：Pixiv 在国内不能直连，代码**不显式配代理**，依赖 httpx 默认的
  `trust_env=True` → `urllib.getproxies()` → 读 Windows 系统代理。Clash 关掉就推不了。

### Pixiv 大规模订阅的限流设计
订阅数会上百，四道防线缺一不可：

| 机制 | 位置 | 说明 |
|---|---|---|
| **间隔自适应** | `pixiv_interval_for()` | `max(pixiv_poll_interval, 订阅数 × PIXIV_MIN_SECONDS_PER_TARGET)`。<br>每个订阅平均占 6 秒 → 20 个以内仍是 300s，**100 个自动变 600s**，总速率恒定 |
| **启动错峰** | `start()` | 首轮 `next_run_time` 在 `[10s, interval]` 内随机，不再让所有任务都在「开机 + interval」同时触发 |
| **抖动放大** | `jitter_for()` | Pixiv 用 60s（其他平台 5s），把请求摊开 |
| **失败退避** | `PixivIllust._register_failure()` | 403/429 从 300s 起指数退避（封顶 1 小时），网络错误 60s 起。<br>日志会明确写「限流：HTTP 429，退避 600s（连续第 2 次）」 |

**退避为什么重要**：早期版本遇到 403 只打一条 warning 就返回空列表，
日志上完全分不清「画师没更新」和「我们被限流了」，排查时会被误导。
`is_backing_off` 属性可用于诊断。

**注意请求放大**：每个新作品除 1 次 `profile/top`，还要 1 次 `pages` + 最多 9 次图片下载 ≈ 11 个请求。

### 大规模订阅的列表分页
`list` 只显示 B站/斗鱼；Pixiv 走独立的 `pixivlist`：

- **每页 20 个**（`PIXIV_LIST_PAGE_SIZE`），默认第 1 页，合并转发，**一页一个节点**
- **排序按添加顺序（`id` 升序）** —— 新增订阅只追加到末尾，前面每一页永远稳定。
  按 uid 排序会让新订阅插到中间、后面所有页边界漂移，所以不采用。
- **序号只用于阅读**，删除一律用 `remove pixiv <UID>`，不会因为编号漂移删错。
- **`paginate_pixiv()` 是纯函数**，分页逻辑全部可单测。

⚠️ `list` 与 `remove <序号>` **必须用同一套过滤**（都排除 `pixiv_illust`），
否则序号错位会删错目标。改任一处都要同步改另一处。

### 软删除说明
`remove_target()` 是**软删除**（`enabled=0`），配合 `add_target` 的
`ON CONFLICT ... DO UPDATE SET enabled=1` 支持重新订阅时复用旧行。
只有所有群都移除了，才会清空该目标的去重记录与直播状态。
所以库里存在大量 `enabled=0` 的行是**正常现象**，不是垃圾数据。

### 进程退出审计（`data/logs/shutdown.log`）
托盘菜单的「关闭机器人」走的是 `os._exit(0)` —— **不执行 atexit、不刷日志缓冲，事后零痕迹**。
2026-10-23 就是因为这个，一次误点导致服务消失 21.5 小时却查不出原因。

现在 `tray.py` 会在两处各写一行：

| 时机 | 内容 |
|---|---|
| `TrayIcon.start()` | `进程启动（托盘已就绪）  self_pid=...` |
| `_on_shutdown()` | `托盘菜单「关闭机器人」  self_pid=...  killed=[...]` |

**排查口诀**：
- 有启动行 + 有对应退出行 → 正常从托盘关闭
- 有启动行 + **没有**退出行 → 被外部强杀（taskkill / 任务管理器 / 会话注销）
- 两者都没有 → 进程没起来，或根本不是这个入口启动的

`_kill_snowluma()` 现在返回被终止的 PID 列表，方便记录到底杀了谁。

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

### 回复分段与图片
- **一问多答**：模型可用 `[SEP]` 主动把回复拆成 2–3 条短消息，`_split_reply()` 负责拆并限段数
  (`MAX_REPLY_SEGMENTS = 3`)，各段间隔 `REPLY_SEGMENT_DELAY = 0.6s` 依次发出。
  第一条带 @，后续直接发。
- **冷却不是丢弃**：`memory.cooldown_remaining()` 返回还差几秒，调用方 `await` 等它过去再答。
  早期版本直接 `return`，会造成「连问几句只答一次」甚至完全不理会。
- **图片识别**：`extract_images()` 取最多 `MAX_IMAGES_PER_MESSAGE = 3` 张 QQ 图片 URL，
  由 `build_context(images=...)` 把**当前消息**发成 OpenAI 多模态格式（text + `image_url`）。
  历史消息的图片不重发（URL 有有效期，且重复计费无意义）。
  实测 `deepseek-v4-flash` 可直接吃外部图片 URL，但**必须关掉 thinking** —— 开着的话推理 token
  会把 `max_tokens` 吃光，`content` 返回空串。`client.py` 默认就发 `thinking: disabled`，别改。
- 既无文字又无图片的消息（纯视频/贴纸）直接跳过，不进记忆。

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
| 5 | `list` | 所有人 | 本群目标列表（**不含 Pixiv**） |
| 5 | `pixivlist [页码]` | 所有人 | Pixiv 订阅列表，每页 20，默认第 1 页 |
| 5 | `remove <序号>` | **群主/管理员** | 移除目标（序号来自 `list`） |
| 5 | `remove pixiv <UID>` | **群主/管理员** | 取消 Pixiv 订阅（UID 或名字） |
| 5 | `help` | 所有人 | 帮助 |
| 5 | `hello ON/OFF` | 所有人 | 开关启动问候 |
| 5 | `chat` | 所有人 | 随机台词 |
| 5 | `kawaii` | 所有人 | 禁言自己 60s + 发 gif |
| 6 | `@Kei <文本>` | 所有人 | LLM 自然回复（需 `KEI ON`） |
| 10 | 任意群消息 | — | 自由聊天缓冲（需 `KEI ON`） |
| 99 | `@Kei <未知>` | 所有人 | 兜底随机台词 |
| — | 拍一拍 | 所有人 | 被拍时回拍 + 回复 |

`add` 支持的平台：`bilibili_dynamic` / `bilibili_live` / `douyu_live` / `pixiv_illust`
（可简写 `pixiv`，画师 UID，例如 `add pixiv 38297201`）。

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
- 已打 tag：`v2.1.2`（最初的基线）… `v2.1.9`
- **当前基线 = `v2.1.9`**：包含推送样式（直播 Ark 卡片 + 合并转发定制）、
  人设收敛（persona.py 为唯一真源）、LLM 交互（图片识别 / 多段回复 / 冷却不丢弃）三批改动。
  回滚用 `git reset --hard v2.1.9`（或回退到更早的 `v2.1.2`）。

## 角色设定
机器人名为 **Kei**，原型是游戏《蔚蓝档案》的天童ケイ，也叫做 kei、KEI、凯伊，称呼用户为"老师"（Sensei）。
消息风格偏中文/日语双语，以简体中文为主。可夹杂部分简单常用的日语（二次元动画游戏里常见的、或日常会话常用的），
但主体使用简体中文。

> 完整的角色提示词见 [`bot/plugins/llm_chat/persona.py`](bot/plugins/llm_chat/persona.py)，那里才是人设的唯一真源。

## 当前状态

- **版本**：v2.3.1
- **测试**：110 passed
- **SnowLuma**：2.0.3（`2.0.3-node` / protocol v11），Node v24.17.0
- **长期记忆**：26 条（锁定 10 / 可整理 16）
- **运行中群**：2 个（`Schale Secret Cafe`、`【FGO】Changing Seasons`）
- **功能**：B站全类型动态 + B站直播 + 斗鱼直播 + **Pixiv 画师作品**监控推送；
  群管理指令；LLM 角色扮演（DeepSeek/Gemini 双供应商，**支持识别群友发的图片**）；
  人设文件 + 事实记忆 + 短期记忆；自由聊天批量判断；回复可拆多条发送；
  Claude CLI 集成；定时提醒与每日公告。
