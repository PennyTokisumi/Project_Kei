"""LLM Chat 插件 — 本地文件读取"""

from pathlib import Path

MAX_SIZE = 200 * 1024  # 200KB


def safe_read(filename: str) -> str | None:
    """安全读取 data/ 下的文件"""
    if ".." in filename or "/" in filename or "\\" in filename:
        return None

    from config import DATA_DIR
    target = DATA_DIR / filename
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
