"""轻提示域：可视区居中的 toast 与"窗口不可见时走托盘气泡"的通知路由。

依赖宿主属性：root、_toast、_toast_fade_job、_tray_hidden、_tray_icon。
"""

from __future__ import annotations

import tkinter as tk

from widgets import F, GREEN_BRIGHT, RUNNING_BG, S, SP_LG, SP_SM, TOAST_BG, TOAST_FG


class ToastMixin:
    """toast 展示、淡出动画与悬停暂停；_notify 是全应用统一的通知入口。"""

    _TOAST_VISIBLE_MS = 3000   # toast 展示时长，到点后整窗淡出
    _TOAST_FADE_TICK_MS = 40   # 淡出动画每帧间隔
    _TOAST_FADE_FRAMES = 6     # 淡出帧数（总时长约 240ms）
    _TOAST_HOVER_POLL_MS = 120  # 淡出期间鼠标悬停的轮询间隔

    def _notify(self, title: str, message: str) -> None:
        """面向“用户可能没盯着窗口”的提醒：窗口可见走 toast，收进托盘/不可见走系统气泡。"""
        if self._tray_hidden or not self.root.winfo_viewable():
            if self._tray_icon is not None and self._tray_icon.is_alive() and self._tray_icon.notify(title, message):
                return
        self.show_toast(f"{title}：{message}")

    def show_toast(self, text: str, *, kind: str = "info", action=None,
                   action_label: str = "撤销") -> None:
        """可视区居中浮出轻提示，展示 3 秒后淡出；kind="error" 红底，action 为右侧可点动作（如撤销）。
        指向 toast 会暂停淡出，点击 toast 任意处立即关闭。"""
        self._dismiss_toast(self._toast)
        toast = tk.Toplevel(self.root)
        toast.overrideredirect(True)
        # 不置顶：toast 只属于点点，切到别的应用时跟随主窗口层级，不再压住所有页面
        row = tk.Frame(toast, bg=RUNNING_BG if kind == "error" else TOAST_BG)
        row.pack()
        tk.Label(row, text=text, bg=row["bg"], fg=TOAST_FG, font=F(12), padx=SP_LG, pady=SP_SM).pack(side="left")
        if action is not None:
            # Label 的 padx 只接受单一数值（不接受二元组），左右各留 8 逻辑像素
            action_button = tk.Label(row, text=action_label, bg=row["bg"], fg=GREEN_BRIGHT, font=F(12, "bold"), padx=SP_SM, pady=SP_SM, cursor="hand2")
            action_button.pack(side="left")
            action_button.bind("<Button-1>", lambda _e: (self._dismiss_toast(toast), action()))
        toast.update_idletasks()
        x, y = self._centered_position(toast.winfo_width(), toast.winfo_height())
        toast.geometry(f"+{x}+{y}")
        self._toast = toast
        self._toast_fade_job = self.root.after(self._TOAST_VISIBLE_MS, lambda: self._fade_out_toast(toast, self._TOAST_FADE_FRAMES))
        toast.bind("<Button-1>", lambda _e: self._dismiss_toast(toast))

    def _cancel_toast_fade_job(self) -> None:
        if self._toast_fade_job is not None:
            try:
                self.root.after_cancel(self._toast_fade_job)
            except tk.TclError:
                pass
            self._toast_fade_job = None

    def _fade_out_toast(self, toast: tk.Toplevel, frames_left: int) -> None:
        """展示期结束后的整窗淡出；toast 已被销毁（如被新 toast 替换）则直接退出，
        鼠标正指向时暂停并恢复不透明（给点击撤销等动作留出时间）。"""
        if not toast.winfo_exists():
            return
        if self._pointer_on_window(toast):
            try:
                toast.attributes("-alpha", 1.0)
            except tk.TclError:
                self._dismiss_toast(toast)
                return
            self._toast_fade_job = self.root.after(self._TOAST_HOVER_POLL_MS, lambda: self._fade_out_toast(toast, self._TOAST_FADE_FRAMES))
            return
        if frames_left <= 0:
            self._dismiss_toast(toast)
            return
        try:
            toast.attributes("-alpha", frames_left / self._TOAST_FADE_FRAMES)
        except tk.TclError:
            self._dismiss_toast(toast)
            return
        self._toast_fade_job = self.root.after(self._TOAST_FADE_TICK_MS, lambda: self._fade_out_toast(toast, frames_left - 1))

    def _pointer_on_window(self, window: tk.Toplevel) -> bool:
        px, py = window.winfo_pointerx(), window.winfo_pointery()
        return (window.winfo_rootx() <= px < window.winfo_rootx() + window.winfo_width()
                and window.winfo_rooty() <= py < window.winfo_rooty() + window.winfo_height())

    def _dismiss_toast(self, toast: tk.Toplevel | None) -> None:
        if toast is None:
            return
        self._cancel_toast_fade_job()
        if self._toast is toast:
            self._toast = None
        try:
            toast.destroy()
        except tk.TclError:
            pass
