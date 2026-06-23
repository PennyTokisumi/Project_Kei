"""数据源抽象基类 - 所有监测适配器需继承此类"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Item:
    """通用内容条目，各适配器统一输出格式"""
    id: str                  # 内容唯一 ID（用于去重）
    platform: str            # bilibili | douyu
    source_type: str         # dynamic | live
    target_id: str           # uid / room_id
    title: str               # 标题
    nickname: str            # 主播/用户昵称
    content: str             # 正文（动态内容或直播标题）
    link: str                # 跳转链接
    cover_url: Optional[str] = None  # 封面图 URL
    extra: dict = field(default_factory=dict)  # 平台特有扩展信息


class SourceBase(ABC):
    """监测源基类

    每个平台/类型的监测适配器继承此类，实现 fetch 和 get_display_name。
    """

    def __init__(self, target_id: str, group_id: int):
        self.target_id = target_id
        self.group_id = group_id

    @property
    @abstractmethod
    def platform(self) -> str:
        """平台标识，如 'bilibili', 'douyu'"""
        ...

    @property
    @abstractmethod
    def source_type(self) -> str:
        """内容类型，如 'dynamic', 'live'"""
        ...

    @abstractmethod
    async def fetch(self) -> list[Item]:
        """拉取最新内容

        返回 Item 列表，由调用方负责去重。
        """

    @abstractmethod
    async def get_display_name(self) -> str:
        """获取显示名称（主播名/用户名），用于缓存到 DB"""
