"""DeepSeek 余额播报 — 轮询官方余额接口，发现「充值 / 赠送 / 低余额」事件并播报

官方只有一个读接口 ``GET /user/balance``，**没有** webhook / 订阅 / 事件流，
所以「充值到账」「赠送额度」只能靠**轮询对比前后两次快照**发现。
（消耗不需要轮询：每次调用 LLM 的 usage 里就有精确 token 数，已记在 llm_token_usage。）

本模块只负责「数据 + 编排」：
- 不含 APScheduler 注册（交给上层按 ``needs_fast_poll()`` 决定间隔）；
- 不生成 Kei 的点评文本（由上层通过 ``comment_fn`` 注入），
  因此纯逻辑部分可以完全脱网单测。
"""

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Awaitable, Callable, Optional, Union

import httpx
from nonebot import logger as nb_logger
from nonebot.adapters.onebot.v11 import Message, MessageSegment

from config import SENSEI_QQ, config

from .database import (
    cleanup_balance_snapshots,
    get_last_balance_snapshot,
    save_balance_snapshot,
)

# 官方余额接口。Gemini 供应商没有这个接口，调用前会先短路。
DEFAULT_BALANCE_URL = "https://api.deepseek.com/user/balance"
BALANCE_API_PATH = "/user/balance"
# 官方接口响应很快，20 秒足够；卡住时宁可这轮放弃，也别拖住调度器
BALANCE_TIMEOUT = 20.0

KIND_TOPUP = "topup"
KIND_GRANT = "grant"
KIND_LOW = "low"

# 增量判定阈值：金额只有两位小数，1 分钱以内的差异只可能是浮点噪声
CHANGE_EPSILON = 0.01

# 多条播报之间的间隔（秒），避免 QQ 端看起来像刷屏
ALERT_GAP_SECONDS = 1.0
# Kei 旁白分条发送时的间隔（秒），比固定播报稍长，像在打字
COMMENT_GAP_SECONDS = 1.5

# 历史快照保留天数（只为「跨重启对比增量」和排查问题，不需要永久保留）
KEEP_DAYS = 90

# comment_fn(kind, amount, balance) -> 非空文本时单独发一条
# 返回单条字符串或多条列表都行；列表用于「她自己也分成两三句说」的场合
CommentFn = Callable[[str, float, float], Awaitable[Optional[Union[str, list[str]]]]]


@dataclass(frozen=True)
class BalanceSnapshot:
    """某一时刻的余额快照（金额均为元，两位小数）"""

    currency: str
    total: float
    granted: float
    topped_up: float
    is_available: bool
    created_at: str | None = None


# ─── 解析 / 抓取 ────────────────────────────────────────────────


def parse_balance_payload(data: dict, currency: str = "CNY") -> BalanceSnapshot | None:
    """解析官方余额响应（纯函数）

    接口里的金额是**字符串**（如 ``"33.23"``），这里统一转 float 并保留两位小数。
    找不到指定币种、或结构不符合预期时返回 None，**不抛异常** ——
    调用方只需要区分「拿到了」和「没拿到」。

    响应示例::

        {"is_available": true,
         "balance_infos": [{"currency": "CNY", "total_balance": "33.23",
                            "granted_balance": "0.00",
                            "topped_up_balance": "33.23"}]}
    """
    if not isinstance(data, dict):
        return None

    infos = data.get("balance_infos")
    if not isinstance(infos, list):
        return None

    for info in infos:
        if not isinstance(info, dict) or info.get("currency") != currency:
            continue
        try:
            total = round(float(info["total_balance"]), 2)
            granted = round(float(info["granted_balance"]), 2)
            topped_up = round(float(info["topped_up_balance"]), 2)
        except (KeyError, TypeError, ValueError):
            # 结构缺失 / 金额不是数字（比如空串）都算解析失败
            return None

        return BalanceSnapshot(
            currency=currency,
            total=total,
            granted=granted,
            topped_up=topped_up,
            is_available=bool(data.get("is_available", False)),
        )

    # 响应里没有这个币种
    return None


def _balance_url() -> str:
    """余额接口地址

    跟随 ``config.llm_base_url``（自建代理 / 兼容网关也能用），
    拿不到时退回官方地址。
    """
    base = (config.llm_base_url or "").strip().rstrip("/")
    return f"{base}{BALANCE_API_PATH}" if base else DEFAULT_BALANCE_URL


async def fetch_balance(currency: str = "CNY") -> BalanceSnapshot | None:
    """GET /user/balance

    无 key / Gemini / HTTP 错误 / 解析失败，一律返回 None，**不抛异常**。
    超时 20 秒。
    """
    if config.is_gemini:
        # Gemini 没有余额接口，别拿 Gemini 的 key 去打 DeepSeek
        nb_logger.debug("当前 LLM 供应商为 Gemini，无余额接口，跳过余额查询")
        return None

    api_key = (config.llm_api_key or "").strip()
    if not api_key:
        nb_logger.debug("未配置 DeepSeek API Key，跳过余额查询")
        return None

    url = _balance_url()
    try:
        async with httpx.AsyncClient(timeout=BALANCE_TIMEOUT) as client:
            resp = await client.get(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Accept": "application/json",
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as e:
        nb_logger.warning(
            f"余额查询失败: HTTP {e.response.status_code}（{url}）"
        )
        return None
    except Exception as e:
        # 超时 / 连接失败 / JSON 解不开，全部归为「这轮没拿到」
        nb_logger.warning(f"余额查询失败: {type(e).__name__}: {e}")
        return None

    snapshot = parse_balance_payload(data, currency)
    if snapshot is None:
        nb_logger.warning(f"余额响应解析失败: {str(data)[:200]}")
        return None

    return replace(snapshot, created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


# ─── 事件判定 ──────────────────────────────────────────────────


def detect_events(prev: BalanceSnapshot | None, curr: BalanceSnapshot,
                  low_threshold: float, low_armed: bool) -> list[tuple[str, float]]:
    """对比前后两次快照，返回 [(kind, amount)]（纯函数，可能同时有多个事件）

    规则：
    - ``prev is None``（首次启动建基线）→ **不报** TOPUP/GRANT；
      但若 ``curr.total < low_threshold`` 且 ``low_armed`` 为真，照样报 LOW
    - 充值增量 ``> 0.01`` → (TOPUP, 增量)
    - 赠送增量 ``> 0.01`` → (GRANT, 增量)
    - ``curr.is_available`` 为 False → (LOW, 当前余额)
    - ``curr.total < low_threshold`` 且 ``low_armed`` 且前面没报过 LOW → (LOW, 当前余额)
    - 顺序固定：TOPUP → GRANT → LOW
    """
    events: list[tuple[str, float]] = []

    # 有上一次快照才谈得上「增量」；首次建基线不补报历史充值
    if prev is not None:
        topup_diff = round(curr.topped_up - prev.topped_up, 2)
        if topup_diff > CHANGE_EPSILON:
            events.append((KIND_TOPUP, topup_diff))

        grant_diff = round(curr.granted - prev.granted, 2)
        if grant_diff > CHANGE_EPSILON:
            events.append((KIND_GRANT, grant_diff))

    low_reported = False

    # 账户不可用（余额被冻结 / 欠费）与阈值无关，必须提醒
    if not curr.is_available:
        events.append((KIND_LOW, curr.total))
        low_reported = True

    # 跌破阈值：low_armed 保证一波低余额只提醒一次，
    # 回到阈值以上后由调用方重新武装
    if not low_reported and low_armed and curr.total < low_threshold:
        events.append((KIND_LOW, curr.total))

    return events


def format_alert_text(kind: str, amount: float, balance: float) -> str:
    """固定播报文本（**不含 @**，上层自己加）

    LOW 的阈值取 ``config.balance_low_threshold``，当前余额取参数 ``balance``。
    金额统一两位小数。

    ⚠️ 充值写的是「**充值额度增加**」而不是「已到账」。
    官方接口只返回当前余额，不提供交易流水，所以两次轮询之间如果发生了消费，
    ``topped_up`` 的净增量会比实际充值额略小（实测充 ¥10 显示 ¥9.99，
    差的 1 分就是轮询间隔内消耗掉的）。这不是浮点误差，也**无法从接口还原**。
    """
    if kind == KIND_TOPUP:
        return f"DeepSeek 账户充值额度增加 ¥{amount:.2f}，当前余额 ¥{balance:.2f}。"
    if kind == KIND_GRANT:
        return f"DeepSeek 账户收到赠送额度 ¥{amount:.2f}，当前余额 ¥{balance:.2f}。"
    if kind == KIND_LOW:
        return (
            f"DeepSeek 账户余额不足 ¥{config.balance_low_threshold:.2f}，"
            f"当前仅剩 ¥{balance:.2f}，请及时充值。"
        )
    raise ValueError(f"未知的余额事件类型: {kind!r}")


# ─── 模块级内存状态 ──────────────────────────────────────────────
#
# 只影响「轮询节奏」与「这波低余额是否已经提醒过」，
# 重启后可由 DB 里的最近一条快照重建，所以不需要持久化。

_state_loaded = False    # 是否已经根据 DB 快照初始化过
_low_armed = True        # 这一波下探还能不能报 LOW（报过就解除，回到阈值上重新武装）
_low_alerted = False     # 当前这波低余额是否已经播报过
_last_total: Optional[float] = None  # 最近一次成功取到的余额


def _db_prev_snapshot(currency: str = "CNY") -> Optional[BalanceSnapshot]:
    """从 DB 取上次快照并转成 BalanceSnapshot

    表还没建 / 读取失败都当作「没有上次快照」处理，不要因此中断轮询。
    """
    try:
        row = get_last_balance_snapshot(currency)
    except Exception as e:
        nb_logger.warning(f"读取余额快照失败: {type(e).__name__}: {e}")
        return None

    if not row:
        return None

    try:
        return BalanceSnapshot(
            currency=row["currency"],
            total=float(row["total_balance"]),
            granted=float(row["granted_balance"]),
            topped_up=float(row["topped_up_balance"]),
            is_available=bool(row["is_available"]),
            created_at=row.get("created_at"),
        )
    except (KeyError, TypeError, ValueError) as e:
        nb_logger.warning(f"余额快照记录异常: {e}")
        return None


def _init_state_from_db(prev: BalanceSnapshot | None = None) -> None:
    """按上次快照推导 low_armed 初值（只在第一次调用时生效）

    上次余额就已经低于阈值 ⇒ 说明这波低余额在重启前已经提醒过，
    视为「已提醒 + 已解除武装」，避免每次重启都重复播报一遍。
    """
    global _state_loaded, _low_armed, _low_alerted, _last_total

    if _state_loaded:
        return
    _state_loaded = True

    if prev is None:
        # 库里没有历史快照 → 首次运行，正常武装
        _low_armed = True
        _low_alerted = False
        return

    _last_total = prev.total
    is_low = prev.total < config.balance_low_threshold
    _low_armed = not is_low
    _low_alerted = is_low


def _apply_snapshot_to_state(snapshot: BalanceSnapshot) -> None:
    """把本轮快照写进内存状态

    回到阈值以上就重新武装 —— 下一次再跌破还要能提醒一次。
    """
    global _last_total, _low_armed, _low_alerted

    _last_total = snapshot.total
    if snapshot.total >= config.balance_low_threshold:
        _low_armed = True
        _low_alerted = False


def needs_fast_poll() -> bool:
    """是否处于「等待充值」状态：最近一次快照已低于阈值，且低余额提醒已经发过

    上层据此把轮询间隔从 ``balance_poll_interval`` 压到
    ``balance_wait_poll_interval``（默认 1800s → 60s），
    这样充值到账后一分钟内就能播报。
    """
    if not _state_loaded:
        # 上层可能在第一次 poll 之前就问间隔；此时先按 DB 里的上次快照初始化，
        # 否则重启后会白等一个完整的 30 分钟周期才发现还处于等待充值状态。
        _init_state_from_db(_db_prev_snapshot())

    if _last_total is None:
        return False
    return bool(_low_alerted and _last_total < config.balance_low_threshold)


def reset_state_for_tests() -> None:
    """清空模块级状态（供测试使用，运行时不要调用）"""
    global _state_loaded, _low_armed, _low_alerted, _last_total

    _state_loaded = False
    _low_armed = True
    _low_alerted = False
    _last_total = None


# ─── 播报编排 ──────────────────────────────────────────────────


def _build_alert_message(text: str) -> Message:
    """@Sensei + 固定播报文本（@ 只在这里加，format_alert_text 不含）

    @ 后面用**换行**而不是空格 —— 「@某人 正文」挤在一行像系统通知，
    换行后正文独占一行，读起来干净。
    """
    return Message([
        MessageSegment.at(SENSEI_QQ),
        MessageSegment.text("\n" + text),
    ])


async def _maybe_send_comment(bot, comment_fn: Optional[CommentFn], kind: str,
                              amount: float, balance: float) -> None:
    """让 Kei 跟一句点评（单独一条、不带 @）

    ``comment_fn`` 可以返回**字符串或字符串列表** —— 她可以像正常聊天那样
    把话分成两三条发出去（「诶？多了钱……」「是老师充的吗……」），
    这里逐条发送并留间隔，看起来像真的在打字。

    失败只记日志：点评是锦上添花，不能影响固定播报。
    """
    if comment_fn is None or not config.balance_kei_comment:
        return

    try:
        comment = await comment_fn(kind, amount, balance)
    except Exception as e:
        nb_logger.warning(f"余额点评生成失败 [{kind}]: {type(e).__name__}: {e}")
        return

    # 兼容两种返回：单条字符串 / 多条列表
    if not comment:
        return
    if isinstance(comment, str):
        segments = [comment]
    else:
        segments = [str(s) for s in comment if s]
    if not segments:
        return

    for index, segment in enumerate(segments):
        try:
            await bot.send_group_msg(
                group_id=config.balance_alert_group_id,
                message=Message([MessageSegment.text(segment)]),
            )
        except Exception as e:
            nb_logger.error(f"余额点评发送失败 [{kind}]: {e}")
            return
        # 多条之间留一点间隔，模拟打字节奏
        if index < len(segments) - 1:
            await asyncio.sleep(COMMENT_GAP_SECONDS)


async def poll_balance(bot, *, comment_fn: Optional[CommentFn] = None) -> None:
    """一次完整的余额轮询（被 APScheduler 每 N 秒调用一次）

    流程：
    1. 开关关闭 → 直接返回
    2. 拉取余额，失败只记 warning（**绝不播报**，宁可没消息也不能报假消息）
    3. 取 DB 里的上次快照，推导 low_armed
    4. 检测事件
    5. 逐条播报（@Sensei + 固定文本；可选 Kei 点评单独一条）
    6. 落库本轮快照 + 清理 90 天前的历史

    整个函数体包在 try/except 里 —— 定时任务里抛异常会被 APScheduler
    记为失败，且可能影响后续轮次，所以异常只记日志、绝不外抛。
    """
    global _low_armed, _low_alerted

    try:
        if not config.balance_alert_enabled:
            return

        curr = await fetch_balance()
        if curr is None:
            nb_logger.warning("DeepSeek 余额获取失败，本轮跳过播报")
            return

        prev = _db_prev_snapshot(curr.currency)
        _init_state_from_db(prev)

        events = detect_events(
            prev, curr, config.balance_low_threshold, _low_armed
        )

        for index, (kind, amount) in enumerate(events):
            text = format_alert_text(kind, amount, curr.total)
            try:
                await bot.send_group_msg(
                    group_id=config.balance_alert_group_id,
                    message=_build_alert_message(text),
                )
                nb_logger.info(f"余额播报 [{kind}] 金额 {amount:.2f} 余额 {curr.total:.2f}")
            except Exception as e:
                nb_logger.error(f"余额播报发送失败 [{kind}]: {e}")

            if kind == KIND_LOW:
                # 这一波低余额已提醒过 → 解除武装，等回到阈值以上再重新武装
                _low_alerted = True
                _low_armed = False

            await _maybe_send_comment(bot, comment_fn, kind, amount, curr.total)

            # 多条事件之间留 1 秒间隔，避免 QQ 端像刷屏
            if index < len(events) - 1:
                await asyncio.sleep(ALERT_GAP_SECONDS)

        _apply_snapshot_to_state(curr)

        save_balance_snapshot(
            curr.currency, curr.total, curr.granted,
            curr.topped_up, curr.is_available,
        )
        cleaned = cleanup_balance_snapshots(keep_days=KEEP_DAYS)
        if cleaned:
            nb_logger.info(f"清理了 {cleaned} 条过期余额快照")

    except Exception as e:
        # 最高层兜底：余额播报失败不能影响调度器
        nb_logger.error(f"余额轮询异常: {type(e).__name__}: {e}")
