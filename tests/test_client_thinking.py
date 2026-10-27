"""LLM 客户端：模型名 / 温度 / 推理等级的配置与请求体组装

这几项都能在 .env 里覆盖，而且 thinking 与 temperature **互斥**
（官方明说 thinking 模式不支持 temperature），所以用测试把它们钉住。
"""

import pytest

from config import config
from plugins.llm_chat.client import LLMClient, normalize_effort


class TestConfigDefaults:
    def test_model_is_canonical_name(self):
        """用正式名 deepseek-flash，不要依赖退役别名 deepseek-v4-flash"""
        assert config.deepseek_model == "deepseek-flash"

    def test_thinking_off_by_default(self):
        """校验的是**代码默认值**，不是当前生效值 —— .env 会覆盖它"""
        assert type(config).model_fields["llm_enable_thinking"].default is False

    def test_default_effort_is_low(self):
        """同上：环境变量配置成 high 也不该让这条测试失败"""
        assert type(config).model_fields["llm_thinking_effort"].default == "low"

    def test_chat_max_tokens_default(self):
        """聊天输出预算默认 512；开 thinking 时要在 .env 调大（思考 token 也占这份额度）"""
        assert type(config).model_fields["llm_chat_max_tokens"].default == 512

    def test_temperature_default(self):
        assert config.chat_temperature == pytest.approx(0.7)

    def test_temperature_is_env_configurable(self):
        """温度必须是 Field，不能是写死的 property —— 否则 .env 覆盖不了"""
        assert "chat_temperature" in type(config).model_fields


class TestNormalizeEffort:
    @pytest.mark.parametrize("raw,expected", [
        ("minimal", "low"), ("low", "low"),
        ("medium", "high"), ("high", "high"), ("xhigh", "high"),
        ("max", "max"), ("ultra", "max"),
    ])
    def test_official_mapping(self, raw, expected):
        assert normalize_effort(raw) == expected

    def test_case_and_space_insensitive(self):
        assert normalize_effort("  HIGH  ") == "high"
        assert normalize_effort("Max") == "max"

    def test_unknown_falls_back_to_low(self):
        """无法识别时退回 low，不要默默升到最贵的 max"""
        assert normalize_effort("乱写的") == "low"

    def test_none_and_empty(self):
        assert normalize_effort(None) == "low"
        assert normalize_effort("") == "low"


class TestBuildPayload:
    def _client(self, gemini: bool = False) -> LLMClient:
        c = LLMClient()
        c._model = "deepseek-flash"
        c._is_gemini = gemini
        return c

    def test_thinking_disabled_explicitly(self):
        """关闭时必须发 thinking: disabled —— 服务端默认是开启的，
        不发这个字段等于没关掉"""
        p = self._client()._build_payload([], 0.7, 512, None, False, "low")
        assert p["thinking"] == {"type": "disabled"}
        assert "reasoning_effort" not in p

    def test_thinking_enabled_sends_effort(self):
        p = self._client()._build_payload([], 0.7, 512, None, True, "max")
        assert p["reasoning_effort"] == "max"
        assert "thinking" not in p

    def test_temperature_passed_through(self):
        p = self._client()._build_payload([], 0.55, 512, None, False, "low")
        assert p["temperature"] == 0.55

    def test_max_tokens_and_stream(self):
        p = self._client()._build_payload([], 0.7, 256, None, False, "low")
        assert p["max_tokens"] == 256
        assert p["stream"] is False

    def test_tools_attached_when_given(self):
        tools = [{"type": "function", "function": {"name": "x"}}]
        p = self._client()._build_payload([], 0.7, 512, tools, False, "low")
        assert p["tools"] == tools
        assert p["tool_choice"] == "auto"

    def test_gemini_gets_neither_thinking_field(self):
        """Gemini 的 OpenAI 兼容层不认这些字段"""
        p = self._client(gemini=True)._build_payload([], 0.7, 512, None, True, "high")
        assert "thinking" not in p
        assert "reasoning_effort" not in p

    def test_model_name_in_payload(self):
        p = self._client()._build_payload([], 0.7, 512, None, False, "low")
        assert p["model"] == "deepseek-flash"
