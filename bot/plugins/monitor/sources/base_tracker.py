"""直播状态跟踪器 - 检测 off→on 状态切换"""

from typing import Optional

from ..database import get_live_status, set_live_status


class LiveStatusTracker:
    """直播状态跟踪，判断是否刚开播"""

    def __init__(self, room_id: str, platform: str):
        self.room_id = room_id
        self.platform = platform

    def check_and_update(self, is_living: bool,
                         title: str = "") -> bool:
        """检测是否刚开播（off→on）

        返回 True 表示检测到开播事件
        """
        prev = get_live_status(self.room_id, self.platform)

        if prev is None:
            # 初次记录，不上报开播
            set_live_status(self.room_id, self.platform, is_living, title)
            return False

        was_living = bool(prev["is_living"])
        set_live_status(self.room_id, self.platform, is_living, title)

        # off → on 检测
        if not was_living and is_living:
            return True

        return False

    def get_current_status(self) -> Optional[dict]:
        """获取当前记录的直播状态"""
        return get_live_status(self.room_id, self.platform)
