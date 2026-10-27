"""Kei 余额变化的「自己跟一句」

余额播报（充值 / 收到赠送额度 / 余额不足）会先在群里发一条固定的系统文本。
如果该群已开启 KEI ON，本模块再让 Kei 自己补一句话 —— 像是她真的瞥见了自己的
账户变化，而不是被要求表演一段「感谢」。

**设计原则：只给「事实」和「视角」，不给情绪或动作指令。**
system 里只写她观察到的事实（金额、余额、阈值），最后用一句舞台指示（她的动作）
让她接着往下说；要不要道谢、要不要嘴硬，交给 ``persona.py`` 里的人格自然产生。
反面例子（永远不要写进提示词）：「老师刚给你充了钱，感谢他，但按你的性格会嘴硬」
「幽默地感谢 DeepSeek 和梁文锋」—— 这类指令会把人格变成表演。

消息顺序固定（见 :func:`build_messages`）：
``[0]`` persona（必须第一条，才能命中 DeepSeek 的前缀缓存）
→ ``[1]`` 环境块（群号 + 当前时间）
→ ``[2]`` 事实块（【你刚注意到的变化】+ 中性事实）
→ ``[3]`` 纯格式约束
→ ``[4]`` user 舞台指示。

这里生成的话**不写进短期记忆**：它是对账户变化的旁白，不是群聊对话的一部分。
"""

import asyncio
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from nonebot import logger as nb_logger

from config import config
from ..monitor.database import get_setting
from .client import llm_client
from .persona import PERSONA_PROMPT

# ─── 常量 ────────────────────────────────────────────────

# 三类事件（kind）的舞台指示：只有「她的动作」，不写情绪、不写台词
_STAGE_DIRECTIONS = {
    "topup": "（你合上账户页面，抬起头。）",
    "grant": "（你盯着那笔额度看了一会儿。）",
    "low": "（你盯着账户余额，沉默了一下。）",
}

# 事实块前缀：把事件写成「她自己注意到的变化」，而不是「通知/请求」
_FACT_PREFIX = "【你刚注意到的变化】"

# 格式约束：三个场景通用。只约束形状，不干涉内容。
# 允许 1~3 句、允许像真人打字那样分成两三条发 —— 和聊天通道的分段逻辑是同一套
# （persona 里本来就有「要不要分段发」一节，这里只是不禁止它）。
_FORMAT_RULE = (
    "这是你在群里自己说的话，不是回复谁。可以说一到三句短话，也可以分成两三条发出去"
    "（用 [SEP] 隔开，最多 3 条）。不要 @ 任何人，不要解释你在做什么。"
)

# 调用的硬性上限，与话题描述一致
_LLM_TIMEOUT = 15.0
# 允许两三句，token 上限相应放宽
_MAX_TOKENS = 200
_TEMPERATURE = 0.7

# 代码侧兜底：模型偶尔会无视约束，单段超过这个长度就截断
_MAX_CHARS = 80
# 最多发几条（与聊天通道一致）
_MAX_SEGMENTS = 3

# 模型判断「此刻不该说话」时返回的标记
_PASS_TOKEN = "[PASS]"

# 开头的 @ 前缀。先按「@昵称 + 空白」整体去掉，再去掉落单的 @ 字符。
# 注意不能写成 ``^[@＠]\S*`` —— 那样遇到「@老师我看到了」会整句吃光。
_AT_MENTION_RE = re.compile(r"^[@＠][^\s@＠]{1,24}\s+")

# 成对的引号：模型很爱把台词包起来，成对出现才剥
_QUOTE_PAIRS = (
    ("“", "”"),
    ("「", "」"),
    ("『", "』"),
    ("‘", "’"),
    ('"', '"'),
    ("'", "'"),
)

_FACT_KINDS = ("topup", "grant", "low")


# ─── 纯函数区（脱网可单测）───────────────────────────────


def _now_beijing() -> datetime:
    """当前北京时间（与 memory.build_context 的时间写法保持一致）"""
    return datetime.now(timezone(timedelta(hours=8)))


def _build_fact(kind: str, amount: float, balance: float, threshold: float) -> str:
    """生成事实块正文：只有数字和陈述，没有任何情绪或动作指令"""
    if kind == "topup":
        # 充值：用「总额 - 本次充值」还原充值前的余额
        prev = round(float(balance) - float(amount), 2)
        return (
            f"你看了眼自己的 DeepSeek 账户：充值额度多了 ¥{amount:.2f}。"
            f"总余额从 ¥{prev:.2f} 变成 ¥{balance:.2f}。"
        )
    if kind == "grant":
        # 「梁文锋」只作为**事实**写进来，不写「要感谢他」——
        # 实测这样她有约 2/3 的概率自然提到他（「梁文锋先生倒是挺大方」），
        # 剩下 1/3 不提，反而更像真人。若写成指令，每句都会硬塞这个名字。
        return (
            f"你看了眼自己的 DeepSeek 账户：账户里多了一笔 ¥{amount:.2f} 的赠送额度，"
            f"是 DeepSeek 官方发的（DeepSeek 的创始人是梁文锋）。"
            f"总余额现在是 ¥{balance:.2f}。"
        )
    if kind == "low":
        return (
            f"你看了眼自己的 DeepSeek 账户：总余额只剩 ¥{balance:.2f}，"
            f"已经低于 ¥{threshold:.2f} 了。"
        )
    raise ValueError(f"未知的余额事件类型：{kind!r}")


def build_messages(kind: str, amount: float, balance: float,
                   group_id: int, now: Optional[datetime] = None,
                   threshold: Optional[float] = None) -> list[dict]:
    """组装一次「余额旁白」的完整 messages（纯函数，方便脱网单测）

    顺序固定，**不要重排**：persona 在最前，变化内容在后，理由与前缀缓存一致
    （见 ``memory.build_context`` 的顺序约定）。

    :param kind: ``topup`` / ``grant`` / ``low``，其它值抛 ``ValueError``
    :param amount: 本次变化的金额（元）；``low`` 场景用不到，可为 0
    :param balance: 变化后的总余额（元）
    :param group_id: 群号，只用于环境块
    :param now: 当前时间，默认取北京时间；显式传入便于测试
    :param threshold: 低余额阈值，默认取 ``config.balance_low_threshold``
    """
    if kind not in _FACT_KINDS:
        raise ValueError(f"未知的余额事件类型：{kind!r}")

    if now is None:
        now = _now_beijing()
    if threshold is None:
        threshold = config.balance_low_threshold

    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    fact = _build_fact(kind, amount, balance, threshold)

    return [
        # [0] 人设：固定内容放第一条，前缀缓存才有得命中
        {"role": "system", "content": PERSONA_PROMPT},
        # [1] 环境块：群号 + 当前时间（每轮都变，所以排在固定内容之后）
        {
            "role": "system",
            "content": f"你当前正在 QQ 群 {group_id} 中。现在的时间是 {now_str}（北京时间）。",
        },
        # [2] 事实块：她自己的观察
        {"role": "system", "content": f"{_FACT_PREFIX}\n{fact}"},
        # [3] 纯格式约束
        {"role": "system", "content": _FORMAT_RULE},
        # [4] 舞台指示：她的动作，而不是给她的命令
        {"role": "user", "content": _STAGE_DIRECTIONS[kind]},
    ]


def _strip_at_prefix(text: str) -> str:
    """去掉开头可能出现的 @ 前缀（模型有时会模仿群聊格式去 @ 人）"""
    while True:
        match = _AT_MENTION_RE.match(text)
        if not match:
            break
        text = text[match.end():]
    # 没有空格的「@老师到账了」：只去掉那个 @ 字符，不吞掉正文
    if text[:1] in ("@", "＠"):
        text = text[1:].lstrip()
    return text


def _strip_wrapping_quotes(text: str) -> str:
    """剥掉成对包裹的引号（可能套了不止一层）"""
    changed = True
    while changed and len(text) >= 2:
        changed = False
        for left, right in _QUOTE_PAIRS:
            if text.startswith(left) and text.endswith(right):
                text = text[len(left):-len(right)].strip()
                changed = True
                break
    return text


def _clean_reply(text: str) -> str:
    """清洗模型输出：去空白 / @ 前缀 / 包裹引号，识别 [PASS]，超长截断

    返回空串表示「这句话不该发出去」。
    """
    if not text:
        return ""
    out = str(text).strip()

    # 引号与 @ 前缀可能互相包裹，来回处理两轮足够
    for _ in range(2):
        out = _strip_wrapping_quotes(out).strip()
        out = _strip_at_prefix(out).strip()

    if not out or out.upper() == _PASS_TOKEN:
        return ""

    if len(out) > _MAX_CHARS:
        nb_logger.debug(f"Kei 余额评论过长（{len(out)} 字），截断到 {_MAX_CHARS} 字")
        out = out[:_MAX_CHARS].strip()

    return out


def _split_and_clean(text: str) -> list[str]:
    """把模型输出拆成 1~3 条，并逐条清洗

    直接复用聊天通道的 ``_split_reply`` —— 它已经处理了三种分隔：
    ``[SEP]``、空行、单换行（超过 3 行时不拆，避免把排版当分段），并封顶 3 段。
    这样余额旁白的分段行为和正常聊天完全一致，用户不会觉得两套逻辑。
    """
    if not text:
        return []
    try:
        from . import _split_reply  # 同包内的聊天分段函数，懒导入避免循环
        raw = _split_reply(str(text))
    except Exception:
        raw = [str(text)]

    segments = []
    for seg in raw:
        cleaned = _clean_reply(seg)
        if cleaned:
            segments.append(cleaned)
    return segments[:_MAX_SEGMENTS]


def _llm_enabled(group_id: int) -> bool:
    """该群是否已开启 LLM（沿用项目里 ``llm_enabled_{group_id}`` 这一套设置）"""
    return get_setting(f"llm_enabled_{group_id}", "0") == "1"


# ─── 对外接口 ────────────────────────────────────────────


async def kei_balance_comment(kind: str, amount: float, balance: float,
                              group_id: Optional[int] = None) -> list[str]:
    """生成 Kei 的旁白，返回 **1~3 条**待发送的消息；不该说话时返回空列表 ``[]``。

    之所以返回列表而不是单个字符串：她可以像正常聊天那样把话分成两三条发出去
    （「诶？多了钱……[SEP]是老师充的吗……谢谢」），播报器会逐条发送、中间留间隔。

    ``group_id`` 缺省时取 ``config.balance_alert_group_id`` —— 播报器
    （``monitor/balance.py`` 的 ``comment_fn``）只按 ``(kind, amount, balance)``
    三个参数调用，而播报本来就固定发到那一个群，用同一个群判断 KEI ON 才对。

    返回空列表的情况：
    - ``config.balance_kei_comment`` 为假
    - 该群未开启 LLM（``llm_enabled`` 为假）
    - LLM 未配置 / 调用失败 / 超时（15 秒）
    - 模型返回空、``[PASS]``、或只有空白
    - ``kind`` 不是 topup / grant / low 之一

    每条都已清洗：去首尾空白、去掉可能出现的 @ 前缀、去掉包裹的引号，
    单段超过 80 字则截断（并打一条 debug 日志）。
    """
    try:
        if group_id is None:
            group_id = config.balance_alert_group_id

        if not config.balance_kei_comment:
            nb_logger.debug("Kei 余额评论：配置已关闭，跳过")
            return []

        if kind not in _FACT_KINDS:
            nb_logger.debug(f"Kei 余额评论：未知事件类型 {kind!r}，跳过")
            return []

        if not _llm_enabled(group_id):
            nb_logger.debug(f"Kei 余额评论：群 {group_id} 未开启 LLM，跳过")
            return []

        messages = build_messages(kind, amount, balance, group_id)

        try:
            result = await asyncio.wait_for(
                llm_client.chat(
                    messages=messages,
                    max_tokens=_MAX_TOKENS,
                    temperature=_TEMPERATURE,
                    enable_thinking=False,
                ),
                timeout=_LLM_TIMEOUT,
            )
        except asyncio.TimeoutError:
            nb_logger.warning(f"Kei 余额评论生成超时（{_LLM_TIMEOUT:.0f}s），跳过")
            return []

        if not isinstance(result, dict):
            nb_logger.warning(f"Kei 余额评论：LLM 返回格式异常（{type(result)!r}），跳过")
            return []

        raw_text = result.get("content") or ""
        segments = _split_and_clean(raw_text)
        if not segments:
            # 供应商未配置 / 请求失败时 client 会带回 error，把原因记进日志
            error = result.get("error")
            if error:
                nb_logger.warning(f"Kei 余额评论生成失败：{error}")
            else:
                nb_logger.debug("Kei 余额评论：模型没有给出可用内容（空 / [PASS]），跳过")
            return []

        nb_logger.info(f"Kei 余额评论（{kind}，{len(segments)} 段）：{segments}")
        return segments
    except Exception as e:
        # 兜底：这句话只是锦上添花，绝对不能影响播报主流程
        nb_logger.warning(f"Kei 余额评论生成异常（已忽略）：{e}")
        return []
