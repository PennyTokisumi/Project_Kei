"""LLM 费用估算 —— 按 DeepSeek 官方价目表**逐条**计价

## 官方价（2026-10 抓取，deepseek-flash，美元 / 百万 tokens）

| 项目 | 低谷 | 高峰 |
|---|---|---|
| 输入（缓存命中） | $0.003 | $0.006 |
| 输入（缓存未命中） | $0.15 | $0.30 |
| 输出 | $0.60 | $1.20 |

**高峰时段**：UTC 周一至周五 `01:00-04:00` 与 `06:00-10:00`
（北京时间 09:00-12:00、14:00-18:00，工作日）。
其余时间 —— 包括周末和节假日 —— 都是低谷，价格为高峰的一半。

## 为什么要逐条算

高峰/低谷差 2 倍，而 `@Kei LLM` 展示的是「今日/昨日/累计」聚合值。
拿聚合 token 数乘一个单价，无法区分每条记录落在哪个时段，
所以在 `pricing.row_cost()` 里按每条记录的 `created_at`（UTC）分别计价再求和。

## 已知误差（无法消除，别假装没有）

- **中国法定节假日判断不了**：官方把节假日算低谷，我们不知道哪天放假，
  会误按高峰计价 —— 最多高估 2 倍，但只影响节假日那几天的记录
- **汇率是固定值**：`config.llm_price_*` 里的元价按 7.1 折算，
  实际汇率波动会带来百分之几的偏差
- 官方明确保留调价权利，价格变了要手动改 `.env`
"""

from datetime import datetime, timezone

from config import config

# 高峰时段（UTC，左闭右开）
PEAK_WINDOWS_UTC = ((1, 4), (6, 10))

_TS_FORMAT = "%Y-%m-%d %H:%M:%S"


def is_peak(utc_dt: datetime) -> bool:
    """判断某个 UTC 时刻是否在高峰时段（周一至周五的 01-04、06-10 点）

    `utc_dt` 必须是 UTC（naive 也按 UTC 解释，因为 SQLite 的
    `CURRENT_TIMESTAMP` 存的就是 UTC）。
    """
    if utc_dt.tzinfo is not None:
        utc_dt = utc_dt.astimezone(timezone.utc).replace(tzinfo=None)
    # Python 的 weekday(): 周一=0 … 周日=6
    if utc_dt.weekday() >= 5:
        return False
    hour = utc_dt.hour
    for start, end in PEAK_WINDOWS_UTC:
        if start <= hour < end:
            return True
    return False


def parse_created_at(value) -> datetime | None:
    """把数据库里的 `created_at` 解析成 UTC datetime，失败返回 None"""
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is None else value.astimezone(timezone.utc).replace(tzinfo=None)
    try:
        return datetime.strptime(str(value), _TS_FORMAT)
    except (ValueError, TypeError):
        return None


def row_cost(
    prompt_tokens: int,
    completion_tokens: int,
    cache_hit_tokens: int = 0,
    cache_miss_tokens: int = 0,
    created_at=None,
) -> float:
    """算一条用量记录花掉的钱（元）

    - 有缓存字段：命中按命中价、未命中按未命中价
    - **旧数据没有缓存字段**（两列都是 0）：把全部 prompt 当未命中算 ——
      那些记录产生时还没有缓存统计，当时的真实情况就是全价
    - `created_at` 缺失时按**当前时刻**判断峰谷（宁可高估不要低估）
    """
    when = parse_created_at(created_at) or datetime.now(timezone.utc).replace(tzinfo=None)
    ratio = 1.0 if is_peak(when) else config.llm_price_offpeak_ratio

    hit = int(cache_hit_tokens or 0)
    miss = int(cache_miss_tokens or 0)
    if hit + miss <= 0:
        # 兼容迁移前的旧记录
        miss = int(prompt_tokens or 0)
        hit = 0

    return (
        hit * config.llm_price_cache_hit
        + miss * config.llm_price_prompt
        + int(completion_tokens or 0) * config.llm_price_completion
    ) * ratio / 1_000_000


def _field(row, key, default=0):
    """安全取字段：sqlite3.Row 缺列会抛 IndexError，dict 缺键会抛 KeyError"""
    try:
        value = row[key]
    except (IndexError, KeyError, TypeError):
        return default
    return default if value is None else value


def total_cost(rows) -> float:
    """把多条用量记录的钱加起来

    `rows` 里每一项要能按名字取到 `prompt_tokens` / `completion_tokens` /
    `cache_hit_tokens` / `cache_miss_tokens` / `created_at`
    （sqlite3.Row 或 dict 都行，缺字段按 0 处理）。
    """
    return sum(
        row_cost(
            _field(r, "prompt_tokens"),
            _field(r, "completion_tokens"),
            _field(r, "cache_hit_tokens"),
            _field(r, "cache_miss_tokens"),
            _field(r, "created_at", None),
        )
        for r in rows
    )


def format_cost(amount: float) -> str:
    """格式化金额：小额保留更多小数位，避免 ¥0.00 看不出差别"""
    if amount >= 1:
        return f"¥{amount:.2f}"
    if amount >= 0.01:
        return f"¥{amount:.3f}"
    return f"¥{amount:.4f}"
