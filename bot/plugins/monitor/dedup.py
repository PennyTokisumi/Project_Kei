"""去重模块 - 封装 pushed_items 表操作"""

from .database import is_pushed, mark_pushed


class Deduplicator:
    """内容去重器，判断一条内容是否已被推送过"""

    @staticmethod
    def make_id(platform: str, source_type: str, target_id: str,
                content_id: str) -> str:
        """生成全局唯一去重 ID

        格式: {platform}_{source_type}/{target_id}/{content_id}
        示例: bilibili_dynamic/436742/123456789
        """
        return f"{platform}_{source_type}/{target_id}/{content_id}"

    def already_pushed(self, item_id: str) -> bool:
        """是否已推送过"""
        return is_pushed(item_id)

    def mark_pushed(self, platform: str, source_type: str,
                    target_id: str, content_id: str,
                    title: str = "", link: str = ""):
        """标记为已推送"""
        item_id = self.make_id(platform, source_type, target_id, content_id)
        mark_pushed(item_id, platform, target_id, source_type, title, link)

    def is_new(self, platform: str, source_type: str,
               target_id: str, content_id: str) -> bool:
        """判断是否新内容（未推送过）"""
        item_id = self.make_id(platform, source_type, target_id, content_id)
        return not self.already_pushed(item_id)


# 全局单例
dedup = Deduplicator()
