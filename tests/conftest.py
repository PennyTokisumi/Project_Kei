"""pytest 共享 fixtures — 在测试导入前 mock NoneBot"""

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ─── 路径 ──────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bot"))

# ─── Mock NoneBot（必须在任何 bot 模块导入之前）─────────
# plugins/monitor/__init__.py 顶层调用了 get_driver()，
# 需要在此先注册 fake driver 到 sys.modules 中

_fake_driver = MagicMock()
_fake_driver.on_startup = lambda func: func  # 装饰器原样返回
_fake_driver.on_shutdown = lambda func: func

_fake_nonebot = MagicMock()
_fake_nonebot.get_driver.return_value = _fake_driver
_fake_nonebot.logger = MagicMock()

# 注入 mock 模块
sys.modules["nonebot"] = _fake_nonebot
sys.modules["nonebot.adapters"] = MagicMock()
sys.modules["nonebot.adapters.onebot"] = MagicMock()
sys.modules["nonebot.adapters.onebot.v11"] = MagicMock()
sys.modules["nonebot.rule"] = MagicMock()

# Mock tray 模块（无 GUI 环境无法导入 pystray）
_fake_tray = MagicMock()
_fake_tray.start = MagicMock()
_fake_tray.stop = MagicMock()
sys.modules["tray"] = MagicMock()
sys.modules["tray"].tray = _fake_tray
sys.modules["tray"].update_status = MagicMock()

# 修补 on_message / to_me / startswith 等
import nonebot.adapters.onebot.v11 as _v11_mock

class _FakeMessageSegment:
    """假的 MessageSegment — 可被 isinstance 检查"""
    def __init__(self, seg_type: str, data: str):
        self.type = seg_type
        self.data = data

    def __eq__(self, other):
        if isinstance(other, _FakeMessageSegment):
            return self.type == other.type and self.data == other.data
        return False

    def __repr__(self):
        return f"MessageSegment({self.type}, {self.data!r})"

    @staticmethod
    def text(text: str):
        return _FakeMessageSegment("text", text)

    @staticmethod
    def image(url: str):
        return _FakeMessageSegment("image", url)


class _FakeMessage(list):
    """假的 Message — 继承 list 以容纳多个 segment"""
    def extract_plain_text(self) -> str:
        return "".join(
            seg.data for seg in self
            if isinstance(seg, _FakeMessageSegment) and seg.type == "text"
        )


_v11_mock.MessageSegment = _FakeMessageSegment
_v11_mock.Message = _FakeMessage


# ─── Fixtures ──────────────────────────────────────────

@pytest.fixture
def temp_db():
    """临时数据库路径，测试后清理"""
    fd, path = tempfile.mkstemp(suffix=".sqlite3")
    os.close(fd)
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture(autouse=True)
def _patch_db_path(monkeypatch, temp_db):
    """所有测试自动使用临时数据库"""
    from config import config as cfg
    monkeypatch.setattr(cfg, "db_path", str(temp_db))


# ─── 测试样本数据 ────────────────────────────────────────

@pytest.fixture
def sample_dynamic_draw():
    """B站图文动态 API 响应片段（单图）"""
    return {
        "id_str": "123456789",
        "id": 123456789,
        "modules": {
            "module_author": {"name": "测试用户", "mid": 436742},
            "module_dynamic": {
                "desc": {
                    "type": "DYNAMIC_TYPE_DRAW",
                    "text": "分享一张好看的图<emoji>",
                },
                "major": {
                    "draw": {
                        "items": [
                            {"src": "https://i0.hdslb.com/bfs/archive/abc123.jpg"},
                        ],
                    },
                },
            },
        },
    }


@pytest.fixture
def sample_dynamic_draw_multi():
    """B站图文动态 API 响应片段（多图）"""
    return {
        "id_str": "987654321",
        "id": 987654321,
        "modules": {
            "module_author": {"name": "多图用户", "mid": 12345},
            "module_dynamic": {
                "desc": {
                    "type": "DYNAMIC_TYPE_DRAW",
                    "text": "九宫格来啦！",
                },
                "major": {
                    "draw": {
                        "items": [
                            {"src": "https://i0.hdslb.com/bfs/archive/img1.jpg"},
                            {"src": "https://i0.hdslb.com/bfs/archive/img2.jpg"},
                            {"src": "https://i0.hdslb.com/bfs/archive/img3.jpg"},
                            {"src": "https://i0.hdslb.com/bfs/archive/img4.jpg"},
                        ],
                    },
                },
            },
        },
    }


@pytest.fixture
def sample_dynamic_av():
    """B站视频投稿动态"""
    return {
        "id_str": "111222333",
        "modules": {
            "module_author": {"name": "UP主"},
            "module_dynamic": {
                "desc": {
                    "type": "DYNAMIC_TYPE_AV",
                    "text": "新视频来啦！",
                },
                "major": {
                    "archive": {
                        "cover": "https://i0.hdslb.com/bfs/archive/video_cover.jpg",
                        "title": "我的新视频",
                    },
                },
            },
        },
    }


@pytest.fixture
def sample_dynamic_forward():
    """B站转发动态 — 应被过滤"""
    return {
        "id_str": "999999999",
        "modules": {
            "module_author": {"name": "转发者"},
            "module_dynamic": {
                "desc": {
                    "type": "DYNAMIC_TYPE_FORWARD",
                    "text": "转发一下",
                },
            },
        },
    }


@pytest.fixture
def sample_dynamic_word():
    """B站纯文字动态"""
    return {
        "id_str": "555555555",
        "modules": {
            "module_author": {"name": "文字博主"},
            "module_dynamic": {
                "desc": {
                    "type": "DYNAMIC_TYPE_WORD",
                    "text": "今天天气真好！",
                },
            },
        },
    }
