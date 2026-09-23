"""与 Tk 无关的任务执行引擎。

执行器只依赖回调，因此可以在单元测试中使用假的输入注入和时钟；UI
控制器负责把进度事件投递回主线程，避免本模块碰任何 Tk 控件。
"""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass
from typing import Callable

from models import Step


@dataclass(frozen=True)
class RunPlan:
    actions: tuple[Step, ...]
    repeats: int = 0
    random_interval: bool = False
    random_percent: float = 20.0


@dataclass(frozen=True)
class RunResult:
    completed: bool
    count: int
    error: Exception | None = None


class RunEngine:
    def __init__(
        self,
        plan: RunPlan,
        stop_event: threading.Event,
        pause_event: threading.Event,
        execute_step: Callable[[Step], None],
        image_matches: Callable[[Step], bool],
        on_progress: Callable[[str], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        random_source: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self.plan = plan
        self.stop_event = stop_event
        self.pause_event = pause_event
        self.execute_step = execute_step
        self.image_matches = image_matches
        self.on_progress = on_progress or (lambda _text: None)
        self.clock = clock
        self.sleeper = sleeper
        self.random_source = random_source

    def run(self) -> RunResult:
        count = 0
        last_progress = float("-inf")
        try:
            while not self.stop_event.is_set() and (self.plan.repeats == 0 or count < self.plan.repeats):
                total = len(self.plan.actions)
                skip_remaining = 0
                for index, step in enumerate(self.plan.actions):
                    if not step.enabled:
                        continue
                    if skip_remaining > 0:
                        skip_remaining -= 1
                        continue
                    if step.type == "if_image":
                        last_progress = self._emit_progress(f"判断图像 · 第 {count + 1} 次", last_progress)
                        matched = self.image_matches(step)
                        condition_met = (matched and step.condition == "存在") or (
                            not matched and step.condition == "不存在"
                        )
                        if not condition_met:
                            skip_remaining = max(0, step.skip_count)
                        continue
                    last_progress = self._emit_progress(f"第 {index + 1}/{total} 步 · 第 {count + 1} 次", last_progress)
                    wait_ms = step.wait_ms if step.type == "wait" or count or index else 0
                    if self.plan.random_interval and wait_ms:
                        spread = max(0.0, min(1.0, self.plan.random_percent / 100))
                        wait_ms = round(self.random_source(wait_ms * (1 - spread), wait_ms * (1 + spread)))
                    if not self._wait_or_stop(wait_ms):
                        return RunResult(False, count)
                    self.execute_step(step)
                count += 1
                last_progress = self._emit_progress(f"运行中 · 第 {count} 次", last_progress)
            return RunResult(not self.stop_event.is_set(), count)
        except Exception as error:
            return RunResult(False, count, error)

    def _emit_progress(self, text: str, last_progress: float) -> float:
        now = self.clock()
        if now - last_progress >= 0.1:
            self.on_progress(text)
            return now
        return last_progress

    def _wait_or_stop(self, milliseconds: int) -> bool:
        deadline = self.clock() + max(0, milliseconds) / 1000
        while self.clock() < deadline:
            if self.stop_event.is_set():
                return False
            while self.pause_event.is_set() and not self.stop_event.is_set():
                self.sleeper(0.05)
            self.sleeper(0.01)
        return not self.stop_event.is_set()
