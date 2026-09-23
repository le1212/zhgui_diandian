"""Crash-safe local persistence with migration, backup rotation, and trash."""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

from models import SCHEMA_VERSION, SCHEDULE_SCHEMA_VERSION, Schedule, Task, migrate_payload


class TaskRepository:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.tasks_path = base_dir / "tasks.json"
        self.trash_path = base_dir / "trash.json"
        self.schedules_path = base_dir / "schedules.json"
        self.backup_dir = base_dir / "backups"
        self.template_dir = base_dir / "templates"
        for directory in (base_dir, self.backup_dir, self.template_dir):
            directory.mkdir(parents=True, exist_ok=True)

    def load_tasks(self) -> list[Task]:
        if not self.tasks_path.exists():
            return [Task()]
        try:
            payload = json.loads(self.tasks_path.read_text(encoding="utf-8"))
            tasks, migrated = migrate_payload(payload)
            if not tasks:
                tasks = [Task()]
            if migrated:
                self.save_tasks(tasks)
            return tasks
        except (OSError, ValueError, TypeError):
            recovered = self._load_latest_backup()
            return recovered or [Task()]

    def save_tasks(self, tasks: list[Task]) -> None:
        payload = {"schemaVersion": SCHEMA_VERSION, "tasks": [task.to_dict() for task in tasks]}
        self._atomic_write(self.tasks_path, payload, create_backup=True)

    def load_trash(self) -> list[Task]:
        if not self.trash_path.exists():
            return []
        try:
            payload = json.loads(self.trash_path.read_text(encoding="utf-8"))
            values = payload.get("tasks", []) if isinstance(payload, dict) else payload
            return [Task.from_dict(item) for item in values]
        except (OSError, ValueError, TypeError):
            return []

    def save_trash(self, tasks: list[Task]) -> None:
        payload = {"schemaVersion": SCHEMA_VERSION, "tasks": [task.to_dict() for task in tasks]}
        self._atomic_write(self.trash_path, payload, create_backup=False)

    def load_schedules(self) -> list[Schedule]:
        if not self.schedules_path.exists():
            return []
        try:
            payload = json.loads(self.schedules_path.read_text(encoding="utf-8"))
            values = payload.get("schedules", []) if isinstance(payload, dict) else payload
            schedules: list[Schedule] = []
            for item in values:
                if not isinstance(item, dict):
                    continue
                try:
                    # 单条损坏只跳过该条，不让全部定时计划静默消失
                    schedules.append(Schedule.from_dict(item))
                except (ValueError, TypeError):
                    continue
            return schedules
        except (OSError, ValueError, TypeError):
            return []

    def save_schedules(self, schedules: list[Schedule]) -> None:
        payload = {"schemaVersion": SCHEDULE_SCHEMA_VERSION, "schedules": [item.to_dict() for item in schedules]}
        self._atomic_write(self.schedules_path, payload, create_backup=False)

    def delete_template_if_unused(self, template_file: str, active: list[Task], trash: list[Task]) -> None:
        if not template_file:
            return
        for task in (*active, *trash):
            candidates = list(task.steps)
            if task.target:
                candidates.append(task.target)
            if any(step.template_file == template_file for step in candidates):
                return
        path = self.template_dir / template_file
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    def cleanup_orphan_templates(self, active: list[Task], trash: list[Task]) -> None:
        referenced: set[str] = set()
        for task in (*active, *trash):
            candidates = list(task.steps)
            if task.target:
                candidates.append(task.target)
            referenced.update(step.template_file for step in candidates if step.template_file)
        for path in self.template_dir.glob("template-*.png"):
            if path.name not in referenced:
                try:
                    path.unlink()
                except OSError:
                    pass

    def _atomic_write(self, destination: Path, payload: dict[str, Any], create_backup: bool) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        serialized = json.dumps(payload, ensure_ascii=False, indent=2)
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
        json.loads(temporary.read_text(encoding="utf-8"))
        if create_backup and destination.exists():
            stamp = f"{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 1_000_000_000:09d}"
            shutil.copy2(destination, self.backup_dir / f"tasks-{stamp}.json")
        os.replace(temporary, destination)
        if create_backup:
            self._rotate_backups()

    def _rotate_backups(self, keep: int = 5) -> None:
        for path in self._backup_paths()[keep:]:
            try:
                path.unlink()
            except OSError:
                pass

    def _backup_paths(self) -> list[Path]:
        """按修改时间倒序的备份列表；stat 失败（文件被外部移除）的条目直接跳过。"""
        entries = []
        for path in self.backup_dir.glob("tasks-*.json"):
            try:
                entries.append((path.stat().st_mtime, path.name, path))
            except OSError:
                continue
        entries.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [path for _mtime, _name, path in entries]

    def _load_latest_backup(self) -> list[Task]:
        for path in self._backup_paths():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                tasks, _ = migrate_payload(payload)
                if tasks:
                    return tasks
            except (OSError, ValueError, TypeError):
                continue
        return []
