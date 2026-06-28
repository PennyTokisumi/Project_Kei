"""LLM Chat 插件 — DeepSeek API 客户端"""

import json
import time
from typing import Optional

import httpx
from nonebot import logger as nb_logger

from config import config


class LLMClient:
    """DeepSeek API 封装"""

    def __init__(self):
        self._api_key: Optional[str] = config.deepseek_api_key or None
        self._model: str = config.deepseek_model or "deepseek-v4-flash"
        self._base_url: str = "https://api.deepseek.com"

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _record_usage(model: str,
                      prompt_tokens: int,
                      completion_tokens: int):
        """记录 token 用量到数据库（由 __init__ 注入回调）"""
        from .database import log_token_usage
        log_token_usage(model, prompt_tokens, completion_tokens)

    async def chat(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 512,
        tools: Optional[list[dict]] = None,
    ) -> dict:
        """发送聊天请求"""
        if not self.available:
            return {"content": "", "error": "API Key 未配置", "usage": {"prompt_tokens": 0, "completion_tokens": 0}}

        payload: dict = {
            "model": self._model,
            "messages": msgs,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{self._base_url}/chat/completions",
                    headers=self._headers(),
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as e:
            err_msg = str(e)
            # 尝试提取响应体中的详细错误
            if hasattr(e, "response") and e.response is not None:
                try:
                    err_body = e.response.text[:500]
                    err_msg = f"{e} | 响应: {err_body}"
                except Exception:
                    pass
            nb_logger.error(f"DeepSeek API 请求失败: {err_msg}")
            return {"content": "", "error": err_msg, "usage": {"prompt_tokens": 0, "completion_tokens": 0}}
        except Exception as e:
            nb_logger.error(f"DeepSeek API 未知错误: {e}")
            return {"content": "", "error": str(e), "usage": {"prompt_tokens": 0, "completion_tokens": 0}}

        elapsed = time.monotonic() - start
        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})
        usage = data.get("usage", {})

        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        nb_logger.info(
            f"LLM 请求完成 [{self._model}] "
            f"prompt={prompt_tokens} completion={completion_tokens} "
            f"耗时={elapsed:.1f}s"
        )

        self._record_usage(self._model, prompt_tokens, completion_tokens)

        return {
            "content": message.get("content") or "",
            "tool_calls": message.get("tool_calls"),
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
        }


# 全局单例
llm_client = LLMClient()
