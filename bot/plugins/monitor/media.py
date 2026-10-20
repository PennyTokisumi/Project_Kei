"""媒体下载与编码 — 给需要「自己取图」的平台用（目前只有 Pixiv）

为什么需要这个模块：

B站/斗鱼的 CDN 没有防盗链，直接把 URL 丢给 QQ，让 QQ 自己去拉就行。
但 Pixiv 的 `i.pximg.net` 会校验 Referer，无 Referer 直接 403，
QQ 拉图时不会带 Pixiv 的 Referer —— **直接传 URL 一定是裂图**。

所以 Pixiv 的图片必须走「下载 → 压缩 → base64」这条路：

    https://i.pximg.net/...   （带 Referer 下载）
        ↓  Pillow 等比缩放到长边 ≤ 1200，JPEG q85
        ↓  base64 编码
    base64://xxxxx            （SnowLuma 的 inlineBase64Payload 认这个格式）

实测数据（2026-10-21）：原图普遍 800 KB～1.2 MB，压缩后 120～280 KB，
压掉约 75%～80%，画质在 QQ 里看没有明显损失。
"""

import base64
import io
from typing import Optional

from httpx import AsyncClient
from nonebot import logger as nb_logger
from PIL import Image

# 等比缩放的长边上限（小图不会被放大）
DEFAULT_MAX_SIDE = 1200
# JPEG 质量
DEFAULT_JPEG_QUALITY = 85

_BASE64_PREFIX = "base64://"


def _to_rgb(img: Image.Image) -> Image.Image:
    """统一转成 RGB，JPEG 不支持透明通道

    带透明通道的图（RGBA / P 模式带透明）直接转 RGB 会得到黑底，
    所以先铺一层白底再合成。
    """
    if img.mode == "RGB":
        return img
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.split()[-1])
        return background
    return img.convert("RGB")


def compress_to_jpeg(
    raw: bytes,
    *,
    max_side: int = DEFAULT_MAX_SIDE,
    quality: int = DEFAULT_JPEG_QUALITY,
) -> Optional[bytes]:
    """把图片字节压缩成 JPEG 字节；解不开时返回 None"""
    try:
        with Image.open(io.BytesIO(raw)) as img:
            img = _to_rgb(img)
            # thumbnail 是等比缩放且**只缩不放**，小图保持原样
            img.thumbnail((max_side, max_side), Image.LANCZOS)
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=quality, optimize=True)
            return out.getvalue()
    except Exception as e:
        nb_logger.warning(f"图片压缩失败: {e!r}")
        return None


async def download_as_base64(
    url: str,
    headers: dict,
    *,
    max_side: int = DEFAULT_MAX_SIDE,
    quality: int = DEFAULT_JPEG_QUALITY,
) -> Optional[str]:
    """下载图片并返回 ``base64://...`` 字符串，失败返回 None

    全程在内存里完成，**不落盘**。
    """
    if not url:
        return None
    try:
        async with AsyncClient(timeout=60, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                nb_logger.warning(f"图片下载失败 HTTP {resp.status_code}: {url[:80]}")
                return None
            raw = resp.content
    except Exception as e:
        nb_logger.warning(f"图片下载异常: {e!r}")
        return None

    compressed = compress_to_jpeg(raw, max_side=max_side, quality=quality)
    if not compressed:
        return None

    nb_logger.debug(
        f"图片 {len(raw)//1024}KB -> {len(compressed)//1024}KB ({url.rsplit('/', 1)[-1]})"
    )
    return _BASE64_PREFIX + base64.b64encode(compressed).decode()
