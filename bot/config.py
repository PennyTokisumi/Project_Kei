"""应用配置 - 基于 Pydantic 从环境变量加载"""

import tomllib
from pathlib import Path
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings


class Config(BaseSettings):
    """全局配置，从环境变量 / .env 文件加载"""

    # NoneBot 驱动
    driver: str = Field(default="~fastapi+~websockets")
    onebot_ws_hosts: list[dict] = Field(default=[{"host": "127.0.0.1", "port": 8080}])

    # 监测
    poll_interval: int = Field(default=30, description="轮询间隔（秒）")
    db_path: str = Field(default="db.sqlite3")

    # B站
    bilibili_cookie: str = Field(
        default="",
        description="B站 Cookie 字符串，如 buvid3=xxx; SESSDATA=xxx; 提高 API 稳定性",
    )

    # 日志
    log_level: str = Field(default="INFO")

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


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
