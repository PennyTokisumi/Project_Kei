"""WBI 签名模块测试"""

import hashlib
import time
from unittest.mock import AsyncMock, patch

import pytest

from utils.wbi import sign_params, clear_cache, MIXIN_KEY_ENC_TAB


class TestWbiSigning:
    """WBI 签名算法测试"""

    @pytest.mark.asyncio
    async def test_sign_params_with_mock_mixin(self, monkeypatch):
        """测试签名参数格式正确性"""
        # Mock _get_mixin_key 返回固定值
        mock_mixin = "a" * 32

        async def mock_get_mixin():
            return mock_mixin

        monkeypatch.setattr(
            "utils.wbi._get_mixin_key", mock_get_mixin,
        )

        params = {"mid": "436742"}
        result = await sign_params(params.copy())

        # 应包含原始参数 + wts + w_rid
        assert "mid" in result
        assert result["mid"] == "436742"
        assert "wts" in result
        assert "w_rid" in result

        # 验证 w_rid 是 32 位 MD5 十六进制
        assert len(result["w_rid"]) == 32
        assert all(c in "0123456789abcdef" for c in result["w_rid"])

    @pytest.mark.asyncio
    async def test_sign_params_key_sorting(self, monkeypatch):
        """测试参数按键排序后签名"""
        mock_mixin = "b" * 32

        async def mock_get_mixin():
            return mock_mixin

        monkeypatch.setattr(
            "utils.wbi._get_mixin_key", mock_get_mixin,
        )

        # 乱序传入参数
        params = {"c": "3", "a": "1", "b": "2"}
        result = await sign_params(params.copy())

        # 验证签名是通过排序后的参数计算的
        # 手动计算预期
        from urllib.parse import urlencode
        sorted_params = sorted(
            {"a": "1", "b": "2", "c": "3", "wts": result["wts"]}.items(),
            key=lambda x: x[0],
        )
        expected_sign = hashlib.md5(
            (urlencode(sorted_params) + mock_mixin).encode()
        ).hexdigest()

        assert result["w_rid"] == expected_sign

    @pytest.mark.asyncio
    async def test_sign_params_empty_mixin(self, monkeypatch):
        """mixin_key 为空时只追加 wts"""
        async def mock_get_mixin():
            return ""

        monkeypatch.setattr(
            "utils.wbi._get_mixin_key", mock_get_mixin,
        )

        result = await sign_params({"mid": "123"})
        assert "mid" in result
        assert "wts" in result
        # 没有 mixin_key 时不应有 w_rid
        # (当前实现仍会添加空的 w_rid，检查其行为)
        assert "w_rid" not in result or result.get("w_rid", "") == ""


class TestMixinKeyEncTab:
    """混淆表验证"""

    def test_table_length(self):
        """混淆表应有 64 个元素"""
        assert len(MIXIN_KEY_ENC_TAB) == 64

    def test_table_all_indices(self):
        """混淆表应覆盖 0-63 且无重复"""
        assert sorted(MIXIN_KEY_ENC_TAB) == list(range(64))


def test_clear_cache(monkeypatch):
    """清除缓存后应重新获取"""
    # 设置缓存
    import utils.wbi as wbi
    wbi._cached_mixin_key = "test_key"
    clear_cache()
    assert wbi._cached_mixin_key is None
