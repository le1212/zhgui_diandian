from __future__ import annotations

import time
import unittest
import tkinter as tk

from widgets import NumberField


class NumberFieldTests(unittest.TestCase):
    """数字输入框：DoubleVar 支持小数，IntVar 保持整数行为。"""

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
            raise unittest.SkipTest("无可用显示器，跳过数字输入框界面测试")
        cls.root.geometry("240x80")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.root.destroy()

    def test_float_field_accepts_decimals(self) -> None:
        variable = tk.DoubleVar(value=0.5)
        field = NumberField(self.root, variable, width=120, height=30, minimum=0.05, maximum=60, step=0.1)
        field._text.set("2.5")
        self.assertEqual(variable.get(), 2.5)
        self.assertFalse(field._validate("2.5.6"))
        self.assertFalse(field._validate("2..5"))
        self.assertTrue(field._validate("0."))
        self.assertTrue(field._validate(""))

    def test_float_field_clamps_and_formats(self) -> None:
        variable = tk.DoubleVar(value=1)
        field = NumberField(self.root, variable, width=120, height=30, minimum=0.05, maximum=60, step=0.1)
        field._apply(99)
        self.assertEqual(variable.get(), 60)
        self.assertEqual(field._text.get(), "60")
        field._apply(0.1 + 0.2)
        self.assertEqual(field._text.get(), "0.3")

    def test_float_field_typing_partial_dot_keeps_text(self) -> None:
        variable = tk.DoubleVar(value=1)
        field = NumberField(self.root, variable, width=120, height=30, minimum=0.05, maximum=60)
        # "0." 解析为 0 属预期；关键是文本原样保留，用户可继续输入小数位
        field._text.set("0.")
        self.assertEqual(variable.get(), 0)
        self.assertEqual(field._text.get(), "0.")
        field._text.set("0.5")
        self.assertEqual(variable.get(), 0.5)
        self.assertEqual(field._text.get(), "0.5")

    def test_int_field_keeps_integer_only(self) -> None:
        variable = tk.IntVar(value=20)
        field = NumberField(self.root, variable, width=120, height=30, minimum=0, maximum=100, step=5)
        self.assertFalse(field._validate("2.5"))
        self.assertTrue(field._validate("35"))
        field._text.set("35")
        self.assertEqual(variable.get(), 35)
        field._apply(12.7)
        self.assertEqual(variable.get(), 12)

    def test_float_field_step_buttons(self) -> None:
        variable = tk.DoubleVar(value=0.5)
        field = NumberField(self.root, variable, width=120, height=30, minimum=0.05, maximum=60, step=0.1)
        field._apply(field._current() + field.step)
        self.assertEqual(field._text.get(), "0.6")


if __name__ == "__main__":
    unittest.main()
