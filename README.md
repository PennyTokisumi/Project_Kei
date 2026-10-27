# Project Kei

一个跑在 QQ 上的 AI 少女「**Kei**」（天童ケイ / 《蔚蓝档案》）的同人实现。

她做两件事：**替你盯着你在意的人**（B站动态、直播、斗鱼、Pixiv 画师作品），
和**用角色口吻跟你聊天**（能看图、会分段说话、有长期记忆）。

> 这不是通用机器人框架，是给特定场景写的单体项目。人设、术语、指令都围绕「Kei」和「老师」展开。

---

## 功能

### 监控推送
| 平台 | 内容 | 说明 |
|---|---|---|
| B站 | 图文 / 视频投稿 / 专栏 / 转发动态 | 合并转发展示，四种类型的正文与图片都会抓 |
| B站 | 直播开播 | 优先发 Ark 卡片，失败自动回退普通图文 |
| 斗鱼 | 直播开播 | 官方 API 为主、镜像兜底；`rst != 0` 视为轮播不算开播 |
| Pixiv | 画师新作品 | 自动过滤 R18，图片本地压缩后以 base64 发送（防盗链） |

**大规模订阅不会被限流打穿**：轮询间隔随订阅数自适应（100 个订阅自动从 300s 变 600s）、
启动错峰、抖动放大、遇到 403/429 按指数退避（封顶 1 小时）。

### LLM 对话
- **角色扮演**：人设写在代码里（`persona.py`），可 diff、可回滚，不散落在数据库
- **能识图**：群里发的图片（最多 3 张）会以多模态格式发给模型
- **会分段**：像真人打字一样把回复拆成 2~3 条发出去，由模型自己判断要不要拆
- **三层记忆**：人设（代码）+ 长期事实（数据库，带重要性分级）+ 短期对话（按群隔离）
- **前缀缓存**：system 消息按「固定在前、易变在后」排序，实测缓存命中率 ~94%，输入成本大幅下降

### 群管理
订阅增删改查、分页列表、定时提醒、每日公告、token 用量与真实花费查询。

---

## 技术栈

| 组件 | 用途 |
|---|---|
| [SnowLuma](https://github.com/SnowLuma/SnowLuma) 2.0.3 | QQ 协议框架（OneBot v11），**不在本仓库内** |
| NoneBot2 | 机器人框架，通过 OneBot v11 连接 SnowLuma |
| APScheduler | 轮询调度 + 定时任务 |
| SQLite | 去重记录 / 直播状态 / 记忆 / token 用量 |
| httpx | B站、斗鱼、Pixiv、LLM 的 HTTP 客户端 |
| pydantic-settings | 配置管理（`.env` → `Config`） |
| pystray + Pillow | Windows 系统托盘 |
| DeepSeek / Gemini | LLM 供应商（可切换） |

---

## 环境要求

| 依赖 | 版本 | 说明 |
|---|---|---|
| **Windows** | 10/11 | 托盘图标、`start.bat`、进程管理都是 Windows 专用 |
| **Python** | ≥ 3.12 | |
| **Node.js** | **≥ 24** | SnowLuma 2.x 的硬性要求（低于 24 会直接拒绝启动） |
| **QQ 客户端** | NTQQ | SnowLuma 通过 hook 客户端工作，QQ 挂了机器人就失联 |
| **SnowLuma** | 2.0.3+ | 需自行下载，**本仓库不包含** |

---

## 快速开始

### 1. 获取 SnowLuma

本仓库的 `.gitignore` 排除了 `snowluma/`，你需要自己把它放进去：

```
Project_Kei/
└── snowluma/
    ├── launcher.bat      ← start.bat 会检查这个文件
    └── ...
```

> 没有 `launcher.bat` 时 `start.bat` 会提示「未找到 SnowLuma」。

### 2. 准备 Python 环境

```powershell
cd Project_Kei
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -U pip
.\.venv\Scripts\python -m pip install -r bot\requirements.txt
```

> `.venv` 必须放在**项目根目录** —— `start.bat` 用的是 `%ROOT%.venv\Scripts\python.exe`。
>
> 依赖里 `fastapi` / `uvicorn` / `websockets` 是必须的：NoneBot 2.4 之后不再提供
> `[fast]` 这类 extra，驱动依赖要显式安装，而本项目用的是 `~fastapi+~websockets` 驱动。

### 3. 配置 `bot/.env`

从模板复制一份再填：

```powershell
Copy-Item bot\.env.example bot\.env
notepad bot\.env
```

至少需要填 `DEEPSEEK_API_KEY`（或 `GEMINI_API_KEY`）才能用聊天功能；
只想要监控推送的话，不填也能跑。

### 4. 启动

```powershell
.\start.bat
```

脚本会：启动 SnowLuma（后台，若 5099 端口已在监听则跳过）→ 等你扫码登录 →
启动 NoneBot → 等 `data/.startup_ok` 出现 → 关闭窗口。

手动启动也可以（需要 SnowLuma 已经在跑）：

```powershell
cd bot
..\.venv\Scripts\python bot.py
```

### 5. 在群里启用

```
@Kei KEI ON
```

> ⚠️ **每次重启都要重新 `KEI ON`** —— 这是有意的安全设计（启动时会清空所有群的 LLM 开关）。

---

## 配置说明

所有配置项在 `bot/config.py`，都可以用 `bot/.env` 覆盖（变量名 = 字段名大写）。

### 基础

| 变量 | 默认值 | 说明 |
|---|---|---|
| `DRIVER` | `~fastapi+~websockets` | NoneBot 驱动 |
| `PORT` | `8081` | NoneBot 自身 HTTP 端口（不用于 OneBot） |
| `ONEBOT_V11_WS_URLS` | `["ws://127.0.0.1:8080/"]` | SnowLuma 的 OneBot WS 地址 |
| `DB_PATH` | `db.sqlite3` | 相对 `data/` 的数据库路径 |
| `POLL_INTERVAL` | `30` | B站/斗鱼轮询间隔（秒） |
| `BILIBILI_COOKIE` | 空 | B站 Cookie，能提高 API 稳定性（可选） |

### Pixiv

| 变量 | 默认值 | 说明 |
|---|---|---|
| `PIXIV_POLL_INTERVAL` | `300` | 基础轮询间隔，订阅多了会自动放大 |
| `PIXIV_MAX_IMAGES` | `9` | 多页作品最多推几张，超出的提示看原链接 |
| `PIXIV_MAX_IMAGE_SIDE` | `1200` | 图片缩放宽边上限 |

> Pixiv 在国内无法直连。代码不显式配代理，依赖 httpx 读取**系统代理** —— 代理软件关掉就推不了。

### LLM

| 变量 | 默认值 | 说明 |
|---|---|---|
| `LLM_PROVIDER` | `deepseek` | `deepseek` / `gemini` |
| `DEEPSEEK_API_KEY` | 空 | DeepSeek 密钥 |
| `DEEPSEEK_MODEL` | `deepseek-flash` | 模型名 |
| `GEMINI_API_KEY` | 空 | Gemini 密钥 |
| `GEMINI_MODEL` | `gemini-2.5-flash` | |
| `LLM_PROXY` | 空 | 仅 Gemini 走代理时需要 |
| `CHAT_TEMPERATURE` | `0.7` | 对话温度 |
| `LLM_ENABLE_THINKING` | `false` | 是否开启思考模式 |
| `LLM_THINKING_EFFORT` | `low` | `low` / `high` / `max` |

**`thinking` 与 `temperature` 互斥**：开启 thinking 后温度会被服务端忽略（官方不报错但不生效）。
角色扮演建议保持关闭 —— 温度才有效，而且更快更便宜。

> 想换模型前先确认它**支持视觉**：`deepseek-v4-pro` 不支持，换过去识图会直接失效。

### 计费

`@Kei LLM` 会用下面三个价格估算花费（元 / 百万 tokens，填**高峰价**）：

`LLM_PRICE_PROMPT`（缓存未命中）· `LLM_PRICE_CACHE_HIT`（缓存命中）· `LLM_PRICE_COMPLETION`（输出）

低谷价按 `LLM_PRICE_OFFPEAK_RATIO`（默认 0.5）折算。
估价是**近似值** —— 无法识别法定节假日，汇率也是写死的。想知道真实花费请以官方账单为准。

---

## 指令

全部需要 `@机器人`（`startswith` 区分大小写：`KEI ON` 有效，`kei on` 无效）。

### 所有人

| 指令 | 说明 |
|---|---|
| `status` | 版本 / 目标数 / 运行时长 |
| `add <平台> <ID>` | 加监测目标（`bilibili_dynamic` / `bilibili_live` / `douyu_live` / `pixiv`） |
| `list` | 本群监测目标（不含 Pixiv） |
| `pixivlist [页码]` | Pixiv 订阅列表，每页 20 个 |
| `remove <序号>` | 移除目标（需群主/管理员） |
| `remove pixiv <UID或名字>` | 取消 Pixiv 订阅（需群主/管理员） |
| `help` | 帮助 |
| `hello ON/OFF` | 开关启动问候 |
| `chat` | 随机台词 |
| `kawaii` | 禁言自己 60 秒 + 发 gif |
| `remind <时间> <内容>` | 一次性定时消息 |
| `daily <HHMM> <内容>` / `daily off` | 每群每日公告 |

### 仅 Sensei（`SENSEI_QQ` 指定的账号）

| 指令 | 说明 |
|---|---|
| `KEI ON/OFF` | 开关本群 LLM 聊天 |
| `LLM` | token 用量 + 缓存命中率 + 估算花费 |
| `memory` | 列出长期记忆 |
| `remember <重要性> <内容>` / `edit <序号> <内容>` / `imp <序号> <数字>` / `forget <序号>` | 记忆管理 |
| `read <文件>` | 读 `data/` 下文件并提取记忆 |
| `sensei` | 含隐藏指令的完整帮助 |
| `history [N]` | 拉群聊历史存 `data/chatlogs/` |
| `claude <任务>` | 经 Kei 转述问 Claude CLI |

---

## 项目结构

```
Project_Kei/
├── start.bat                  # 一键启动（SnowLuma → NoneBot）
├── bot/
│   ├── bot.py                 # 入口：双开检测 + 加载插件
│   ├── config.py              # 配置 + 版本号锁定
│   ├── tray.py                # 系统托盘（状态/自启动/关闭）
│   ├── cli.py                 # 命令行管理工具
│   ├── pyproject.toml         # 版本号唯一来源 + 依赖
│   └── plugins/
│       ├── monitor/           # 监控推送（含 sources/ 各平台解析器）
│       ├── admin/             # 群指令
│       ├── llm_chat/          # 对话 / 记忆 / 人设 / 计价
│       └── agent/             # Claude CLI 集成 + 定时任务
├── tests/                     # pytest（185 个用例）
├── data/                      # 运行时状态（gitignore）
└── snowluma/                  # 协议框架（gitignore，需自行下载）
```

---

## 设计要点

几条踩过坑之后定下来的规矩，改代码前值得先读：

- **人设即代码**：`llm_chat/persona.py` 是人设的唯一真源。能 diff、能 review、能跟版本回滚，
  比散在数据库里强得多。
- **记忆分三层计预算**：锁定条目（身份/生日/关系）最多 20 条 + 事实类条目最多 30 条，互不挤占。
  早期用统一的 `ORDER BY importance DESC LIMIT 40`，锁定条目一多就把近期事实全挤出去了。
- **system 消息固定内容必须排前面**：DeepSeek 按前缀做缓存，把「群号 + 当前时间」插在前面会让
  后面约 1500 字符的长期记忆块永远命中不了。
- **Pixiv 图片必须自己下载**：`i.pximg.net` 有防盗链，直接甩 URL 给 QQ 一定是裂图。
  多张图要用 `download_many_as_base64()`（共用连接池 + 压缩走线程池），比串行快 3 倍。
- **冷却不是丢弃**：用户连发几句时，早期版本第二句会被直接 `return` 掉，表现为「只答第一句」。
  现在是等冷却过去再答。

更细的工程细节（踩过的坑、为什么这么写）见 [`CLAUDE.md`](CLAUDE.md) 和 [`DEEPSEEK.md`](DEEPSEEK.md)。

---

## 已知限制

| 限制 | 说明 |
|---|---|
| **仅 Windows** | 托盘、`start.bat`、进程管理都依赖 Windows API |
| **托盘「关闭机器人」会停整套服务** | 会同时杀掉 SnowLuma，且没有二次确认 |
| **QQ 崩溃会失联** | SnowLuma 靠 hook QQ 客户端工作，没有自动恢复机制 |
| **重启需重新 `KEI ON`** | 有意的安全设计 |
| **Pixiv 依赖系统代理** | 国内直连不可用 |
| **成本是估算值** | 节假日、汇率无法精确判断 |

---

## 版本

当前版本 **v2.5.2**，版本号唯一来源是 `bot/pyproject.toml`。

```powershell
# 跑测试
.\.venv\Scripts\python -m pytest tests/ -q
```

> `config.py` 在模块导入时一次性读取版本号，所以 `@status` 显示的是**进程启动时**的版本，
> 改版本号必须重启才可见。

---

## 许可

本仓库**未附带开源许可证**，默认保留所有权利。

另外，仓库不包含 QQ 协议框架（SnowLuma）、任何账号凭据或运行时数据 ——
使用前请自行准备，并遵守相关平台的服务条款。
