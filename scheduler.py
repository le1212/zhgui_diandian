"""定时任务决策器：纯时间与状态逻辑，不依赖 Tk 或线程。"""

from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass
from typing import Callable

from models import Schedule, ScheduleKind


@dataclass(frozen=True)
class ScheduleDecision:
    schedule_id: str
    action: str
    reason: str


class Scheduler:
    """维护定时任务的到期推进。

    `run_at` 始终表示下一次计划时间。一次性任务触发或错过后禁用；每日
    与间隔任务会推进到未来的下一个时间点，避免应用长时间关闭后补跑一串。
    """

    def __init__(self, schedules: list[Schedule], *, grace_seconds: int = 60) -> None:
        self.schedules = schedules
        self.grace_seconds = max(0, grace_seconds)

    def tick(
        self,
        task_exists: Callable[[str], bool],
        is_running: bool | Callable[[], bool],
        on_due: Callable[[Schedule], bool],
        now: float | None = None,
    ) -> list[ScheduleDecision]:
        current = time.time() if now is None else now
        running = is_running if callable(is_running) else lambda: is_running
        decisions: list[ScheduleDecision] = []
        for schedule in self.schedules:
            if not schedule.enabled or schedule.run_at is None:
                continue
            if not task_exists(schedule.task_id):
                schedule.enabled = False
                schedule.last_result = "任务不存在，已停用"
                decisions.append(ScheduleDecision(schedule.id, "disabled", schedule.last_result))
                continue
            if schedule.run_at > current:
                continue
            late = current - schedule.run_at
            if late > self.grace_seconds:
                schedule.last_result = "已错过"
                self._advance(schedule, current)
                decisions.append(ScheduleDecision(schedule.id, "missed", schedule.last_result))
                continue
            if running():
                schedule.last_result = "运行中跳过"
                self._advance(schedule, current)
                decisions.append(ScheduleDecision(schedule.id, "skipped", schedule.last_result))
                continue
            if on_due(schedule):
                schedule.last_run_at = current
                schedule.last_result = "已触发"
                self._advance(schedule, current)
                decisions.append(ScheduleDecision(schedule.id, "started", schedule.last_result))
            else:
                schedule.last_result = "触发失败"
                self._advance(schedule, current)
                decisions.append(ScheduleDecision(schedule.id, "rejected", schedule.last_result))
        return decisions

    @staticmethod
    def _advance(schedule: Schedule, now: float) -> None:
        if schedule.kind == ScheduleKind.ONCE:
            schedule.enabled = False
            return
        if schedule.run_at is None:
            return
        if schedule.kind == ScheduleKind.DAILY:
            current = dt.datetime.fromtimestamp(now)
            scheduled = dt.datetime.fromtimestamp(schedule.run_at)
            next_day = (current.date() + dt.timedelta(days=1))
            schedule.run_at = dt.datetime.combine(next_day, scheduled.timetz()).timestamp()
            while schedule.run_at <= now:
                next_day += dt.timedelta(days=1)
                schedule.run_at = dt.datetime.combine(next_day, scheduled.timetz()).timestamp()
            return
        if schedule.kind == ScheduleKind.INTERVAL:
            step = max(1, schedule.interval_seconds)
        else:
            schedule.enabled = False
            schedule.last_result = "未知类型，已停用"
            return
        next_run = schedule.run_at + step
        while next_run <= now:
            next_run += step
        schedule.run_at = next_run
