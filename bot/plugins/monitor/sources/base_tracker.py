"""直播状态跟踪器 - 检测 off→on 状态切换"""

from ..database import get_live_status, set_live_status


class LiveStatusTracker:
    """直播状态跟踪，判断是否刚开播

    room_id 在 DB 中编码为 {group_id}:{room_id} 以支持多群独立追踪。
    """

    _OFFLINE_CONFIRM = 2  # 连续 N 次空结果才确认下播（容错 API 波动）

    def __init__(self, room_id: str, platform: str, group_id: int = 0):
        self.room_id = room_id
        self.platform = platform
        self.group_id = group_id
        self._db_room_id = f"{group_id}:{room_id}" if group_id else room_id

    def check_and_update(self, is_living: bool,
                         title: str = "") -> bool:
        """检测是否刚开播（off→on）

        返回 True 表示检测到开播事件
        """
        prev = get_live_status(self._db_room_id, self.platform)

        if prev is None:
            set_live_status(self._db_room_id, self.platform,
                          is_living, title,
                          offline_count=0 if is_living else 1)
            return is_living

        was_living = bool(prev["is_living"])
        offline_count = int(prev.get("offline_count", 0))

        if is_living:
            # 开播中 → 直接确认在线
            set_live_status(self._db_room_id, self.platform,
                          True, title, offline_count=0)
            return not was_living
        else:
            # 未开播 → 需要连续多次确认，防止 API 波动导致误判
            offline_count += 1
            confirmed = offline_count >= self._OFFLINE_CONFIRM
            set_live_status(self._db_room_id, self.platform,
                          False if confirmed else was_living, title,
                          offline_count=offline_count)
            return False
