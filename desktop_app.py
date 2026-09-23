"""点点：免安装 Windows 桌面连点器。

The application intentionally uses Win32 APIs directly so the packaged executable
can control real desktop windows without requiring Python or third-party runtimes.
"""

from __future__ import annotations

import copy
import datetime as dt
import json
import math
import os
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
import uuid
from collections import OrderedDict
from pathlib import Path
from tkinter import filedialog

from PIL import Image, ImageDraw, ImageGrab, ImageTk

from image_locator import locate_template, virtual_screen_origin
import autostart
from models import Schedule, ScheduleKind, Step, Task
from raw_input import RawInputRecorder, key_name
from run_engine import RunEngine, RunPlan
from scheduler import Scheduler
from task_repository import TaskRepository
from theme import THEME_NAME
from tray_icon import TrayIcon
from widgets import (
    BORDER, BORDER_SOFT, CARD_BG, CheckBox, ContextMenu, DANGER_BG,
    DANGER_DEEP, DANGER_FG, DANGER_HOVER, DANGER_SOFT_BG, FIELD_BORDER, F,
    GRAY, GREEN, GREEN_BRIGHT, GREEN_DEEP, GREEN_HOVER, GREEN_TEXT, HEADER_BG, HelpIcon,
    ICON_MUTED, INK, INK_SOFT, INFO_BLUE, LIST_BG, LIST_BORDER, MAIN_BG, M,
    NEUTRAL_HOVER, NumberField, ON_COLOR_FG, OVERLAY_ACCENT, OVERLAY_BG,
    OVERLAY_BUTTON, OVERLAY_BUTTON_HOVER, OVERLAY_INK_SOFT, PANEL_BG, PILL_ACTIVE, PillButton,
    RoundedCard, ROW_HOVER, RUNNING_BG, RUNNING_PEAK, S, Select, SIDEBAR_BG,
    SUBTLE_BG, SUBTLE_HOVER, TASK_ACTIVE_FG, TASK_FG, TextField,
    ThinScrollbar, TOAST_BG, TOAST_FG, TOOLBAR_BG, TOOLBAR_FG,
    TOOLBAR_HOVER, brand_icon_image, mix, paint_icon, rounded_rect, shade,
    text_font,
)
from winapi import (
    HWND_TOPMOST, POINT, SWP_SHOWWINDOW, USER32, VK_ESCAPE, VK_F2, VK_F6,
    VK_F7, acquire_mutex, activate_window_by_title, click_at,
    cursor_position, enable_per_monitor_dpi_awareness, monitor_workarea_at,
    monitor_workareas, paste_text, release_mutex, scroll_at, send_key, window_rect,
    window_title,
)
from window_coordinator import WindowCoordinator


# 步骤模式下信息卡三栏标题随步骤类型切换：(坐标/按键栏, 窗口/对象栏, 动作栏)
STEP_READOUT_LABELS = {
    "click": ("坐标位置", "目标窗口", "点击方式"),
    "scroll": ("坐标位置", "目标窗口", "滚动方式"),
    "key_down": ("按键", "目标窗口", "按键动作"),
    "key_up": ("按键", "目标窗口", "按键动作"),
    "image_click": ("定位方式", "模板文件", "点击方式"),
    "wait": ("时长", "目标窗口", "动作"),
    "type_text": ("输入内容", "目标窗口", "动作"),
}


class DesktopClicker:
    # 运行按钮呼吸动画的两端色（深红 ↔ 亮红），由 _run_breath_tick 插值
    _RUN_BREATH_BASE = RUNNING_BG
    _RUN_BREATH_PEAK = RUNNING_PEAK

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("点点")
        configured_data_dir = os.environ.get("DIANDIAN_DATA_DIR")
        self.app_data_dir = Path(configured_data_dir) if configured_data_dir else Path(os.environ.get("APPDATA", Path.home())) / "Diandian"
        self.repository = TaskRepository(self.app_data_dir)
        self._ui_state = self.repository.load_ui_state()
        self._status_history: list[tuple[float, str, str]] = []
        self._history_bubble: tk.Toplevel | None = None
        self._history_show_job: str | None = None
        self._toast: tk.Toplevel | None = None
        self.root.minsize(S(1180), S(760))
        self.root.configure(bg=MAIN_BG)
        self._restore_window_placement()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind("<Map>", self._on_window_restore)
        self.thumbs_dir = self.app_data_dir / "thumbs"
        self.thumbs_dir.mkdir(parents=True, exist_ok=True)
        self.tasks = self.repository.load_tasks()
        self.trash = self.repository.load_trash()
        self.schedules = self.repository.load_schedules()
        self.scheduler = Scheduler(self.schedules)
        self.active_index = 0
        self.steps: list[Step] = []
        self.target: Step | None = None
        self.capture_armed = False
        self.recording = False
        self.running = False
        self.paused = False
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.input_recorder = RawInputRecorder(self._raw_input_event)
        self.last_record_time = 0.0
        self._steps_render_scheduled = False
        self.ui_events: queue.SimpleQueue[tuple[object, tuple]] = queue.SimpleQueue()
        self.undo_stack: list[tuple[str, object]] = []
        self.drag_step_index: int | None = None
        self.run_actions: tuple[Step, ...] = ()
        self.run_repeats = 0
        self.run_random_interval = False
        self.run_random_percent = 20.0
        self.run_position_mode = "窗口相对"
        self.run_keys_down: set[int] = set()
        self.tasks_expanded = True
        self._task_list_signature: tuple | None = None
        self._screen_thumb: Image.Image | None = None
        self._step_thumbs: OrderedDict[str, Image.Image] = OrderedDict()
        self._THUMB_CACHE_LIMIT = 48
        self._thumb_photo: ImageTk.PhotoImage | None = None
        self._thumb_render_size: tuple[int, int] | None = None
        self._thumb_display_rect: tuple[int, int, int, int] | None = None
        self._dot_photo: ImageTk.PhotoImage | None = None
        self._dot_photo_key: tuple[int, int, int, str] | None = None
        self._preview_point: tuple[int, int, str] | None = None
        self.task_name_var = tk.StringVar(value="点点快速连点任务")
        self.mode_var = tk.StringVar(value="单点连点")
        self.button_var = tk.StringVar(value="左键单击")
        self.position_var = tk.StringVar(value="窗口相对")
        self.interval_var = tk.DoubleVar(value=0.5)
        self.repeat_var = tk.IntVar(value=20)
        self.countdown_seconds_var = tk.DoubleVar(value=3)
        self.random_percent_var = tk.DoubleVar(value=20)
        self.countdown_var = tk.BooleanVar(value=True)
        self.random_var = tk.BooleanVar(value=False)
        self.capture_overlay: tk.Toplevel | None = None
        self.capture_overlay_handle = 0
        self.run_overlay: tk.Toplevel | None = None
        self.run_overlay_handle = 0
        self.run_overlay_status: tk.Label | None = None
        self.run_overlay_pause: tk.Button | None = None
        self.record_overlay: tk.Toplevel | None = None
        self.record_overlay_handle = 0
        self._schedule_dialog: tk.Toplevel | None = None
        self._schedule_listing: tk.Listbox | None = None
        self._countdown_generation = 0
        self._tray_icon: TrayIcon | None = None
        self._tray_hidden = False
        self._exit_requested = False
        self.closing = False
        self.hotkey_state = {
            key: bool(USER32.GetAsyncKeyState(key) & 0x8000)
            for key in (VK_F2, VK_F6, VK_F7, VK_ESCAPE)
        }

        self._build_ui()
        self.window_coordinator = WindowCoordinator(self.root)
        self.message_window_handle = self.window_coordinator.message_window_handle
        self.root_window_handle = self.window_coordinator.root_window_handle
        self._load_active_task()
        self._ensure_tray()
        self.root.after(15, self._drain_ui_events)
        self.root.after(15, self._poll_hotkeys)
        self.root.after(1000, self._poll_schedules)
        self.root.after(500, self._refresh_cursor_readout)
        self.root.after(400, self._maybe_show_onboarding)

    # ------------------------------------------------------------------
    # 界面搭建
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self._build_sidebar()
        main = tk.Frame(self.root, bg=MAIN_BG)
        main.pack(side="left", fill="both", expand=True)
        self._build_header(main)
        self._build_toolbar(main)
        body = tk.Frame(main, bg=MAIN_BG)
        body.pack(fill="both", expand=True, padx=S(36), pady=(S(22), 0))
        self._build_workspace(body)
        self._build_settings(body)
        self._build_statusbar(main)

    def _restore_window_placement(self) -> None:
        """恢复上次窗口位置/尺寸；记录缺失或不可信（显示器已拔等）时回退默认最大化。"""
        self.root.geometry(f"{S(1500)}x{S(920)}")
        placement = self._ui_state.get("main_window")
        if not isinstance(placement, dict) or placement.get("zoomed"):
            self.root.state("zoomed")
            return
        match = re.fullmatch(r"(\d+)x(\d+)([+-]\d+)([+-]\d+)", str(placement.get("geometry") or ""))
        if not match:
            self.root.state("zoomed")
            return
        width, height, x, y = (int(value) for value in match.groups())
        if width < S(1180) or height < S(760):
            self.root.state("zoomed")
            return
        center_x, center_y = x + width // 2, y + height // 2
        visible = any(
            work_x <= center_x < work_x + work_w and work_y <= center_y < work_y + work_h
            for work_x, work_y, work_w, work_h in monitor_workareas()
        )
        if visible:
            self.root.geometry(f"{width}x{height}{x:+d}{y:+d}")
        else:
            self.root.state("zoomed")

    def _persist_window_placement(self) -> None:
        try:
            zoomed = self.root.state() == "zoomed"
            self._ui_state["main_window"] = {
                "zoomed": zoomed,
                "geometry": None if zoomed else self.root.geometry(),
            }
            self.repository.save_ui_state(self._ui_state)
        except (OSError, tk.TclError):
            pass

    def _build_sidebar(self) -> None:
        sidebar = tk.Frame(self.root, width=S(310), bg=SIDEBAR_BG, highlightthickness=1, highlightbackground=BORDER)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        brand = tk.Frame(sidebar, bg=SIDEBAR_BG)
        brand.pack(fill="x", padx=S(24), pady=(S(24), 0))
        mark = tk.Canvas(brand, width=S(56), height=S(56), bg=SIDEBAR_BG, highlightthickness=0)
        mark.pack(side="left")
        self._brand_icon_photo = ImageTk.PhotoImage(brand_icon_image(56))
        mark.create_image(S(28), S(28), image=self._brand_icon_photo)
        brand_text = tk.Frame(brand, bg=SIDEBAR_BG)
        brand_text.pack(side="left", padx=(S(12), 0))
        tk.Label(brand_text, text="点点 · 桌面连点器", bg=SIDEBAR_BG, fg=INK, font=F(17, "bold")).pack(anchor="w")
        tk.Label(brand_text, text="让重复操作变得更简单", bg=SIDEBAR_BG, fg=GRAY, font=F(11)).pack(anchor="w", pady=(S(3), 0))
        tk.Label(brand_text, text="By Zhgui", bg=SIDEBAR_BG, fg=ICON_MUTED, font=F(10)).pack(anchor="w", pady=(S(2), 0))
        self.tasks_item = PillButton(sidebar, "我的任务", self._toggle_task_list, width=S(272), height=S(44), radius=S(10), bg=SIDEBAR_BG, hover_bg=ROW_HOVER, fg=INK, icon="tasks", icon_color=INK_SOFT, font=F(13), align="left", trailing="chevron-up")
        self.tasks_item.pack(padx=S(18), pady=(S(30), S(4)))
        # 底栏固定在侧边栏底部，任务再多也不会把它顶出可视区
        footer = tk.Frame(sidebar, bg=SIDEBAR_BG)
        footer.pack(side="bottom", fill="x")
        shortcuts = tk.Canvas(footer, width=S(260), height=S(152), bg=SIDEBAR_BG, highlightthickness=0)
        shortcuts.pack(side="bottom", padx=S(24), pady=(0, S(24)))
        rounded_rect(shortcuts, 0, 0, S(259), S(151), S(12), fill=PANEL_BG, outline="")
        for index, (key, action) in enumerate((("F2", "捕获位置"), ("F6", "运行 / 停止"), ("F7", "暂停 / 继续"), ("Esc", "紧急停止"))):
            y = S(18) + index * S(31)
            shortcuts.create_text(S(20), y + S(8), text=key, anchor="w", fill=ICON_MUTED, font=M(11))
            shortcuts.create_text(S(62), y + S(8), text=action, anchor="w", fill=TOOLBAR_FG, font=F(11))
        task_actions = tk.Frame(footer, bg=SIDEBAR_BG)
        task_actions.pack(side="bottom", fill="x", padx=S(24), pady=(0, S(16)))
        PillButton(task_actions, "复制", self.duplicate_task, width=S(76), height=S(30), radius=S(8), bg=CARD_BG, fg=INK_SOFT, border=FIELD_BORDER, hover_bg=NEUTRAL_HOVER, font=F(12)).pack(side="left")
        PillButton(task_actions, "删除", self.delete_task, width=S(76), height=S(30), radius=S(8), bg=CARD_BG, fg=DANGER_FG, border=FIELD_BORDER, hover_bg=DANGER_BG, font=F(12)).pack(side="left", padx=(S(6), 0))
        self.trash_button = PillButton(task_actions, "回收站", self.show_trash, width=S(96), height=S(30), radius=S(8), bg=CARD_BG, fg=INK_SOFT, border=FIELD_BORDER, hover_bg=NEUTRAL_HOVER, font=F(12))
        self.trash_button.pack(side="left", padx=(S(6), 0))
        self.new_button = PillButton(footer, "新建任务", self.new_task, width=S(260), height=S(46), radius=S(10), bg=GREEN, fg=ON_COLOR_FG, hover_bg=GREEN_HOVER, icon="plus", icon_color=ON_COLOR_FG, font=F(14, "bold"))
        self.new_button.pack(side="bottom", padx=S(24), pady=(S(16), S(8)))
        self.schedule_button = PillButton(footer, "定时任务", self.show_schedules, width=S(260), height=S(34), radius=S(9), bg=CARD_BG, fg=INK_SOFT, border=FIELD_BORDER, hover_bg=NEUTRAL_HOVER, font=F(12, "bold"))
        self.schedule_button.pack(side="bottom", padx=S(24), pady=(0, S(8)))
        # 中间区域只放任务列表，超出时滚动
        self.task_scroll = tk.Canvas(sidebar, bg=SIDEBAR_BG, highlightthickness=0)
        self.task_scroll.pack(fill="both", expand=True, pady=(S(4), S(8)))
        self.task_column = tk.Frame(self.task_scroll, bg=SIDEBAR_BG)
        self.task_window = self.task_scroll.create_window((0, 0), window=self.task_column, anchor="nw")
        self.task_column.bind("<Configure>", lambda _event: self.task_scroll.configure(scrollregion=self.task_scroll.bbox("all")))
        self.task_scroll.bind("<Configure>", lambda event: self.task_scroll.itemconfigure(self.task_window, width=event.width))
        self.task_scroll.bind("<MouseWheel>", self._on_task_scroll)
        ThinScrollbar(sidebar, self.task_scroll)
        self._render_task_list()

    def _build_header(self, parent: tk.Frame) -> None:
        header = tk.Frame(parent, height=S(48), bg=HEADER_BG, highlightthickness=1, highlightbackground=BORDER)
        header.pack(fill="x")
        header.pack_propagate(False)
        crumb = tk.Frame(header, bg=HEADER_BG)
        crumb.place(x=S(36), rely=0.5, anchor="w")
        tk.Label(crumb, text="我的任务", bg=HEADER_BG, fg=GRAY, font=F(12)).pack(side="left")
        tk.Label(crumb, text="/", bg=HEADER_BG, fg=BORDER_SOFT, font=F(12)).pack(side="left", padx=S(14))
        self.breadcrumb_task = tk.Label(crumb, text="桌面连点", bg=HEADER_BG, fg=INK, font=F(12, "bold"))
        self.breadcrumb_task.pack(side="left")
        theme_button = PillButton(
            header, "深色模式" if THEME_NAME == "light" else "浅色模式", self._toggle_theme,
            width=S(96), height=S(30), radius=S(8), bg=HEADER_BG, fg=INK_SOFT,
            border=BORDER, hover_bg=NEUTRAL_HOVER, font=F(11, "bold"),
        )
        theme_button.place(relx=1.0, x=-S(28), rely=0.5, anchor="e")

    def _toggle_theme(self) -> None:
        """切换浅/深主题：写入偏好后重启进程生效（调色板在导入期定色）。"""
        target = "dark" if THEME_NAME == "light" else "light"
        self._ui_state["theme"] = target
        try:
            self.repository.save_ui_state(self._ui_state)
        except OSError as error:
            self.show_toast(f"主题保存失败：{error}", kind="error")
            return
        label = "深色" if target == "dark" else "浅色"
        if not self._ask_confirm("切换主题", f"已选择{label}模式，重启点点后生效。\n\n现在就重启吗？", confirm_text="重启"):
            return
        self._persist_window_placement()
        if getattr(sys, "frozen", False):
            command = [sys.executable]
        else:
            command = [sys.executable, os.path.abspath(__file__)]
        # 先释放单实例互斥体再拉起新进程：旧进程退出与新进程启动的先后不可控，
        # 不释放会让新实例误判双开而直接退出
        release_mutex()
        try:
            subprocess.Popen(command)
        except OSError:
            self._set_status("自动重启失败，请手动重启点点")
            return
        self._exit_requested = True
        self.close()  # 复用完整收尾：停运行/录制、保存任务与计划、移除托盘图标

    def _build_toolbar(self, parent: tk.Frame) -> None:
        hero = tk.Frame(parent, bg=MAIN_BG)
        hero.pack(fill="x", padx=S(36), pady=(S(18), 0))
        bolt = tk.Canvas(hero, width=S(31), height=S(32), bg=MAIN_BG, highlightthickness=0)
        bolt.pack(side="left", padx=(S(1), S(10)))
        paint_icon(bolt, "bolt", S(15), S(16), S(29), GREEN, MAIN_BG)
        copy = tk.Frame(hero, bg=MAIN_BG)
        copy.pack(side="left")
        self.title_label = tk.Label(copy, text="快速连点", bg=MAIN_BG, fg=INK, font=F(19, "bold"))
        self.title_label.pack(anchor="w")
        self.subtitle_label = tk.Label(copy, text="自定义点击位置与间隔，轻松实现自动连点", bg=MAIN_BG, fg=GRAY, font=F(14))
        self.subtitle_label.pack(anchor="w", pady=(S(3), 0))
        bar = tk.Frame(parent, bg=MAIN_BG)
        bar.pack(fill="x", padx=S(36), pady=(S(17), 0))
        Select(bar, self.mode_var, ("单点连点", "多点任务", "录制操作"), width=S(108), height=S(38), command=self._mode_changed, font=F(15, "bold")).pack(side="left")
        self.capture_button = PillButton(bar, "捕获位置 (F2)", self.arm_capture, width=S(115), height=S(38), radius=S(7), bg=CARD_BG, fg=GREEN_TEXT, border=FIELD_BORDER, hover_bg=NEUTRAL_HOVER, font=F(14, "bold"))
        self.capture_button.pack(side="left", padx=(S(10), 0))
        self.clear_position_button = PillButton(bar, "清除位置", self.clear_position, width=S(84), height=S(38), radius=S(7), bg=CARD_BG, fg=DANGER_FG, border=FIELD_BORDER, hover_bg=DANGER_SOFT_BG, font=F(14, "bold"))
        self.clear_position_button.pack(side="left", padx=(S(6), 0))
        self.record_button = PillButton(bar, "开始录制", self.toggle_recording, width=S(84), height=S(38), radius=S(7), bg=CARD_BG, fg=INK_SOFT, border=FIELD_BORDER, hover_bg=NEUTRAL_HOVER, font=F(14, "bold"))
        self.record_button.pack(side="left", padx=(S(10), 0))
        self.save_button = PillButton(bar, "保存任务", self.save_task, width=S(106), height=S(35), radius=S(7), bg=CARD_BG, fg=INK, border=FIELD_BORDER, hover_bg=NEUTRAL_HOVER, icon="floppy", icon_color=INK_SOFT, font=F(12, "bold"), icon_size=S(14))
        self.save_button.pack(side="right", padx=(S(6), 0))
        self.export_button = PillButton(bar, "导出", self.export_task, width=S(72), height=S(35), radius=S(7), bg=CARD_BG, fg=INK, border=FIELD_BORDER, hover_bg=NEUTRAL_HOVER, font=F(12, "bold"))
        self.export_button.pack(side="right", padx=(S(6), 0))
        self.import_button = PillButton(bar, "导入", self.import_task, width=S(72), height=S(35), radius=S(7), bg=CARD_BG, fg=INK, border=FIELD_BORDER, hover_bg=NEUTRAL_HOVER, font=F(12, "bold"))
        self.import_button.pack(side="right", padx=(S(6), 0))
        self.run_button = PillButton(bar, "开始运行 (F6)", self.toggle_run, width=S(150), height=S(35), radius=S(7), bg=GREEN, fg=ON_COLOR_FG, hover_bg=GREEN_HOVER, icon="play", icon_color=ON_COLOR_FG, font=F(12, "bold"), icon_size=S(14))
        self.run_button.pack(side="right", padx=(0, S(10)))

    def _build_workspace(self, parent: tk.Frame) -> None:
        card = RoundedCard(parent, radius=S(20))
        card.pack(side="left", fill="both", expand=True, padx=(0, S(14)))
        body = card.body
        head = tk.Frame(body, bg=CARD_BG)
        head.pack(fill="x", padx=S(26), pady=(S(22), 0))
        pin = tk.Canvas(head, width=S(18), height=S(18), bg=CARD_BG, highlightthickness=0)
        pin.pack(side="left")
        paint_icon(pin, "pin", S(9), S(9), S(16), GREEN, CARD_BG)
        self.workspace_kind = tk.Label(head, text="定位工作区", bg=CARD_BG, fg=INFO_BLUE, font=F(12, "bold"))
        self.workspace_kind.pack(side="left", padx=(S(8), 0))
        self.workspace_heading = tk.Label(body, text="在真实桌面上捕获位置", bg=CARD_BG, fg=INK, font=F(22, "bold"))
        self.workspace_heading.pack(anchor="w", padx=S(26), pady=(S(10), 0))
        self.workspace_desc = tk.Label(body, text="按 F2 或点击下方预览区捕获，捕获时点点会暂时隐藏", bg=CARD_BG, fg=GRAY, font=F(13))
        self.workspace_desc.pack(anchor="w", padx=S(26), pady=(S(6), 0))
        # 底部信息卡先占住卡片底沿，任何模式下都不会被后续内容顶出
        self.readout_frame = tk.Frame(body, bg=CARD_BG)
        self.readout_frame.pack(side="bottom", fill="x", padx=S(26), pady=(S(12), 0))
        self.readout_coord, self.readout_coord_title = self._readout_item(self.readout_frame, "crosshair", "坐标位置")
        self._readout_divider(self.readout_frame)
        self.readout_window, self.readout_window_title = self._readout_item(self.readout_frame, "windows", "目标窗口")
        self._readout_divider(self.readout_frame)
        self.readout_action, self.readout_action_title = self._readout_item(self.readout_frame, "target", "点击方式")
        # 预览区画布：单点模式占满中间，多点/录制模式贴底显示截图
        self.workspace = tk.Canvas(body, bg=TOOLBAR_BG, highlightthickness=0, relief="flat", cursor="hand2")
        self.workspace.bind("<Button-1>", self._workspace_click)
        self.workspace.bind("<Configure>", self._schedule_preview_redraw)
        # 执行路径固定高度（4 行列表，超出在列表内滚动），出现时由预览区让高
        self.path_panel = tk.Frame(body, bg=CARD_BG)
        path_head = tk.Frame(self.path_panel, bg=CARD_BG)
        path_head.pack(fill="x", pady=(0, S(6)))
        self.steps_heading = tk.Label(path_head, text="执行路径", bg=CARD_BG, fg=INK, font=F(12, "bold"))
        self.steps_heading.pack(side="left")
        self.step_toolbar = tk.Frame(path_head, bg=CARD_BG)
        self.step_toolbar.pack(side="right")
        toolbar_actions = (
            ("位置", self.arm_capture), ("等待", self.add_wait_step), ("滚轮", self.add_scroll_step), ("按键", self.add_key_step), ("文字", self.add_text_step), ("图像", self.start_image_capture),
            ("测试", self.test_step), ("复制", self.duplicate_step), ("↑", lambda: self.move_step(-1)), ("↓", lambda: self.move_step(1)),
            ("启/停", self.toggle_step_enabled), ("删除", self.delete_step), ("清空", self.clear_path), ("撤销", self.undo_last),
        )
        for label, command in toolbar_actions:
            text_width = text_font(F(11)).measure(label)
            PillButton(self.step_toolbar, label, command, width=text_width + S(14), height=S(32), radius=S(7), bg=TOOLBAR_BG, fg=TOOLBAR_FG, hover_bg=TOOLBAR_HOVER, font=F(11)).pack(side="left", padx=(0, S(2)))
        self.step_list = tk.Listbox(self.path_panel, height=1, selectmode=tk.EXTENDED, bg=LIST_BG, fg=TOOLBAR_FG, selectbackground=PILL_ACTIVE, selectforeground=GREEN_TEXT, highlightthickness=1, highlightbackground=LIST_BORDER, relief="flat", font=M(12))
        self.step_list.pack(fill="both", expand=True, pady=(S(12), S(12)))
        self.step_list.bind("<Double-Button-1>", self.edit_step)
        self.step_list.bind("<Delete>", lambda _event: self.delete_step())
        self.step_list.bind("<ButtonPress-1>", self._step_drag_start)
        self.step_list.bind("<ButtonRelease-1>", self._step_drag_end)
        self.step_list.bind("<<ListboxSelect>>", self._update_step_readout)
        self.step_list.bind("<Button-3>", self._show_step_context_menu)
        ThinScrollbar(self.path_panel, self.step_list)
        self.step_hint = tk.Label(self.path_panel, text="双击编辑 · Del 删除 · 拖动排序 · 右键更多", bg=CARD_BG, fg=GRAY, font=F(11), anchor="w")
        self.step_hint.pack(fill="x", pady=(0, S(10)))
        self._build_step_context_menu()
        # 任务列表快捷菜单全程复用同一实例，配合 ContextMenu 的单例互斥保证永不叠加
        self._task_menu = ContextMenu(self.root)
        self._task_menu_tag: int | None = None

    def _build_step_context_menu(self) -> None:
        """步骤列表右键快捷菜单（自定义圆角菜单）。"""
        menu = ContextMenu(self.root)
        menu.add_command("添加位置 (F2)", self.arm_capture, icon="crosshair")
        menu.add_command("添加等待", self.add_wait_step, icon="clock")
        menu.add_command("添加滚轮", self.add_scroll_step, icon="scroll")
        menu.add_command("添加按键", self.add_key_step, icon="keyboard")
        menu.add_command("添加文字", self.add_text_step, icon="type")
        menu.add_command("添加图像点击", self.start_image_capture, icon="image")
        menu.add_command("添加如果图像", self.add_if_image_step, icon="branch")
        menu.add_separator()
        menu.add_command("编辑步骤", self.edit_step, icon="edit")
        menu.add_command("复制步骤 (Ctrl+D)", self.duplicate_step, icon="copy")
        menu.add_command("启用/停用", self.toggle_step_enabled, icon="toggle")
        menu.add_separator()
        menu.add_command("删除选中 (Del)", self.delete_step, icon="trash", danger=True)
        menu.add_command("清空路径", self.clear_path, icon="broom")
        self._step_context_menu = menu

    def _show_step_context_menu(self, event) -> str:
        if not self._editing_allowed():
            return ""
        self._step_context_menu.popup(event.x_root, event.y_root,
                                      workarea=monitor_workarea_at(event.x_root, event.y_root))
        return "break"  # 阻止冒泡到主窗口的“点外收起”绑定，避免菜单被同一次右键关闭

    _ONBOARDING_STEPS = [
        ("选择模式", "顶部可切换「单点连点」「多点任务」「录制操作」三种模式。\n单点适合固定位置连点，多点适合按顺序执行多个动作。", "下一步"),
        ("捕获位置", "按 F2 或点击预览区的大按钮捕获鼠标当前位置。\n捕获时点点窗口会暂时隐藏，点击目标位置即可。", "下一步"),
        ("开始运行", "设置好点击间隔和次数后，按 F6 或点击「开始运行」启动。\n运行中按 F7 暂停/继续，再按 F6 停止。", "开始使用"),
    ]

    def _maybe_show_onboarding(self) -> None:
        """首次启动时显示三步引导，已引导过则跳过。"""
        flag = Path(self.repository.base_dir) / ".onboarded"
        if flag.exists():
            return
        self._show_onboarding()

    def _show_onboarding(self) -> None:
        """显示首次启动引导浮层：选模式 → F2捕获 → F6运行。"""
        self._onboarding_step = 0

        dialog = tk.Toplevel(self.root)
        dialog.overrideredirect(True)
        dialog.configure(bg=OVERLAY_BG)
        dialog.minsize(S(460), S(220))
        dialog.maxsize(S(460), S(220))
        self._onboarding_dialog = dialog

        card = tk.Frame(dialog, bg=CARD_BG, padx=S(32), pady=S(20))
        card.pack(fill="both", expand=True, padx=2, pady=2)

        # 进度点
        dots = tk.Frame(card, bg=CARD_BG)
        dots.pack(anchor="w", pady=(0, S(16)))
        self._onboarding_dots = []
        for i in range(3):
            dot = tk.Canvas(dots, width=S(10), height=S(10), bg=CARD_BG, highlightthickness=0)
            dot.pack(side="left", padx=(0, S(6)))
            self._onboarding_dots.append(dot)

        title_label = tk.Label(card, text="", bg=CARD_BG, fg=INK, font=F(18, "bold"), anchor="w")
        title_label.pack(fill="x", pady=(0, S(10)))
        self._onboarding_title = title_label

        desc_label = tk.Label(card, text="", bg=CARD_BG, fg=GRAY, font=F(13), justify="left", anchor="w", wraplength=S(392))
        desc_label.pack(fill="x", pady=(0, S(24)))
        self._onboarding_desc = desc_label

        btn_row = tk.Frame(card, bg=CARD_BG)
        btn_row.pack(fill="x")

        skip_btn = tk.Button(btn_row, text="跳过", bg=CARD_BG, fg=GRAY, font=F(12), relief="flat", cursor="hand2",
                             command=self._finish_onboarding)
        skip_btn.pack(side="left")

        next_btn = tk.Button(btn_row, text="", bg=GREEN, fg=ON_COLOR_FG, font=F(12, "bold"), relief="flat", cursor="hand2",
                             padx=S(20), pady=S(6), command=self._onboarding_next)
        next_btn.pack(side="right")
        self._onboarding_next_btn = next_btn

        self._onboarding_steps = self._ONBOARDING_STEPS
        self._update_onboarding_step()

        # 居中显示（固定大小）
        dialog.update_idletasks()
        w, h = S(460), S(220)
        x = self.root.winfo_x() + (self.root.winfo_width() - w) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - h) // 2
        dialog.geometry(f"{w}x{h}+{x}+{y}")


    def _update_onboarding_step(self) -> None:
        idx = self._onboarding_step
        title, desc, btn_text = self._onboarding_steps[idx]
        self._onboarding_title.configure(text=f"{idx + 1}/3  {title}")
        self._onboarding_desc.configure(text=desc)
        self._onboarding_next_btn.configure(text=btn_text)
        for i, dot in enumerate(self._onboarding_dots):
            dot.delete("all")
            color = GREEN if i <= idx else BORDER_SOFT
            dot.create_oval(0, 0, S(10), S(10), fill=color, outline="")

    def _onboarding_next(self) -> None:
        if self._onboarding_step < len(self._onboarding_steps) - 1:
            self._onboarding_step += 1
            self._update_onboarding_step()
        else:
            self._finish_onboarding()

    def _finish_onboarding(self) -> None:
        """关闭引导浮层并写入已引导标记。"""
        dialog = getattr(self, "_onboarding_dialog", None)
        if dialog is not None:
            try:
                dialog.destroy()
            except Exception:
                pass
            self._onboarding_dialog = None
        try:
            flag = Path(self.repository.base_dir) / ".onboarded"
            flag.write_text("1", encoding="utf-8")
        except OSError:
            pass
        self.show_toast("欢迎使用点点连点器")


    def _readout_item(self, parent: tk.Frame, icon: str, title: str) -> tuple[tk.Label, tk.Label]:
        item = tk.Frame(parent, bg=CARD_BG)
        item.pack(side="left", fill="both", expand=True)
        icon_canvas = tk.Canvas(item, width=S(32), height=S(24), bg=CARD_BG, highlightthickness=0)
        icon_canvas.pack(side="left")
        paint_icon(icon_canvas, icon, S(16), S(15), S(17), GREEN, CARD_BG)
        stack = tk.Frame(item, bg=CARD_BG)
        stack.pack(side="left", padx=(S(8), 0))
        title_label = tk.Label(stack, text=title, bg=CARD_BG, fg=GRAY, font=F(11))
        title_label.pack(anchor="w")
        value = tk.Label(stack, text="—", bg=CARD_BG, fg=INK, font=F(13, "bold"))
        value.pack(anchor="w", pady=(S(2), 0))
        return value, title_label

    @staticmethod
    def _readout_divider(parent: tk.Frame) -> None:
        tk.Frame(parent, width=1, bg=LIST_BORDER).pack(side="left", fill="y", padx=S(6))

    def _build_settings(self, parent: tk.Frame) -> None:
        card = RoundedCard(parent, width=S(450), radius=S(20))
        card.pack(side="right", fill="y")
        body = card.body
        head = tk.Frame(body, bg=CARD_BG)
        head.pack(fill="x", padx=S(26), pady=(S(24), 0))
        gear = tk.Canvas(head, width=S(20), height=S(20), bg=CARD_BG, highlightthickness=0)
        gear.pack(side="left")
        paint_icon(gear, "gear", S(10), S(10), S(17), INK_SOFT, CARD_BG)
        tk.Label(head, text="任务设置", bg=CARD_BG, fg=INK, font=F(16, "bold")).pack(side="left", padx=(S(8), 0))
        form = tk.Frame(body, bg=CARD_BG)
        form.pack(fill="x", padx=S(26), pady=(S(18), S(24)))
        self._field_label(form, "任务名称", "任务在左侧列表中的显示名称，最多 20 个字符。")
        TextField(form, self.task_name_var, width=S(378), height=S(44), maxlength=20).pack(pady=(0, S(14)))
        self._field_label(form, "鼠标动作", "每次点击使用的鼠标按键：\n· 左键单击：最常用，适用绝大多数场景\n· 右键单击：触发目标的右键菜单\n· 中键单击：按下鼠标滚轮，较少使用")
        Select(form, self.button_var, ("左键单击", "右键单击", "中键单击"), width=S(378), height=S(44), icon="cursor", command=self._button_changed).pack(pady=(0, S(14)))
        self._field_label(form, "点击方式", "点击位置的定位方式：\n· 窗口相对：目标窗口移动后，点击位置自动跟随窗口\n· 屏幕坐标：始终点击屏幕上的固定位置")
        Select(form, self.position_var, ("窗口相对", "屏幕坐标"), width=S(378), height=S(44), icon="windows").pack(pady=(0, S(14)))
        self._field_label(form, "点击间隔（秒）", "两次点击之间的等待时间，支持小数（如 0.5）。\n多点与录制任务中，作为每个步骤之间的等待间隔。")
        NumberField(form, self.interval_var, width=S(378), height=S(44), minimum=0.05, maximum=60, step=0.1).pack(pady=(0, S(14)))
        self._field_label(form, "执行次数（0 = 持续运行）", "整套动作重复执行的次数。\n填 0 表示一直运行，直到手动停止。")
        NumberField(form, self.repeat_var, width=S(378), height=S(44), minimum=0, maximum=999999).pack(pady=(0, S(16)))
        countdown_row = tk.Frame(form, bg=CARD_BG)
        countdown_row.pack(fill="x", pady=(0, S(14)))
        CheckBox(countdown_row, "启动前倒计时（秒）", self.countdown_var).pack(side="left")
        HelpIcon(countdown_row, "点击「开始运行」后先倒计时再执行，留出时间把焦点切换到目标窗口。支持小数。").pack(side="left", padx=(S(6), 0))
        NumberField(countdown_row, self.countdown_seconds_var, width=S(56), height=S(26), minimum=0, maximum=10, step=0.5, radius=S(6), compact=True).pack(side="left", padx=(S(12), S(6)))
        random_row = tk.Frame(form, bg=CARD_BG)
        random_row.pack(fill="x", pady=(0, S(4)))
        CheckBox(random_row, "随机间隔", self.random_var).pack(side="left")
        HelpIcon(random_row, "每次等待在设定幅度内随机浮动，模拟真人节奏。\n例：间隔 1 秒、幅度 20% 时，实际间隔约 0.8～1.2 秒。").pack(side="left", padx=(S(6), 0))
        tk.Label(random_row, text="±", bg=CARD_BG, fg=INK_SOFT, font=F(12)).pack(side="left", padx=(S(10), S(4)))
        NumberField(random_row, self.random_percent_var, width=S(56), height=S(26), minimum=0, maximum=100, step=5, radius=S(6), compact=True).pack(side="left")
        tk.Label(random_row, text="%", bg=CARD_BG, fg=INK_SOFT, font=F(12)).pack(side="left", padx=(S(4), 0))

    def _field_label(self, parent: tk.Widget, text: str, help_text: str | None = None) -> None:
        row = tk.Frame(parent, bg=CARD_BG)
        row.pack(anchor="w", pady=(0, S(8)))
        tk.Label(row, text=text, bg=CARD_BG, fg=INK_SOFT, font=F(12)).pack(side="left")
        if help_text:
            HelpIcon(row, help_text).pack(side="left", padx=(S(6), 0), pady=(S(1), 0))

    def _build_statusbar(self, parent: tk.Frame) -> None:
        bar = tk.Frame(parent, bg=MAIN_BG, height=S(31))
        bar.pack(fill="x", padx=S(36), pady=(S(14), S(12)))
        bar.pack_propagate(False)
        self.status_label = tk.Label(bar, text="●  准备就绪", bg=MAIN_BG, fg=GREEN, font=F(12, "bold"), cursor="hand2")
        self.status_label.pack(side="left")
        # 悬停回看最近状态消息；关键调度结果不再一闪而过
        self.status_label.bind("<Enter>", lambda _e: self._schedule_status_history())
        self.status_label.bind("<Leave>", lambda _e: self._hide_status_history())
        self.status_label.bind("<Button-1>", lambda _e: self._show_status_history())
        self.run_info = tk.Label(bar, text="运行状态：未启动", bg=MAIN_BG, fg=GRAY, font=F(11))
        self.run_info.pack(side="right")
        status_dot = tk.Canvas(bar, width=S(14), height=S(14), bg=MAIN_BG, highlightthickness=0)
        status_dot.pack(side="right", padx=(0, S(6)))
        paint_icon(status_dot, "target", S(7), S(7), S(10), ICON_MUTED, MAIN_BG)

    # ------------------------------------------------------------------
    # 预览区绘制
    # ------------------------------------------------------------------

    def _schedule_preview_redraw(self, _event=None) -> None:
        """窗口缩放时 <Configure> 高频触发，延迟合并到 idle 再重绘预览，避免卡顿。"""
        job = getattr(self, "_preview_redraw_job", None)
        if job:
            try:
                self.root.after_cancel(job)
            except tk.TclError:
                pass
        self._preview_redraw_job = self.root.after(120, self._draw_preview)

    def _draw_preview(self) -> None:
        self._preview_redraw_job = None
        if not hasattr(self, "workspace"):
            return
        cv = self.workspace
        width, height = cv.winfo_width(), cv.winfo_height()
        if width < S(120) or height < S(60):
            return
        cv.delete("all")
        self._empty_btn_rect = None
        rounded_rect(cv, 0, 0, width - 1, height - 1, S(12), fill=TOOLBAR_BG, outline="")
        if self._screen_thumb is not None and self._preview_point:
            self._draw_screen_thumb(cv, width, height, self._preview_point)
        else:
            self._draw_screen_placeholder(cv, width, height, self._preview_point)

    def _draw_screen_thumb(self, cv: tk.Canvas, width: float, height: float, point: tuple[int, int, str]) -> None:
        """把捕获瞬间的全屏截图等比缩放铺进预览区，标记落在真实画面上。

        缩放系数按 1/256 向下取整：小幅缩放时渲染尺寸不变，直接复用
        上一档的 PhotoImage，避免每次重采样整幅截图。
        """
        margin = S(14)
        box_w, box_h = width - margin * 2, height - margin * 2
        scale = min(box_w / self._screen_thumb.width, box_h / self._screen_thumb.height)
        quantized = math.floor(scale * 256) / 256
        scale = quantized if quantized > 0 else scale
        draw_w, draw_h = max(1, round(self._screen_thumb.width * scale)), max(1, round(self._screen_thumb.height * scale))
        ox, oy = round((width - draw_w) / 2), round((height - draw_h) / 2)
        self._thumb_display_rect = (ox, oy, draw_w, draw_h)
        self._paint_dot_grid(cv, margin, margin, width - margin, height - margin, S(24), BORDER_SOFT)
        if (draw_w, draw_h) != self._thumb_render_size:
            resized = self._screen_thumb.resize((draw_w, draw_h), Image.Resampling.BILINEAR)
            self._thumb_photo = ImageTk.PhotoImage(resized)
            self._thumb_render_size = (draw_w, draw_h)
        cv.create_image(ox, oy, image=self._thumb_photo, anchor="nw")
        cv.create_rectangle(ox, oy, ox + draw_w, oy + draw_h, outline=BORDER)
        vx, vy = virtual_screen_origin()
        fx = min(.98, max(.02, (point[0] - vx) / self._screen_thumb.width))
        fy = min(.98, max(.02, (point[1] - vy) / self._screen_thumb.height))
        self._draw_marker(cv, ox + fx * draw_w, oy + fy * draw_h, point[2])

    def _draw_screen_placeholder(self, cv: tk.Canvas, width: float, height: float, point: tuple[int, int, str] | None) -> None:
        """空状态：按虚拟屏幕宽高比画显示器轮廓；有落点时标记按比例落在轮廓上。"""
        self._thumb_display_rect = None
        SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79
        virtual_w = max(1, USER32.GetSystemMetrics(SM_CXVIRTUALSCREEN))
        virtual_h = max(1, USER32.GetSystemMetrics(SM_CYVIRTUALSCREEN))
        margin = S(12)
        box_w = min(width - margin * 2, (height - margin * 2) * virtual_w / virtual_h)
        box_h = box_w * virtual_h / virtual_w
        ox, oy = (width - box_w) / 2, (height - box_h) / 2
        self._paint_dot_grid(cv, margin, margin, width - margin, height - margin, S(24), BORDER_SOFT)
        rounded_rect(cv, ox, oy, ox + box_w, oy + box_h, S(10), fill=ROW_HOVER, outline=BORDER)
        if point:
            vx, vy = virtual_screen_origin()
            fx = min(.96, max(.04, (point[0] - vx) / virtual_w))
            fy = min(.96, max(.04, (point[1] - vy) / virtual_h))
            self._draw_marker(cv, ox + fx * box_w, oy + fy * box_h, point[2])
        else:
            btn_w, btn_h = min(S(260), width * 0.6), S(48)
            bx, by = (width - btn_w) / 2, (height - btn_h) / 2
            self._empty_btn_rect = (bx, by, bx + btn_w, by + btn_h)
            rounded_rect(cv, bx, by, bx + btn_w, by + btn_h, S(10), fill=GREEN, outline="")
            cv.create_text(width / 2, height / 2, text="点击捕获第一个位置  F2", fill=ON_COLOR_FG, font=F(13, "bold"))
            cv.create_text(width / 2, by + btn_h + S(18), text="捕获时点点会暂时隐藏，点击目标位置即可", fill=ICON_MUTED, font=F(11))

    def _paint_dot_grid(self, cv: tk.Canvas, x1: float, y1: float, x2: float, y2: float, step: float, color: str) -> None:
        """用一张预渲染 PIL 图贴出点阵，避免 resize 时在 Tk 上创建上百个 oval item。"""
        w, h = int(x2 - x1), int(y2 - y1)
        if w < 2 or h < 2:
            return
        key = (w, h, int(step), color)
        if key != self._dot_photo_key:
            img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            px, py = step / 2, step / 2
            r = max(1, int(step / 12))
            yy = py
            while yy < h:
                xx = px
                while xx < w:
                    draw.ellipse((xx - r, yy - r, xx + r, yy + r), fill=color)
                    xx += step
                yy += step
            self._dot_photo = ImageTk.PhotoImage(img)
            self._dot_photo_key = key
        cv.create_image(x1, y1, image=self._dot_photo, anchor="nw")

    def _draw_marker(self, cv: tk.Canvas, cx: float, cy: float, label: str) -> None:
        """在 (cx, cy) 画十字标记与坐标徽标，位置由调用方按真实屏幕映射计算。"""
        radius = S(8)
        cv.create_oval(cx - radius, cy - radius, cx + radius, cy + radius, outline=GREEN, width=S(2))
        for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
            cv.create_line(cx + dx * radius, cy + dy * radius, cx + dx * (radius + S(4)), cy + dy * (radius + S(4)), fill=GREEN, width=S(2))
        font = text_font(F(10, "bold"))
        box_w, box_h = font.measure(label) + S(16), S(22)
        bx, by = cx + S(14), cy + S(4)
        if bx + box_w > cv.winfo_width() - S(4):
            bx = cx - box_w - S(18)
        if by + box_h > cv.winfo_height() - S(4):
            by = cy - box_h - S(6)
        rounded_rect(cv, bx, by, bx + box_w, by + box_h, box_h / 2, fill=GREEN_DEEP, outline="")
        cv.create_text(bx + box_w / 2, by + box_h / 2, text=label, fill=ON_COLOR_FG, font=font)

    # ------------------------------------------------------------------
    # 任务数据
    # ------------------------------------------------------------------

    def _load_active_task(self) -> None:
        self.undo_stack.clear()
        task = self.tasks[self.active_index]
        self.mode_var.set(task.mode)
        self.task_name_var.set(task.name)
        self.breadcrumb_task.configure(text=task.name)
        self.target = copy.deepcopy(task.target)
        self.steps = copy.deepcopy(task.steps)
        # 缩略图是捕获瞬间的画面，切换任务后不再代表当前目标
        self._clear_preview_point()
        self.button_var.set(task.settings.button)
        self.position_var.set(task.settings.position_mode)
        self.interval_var.set(task.settings.interval_ms / 1000)
        self.repeat_var.set(task.settings.repeat_count)
        self.countdown_var.set(task.settings.countdown_enabled)
        self.countdown_seconds_var.set(task.settings.countdown_seconds)
        self.random_var.set(task.settings.random_interval)
        self.random_percent_var.set(task.settings.random_percent)
        self._update_mode_ui()
        self._render_target()
        self._render_steps()
        self._render_task_list()

    def _render_task_list(self) -> None:
        self.trash_button.configure(text=f"回收站 ({len(self.trash)})" if self.trash else "回收站")
        # 保存当前滚动位置，渲染后恢复，避免选中底部任务时列表跳回顶部
        saved_scroll = self.task_scroll.yview()[0]
        for child in self.task_column.winfo_children():
            child.destroy()
        if self.tasks_expanded:
            for index, task in enumerate(self.tasks):
                active = index == self.active_index
                button = PillButton(
                    self.task_column, task.name,
                    lambda picked=index: self.select_task(picked),
                    width=S(252), height=S(38), radius=S(9),
                    bg=PILL_ACTIVE if active else SIDEBAR_BG, hover_bg=ROW_HOVER,
                    fg=TASK_ACTIVE_FG if active else TASK_FG,
                    icon="bolt" if active else "dot",
                    icon_color=GREEN if active else ICON_MUTED,
                    font=F(12, "bold") if active else F(12),
                    align="left", padx=S(14),
                )
                button.pack(padx=S(28), pady=1)
                button.bind("<MouseWheel>", self._on_task_scroll)
                button.bind("<Button-3>", lambda e, picked=index: self._show_task_context_menu(e, picked))
        self.task_scroll.yview_moveto(saved_scroll)
        self._task_list_signature = self._task_list_fingerprint()

    def _show_task_context_menu(self, event, index: int) -> str:
        """任务列表右键菜单：右键先选中高亮，再弹出圆角快捷菜单。

        返回 "break" 阻止事件冒泡到主窗口的“点外收起”绑定，否则菜单刚弹出就会被同一次右键关闭。
        """
        if self.running:
            self._set_status("任务运行中，停止后才能操作")
            return ""
        if self._task_menu.is_open() and self._task_menu_tag == index:
            self._task_menu.close()  # 再次右键同一任务 = 收起，与系统菜单的开关语义一致
            return "break"
        if index != self.active_index:
            self.select_task(index)
        self._task_menu.clear()
        self._task_menu.add_command("开始此任务", lambda: self.start_run(index), icon="play")
        self._task_menu.add_separator()
        self._task_menu.add_command("删除此任务", lambda: self.delete_task(), icon="trash", danger=True)
        self._task_menu_tag = index
        self._task_menu.popup(event.x_root, event.y_root,
                              workarea=monitor_workarea_at(event.x_root, event.y_root))
        return "break"

    def _task_list_fingerprint(self) -> tuple:
        """决定任务列表内容的因子：展开状态与各任务名称。"""
        return (self.tasks_expanded, tuple(task.name for task in self.tasks))

    def _on_task_scroll(self, event) -> str:
        """任务列表滚轮：只在内容超出可视高度时滚动，避免短列表空转。"""
        self.task_scroll.yview_scroll(-1 if event.delta > 0 else 1, "units")
        return "break"

    def _render_target(self) -> None:
        if self.mode_var.get() != "单点连点":
            self._update_step_readout()
            self._draw_preview()
            return
        self._set_readout_titles(("坐标位置", "目标窗口", "点击方式"))
        if self.target:
            self.readout_coord.configure(text=f"{self.target.x}, {self.target.y}")
            self.readout_window.configure(text=self.target.title[:14])
            self._preview_point = (self.target.x, self.target.y, f"{self.target.x}, {self.target.y}")
        else:
            self.readout_coord.configure(text="—")
            self.readout_window.configure(text="—")
            self._preview_point = None
        self.readout_action.configure(text=self.button_var.get())
        self._draw_preview()

    def _render_steps(self) -> None:
        prev = self._selected_step_index()
        self.step_list.delete(0, tk.END)
        if self.steps:
            # 一次 Tcl 调用批量插入：录制时每 60ms 全量重建，逐条插入会随步骤数明显变卡
            rows = [
                f"{'✓' if step.enabled else '○'} {index:02d}【{step.action_name}】 {step.target_summary} · 等 {step.wait_ms}ms"
                for index, step in enumerate(self.steps, 1)
            ]
            self.step_list.insert(tk.END, *rows)
            target = prev if prev is not None and prev < len(self.steps) else len(self.steps) - 1
            self.step_list.selection_clear(0, tk.END)
            self.step_list.selection_set(target)
            self.step_list.see(target)
        self._update_step_readout()

    def _update_step_readout(self, *_args) -> None:
        """多点/录制模式下，信息卡与验证预览跟随选中的步骤。"""
        if self.mode_var.get() == "单点连点":
            return
        index = self._selected_step_index()
        if index is None or index >= len(self.steps):
            self._set_readout_titles(("坐标位置", "执行路径", "点击方式"))
            self.readout_coord.configure(text="—")
            self.readout_window.configure(text=f"共 {len(self.steps)} 步")
            self.readout_action.configure(text="—")
            self._clear_preview_point()
            self._draw_preview()
            return
        step = self.steps[index]
        self._set_readout_titles(STEP_READOUT_LABELS.get(step.type, ("坐标位置", "目标窗口", "点击方式")))
        self._clear_preview_point()
        if step.type in {"click", "scroll"}:
            coord, window = f"{step.x}, {step.y}", step.title[:14]
            self._preview_point = (step.x, step.y, coord)
            if step.id not in self._step_thumbs:
                loaded = self._load_thumb(step.id)
                if loaded is not None:
                    self._remember_thumb(step.id, loaded)
            self._screen_thumb = self._step_thumbs.get(step.id)
        elif step.type in {"key_down", "key_up"}:
            coord, window = step.key_name or f"VK {step.key_code}", "—"
        elif step.type == "image_click":
            coord, window = "图像定位", step.template_file[:14] or "未设置模板"
        elif step.type == "wait":
            coord, window = f"{step.wait_ms} ms", "—"
        elif step.type == "type_text":
            display = step.text if len(step.text) <= 20 else step.text[:20] + "…"
            coord, window = display or "(空)", "—"
        else:
            coord, window = "—", "—"
        self.readout_coord.configure(text=coord)
        self.readout_window.configure(text=window)
        self.readout_action.configure(text=step.action_name)
        self._draw_preview()

    def _set_readout_titles(self, titles: tuple[str, str, str]) -> None:
        self.readout_coord_title.configure(text=titles[0])
        self.readout_window_title.configure(text=titles[1])
        self.readout_action_title.configure(text=titles[2])

    def _clear_preview_point(self) -> None:
        self._preview_point = None
        self._screen_thumb = None
        self._thumb_render_size = None

    def _selected_step_index(self) -> int | None:
        selection = self.step_list.curselection()
        return int(selection[0]) if selection else None

    def add_wait_step(self) -> None:
        if not self._editing_allowed():
            return
        self._push_undo_state()
        wait_ms = self._ask_input_dialog("添加等待", "等待时间（毫秒）", initial="500", input_type="int", minvalue=0, maxvalue=3_600_000)
        if wait_ms is None:
            return
        self.steps.append(Step(type="wait", wait_ms=wait_ms))
        self._render_steps()
        self.save_task(silent=True)
        self._set_status("已添加等待步骤")
        self.show_toast("已添加等待步骤")

    def add_scroll_step(self) -> None:
        if not self._editing_allowed():
            return
        self._push_undo_state()
        delta = self._ask_input_dialog("添加滚轮", "滚动量（正数向上，负数向下，120 为一格）", initial="-120", input_type="int", minvalue=-12000, maxvalue=12000)
        if delta is None:
            return
        self.steps.append(Step(type="scroll", wait_ms=self._interval_ms(), scroll_delta=delta))
        self._render_steps()
        self.save_task(silent=True)
        self._set_status("已添加滚轮步骤")
        self.show_toast("已添加滚轮步骤")

    def add_key_step(self) -> None:
        if not self._editing_allowed():
            return
        self._push_undo_state()
        picked = self._ask_key_dialog("添加按键")
        if picked is None:
            return
        code, name = picked
        tap = Step(type="key_down", wait_ms=self._interval_ms(), key_code=code, key_name=name)
        release = Step(type="key_up", wait_ms=30, key_code=code, key_name=name)
        self.steps.extend((tap, release))
        self._render_steps()
        self.save_task(silent=True)
        self._set_status(f"已添加按键 {name}（按下 + 松开）")
        self.show_toast(f"已添加按键 {name}")

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
        body = tk.Frame(top, bg=MAIN_BG, padx=S(20), pady=S(16))
        body.pack(fill="both", expand=True)
        tk.Label(body, text="选择按键（双击直接确认）：", bg=MAIN_BG, fg=INK, font=F(13)).pack(anchor="w")
        list_frame = tk.Frame(body, bg=CARD_BG, highlightthickness=1, highlightbackground=FIELD_BORDER)
        list_frame.pack(fill="both", expand=True, pady=(S(8), S(12)))
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
        PillButton(btn_row, "自定义键码", pick_custom, width=S(96), height=S(34), radius=S(8),
                   bg=CARD_BG, fg=INK, border=FIELD_BORDER, hover_bg=NEUTRAL_HOVER, font=F(12)).pack(side="left")
        PillButton(btn_row, "确定", confirm, width=S(80), height=S(34), radius=S(8),
                   bg=GREEN, fg=ON_COLOR_FG, hover_bg=GREEN_HOVER, font=F(12, "bold")).pack(side="right")
        PillButton(btn_row, "取消", cancel, width=S(80), height=S(34), radius=S(8),
                   bg=CARD_BG, fg=INK, border=FIELD_BORDER, hover_bg=NEUTRAL_HOVER, font=F(12)).pack(side="right", padx=(0, S(8)))
        key_list.bind("<Double-Button-1>", confirm)
        top.bind("<Escape>", cancel)
        top.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() - top.winfo_width()) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - top.winfo_height()) // 3
        top.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.root.wait_window(top)
        return result["value"]

    def _center_dialog(self, dialog: tk.Toplevel) -> None:
        """将对话框居中到主窗口（延迟执行，确保布局完成后尺寸正确）。"""
        def do_center() -> None:
            if not dialog.winfo_exists():
                return
            dialog.update_idletasks()
            w, h = dialog.winfo_width(), dialog.winfo_height()
            x = self.root.winfo_rootx() + (self.root.winfo_width() - w) // 2
            y = self.root.winfo_rooty() + (self.root.winfo_height() - h) // 2
            dialog.geometry(f"+{max(0, x)}+{max(0, y)}")
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
        body = tk.Frame(top, bg=MAIN_BG, padx=S(24), pady=S(20))
        body.pack(fill="both", expand=True)
        tk.Label(body, text=label, bg=MAIN_BG, fg=INK, font=F(13)).pack(anchor="w")
        entry = tk.Entry(body, width=36, font=F(13), bg=CARD_BG, fg=INK, relief="flat", highlightthickness=1, highlightbackground=FIELD_BORDER, highlightcolor=GREEN)
        entry.pack(fill="x", pady=(S(10), S(6)), ipady=S(8))
        entry.insert(0, str(initial))
        entry.select_range(0, tk.END)
        entry.focus_set()
        error_label = tk.Label(body, text="", bg=MAIN_BG, fg=DANGER_FG, font=F(11))
        error_label.pack(anchor="w", pady=(0, S(12)))
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
        PillButton(btn_row, "确定", confirm, width=S(88), height=S(36), radius=S(8), bg=GREEN, fg=ON_COLOR_FG, hover_bg=GREEN_HOVER, font=F(13, "bold")).pack(side="right")
        PillButton(btn_row, "取消", cancel, width=S(88), height=S(36), radius=S(8), bg=CARD_BG, fg=INK, border=FIELD_BORDER, hover_bg=NEUTRAL_HOVER, font=F(13)).pack(side="right", padx=(0, S(10)))
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
        body = tk.Frame(top, bg=MAIN_BG, padx=S(24), pady=S(20))
        body.pack(fill="both", expand=True)
        width = S(420)
        for line in message.splitlines():
            tk.Label(body, text=line or " ", bg=MAIN_BG, fg=INK, font=F(13), wraplength=width - S(60), justify="left").pack(anchor="w", pady=(0, S(4)))
        btn_row = tk.Frame(body, bg=MAIN_BG)
        btn_row.pack(fill="x", pady=(S(14), 0))

        def confirm(_e=None):
            result["confirmed"] = True
            top.destroy()

        def cancel(_e=None):
            top.destroy()

        PillButton(btn_row, confirm_text, confirm, width=S(88), height=S(36), radius=S(8),
                   bg=RUNNING_BG if danger else GREEN, fg=ON_COLOR_FG,
                   hover_bg=shade(RUNNING_BG, 0.9) if danger else GREEN_HOVER, font=F(13, "bold")).pack(side="right")
        PillButton(btn_row, cancel_text, cancel, width=S(88), height=S(36), radius=S(8),
                   bg=CARD_BG, fg=INK, border=FIELD_BORDER, hover_bg=NEUTRAL_HOVER, font=F(13)).pack(side="right", padx=(0, S(10)))
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

    def add_text_step(self) -> None:
        if not self._editing_allowed():
            return
        self._push_undo_state()
        text = self._ask_text_dialog("输入文字", "要输入的内容（支持中英文）：")
        if text is None:
            return
        step = Step(type="type_text", wait_ms=self._interval_ms(), text=text)
        self.steps.append(step)
        self._render_steps()
        self.save_task(silent=True)
        self._set_status("已添加文字输入步骤")
        self.show_toast("已添加文字输入步骤")

    def duplicate_step(self) -> None:
        if not self._editing_allowed():
            return
        selected = sorted(int(value) for value in self.step_list.curselection())
        if not selected:
            return
        self._push_undo_state()
        # 倒序插入，避免索引偏移
        new_indices = []
        for index in reversed(selected):
            clone = self.steps[index].clone()
            self.steps.insert(index + 1, clone)
            new_indices.append(index + 1)
        self._render_steps()
        self.step_list.selection_clear(0, tk.END)
        for index in new_indices:
            self.step_list.selection_set(index)
        self.save_task(silent=True)
        self._set_status(f"已复制 {len(selected)} 个步骤")
        self.show_toast(f"已复制 {len(selected)} 个步骤")

    def test_step(self) -> None:
        if not self._editing_allowed():
            return
        index = self._selected_step_index()
        if index is None:
            return
        step = copy.deepcopy(self.steps[index])
        self.root.withdraw()
        self.root.update()

        def execute() -> None:
            try:
                time.sleep(0.2)
                self._execute_step(step)
                self.ui_events.put((self._set_status, (f"步骤 {index + 1} 测试完成",)))
            except Exception as error:
                self.ui_events.put((self._set_status, (f"步骤测试失败：{error}",)))
            finally:
                self.ui_events.put((self._restore_main_window, ()))

        threading.Thread(target=execute, name="step-test", daemon=True).start()

    def move_step(self, direction: int) -> None:
        if not self._editing_allowed():
            return
        self._push_undo_state()
        index = self._selected_step_index()
        target = index + direction if index is not None else -1
        if index is None or target < 0 or target >= len(self.steps):
            return
        self.steps[index], self.steps[target] = self.steps[target], self.steps[index]
        self._render_steps()
        self.step_list.selection_clear(0, tk.END)
        self.step_list.selection_set(target)
        self.step_list.see(target)
        self._update_step_readout()
        self.save_task(silent=True)
        self._set_status("已移动步骤")
        self.show_toast("已移动步骤")

    def toggle_step_enabled(self) -> None:
        if not self._editing_allowed():
            return
        selected = sorted(int(value) for value in self.step_list.curselection())
        if not selected:
            return
        self._push_undo_state()
        for index in selected:
            self.steps[index].enabled = not self.steps[index].enabled
        self._render_steps()
        for index in selected:
            if index < len(self.steps):
                self.step_list.selection_set(index)
        self.save_task(silent=True)
        self._set_status(f"已切换 {len(selected)} 个步骤的启用状态")
        self.show_toast(f"已切换 {len(selected)} 个步骤的启用状态")

    def delete_step(self) -> None:
        if not self._editing_allowed():
            return
        selected = sorted((int(value) for value in self.step_list.curselection()), reverse=True)
        if not selected:
            return
        removed = [(index, self.steps.pop(index)) for index in selected]
        self.undo_stack.append(("steps", removed))
        self._render_steps()
        self.save_task(silent=True)
        self._set_status(f"已删除 {len(removed)} 个步骤，按 Ctrl+Z 撤销")
        self.show_toast(f"已删除 {len(removed)} 个步骤")

    def clear_path(self) -> None:
        if not self._editing_allowed() or not self.steps:
            return
        if not self._ask_confirm("清空路径", f"确定清空当前路径吗？\n将移除全部 {len(self.steps)} 个步骤，任务设置会保留。", danger=True, confirm_text="清空"):
            return
        removed = list(enumerate(self.steps))
        for _idx, step in removed:
            self._delete_thumb(step.id)
            self._step_thumbs.pop(step.id, None)
        self.undo_stack.append(("steps", removed))
        self.steps = []
        self._render_steps()
        self.save_task(silent=True)
        self._set_status("路径已清空，按 Ctrl+Z 撤销")
        self.show_toast("路径已清空")

    def _push_undo_state(self) -> None:
        """修改步骤前调用，把当前步骤列表快照压入撤销栈。"""
        self.undo_stack.append(("snapshot", [copy.deepcopy(step) for step in self.steps]))
        if len(self.undo_stack) > 50:
            self.undo_stack.pop(0)

    def undo_last(self) -> None:
        if not self._editing_allowed() or not self.undo_stack:
            self._set_status("没有可撤销的操作")
            return
        action, payload = self.undo_stack.pop()
        if action == "snapshot":
            self.steps = payload
            self._render_steps()
            self._set_status("已撤销上一步操作")
        elif action == "target":
            self.target = payload
            self._render_target()
            self._set_status("已撤销位置清除")
        elif action == "steps":
            for index, step in sorted(payload, key=lambda item: item[0]):
                self.steps.insert(min(index, len(self.steps)), step)
            self._render_steps()
            self._set_status("已撤销步骤删除")
        self.save_task(silent=True)
        self.show_toast("已撤销")

    def edit_step(self, _event=None) -> None:
        if not self._editing_allowed():
            return
        self._push_undo_state()
        index = self._selected_step_index()
        if index is None:
            return
        step = self.steps[index]
        wait_ms = self._ask_input_dialog("编辑步骤", "执行前等待（毫秒）", initial=str(step.wait_ms), input_type="int", minvalue=0, maxvalue=3_600_000)
        if wait_ms is None:
            return
        step.wait_ms = wait_ms
        if step.type in {"click", "scroll"}:
            x = self._ask_input_dialog("编辑位置", "屏幕 X 坐标", initial=str(step.x), input_type="int")
            y = self._ask_input_dialog("编辑位置", "屏幕 Y 坐标", initial=str(step.y), input_type="int")
            if x is not None and y is not None:
                step.x, step.y = x, y
            if step.type == "scroll":
                delta = self._ask_input_dialog("编辑滚轮", "滚动量（正数向上，负数向下）", initial=str(step.scroll_delta), input_type="int", minvalue=-12000, maxvalue=12000)
                if delta is not None:
                    step.scroll_delta = delta
            elif step.type == "click":
                button = self._ask_input_dialog("编辑点击", "鼠标按钮（左 / 右 / 中）", initial=step.button, input_type="str")
                if button in {"左", "右", "中"}:
                    step.button = button
        elif step.type in {"key_down", "key_up"}:
            picked = self._ask_key_dialog("编辑按键", initial_code=step.key_code)
            if picked is not None:
                step.key_code, step.key_name = picked
        elif step.type == "image_click":
            threshold = self._ask_input_dialog("图像定位", "匹配阈值（0.50 - 0.99）", initial=str(step.match_threshold), input_type="float", minvalue=.5, maxvalue=.99)
            if threshold is not None:
                step.match_threshold = threshold
            offset_x = self._ask_input_dialog("图像定位", "点击点相对图像中心 X 偏移", initial=str(step.click_offset_x), input_type="int", minvalue=-10000, maxvalue=10000)
            offset_y = self._ask_input_dialog("图像定位", "点击点相对图像中心 Y 偏移", initial=str(step.click_offset_y), input_type="int", minvalue=-10000, maxvalue=10000)
            if offset_x is not None and offset_y is not None:
                step.click_offset_x, step.click_offset_y = offset_x, offset_y
        elif step.type == "type_text":
            text = self._ask_text_dialog("编辑文字", "要输入的内容（支持中英文）：", step.text)
            if text is not None:
                step.text = text
        self._render_steps()
        self.step_list.selection_set(index)
        self.save_task(silent=True)

    def _step_drag_start(self, event) -> None:
        if self.running:
            return
        self.drag_step_index = self.step_list.nearest(event.y)

    def _step_drag_end(self, event) -> None:
        if self.running or self.drag_step_index is None or not self.steps:
            self.drag_step_index = None
            return
        target = self.step_list.nearest(event.y)
        source = self.drag_step_index
        self.drag_step_index = None
        if source == target or source >= len(self.steps) or target >= len(self.steps):
            return
        step = self.steps.pop(source)
        self.steps.insert(target, step)
        self._render_steps()
        self.step_list.selection_set(target)
        self.save_task(silent=True)

    def _editing_allowed(self) -> bool:
        if self.running:
            self._set_status("任务运行中，停止后才能编辑")
            return False
        return True

    def select_task(self, index: int) -> None:
        if self.running:
            self._set_status("任务运行中，停止后才能切换任务")
            return
        if index == self.active_index:
            return
        self.save_task(silent=True)
        self.active_index = index
        self._load_active_task()

    def _toggle_task_list(self) -> None:
        self.tasks_expanded = not self.tasks_expanded
        self.tasks_item.set_trailing("chevron-up" if self.tasks_expanded else "chevron-down")
        self._render_task_list()
        self.task_scroll.yview_moveto(0)

    def _mode_changed(self, *_args) -> None:
        self._update_mode_ui()
        self.save_task(silent=True)

    def _button_changed(self, *_args) -> None:
        if self.mode_var.get() == "单点连点":
            self.readout_action.configure(text=self.button_var.get())
        else:
            self._update_step_readout()

    def _update_mode_ui(self, *_args) -> None:
        if not hasattr(self, "workspace") or not hasattr(self, "path_panel"):
            return
        mode = self.mode_var.get()
        titles = {
            "单点连点": ("快速连点", "自定义点击位置与间隔，轻松实现自动连点", "在真实桌面上捕获位置", "按 F2 或点击下方预览区捕获，捕获时点点会暂时隐藏"),
            "多点任务": ("多点任务", "按顺序设置多个位置，执行完整操作流程", "按顺序添加多个位置", "按 F2 向路径逐步添加位置，「图像」可框选屏幕模板"),
            "录制操作": ("录制操作", "记录鼠标点击、滚轮、键盘与动作间隔", "记录真实桌面输入", "录制真实键鼠；也可按 F2 或「图像」手动补充步骤"),
        }
        title, subtitle, workspace_title, workspace_desc = titles[mode]
        self.title_label.configure(text=title)
        self.subtitle_label.configure(text=subtitle)
        self.workspace_heading.configure(text=workspace_title)
        self.workspace_desc.configure(text=workspace_desc)
        if mode in {"多点任务", "录制操作"}:
            self.workspace_kind.configure(text="步骤工作区")
            self.workspace.configure(height=S(180))
            # 显式按视觉顺序重新 pack：workspace 贴底，readout 在其上，path_panel 填满中间
            self.workspace.pack_forget()
            self.readout_frame.pack_forget()
            self.path_panel.pack_forget()
            self.workspace.pack(side="bottom", fill="x", padx=S(26), pady=(0, S(12)))
            self.readout_frame.pack(side="bottom", fill="x", padx=S(26), pady=(S(12), S(12)))
            self.path_panel.pack(side="top", fill="both", expand=True, padx=S(26), pady=(S(12), 0))
        else:
            self.workspace_kind.configure(text="定位工作区")
            self.path_panel.pack_forget()
            self.workspace.pack_forget()
            self.readout_frame.pack_forget()
            self.readout_frame.pack(side="bottom", fill="x", padx=S(26), pady=(S(12), S(12)))
            self.workspace.pack(side="top", fill="both", expand=True, padx=S(26), pady=(S(10), S(8)))
        if mode == "单点连点":
            self._render_target()
        else:
            self._update_step_readout()
        if mode == "录制操作":
            self.record_button.configure(state="normal")
        else:
            self.record_button.configure(state="disabled")
            if self.recording:
                self.toggle_recording()

    # ------------------------------------------------------------------
    # 捕获与录制
    # ------------------------------------------------------------------

    def _dismiss_entry_focus(self, event) -> None:
        """点击主窗口空白处时，把焦点从输入框移走，避免光标一直停留在输入框里。"""
        widget = getattr(event, "widget", None)
        if widget is None or not hasattr(widget, "winfo_toplevel"):
            return
        try:
            if widget.winfo_toplevel() is not self.root:
                return
        except Exception:
            return
        # 输入控件本身、下拉框等可交互组件不干预，避免打断其自身焦点/弹层逻辑
        if isinstance(widget, (tk.Entry, tk.Text, tk.Spinbox, Select)):
            return
        self.root.focus_set()

    def _workspace_click(self, event) -> None:
        if self.running:
            return
        # 有截图时点击截图区域显示大图，其余区域触发捕获
        if self._thumb_display_rect is not None and self._screen_thumb is not None:
            ox, oy, dw, dh = self._thumb_display_rect
            if ox <= event.x <= ox + dw and oy <= event.y <= oy + dh:
                self._show_large_preview()
                return
        self.arm_capture()

    def _thumb_path(self, step_id: str) -> Path:
        return self.thumbs_dir / f"{step_id}.png"

    def _remember_thumb(self, step_id: str, image: Image.Image) -> None:
        """缩略图缓存有界（LRU）：录制会话每次点击都产生一张整屏截图，
        不设上限时内存会随步骤数无界增长；磁盘上的原图不受影响。"""
        cache = self._step_thumbs
        cache.pop(step_id, None)
        cache[step_id] = image
        while len(cache) > self._THUMB_CACHE_LIMIT:
            cache.popitem(last=False)

    def _save_thumb(self, step_id: str, image: Image.Image) -> None:
        try:
            image.save(self._thumb_path(step_id), "PNG", optimize=True)
        except OSError:
            pass

    def _load_thumb(self, step_id: str) -> Image.Image | None:
        path = self._thumb_path(step_id)
        if not path.exists():
            return None
        try:
            return Image.open(path).convert("RGB")
        except OSError:
            return None

    def _delete_thumb(self, step_id: str) -> None:
        try:
            self._thumb_path(step_id).unlink(missing_ok=True)
        except OSError:
            pass

    def _show_large_preview(self) -> None:
        """弹出大图窗口展示原始截图，点击或 Esc 关闭。"""
        if self._screen_thumb is None:
            return
        top = tk.Toplevel(self.root)
        top.title("截图预览")
        top.configure(bg=OVERLAY_BG)
        top.attributes("-topmost", True)
        sw, sh = top.winfo_screenwidth(), top.winfo_screenheight()
        max_w, max_h = int(sw * 0.85), int(sh * 0.85)
        img = self._screen_thumb
        scale = min(max_w / img.width, max_h / img.height, 1.0)
        disp_w, disp_h = max(1, round(img.width * scale)), max(1, round(img.height * scale))
        resized = img.resize((disp_w, disp_h), Image.Resampling.BILINEAR) if scale < 1.0 else img
        photo = ImageTk.PhotoImage(resized)
        canvas = tk.Canvas(top, width=disp_w, height=disp_h, bg=OVERLAY_BG, highlightthickness=0, cursor="hand2")
        canvas.pack(padx=S(12), pady=S(12))
        canvas.create_image(0, 0, image=photo, anchor="nw")
        canvas._large_photo = photo
        if self._preview_point:
            vx, vy = virtual_screen_origin()
            fx = min(.98, max(.02, (self._preview_point[0] - vx) / img.width))
            fy = min(.98, max(.02, (self._preview_point[1] - vy) / img.height))
            self._draw_marker(canvas, fx * disp_w, fy * disp_h, self._preview_point[2])
        top.geometry(f"+{(sw - disp_w - S(24)) // 2}+{(sh - disp_h - S(24)) // 2}")
        top.bind("<Escape>", lambda _e: top.destroy())
        canvas.bind("<Button-1>", lambda _e: top.destroy())
        top.focus_set()

    def arm_capture(self) -> None:
        if self.running:
            return
        self.capture_armed = True
        self.capture_button.configure(text="移动鼠标后按 F2", bg=TOOLBAR_HOVER)
        self._set_status("等待捕获位置")
        self._show_capture_overlay()

    def _show_capture_overlay(self) -> None:
        self._hide_capture_overlay(restore_main=False)
        overlay = tk.Toplevel(self.root)
        overlay.overrideredirect(True)
        overlay.configure(bg=OVERLAY_BG)
        overlay.attributes("-topmost", True)
        overlay.geometry(self._overlay_geometry(S(560), S(66)))
        overlay_frame = tk.Frame(overlay, bg=OVERLAY_BG, padx=S(16), pady=S(10))
        overlay_frame.pack(fill="both", expand=True)
        tk.Label(overlay_frame, text="⌖", bg=OVERLAY_BG, fg=GREEN_BRIGHT, font=("Segoe UI Symbol", -S(24), "bold")).pack(side="left", padx=(0, S(11)))
        copy = tk.Frame(overlay_frame, bg=OVERLAY_BG)
        copy.pack(side="left", fill="y")
        tk.Label(copy, text="定位模式", bg=OVERLAY_BG, fg=ON_COLOR_FG, font=F(13, "bold")).pack(anchor="w")
        tk.Label(copy, text="把鼠标移到目标位置，按 F2 确认；Esc 取消", bg=OVERLAY_BG, fg=OVERLAY_INK_SOFT, font=F(12)).pack(anchor="w", pady=(S(3), 0))
        cancel = tk.Button(overlay_frame, text="取消  Esc", command=self.cancel_capture, bg=OVERLAY_BUTTON, fg=ON_COLOR_FG, activebackground=OVERLAY_BUTTON_HOVER, activeforeground=ON_COLOR_FG, relief="flat", bd=0, padx=S(13), pady=S(7), font=F(12, "bold"))
        cancel.pack(side="right")
        overlay.update_idletasks()
        self.capture_overlay = overlay
        self.capture_overlay_handle = int(USER32.GetAncestor(overlay.winfo_id(), 2) or overlay.winfo_id())
        self.root.withdraw()
        overlay.lift()

    def _hide_capture_overlay(self, restore_main: bool = True) -> None:
        if self.capture_overlay:
            self.capture_overlay.destroy()
            self.capture_overlay = None
            self.capture_overlay_handle = 0
        if restore_main and not self.closing:
            self._restore_main_window()

    def cancel_capture(self) -> None:
        if not self.capture_armed:
            return
        self.capture_armed = False
        self.capture_button.configure(text="捕获位置 (F2)", bg=CARD_BG)
        self._hide_capture_overlay()
        self._set_status("已取消定位")

    def capture_position(self) -> None:
        if self.running:
            return
        try:
            x, y = cursor_position()
            hwnd = USER32.GetAncestor(USER32.WindowFromPoint(POINT(x, y)), 2)
            if not hwnd or self._is_own_window(hwnd):
                if self.capture_armed:
                    self._set_status("请把鼠标移到点点窗口之外，再按 F2")
                return
            rect = window_rect(hwnd)
            self.target = Step(
                type="click", wait_ms=self._interval_ms(), x=x, y=y,
                button=self._selected_button(), title=window_title(hwnd), hwnd=int(hwnd or 0),
                relative_x=x - rect.left if rect else None, relative_y=y - rect.top if rect else None,
            )
            self.capture_armed = False
            self.capture_button.configure(text="捕获位置 (F2)", bg=CARD_BG)
            self._hide_capture_overlay(restore_main=False)
            self.root.update()
            time.sleep(0.08)
            self._screen_thumb = None
            for _attempt in range(3):
                try:
                    self._screen_thumb = ImageGrab.grab(all_screens=True).convert("RGB")
                    break
                except OSError:
                    time.sleep(0.08)
            if not self.closing:
                self._restore_main_window()
            self._thumb_render_size = None
            captured_thumb = self._screen_thumb
            self._render_target()
            if self.mode_var.get() in {"多点任务", "录制操作"}:
                self.steps.append(self.target)
                if captured_thumb is not None:
                    self._remember_thumb(self.target.id, captured_thumb)
                    self._save_thumb(self.target.id, captured_thumb)
                self._render_steps()
            self.save_task(silent=True)
            self._set_status(f"已捕获 {x}, {y}")
        except OSError as error:
            self._set_status(str(error))

    def clear_position(self) -> None:
        if not self._editing_allowed():
            return
        if self.mode_var.get() == "单点连点":
            if not self.target:
                self._set_status("当前没有捕获位置，无需清除")
                return
            self.undo_stack.append(("target", copy.deepcopy(self.target)))
            self.target = None
            self._clear_preview_point()
            self._render_target()
            self.save_task(silent=True)
            self._set_status("单点位置已清除")
        else:
            index = self._selected_step_index()
            if index is None:
                self._set_status("请先在步骤列表中选中要清除的步骤")
                return
            self._push_undo_state()
            removed = self.steps.pop(index)
            self._delete_thumb(removed.id)
            self._step_thumbs.pop(removed.id, None)
            self._render_steps()
            self.save_task(silent=True)
            self._set_status(f"已清除第 {index + 1} 步")

    def _capture_template_region(self) -> tuple | None:
        """全屏框选模板区域，返回 (screenshot, left, top, right, bottom)，取消返回 None。"""
        self.root.withdraw()
        self.root.update_idletasks()
        time.sleep(.15)
        screenshot = ImageGrab.grab(all_screens=True)
        origin_x, origin_y = virtual_screen_origin()
        overlay = tk.Toplevel(self.root)
        overlay.overrideredirect(True)
        overlay.attributes("-topmost", True)
        overlay.geometry(f"{screenshot.width}x{screenshot.height}+0+0")
        canvas = tk.Canvas(overlay, width=screenshot.width, height=screenshot.height, highlightthickness=0, cursor="cross")
        canvas.pack(fill="both", expand=True)
        photo = ImageTk.PhotoImage(screenshot)
        canvas.create_image(0, 0, image=photo, anchor="nw")
        canvas.create_rectangle(S(10), S(10), S(390), S(50), fill=OVERLAY_BG, outline="")
        canvas.create_text(S(20), S(18), text="拖动框选要识别的图像区域 · Esc 取消", anchor="nw", fill=ON_COLOR_FG, font=F(14, "bold"))
        result = {"start": None, "rect": None, "box": None}

        def cancel(_event=None) -> None:
            overlay.destroy()
            self._restore_main_window()

        def press(event) -> None:
            result["start"] = (event.x, event.y)
            if result["rect"]:
                canvas.delete(result["rect"])
            result["rect"] = canvas.create_rectangle(event.x, event.y, event.x, event.y, outline=GREEN, width=S(2))

        def drag(event) -> None:
            if result["start"] and result["rect"]:
                canvas.coords(result["rect"], *result["start"], event.x, event.y)

        def release(event) -> None:
            if not result["start"]:
                return
            x1, y1 = result["start"]
            left, right = sorted((max(0, x1), min(screenshot.width, event.x)))
            top, bottom = sorted((max(0, y1), min(screenshot.height, event.y)))
            if right - left < S(16) or bottom - top < S(16):
                self._set_status("图像选区太小，请重新框选")
                return
            result["box"] = (left, top, right, bottom)
            overlay.destroy()
            self._restore_main_window()

        canvas.bind("<ButtonPress-1>", press)
        canvas.bind("<B1-Motion>", drag)
        canvas.bind("<ButtonRelease-1>", release)
        overlay.bind("<Escape>", cancel)
        overlay._photo = photo
        overlay.update_idletasks()
        overlay_handle = USER32.GetAncestor(overlay.winfo_id(), 2) or overlay.winfo_id()
        USER32.SetWindowPos(overlay_handle, HWND_TOPMOST, origin_x, origin_y, screenshot.width, screenshot.height, SWP_SHOWWINDOW)
        overlay.focus_force()
        self.root.wait_window(overlay)
        if result["box"]:
            return (screenshot,) + result["box"]
        return None

    def _save_template(self, screenshot, left, top, right, bottom) -> str:
        filename = f"template-{uuid.uuid4().hex}.png"
        screenshot.crop((left, top, right, bottom)).save(self.repository.template_dir / filename)
        return filename

    def start_image_capture(self) -> None:
        if not self._editing_allowed():
            return
        region = self._capture_template_region()
        if region is None:
            return
        screenshot, left, top, right, bottom = region
        filename = self._save_template(screenshot, left, top, right, bottom)
        step = Step(type="image_click", wait_ms=max(0, self.interval_var.get()), button=self._selected_button(), template_file=filename)
        self.steps.append(step)
        self._render_steps()
        self.save_task(silent=True)
        self._set_status("图像定位步骤已添加")
        self.show_toast("已添加图像定位步骤")

    def add_if_image_step(self) -> None:
        if not self._editing_allowed():
            return
        existing = getattr(self, "_if_image_dialog", None)
        if existing is not None and existing.winfo_exists():
            existing.focus_force()
            existing.lift()
            return
        region = self._capture_template_region()
        if region is None:
            return
        screenshot, left, top, right, bottom = region
        filename = self._save_template(screenshot, left, top, right, bottom)
        # 参数对话框
        params = {"condition": "存在", "skip_count": 1, "threshold": 0.86, "confirmed": False}
        dlg = tk.Toplevel(self.root)
        dlg.title("如果图像 - 参数设置")
        dlg.configure(bg=MAIN_BG)
        dlg.resizable(False, False)
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.focus_force()
        self._if_image_dialog = dlg
        body = tk.Frame(dlg, bg=MAIN_BG, padx=S(28), pady=S(24))
        body.pack(fill="both", expand=True)
        tk.Label(body, text="条件：图像", bg=MAIN_BG, fg=INK, font=F(12)).grid(row=0, column=0, sticky="w", pady=(0, S(14)))
        cond_var = tk.StringVar(value="存在")
        tk.Radiobutton(body, text="存在时继续", variable=cond_var, value="存在", bg=MAIN_BG, fg=INK, font=F(12), activebackground=MAIN_BG).grid(row=0, column=1, sticky="w", padx=(S(12), 0))
        tk.Radiobutton(body, text="不存在时继续", variable=cond_var, value="不存在", bg=MAIN_BG, fg=INK, font=F(12), activebackground=MAIN_BG).grid(row=0, column=2, sticky="w", padx=(S(12), 0))
        tk.Label(body, text="不满足时跳过步数：", bg=MAIN_BG, fg=INK, font=F(12)).grid(row=1, column=0, sticky="w", pady=(0, S(14)))
        skip_var = tk.IntVar(value=1)
        tk.Spinbox(body, from_=1, to=20, textvariable=skip_var, width=6, font=F(12)).grid(row=1, column=1, sticky="w", padx=(S(12), 0))
        tk.Label(body, text="匹配阈值：", bg=MAIN_BG, fg=INK, font=F(12)).grid(row=2, column=0, sticky="w", pady=(0, S(14)))
        th_var = tk.DoubleVar(value=0.86)
        th_value = tk.Label(body, text="0.86", bg=MAIN_BG, fg=GREEN_TEXT, font=F(12, "bold"))
        th_value.grid(row=2, column=1, sticky="w", padx=(S(12), 0))
        def update_th_label(*_a):
            th_value.configure(text=f"{th_var.get():.2f}")
        th_var.trace_add("write", update_th_label)
        tk.Scale(body, from_=0.5, to=1.0, resolution=0.01, orient="horizontal", variable=th_var, length=S(220), bg=MAIN_BG, highlightthickness=0).grid(row=3, column=0, columnspan=3, sticky="ew", pady=(0, S(18)))
        btn_row = tk.Frame(body, bg=MAIN_BG)
        btn_row.grid(row=4, column=0, columnspan=3, sticky="ew")
        def confirm():
            params["condition"] = cond_var.get()
            params["skip_count"] = skip_var.get()
            params["threshold"] = th_var.get()
            params["confirmed"] = True
            self._if_image_dialog = None
            dlg.destroy()
        PillButton(btn_row, "确定", confirm, width=S(80), height=S(34), radius=S(8), bg=GREEN, fg=ON_COLOR_FG, hover_bg=GREEN_HOVER, font=F(12, "bold")).pack(side="right")
        PillButton(btn_row, "取消", lambda: (setattr(self, "_if_image_dialog", None), dlg.destroy()), width=S(80), height=S(34), radius=S(8), bg=CARD_BG, fg=INK, border=FIELD_BORDER, hover_bg=NEUTRAL_HOVER, font=F(12)).pack(side="right", padx=(0, S(8)))
        self._center_dialog(dlg)
        self.root.wait_window(dlg)
        if not params["confirmed"]:
            return
        step = Step(type="if_image", template_file=filename, condition=params["condition"], skip_count=params["skip_count"], match_threshold=params["threshold"])
        self.steps.append(step)
        self._render_steps()
        self.save_task(silent=True)
        self._set_status("条件判断步骤已添加")
        self.show_toast("已添加如果图像步骤")

    @staticmethod
    def _check_image_exists(template_path, threshold: float) -> bool:
        try:
            locate_template(template_path, threshold)
            return True
        except Exception:
            return False

    def toggle_recording(self) -> None:
        if self.recording:
            self._stop_recording()
        else:
            if self.steps and not self._ask_confirm("重新录制", f"开始录制会替换当前 {len(self.steps)} 个步骤。继续吗？\n录制后仍可按 Ctrl+Z 恢复。", confirm_text="继续"):
                return
            if self.steps:
                self.undo_stack.append(("steps", list(enumerate(self.steps))))
            self.steps = []
            self._render_steps()
            try:
                self.input_recorder.start()
            except RuntimeError as error:
                self._set_status(str(error))
                return
            self.recording = True
            self.last_record_time = time.perf_counter()
            self.record_button.configure(text="停止录制", bg=DANGER_BG, fg=DANGER_FG)
            self._show_record_overlay()
            self._set_status("正在录制鼠标、键盘和滚轮输入")

    def _stop_recording(self, restore_main: bool = True) -> None:
        self.recording = False
        self.input_recorder.stop()
        self._hide_record_overlay(restore_main=restore_main)
        self.record_button.configure(text="开始录制", bg=CARD_BG, fg=INK_SOFT)
        self.save_task(silent=True)
        self._set_status(f"录制完成，共 {len(self.steps)} 个动作")

    def _raw_input_event(self, event: dict, timestamp: float) -> None:
        self.ui_events.put((self._record_event, (dict(event), timestamp)))

    def _record_event(self, event: dict, timestamp: float) -> None:
        if not self.recording:
            return
        wait_ms = 0 if not self.steps else max(10, int((timestamp - self.last_record_time) * 1000))
        if event["type"] in {"click", "scroll"}:
            step = self._pointer_event_to_step(event, wait_ms)
            if step is None:
                return
            self.steps.append(step)
            try:
                shot = ImageGrab.grab(all_screens=True).convert("RGB")
                if shot.width > 960:
                    ratio = 960 / shot.width
                    shot = shot.resize((960, max(1, round(shot.height * ratio))), Image.Resampling.BILINEAR)
                self._remember_thumb(step.id, shot)
                self._save_thumb(step.id, shot)
            except OSError:
                pass
        elif event["type"] in ("key_down", "key_up"):
            self.steps.append(Step(
                type=event["type"], wait_ms=wait_ms,
                key_code=event["key_code"], key_name=event["key_name"],
            ))
        else:
            return
        self.last_record_time = timestamp
        self._schedule_steps_render()

    def _schedule_steps_render(self) -> None:
        """录制高频输入时合并渲染：每个事件都全量重建列表，会随步骤数增长越来越卡。"""
        if self._steps_render_scheduled:
            return
        self._steps_render_scheduled = True
        self.root.after(60, self._flush_steps_render)

    def _flush_steps_render(self) -> None:
        self._steps_render_scheduled = False
        self._render_steps()
        if self.recording:
            self._set_status(f"已记录第 {len(self.steps)} 个动作")

    def _pointer_event_to_step(self, event: dict, wait_ms: int) -> Step | None:
        hwnd = USER32.GetAncestor(USER32.WindowFromPoint(POINT(event["x"], event["y"])), 2)
        if not hwnd or self._is_own_window(int(hwnd)):
            return None
        rect = window_rect(hwnd)
        return Step(
            type=event["type"], wait_ms=wait_ms, x=event["x"], y=event["y"],
            button=event.get("button", "左"), scroll_delta=event.get("scroll_delta", 0),
            title=window_title(hwnd), hwnd=int(hwnd),
            relative_x=event["x"] - rect.left if rect else None,
            relative_y=event["y"] - rect.top if rect else None,
        )

    # ------------------------------------------------------------------
    # 运行控制
    # ------------------------------------------------------------------

    def toggle_run(self) -> None:
        if self.running:
            self.stop_run("已停止运行")
            return
        if self.mode_var.get() == "单点连点" and not self.target:
            self.show_toast("还没有位置：请先按 F2 捕获鼠标当前位置")
            return
        if self.mode_var.get() != "单点连点" and not self.steps:
            self.show_toast("还没有动作：请先捕获位置或录制至少一个动作")
            return
        self.save_task(silent=True)
        if not self._start_task(self.tasks[self.active_index]):
            self._set_status("当前任务没有可执行动作")

    def start_run(self, index: int) -> None:
        """右键菜单“开始此任务”：切到指定任务后走主启动流程，复用空任务校验与提示。"""
        if self.running:
            self._set_status("任务运行中，停止后才能开始其他任务")
            return
        self.select_task(index)
        self.toggle_run()

    def _start_task(self, task: Task) -> bool:
        """从任务快照创建运行计划；手动与定时触发共用，避免读取 UI 临时状态。"""
        if self.running:
            return False
        if task.mode == "单点连点":
            if not task.target:
                return False
            single = copy.deepcopy(task.target)
            single.type = "click"
            single.wait_ms = max(10, int(task.settings.interval_ms))
            single.button = {"右键单击": "右", "中键单击": "中"}.get(task.settings.button, "左")
            actions = (single,)
        else:
            if not task.steps:
                return False
            actions = tuple(copy.deepcopy(step) for step in task.steps)
        self.stop_event.clear()
        self.paused = False
        self.pause_event.clear()
        self.run_actions = actions
        self.run_repeats = max(0, int(task.settings.repeat_count))
        self.run_random_interval = bool(task.settings.random_interval)
        self.run_random_percent = float(task.settings.random_percent)
        self.run_position_mode = task.settings.position_mode
        self.running = True
        self.run_button.configure(text="停止运行 (F6)", bg=RUNNING_BG)
        self._show_run_overlay()
        self._run_breath_phase = 0
        self._run_breath_tick()
        self._set_status(f"正在运行：{task.name}", "running")
        self.run_info.configure(fg=GREEN, font=F(12, "bold"))
        self._countdown_generation += 1
        generation = self._countdown_generation
        if task.settings.countdown_enabled and task.settings.countdown_seconds > 0:
            self.root.after(100, self._start_countdown, task.settings.countdown_seconds, generation)
        else:
            self._start_worker()
        return True

    def _start_countdown(self, seconds: float, generation: int | None = None) -> None:
        if not self.running or (generation is not None and generation != self._countdown_generation):
            return
        self._countdown_tick(time.monotonic() + max(0.0, seconds), generation)

    def _countdown_tick(self, deadline: float, generation: int | None = None) -> None:
        if not self.running or (generation is not None and generation != self._countdown_generation):
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            self._start_worker()
            return
        text = f"{remaining:.1f}".rstrip("0").rstrip(".")
        self._set_status(f"{text} 秒后开始运行")
        self._update_run_overlay(f"准备中 · {text} 秒后开始")
        self.root.after(100, self._countdown_tick, deadline, generation)

    def _start_worker(self) -> None:
        if not self.running:
            return
        if self.worker and self.worker.is_alive():
            # 快速“停止后再运行”时旧线程可能尚未退出；等它结束后再启动，
            # 避免新计划无人执行的假死。正常情况下旧线程十几毫秒内就会退出
            self.worker.join(timeout=1.5)
            if self.worker.is_alive():
                self.stop_run("上一任务尚未退出，请稍后重新开始")
                return
        self.worker = threading.Thread(target=self._run_task, daemon=True)
        self.worker.start()

    def _run_task(self) -> None:
        engine = RunEngine(
            RunPlan(
                actions=self.run_actions,
                repeats=self.run_repeats,
                random_interval=self.run_random_interval,
                random_percent=self.run_random_percent,
            ),
            stop_event=self.stop_event,
            pause_event=self.pause_event,
            execute_step=self._execute_step,
            image_matches=lambda step: self._check_image_exists(
                self.repository.template_dir / step.template_file, step.match_threshold
            ),
            on_progress=lambda text: self.ui_events.put((self._update_run_info, (text,))),
        )
        result = engine.run()
        if result.error is not None:
            self.ui_events.put((self.stop_run, (f"运行失败：{result.error}",)))
        elif result.completed:
            self.ui_events.put((self.stop_run, ("任务已完成",)))

    def _execute_step(self, step: Step) -> None:
        if step.type == "click":
            x, y = self._resolve_position(step)
            click_at(x, y, self._mouse_button(step.button))
        elif step.type == "scroll":
            x, y = self._resolve_position(step)
            scroll_at(x, y, step.scroll_delta)
        elif step.type == "key_down":
            send_key(step.key_code)
            self.run_keys_down.add(step.key_code)
        elif step.type == "key_up":
            send_key(step.key_code, key_up=True)
            self.run_keys_down.discard(step.key_code)
        elif step.type == "image_click":
            template = self.repository.template_dir / step.template_file
            match_x, match_y, _score = locate_template(template, step.match_threshold)
            click_at(match_x + step.click_offset_x, match_y + step.click_offset_y, self._mouse_button(step.button))
        elif step.type == "type_text":
            if step.x or step.y or step.hwnd:
                x, y = self._resolve_position(step)
                click_at(x, y, "left")
                time.sleep(0.15)
            paste_text(step.text)

    @staticmethod
    def _mouse_button(button: str) -> str:
        return {"左": "left", "右": "right", "中": "middle"}.get(button, "left")

    def _resolve_position(self, step: Step) -> tuple[int, int]:
        if self.run_position_mode != "窗口相对" or not step.hwnd or not USER32.IsWindow(step.hwnd):
            return step.x, step.y
        if step.title != window_title(step.hwnd):
            return step.x, step.y
        rect = window_rect(step.hwnd)
        if not rect or step.relative_x is None or step.relative_y is None:
            return step.x, step.y
        return rect.left + step.relative_x, rect.top + step.relative_y

    def stop_run(self, message: str = "已停止运行") -> None:
        self._countdown_generation += 1
        self.stop_event.set()
        self.pause_event.clear()
        for key_code in tuple(self.run_keys_down):
            try:
                send_key(key_code, key_up=True)
            except OSError:
                pass
        self.run_keys_down.clear()
        self.running = False
        self.paused = False
        self.run_button.configure(text="开始运行 (F6)", bg=GREEN, fg=ON_COLOR_FG)
        if hasattr(self, "_run_breath_job") and self._run_breath_job:
            try:
                self.root.after_cancel(self._run_breath_job)
            except Exception:
                pass
            self._run_breath_job = None
        self.run_info.configure(text=f"运行状态：{message}", fg=GRAY, font=F(11))
        self._hide_run_overlay()
        self._set_status(message)

    def toggle_pause(self) -> None:
        if not self.running:
            return
        self.paused = not self.paused
        if self.paused:
            self.pause_event.set()
        else:
            self.pause_event.clear()
        status = "已暂停" if self.paused else "正在运行"
        self._set_status(status)
        self._update_run_overlay(status)
        if self.run_overlay_pause:
            self.run_overlay_pause.configure(text="继续  F7" if self.paused else "暂停  F7")

    def save_task(self, silent: bool = False) -> None:
        task = self.tasks[self.active_index]
        task.name = self.task_name_var.get().strip() or "未命名任务"
        task.mode = self.mode_var.get()
        task.target = self.target
        task.steps = self.steps
        task.settings.button = self.button_var.get()
        task.settings.position_mode = self.position_var.get()
        task.settings.interval_ms = self._interval_ms()
        task.settings.repeat_count = self.repeat_var.get()
        task.settings.countdown_enabled = self.countdown_var.get()
        task.settings.countdown_seconds = self.countdown_seconds_var.get()
        task.settings.random_interval = self.random_var.get()
        task.settings.random_percent = self.random_percent_var.get()
        task.updated_at = time.time()
        self.breadcrumb_task.configure(text=task.name)
        # 列表只受任务名称影响：保存高频发生（每次编辑步骤/切换模式都会保存），
        # 全量重建几十个按钮控件的开销只在名称真正变化时才有必要
        if self._task_list_fingerprint() != self._task_list_signature:
            self._render_task_list()
        if self._persist_tasks() and not silent:
            self._set_status("任务已保存")
            self.show_toast("任务已保存")

    def export_task(self) -> None:
        """把当前选中任务导出为 .json，用户自选保存位置。"""
        self.save_task(silent=True)
        task = self.tasks[self.active_index]
        default_name = f"{task.name or '任务'}.json"
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("点点任务", "*.json")],
            initialfile=default_name,
            title="导出任务",
            parent=self.root,
        )
        if not path:
            return
        try:
            payload = {"schemaVersion": 2, "tasks": [task.to_dict()]}
            Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as error:
            self.show_toast(f"导出失败：{error}", kind="error")
            return
        self.show_toast("已导出")

    def import_task(self) -> None:
        """从 .json 导入任务，追加到任务列表末尾。"""
        path = filedialog.askopenfilename(
            filetypes=[("点点任务", "*.json")],
            title="导入任务",
            parent=self.root,
        )
        if not path:
            return
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            items = payload.get("tasks", []) if isinstance(payload, dict) else payload
            imported = [Task.from_dict(item) for item in items if isinstance(item, dict)]
        except (OSError, ValueError, TypeError) as error:
            self.show_toast(f"导入失败：文件无法解析（{error}）", kind="error")
            return
        if not imported:
            self.show_toast("文件中没有可导入的任务")
            return
        self.tasks.extend(imported)
        self._persist_tasks()
        self.active_index = len(self.tasks) - len(imported)
        self._render_task_list()
        self._load_active_task()
        self.show_toast(f"已导入 {len(imported)} 个任务")

    def _interval_ms(self) -> int:
        """点击间隔（秒）换算为内部毫秒，下限 10ms。"""
        return max(10, round(self.interval_var.get() * 1000))

    def _persist_schedules(self) -> bool:
        try:
            self.repository.save_schedules(self.schedules)
            return True
        except OSError as error:
            self._set_status(f"定时任务保存失败：{error}")
            return False

    def _schedule_task_name(self, schedule: Schedule) -> str:
        for task in self.tasks:
            if task.id == schedule.task_id:
                return task.name
        return "任务不存在"

    def _schedule_description(self, schedule: Schedule) -> str:
        if schedule.run_at is None:
            timing = "未设置时间"
        elif schedule.kind == ScheduleKind.INTERVAL:
            timing = f"每 {max(1, schedule.interval_seconds // 60)} 分钟"
        elif schedule.kind == ScheduleKind.DAILY:
            timing = time.strftime("每天 %H:%M", time.localtime(schedule.run_at))
        else:
            timing = time.strftime("一次性 %Y-%m-%d %H:%M", time.localtime(schedule.run_at))
        state = "启用" if schedule.enabled else "停用"
        result = f" · {schedule.last_result}" if schedule.last_result else ""
        return f"{self._schedule_task_name(schedule)} · {timing} · {state}{result}"

    def _refresh_schedule_list(self, listing: tk.Listbox) -> None:
        listing.delete(0, tk.END)
        for schedule in self.schedules:
            listing.insert(tk.END, self._schedule_description(schedule))

    def _schedule_selected(self, listing: tk.Listbox) -> Schedule | None:
        values = listing.curselection()
        if not values:
            return None
        index = int(values[0])
        return self.schedules[index] if 0 <= index < len(self.schedules) else None

    def _maybe_prompt_autostart(self) -> None:
        """首次启用定时计划时一次性询问开机自启；拒绝后记住，不再打扰。"""
        if autostart.is_enabled() or (self.repository.base_dir / ".autostart_declined").exists():
            return
        enabled = self._ask_confirm(
            "开机自启",
            "定时任务在点点运行期间就会生效。\n"
            "开启开机自启后，重启电脑也会自动启动点点、按计划继续执行。\n\n"
            "现在开启开机自启吗？",
            confirm_text="开启",
            cancel_text="暂不",
        )
        if enabled:
            if autostart.set_enabled(True):
                self.show_toast("已开启开机自启")
            else:
                self._set_status("开机自启设置失败，请检查权限")
        else:
            self._mark_autostart_declined()

    def _mark_autostart_declined(self) -> None:
        try:
            (self.repository.base_dir / ".autostart_declined").write_text("", encoding="utf-8")
        except OSError:
            pass

    def _clear_autostart_declined(self) -> None:
        try:
            (self.repository.base_dir / ".autostart_declined").unlink(missing_ok=True)
        except OSError:
            pass

    def _next_daily_timestamp(self, text: str, now: float) -> float:
        parsed = dt.datetime.strptime(text.strip(), "%H:%M")
        current = dt.datetime.fromtimestamp(now)
        candidate = current.replace(hour=parsed.hour, minute=parsed.minute, second=0, microsecond=0)
        if candidate.timestamp() <= now:
            candidate += dt.timedelta(days=1)
        return candidate.timestamp()

    def _schedule_add_dialog(self, refresh: callable, schedule: Schedule | None = None) -> None:
        """新增/编辑定时计划：传入 schedule 时进入编辑模式并回填当前值。"""
        if not self.tasks:
            return
        dialog = tk.Toplevel(self.root)
        dialog.title("编辑定时任务" if schedule else "添加定时任务")
        dialog.configure(bg=MAIN_BG)
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.focus_force()
        body = tk.Frame(dialog, bg=MAIN_BG, padx=S(28), pady=S(24))
        body.pack(fill="both", expand=True)

        # 只显示中文任务名，重名时加序号区分，不暴露技术 ID；
        # 序号名若与既有本名撞车则继续递增，保证显示名与任务一一对应
        name_totals: dict[str, int] = {}
        for task in self.tasks:
            name_totals[task.name] = name_totals.get(task.name, 0) + 1
        name_seen: dict[str, int] = {}
        task_names = []
        task_id_by_name: dict[str, str] = {}
        for task in self.tasks:
            name_seen[task.name] = name_seen.get(task.name, 0) + 1
            display = f"{task.name}（{name_seen[task.name]}）" if name_totals[task.name] > 1 else task.name
            while display in task_id_by_name:
                name_seen[task.name] += 1
                display = f"{task.name}（{name_seen[task.name]}）"
            task_names.append(display)
            task_id_by_name[display] = task.id
        kind_labels = {ScheduleKind.ONCE: "一次性", ScheduleKind.DAILY: "每天", ScheduleKind.INTERVAL: "间隔"}
        if schedule is not None:
            task_var = tk.StringVar(value=next((name for name, task_id in task_id_by_name.items() if task_id == schedule.task_id), task_names[0]))
            kind_var = tk.StringVar(value=kind_labels.get(schedule.kind, kind_labels[ScheduleKind.ONCE]))
        else:
            task_var = tk.StringVar(value=task_names[self.active_index])
            kind_var = tk.StringVar(value=kind_labels[ScheduleKind.ONCE])
        value_var = tk.StringVar()
        error_var = tk.StringVar()

        field_width = S(300)
        field_height = S(38)

        tk.Label(body, text="任务", bg=MAIN_BG, fg=INK_SOFT, font=F(12)).grid(row=0, column=0, sticky="w", pady=(0, S(6)))
        Select(body, task_var, task_names, width=field_width, height=field_height, radius=S(8), border=FIELD_BORDER, font=F(12)).grid(row=1, column=0, sticky="w", pady=(0, S(16)))

        tk.Label(body, text="触发方式", bg=MAIN_BG, fg=INK_SOFT, font=F(12)).grid(row=2, column=0, sticky="w", pady=(0, S(6)))
        Select(body, kind_var, list(kind_labels.values()), width=field_width, height=field_height, radius=S(8), border=FIELD_BORDER, font=F(12)).grid(row=3, column=0, sticky="w", pady=(0, S(16)))

        value_label = tk.Label(body, text="时间", bg=MAIN_BG, fg=INK_SOFT, font=F(12))
        value_label.grid(row=4, column=0, sticky="w", pady=(0, S(6)))
        value_entry = tk.Entry(body, textvariable=value_var, width=30, font=F(12), bg=CARD_BG, fg=INK, relief="flat", highlightthickness=1, highlightbackground=FIELD_BORDER, highlightcolor=GREEN)
        value_entry.grid(row=5, column=0, sticky="w", pady=(0, S(10)), ipady=S(7))
        error_label = tk.Label(body, textvariable=error_var, bg=MAIN_BG, fg=DANGER_FG, font=F(11), justify="left", wraplength=field_width)
        error_label.grid(row=6, column=0, sticky="w", pady=(0, S(8)))

        def update_hint(*_args) -> None:
            label = kind_var.get()
            if label == kind_labels[ScheduleKind.ONCE]:
                value_label.configure(text="时间")
                value_var.set((dt.datetime.now() + dt.timedelta(minutes=5)).replace(second=0, microsecond=0).strftime("%Y-%m-%d %H:%M"))
            elif label == kind_labels[ScheduleKind.DAILY]:
                value_label.configure(text="每天时间")
                value_var.set(dt.datetime.now().strftime("%H:%M"))
            else:
                value_label.configure(text="间隔分钟")
                value_var.set("60")

        def cancel() -> None:
            dialog.destroy()

        def confirm() -> None:
            try:
                task_id = task_id_by_name[task_var.get()]
                label = kind_var.get()
                if label == kind_labels[ScheduleKind.ONCE]:
                    run_at = dt.datetime.strptime(value_var.get().strip(), "%Y-%m-%d %H:%M").timestamp()
                    kind = ScheduleKind.ONCE
                    interval_seconds = 3600
                elif label == kind_labels[ScheduleKind.DAILY]:
                    run_at = self._next_daily_timestamp(value_var.get(), time.time())
                    kind = ScheduleKind.DAILY
                    interval_seconds = 86400
                else:
                    minutes = int(value_var.get().strip())
                    if minutes < 1:
                        raise ValueError("间隔分钟必须大于等于 1")
                    run_at = time.time() + minutes * 60
                    kind = ScheduleKind.INTERVAL
                    interval_seconds = minutes * 60
                if kind == ScheduleKind.ONCE and run_at <= time.time():
                    raise ValueError("一次性任务时间必须晚于当前时间")
                if schedule is not None:
                    schedule.task_id = task_id
                    schedule.kind = kind
                    schedule.run_at = run_at
                    schedule.interval_seconds = interval_seconds
                    schedule.last_result = ""
                else:
                    self.schedules.append(Schedule(task_id=task_id, kind=kind, run_at=run_at, interval_seconds=interval_seconds))
                self._persist_schedules()
                self._ensure_tray()
                refresh()
                dialog.destroy()
                if schedule is None and sum(1 for item in self.schedules if item.enabled) == 1:
                    self._maybe_prompt_autostart()
            except (ValueError, IndexError, KeyError) as error:
                error_var.set(f"输入无效：{error}")

        kind_var.trace_add("write", update_hint)
        update_hint()
        if schedule is not None:
            if schedule.kind == ScheduleKind.INTERVAL:
                value_var.set(str(max(1, schedule.interval_seconds // 60)))
            elif schedule.run_at:
                format_text = "%H:%M" if schedule.kind == ScheduleKind.DAILY else "%Y-%m-%d %H:%M"
                value_var.set(dt.datetime.fromtimestamp(schedule.run_at).strftime(format_text))
        actions = tk.Frame(body, bg=MAIN_BG)
        actions.grid(row=7, column=0, sticky="ew")
        PillButton(actions, "确定", confirm, width=S(84), height=S(34), radius=S(8), bg=GREEN, fg=ON_COLOR_FG, hover_bg=GREEN_HOVER, font=F(12, "bold")).pack(side="right")
        PillButton(actions, "取消", cancel, width=S(84), height=S(34), radius=S(8), bg=CARD_BG, fg=INK, border=FIELD_BORDER, hover_bg=NEUTRAL_HOVER, font=F(12)).pack(side="right", padx=(0, S(8)))
        dialog.bind("<Return>", lambda _event: confirm())
        dialog.bind("<Escape>", lambda _event: cancel())
        self._center_dialog(dialog)
        value_entry.focus_set()

    def show_schedules(self) -> None:
        self.save_task(silent=True)
        existing = getattr(self, "_schedule_dialog", None)
        if existing is not None and existing.winfo_exists():
            existing.focus_force()
            existing.lift()
            return
        dialog = tk.Toplevel(self.root)
        dialog.title("定时任务")
        dialog.geometry(f"{S(700)}x{S(430)}")
        dialog.minsize(S(560), S(320))
        dialog.configure(bg=MAIN_BG)
        dialog.transient(self.root)
        self._schedule_dialog = dialog
        self._center_dialog(dialog)

        tk.Label(dialog, text="定时任务", bg=MAIN_BG, fg=INK, font=F(18, "bold")).pack(anchor="w", padx=S(24), pady=(S(20), S(4)))
        tk.Label(dialog, text="应用保持运行时按计划触发；关闭主窗口会最小化到托盘，任务继续执行。", bg=MAIN_BG, fg=GRAY, font=F(11)).pack(anchor="w", padx=S(24), pady=(0, S(12)))
        listing = tk.Listbox(dialog, bg=LIST_BG, fg=INK, selectbackground=PILL_ACTIVE, selectforeground=GREEN_TEXT, relief="flat", highlightthickness=1, highlightbackground=BORDER, font=F(11))
        listing.pack(fill="both", expand=True, padx=S(24), pady=(0, S(12)))
        ThinScrollbar(dialog, listing)
        self._schedule_listing = listing

        def refresh() -> None:
            self._refresh_schedule_list(listing)

        def edit_selected() -> None:
            schedule = self._schedule_selected(listing)
            if schedule is not None:
                self._schedule_add_dialog(refresh, schedule)

        listing.bind("<Double-Button-1>", lambda _event: edit_selected())

        def toggle_selected() -> None:
            schedule = self._schedule_selected(listing)
            if schedule is None:
                return
            schedule.enabled = not schedule.enabled
            schedule.last_result = ""
            self._persist_schedules()
            self._sync_tray()
            refresh()

        def delete_selected() -> None:
            schedule = self._schedule_selected(listing)
            if schedule is None:
                return
            self.schedules.remove(schedule)
            self._persist_schedules()
            self._sync_tray()
            refresh()

        actions = tk.Frame(dialog, bg=MAIN_BG)
        actions.pack(fill="x", padx=S(24), pady=(0, S(18)))
        PillButton(actions, "添加", lambda: self._schedule_add_dialog(refresh), width=S(84), height=S(34), radius=S(8), bg=GREEN, fg=ON_COLOR_FG, hover_bg=GREEN_HOVER, font=F(12, "bold")).pack(side="left")
        PillButton(actions, "启用/停用", toggle_selected, width=S(100), height=S(34), radius=S(8), bg=CARD_BG, fg=INK, border=FIELD_BORDER, hover_bg=NEUTRAL_HOVER, font=F(12)).pack(side="left", padx=S(8))
        PillButton(actions, "删除", delete_selected, width=S(84), height=S(34), radius=S(8), bg=DANGER_BG, fg=DANGER_FG, border=FIELD_BORDER, hover_bg=DANGER_HOVER, font=F(12)).pack(side="left")
        def close_dialog() -> None:
            self._schedule_dialog = None
            self._schedule_listing = None
            dialog.destroy()
        PillButton(actions, "关闭", close_dialog, width=S(84), height=S(34), radius=S(8), bg=SUBTLE_BG, fg=INK_SOFT, hover_bg=SUBTLE_HOVER, font=F(12)).pack(side="right")
        dialog.protocol("WM_DELETE_WINDOW", close_dialog)
        dialog.bind("<Escape>", lambda _event: close_dialog())

        autostart_row = tk.Frame(dialog, bg=MAIN_BG)
        autostart_row.pack(fill="x", padx=S(24), pady=(0, S(18)))
        autostart_state = tk.Label(autostart_row, bg=MAIN_BG, fg=GRAY, font=F(11))
        autostart_state.pack(side="left")

        def refresh_autostart_label() -> None:
            if autostart.is_enabled():
                autostart_state.configure(text="开机自启：已开启（重启电脑后点点自动启动并执行定时任务）", fg=GREEN_TEXT)
            else:
                autostart_state.configure(text="开机自启：未开启（重启电脑后需手动打开点点才会执行定时任务）", fg=GRAY)

        def toggle_autostart() -> None:
            target = not autostart.is_enabled()
            if autostart.set_enabled(target):
                if not target:
                    self._clear_autostart_declined()
                refresh_autostart_label()
                self.show_toast("已开启开机自启" if target else "已关闭开机自启")
            else:
                self._set_status("开机自启设置失败，请检查权限")

        refresh_autostart_label()
        PillButton(autostart_row, "开机自启", toggle_autostart, width=S(96), height=S(30), radius=S(8), bg=CARD_BG, fg=INK, border=FIELD_BORDER, hover_bg=NEUTRAL_HOVER, font=F(12, "bold")).pack(side="right")
        refresh()

    def _poll_schedules(self) -> None:
        try:
            try:
                decisions = self.scheduler.tick(
                    task_exists=lambda task_id: any(task.id == task_id for task in self.tasks),
                    is_running=lambda: self.running,
                    on_due=lambda schedule: self._start_scheduled_task(schedule),
                )
                if decisions:
                    self._sync_tray()
                    self._persist_schedules()
                    self._report_schedule_decisions(decisions)
                    if self._schedule_dialog is not None and self._schedule_dialog.winfo_exists() and self._schedule_listing is not None:
                        self._refresh_schedule_list(self._schedule_listing)
            except Exception as error:
                # 与姊妹轮询一致：调度出错只上报状态，轮询循环不能死
                self._set_status(f"定时调度失败：{error}")
        finally:
            self._reschedule(self._poll_schedules, 1000)

    def _start_scheduled_task(self, schedule: Schedule) -> bool:
        task = next((item for item in self.tasks if item.id == schedule.task_id), None)
        if task is None:
            return False
        started = self._start_task(task)
        if started:
            self._set_status(f"定时任务已触发：{task.name}", "running")
            if not self.root.winfo_viewable():
                self._notify("定时任务", f"已触发：{task.name}")
        return started

    _SCHEDULE_DECISION_MESSAGES = {
        "skipped": "上一任务仍在运行，本次触发已跳过",
        "missed": "错过计划时间，已自动顺延",
        "rejected": "触发失败，请检查任务步骤",
        "disabled": "任务不存在，计划已自动停用",
    }

    def _report_schedule_decisions(self, decisions) -> None:
        """跳过/错过/失败等调度结果用户不易察觉，主动用气泡或 toast 告知。"""
        for decision in decisions:
            message = self._SCHEDULE_DECISION_MESSAGES.get(decision.action)
            if message is None:
                continue
            schedule = next((item for item in self.schedules if item.id == decision.schedule_id), None)
            name = self._schedule_task_name(schedule) if schedule else "定时计划"
            self._notify("定时任务", f"{name}：{message}")

    def _notify(self, title: str, message: str) -> None:
        """面向“用户可能没盯着窗口”的提醒：窗口可见走 toast，收进托盘/不可见走系统气泡。"""
        if self._tray_hidden or not self.root.winfo_viewable():
            if self._tray_icon is not None and self._tray_icon.is_alive() and self._tray_icon.notify(title, message):
                return
        self.show_toast(f"{title}：{message}")

    def show_toast(self, text: str, *, kind: str = "info", action=None,
                   action_label: str = "撤销", duration: int = 1800) -> None:
        """窗口中上部浮出轻提示；kind="error" 红底且停留更久，action 为右侧可点动作（如撤销）。"""
        self._dismiss_toast(self._toast)
        toast = tk.Toplevel(self.root)
        toast.overrideredirect(True)
        toast.attributes("-topmost", True)
        row = tk.Frame(toast, bg=RUNNING_BG if kind == "error" else TOAST_BG)
        row.pack()
        tk.Label(row, text=text, bg=row["bg"], fg=TOAST_FG, font=F(12), padx=S(18), pady=S(10)).pack(side="left")
        if action is not None:
            action_button = tk.Label(row, text=action_label, bg=row["bg"], fg=GREEN_BRIGHT, font=F(12, "bold"), padx=(0, S(16)), pady=S(10), cursor="hand2")
            action_button.pack(side="left")
            action_button.bind("<Button-1>", lambda _e: (self._dismiss_toast(toast), action()))
        toast.update_idletasks()
        x = max(0, self.root.winfo_x() + (self.root.winfo_width() - toast.winfo_width()) // 2)
        y = self.root.winfo_y() + S(80)
        toast.geometry(f"+{x}+{y}")
        self._toast = toast
        self.root.after(duration if kind == "info" else max(duration, 4000), lambda: self._dismiss_toast(toast))

    def _dismiss_toast(self, toast: tk.Toplevel | None) -> None:
        if toast is None:
            return
        if self._toast is toast:
            self._toast = None
        try:
            toast.destroy()
        except tk.TclError:
            pass



    def _persist_tasks(self) -> bool:
        """持久化失败只提示不抛出：热键路径（F6 启动）也会走到这里，磁盘异常不能拖垮轮询循环。"""
        try:
            self.repository.save_tasks(self.tasks)
            return True
        except OSError as error:
            self._set_status(f"任务保存失败：{error}")
            return False

    def _persist_trash(self) -> bool:
        try:
            self.repository.save_trash(self.trash)
            return True
        except OSError as error:
            self._set_status(f"回收站保存失败：{error}")
            return False

    def _selected_button(self) -> str:
        value = self.button_var.get()
        if "右" in value:
            return "右"
        if "中" in value:
            return "中"
        return "左"

    def new_task(self) -> None:
        if not self._editing_allowed():
            return
        self.save_task(silent=True)
        self.tasks.append(Task(name=f"任务 {len(self.tasks) + 1}"))
        self.active_index = len(self.tasks) - 1
        self._render_task_list()
        self._load_active_task()

    def duplicate_task(self) -> None:
        if not self._editing_allowed():
            return
        self.save_task(silent=True)
        self.tasks.insert(self.active_index + 1, self.tasks[self.active_index].clone())
        self.active_index += 1
        self._persist_tasks()
        self._render_task_list()
        self._load_active_task()
        self._set_status("任务已复制")

    def delete_task(self) -> None:
        """删除改为“先删 + 可撤销”范式：不打断操作流，5 秒内可一键撤销，之后仍可从回收站恢复。"""
        if not self._editing_allowed() or not self.tasks:
            return
        self.save_task(silent=True)
        index = self.active_index
        task = self.tasks.pop(index)
        task.deleted_at = time.time()
        self.trash.append(task)
        placeholder = None
        if not self.tasks:
            placeholder = Task()
            self.tasks.append(placeholder)
        self.active_index = min(index, len(self.tasks) - 1)
        # 先落回收站再写 tasks.json：任一步失败，任务都至少还存在于一份文件中
        self._persist_trash()
        self._persist_tasks()
        self._render_task_list()
        self._load_active_task()

        def undo() -> None:
            if self.running:
                self._set_status("任务运行中，停止后才能撤销删除")
                return
            if task not in self.trash:
                return
            self.trash.remove(task)
            task.deleted_at = None
            if placeholder is not None and len(self.tasks) == 1 and self.tasks[0] is placeholder:
                self.tasks.clear()  # 撤销时移除删除后自动补的占位任务
            self.tasks.insert(min(index, len(self.tasks)), task)
            self.active_index = self.tasks.index(task)
            # 先写 tasks.json 再落回收站：任一步失败，任务都至少还存在于一份文件中
            self._persist_tasks()
            self._persist_trash()
            self._render_task_list()
            self._load_active_task()
            self._set_status("已恢复任务")

        self.show_toast(f"已删除“{task.name}”", action=undo, duration=5000)

    def show_trash(self) -> None:
        if self.running:
            self._set_status("任务运行中，停止后才能管理回收站")
            return
        existing = getattr(self, "_trash_dialog", None)
        if existing is not None and existing.winfo_exists():
            existing.focus_force()
            existing.lift()
            return
        top = tk.Toplevel(self.root)
        top.title("任务回收站")
        top.geometry(f"{S(520)}x{S(400)}")
        top.transient(self.root)
        self._trash_dialog = top
        self._center_dialog(top)
        top.focus_force()
        top.configure(bg=MAIN_BG)
        tk.Label(top, text="任务回收站", bg=MAIN_BG, fg=INK, font=F(18, "bold")).pack(anchor="w", padx=S(24), pady=(S(20), S(12)))
        listing = tk.Listbox(top, bg=LIST_BG, fg=INK, selectbackground=PILL_ACTIVE, selectforeground=GREEN_TEXT, relief="flat", highlightthickness=1, highlightbackground=BORDER, font=F(12))
        listing.pack(fill="both", expand=True, padx=S(24), pady=(0, S(12)))

        def refresh() -> None:
            listing.delete(0, tk.END)
            for item in self.trash:
                deleted = time.strftime("%Y-%m-%d %H:%M", time.localtime(item.deleted_at)) if item.deleted_at else "未知时间"
                listing.insert(tk.END, f"{item.name}（{deleted} 删除）")

        def selected() -> int | None:
            values = listing.curselection()
            return int(values[0]) if values else None

        def restore() -> None:
            index = selected()
            if index is None:
                return
            task = self.trash.pop(index)
            task.deleted_at = None
            self.tasks.append(task)
            self._persist_tasks()
            self._persist_trash()
            self._render_task_list()
            refresh()

        def purge() -> None:
            index = selected()
            if index is None:
                return
            task = self.trash[index]
            if not self._ask_confirm("永久删除", f"永久删除“{task.name}”？此操作无法撤销。", danger=True, confirm_text="永久删除"):
                return
            removed = self.trash.pop(index)
            if not self._persist_trash():
                # 落盘失败先回滚，模板文件绝不能在回收站记录尚未更新时被删除
                self.trash.insert(index, removed)
                refresh()
                return
            candidates = list(removed.steps)
            if removed.target:
                candidates.append(removed.target)
            for step in candidates:
                self.repository.delete_template_if_unused(step.template_file, self.tasks, self.trash)
            refresh()
            self._render_task_list()

        actions = tk.Frame(top, bg=MAIN_BG)
        actions.pack(fill="x", padx=S(24), pady=(0, S(18)))
        tk.Button(actions, text="恢复", command=restore, bg=GREEN, fg=ON_COLOR_FG, activebackground=GREEN_HOVER, relief="flat", padx=S(20), pady=S(8), font=F(11, "bold")).pack(side="left")
        tk.Button(actions, text="永久删除", command=purge, bg=DANGER_BG, fg=DANGER_FG, activebackground=DANGER_HOVER, relief="flat", padx=S(16), pady=S(8), font=F(11)).pack(side="left", padx=S(8))
        def close_trash() -> None:
            self._trash_dialog = None
            top.destroy()
        tk.Button(actions, text="关闭", command=close_trash, bg=SUBTLE_BG, fg=INK_SOFT, activebackground=SUBTLE_HOVER, relief="flat", padx=S(16), pady=S(8), font=F(11)).pack(side="right")
        top.protocol("WM_DELETE_WINDOW", close_trash)
        refresh()

    # ------------------------------------------------------------------
    # 热键与事件循环
    # ------------------------------------------------------------------

    def _poll_hotkeys(self) -> None:
        actions = {VK_F2: self.capture_position, VK_F6: self.toggle_run, VK_F7: self.toggle_pause, VK_ESCAPE: self._emergency_stop}
        try:
            for key, action in actions.items():
                key_state = USER32.GetAsyncKeyState(key)
                is_down = bool(key_state & 0x8000)
                if key_state & 1 or is_down and not self.hotkey_state[key]:
                    self._safe_call(action, "快捷键操作失败")
                self.hotkey_state[key] = is_down
        finally:
            # 无论本轮是否出错都必须重新排队，否则热键从此全部失灵
            self._reschedule(self._poll_hotkeys, 15)

    def _emergency_stop(self) -> None:
        if self.recording:
            self._stop_recording()
        if self.running:
            self.stop_run("已紧急停止")

    def _refresh_cursor_readout(self) -> None:
        try:
            if self.capture_armed:
                self._set_status("移动鼠标到目标位置，按 F2 捕获")
        finally:
            self._reschedule(self._refresh_cursor_readout, 500)

    def _drain_ui_events(self) -> None:
        try:
            for _ in range(100):
                try:
                    callback, arguments = self.ui_events.get_nowait()
                except queue.Empty:
                    break
                self._safe_call(callback, "界面事件处理失败", arguments)
        finally:
            # 工作线程依赖此循环执行 stop_run：循环一旦停摆，运行结束/失败后界面将永远假死
            self._reschedule(self._drain_ui_events, 15)

    def _safe_call(self, action, prefix: str, arguments: tuple = ()) -> None:
        try:
            action(*arguments)
        except Exception as error:
            self._set_status(f"{prefix}：{error}")

    def _reschedule(self, callback, delay_ms: int) -> None:
        if not self.closing:
            try:
                self.root.after(delay_ms, callback)
            except tk.TclError:
                pass

    def _update_run_info(self, text: str) -> None:
        self.run_info.configure(text=f"运行状态：{text}")
        if self.running:
            self._update_run_overlay(text)

    def _set_status(self, text: str, kind: str = "ready") -> None:
        """更新状态栏；同时记录到历史（悬停状态栏可回看最近消息，避免关键信息一闪而过）。"""
        self._status_history.append((time.time(), text, kind))
        del self._status_history[:-30]
        color = RUNNING_BG if kind == "running" else GREEN
        self.status_label.configure(text=f"●  {text}", fg=color)

    def _schedule_status_history(self) -> None:
        """悬停 500ms 才弹出，避免快速划过状态栏时闪烁；移开会取消尚未触发的弹出。"""
        self._cancel_status_history_show()
        self._history_show_job = self.root.after(500, self._show_status_history)

    def _cancel_status_history_show(self) -> None:
        job = self._history_show_job
        if job is not None:
            self._history_show_job = None
            try:
                self.root.after_cancel(job)
            except tk.TclError:
                pass

    def _show_status_history(self) -> None:
        self._cancel_status_history_show()
        if self._history_bubble is not None or not self._status_history:
            return
        bubble = tk.Toplevel(self.root)
        bubble.overrideredirect(True)
        bubble.attributes("-topmost", True)
        tk.Frame(bubble, bg=TOAST_BG, padx=S(4), pady=S(6)).pack()
        for stamp, text, kind in self._status_history[-8:][::-1]:
            row = tk.Frame(bubble, bg=TOAST_BG)
            row.pack(fill="x", padx=S(10))
            tk.Label(row, text=time.strftime("%H:%M:%S", time.localtime(stamp)), bg=TOAST_BG, fg=ICON_MUTED, font=M(10)).pack(side="left")
            tk.Label(row, text=text, bg=TOAST_BG, fg=GREEN_BRIGHT if kind == "running" else TOAST_FG, font=F(11)).pack(side="left", padx=(S(10), 0))
        bubble.update_idletasks()
        width, height = bubble.winfo_width(), bubble.winfo_height()
        x = max(S(8), self.status_label.winfo_rootx() + self.status_label.winfo_width() // 2 - width // 2)
        y = self.status_label.winfo_rooty() - height - S(8)
        bubble.geometry(f"+{x}+{max(S(8), y)}")
        self._history_bubble = bubble

    def _hide_status_history(self) -> None:
        self._cancel_status_history_show()
        bubble = getattr(self, "_history_bubble", None)
        if bubble is not None:
            self._history_bubble = None
            try:
                bubble.destroy()
            except tk.TclError:
                pass

    def _overlay_geometry(self, width: int, height: int) -> str:
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        return f"{width}x{height}+{max(S(18), (screen_width - width) // 2)}+{max(S(18), screen_height - height - S(48))}"

    def _show_run_overlay(self) -> None:
        self._hide_run_overlay()
        w, h = S(540), S(72)
        overlay = tk.Toplevel(self.root)
        overlay.overrideredirect(True)
        overlay.configure(bg=OVERLAY_BG)
        overlay.attributes("-topmost", True)
        overlay.geometry(self._overlay_geometry(w, h))
        overlay.minsize(w, h)
        overlay.maxsize(w, h)
        frame = tk.Frame(overlay, bg=OVERLAY_BG, padx=S(18), pady=S(12))
        frame.pack(fill="both", expand=True)
        tk.Label(frame, text="●", bg=OVERLAY_BG, fg=OVERLAY_ACCENT, font=F(20, "bold")).pack(side="left", padx=(0, S(12)))
        copy = tk.Frame(frame, bg=OVERLAY_BG)
        copy.pack(side="left", fill="y", expand=True)
        tk.Label(copy, text="点点正在执行", bg=OVERLAY_BG, fg=ON_COLOR_FG, font=F(13, "bold")).pack(anchor="w")
        self.run_overlay_status = tk.Label(copy, text="准备中…", bg=OVERLAY_BG, fg=OVERLAY_INK_SOFT, font=F(12))
        self.run_overlay_status.pack(anchor="w", pady=(S(3), 0))
        self.run_overlay_pause = tk.Button(frame, text="暂停  F7", command=self.toggle_pause, bg=OVERLAY_BUTTON, fg=ON_COLOR_FG, activebackground=OVERLAY_BUTTON_HOVER, activeforeground=ON_COLOR_FG, relief="flat", bd=0, padx=S(14), pady=S(7), font=F(12, "bold"))
        self.run_overlay_pause.pack(side="left", padx=(S(12), S(8)))
        tk.Button(frame, text="停止  Esc", command=lambda: self.stop_run("已停止运行"), bg=RUNNING_BG, fg=ON_COLOR_FG, activebackground=DANGER_DEEP, activeforeground=ON_COLOR_FG, relief="flat", bd=0, padx=S(14), pady=S(7), font=F(12, "bold")).pack(side="left")
        overlay.update_idletasks()
        self.run_overlay = overlay
        self.run_overlay_handle = int(USER32.GetAncestor(overlay.winfo_id(), 2) or overlay.winfo_id())
        self.root.withdraw()
        overlay.lift()

    def _show_record_overlay(self) -> None:
        self._hide_record_overlay(restore_main=False)
        overlay = tk.Toplevel(self.root)
        overlay.overrideredirect(True)
        overlay.configure(bg=OVERLAY_BG)
        overlay.attributes("-topmost", True)
        overlay.geometry(self._overlay_geometry(S(510), S(70)))
        overlay.pack_propagate(False)
        frame = tk.Frame(overlay, bg=OVERLAY_BG, padx=S(15), pady=S(10))
        frame.pack(fill="both", expand=True)
        tk.Label(frame, text="●", bg=OVERLAY_BG, fg=OVERLAY_ACCENT, font=F(20, "bold")).pack(side="left", padx=(0, S(10)))
        copy_frame = tk.Frame(frame, bg=OVERLAY_BG)
        copy_frame.pack(side="left", fill="y", expand=True)
        tk.Label(copy_frame, text="正在录制桌面输入", bg=OVERLAY_BG, fg=ON_COLOR_FG, font=F(13, "bold")).pack(anchor="w")
        tk.Label(copy_frame, text="记录点击、滚轮和键盘 · Esc 停止", bg=OVERLAY_BG, fg=OVERLAY_INK_SOFT, font=F(12)).pack(anchor="w", pady=(S(3), 0))
        tk.Button(frame, text="停止录制  Esc", command=self._stop_recording, bg=RUNNING_BG, fg=ON_COLOR_FG, activebackground=DANGER_DEEP, activeforeground=ON_COLOR_FG, relief="flat", bd=0, padx=S(13), pady=S(7), font=F(12, "bold")).pack(side="right")
        overlay.update_idletasks()
        self.record_overlay = overlay
        self.record_overlay_handle = int(USER32.GetAncestor(overlay.winfo_id(), 2) or overlay.winfo_id())
        self.root.withdraw()
        overlay.lift()

    def _hide_record_overlay(self, restore_main: bool = True) -> None:
        if self.record_overlay:
            self.record_overlay.destroy()
            self.record_overlay = None
            self.record_overlay_handle = 0
        if restore_main and not self.closing and not self.running:
            self._restore_main_window()

    def _update_run_overlay(self, text: str) -> None:
        if self.run_overlay_status:
            self.run_overlay_status.configure(text=text)

    def _run_breath_tick(self) -> None:
        """运行按钮呼吸动画：在深红和亮红之间渐变，强化运行状态。"""
        if not self.running:
            return
        phase = getattr(self, "_run_breath_phase", 0)
        t = (phase % 32) / 32.0
        intensity = 1.0 - abs(2.0 * t - 1.0)  # 0..1..0 三角波
        try:
            self.run_button.configure(bg=mix(self._RUN_BREATH_BASE, self._RUN_BREATH_PEAK, intensity))
        except Exception:
            pass
        self._run_breath_phase = phase + 1
        self._run_breath_job = self.root.after(50, self._run_breath_tick)

    def _hide_run_overlay(self) -> None:
        if self.run_overlay:
            self.run_overlay.destroy()
            self.run_overlay = None
            self.run_overlay_handle = 0
            self.run_overlay_status = None
            self.run_overlay_pause = None
        if not self.closing:
            self._restore_main_window()

    def _restore_main_window(self) -> None:
        if self._tray_hidden:
            # 主窗口被用户收进托盘：后台任务照常执行，但不主动弹出窗口
            return
        self.window_coordinator.restore_main_window()

    # ------------------------------------------------------------------
    # 托盘与后台运行
    # ------------------------------------------------------------------

    def _ensure_tray(self) -> None:
        """存在启用中的定时任务时保持托盘常驻；失败则退回前台关闭确认流程。"""
        if self._tray_icon is not None or self.closing:
            return
        if not any(schedule.enabled for schedule in self.schedules):
            return
        try:
            icon_path = Path(__file__).parent / "assets" / "app_icon.ico"
            self._tray_icon = TrayIcon(
                "点点 · 定时任务运行中",
                icon_path,
                on_show=lambda: self.ui_events.put((self._show_from_tray, ())),
                on_exit=lambda: self.ui_events.put((self._exit_from_tray, ())),
            )
            self._tray_icon.start()
        except (RuntimeError, OSError) as error:
            self._tray_icon = None
            self._set_status(f"托盘不可用：{error}")

    def _sync_tray(self) -> None:
        """托盘与启用中的定时计划保持一致：全部停用/删除后撤下托盘图标。"""
        if any(schedule.enabled for schedule in self.schedules):
            self._ensure_tray()
        else:
            self._stop_tray()

    def _stop_tray(self) -> None:
        if self._tray_icon is not None:
            try:
                self._tray_icon.stop()
            except Exception:
                pass
            self._tray_icon = None

    def _show_from_tray(self) -> None:
        self._tray_hidden = False
        self._restore_main_window()

    def _exit_from_tray(self) -> None:
        self._exit_requested = True
        self.close()

    def _minimize_to_tray(self) -> None:
        self.root.withdraw()
        self._tray_hidden = True
        self.show_toast("点点已最小化到托盘，定时任务继续执行")

    def close(self) -> None:
        if self._tray_icon is not None and self._tray_icon.is_alive() and not self._exit_requested and not self.closing:
            self._minimize_to_tray()
            return
        if not self._exit_requested and not self.closing and any(schedule.enabled for schedule in self.schedules):
            # 托盘不可用时的兜底：让用户知道退出会停掉定时任务
            confirmed = self._ask_confirm(
                "退出点点",
                "当前有启用中的定时任务。点点关闭后不会在后台执行，确定退出吗？",
                confirm_text="退出",
            )
            if not confirmed:
                return
        self.closing = True
        self._persist_window_placement()
        self.capture_armed = False
        self._hide_capture_overlay(restore_main=False)
        if self.running:
            self.stop_run("已停止运行")
        if self.recording:
            self._stop_recording(restore_main=False)
        self._hide_record_overlay(restore_main=False)
        self._hide_run_overlay()
        self.stop_event.set()
        if self.worker and self.worker.is_alive():
            # 等待正在执行的步骤（如整屏图像匹配）收尾，避免销毁窗口后仍注入输入
            self.worker.join(timeout=1.5)
        try:
            self.save_task(silent=True)
            self._persist_schedules()
            self.repository.cleanup_orphan_templates(self.tasks, self.trash)
        except OSError:
            # 磁盘异常时也要保证窗口能正常退出
            pass
        self._stop_tray()
        self.root.destroy()

    def _is_own_window(self, hwnd: int) -> bool:
        return int(hwnd) in {self.root_window_handle, self.capture_overlay_handle, self.run_overlay_handle, self.record_overlay_handle}

    def _on_window_restore(self, event: tk.Event) -> None:
        """窗口从最小化恢复时，轻量触发重绘，避免阻塞主线程。"""
        if event.widget is not self.root:
            return
        self.root.after_idle(self.root.update_idletasks)


def main() -> None:
    enable_per_monitor_dpi_awareness()
    # 单实例守护：托盘常驻后双开会导致两份定时计划同时注入输入。
    # 句柄由 winapi 记录并保持存活到进程退出（主题重启时经 release_mutex 提前释放）
    if acquire_mutex("Diandian.SingleInstance") is None:
        activate_window_by_title("点点")
        return
    root = tk.Tk()
    root.iconphoto(True, ImageTk.PhotoImage(brand_icon_image(256)))
    DesktopClicker(root)
    root.mainloop()


if __name__ == "__main__":
    main()
