"""B站 API 共享工具 — headers 和 URL 处理"""

from config import config


def make_headers(referer: str = "https://space.bilibili.com/") -> dict:
    """构建 B站 API 请求头，自动注入 Cookie（如已配置）"""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": referer,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Origin": "https://space.bilibili.com",
    }
    if config.bilibili_cookie:
        headers["Cookie"] = config.bilibili_cookie
    return headers
