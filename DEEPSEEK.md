# DEEPSEEK.md — 接手说明

> 本文件是 **变更记录 / 遗留事项 / 环境坑** 的集合。
> 项目的事实性说明（架构、指令表、设计决策）在 [`CLAUDE.md`](CLAUDE.md)，两者不重复。

## 0. 背景

Project Kei 原本由 **Claude Code** 开发（`git log` 到 v2.1.1 为止的提交都是它做的）。
**2026-10-19 起由 DeepSeek 接手。** 本文件记录接手后发生了什么、哪些东西还没做、以及踩过的坑。

接手时的状态是失真的：`CLAUDE.md` 停在 v1.9.10 / 63 测试，而磁盘上已经是 v2.1.1 / 43 测试，
且有一个 793 行的 `plugins/agent` 完全没被记录。**不要相信旧文档的版本号和测试数。**

---

## 1. 提交历史（接手后）

| 提交 | 内容 |
|---|---|
| `9c062be` | **v2.1.2 基线**：托盘图标换成 `kei_bot_icon.ico`；`.gitignore` 收敛（`data/`、`.claude/` 整体忽略）；版本 2.1.1 → 2.1.2 |
| `0213303` | **v2.1.3 修复批次**：6 个问题，见下节 |
| `0211b32` | 修正 `get_reply_text` 关于 `event.reply` 来源的错误注释 + 补 debug 日志 |
| `42752b1` | `.gitignore` 忽略 `snowluma_v*/`（升级备份目录） |
| 本次 | `CLAUDE.md` 重写为真实状态；新增本文件 |

**`9c062be`（v2.1.2）是约定的回滚基线。**

---

## 2. v2.1.3 修了什么（都实测验证过）

### 2.1 定时任务被误删（**真 bug，影响已上线功能**）
`scheduler.reload_targets()` 原本按 job id 差集无差别删除：

```python
for job_id in current_jobs - wanted_ids:
    scheduler.remove_job(job_id)
```

`monitor` 和 `agent` 共用同一个全局 APScheduler，所以 **任何一次 `add` / `remove` 都会把
`agent` 的 `daily_*` 和 `scheduled_msg_*` 一起抹掉**。改为只删 `job.func is poll_source` 的任务。

> 实测：修复前 `reload_targets()` 后 `daily_123456` / `scheduled_msg_42` 消失；修复后在 add 与 remove
> 两条路径下都存活，同时轮询任务正常增删。

### 2.2 `DB_PATH` 导入时固化
`utils/db.py` 原本 `from config import DB_PATH` 后 `sqlite3.connect(str(DB_PATH))` —— 导入即定死。
后果不只是"改配置不生效"：**`tests/conftest.py` 的临时库 fixture 从来没真正生效过**，
43 个测试一直在读写**生产库** `data/db.sqlite3`。

改为 `db_file()` 每次现读 `DATA_DIR / config.db_path`，`get_conn()` 按路径缓存连接
（路径变了就关旧连接重开），并新增 `close_conn()`。

> 实测：修好后 6 个 test_dedup 从失败转绿，43 passed。`conftest` 里补了 `close_conn()`
> 以便清理临时库（Windows 不释放句柄就删不掉文件），现在单次测试运行新增残留 **0** 个。

### 2.3 B站直播每轮多打一次接口
`bilibili_live.fetch()` 每次只要在播就调 `_get_username()` 查主播名，而主播名是不变的。

> 实测（mock 计数）：修复前 3 次 fetch 共 **6 个**请求（`get_info` + `card` 成对），
> 修复后 **4 个**（首轮 2 个，之后每轮 1 个）。主播名缓存在 source 实例上，
> 而调度器对同一目标复用同一实例，所以稳态每轮 1 个请求。

### 2.4 `remove_target` 删除范围过宽
原本只按 `target_id` 删 `pushed_items` / `live_status`，会误删同 id 的其它平台记录
（如 B站直播间 123 与 B站动态 UID 123）。改为 `rpartition("_")` 拆出
`platform + item_type` 两列精确匹配。

### 2.5 记忆缓存不失效
`_mem_cache` 只在部分写路径失效。除了 `delete_memory_by_keyword`，
**`replace_memories`（自动整理后全量替换）和 `cleanup_memory` 也漏了** ——
意味着每次自动整理完记忆，去重判断仍在用旧缓存。三处都补上 `_invalidate_cache()`。

### 2.6 token 计价写死
`handle_llm_usage` 里 `price_prompt = 2.02 / price_completion = 3.02` 硬编码，
但 provider 可能是 DeepSeek 也可能是 Gemini，单价必然不同。
改为 `config.llm_price_prompt` / `config.llm_price_completion`，**默认值保持原数字不变**
（我没有猜真实单价，需要用户自己在 `.env` 覆盖）。

### 2.7 清理项
- 删除零调用的 `load_short_term_global` / `cleanup_short_term_global`（全局记忆方案废弃后的遗留）
- 删除零引用的 `Config.onebot_ws_hosts`（适配器直读 `ONEBOT_V11_WS_URLS`，此项纯误导）
- 删除 `config.DB_PATH`（2.2 的病根，留着怕被误用）
- `UNKNOWN_MSGS` 移到首次使用之前（原先靠运行时解析侥幸能过）
- `memory.py` 去掉未调用的 `load_short_term` 导入
- `_flush_buffer` 里恒为真的死分支简化（行为不变）
- 修正过时测试 `test_fetch_both_fail`（它假设斗鱼官方失败后回退镜像，但实现是故意不回退的）

---

## 3. 明确**不改**的东西（用户已确认是设计）

| 项 | 说明 |
|---|---|
| `llm_enabled_*` 每次启动清零 | `_llm_startup()` 主动清零。**每次重启都要重新 `@Kei KEI ON`**，否则 `@Kei` 会被 priority 99 的兜底接走回固定台词 |
| `add` / `hello` 无权限校验 | 任何群成员都能加监测目标、开关问候；只有 `remove` 校验群主/管理员 |
| `startswith` 大小写敏感 | `@Kei kei on` 不生效，必须 `KEI` |

---

## 4. 遗留事项

### 4.1 `voice.txt` 路径不一致（已确认是死代码，无需修复）
`llm_chat/__init__.py::_learn_from_voice()` 读的是 `data/voice.txt`，
但实际文件在 **`data/seen/voice.txt`**。

**但这只是表象。** 该函数真正的提前返回点在**更前面**的守卫：

```python
if any("[语音风格]" in m or "[性格特点]" in m for m in existing):
    _VOICE_LEARNED = True
    return          # ← 这里就先返回了，根本走不到读文件的代码
```

而数据库里**已经存在 5 条 `[性格特点]` 记忆**（另有 10 条 `[说话风格]`），
所以守卫必然命中 —— **就算把路径改对，函数也在到达之前就返回了，改路径不会有任何效果**。

结论：这条路径对人设没有实际缺失。当前长期记忆里已有 15 条 `importance=1.0` 的
风格/性格条目（`_learn_voice()` 从 20 多条预设台词提炼的那条路径是正常工作的）。
**除非想"重新学习"（需要先删掉这 15 条再从 `data/seen/voice.txt` 重跑），否则不用动。**

### 4.2 中文长期记忆检索几乎无效（暂不修）
`database.search_memory()` 用 `query.split()` 切词，中文整句会变成一个 token 再 `LIKE '%整句%'`，
基本匹配不到。**但 `memory.recall()` 全项目零调用**，`search_memory` 唯二用处是
`agent._seed_kei_claude_memory()` 查一次 Claude 关系记忆。所以影响面极小。
真要做检索时应该整体设计（目前 `build_context` 是固定灌 `get_all_memories(limit=40)`）。

### 4.3 调度器共享架构偏脆
`monitor` 和 `agent` 直接操作同一个全局 `scheduler` 实例（`agent` 用
`from plugins.monitor.scheduler import scheduler`）。2.1 的修复只堵住了"误删"这一个口子，
架构上仍然是隐式耦合。若以后再加定时类功能，建议给 scheduler 加一个带命名空间的注册封装。

### 4.4 `data/_*_diag` 调试残留
`data/` 下有 4 个 7 月的诊断文件（`_douyu_fetch_diag` 等），是历史排查留下的。
属于用户数据，**我没有删**，且 `data/` 已整体 gitignore。

### 4.5 测试覆盖盲区
43 个用例只覆盖 `monitor`（解析/去重/格式化/WBI）和斗鱼双 API。
**`llm_chat` 和 `agent` 完全没有测试** —— 这两个插件目前只能靠人工回归。
改动它们时至少跑一次 4 插件加载冒烟（见第 6 节）。

---

## 5. SnowLuma 升级记录（1.12.9 → 2.0.3）

2026-10-19 完成。**不是**从用户以为的 1.11.2 升的 —— 实际安装的是 1.12.9
（`package.json` 的 `version` 字段证明，`downloads/` 里没有 1.12.9 的包，应是 WebUI 内置更新器升上去的）。

### 5.1 关键事实
- **唯一硬门槛是 Node ≥ 24**（`check-node-version.cjs` 硬闸）。本机 v24.17.0 ✅
- `native/` 必须与应用**成套**替换：1.x 用 Node 内置 `node:sqlite`，
  2.0.3 换成 `@appthreat/sqlite3` 原生扩展（新增 `native/sqlite/`）。**绝不能只覆盖 `index.mjs`**
- 发行包是**纯运行时**，不含 `config/` `data/` `logs/`；`release.json` 列出精确的 9 个条目
- 数据迁移是 **copy-only + `ALTER TABLE ADD COLUMN`**：`media.db` 的媒体元数据被复制进
  `messages.db` 的 `legacy_media_*` 表，**源文件保留**
- 升级方式：**新目录安装 + 搬 config/data**，不要新旧混放

### 5.2 实测迁移结果
`legacy_media_entries` 4133 行、`legacy_media_keys` 11144 行；
`messages` 表新增 `media_data` / `classification_version` / `sequence_authoritative` / `private_direction` 四列；
`media_migration_state` / `message_store_migration_state` 各 1 行。全部符合预期。

### 5.3 调研报告的两处**错误**（不要照抄）
| 报告结论 | 实际 |
|---|---|
| "EULA/PRIVACY 字节完全相同，无需重新同意" | **错**。两版文件 SHA-256 不同，协议版本从 `af07dc84e7818708` 变成 `dcb31a7b03a0b64d`，WebUI **需要重新点一次同意**（只挡面板，不影响 OneBot） |
| "SnowLuma 在 `event.reply` 中直接提供完整回复消息" | **错**。`event.reply` 是 nonebot-adapter 自己调 `get_msg` 造的，详见 CLAUDE.md 对应小节 |

### 5.4 回滚
旧版本完整保留在 `snowluma_v1.12.9_backup/`（含原始 config、data 及其 WAL、logs）。

```powershell
# 1. 停 v2.0.3（杀父进程）
Get-CimInstance Win32_Process -Filter "Name='node.exe'" |
  Where-Object { $_.CommandLine -match 'index\.mjs' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
# 2. 必须清掉残留的 launcher cmd 窗口（见 6.2），否则目录被锁
# 3. 换目录
Rename-Item .\snowluma .\snowluma_v2.0.3_broken
Rename-Item .\snowluma_v1.12.9_backup .\snowluma
```

> ⚠️ v2.0.3 已就地改写 `messages.db`（加列加表）。回滚到 1.x 大概率能跑（1.x 见到多余列不报错），
> 但**官方没有回滚支持声明**，且此后新产生的媒体元数据只写进 `messages.db`，1.x 不认。
> 所以要么尽早回滚，要么把这个备份当成只读快照。

---

## 6. 环境坑（只有实际跑过才知道）

### 6.1 DSH 沙箱只允许写工作区**根目录**
在这个项目里，沙箱（workspace-write）下 `pwsh` 只能写 `H:\Agent\Project\Project_Kei\` 本身，
**子目录 `bot/` `data/` `tests/` `.git/` 全部拒绝写入**。表现为：
- `git commit` 报 `Unable to create '.git/index.lock': Permission denied`
- `pytest` 报 `sqlite3.OperationalError: attempt to write a readonly database`
- `.pytest_cache` 报 `WinError 5 拒绝访问`

DSH 的 file 工具（`edit`/`write`/`read`）不受影响，只有 shell 受限。
遇到时不要怀疑代码 —— 先确认是不是沙箱。当前会话已放到 full access。

### 6.2 ⚠️ `launcher.bat` 的 `pause` 会留下孤儿窗口锁住目录
`snowluma/launcher.bat` 结尾是 `pause`，而 `start.bat` 用 **VBS 隐藏窗口**启动它。
后果：**node 被终止后，那个隐藏的 `cmd.exe /c cd /d ...\snowluma && launcher.bat` 会挂在 pause 上，
持有 `snowluma` 目录句柄，导致目录无法改名/删除。**

这个坑在升级时真的踩到了（rename 失败）。改 snowluma 目录之前必须：

```powershell
Get-CimInstance Win32_Process -Filter "Name='cmd.exe'" |
  Where-Object { $_.CommandLine -match 'snowluma' -and $_.CommandLine -match 'launcher\.bat' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

同理，`start.bat` 的 `:wait_loop` 等 `data\.startup_ok` **没有超时**，
所以 SnowLuma 起不来时会静默卡在"等待 Kei 启动完成..."。
用户表示目前没出过问题，暂不修改。

### 6.3 版本号锁定 ⇒ `@status` 报旧版本是正常的
见 CLAUDE.md「版本号锁定」。**不要**把 `@status` 显示旧版本当成 bug，先看进程启动时间。

### 6.4 PowerShell 不支持 heredoc
`git commit -F - <<'EOF'` 会 `ParserError`。改用临时文件：

```powershell
$msg = @'
提交信息
'@
$tmp = Join-Path $env:TEMP 'msg.txt'
Set-Content -Path $tmp -Value $msg -Encoding utf8 -NoNewline
git -c i18n.commitEncoding=UTF-8 commit -F $tmp
```

### 6.5 别杀错的 node
本机同时跑着 Adobe Creative Cloud 的 `node.exe`
（`...\Adobe Creative Cloud Experience\libs\node.exe`）。
按 PID 操作前**务必先核对 `CommandLine` 里是否含 `index.mjs`**。

---

## 7. 工作约定

### 7.1 能实测就不要只读代码
接手后每次下结论都尽量拿证据：
- 改调度器 → 起真的 APScheduler，塞 `daily_*` / `scheduled_msg_*`，调 `reload_targets()` 看 job 集合
- 改 B站直播 → mock `AsyncClient` 数请求次数
- 改 `db.py` → 跑全量 pytest，对比修复前后
- 改插件 → 用真 `nonebot.init()` 加载 4 个插件做冒烟

### 7.2 4 插件加载冒烟（改任何插件后都该跑）
```powershell
# 在 bot/ 目录下，用真 nonebot.init() 逐个 load_plugin
# plugins.monitor / plugins.admin / plugins.llm_chat / plugins.agent
```
这是 `llm_chat` 和 `agent` 目前唯一的结构性保护（它们没有单测）。

### 7.3 说"未确认"而不是猜
版本号、字段名、API 行为这类，查不到就明确标"未确认"。
SnowLuma 那份调研报告就是反面教材：整体质量很高，但有两处把推断写成了结论。

### 7.4 凭据
`bot/.env` 里有**真实可用的** B站 Cookie（含 `SESSDATA` / `bili_jct`）、DeepSeek key、Gemini key。
该文件已被 `.gitignore` 覆盖且从未入 git —— 改动时不要让它进版本库，也不要把内容写进文档或日志。

---

## 8. 当前可信任的基线

```
版本        : Kei v2.1.4  /  SnowLuma 2.0.3-node (protocol v11)
测试        : 43 passed（.venv\Scripts\python -m pytest tests/ -q）
插件        : monitor / admin / llm_chat / agent  全部可加载
端口        : 5099 WebUI / 8080 OneBot WS / 3000 OneBot HTTP
Node        : v24.17.0（SnowLuma 2.x 要求 ≥ 24）
回滚基线    : 9c062be (v2.1.2)
SnowLuma 备份: snowluma_v1.12.9_backup/
```

### 推送路径验证状态（2026-10-19）
SnowLuma 2.0.3 上的三条推送路径**均已实测通过**：

- **`send_group_forward_msg`（B站动态合并转发）** ✅ 用户以真实 B站动态触发，
  转发正常展开、图片未丢。这是 2.0 改动最多的部分（v1.14.13/.14/.17/.21 连续在修），
  验证通过意味着升级最关键的兼容性风险已排除。
- **`send_group_msg`** ✅ 开机问候与 @ 回复均走此路径。
- **`send_tuwen_ark`（直播 Ark 卡片）** ✅ v2.1.4 起作为直播推送首选，
  失败自动回退普通图文消息。

代码上 `send_group_forward_msg` **全项目只有一处调用**：`monitor/formatter.py::send_dynamic_forward()`。
两个直播源（`bilibili_live` / `douyu_live`）的 `source_type` 都是 `live`，
在 `scheduler.poll_source()` 里经 `is_live` 分派到 `send_live_notification()`。
因此**只有 B站动态走合并转发**，即使只有 1 条动态也用转发。

---

## 9. Ark 卡片实测结论（踩过的坑，别再重试）

直播 Ark 卡片是逐条**真机验证**出来的，以下结论都是实测，不是推测。改动样式前先读这节。

### 9.1 唯一可用的发卡方式
只有 OneBot 动作 **`send_tuwen_ark`** 能正常渲染，7 个参数：
`user_id` / `group_id`（二选一）、`title`、`desc`、`summary`、`preview_url`、`jump_url`。

**❌ 不要试图自己拼 ark JSON 走 `json` 消息段。** 虽然 SnowLuma 的 `json` 段转换器可用
（`case "json"` + `toSegment`），但自拼的 `com.tencent.structmsg` 会被 QQ 判定为非法卡片，
客户端直接显示「**发送者版本过低，无法展示内容**」。`share` 消息段造的也是同一个模板，同样受限。

### 9.2 字段到底渲染在哪

| 参数 | 位置 |
|---|---|
| `title` | 卡片内第 1 行（粗体，直播标题） |
| `desc` | 卡片内正文，单段落，只能自然折行 |
| `preview_url` | 卡片右侧缩略图 |
| `jump_url` | 点击跳转（无可见文字） |
| `summary` | **气泡外面**的消息预览文字（不是卡片内标签） |

### 9.3 换行的坑
- 普通 `\n` 在 `desc` 里会被**直接删除**（连空格都不留，两段会粘连）
- `U+2028`（LINE SEPARATOR）**只有桌面 QQ 断行**，手机 QQ 会渲染成空格；
  两个连用 = 手机上约 3 个空格，且不会产生空行
- 结论：**desc 只用单行**，靠自然折行

### 9.4 改不了的东西
卡片底部固定有一行「**其他来源**」——QQ 给未注册来源的 ark 打的默认标签。
`send_tuwen_ark` 没有 `source`/`tag` 参数，自拼 JSON 又被拒收，所以**无解，接受它**。

### 9.5 卡片中间的大片空白
手机端正文下方到底栏之间会有一块空白，是该模板为容纳右侧缩略图预留的固定高度，
`send_tuwen_ark` 不暴露宽高参数，**改不了**。

---

## 10. 合并转发卡片实测结论（2026-10-19）

`send_group_forward_msg` / `send_private_forward_msg` 除 `messages` 外还接受 4 个
预览参数（SnowLuma 的 `forwardPreviewParams`）。**私聊逐条实测结果：**

| 参数 | 实测效果 |
|---|---|
| `source` | ✅ **就是卡片标题行**。传「哔哩哔哩」→ 标题显示「哔哩哔哩」 |
| `news` | ✅ **逐条接管中间预览行**，完全取代 QQ 自动拼的「昵称：正文首行」 |
| `summary` | ✅ 底部文字，控制「查看 N 条转发消息」 |
| `prompt` | ❌ **没有任何可见效果**。传了「B站动态」，标题仍显示默认的「xxx的聊天记录」 |

`news` 的元素形态：字符串，或 `{"text": "..."}` 对象。**条数应与节点数一一对应。**

不传任何参数时，标题是「群聊的聊天记录」（群）/「{节点名}的聊天记录」（私聊），
预览行由 QQ 用「节点 name：正文首行」自动拼 —— 这正是原来视频投稿会显示
**「昵称：昵称投稿了视频」** 双昵称的原因（节点 name 和正文首行都带了昵称）。

### 文案分工（v2.1.6 定稿）
**标题行说「谁做了什么」，预览行说「标题/正文」** —— 这样折叠状态下就能直接看到
视频标题 / 文章标题 / 动态正文。

| 类型 | 标题行 `source`（单条） | 预览行 `news` |
|---|---|---|
| 视频投稿 | `{昵称}投稿了视频` | `标题：{视频标题}` |
| 专栏文章 | `{昵称}投稿了专栏` | `标题：{文章标题}` |
| 图文动态 | `{昵称}发布了动态` | `{正文首行}` |
| 转发动态 | `{昵称}转发了动态` | `{转发评论首行}`（已剥掉正文自带的「昵称：」前缀） |

多条合并时标题行退化为 `B站动态 · N 条更新`，昵称下沉到各条预览行。

节点正文（展开后看到的）同步调整：**不再重复昵称**（节点发送者名已承担），
各类顺序为 —— 视频：标题 → 封面 → 简介 →（正文）→ 链接；专栏：标题 → 封面 → 链接；
图文/转发：正文 → 图片 → 链接。

`send_dynamic_forward()` 带参数发送失败时会自动重试不带参数，避免丢推送。

> 只在**私聊**验证过。群里是否完全一致尚未实测，所以保留了失败回退。

---

## 11. 人设架构重构（v2.1.8）

### 11.1 重构前的四个问题

爆发点是核对 `llm_memory` 时发现的：**49 条长期记忆里有 32 条是 imp=1.0 的锁定条目**，
而 `build_context()` 只取 `ORDER BY importance DESC LIMIT 40` ——
锁定条目占满 80% 名额，**17 条可整理记忆里有 9 条永远进不了上下文**。
被挤掉的恰恰是近期、具体的事实（老师的告白、某部动画、某次对话），
留在 prompt 里的全是泛化的人设条款。

连带查出另外三个问题：

1. **规则打架**：`persona.py` 写「禁止括号」，记忆 #37 写「可酌情少量使用括号描写心理和动作」
2. **三重冗余**：`persona.py` 的性格段 + 15 条 `[说话风格]`/`[性格特点]` 记忆描述同一批特质
3. **`voice.txt` 是死代码**：见 4.1，守卫先返回，路径修了也没用

### 11.2 决策与落地

| 项 | 决策 | 落地 |
|---|---|---|
| **A** | 统一括号规则：**允许少量括号内心理/动作描写**，禁止括注语气元信息 | `persona.py`「输出格式」重写 |
| **B** | 记忆预算分层，互不挤占 | `memory.py` 用 `MEMORY_LOCKED_LIMIT=20` + `MEMORY_FACT_LIMIT=30`，新增 `get_locked_memories()` / `get_fact_memories()` |
| **C** | 人设收敛到 `persona.py`，数据库不再存风格条目 | 见 11.3 迁移 |
| **D** | 保留自动学习能力但**默认关闭** | 新增 `config.persona_autolearn`（默认 `False`），门控 `_learn_voice()` / `_learn_from_voice()` |

**D 为什么是"关掉"而不是"重跑"**：C 已经把 15 条风格记忆的内容人工合并进 `persona.py`。
若按 D 原意重跑蒸馏，结果会**写回 `llm_memory`**，立刻与 `persona.py` 形成两个真源 ——
那正是这次要解决的问题。所以正确做法是把它降级为「需要重新蒸馏时才临时打开的工具」，
日常保持关闭。

### 11.3 数据库迁移

删除了 **23 条**已被 `persona.py` 接管的记忆：

- 身份重复：`1`（创造者/原型）、`2`（称呼规则）
- 规则类：`4`、`5`、`6`、`11`、`31`、`37`
- 说话风格/性格：`13`–`27`（共 15 条）

**49 → 26 条**（锁定 10 / 可整理 16）。

留档位置：
- 整库备份：`_backup/db_before_persona_refactor.sqlite3`
- 被删条目原文：`_backup/persona_migrated_memories.txt`

### 11.4 效果

| | 重构前 | 重构后 |
|---|---|---|
| PERSONA_PROMPT | 1810 字符 | 2951 字符 |
| 长期记忆注入 | 40 条（含 32 条锁定，9 条事实永远进不来） | 26 条（全部装入，无遗漏） |
| 记忆块字符数 | ~2000 | 1313 |
| 系统提示总量 | ~4000 | 4480（基本持平） |

信息量没减少，但**去掉了重复、消除了矛盾、近期事实不再被挤掉**。

### 11.5 后续可以做的

1. **事实类记忆还有重复**：迁移后仍有明显重叠，例如
   `#8`（名字来源于 Key）与 `#12`、`#6`/`#7`/`#10`/`#20`（同一次「活下去/守护老师」的誓言）、
   `#1` 与 `#11`（爱丽丝往事）。自动整理只在记忆数 > 50 时触发，现在是 26 条，**永远不会触发**。
   要清理得手工合并，或调低 `TIDY_THRESHOLD`。
2. `_learn_voice()` / `_learn_from_voice()` 现在是死代码（默认不调用），
   保留是为了将来重新蒸馏人设；如果确定不要，可以整段删除。
3. 重做 LLM/agent 时，`persona.py` 这种「人设即代码」的形态应该保留 ——
   它能 diff、能 review、能跟着版本回滚，比散在数据库里强得多。

---

## 12. 回复分段 + 图片识别（v2.1.9）

### 12.1 三个问题

用户反馈「连问几句她只答一次，有时干脆无视」，查下来是三个独立原因：

1. **图片消息被静默丢弃**（最严重）
   ```python
   if has_media(event):
       return      # 带图片/视频的消息直接 return，连记忆都不进
   ```
   `@Kei` 和自由聊天两条路径都有这一句。用户发图 = 什么都不会发生。

2. **冷却丢弃回复**
   `_flush_buffer` 里冷却未到时 `return`，消息虽写进短期记忆但**永远不回复**。

3. **没有任何多段回复机制**
   `@Kei` 路径用 `finish()` 一次发完，回复再长也是一条。

### 12.2 修复

| 问题 | 修复 |
|---|---|
| 图片丢弃 | 删掉 `has_media` 门禁，改为 `extract_images()` 取图；只在「既无文字又无图」时跳过 |
| 冷却丢弃 | `memory.cooldown_remaining()` 返回剩余秒数，调用方 `await asyncio.sleep()` **等它过去再答**，不再丢弃 |
| 单条回复 | `_split_reply()` 按 `[SEP]` 拆分，`MAX_REPLY_SEGMENTS=3` 限段，段间 `REPLY_SEGMENT_DELAY=0.6s` 依次发 |

### 12.3 图片识别的实测结论（重要）

用一张「红底白字 KEI 42」的图逐项验证：

| 结论 | 证据 |
|---|---|
| `deepseek-v4-flash` **支持视觉** | 正确读出「KEI 42」和红色背景 |
| **可直接传外部 URL**，不需要下载+base64 | 传 B站封面 URL，模型准确描述「粉色长发戴发饰的少女端着狙击步枪」 |
| ⚠️ **必须关掉 thinking** | 开着时 200 个 `max_tokens` 全被 `reasoning_tokens` 吃光，`content` 返回空串、`finish_reason=length` |

`client.py` 默认就发 `thinking: {"type": "disabled"}`，所以**不要改这个默认值** ——
一改图片识别就会静默失效（返回空回复）。

### 12.4 实现要点

- `MAX_IMAGES_PER_MESSAGE = 3`（图片每张约 250+ prompt token）
- `build_context(images=...)`：**只把当前消息**发成多模态
  `[{"type":"text",...}, {"type":"image_url",...}]`；历史消息的图片不重发
  —— QQ 图片 URL 有有效期，重复计费也没意义
- 自由聊天的缓冲区条目加了 `images` 字段，一批消息里最多合并 3 张图
- `persona.py` 补两条：可用 `[SEP]` 拆多条（但默认只发一条）；能看到图片内容

### 12.5 端到端验证

```
deepseek-v4-flash  prompt=3045 completion=27
content: '粉色长发戴蓝发饰的少女正侧身端着狙击枪瞄准呢……等等，这画风怎么这么眼熟。'
```

4096 的上下文里含 1 张图，模型正确识图且回复已经是 Kei 的语气。

### 12.6 还没做的

- **多图理解**：一次塞 3 张图模型容易只看第一张，没有做特殊处理
- **视频**：完全没处理（`extract_text` 里也没有 video 分支，纯视频消息会被跳过）
- **图片过期**：QQ 图片 URL 有时效，如果排队很久（比如冷却等待）可能失效 —— 目前没做重取

---

## 13. Pixiv 画师作品推送（v2.2.0）

### 13.1 需求

`add pixiv <画师UID>` 订阅画师，新作品推送到群；**Pixiv 标记为 R18 的跳过**，其余推送。

### 13.2 上线前的实测（每条都真机验证过）

| 问题 | 结论 |
|---|---|
| 免登录能用吗 | ✅ 能。`ajax/user/{uid}/profile/top` 直接返回作品列表 |
| R18 标记在哪 | ✅ 每条自带 `xRestrict`（0=全年龄 1=R18 2=R18G），**不用额外查详情** |
| 能用 `sl` 判 R18 吗 | ❌ **不能**。全年龄作品上 `sl` 见过 2 也见过 6，那是别的分级维度 |
| 图片能直接传 URL 吗 | ❌ **不行**。`i.pximg.net` 无 Referer 返回 **403**，QQ 拉图不带 Referer ⇒ 必裂 |
| 国内能直连吗 | ❌ **不能**。真直连全部 ConnectTimeout，必须走代理 |
| `tags` 结构 | ⚠️ `profile/top` 是**字符串列表**，`illust/{id}` 是 `[{"tag":...}]` 对象列表，两种都要兼容 |
| base64 发图支持吗 | ✅ SnowLuma 的 `inlineBase64Payload()` 认 `base64://`，实测 2MB 请求体也能发 |

**关于"直连"的一次误判**：最初我用 `httpx.AsyncClient(proxy=None)` 测出"直连可用"，
但 `trust_env` 默认 `True` → httpx 调 `urllib.getproxies()` → **Windows 上它读注册表的系统代理**，
所以那次其实一直在偷偷走 Clash。加 `trust_env=False` 后才暴露真实情况。

### 13.3 关键设计

| 项 | 决定 |
|---|---|
| 平台 key | `pixiv_illust`（带下划线，因为 `remove_target()` 用 `rpartition("_")` 拆平台名） |
| 用户输入 | `pixiv` / `px` 由 `PLATFORM_ALIASES` 映射到 `pixiv_illust` |
| 轮询 | `profile/top` 一次请求，`pixiv_poll_interval` 默认 **300 秒**（B站是 30 秒，分开） |
| R18 过滤 | `xRestrict != 0` 跳过；`isMasked` 跳过；**不过滤 AI**（用户明确要求只看 R18） |
| 图片数量 | 最多 `pixiv_max_images = 9` 张；超过 9 页时末尾加一行 `（全部图片请查看链接）` |
| 图片处理 | 下载（带 Referer）→ 等比缩到长边 ≤1200 → JPEG q85 → base64，**全程内存不落盘** |
| 首次订阅 | 静默同步：全部标记已推送，一条不发 |
| 重启补推 | 用数据库判断（不是启动时间戳），最多补 3 条，多出来的静默标记 |
| 补推形态 | 全部塞进**同一个合并转发**，不会刷屏 |
| 代理 | 不显式配，依赖 httpx 读系统代理 |

### 13.4 卡片格式

折叠状态可见两行：

```
{画师名}投稿了P站        ← source
{作品标题}                ← news
查看 1 条转发消息          ← summary
```

点开后：

```
{标题}
[图片 1]
...
[图片 N]                ← 最多 9 张
（全部图片请查看链接）      ← 仅当页数 > 9
                        ← 空行
链接：https://www.pixiv.net/artworks/{pid}
```

空行规则沿用之前摸出来的：紧跟**图片**的文本用 `\n`，紧跟**文字**的用 `\n\n`。

### 13.5 实现文件

| 文件 | 内容 |
|---|---|
| `sources/pixiv_illust.py` | **新建**。`PixivIllust` 数据源 + `fetch_page_urls()` + `_parse_tags()` |
| `media.py` | **新建**。`download_as_base64()` / `compress_to_jpeg()` |
| `formatter.py` | 新增 `build_pixiv_preview()` / `build_pixiv_forward_msg()` / `send_pixiv_forward()` |
| `scheduler.py` | 注册工厂、`poll_interval_for()`、`_sync_pixiv_new_items()`、推送分派 |
| `admin/commands.py` | `PLATFORM_ALIASES` / `PLATFORM_SHORT_NAMES`、add 校验、help |
| `cli.py` | 平台别名 + **修了一个既有 bug**（见 13.7） |
| `config.py` | `pixiv_poll_interval` / `pixiv_max_images` / `pixiv_max_image_side` |
| `tests/test_pixiv_illust.py` | **新建**，34 个用例 |

### 13.6 上线前用手动脚本验过的样本

| pid | 页数 | 结果 |
|---|---|---|
| 139242565 | 6 | 6 图全推，无提示行，1.5 MB → 请求体 2.01 MB，9.5 秒 |
| 149405647 | 1 | 单图分支，无提示行，462 KB → 101 KB |
| 147543315 | 6 | 6 图，4585 KB → 939 KB，请求体 1.22 MB，9.5 秒 |
| 148404748 | 2 | 卡片样式确认 |

实测压缩率稳定在 **75%～80%**，画质在 QQ 里看没有明显损失。

### 13.7 顺手修的既有 bug

`bot/cli.py` 一直是**跑不起来**的：它 `from plugins.monitor.database import ...`，
而 `plugins/monitor/__init__.py` 顶层就调 `get_driver()`，没有 `nonebot.init()` 直接 RuntimeError。
文档里写着的 `python cli.py add/list/remove` 从来没成功过。已加 `nonebot.init()` 修掉。

### 13.8 还没做的

- **多图理解**：9 张图一次发，客户端加载压力大，但没做分批
- **ugoira 动图**（`illustType=2`）：只能拿到静态帧，没特殊处理
- **登录态**：匿名够用；若出现漏推（画师更了但没推），再填 `PHPSESSID`
- **反爬兜底**：现在只靠 UA + Referer + 300 秒间隔，没做 403 退避和重试
- **标签**：已解析进 `item.extra["tags"]`，但没显示在卡片里

### 13.9 真机验证（2026-10-21 凌晨）

订阅了两个画师（`38297201` にんげんまめ、`99219552` ミリペパー），群 `1031343674`。

**首次静默同步**：01:39:06 完成，写入 33 条（uid 38297201）、01:39:53 写入 25 条（uid 99219552），
**全程没发任何消息**。

**强制触发一次真实推送**：为保证 `has_pushed_items()` 仍为 True（否则会被当成首次订阅再次静默），
只删每个目标**一条**记录，保留其余：

```
pixiv_illust/1031343674/38297201/139242565    （6 页）
pixiv_illust/1031343674/99219552/150503704    （ツムギ）
```

结果：

```
01:44:10  记录回写 139242565
01:44:34  send_group_forward_msg  source="にんげんまめ￤1日目東ア-31投稿了P站"
01:44:49  记录回写 150503704
01:44:56  send_group_forward_msg  source="ミリペパー投稿了P站"
```

计数回到 33 / 25，卡片参数（source / news / summary）全部带全。
**这是第一次真实走完「检测到新作 → 下载图 → 压缩 → base64 → 合并转发」的完整闭环。**

### 13.10 新目标立即首轮（v2.2.1）

上面验证暴露出一个体验问题：`add` 之后要**等满一个轮询周期**才第一次抓取。
B站 30 秒不明显，Pixiv 要等 5 分钟，很容易让人以为没生效（实测 01:34 加、01:39 才抓）。

修复：`reload_targets()` 给**新增**任务设 `next_run_time = now + 10s`
（`NEW_TARGET_FIRST_POLL_DELAY = 10`）。`start()` 不加这个 —— 启动时保持按各自间隔散开，
避免开机瞬间十几个目标同时轰出去被限流。

---

## 14. 大规模订阅：列表分页 + 限流（v2.3.0）

### 14.1 为什么现在才做

用户计划把 Pixiv 订阅扩到**数十乃至上百**。原来 `list` 一次输出全部目标，
100 个是一条超长消息，而且 `remove <序号>` 依赖这个列表 —— 翻 100 条去找编号，体验是崩的。

先做了一次数据审计，纠正了两个误判：

| 我一开始以为 | 实际 |
|---|---|
| 「36 个目标里 31 个非 Pixiv」 | 36 是**含软删除**的总行数，`enabled=1` 只有 **15** 个（非 Pixiv 12 + Pixiv 3） |
| 「21 条 `enabled=0` 是历史垃圾」 | **是软删除，有意为之**。`remove_target()` 设 `enabled=0`，`add_target` 的 `ON CONFLICT ... DO UPDATE SET enabled=1` 复用它 |

所以那 21 行**没动**。`CLAUDE.md` 里补了一条说明，免得以后又有人当垃圾清掉。

### 14.2 列表：命令拆开 + 分页

```
list                    → 只显示 B站 + 斗鱼
pixivlist [页码]         → Pixiv 订阅，每页 20，默认第 1 页
remove <序号>            → 序号来自 list（不含 Pixiv）
remove pixiv <UID>       → 按 UID 删除（也可用名字模糊匹配）
```

**排序选添加顺序（`id` 升序）而不是 uid**，理由：

1. **分页稳定** —— 新增订阅追加到末尾，前 N-1 页永远不变。
   按 uid 排的话，新订阅插到中间，后面每一页边界全漂移。
2. `add` 的回复里已经带了名字和 uid，用户不需要靠列表确认新订阅，"最新在前"的优势用不上。
3. 和现有 `list` 一致。

**序号只用于阅读**，删除一律用 UID —— 这样编号漂移不会导致删错。

**用户明确不要 `pixivfind` 搜索**，所以最终只有 `pixivlist [页码]` 一条命令。
页码参数只接受纯数字，不做关键词匹配，避免"数字既可能是页码又可能是 uid"的歧义。

**一个必须同步改的地方**：`list` 和 `remove <序号>` 都必须排除 `pixiv_illust`，
否则序号错位会删错目标。两处代码里都写了注释提醒。

### 14.3 限流：四道防线

100 个订阅 × 每 300 秒轮询 = 约 0.33 次/秒持续请求，而且**重启瞬间**所有任务原本会在
同一时刻（开机 + interval）一起触发 —— 100 个请求挤在几秒内，几乎肯定被 403/429。

| 防线 | 实现 |
|---|---|
| 间隔自适应 | `pixiv_interval_for(n) = max(pixiv_poll_interval, n × 6)`。20 个以内 300s，**100 个自动 600s**，总速率恒定 |
| 启动错峰 | `start()` 首轮 `next_run_time` 在 `[10s, interval]` 内随机 |
| 抖动放大 | `jitter_for()`：Pixiv 60s，其他平台 5s |
| 失败退避 | 403/429 → 300s 起指数退避（封顶 3600s）；网络错误 → 60s 起。日志明确写「限流」+ 退避时长 |

**退避是这次最实质的修复**。之前 `fetch()` 遇到非 200 只打一条 warning 就 `return []`，
调度器看到空列表就当"没更新"，于是**被限流和没新作在日志上完全一样**，
排查时会一直往错误方向找。现在有 `_fail_streak` / `_backoff_until` 状态，
`is_backing_off` 可直接查询，恢复时也会打一条「恢复正常（此前连续失败 N 次）」。

### 14.4 改动文件

| 文件 | 内容 |
|---|---|
| `admin/commands.py` | `list` 过滤、`pixivlist` 新指令、`paginate_pixiv()` 纯函数、`remove pixiv` 分支 |
| `scheduler.py` | `pixiv_interval_for()` / `jitter_for()` / 启动错峰 |
| `sources/pixiv_illust.py` | 退避状态机 `_register_failure()` / `_reset_backoff()` |
| `tests/test_pixiv_illust.py` | 新增分页、间隔自适应、退避共 15 个用例 |

### 14.5 还没做的

- `pixivlist` 只有分页，没有排序选项（比如按更新频率排）
- 100 个订阅时每轮 100 个请求，即使有自适应间隔，**仍在同一批响应里集中下载图片** ——
  如果同时有多个画师更新，图片下载会串行排队，推送可能延迟几十秒
- 没有"批量导入订阅"的命令，100 个只能一个个 `add`

---

## 15. 一次 21.5 小时的掉线排查（v2.3.1）

### 15.1 现象

2026-10-23 白天 Kei 完全失联，直到 22:26 才被手动重启。

### 15.2 定位过程（按证据强度排序）

**① 先找日志空档** —— 比逐行读日志有效得多：

```python
# 解析每行开头的时间戳，算相邻间隔
gaps = [(t2-t1) for t1, t2 in zip(rows, rows[1:]) if (t2-t1) >= 120s]
```

结果：`00:53:16 → 22:26:27`，**1293 分钟（21 小时 33 分）**。

**② 逐项排除**

| 猜测 | 结论 | 依据 |
|---|---|---|
| 系统关机/重启 | ❌ | `System` 日志 6013：系统自 10-22 12:48 起连续运行 83492 秒 |
| 休眠 | ❌ | 无 Kernel-Power 41/42/107 |
| 程序崩溃 | ❌ | `Application` 日志无 node.exe / python.exe 的 1000/1001 事件 |
| 被腾讯踢下线 | ❌ | 全文搜 `kick/下线/relogin/session invalid` 零命中；日志末尾是普通 `OK` 消息 |

**③ 关键推论**：如果只是被踢下线，node 进程应该还活着并尝试重连。
但进程整个消失 + 21.5 小时零推送（11 个 B站/斗鱼目标每 30 秒轮询，不可能毫无产出）
⇒ **整个服务被终止，不是连接问题**。

**④ 找唯一可能的终止路径**

全项目搜 `taskkill|os.kill|TerminateProcess`，只有 `tray.py` 两处：

```python
def _on_shutdown(self, icon, item):
    _kill_snowluma()                                      # 按端口 5099/5100/5101/8080 查杀
    (config.DATA_DIR / ".pid").unlink(missing_ok=True)
    os._exit(0)                                           # ← atexit 都不走，零日志
```

**⑤ 日志还原时间线**

```
00:51:1x   老实例被杀（同一秒有 4 次 Windows RestartManager 会话事件）
00:51:37   新实例启动（start.bat）
00:51:39   启动问候发到两个群
00:52:46   用户拍一拍，Kei 回拍并回复
00:52:52   用户发 @Kei pixivlist → Kei 回复「Pixiv 订阅 1–20 / 共 77」
00:53:07   用户："我2000多个关注"
00:53:16   用户："就筛出来70个订阅"
           ↓ 进程消失，零日志
22:26:27   SnowLuma starting（手动重启）
```

**结论**：`00:52` 时服务完全健康（v2.3.0 的 `pixivlist` 正常返回 77 个订阅），
用户说完话不到 1 分钟服务就没了 —— 与「误点托盘『关闭机器人』」完全吻合。

### 15.3 根因不是崩溃，而是「没有留下任何证据」

`os._exit(0)` 会跳过 atexit、跳过日志缓冲区刷新。加上 Windows 默认不开进程退出审计（4689），
事后无从判断是崩溃、被杀、还是点了菜单。**这才是真正需要修的东西。**

### 15.4 修复

`tray.py` 新增 `_log_shutdown()`，在**启动**和**托盘关闭**两处各写一行到
`data/logs/shutdown.log`：

```
2026-10-23 22:26:27  进程启动（托盘已就绪）        self_pid=32156  killed=[]
2026-10-24 01:20:03  托盘菜单「关闭机器人」        self_pid=32156  killed=[20780, 7972]
```

`_kill_snowluma()` 改为返回被终止的 PID 列表，记进 `killed`。

**排查口诀**（已写进 CLAUDE.md）：

| 日志表现 | 含义 |
|---|---|
| 有启动行 + 有退出行 | 正常从托盘关闭 |
| 有启动行 + **无**退出行 | 被外部强杀（taskkill / 任务管理器 / 会话注销） |
| 两行都没有 | 进程没起来，或不是这个入口启动的 |

### 15.5 顺带发现的隐患（未处理）

- **QQ.exe 当天崩了两次**（09:31、13:29，`ntdll.dll`/`QQNT.dll` 访问冲突 `0xc0000005`）。
  SnowLuma 靠 hook QQ 客户端工作，**QQ 崩溃会导致 Kei 失联**，而目前没有自动恢复机制。
- 托盘菜单只有一个「关闭机器人」，文案容易被理解成"关掉托盘/关掉推送"，**误点成本过高**。
  后续可考虑加二级确认（`关闭机器人 ▸ 确认关闭`）。

---

## 16. 记忆去重 + 图片并发下载（v2.3.2）

### 16.1 为什么突然又去重

v2.1.8 清理过一次（49 → 26 条），但**自动提取一直在加新记忆**，
到 v2.3.1 时又涨回 **35 条**，而且新的重复更离谱 —— 「骑在爱丽丝头上」这件事被记了 **3 遍**。

根因不是提取逻辑坏了，而是：

1. `remember.py` 确实会在写入前查 `get_mem_cache()`，但它查的是**整条完全相同**；
   同一事件换个说法（"Kei 很喜欢老师" vs "Kei 有点傲娇但喜欢老师"）就查不出来。
2. `TIDY_THRESHOLD = 50` —— 只有记忆数 **> 50** 才触发自动整理。
   35 条永远够不到，等于自动整理形同虚设。

### 16.2 本次处理（一次性手术）

35 → **27 条**：删 8、合并改写 4。

| 重复簇 | 处理 |
|---|---|
| `#8` 名字来源 Key + `#12` "打开未来的钥匙" | 合并成一条（锁定 1.0） |
| `#10` "天童ケイ承诺…" | 删除 —— `#6`（重启台词）与 `#7`（深夜誓言）已完整覆盖 |
| `#19` "Penny时墨承诺 level up" | 删除 —— `#7` 已含 |
| `#20` "你的诞生对我意义重大" | 删除 —— `#7` 已含 |
| `#1` 爱丽丝往事 + `#11`（更详细版） | 合并成一条 |
| `#25` 括号心情 + `#26` 恼羞成怒称系统bug | 合并成一条 |
| `#51` + `#52` + `#57` 骑爱丽丝头的图（三条互相矛盾） | 合并成一条中性的 |

**安全措施**：脚本对每一行都做了「内容必须包含预期关键字」的断言，不匹配就整体中止不提交。
第一次跑就是因为关键字写错（`#26` 里没有"括号"二字）直接中止，**库没被改动** —— 这个守卫值得保留。

留档：
- 整库：`_backup/db_before_memory_dedup_<时间戳>.sqlite3`
- 被删原文：`_backup/memory_dedup_removed_<时间戳>.txt`

### 16.3 图片并发下载：从 1.3x 到 3.1x

第一版只把 `for` 循环改成 `asyncio.gather` + 信号量，实测 6 张图 **只快 1.3 倍**（12.5s → 10.0s）。

两个真正的瓶颈：

| 瓶颈 | 现象 | 修法 |
|---|---|---|
| **每张图 new 一个 `AsyncClient`** | 每张都要重做 TLS 握手 + 建连，白花几百毫秒 | 整个批量共用一个 client |
| **Pillow 压缩在事件循环里** | `thumbnail` + JPEG 编码是同步 CPU 活，卡住整个 loop，并发等于没用 | `await asyncio.to_thread(compress_to_jpeg, ...)` |

改完以后 **10.2s → 3.3s（3.1x）**。

新增 `media.download_many_as_base64(urls, headers, *, max_side, quality, concurrency=4)`：
- 返回**与入参等长且同序**的列表，失败项是 `None`
- **不能过滤掉 None**，否则后续图片会整体错位（这条已在 docstring 里写明）
- `download_as_base64()` 保留为单张包装，内部转调批量函数

### 16.4 待办没有变化

上一轮列出的其余项（视频消息、llm_chat/agent 补测试、ugoira、标签进卡片、
pixivlist 排序、图片过期重取）**都按用户决定暂不处理**，见第 4 / 11.5 / 12.6 / 13.8 / 14.5 / 15.5 节。

---

## 17. 前缀缓存优化（v2.4.0）

### 17.1 动机

用户提了一个尖锐的问题：**「这套东西和我在网页版手动带一堆前缀发对话有什么本质区别？」**

答案是：**没有本质区别，而且网页版内部也是这么干的** —— chat completions 是无状态 API，
"AI 记得你"从来是客户端在重发。这是范式，不是实现缺陷。

但范式内仍有优化空间，而且方向很明确：**让重发的东西进缓存**。

### 17.2 原来的顺序为什么是错的

DeepSeek 按**前缀**自动做上下文缓存（响应里有 `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens`）。
原顺序：

```
[0] persona      固定
[1] 群号 + 时间   每轮都变   ← 前缀在这里断开
[2] 长期记忆      固定 ← 本该可缓存，但因为位置靠后，永远 miss
[3+] 对话
```

也就是说：**1500 字符的长期记忆块每轮都在被重新计算**，纯浪费。

### 17.3 改动

| 文件 | 改动 |
|---|---|
| `memory.py` | `build_context()` 把 system 消息重排为 persona → 长期记忆 → 群号+时间 → 对话；并在 docstring 写明「固定内容必须在前」的约定 |
| `database.py` | `llm_token_usage` 新增 `cache_hit_tokens` / `cache_miss_tokens`；`_ensure_token_usage_cache_columns()` 用 `PRAGMA table_info` 做幂等 `ALTER TABLE` 迁移旧库 |
| `client.py` | 解析并记录两个缓存字段，日志行增加 `cache=hit/miss` |
| `__init__.py` | `@Kei LLM` 增加一行 `缓存命中 X / Y（Z%）`；分母用「命中+未命中」，避免迁移前的 0/0 稀释命中率 |

### 17.4 实测结果

同一上下文连发两次：

| | prompt | cache_hit | cache_miss | 命中率 |
|---|---|---|---|---|
| 第 1 次 | 2873 | 1664 | 1209 | 58% |
| 第 2 次 | 2873 | 2688 | 185 | **94%** |

**第一次 58% 恰好对应"只有 persona 被缓存"** —— 因为历史流量已经把 persona 前缀缓存住了，
但旧顺序让记忆块永远进不去。重排后第二次冲到 94%，证明长期记忆块也进了缓存前缀。

### 17.5 顺带确认的一件事

`prompt_cache_hit_tokens` 在**第一次**调用就有 1664，说明线上的真实对话早就在积累 persona 缓存。
也就是说这项优化**不需要等缓存预热**，重启后立刻受益。

### 17.6 还没做的

- **成本公式仍是全价**：`@Kei LLM` 用 `config.llm_price_prompt` 乘全部 prompt token，
  命中部分实际便宜得多，所以现在**显示的成本是高估的**。
  要算准需要再加一个 `llm_price_cache_hit` 配置项（按 DeepSeek 官网的缓存命中单价填）。
- 缓存只覆盖固定前缀，**对话部分永远是 miss**。想进一步降本只能走 B（按需检索记忆）。

---

## 18. 自然分段说话（v2.5.0）

### 18.1 需求

用户希望 Kei **像普通人打字聊天**：一次回复拆成 2~3 条发出来，
而不是一口气丢一整段。但明确要求：

- **不强制**，让 Kei 自己判断分不分
- `@Kei` 和自由聊天**两条通道都启用**
- **封顶 3 条**，避免拆得太碎

### 18.2 改之前的状态

基础设施其实早在 v2.1.9 就有了：`_split_reply()` 按 `[SEP]` 拆、`MAX_REPLY_SEGMENTS = 3`、
固定 0.6 秒间隔。但基本不生效，原因是**提示词在压制**：

> 如果确实想把回复拆成两三条发出去……**绝大多数时候只用一条**，不要为了显得活泼而刻意拆

模型很听话，于是从不拆。

### 18.3 改动

| 位置 | 改动 |
|---|---|
| `persona.py` | 删掉压制性措辞，换成「要不要分段发」一节：给出**值得拆 / 不值得拆**的判断标准，保留「不确定就只发一条」 |
| `persona.py` | 新增一组示例对话（加班到深夜），演示 `[SEP]` 的用法 —— **few-shot 比纯规则有效得多** |
| `__init__.py` | 自由聊天的指令从「想拆就拆」改成「可以自己判断要不要分成两三条」 |
| `__init__.py` | 间隔从固定 0.6s 改成 `_segment_delay()` = `random.uniform(0.5, 1.8)` |

### 18.4 实测发现的坑：模型不全用 `[SEP]`

第一次探针（5 条提问）：**3/5 用了 `[SEP]`，2/5 用的是空行**。

空行那两条没被拆开，结果变成「一条消息里夹一个大空隙」，看起来像排版错误。
第二次探针又发现一条用的是**单个换行**。

所以最终 `_split_reply()` 认三种分隔：

| 分隔符 | 规则 |
|---|---|
| `[SEP]` | 显式标记，直接拆 |
| **空行** | 模型最自然的断句方式，直接拆（多了靠封顶截住） |
| **单换行** | **仅 2~3 行时拆**；超过 3 行不拆 —— 那是在做分行排版，强拆会把封顶外的内容丢掉 |

段内残留的连续空行会压成单个换行（只在用了 `[SEP]` 的场景出现）。

### 18.5 效果

第二轮探针（5 条不同类型提问）：**2/5 自然分段**，其余一条说完。
分段的那些读起来确实更像真人：先一句短反应，再补一句解释。

### 18.6 没做的

- 没有做「打字中」状态（QQ 没有可靠的 typing 指示 API）
- 没有按段长度动态调整间隔（现在是统一随机区间）
- 主动说话（下一节再议）

---

## 19. 费用估算改成真实价（v2.5.1）

### 19.1 原来错在哪

`@Kei LLM` 的成本是 `prompt_tokens × 2.02 + completion_tokens × 3.02`（元/百万）。
三个问题叠在一起：

1. **2.02 / 3.02 是旧 DeepSeek V3 的价格**，当前 deepseek-flash 的输出价是 $1.20 ≈ 8.5 元
2. **完全没考虑缓存命中** —— 命中价只有未命中的 1/50
3. **没考虑峰谷价差** —— 高峰是低谷的 2 倍

实测一次请求（prompt 2873，其中命中 2688）：

```
旧算法：0.0058 元
真实：  0.0006 元      → 高估约 10 倍
```

### 19.2 官方价（2026-10 抓取，deepseek-flash，美元/百万 tokens）

| 项目 | 低谷 | 高峰 |
|---|---|---|
| 输入（缓存命中） | $0.003 | $0.006 |
| 输入（缓存未命中） | $0.15 | $0.30 |
| 输出 | $0.60 | $1.20 |

高峰 = UTC 周一至周五 `01:00-04:00`、`06:00-10:00`（北京时间 09-12、14-18）。
其余时间含周末为低谷，恰为高峰一半。

### 19.3 实现要点

**必须逐条计价**。`@Kei LLM` 显示的是「今日/昨日/累计」聚合值，
而峰谷价差 2 倍，拿聚合 token 数乘单一单价根本区分不了一个月里哪些请求在高峰。

新增 `llm_chat/pricing.py`：

| 函数 | 作用 |
|---|---|
| `is_peak(utc_dt)` | 判断某个 UTC 时刻是否高峰（窗口左闭右开、周末全低谷） |
| `row_cost(...)` | 单条记录计费；**旧数据没有缓存字段时兜底成全按未命中价** |
| `total_cost(rows)` | 把多条加起来，缺字段按 0 处理 |
| `format_cost(x)` | 小额多留几位小数，否则 ¥0.0006 显示成 ¥0.00 看不出差别 |

配套改动：
- `database.py::get_usage_rows(period)` 返回原始行（含 `created_at`），
  因为峰谷判断需要每条的时间；`created_at` 由 SQLite `CURRENT_TIMESTAMP` 写入，是 **UTC**
- `config.py` 新增 `llm_price_cache_hit`（0.043）和 `llm_price_offpeak_ratio`（0.5），
  `llm_price_prompt` 改 2.13（未命中高峰价）、`llm_price_completion` 改 8.52

### 19.4 实际影响

| 范围 | 旧算法 | 新算法 | 差异 |
|---|---|---|---|
| 今日 35 条 | ¥0.1630 | ¥0.112 | 1.5x |
| 累计 5853 条 | ¥17.23 | ¥13.72 | 1.3x |

**为什么整体只差 1.3~1.5 倍而不是 10 倍**：累计的 5853 条里绝大多数是
v2.4.0 之前写入的，**没有缓存字段**（两列都是 0），只能按未命中价算 ——
那些记录产生时的真实情况确实就是全价。差异集中在有缓存数据的新记录上。

重启到 v2.5.1 之后，所有新记录都会带缓存信息，显示成本会贴近真实值。

### 19.5 已知误差（无法消除）

- **中国法定节假日判断不了**：官方算低谷，我们会误按高峰 → 那几天高估 2 倍
- **汇率写死 7.1**：实际波动带来百分之几偏差
- 官方保留调价权，改价要手动覆盖 `.env`

测试：22 个新用例（峰谷边界、命中/未命中 50 倍、旧数据兜底、格式化）
