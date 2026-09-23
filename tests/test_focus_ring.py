from __future__ import annotations

import time
import unittest
import tkinter as tk

from theme import color
from widgets import PillButton, Select


class FocusRingTests(unittest.TestCase):
    """焦点描边回归测试。

    背景：Tk 多边形在不显式传 fill 时的原生默认是不透明的系统色
    （SystemButtonText），描边会被画成实心色块，盖住按钮面与文字——
    表现为弹窗确认键变黑块。rounded_rect 现默认透明填充，
    焦点描边必须是"透明填充 + 可见描边"的纯轮廓。
    """

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
            raise unittest.SkipTest("无可用显示器，跳过焦点描边界面测试")
        cls.root.geometry("240x120")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.root.destroy()

    def _assert_outline_only_ring(self, canvas: tk.Canvas) -> None:
        rings = [item for item in canvas.find_all() if canvas.type(item) == "polygon"]
        self.assertTrue(rings, "焦点态应绘制描边多边形")
        ring = rings[-1]
        self.assertEqual(canvas.itemcget(ring, "fill"), "", "描边不得带填充，否则会盖住按钮面与文字")
        self.assertNotEqual(canvas.itemcget(ring, "outline"), "", "描边应可见")

    def test_pill_button_focus_ring_is_outline_only(self) -> None:
        button = PillButton(self.root, "重启", lambda: None, width=88, height=36,
                            bg=color("green"), fg=color("on_color_fg"), hover_bg=color("green_hover"))
        button.pack()
        button.focus_set()
        self.root.update()
        self.assertTrue(button._focused)
        self._assert_outline_only_ring(button)

    def test_select_focus_ring_is_outline_only(self) -> None:
        select = Select(self.root, tk.StringVar(value="单点连点"), ("单点连点", "多点任务"), width=120, height=38)
        select.pack()
        select.focus_set()
        self.root.update()
        self.assertTrue(select._focused)
        self._assert_outline_only_ring(select)


if __name__ == "__main__":
    unittest.main()
