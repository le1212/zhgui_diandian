from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from image_locator import _normalized_cross_correlation
from models import Step, Task, TaskSettings
from task_repository import TaskRepository


class ModelTests(unittest.TestCase):
    def test_step_clone_has_new_identity(self) -> None:
        original = Step(type="click", x=10, y=20)
        clone = original.clone()
        self.assertNotEqual(original.id, clone.id)
        self.assertEqual((clone.x, clone.y), (10, 20))

    def test_task_clone_clones_steps(self) -> None:
        task = Task(name="路径", steps=[Step(type="wait", wait_ms=200)])
        clone = task.clone()
        self.assertNotEqual(task.id, clone.id)
        self.assertNotEqual(task.steps[0].id, clone.steps[0].id)


class RepositoryTests(unittest.TestCase):
    def test_legacy_payload_migrates_and_creates_backup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = [{"name": "旧任务", "mode": "多点任务", "steps": [{"x": 3, "y": 5, "wait_ms": 80}]}]
            (root / "tasks.json").write_text(json.dumps(legacy), encoding="utf-8")
            repository = TaskRepository(root)
            tasks = repository.load_tasks()
            self.assertEqual(tasks[0].steps[0].type, "click")
            payload = json.loads((root / "tasks.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["schemaVersion"], 2)
            repository.save_tasks(tasks)
            self.assertTrue(list((root / "backups").glob("tasks-*.json")))


class SettingsTests(unittest.TestCase):
    def test_settings_roundtrip_decimal_and_legacy_defaults(self) -> None:
        task = Task(settings=TaskSettings(countdown_seconds=2.5, random_percent=35))
        restored = Task.from_dict(task.to_dict())
        self.assertEqual(restored.settings.countdown_seconds, 2.5)
        self.assertEqual(restored.settings.random_percent, 35)
        legacy = Task.from_dict({"name": "旧任务"})
        self.assertEqual(legacy.settings.countdown_seconds, 3.0)
        self.assertEqual(legacy.settings.random_percent, 20.0)


class ImageLocatorTests(unittest.TestCase):
    def test_template_match_finds_known_position(self) -> None:
        generator = np.random.default_rng(7)
        image = generator.normal(120, 20, (100, 140)).astype(np.float32)
        template = generator.normal(120, 20, (18, 22)).astype(np.float32)
        image[37:55, 71:93] = template
        fft_shape = (image.shape[0] + template.shape[0] - 1, image.shape[1] + template.shape[1] - 1)
        template_fft = np.fft.rfftn(np.flip(template), fft_shape, axes=(0, 1))
        x, y, score = _normalized_cross_correlation(image, template, template_fft)
        self.assertEqual((x, y), (71, 37))
        self.assertGreater(score, .99)


if __name__ == "__main__":
    unittest.main()
