"""使用统计与里程碑：陪伴感的数据地基。

零 Tk 依赖，可独立测试。持久化沿用 task_repository 的原子写约定
（临时文件 + fsync + 校验 + 替换）；统计文件损坏时静默重来——
丢了统计不值得像任务数据那样走备份恢复。

省时估算的假设：同样的点击由人工完成，平均每次需要定位 + 点击
约 MANUAL_CLICK_MS；点点按任务设定间隔执行，省时 = 次数 ×
max(0, 人工耗时 − 任务间隔)。该数值是鼓励性的近似，不是精确账目。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

MANUAL_CLICK_MS = 2000

# 里程碑档位：触发一次后记入 milestones，不再重复
CLICK_TIERS = (1_000, 10_000, 100_000, 1_000_000)
STREAK_TIERS = (3, 7, 30)
RUN_TIERS = (10, 100)
STATS_SCHEMA_VERSION = 1


@dataclass
class UsageStats:
    total_clicks: int = 0
    total_runs: int = 0
    total_run_ms: int = 0
    saved_ms: int = 0
    today: str = ""            # YYYY-MM-DD；跨天时清零当日计数
    today_clicks: int = 0
    day_streak: int = 0
    last_seen: str = ""        # 最近一次启动日期，用于连击与归来判断
    milestones: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "UsageStats":
        """读取统计文件；任何畸形数据（缺键/错型/损坏）按默认值处理，绝不阻断启动。"""
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            data = payload.get("stats", payload) if isinstance(payload, dict) else {}
            if not isinstance(data, dict):
                return cls()
            milestones = data.get("milestones", [])
            if not isinstance(milestones, list):
                milestones = []
            stats = cls(
                total_clicks=_as_int(data.get("total_clicks")),
                total_runs=_as_int(data.get("total_runs")),
                total_run_ms=_as_int(data.get("total_run_ms")),
                saved_ms=_as_int(data.get("saved_ms")),
                today=_as_text(data.get("today")),
                today_clicks=_as_int(data.get("today_clicks")),
                day_streak=_as_int(data.get("day_streak")),
                last_seen=_as_text(data.get("last_seen")),
                milestones=[item for item in milestones if isinstance(item, str)],
            )
        except (OSError, ValueError, TypeError):
            return cls()
        return stats

    def save(self, path: Path) -> bool:
        payload = {"schemaVersion": STATS_SCHEMA_VERSION, "stats": self.to_dict()}
        temporary = path.with_suffix(path.suffix + ".tmp")
        try:
            serialized = json.dumps(payload, ensure_ascii=False, indent=2)
            with temporary.open("w", encoding="utf-8", newline="\n") as stream:
                stream.write(serialized)
                stream.flush()
                os.fsync(stream.fileno())
            json.loads(temporary.read_text(encoding="utf-8"))
            os.replace(temporary, path)
            return True
        except OSError:
            return False
        finally:
            # replace 失败时清理残留的临时文件
            if temporary.exists():
                try:
                    temporary.unlink()
                except OSError:
                    pass

    def to_dict(self) -> dict:
        return {
            "total_clicks": self.total_clicks,
            "total_runs": self.total_runs,
            "total_run_ms": self.total_run_ms,
            "saved_ms": self.saved_ms,
            "today": self.today,
            "today_clicks": self.today_clicks,
            "day_streak": self.day_streak,
            "last_seen": self.last_seen,
            "milestones": list(self.milestones),
        }

    def note_session_start(self, today: str, yesterday: str) -> tuple[list[str], int]:
        """启动时结算连击天数与归来间隔；返回（本次触发的里程碑, 距上次使用的天数）。

        同一天重复启动不重复结算；隔天续上连击，断档归一。
        """
        if self.today != today:
            self.today = today
            self.today_clicks = 0
        absent_days = 0
        fired: list[str] = []
        if self.last_seen != today:
            absent_days = _days_between(self.last_seen, today) if self.last_seen else 0
            self.day_streak = self.day_streak + 1 if self.last_seen == yesterday else 1
            self.last_seen = today
            fired = self._check_streak_milestones()
        return fired, absent_days

    def note_run(self, clicks: int, run_ms: int, interval_ms: int, today: str) -> list[str]:
        """一次运行结束后累计；返回本次新触发的里程碑。

        跨天结束的运行（过了午夜）把当日计数切到新一天，避免丢账。
        """
        if self.today != today:
            self.today = today
            self.today_clicks = 0
        self.total_clicks += clicks
        self.today_clicks += clicks
        self.total_runs += 1
        self.total_run_ms += run_ms
        self.saved_ms += clicks * max(0, MANUAL_CLICK_MS - max(0, interval_ms))
        fired = self._check_click_milestones()
        fired += self._check_run_milestones()
        return fired

    def _check_click_milestones(self) -> list[str]:
        return self._fire(f"clicks:{tier}" for tier in CLICK_TIERS if self.total_clicks >= tier)

    def _check_run_milestones(self) -> list[str]:
        return self._fire(f"runs:{tier}" for tier in RUN_TIERS if self.total_runs >= tier)

    def _check_streak_milestones(self) -> list[str]:
        return self._fire(f"streak:{tier}" for tier in STREAK_TIERS if self.day_streak >= tier)

    def _fire(self, candidates) -> list[str]:
        fired = [item for item in candidates if item not in self.milestones]
        self.milestones.extend(fired)
        return fired


def _as_int(value) -> int:
    """统计数值字段只接受真整数（bool 是 int 子类需排除），畸形数据回退 0。"""
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _as_text(value) -> str:
    return value if isinstance(value, str) else ""


def _days_between(older: str, newer: str) -> int:
    """两个 YYYY-MM-DD 之间的天数；解析失败按 0 处理（宁可不说想你了，也不误报）。"""
    try:
        span = date.fromisoformat(newer) - date.fromisoformat(older)
        return max(0, span.days)
    except ValueError:
        return 0
