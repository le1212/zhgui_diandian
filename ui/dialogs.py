"""对话框域：项目风格的自建弹窗与可视区居中定位。

依赖宿主属性：root。_centered_position 同时服务弹窗、toast 与大图预览，
是全应用"必须落在用户看得见的区域内"的主要坐标出口。
"""

from __future__ import annotations

import tkinter as tk

from raw_input import key_name
from winapi import monitor_workarea_at
from widgets import (
    CARD_BG, DANGER_FG, F, FIELD_BORDER, GREEN, GREEN_HOVER, GREEN_TEXT, H_LG, INK,
    MAIN_BG, NEUTRAL_HOVER, ON_COLOR_FG, PILL_ACTIVE, PillButton, RUNNING_BG, S,
    SP_LG, SP_MD, SP_SM, SP_XL, SP_XS, shade,
)


class DialogsMixin:
    """自建对话框（输入/确认/选键）与多显示器安全的居中定位。"""

    KEY_NAME_CN = {
        "Backspace": "退格键", "Tab": "Tab键", "Enter": "回车键", "Shift": "Shift键",
        "Ctrl": "Ctrl键", "Alt": "Alt键", "Space": "空格键", "Page Up": "上翻页",
        "Page Down": "下翻页", "End": "行尾键", "Home": "行首键", "Insert": "插入键",
        "Delete": "删除键",
    }
    COMMON_KEYS = [
        ("回车键", 13), ("空格键", 32), ("Tab键", 9), ("Esc键", 27),
        ("退格键", 8), ("删除键", 46), ("行首键", 36), ("行尾键", 35),
        ("上翻页", 33), ("下翻页", 34),
        ("↑ 上方向键", 38), ("↓ 下方向键", 40), ("← 左方向键", 37), ("→ 右方向键", 39),
        ("F1", 112), ("F2", 113), ("F3", 114), ("F4", 115),
        ("F5", 116), ("F6", 117), ("F7", 118), ("F8", 119),
        ("F9", 120), ("F10", 121), ("F11", 122), ("F12", 123),
        ("数字 0", 48), ("数字 1", 49), ("数字 2", 50), ("数字 3", 51), ("数字 4", 52),
        ("数字 5", 53), ("数字 6", 54), ("数字 7", 55), ("数字 8", 56), ("数字 9", 57),
    ]

    @classmethod
    def _friendly_key_name(cls, code: int) -> str:
        raw = key_name(code)
        return cls.KEY_NAME_CN.get(raw, raw)

    def _ask_key_dialog(self, title: str = "添加按键", initial_code: int | None = None) -> tuple[int, str] | None:
        """常用按键选择对话框，返回 (键码, 名称) 或 None；支持自定义键码。"""
        result: dict[str, tuple[int, str] | None] = {"value": None}
        top = tk.Toplevel(self.root)
        top.title(title)
        top.configure(bg=MAIN_BG)
        top.resizable(False, False)
        top.transient(self.root)
        top.grab_set()
        top.focus_force()
        body = tk.Frame(top, bg=MAIN_BG, padx=SP_LG, pady=SP_LG)
        body.pack(fill="both", expand=True)
        tk.Label(body, text="选择按键（双击直接确认）：", bg=MAIN_BG, fg=INK, font=F(13)).pack(anchor="w")
        list_frame = tk.Frame(body, bg=CARD_BG, highlightthickness=1, highlightbackground=FIELD_BORDER)
        list_frame.pack(fill="both", expand=True, pady=(SP_SM, SP_MD))
        key_list = tk.Listbox(list_frame, height=10, font=F(12), bg=CARD_BG, fg=INK,
                               selectbackground=PILL_ACTIVE, selectforeground=GREEN_TEXT,
                               highlightthickness=0, relief="flat", activestyle="none")
        key_list.pack(side="left", fill="both", expand=True)
        scrollbar = tk.Scrollbar(list_frame, command=key_list.yview, width=S(10))
        scrollbar.pack(side="right", fill="y")
        key_list.config(yscrollcommand=scrollbar.set)
        for name, code in self.COMMON_KEYS:
            key_list.insert(tk.END, name)
        if initial_code is not None:
            for i, (_n, c) in enumerate(self.COMMON_KEYS):
                if c == initial_code:
                    key_list.selection_set(i)
                    key_list.see(i)
                    break
        btn_row = tk.Frame(body, bg=MAIN_BG)
        btn_row.pack(fill="x")
        def pick_custom():
            code = self._ask_input_dialog("自定义键码", "Windows 虚拟键码（A=65，B=66…）",
                                           initial=str(initial_code or 65), input_type="int", minvalue=1, maxvalue=255)
            if code is not None:
                result["value"] = (code, self._friendly_key_name(code))
                top.destroy()
        def confirm(_e=None):
            sel = key_list.curselection()
            if not sel:
                return
            name, code = self.COMMON_KEYS[sel[0]]
            result["value"] = (code, name)
            top.destroy()
        def cancel(_e=None):
            top.destroy()
        PillButton(btn_row, "自定义键码", pick_custom, width=S(96), height=H_LG, radius=S(8),
                   bg=CARD_BG, fg=INK, border=FIELD_BORDER, hover_bg=NEUTRAL_HOVER, font=F(12)).pack(side="left")
        PillButton(btn_row, "确定", confirm, width=S(80), height=H_LG, radius=S(8),
                   bg=GREEN, fg=ON_COLOR_FG, hover_bg=GREEN_HOVER, font=F(12, "bold")).pack(side="right")
        PillButton(btn_row, "取消", cancel, width=S(80), height=H_LG, radius=S(8),
                   bg=CARD_BG, fg=INK, border=FIELD_BORDER, hover_bg=NEUTRAL_HOVER, font=F(12)).pack(side="right", padx=(0, SP_SM))
        key_list.bind("<Double-Button-1>", confirm)
        top.bind("<Escape>", cancel)
        self._center_dialog(top)
        self.root.wait_window(top)
        return result["value"]

    def _centered_position(self, width: int, height: int) -> tuple[int, int]:
        """以主窗口可视区中心为基准返回左上角坐标；窗口被拖出屏幕时按所在显示器工作区收回，
        保证弹窗与 toast 始终完整落在用户看得见的区域内。"""
        root_x, root_y = self.root.winfo_rootx(), self.root.winfo_rooty()
        x = root_x + max(0, (self.root.winfo_width() - width) // 2)
        y = root_y + max(0, (self.root.winfo_height() - height) // 2)
        work_area = monitor_workarea_at(root_x + self.root.winfo_width() // 2, root_y + self.root.winfo_height() // 2)
        if work_area is not None:
            work_x, work_y, work_width, work_height = work_area
            x = min(max(x, work_x), max(work_x, work_x + work_width - width))
            y = min(max(y, work_y), max(work_y, work_y + work_height - height))
        return x, y

    def _center_dialog(self, dialog: tk.Toplevel) -> None:
        """将对话框居中到主窗口可视区：先按当前布局立即居中（避免在默认位置闪现后跳变），
        延迟 60ms 再校正一次（确保最终布局完成后位置准确）。"""
        def do_center() -> None:
            if not dialog.winfo_exists():
                return
            dialog.update_idletasks()
            x, y = self._centered_position(dialog.winfo_width(), dialog.winfo_height())
            dialog.geometry(f"+{x}+{y}")
        do_center()
        dialog.after(60, do_center)

    def _ask_input_dialog(self, title: str, label: str, initial: str = "",
                           input_type: str = "str", minvalue: float | None = None,
                           maxvalue: float | None = None) -> int | float | str | None:
        """和项目风格一致的中文输入对话框，支持 str/int/float 与范围校验，返回值或 None。"""
        result: dict[str, int | float | str | None] = {"value": None}
        top = tk.Toplevel(self.root)
        top.title(title)
        top.configure(bg=MAIN_BG)
        top.resizable(False, False)
        top.transient(self.root)
        top.grab_set()
        top.focus_force()
        body = tk.Frame(top, bg=MAIN_BG, padx=SP_XL, pady=SP_LG)
        body.pack(fill="both", expand=True)
        tk.Label(body, text=label, bg=MAIN_BG, fg=INK, font=F(13)).pack(anchor="w")
        entry = tk.Entry(body, width=36, font=F(13), bg=CARD_BG, fg=INK, relief="flat", highlightthickness=1, highlightbackground=FIELD_BORDER, highlightcolor=GREEN)
        entry.pack(fill="x", pady=(SP_SM, SP_XS), ipady=SP_SM)
        entry.insert(0, str(initial))
        entry.select_range(0, tk.END)
        entry.focus_set()
        error_label = tk.Label(body, text="", bg=MAIN_BG, fg=DANGER_FG, font=F(11))
        error_label.pack(anchor="w", pady=(0, SP_MD))
        btn_row = tk.Frame(body, bg=MAIN_BG)
        btn_row.pack(fill="x")
        def confirm(_e=None):
            raw = entry.get().strip()
            if input_type == "int":
                try:
                    value = int(raw)
                except ValueError:
                    error_label.configure(text="请输入整数")
                    return
            elif input_type == "float":
                try:
                    value = float(raw)
                except ValueError:
                    error_label.configure(text="请输入数字")
                    return
            else:
                value = raw
            if minvalue is not None and value < minvalue:
                error_label.configure(text=f"不能小于 {minvalue:g}")
                return
            if maxvalue is not None and value > maxvalue:
                error_label.configure(text=f"不能大于 {maxvalue:g}")
                return
            result["value"] = value
            top.destroy()
        def cancel(_e=None):
            top.destroy()
        PillButton(btn_row, "确定", confirm, width=S(88), height=H_LG, radius=S(8), bg=GREEN, fg=ON_COLOR_FG, hover_bg=GREEN_HOVER, font=F(13, "bold")).pack(side="right")
        PillButton(btn_row, "取消", cancel, width=S(88), height=H_LG, radius=S(8), bg=CARD_BG, fg=INK, border=FIELD_BORDER, hover_bg=NEUTRAL_HOVER, font=F(13)).pack(side="right", padx=(0, SP_SM))
        top.bind("<Return>", confirm)
        top.bind("<Escape>", cancel)
        self._center_dialog(top)
        self.root.wait_window(top)
        return result["value"]

    def _ask_text_dialog(self, title: str, label: str, initial: str = "") -> str | None:
        return self._ask_input_dialog(title, label, initial, input_type="str")

    def _ask_confirm(self, title: str, message: str, *, danger: bool = False,
                     confirm_text: str = "确定", cancel_text: str = "取消") -> bool:
        """和项目风格一致的确认对话框，替代原生 messagebox。

        危险操作时确认键为红色且默认焦点落在取消上（回车=取消），避免手滑回车直接确认破坏性操作。
        """
        result = {"confirmed": False}
        top = tk.Toplevel(self.root)
        top.title(title)
        top.configure(bg=MAIN_BG)
        top.resizable(False, False)
        top.transient(self.root)
        top.grab_set()
        body = tk.Frame(top, bg=MAIN_BG, padx=SP_XL, pady=SP_LG)
        body.pack(fill="both", expand=True)
        width = S(420)
        for line in message.splitlines():
            tk.Label(body, text=line or " ", bg=MAIN_BG, fg=INK, font=F(13), wraplength=width - S(60), justify="left").pack(anchor="w", pady=(0, SP_XS))
        btn_row = tk.Frame(body, bg=MAIN_BG)
        btn_row.pack(fill="x", pady=(SP_MD, 0))

        def confirm(_e=None):
            result["confirmed"] = True
            top.destroy()

        def cancel(_e=None):
            top.destroy()

        PillButton(btn_row, confirm_text, confirm, width=S(88), height=H_LG, radius=S(8),
                   bg=RUNNING_BG if danger else GREEN, fg=ON_COLOR_FG,
                   hover_bg=shade(RUNNING_BG, 0.9) if danger else GREEN_HOVER, font=F(13, "bold")).pack(side="right")
        PillButton(btn_row, cancel_text, cancel, width=S(88), height=H_LG, radius=S(8),
                   bg=CARD_BG, fg=INK, border=FIELD_BORDER, hover_bg=NEUTRAL_HOVER, font=F(13)).pack(side="right", padx=(0, SP_SM))
        top.bind("<Return>", cancel if danger else confirm)
        top.bind("<Escape>", cancel)
        if danger:
            cancel_button = btn_row.winfo_children()[1]
            cancel_button.focus_set()
        else:
            btn_row.winfo_children()[0].focus_set()
        self._center_dialog(top)
        self.root.wait_window(top)
        return result["confirmed"]
