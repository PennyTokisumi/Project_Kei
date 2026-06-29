"""LLM Chat 插件 — LLM API 客户端（支持 DeepSeek / Gemini）"""

import json
import time
from typing import Optional

import httpx
from nonebot import logger as nb_logger

from config import config


class LLMClient:
    """多供应商 LLM API 封装"""

    def __init__(self):
        self._api_key: Optional[str] = config.llm_api_key or None
        self._model: str = config.llm_model
        self._base_url: str = config.llm_base_url
        self._is_gemini: bool = config.is_gemini
        self._proxy: Optional[str] = config.llm_proxy if self._is_gemini else None

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
        """记录 token 用量到数据库"""
        from .database import log_token_usage
        log_token_usage(model, prompt_tokens, completion_tokens)

    async def chat(
        self,
        messages: list[dict],
        temperature: float = None,
        max_tokens: int = 512,
        tools: Optional[list[dict]] = None,
        enable_thinking: bool = False,
        thinking_effort: str = "low",
    ) -> dict:
        """发送聊天请求。enable_thinking/thinking_effort 仅 DeepSeek 生效，Gemini 静默跳过。"""
        if temperature is None:
            temperature = config.chat_temperature
        if not self.available:
            return {"content": "", "error": "API Key 未配置", "usage": {"prompt_tokens": 0, "completion_tokens": 0}}

        payload: dict = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        # Gemini 的 max_tokens 含 prompt，设低会截断输出；设安全帽即可
        if not self._is_gemini:
            payload["max_tokens"] = max_tokens
        else:
            payload["max_tokens"] = 2048
        if not self._is_gemini and enable_thinking:
            payload["reasoning_effort"] = thinking_effort
        elif not self._is_gemini:
            payload["thinking"] = {"type": "disabled"}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        start = time.monotonic()
        try:
            async with httpx.AsyncClient(proxy=self._proxy, timeout=30) as client:
                resp = await client.post(
                    f"{self._base_url}/chat/completions",
                    headers=self._headers(),
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as e:
            err_msg = str(e)
            if hasattr(e, "response") and e.response is not None:
                try:
                    err_msg = f"{e} | 响应: {e.response.text[:500]}"
                except Exception:
                    pass
            nb_logger.error(f"LLM API 请求失败 [{self._model}]: {err_msg}")
            return {"content": "", "error": err_msg, "usage": {"prompt_tokens": 0, "completion_tokens": 0}}
        except Exception as e:
            nb_logger.error(f"LLM API 未知错误 [{self._model}]: {e}")
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
