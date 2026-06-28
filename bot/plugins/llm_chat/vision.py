"""LLM Chat 插件 — 图像工具（预留接口，当前 API 不支持视觉）"""

import base64

import httpx
from nonebot import logger as nb_logger


async def download_image(url: str) -> bytes | None:
    """下载图片，返回字节"""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.content
    except Exception as e:
        nb_logger.warning(f"下载图片失败: {url[:100]}... {e}")
        return None


def image_to_data_url(data: bytes, mime: str = "image/jpeg") -> str:
    """图片字节 → base64 data URL（供视觉 LLM）"""
    b64 = base64.b64encode(data).decode()
    return f"data:{mime};base64,{b64}"
