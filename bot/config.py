"""应用配置 - 基于 Pydantic 从环境变量加载"""

import tomllib
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings


class Config(BaseSettings):
    """全局配置，从环境变量 / .env 文件加载"""

    # NoneBot 驱动（OneBot 实际连接地址由 nonebot-adapter-onebot
    # 直读 .env 的 ONEBOT_V11_WS_URLS，此处仅作说明）
    driver: str = Field(default="~fastapi+~websockets")

    # 监测
    poll_interval: int = Field(default=30, description="轮询间隔（秒）")
    db_path: str = Field(default="db.sqlite3")

    # Pixiv
    # 反爬比 B站严，且每次轮询要打一次画师主页，间隔不能太短
    pixiv_poll_interval: int = Field(default=300, description="Pixiv 轮询间隔（秒）")
    pixiv_max_images: int = Field(
        default=9, description="多页作品最多推送几张图，超出时提示看原链接"
    )
    pixiv_max_image_side: int = Field(
        default=1200, description="图片等比缩放的长边上限（像素）"
    )

    # B站
    bilibili_cookie: str = Field(
        default="",
        description="B站 Cookie 字符串，如 buvid3=xxx; SESSDATA=xxx; 提高 API 稳定性",
    )

# LLM
    llm_provider: str = Field(default="deepseek", description="LLM 供应商: deepseek / gemini")
    deepseek_api_key: str = Field(default="", description="DeepSeek API Key")
    # 用正式名 `deepseek-flash`（= DeepSeek-V4.1-Flash-1017）。
    # 旧名 `deepseek-v4-flash` 仍被接受、也会路由到同一个模型（服务端会把响应里的
    # model 改写成 deepseek-flash），但那是"兼容旧名"，不保证长期有效。
    # 注意别换成 `deepseek-v4-pro`：它不支持视觉，识图会直接失效，价格也是 4.4 倍。
    deepseek_model: str = Field(default="deepseek-flash", description="DeepSeek 模型名")
    gemini_api_key: str = Field(default="", description="Gemini API Key")
    gemini_model: str = Field(default="gemini-2.5-flash", description="Gemini 模型名")
    llm_proxy: str = Field(default="", description="LLM API 代理地址，如 http://127.0.0.1:7890")

    # 对话温度（默认 0.7）
    # ⚠️ 开启 thinking 后此值会被**忽略** —— 官方明说 thinking 模式不支持 temperature，
    # 传了也不报错、但没有任何效果。所以想靠温度调节"活泼程度"，就必须关掉 thinking。
    chat_temperature: float = Field(default=0.7, description="对话温度（thinking 模式下无效）")

    # 推理（thinking）开关与等级
    #
    # 关闭时（默认）：payload 发 `thinking: disabled`，温度生效，出话快、成本低。
    # 开启时：发 `reasoning_effort`，模型先输出思考再回答，更准但更慢更贵。
    #
    # 等级只认 low / high / max（服务端默认 high）。也接受 minimal/medium/xhigh/ultra，
    # 会按官方映射折算（minimal→low、medium→high、xhigh→high、ultra→max）。
    #
    # ⚠️ 开启 thinking 后**必须同时把 max_tokens 调大**：思考 token 计入输出额度，
    # 512 的额度很容易被思考吃光，导致 content 返回空串（v2.1.9 实测踩过）。
    # 聊天回复的输出预算（@Kei 与自由聊天共用）。
    # ⚠️ 开启 thinking 后，**思考 token 也算在这里面** —— 512 会被思考吃光、正文变空。
    # 实测：effort=low 思考约 130~540 字符，effort=max 可达 1400 字符（≈900 token）。
    # 因此开 thinking 时建议 2048 起步。
    llm_chat_max_tokens: int = Field(
        default=512, description="聊天回复的输出 token 上限（thinking 的思考量也占这份额度）")

    llm_enable_thinking: bool = Field(
        default=False, description="对话是否开启 thinking（关闭时温度才生效）")
    llm_thinking_effort: str = Field(
        default="low", description="thinking 等级: low / high / max")

    # LLM 计价（元 / 每百万 tokens），仅用于 @Kei LLM 指令的成本估算。
    #
    # 以下默认值按 DeepSeek 官方价（2026-10 抓取，deepseek-flash）折算，汇率按 7.1：
    #     输入(缓存未命中)  peak $0.30 → 2.13 元     off-peak $0.15 → 1.065 元
    #     输入(缓存命中)    peak $0.006 → 0.043 元   off-peak $0.003 → 0.0215 元
    #     输出              peak $1.20 → 8.52 元     off-peak $0.60 → 4.26 元
    #
    # **填的是高峰价**，低谷价由 llm_price_offpeak_ratio 折算（官方低谷恰为高峰一半）。
    # 涨价 / 换模型 / 汇率变动时在 .env 里覆盖即可。
    llm_price_prompt: float = Field(
        default=2.13, description="输入价格（缓存未命中，高峰价）元/百万 tokens")
    llm_price_cache_hit: float = Field(
        default=0.043, description="输入价格（缓存命中，高峰价）元/百万 tokens")
    llm_price_completion: float = Field(
        default=8.52, description="输出价格（高峰价）元/百万 tokens")
    llm_price_offpeak_ratio: float = Field(
        default=0.5, description="低谷价相对高峰价的倍率（DeepSeek 低谷为高峰的一半）")

    # ─── DeepSeek 余额播报 ────────────────────────────────
    #
    # 官方只有一个读接口 GET /user/balance，没有 webhook / 订阅 / 事件流，
    # 所以「充值」「赠送额度」**只能靠轮询发现**（消耗不用轮询：每次调用的 usage
    # 里就有精确 token 数，已经记在 llm_token_usage）。
    #
    # 不过该接口没有"每日次数"限制（官方只写了并发限制），所以等充值时
    # 可以把间隔压到 60 秒，几乎无额外代价。
    balance_alert_enabled: bool = Field(
        default=True, description="是否启用 DeepSeek 余额播报")
    balance_alert_group_id: int = Field(
        default=1031343674,
        description="播报发到哪个群（当前是测试群，测完改成正式群即可）")
    balance_poll_interval: int = Field(
        default=1800, description="正常轮询间隔（秒），默认 30 分钟")
    balance_wait_poll_interval: int = Field(
        default=60, description="余额低于阈值、等待充值时的加密轮询间隔（秒）")
    balance_low_threshold: float = Field(
        default=10.0, description="低余额阈值（元）。跌破提醒一次，回到阈值以上后重新武装")
    balance_kei_comment: bool = Field(
        default=True, description="播报后是否让 Kei 再跟一句（需该群已 KEI ON）")

    # 人设自动学习（默认关闭）。人设现已固定在 llm_chat/persona.py，
    # 若打开，_learn_voice / _learn_from_voice 会把 [说话风格] / [性格特点]
    # 这类条目重新写进 llm_memory，与 persona.py 形成两个真源。
    persona_autolearn: bool = Field(
        default=False,
        description="是否允许从台词池/语音文本自动提炼说话风格并写入长期记忆",
    )

    @property
    def llm_api_key(self) -> str:
        return self.gemini_api_key if self.llm_provider == "gemini" else self.deepseek_api_key

    @property
    def llm_model(self) -> str:
        return self.gemini_model if self.llm_provider == "gemini" else self.deepseek_model

    @property
    def llm_base_url(self) -> str:
        if self.llm_provider == "gemini":
            return "https://generativelanguage.googleapis.com/v1beta/openai"
        return "https://api.deepseek.com"

    @property
    def is_gemini(self) -> bool:
        return self.llm_provider == "gemini"

    model_config = {
        "env_file": str(Path(__file__).resolve().parent / ".env"),
        "env_file_encoding": "utf-8",
        "extra": "ignore",  # 忽略 NoneBot2 自身需要的 env 变量（如 PORT、HOST）
    }


# 路径解析
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

config = Config()


def _read_version() -> str:
    """启动时从 pyproject.toml 读取版本号"""
    toml_path = Path(__file__).resolve().parent / "pyproject.toml"
    try:
        with open(toml_path, "rb") as f:
            data = tomllib.load(f)
        return data.get("project", {}).get("version", "0.0.0")
    except Exception:
        return "0.0.0"


# 启动时锁定版本号，运行时永不变化
VERSION = _read_version()

# Sensei QQ 号（唯一管理员，全局引用）
SENSEI_QQ = "823262716"
