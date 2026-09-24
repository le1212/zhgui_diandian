"""主窗口与悬浮窗口编排。

负责 Tk 主窗口的 Win32 句柄、缩放节流与隐藏后原子恢复；业务控制器只
调用 restore_main_window，不再重复处理 WM_SETREDRAW 的时序细节。
"""

from __future__ import annotations

import tkinter as tk

from winapi import (
    RDW_ALLCHILDREN,
    RDW_ERASE,
    RDW_FRAME,
    RDW_INVALIDATE,
    RDW_UPDATENOW,
    ResizeThrottle,
    USER32,
    WM_SETREDRAW,
)


class WindowCoordinator:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.message_window_handle = root.winfo_id()
        self.root_window_handle = int(USER32.GetAncestor(self.message_window_handle, 2) or self.message_window_handle)
        try:
            self.resize_throttle: ResizeThrottle | None = ResizeThrottle(self.root_window_handle)
        except OSError:
            self.resize_throttle = None
        self.pre_hide_state = "normal"

    def freeze_paint(self) -> None:
        USER32.SendMessageW(self.root_window_handle, WM_SETREDRAW, 0, 0)

    def thaw_paint(self) -> None:
        USER32.SendMessageW(self.root_window_handle, WM_SETREDRAW, 1, 0)
        USER32.RedrawWindow(
            self.root_window_handle,
            None,
            None,
            RDW_INVALIDATE | RDW_ERASE | RDW_FRAME | RDW_ALLCHILDREN | RDW_UPDATENOW,
        )

    def remember_state(self) -> None:
        """主窗口隐藏前记录当前窗口状态，恢复时不再无条件强制最大化。"""
        try:
            self.pre_hide_state = self.root.state()
        except tk.TclError:
            self.pre_hide_state = "normal"

    def restore_main_window(self) -> None:
        """在冻结期间完成状态切换，避免 deiconify→zoomed 的中间黑帧。

        只在隐藏前本就是最大化时才回到最大化，普通尺寸窗口恢复原状态。
        """
        self.freeze_paint()
        try:
            was_zoomed = self.pre_hide_state == "zoomed"
            self.root.deiconify()
            if was_zoomed:
                self.root.state("zoomed")
                if self.root.state() != "zoomed":
                    self.root.state("zoomed")
            self.root.update_idletasks()
            self.root.lift()
            self.root.focus_force()
        finally:
            self.thaw_paint()
