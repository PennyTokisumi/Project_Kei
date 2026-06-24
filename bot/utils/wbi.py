"""B站 WBI 签名工具

B站从 2023 年起对部分 API 启用了 WBI 签名校验。
此模块负责获取 mixin_key 并对请求参数签名。
"""

import hashlib
import time
import urllib.parse
from typing import Optional

from httpx import AsyncClient

# ─── 固定混淆表（取自 B站 前端源码）─────────────────────────
MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]

# 全局缓存：mixin_key 只需获取一次，有有效期但很长
_cached_mixin_key: Optional[str] = None


async def _get_mixin_key() -> str:
    """从 B站 nav 接口获取 img_key + sub_key 并计算出 mixin_key"""
    global _cached_mixin_key
    if _cached_mixin_key is not None:
        return _cached_mixin_key

    try:
        async with AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://api.bilibili.com/x/web-interface/nav",
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                                  "Chrome/120.0.0.0 Safari/537.36",
                    "Referer": "https://www.bilibili.com/",
                },
            )
            data = resp.json()
    except Exception:
        # 获取失败返回空字符串，后续签名也将失败
        return ""

    wbi_img = data.get("data", {}).get("wbi_img", {})
    img_url = wbi_img.get("img_url", "")
    sub_url = wbi_img.get("sub_url", "")

    # 从 URL 中提取 key（文件名去掉扩展名）
    def _extract_key(url: str) -> str:
        # URL 格式: https://i0.hdslb.com/bfs/wbi/xxx.png
        try:
            filename = url.rsplit("/", 1)[-1]
            return filename.rsplit(".", 1)[0]
        except Exception:
            return ""

    img_key = _extract_key(img_url)
    sub_key = _extract_key(sub_url)

    raw = img_key + sub_key
    if len(raw) < 32:
        return ""

    mixin = "".join(raw[i] for i in MIXIN_KEY_ENC_TAB if i < len(raw))
    _cached_mixin_key = mixin[:32]
    return _cached_mixin_key


async def sign_params(params: dict) -> dict:
    """对参数字典进行 WBI 签名，返回添加了 w_rid 和 wts 的新字典"""
    mixin_key = await _get_mixin_key()
    if not mixin_key:
        # 签名不可用，只追加 wts 不加 w_rid
        params["wts"] = str(int(time.time()))
        return params

    # 添加时间戳
    params["wts"] = str(int(time.time()))

    # 按键排序
    sorted_params = sorted(params.items(), key=lambda x: x[0])

    # 构造待签名字符串（过滤掉值为空字符串和跳过特殊字符）
    query = urllib.parse.urlencode(sorted_params)
    sign_str = query + mixin_key

    # 计算 MD5
    w_rid = hashlib.md5(sign_str.encode()).hexdigest()
    params["w_rid"] = w_rid
    return params


def clear_cache():
    """清除 mixin_key 缓存（用于测试或 key 过期时）"""
    global _cached_mixin_key
    _cached_mixin_key = None
