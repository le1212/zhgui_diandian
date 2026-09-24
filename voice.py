"""点点的人格化文案池。

约束（陪伴感的纪律）：只在状态转换时说话；文案池小而精，宁缺毋滥；
深夜文案克制提醒休息，不渲染熬夜。全部为纯函数，便于审阅与测试。
"""

from __future__ import annotations


def startup_greeting(hour: int) -> str:
    if 5 <= hour < 11:
        return "早上好，点点就位"
    if 11 <= hour < 13:
        return "中午好，点点待命中"
    if 13 <= hour < 18:
        return "下午好，随时开点"
    if 18 <= hour < 23:
        return "晚上好，点点陪你"
    return "这么晚了，点点陪你，记得早点休息"


def welcome_back(days: int) -> str:
    return f"{days} 天不见，点点想你了"


def task_completed(duration_text: str) -> str:
    return f"辛苦啦，这一轮跑了 {duration_text}"


def milestone_line(kind: str, value: int) -> str:
    if kind == "clicks":
        return f"里程碑：点点已经陪你点了 {value:,} 次"
    if kind == "runs":
        return f"里程碑：点点陪你跑完了 {value} 次任务"
    if kind == "streak":
        return f"里程碑：连续第 {value} 天见面，点点很开心"
    return "里程碑达成了"


def care_break() -> str:
    return "已连续运行 30 分钟，记得让眼睛也歇会儿"


def night_watch(message: str, hour: int) -> str:
    """深夜（23 点后 / 5 点前）的定时任务播报加一句陪伴前缀。"""
    if hour >= 23 or hour < 5:
        return f"夜深了，点点替你盯着 — {message}"
    return message


def format_count(value: int) -> str:
    return f"{value:,}"


def format_duration(milliseconds: int) -> str:
    """把毫秒时长格式化为人类可读的短句：45 秒 / 2 分 14 秒 / 3 小时 12 分。"""
    seconds = max(0, milliseconds) // 1000
    if seconds < 60:
        return f"{seconds} 秒"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} 分 {seconds} 秒" if seconds else f"{minutes} 分钟"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} 小时 {minutes} 分" if minutes else f"{hours} 小时"
