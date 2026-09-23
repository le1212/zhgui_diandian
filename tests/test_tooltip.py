from __future__ import annotations

import time
import unittest
import tkinter as tk

from widgets import HelpIcon, ToolTip


class ToolTipTests(unittest.TestCase):
    """悬浮说明气泡：显示/隐藏生命周期与组件构建。"""

    @classmethod
    def setUpClass(cls) -> None:
        # 快速连续创建/销毁多个 Tk 实例时，Windows 下偶发瞬态创建失败，重试数次
        cls.root = None
        for _attempt in range(5):
            try:
                cls.root = tk.Tk()
                break
            except tk.TclError:
                time.sleep(0.3)
        if cls.root is None:
            raise unittest.SkipTest("无可用显示器，跳过悬浮提示界面测试")
        cls.root.geometry("320x120")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.root.destroy()

    def setUp(self) -> None:
        self.anchor = tk.Label(self.root, text="锚点控件")
        self.anchor.pack(padx=20, pady=20)
        self.root.update()

    def tearDown(self) -> None:
        for child in self.root.winfo_children():
            if child is not self.anchor:
                child.destroy()
        self.root.update()

    def test_show_creates_bubble_and_hide_destroys(self) -> None:
        tip = ToolTip(self.anchor, "第一行说明\n· 选项 A：说明 A\n· 选项 B：说明 B")
        tip._show()
        bubble = tip._bubble
        self.assertIsNotNone(bubble)
        self.assertTrue(bubble.winfo_exists())
        tip._hide()
        self.assertIsNone(tip._bubble)
        self.assertFalse(bubble.winfo_exists())

    def test_show_is_idempotent(self) -> None:
        tip = ToolTip(self.anchor, "说明")
        tip._show()
        bubble = tip._bubble
        tip._show()
        self.assertIs(tip._bubble, bubble)
        tip._hide()

    def test_schedule_then_hide_cancels(self) -> None:
        tip = ToolTip(self.anchor, "说明")
        tip._schedule()
        tip._hide()
        self.assertIsNone(tip._job)
        self.assertIsNone(tip._bubble)
        self.root.update()
        self.assertIsNone(tip._bubble)

    def test_help_icon_builds_and_draws(self) -> None:
        icon = HelpIcon(self.root, "字段说明")
        icon.pack()
        self.root.update()
        self.assertEqual(len(icon.find_all()), 2)


if __name__ == "__main__":
    unittest.main()
