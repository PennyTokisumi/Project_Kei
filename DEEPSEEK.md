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

### 4.1 `voice.txt` 路径不一致（功能从未生效）
`llm_chat/__init__.py::_learn_from_voice()` 读的是 `data/voice.txt`，
但实际文件在 **`data/seen/voice.txt`**。所以那条"从官方语音台词自学习说话风格"的路径
**从来没有跑起来过**。要不要恢复、以及恢复后是否值得重跑一次，待定。

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
