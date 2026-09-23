"""Versioned domain models for tasks and automation steps."""

from __future__ import annotations

import copy
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any


SCHEMA_VERSION = 2
SCHEDULE_SCHEMA_VERSION = 1


class ScheduleKind:
    ONCE = "once"
    DAILY = "daily"
    INTERVAL = "interval"


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


@dataclass
class Step:
    id: str = field(default_factory=lambda: new_id("step"))
    type: str = "click"
    enabled: bool = True
    wait_ms: int = 500
    x: int = 0
    y: int = 0
    button: str = "左"
    title: str = "未命名窗口"
    hwnd: int = 0
    relative_x: int | None = None
    relative_y: int | None = None
    key_code: int = 0
    key_name: str = ""
    scroll_delta: int = 0
    text: str = ""
    template_file: str = ""
    match_threshold: float = 0.86
    click_offset_x: int = 0
    click_offset_y: int = 0
    condition: str = "存在"
    skip_count: int = 1

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Step":
        data = dict(value)
        data.setdefault("id", new_id("step"))
        data.setdefault("type", "click")
        data.setdefault("enabled", True)
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{key: data[key] for key in allowed if key in data})

    def clone(self) -> "Step":
        data = asdict(self)
        data["id"] = new_id("step")
        return Step.from_dict(data)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def action_name(self) -> str:
        names = {
            "click": f"{self.button}键单击",
            "key_down": f"按下 {self.key_name or self.key_code}",
            "key_up": f"松开 {self.key_name or self.key_code}",
            "scroll": "向上滚动" if self.scroll_delta > 0 else "向下滚动",
            "wait": "等待",
            "image_click": "图像定位点击",
            "if_image": "如果图像",
            "type_text": "输入文字",
        }
        return names.get(self.type, self.type)

    @property
    def target_summary(self) -> str:
        if self.type in {"click", "scroll"}:
            return f"{self.x}, {self.y} · {self.title[:18]}"
        if self.type in {"key_down", "key_up"}:
            return self.key_name or f"VK {self.key_code}"
        if self.type == "image_click":
            return self.template_file or "未设置模板"
        if self.type == "if_image":
            return f"{self.condition}则跳过{self.skip_count}步 · {self.template_file or '未设置模板'}"
        if self.type == "type_text":
            return (self.text[:24] + "…") if len(self.text) > 24 else (self.text or "空")
        if self.type == "wait":
            return "—"
        return "—"


@dataclass
class TaskSettings:
    button: str = "左键单击"
    position_mode: str = "窗口相对"
    interval_ms: int = 500
    repeat_count: int = 20
    countdown_enabled: bool = True
    countdown_seconds: float = 3.0
    random_interval: bool = False
    random_percent: float = 20.0

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> "TaskSettings":
        data = value or {}
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{key: data[key] for key in allowed if key in data})


@dataclass
class Task:
    id: str = field(default_factory=lambda: new_id("task"))
    name: str = "快速连点"
    mode: str = "单点连点"
    target: Step | None = None
    steps: list[Step] = field(default_factory=list)
    settings: TaskSettings = field(default_factory=TaskSettings)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    deleted_at: float | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Task":
        target = Step.from_dict(value["target"]) if value.get("target") else None
        steps = [Step.from_dict(item) for item in value.get("steps", [])]
        return cls(
            id=value.get("id") or new_id("task"),
            name=value.get("name") or "未命名任务",
            mode=value.get("mode") or "单点连点",
            target=target,
            steps=steps,
            settings=TaskSettings.from_dict(value.get("settings")),
            created_at=float(value.get("created_at", time.time())),
            updated_at=float(value.get("updated_at", time.time())),
            deleted_at=value.get("deleted_at"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "mode": self.mode,
            "target": self.target.to_dict() if self.target else None,
            "steps": [step.to_dict() for step in self.steps],
            "settings": asdict(self.settings),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "deleted_at": self.deleted_at,
        }

    def clone(self) -> "Task":
        clone = copy.deepcopy(self)
        clone.id = new_id("task")
        clone.name = f"{self.name} - 副本"
        clone.steps = [step.clone() for step in clone.steps]
        clone.target = clone.target.clone() if clone.target else None
        clone.created_at = time.time()
        clone.updated_at = clone.created_at
        clone.deleted_at = None
        return clone


@dataclass
class Schedule:
    id: str = field(default_factory=lambda: new_id("schedule"))
    task_id: str = ""
    kind: str = ScheduleKind.ONCE
    run_at: float | None = None
    interval_seconds: int = 3600
    enabled: bool = True
    last_run_at: float | None = None
    last_result: str = ""
    created_at: float = field(default_factory=time.time)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Schedule":
        return cls(
            id=value.get("id") or new_id("schedule"),
            task_id=value.get("task_id", ""),
            kind=value.get("kind", ScheduleKind.ONCE),
            run_at=float(value["run_at"]) if value.get("run_at") is not None else None,
            interval_seconds=max(1, int(value.get("interval_seconds", 3600))),
            enabled=bool(value.get("enabled", True)),
            last_run_at=float(value["last_run_at"]) if value.get("last_run_at") is not None else None,
            last_result=str(value.get("last_result", "")),
            created_at=float(value.get("created_at", time.time())),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def migrate_payload(payload: Any) -> tuple[list[Task], bool]:
    """Return migrated tasks and whether the persisted payload needs rewriting."""
    if isinstance(payload, dict) and payload.get("schemaVersion") == SCHEMA_VERSION:
        return [Task.from_dict(item) for item in payload.get("tasks", [])], False
    if isinstance(payload, list):
        return [Task.from_dict(item) for item in payload], True
    return [], True
