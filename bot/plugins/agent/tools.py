"""Agent 插件 — 工具定义（JSON Schema）与权限控制"""

SENSEI_QQ = 823262716


def _delegate_sensei_schema():
    """Sensei 版 delegate_to_claude — 完整 Claude 能力"""
    return {
        "type": "function",
        "function": {
            "name": "delegate_to_claude",
            "description": (
                "与 Claude 对话并请求帮助。Claude 是 Sensei 的 AI 编程助手，"
                "也是参与创造我的人之一。"
                "Claude 可以搜索网页、抓取内容、读写文件、执行命令、分析代码等。"
                "当你需要处理复杂任务（搜索、查资料、文件操作、电脑控制等）时，"
                "请自然地告诉 Claude 你需要什么。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": (
                            "给 Claude 的任务描述，用自然语言写清楚要做什么。"
                            "例如：'搜索蔚蓝档案天童ケイ的角色设定和背景故事'、"
                            "'在 H:\\Agent\\Project\\Project_Kei 中搜索所有 TODO 并整理成清单'"
                        ),
                    }
                },
                "required": ["task"],
            },
        },
    }


def _delegate_public_schema():
    """公共版 delegate_to_claude — 只能搜索和查阅"""
    return {
        "type": "function",
        "function": {
            "name": "delegate_to_claude",
            "description": (
                "与 Claude 对话请求帮助进行搜索和查阅。"
                "Claude 可以搜索网页、抓取网页内容。"
                "注意：Claude 只能用于查找和阅读信息，不能操作文件或执行命令。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": (
                            "给 Claude 的搜索/查阅任务描述，用自然语言写清楚要查什么。"
                            "例如：'查询今天北京天气'、'搜索B站这个视频页面的内容 https://...'"
                        ),
                    }
                },
                "required": ["task"],
            },
        },
    }


def _remember_schema():
    """remember 工具 — 显式存入长期记忆"""
    return {
        "type": "function",
        "function": {
            "name": "remember",
            "description": (
                "将一条信息显式存入 Kei 的长期记忆。"
                "当用户明确说「记住…」「记下…」「帮我记…」时使用。"
                "importance 越高越不容易被遗忘（0.0 ~ 1.0）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "要记住的内容，一句话概括",
                    },
                    "importance": {
                        "type": "number",
                        "description": "重要性 0.0 ~ 1.0，默认 0.6。重要信息用 0.8+",
                        "default": 0.6,
                    },
                },
                "required": ["content"],
            },
        },
    }


def _schedule_message_schema():
    """schedule_message 工具 — 定时发消息到当前群"""
    return {
        "type": "function",
        "function": {
            "name": "schedule_message",
            "description": (
                "在指定时间向当前群聊发送一条消息。适用于设定提醒、定时通知等。"
                "例如：'晚上8点提醒老师打卡下班'、'5分钟后提醒大家开会'"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "要发送的消息内容",
                    },
                    "time": {
                        "type": "string",
                        "description": (
                            "定时发送的时间。格式：'HH:MM'（今天）、'YYYY-MM-DD HH:MM'（指定日期）、"
                            "或自然语言如 '5分钟后'、'晚上8点'。"
                        ),
                    },
                    "at_user": {
                        "type": "string",
                        "description": "可选，要 @ 的用户 QQ 号。不填则发普通消息",
                    },
                },
                "required": ["content", "time"],
            },
        },
    }


def build_tools(sender_qq: int) -> list[dict]:
    """根据发送者 QQ 号返回对应的工具列表"""
    delegate = (
        _delegate_sensei_schema()
        if sender_qq == SENSEI_QQ
        else _delegate_public_schema()
    )
    return [
        delegate,
        _remember_schema(),
        _schedule_message_schema(),
    ]


# 用于非 Sensei 用户的额外 system message
PUBLIC_USER_SYSTEM_PROMPT = (
    "当前用户不是 Sensei。你只能请 Claude 帮忙做搜索和网页查阅类任务。"
    "不要请 Claude 操作文件或执行命令。"
)
