"""Pixiv 插画/漫画监测源 — 直连 Pixiv 网页 AJAX 接口

## 实测结论（2026-10-21，全部真机验证过）

- `ajax/user/{uid}/profile/top` **一次请求**就能拿到该画师的近期作品列表，
  每条自带 `xRestrict`（R18 标记）和 `createDate`，**不需要为每个作品再查详情**。
- **不需要登录**；但必须带真实 UA 和 `Referer: https://www.pixiv.net/`。
- Pixiv 在本机网络**不能直连**，必须走代理。代码不显式配代理，依赖 httpx 默认行为
  （`trust_env=True` → `urllib.getproxies()` → 读 Windows 系统代理），
  所以只要 Clash 开着就能通。
- 图片 CDN `i.pximg.net` **有防盗链**：无 Referer 直接 403，带 Referer 才 200。
  因此图片不能把 URL 丢给 QQ，必须自己下载后转 base64（见 `media.py`）。

## R18 判定：只看 xRestrict

- `0` = 全年龄，`1` = R18，`2` = R18G
- **不要用 `sl` 字段判断**。实测正常全年龄作品上 `sl` 可能是 2，也可能是 6，
  它表示的是别的分级维度。项目约定：**只信 `xRestrict`**。

## 典型返回（节选）

```json
"id": "148404748",
"title": "燐羽ちゃん!お姉ちゃんは渡さないよ!!",
"userName": "にんげんまめ￤1日目東ア-31",
"xRestrict": 0,
"sl": 2,
"illustType": 0,
"pageCount": 2,
"createDate": "2026-08-14T01:50:00+00:00",
"isMasked": false,
"aiType": 1,
"urls": { "mini": ..., "thumb": ..., "small": ..., "regular": ... }
```
"""

from datetime import datetime
from typing import Optional

from httpx import AsyncClient
from nonebot import logger as nb_logger

import time as time_module

from .base import Item, SourceBase

# ─── 失败退避 ─────────────────────────────────────────────
# 早期版本遇到 403/429 只打一条 warning 就返回空列表，
# 从日志上根本分不清「画师没更新」和「我们被限流了」。
# 现在会指数退避，并把退避时长明确写进日志。

# 明确表示被限流的状态码
RATE_LIMIT_STATUS = frozenset({403, 429})
# 被限流时的退避基数：300s → 600s → 1200s …（封顶 1 小时）
BACKOFF_BASE_SECONDS = 300
# 网络异常等普通失败：60s → 120s → 240s …
BACKOFF_MILD_SECONDS = 60
BACKOFF_MAX_SECONDS = 3600

PIXIV_PROFILE_TOP = "https://www.pixiv.net/ajax/user/{uid}/profile/top"
PIXIV_ILLUST_PAGES = "https://www.pixiv.net/ajax/illust/{pid}/pages"
PIXIV_ARTWORK = "https://www.pixiv.net/artworks/{pid}"

PIXIV_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# R18 判定：0=全年龄（可推），1=R18，2=R18G
ALL_AGES_RESTRICT = 0


def pixiv_headers(uid: Optional[str] = None) -> dict:
    """Pixiv AJAX 请求头。Referer 必须指向具体页面，否则可能被拦。"""
    referer = f"https://www.pixiv.net/users/{uid}" if uid else "https://www.pixiv.net/"
    return {
        "User-Agent": PIXIV_UA,
        "Referer": referer,
        "Accept": "application/json",
        "Accept-Language": "zh-CN,zh;q=0.9,ja;q=0.8",
    }


def image_headers() -> dict:
    """下载 i.pximg.net 图片用的请求头（防盗链要求 Referer）"""
    return {"User-Agent": PIXIV_UA, "Referer": "https://www.pixiv.net/"}


def parse_pub_ts(create_date: str) -> float:
    """把 Pixiv 的 ISO 时间转成 Unix 时间戳，失败返回 0"""
    if not create_date:
        return 0.0
    try:
        return datetime.fromisoformat(create_date).timestamp()
    except Exception:
        return 0.0


def _parse_tags(raw) -> list[str]:
    """标签解析，兼容两种接口格式

    - `ajax/user/{uid}/profile/top`：`tags` 是**字符串列表** `["漫画", "C108"]`
    - `ajax/illust/{id}`：`tags.tags` 是**对象列表** `[{"tag": "漫画"}]`

    两种都要能读，否则轮询一上来就 AttributeError。
    """
    if not raw or not isinstance(raw, list):
        return []
    tags: list[str] = []
    for t in raw:
        if isinstance(t, dict):
            name = (t.get("tag") or "").strip()
        else:
            name = str(t).strip()
        if name:
            tags.append(name)
    return tags


async def fetch_page_urls(pid: str, uid: Optional[str] = None) -> list[str]:
    """取某个作品每一页的 regular（约 1200px）图片地址

    模块级函数，`PixivIllust` 和 formatter 都用它。
    """
    url = PIXIV_ILLUST_PAGES.format(pid=pid)
    try:
        async with AsyncClient(timeout=20) as client:
            resp = await client.get(url, headers=pixiv_headers(uid))
            if resp.status_code != 200:
                nb_logger.warning(f"Pixiv 页列表 HTTP {resp.status_code} [pid={pid}]")
                return []
            data = resp.json()
    except Exception as e:
        nb_logger.warning(f"Pixiv 页列表请求失败 [pid={pid}]: {e!r}")
        return []

    if data.get("error"):
        return []

    urls: list[str] = []
    for page in data.get("body") or []:
        u = (page.get("urls") or {}).get("regular") or ""
        if u:
            urls.append(u)
    return urls


class PixivIllust(SourceBase):
    """Pixiv 画师的作品更新监测源"""

    def __init__(self, target_id: str, group_id: int):
        super().__init__(target_id, group_id)
        # 失败退避状态（跨轮次保留：同一个 source 实例由调度器复用）
        self._fail_streak = 0
        self._backoff_until = 0.0

    # ─── 退避 ────────────────────────────────────────────

    def _register_failure(self, reason: str, severe: bool) -> None:
        self._fail_streak += 1
        base = BACKOFF_BASE_SECONDS if severe else BACKOFF_MILD_SECONDS
        delay = min(base * (2 ** (self._fail_streak - 1)), BACKOFF_MAX_SECONDS)
        self._backoff_until = time_module.time() + delay
        level = "限流" if severe else "失败"
        nb_logger.warning(
            f"Pixiv {level}：{reason} [uid={self.target_id}]，"
            f"退避 {delay}s（连续第 {self._fail_streak} 次）"
        )

    def _reset_backoff(self) -> None:
        if self._fail_streak:
            nb_logger.info(
                f"Pixiv 恢复正常 [uid={self.target_id}]（此前连续失败 "
                f"{self._fail_streak} 次）"
            )
        self._fail_streak = 0
        self._backoff_until = 0.0

    @property
    def is_backing_off(self) -> bool:
        """当前是否处于退避期（供测试与诊断用）"""
        return time_module.time() < self._backoff_until

    @property
    def platform(self) -> str:
        return "pixiv"

    @property
    def source_type(self) -> str:
        return "illust"

    # ─── 主入口 ──────────────────────────────────────────

    async def fetch(self) -> list[Item]:
        """拉取该画师近期作品（只返回全年龄的）

        注意：这里**不**去查每一页的图片地址 —— 那会变成每个作品一次请求。
        页地址留到真正要推送时再取（见 `fetch_page_urls`），
        因为每轮轮询里真正的新作品通常只有 0～1 个。
        """
        url = PIXIV_PROFILE_TOP.format(uid=self.target_id)

        # 退避期内直接跳过，不再打 Pixiv
        now = time_module.time()
        if now < self._backoff_until:
            nb_logger.debug(
                f"Pixiv 退避中 [uid={self.target_id}]，还剩 "
                f"{int(self._backoff_until - now)}s"
            )
            return []

        try:
            async with AsyncClient(timeout=20) as client:
                resp = await client.get(url, headers=pixiv_headers(self.target_id))
                if resp.status_code != 200:
                    # 403/429 是被限流的明确信号，重罚；其他状态码按普通失败处理
                    self._register_failure(
                        f"作品列表 HTTP {resp.status_code}",
                        severe=resp.status_code in RATE_LIMIT_STATUS,
                    )
                    return []
                data = resp.json()
        except Exception as e:
            self._register_failure(f"作品列表请求失败 {e!r}", severe=False)
            return []

        if data.get("error"):
            self._register_failure(f"返回错误 {data.get('error')}", severe=False)
            return []

        self._reset_backoff()

        body = data.get("body") or {}
        works: dict[str, dict] = {}
        for kind in ("illusts", "manga"):
            works.update(body.get(kind) or {})

        items: list[Item] = []
        for work in works.values():
            item = self._parse_work(work)
            if item is not None:
                items.append(item)

        # 新的在前，方便上层按时间截断
        items.sort(key=lambda it: it.pub_ts, reverse=True)
        return items

    # ─── 解析 ────────────────────────────────────────────

    def _parse_work(self, work: dict) -> Optional[Item]:
        """把 profile/top 里的一条作品解析成 Item，R18/屏蔽的作品返回 None"""
        if not isinstance(work, dict):
            return None

        pid = str(work.get("id") or "").strip()
        if not pid:
            return None

        # 只信 xRestrict；sl 在正常作品上也会是 2 甚至 6，不能用来判 R18
        if work.get("xRestrict") != ALL_AGES_RESTRICT:
            nb_logger.debug(
                f"Pixiv 跳过 R18 作品 [{pid}] xRestrict={work.get('xRestrict')} "
                f"title={work.get('title', '')[:30]}"
            )
            return None

        # isMasked 表示作品被屏蔽，推出去也是灰图
        if work.get("isMasked"):
            nb_logger.debug(f"Pixiv 跳过被屏蔽作品 [{pid}]")
            return None

        title = (work.get("title") or "").strip() or f"作品 {pid}"
        nickname = (work.get("userName") or "").strip() or f"画师{self.target_id}"
        page_count = int(work.get("pageCount") or 1)

        return Item(
            id=pid,
            platform=self.platform,
            source_type=self.source_type,
            target_id=self.target_id,
            title=title,
            nickname=nickname,
            content=title,
            link=PIXIV_ARTWORK.format(pid=pid),
            cover_url=(work.get("urls") or {}).get("regular") or None,
            cover_urls=[],
            pub_ts=parse_pub_ts(work.get("createDate", "")),
            extra={
                "page_count": page_count,
                "illust_type": work.get("illustType"),
                "ai_type": work.get("aiType"),
                "tags": _parse_tags(work.get("tags")),
                "user_id": str(work.get("userId") or self.target_id),
            },
        )

    # ─── 页地址 ──────────────────────────────────────────

    async def fetch_page_urls(self, pid: str) -> list[str]:
        """取某个作品每一页的图片地址（转调模块级函数）"""
        return await fetch_page_urls(pid, self.target_id)

    # ─── 画师名 ──────────────────────────────────────────

    async def get_display_name(self) -> str:
        """从作品列表里取画师名，取不到就退回 uid"""
        url = PIXIV_PROFILE_TOP.format(uid=self.target_id)
        try:
            async with AsyncClient(timeout=15) as client:
                resp = await client.get(url, headers=pixiv_headers(self.target_id))
                if resp.status_code == 200:
                    body = resp.json().get("body") or {}
                    for kind in ("illusts", "manga"):
                        for work in (body.get(kind) or {}).values():
                            name = (work.get("userName") or "").strip()
                            if name:
                                return name
        except Exception:
            pass
        return self.target_id
