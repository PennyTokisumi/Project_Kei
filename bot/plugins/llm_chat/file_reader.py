"""LLM Chat 插件 — 本地文件读取"""

import re
from pathlib import Path

MAX_SIZE = 200 * 1024  # 200KB


def safe_read(path_str: str) -> str | None:
    """安全读取 data/ 下的文件"""
    p = Path(path_str)

    if ".." in path_str:
        return None

    from config import DATA_DIR
    # 只允许 data/ 目录
    target = DATA_DIR / p.name
    resolved = target.resolve()
    if not str(resolved).startswith(str(DATA_DIR.resolve())):
        return None
    if not resolved.exists() or not resolved.is_file():
        return None
    if resolved.stat().st_size > MAX_SIZE:
        return None

    try:
        return resolved.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            return resolved.read_text(encoding="gbk")
        except Exception:
            return None


def extract_filename(text: str) -> str | None:
    """从消息中提取文件名"""
    # 匹配 .txt .log .md 等文本文件
    m = re.search(r"(\S+\.(?:txt|log|md|csv|json))", text)
    if m:
        filename = m.group(1)
        # 只允许 data/ 下的文件或裸文件名
        if filename.startswith("data/") or filename.startswith("data\\"):
            return filename
        if "/" not in filename and "\\" not in filename:
            return filename
    return None
