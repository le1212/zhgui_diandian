from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from models import Schedule, ScheduleKind, Step
from run_engine import RunEngine, RunPlan
from scheduler import Scheduler
from task_repository import TaskRepository


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


class RunEngineTests(unittest.TestCase):
    def test_repeats_and_condition_skip(self) -> None:
        clock = FakeClock()
        executed: list[str] = []
        actions = (
            Step(type="if_image", condition="存在", skip_count=1),
            Step(type="click", x=1),
            Step(type="wait", wait_ms=20),
        )
        engine = RunEngine(
            RunPlan(actions=actions, repeats=2),
            threading.Event(),
            threading.Event(),
            execute_step=lambda step: executed.append(step.type),
            image_matches=lambda _step: False,
            clock=clock,
            sleeper=clock.sleep,
        )
        result = engine.run()
        self.assertTrue(result.completed)
        self.assertEqual(result.count, 2)
        self.assertEqual(executed, ["wait", "wait"])

    def test_stop_event_stops_infinite_plan(self) -> None:
        clock = FakeClock()
        stop = threading.Event()
        executed: list[str] = []

        def execute(step: Step) -> None:
            executed.append(step.type)
            stop.set()

        result = RunEngine(
            RunPlan(actions=(Step(type="click"),), repeats=0),
            stop,
            threading.Event(),
            execute_step=execute,
            image_matches=lambda _step: False,
            clock=clock,
            sleeper=clock.sleep,
        ).run()
        self.assertFalse(result.completed)
        self.assertEqual(result.count, 1)
        self.assertEqual(executed, ["click"])

    def test_step_error_is_returned(self) -> None:
        error = RuntimeError("inject failed")
        result = RunEngine(
            RunPlan(actions=(Step(type="click"),), repeats=1),
            threading.Event(),
            threading.Event(),
            execute_step=lambda _step: (_ for _ in ()).throw(error),
            image_matches=lambda _step: False,
        ).run()
        self.assertIs(result.error, error)
        self.assertFalse(result.completed)


class SchedulerTests(unittest.TestCase):
    def test_once_due_is_started_and_disabled(self) -> None:
        schedule = Schedule(task_id="task-1", kind=ScheduleKind.ONCE, run_at=100)
        called: list[str] = []
        decisions = Scheduler([schedule]).tick(
            task_exists=lambda task_id: task_id == "task-1",
            is_running=lambda: False,
            on_due=lambda item: called.append(item.id) or True,
            now=100,
        )
        self.assertEqual(called, [schedule.id])
        self.assertFalse(schedule.enabled)
        self.assertEqual(decisions[0].action, "started")

    def test_daily_missed_advances_to_future(self) -> None:
        schedule = Schedule(task_id="task-1", kind=ScheduleKind.DAILY, run_at=100)
        Scheduler([schedule], grace_seconds=60).tick(
            task_exists=lambda _task_id: True,
            is_running=False,
            on_due=lambda _item: True,
            now=3 * 86400 + 100,
        )
        self.assertEqual(schedule.last_result, "已错过")
        self.assertGreater(schedule.run_at, 3 * 86400 + 100)
        self.assertTrue(schedule.enabled)

    def test_running_schedule_is_skipped_without_starting(self) -> None:
        schedule = Schedule(task_id="task-1", kind=ScheduleKind.INTERVAL, run_at=100, interval_seconds=60)
        started: list[str] = []
        decisions = Scheduler([schedule]).tick(
            task_exists=lambda _task_id: True,
            is_running=lambda: True,
            on_due=lambda item: started.append(item.id) or True,
            now=100,
        )
        self.assertEqual(started, [])
        self.assertEqual(decisions[0].action, "skipped")
        self.assertEqual(schedule.run_at, 160)

    def test_missing_task_is_disabled(self) -> None:
        schedule = Schedule(task_id="gone", run_at=100)
        Scheduler([schedule]).tick(
            task_exists=lambda _task_id: False,
            is_running=False,
            on_due=lambda _item: True,
            now=100,
        )
        self.assertFalse(schedule.enabled)
        self.assertEqual(schedule.last_result, "任务不存在，已停用")


class ScheduleRepositoryTests(unittest.TestCase):
    def test_schedules_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = TaskRepository(Path(directory))
            original = [Schedule(task_id="task-1", kind=ScheduleKind.INTERVAL, run_at=123.5, interval_seconds=900)]
            repository.save_schedules(original)
            restored = repository.load_schedules()
            self.assertEqual(len(restored), 1)
            self.assertEqual(restored[0].task_id, "task-1")
            self.assertEqual(restored[0].interval_seconds, 900)
            self.assertEqual(restored[0].run_at, 123.5)


if __name__ == "__main__":
    unittest.main()
