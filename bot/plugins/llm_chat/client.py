"""LLM Chat 插件 — LLM API 客户端（支持 DeepSeek / Gemini）"""

import json
import time
from typing import Optional

import httpx
from nonebot import logger as nb_logger

from config import config

# 官方接受的等级是 low / high / max，其余写法按官方映射折算
_EFFORT_MAP = {
    "minimal": "low",
    "low": "low",
    "medium": "high",
    "high": "high",
    "xhigh": "high",
    "max": "max",
    "ultra": "max",
}


def normalize_effort(value: Optional[str]) -> str:
    """把各种推理等级写法折算成服务端认识的 low / high / max

    官方映射：minimal→low、low→low、medium→high、high→high、xhigh→high、
    max→max、ultra→max。无法识别时退回 low（**不是** high —— 默认别给她最贵的档）。
    """
    if not value:
        return "low"
    return _EFFORT_MAP.get(str(value).strip().lower(), "low")


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
                      completion_tokens: int,
                      cache_hit_tokens: int = 0,
                      cache_miss_tokens: int = 0):
        """记录 token 用量到数据库（缓存字段缺失时按 0 记）"""
        from .database import log_token_usage
        log_token_usage(
            model, prompt_tokens, completion_tokens,
            cache_hit_tokens=cache_hit_tokens,
            cache_miss_tokens=cache_miss_tokens,
        )

    def _build_payload(
        self,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
        tools: Optional[list[dict]],
        enable_thinking: bool,
        effort: str,
    ) -> dict:
        """组装请求体（单独抽出来是为了能脱离网络单测）

        thinking 的开关方式：
        - 开：只发 `reasoning_effort`。服务端**默认就是开启**，所以不必再发
          `{"thinking": {"type": "enabled"}}`（发了也无害）
        - 关：发 `thinking: disabled`
        Gemini 不认这些字段，直接跳过。
        """
        payload: dict = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
            "max_tokens": max_tokens,
        }
        if not self._is_gemini:
            if enable_thinking:
                payload["reasoning_effort"] = effort
            else:
                payload["thinking"] = {"type": "disabled"}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        return payload

    async def chat(
        self,
        messages: list[dict],
        temperature: float = None,
        max_tokens: int = 512,
        tools: Optional[list[dict]] = None,
        enable_thinking: Optional[bool] = None,
        thinking_effort: Optional[str] = None,
    ) -> dict:
        """发送聊天请求

        `enable_thinking` / `thinking_effort` 传 None 时取 config 里的全局设置
        （`LLM_ENABLE_THINKING` / `LLM_THINKING_EFFORT`），所以对话主路径不必逐处传参。
        两者仅 DeepSeek 生效，Gemini 静默跳过。

        ⚠️ 开启 thinking 后 `temperature` 会被服务端忽略（官方明确不支持），
        而且思考 token 计入 `max_tokens`，额度太小会导致 content 为空。
        """
        if temperature is None:
            temperature = config.chat_temperature
        if enable_thinking is None:
            enable_thinking = config.llm_enable_thinking
        effort = normalize_effort(
            thinking_effort if thinking_effort is not None else config.llm_thinking_effort
        )
        if not self.available:
            return {"content": "", "error": "API Key 未配置", "usage": {"prompt_tokens": 0, "completion_tokens": 0}}

        payload = self._build_payload(
            messages, temperature, max_tokens, tools, enable_thinking, effort
        )

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
        # DeepSeek 前缀缓存的命中 / 未命中 token 数；
        # Gemini 等供应商的 usage 没有这两个字段，按 0 处理
        cache_hit_tokens = usage.get("prompt_cache_hit_tokens") or 0
        cache_miss_tokens = usage.get("prompt_cache_miss_tokens") or 0

        # 思考内容走独立的 reasoning_content 字段（不计入 content）。
        # content 为空而 reasoning 有内容 = 思考把 max_tokens 吃光了，
        # 这是开启 thinking 后最容易踩的坑，必须打出可操作的日志。
        reasoning = message.get("reasoning_content") or ""
        content = message.get("content") or ""
        if reasoning:
            nb_logger.info(f"LLM 思考内容 {len(reasoning)} 字符（不计入 content）")
        if reasoning and not content.strip():
            nb_logger.warning(
                f"LLM 只输出了思考、正文为空：思考 {len(reasoning)} 字符吃光了 "
                f"max_tokens={max_tokens}。请调大 max_tokens，或设 LLM_ENABLE_THINKING=false"
            )

        nb_logger.info(
            f"LLM 请求完成 [{self._model}] "
            f"prompt={prompt_tokens} completion={completion_tokens} "
            f"cache={cache_hit_tokens}/{cache_miss_tokens} "
            f"耗时={elapsed:.1f}s"
        )

        self._record_usage(
            self._model, prompt_tokens, completion_tokens,
            cache_hit_tokens, cache_miss_tokens,
        )

        return {
            "content": content,
            "tool_calls": message.get("tool_calls"),
            "reasoning_chars": len(reasoning),
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "prompt_cache_hit_tokens": cache_hit_tokens,
                "prompt_cache_miss_tokens": cache_miss_tokens,
            },
        }


# 全局单例
llm_client = LLMClient()
