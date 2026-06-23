"""应用配置 - 基于 Pydantic 从环境变量加载"""

from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings


class Config(BaseSettings):
    """全局配置，从环境变量 / .env 文件加载"""

    # NoneBot 驱动
    driver: str = Field(default="~fastapi+~websockets")
    onebot_ws_hosts: list[dict] = Field(default=[{"host": "127.0.0.1", "port": 8080}])

    # 监测
    poll_interval: int = Field(default=60, description="轮询间隔（秒）")
    db_path: str = Field(default="db.sqlite3")

    # 日志
    log_level: str = Field(default="INFO")

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


# 路径解析
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

config = Config()
DB_PATH = DATA_DIR / config.db_path
