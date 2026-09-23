from __future__ import annotations

import time
import unittest
import tkinter as tk

from widgets import S, Select


class SelectPopupTests(unittest.TestCase):
    """Select 弹层生命周期回归测试。

    背景：弹层曾依赖模态抓取实现"点击外部收起"，但抓取在 Windows 上对真实
    点击的派发不可靠，一旦收起路径失效，全部点击被吞、下拉框无法收起、
    应用假死。现在弹层不持有任何抓取：应用内点击由根窗口兜底绑定收起，
    焦点被其他应用夺走时由 FocusOut 收起，Esc 收起，选项点击走普通命中测试。

    注意：注入的指针事件（event_generate）只能派发到弹层树内目标，
    「点击应用内其他区域收起」这条物理路径由真实鼠标的验收测试覆盖。
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
            raise unittest.SkipTest("无可用显示器，跳过下拉框界面测试")
        cls.root.geometry("240x80")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.root.destroy()

    def setUp(self) -> None:
        self.values = ("左键单击", "右键单击", "中键单击")
        self.variable = tk.StringVar(value=self.values[0])
        self.picked: list[str] = []
        self.select = Select(self.root, self.variable, self.values, width=200, height=40,
                             command=lambda: self.picked.append(self.variable.get()))
        self.root.update()

    def tearDown(self) -> None:
        self.select._close_popup()
        self.root.update()

    def _open_popup(self) -> tk.Toplevel:
        self.select._open_popup()
        self.root.update()
        return self.select._popup

    def _assert_closed(self, popup: tk.Toplevel) -> None:
        self.assertIsNone(self.select._popup)
        self.assertFalse(popup.winfo_exists())
        self.assertIsNone(self.root.grab_current())

    def test_open_popup_without_modal_grab(self) -> None:
        popup = self._open_popup()
        self.assertIsNotNone(popup)
        # 关键不变量：弹层绝不持有抓取，否则收起路径失效即应用假死
        self.assertIsNone(self.root.grab_current())

    def test_row_click_selects_and_closes(self) -> None:
        popup = self._open_popup()
        self.select._popup_canvas.event_generate("<Button-1>", x=S(20), y=S(6) + S(38) + S(19))
        self.root.update()
        self.assertEqual(self.variable.get(), "右键单击")
        self.assertEqual(self.picked, ["右键单击"])
        self._assert_closed(popup)

    def test_blank_click_on_popup_closes(self) -> None:
        popup = self._open_popup()
        self.select._popup_canvas.event_generate("<Button-1>", x=S(20), y=2)
        self.root.update()
        self.assertEqual(self.variable.get(), "左键单击")
        self._assert_closed(popup)

    def test_escape_closes(self) -> None:
        popup = self._open_popup()
        popup.event_generate("<Escape>")
        self.root.update()
        self._assert_closed(popup)

    def test_toggle_closes_when_open(self) -> None:
        popup = self._open_popup()
        self.select._toggle()
        self.root.update()
        self._assert_closed(popup)

    def test_app_click_closes_via_root_binding(self) -> None:
        popup = self._open_popup()
        self.root.event_generate("<Button-1>", x=S(220), y=S(60))
        self.root.update()
        self._assert_closed(popup)

    def test_reopen_after_close(self) -> None:
        self._open_popup()
        self.select._close_popup()
        self.root.update()
        popup = self._open_popup()
        self.assertIsNotNone(popup)
        self.assertIsNone(self.root.grab_current())


if __name__ == "__main__":
    unittest.main()
