"""应用配置 - 基于 Pydantic 从环境变量加载"""

import tomllib
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings


class Config(BaseSettings):
    """全局配置，从环境变量 / .env 文件加载"""

    # NoneBot 驱动
    driver: str = Field(default="~fastapi+~websockets")
    onebot_ws_hosts: list[dict] = Field(default=[{"host": "127.0.0.1", "port": 8080, "access_token": ""}])

    # 监测
    poll_interval: int = Field(default=30, description="轮询间隔（秒）")
    db_path: str = Field(default="db.sqlite3")

    # B站
    bilibili_cookie: str = Field(
        default="",
        description="B站 Cookie 字符串，如 buvid3=xxx; SESSDATA=xxx; 提高 API 稳定性",
    )

# LLM
    llm_provider: str = Field(default="deepseek", description="LLM 供应商: deepseek / gemini")
    deepseek_api_key: str = Field(default="", description="DeepSeek API Key")
    deepseek_model: str = Field(default="deepseek-v4-flash", description="DeepSeek 模型名")
    gemini_api_key: str = Field(default="", description="Gemini API Key")
    gemini_model: str = Field(default="gemini-2.5-flash", description="Gemini 模型名")
    llm_proxy: str = Field(default="", description="LLM API 代理地址，如 http://127.0.0.1:7890")

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
    def chat_temperature(self) -> float:
        """供应商默认温度：统一 0.7"""
        return 0.7

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
DB_PATH = DATA_DIR / config.db_path


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
