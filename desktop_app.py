"""点点：免安装 Windows 桌面连点器。

The application intentionally uses Win32 APIs directly so the packaged executable
can control real desktop windows without requiring Python or third-party runtimes.
"""

from __future__ import annotations

import copy
import datetime as dt
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
import uuid
import webbrowser
from collections import OrderedDict
from pathlib import Path
from tkinter import filedialog

from PIL import Image, ImageGrab, ImageTk

from image_locator import locate_template, virtual_screen_origin
import autostart
import updater
from models import Schedule, ScheduleKind, Step, Task
from raw_input import RawInputRecorder, key_name
from run_engine import RunEngine, RunPlan
from scheduler import Scheduler
from task_repository import TaskRepository
from theme import THEME_NAME
from tray_icon import TrayIcon
from ui import CanvasPaintingMixin, DialogsMixin, ToastMixin
from usage_stats import UsageStats
import voice
from version import APP_VERSION
from widgets import (
    BORDER, BORDER_SOFT, CARD_BG, CheckBox, COMMAND_BORDER, COMMAND_DANGER,
    COMMAND_FG, COMMAND_HOVER, COMMAND_MUTED, COMMAND_SLAB, ContextMenu, DANGER_BG,
    DANGER_DEEP, DANGER_FG, DANGER_HOVER, DANGER_SOFT_BG, FIELD_BORDER, F,
    GRAY, GREEN, GREEN_BRIGHT, GREEN_HOVER, GREEN_TEXT, H_LG, H_MD, H_SM,
    H_XL, H_XS, HEADER_BG, HelpIcon,
    ICON_MUTED, INK, INK_SOFT, INFO_BLUE, LIST_BG, LIST_BORDER, MAIN_BG, M,
    NEUTRAL_HOVER, NumberField, ON_AMBER, ON_COLOR_FG, OVERLAY_ACCENT, OVERLAY_BG,
    OVERLAY_BUTTON, OVERLAY_BUTTON_HOVER, OVERLAY_INK_SOFT, PANEL_BG, PILL_ACTIVE, PillButton,
    RoundedCard, ROW_HOVER, RUNNING_BG, RUNNING_PEAK, S, Select, SIDEBAR_BG,
    SP_LG, SP_MD, SP_SM, SP_XL, SP_XS, SP_XXS, SP_XXL,
    SUBTLE_BG, SUBTLE_HOVER, TASK_ACTIVE_FG, TASK_FG, TextField,
    ThinScrollbar, TOAST_BG, TOAST_FG, TOOLBAR_BG, TOOLBAR_FG,
    TOOLBAR_HOVER, brand_icon_image, mix, paint_icon, rounded_rect,
    text_font,
)
from winapi import (
    HWND_TOPMOST, POINT, SWP_SHOWWINDOW, USER32, VK_ESCAPE, VK_F2, VK_F6,
    VK_F7, acquire_mutex, activate_window, activate_window_by_title, click_at,
    cursor_position, enable_per_monitor_dpi_awareness, find_window_by_title,
    monitor_workarea_at, monitor_workareas, paste_text, release_mutex, scroll_at,
    send_key, window_rect, window_title,
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


class StepWindowMissing(RuntimeError):
    """步骤绑定的目标窗口已关闭（按句柄与标题都无法找回）。

    抛给运行引擎中断任务并明确报错，绝不退化成按旧坐标盲点——
    点进无关窗口比停运危险得多。
    """

    def __init__(self, title: str):
        super().__init__(f"目标窗口已关闭：{title or '未知窗口'}")


class DesktopClicker(CanvasPaintingMixin, DialogsMixin, ToastMixin):
    """主窗口宿主：组合各 UI 域 Mixin（画布绘制/对话框/toast），自身保留布局、
    任务编排与运行时状态。各 Mixin 通过 self 共享宿主状态，见 ui/__init__.py。"""

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
        self._toast_fade_job: str | None = None
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
        self._f2_photo: ImageTk.PhotoImage | None = None
        self._f2_photo_key: int | None = None
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
        # 陪伴感数据：使用统计与里程碑（点击计数在 worker 线程累计，锁保护）
        self.stats = UsageStats.load(self.app_data_dir / "stats.json")
        self._stats_lock = threading.Lock()
        self._run_clicks = 0
        self._run_started_at = 0.0
        self._run_interval_ms = 0
        self._care_job: str | None = None
        today = dt.date.today()
        self._startup_milestones, self._absent_days = self.stats.note_session_start(
            today.isoformat(), (today - dt.timedelta(days=1)).isoformat())
        self._tray_icon: TrayIcon | None = None
        self._tray_hidden = False
        self._exit_requested = False
        self.closing = False
        self.update_info: updater.UpdateInfo | None = None
        self.update_banner: tk.Widget | None = None
        self._update_dialog: tk.Toplevel | None = None
        self._update_checking = False
        self._update_busy = False
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
        self.root.after(2200, self._companion_greeting)
        self.root.after(4000, self._startup_update_check)

    # ------------------------------------------------------------------
    # 界面搭建
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self._build_sidebar()
        main = tk.Frame(self.root, bg=MAIN_BG)
        main.pack(side="left", fill="both", expand=True)
        self.main_area = main
        # 背景点缀：垫底的点轨画布，只在左右留白边露出来（先于其他子控件创建，叠在最下层）
        deco = tk.Canvas(main, bg=MAIN_BG, highlightthickness=0)
        deco.place(relx=0, rely=0, relwidth=1, relheight=1)
        deco.bind("<Configure>", lambda _event: self._paint_bg_deco(deco))
        # dual-nav 双层导航：碳素细带（面包屑）→ 淡铬副导航条（标题）→ 浅色内容
        self._build_header(main)
        self._build_toolbar(main)
        body = tk.Frame(main, bg=MAIN_BG)
        body.pack(fill="both", expand=True, padx=SP_XXL, pady=(SP_XL, 0))
        self.body_area = body
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
        brand.pack(fill="x", padx=SP_XL, pady=(SP_XL, 0))
        mark = tk.Canvas(brand, width=S(56), height=S(56), bg=SIDEBAR_BG, highlightthickness=0)
        mark.pack(side="left")
        self._brand_icon_photo = ImageTk.PhotoImage(brand_icon_image(56))
        mark.create_image(S(28), S(28), image=self._brand_icon_photo)
        brand_text = tk.Frame(brand, bg=SIDEBAR_BG)
        brand_text.pack(side="left", padx=(SP_MD, 0))
        tk.Label(brand_text, text="点点 · 桌面连点器", bg=SIDEBAR_BG, fg=INK, font=F(17, "bold")).pack(anchor="w")
        tk.Label(brand_text, text="让重复操作变得更简单", bg=SIDEBAR_BG, fg=GRAY, font=F(11)).pack(anchor="w", pady=(SP_XS, 0))
        meta_row = tk.Frame(brand_text, bg=SIDEBAR_BG)
        meta_row.pack(anchor="w", pady=(SP_XS, 0))
        tk.Label(meta_row, text="By Zhgui", bg=SIDEBAR_BG, fg=ICON_MUTED, font=F(10)).pack(side="left")
        # ESRB 徽章式版本牌：琥珀底 + 碳字（文档 button-primary 配对），点击检查更新
        version_chip = tk.Label(meta_row, text=f"v{APP_VERSION}", bg=GREEN_BRIGHT, fg=ON_AMBER,
                                font=F(10, "bold"), padx=SP_XS, pady=S(1), cursor="hand2")
        version_chip.pack(side="left", padx=(SP_SM, 0))
        version_label = tk.Label(meta_row, text="检查更新", bg=SIDEBAR_BG, fg=ICON_MUTED, font=F(10), cursor="hand2")
        version_label.pack(side="left", padx=(SP_XS, 0))
        for widget in (version_chip, version_label):
            widget.bind("<Button-1>", lambda _event: self._check_updates_manually())
        self.tasks_item = PillButton(sidebar, "我的任务", self._toggle_task_list, width=S(272), height=H_XL, radius=S(10), bg=SIDEBAR_BG, hover_bg=ROW_HOVER, fg=INK, icon="tasks", icon_color=INK_SOFT, font=F(13), align="left", trailing="chevron-up")
        self.tasks_item.pack(padx=SP_LG, pady=(SP_XXL, SP_XS))
        # 底栏固定在侧边栏底部，任务再多也不会把它顶出可视区
        footer = tk.Frame(sidebar, bg=SIDEBAR_BG)
        footer.pack(side="bottom", fill="x")
        shortcuts = tk.Canvas(footer, width=S(260), height=S(118), bg=SIDEBAR_BG, highlightthickness=0)
        shortcuts.pack(side="bottom", padx=SP_XL, pady=(0, SP_LG))
        rounded_rect(shortcuts, 0, 0, S(259), S(117), S(12), fill=PANEL_BG, outline="")
        for index, (key, action) in enumerate((("F2", "捕获位置"), ("F6", "运行 / 停止"), ("F7", "暂停 / 继续"), ("Esc", "紧急停止"))):
            y = S(22) + index * S(24)
            shortcuts.create_text(S(20), y, text=key, anchor="w", fill=ICON_MUTED, font=M(11))
            shortcuts.create_text(S(62), y, text=action, anchor="w", fill=TOOLBAR_FG, font=F(11))
        # 今日战果卡：陪伴感的常驻读数（与快捷键面板同一视觉语言）
        self.stats_card = tk.Canvas(footer, width=S(260), height=S(60), bg=SIDEBAR_BG, highlightthickness=0)
        self.stats_card.pack(side="bottom", padx=SP_XL, pady=(0, SP_SM))
        self._render_stats_card()
        task_actions = tk.Frame(footer, bg=SIDEBAR_BG)
        task_actions.pack(side="bottom", fill="x", padx=SP_XL, pady=(0, SP_LG))
        PillButton(task_actions, "复制", self.duplicate_task, width=S(76), height=H_SM, radius=S(8), bg=CARD_BG, fg=INK_SOFT, border=FIELD_BORDER, hover_bg=NEUTRAL_HOVER, font=F(12)).pack(side="left")
        PillButton(task_actions, "删除", self.delete_task, width=S(76), height=H_SM, radius=S(8), bg=CARD_BG, fg=DANGER_FG, border=FIELD_BORDER, hover_bg=DANGER_BG, font=F(12)).pack(side="left", padx=(SP_XS, 0))
        self.trash_button = PillButton(task_actions, "回收站", self.show_trash, width=S(96), height=H_SM, radius=S(8), bg=CARD_BG, fg=INK_SOFT, border=FIELD_BORDER, hover_bg=NEUTRAL_HOVER, font=F(12))
        self.trash_button.pack(side="left", padx=(SP_XS, 0))
        self.new_button = PillButton(footer, "新建任务", self.new_task, width=S(260), height=H_XL, radius=S(10), bg=GREEN, fg=ON_COLOR_FG, hover_bg=GREEN_HOVER, icon="plus", icon_color=ON_COLOR_FG, font=F(14, "bold"))
        self.new_button.pack(side="bottom", padx=SP_XL, pady=(SP_LG, SP_SM))
        self.schedule_button = PillButton(footer, "定时任务", self.show_schedules, width=S(260), height=H_LG, radius=S(8), bg=CARD_BG, fg=INK_SOFT, border=FIELD_BORDER, hover_bg=NEUTRAL_HOVER, font=F(12, "bold"))
        self.schedule_button.pack(side="bottom", padx=SP_XL, pady=(0, SP_SM))
        # 中间区域只放任务列表，超出时滚动
        self.task_scroll = tk.Canvas(sidebar, bg=SIDEBAR_BG, highlightthickness=0)
        self.task_scroll.pack(fill="both", expand=True, pady=(SP_XS, SP_SM))
        self.task_column = tk.Frame(self.task_scroll, bg=SIDEBAR_BG)
        self.task_window = self.task_scroll.create_window((0, 0), window=self.task_column, anchor="nw")
        self.task_column.bind("<Configure>", lambda _event: self.task_scroll.configure(scrollregion=self.task_scroll.bbox("all")))
        self.task_scroll.bind("<Configure>", lambda event: self.task_scroll.itemconfigure(self.task_window, width=event.width))
        self.task_scroll.bind("<MouseWheel>", self._on_task_scroll)
        ThinScrollbar(sidebar, self.task_scroll)
        self._render_task_list()

    def _build_header(self, parent: tk.Frame) -> None:
        header = tk.Frame(parent, height=S(48), bg=HEADER_BG, highlightthickness=1, highlightbackground=COMMAND_BORDER)
        header.pack(fill="x")
        header.pack_propagate(False)
        halftone = self._halftone_canvas(header)
        # 必须先于 crumb/按钮创建（创建顺序决定叠放，网点垫底）；右端留出主题切换按钮的位置
        halftone.place(relx=1.0, x=-S(150), anchor="ne", width=S(120), relheight=1.0)
        crumb = tk.Frame(header, bg=HEADER_BG)
        crumb.place(x=S(36), rely=0.5, anchor="w")
        tk.Label(crumb, text="我的任务", bg=HEADER_BG, fg=COMMAND_MUTED, font=F(12)).pack(side="left")
        tk.Label(crumb, text="/", bg=HEADER_BG, fg=COMMAND_BORDER, font=F(12)).pack(side="left", padx=SP_MD)
        self.breadcrumb_task = tk.Label(crumb, text="桌面连点", bg=HEADER_BG, fg=COMMAND_FG, font=F(12, "bold"))
        self.breadcrumb_task.pack(side="left")
        self.theme_button = PillButton(
            header, "深色模式" if THEME_NAME == "light" else "浅色模式", self._toggle_theme,
            width=S(96), height=H_SM, radius=S(8), bg=COMMAND_SLAB, fg=COMMAND_MUTED,
            border=COMMAND_BORDER, hover_bg=COMMAND_HOVER, font=F(11, "bold"),
        )
        self.theme_button.place(relx=1.0, x=-S(28), rely=0.5, anchor="e")

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
        # PyInstaller onefile 用 _MEIPASS2/_PYI* 做父子进程握手，重启进程一旦继承
        # 就会跳过解包、复用旧进程正在销毁的临时目录，导致 import 崩溃，必须剥离
        restart_env = {key: value for key, value in os.environ.items() if not key.startswith(("_MEI", "_PYI"))}
        try:
            subprocess.Popen(command, env=restart_env)
        except OSError:
            self._set_status("自动重启失败，请手动重启点点")
            return
        self._exit_requested = True
        self.close()  # 复用完整收尾：停运行/录制、保存任务与计划、移除托盘图标

    def _build_toolbar(self, parent: tk.Frame) -> None:
        # 副导航条：淡铬底承载标题（dual-nav 第二层），内容与头栏对齐
        strip = tk.Frame(parent, bg=PANEL_BG)
        strip.pack(fill="x")
        inner = tk.Frame(strip, bg=PANEL_BG)
        inner.pack(fill="x", padx=SP_XXL, pady=(SP_MD, SP_MD))
        bolt = tk.Canvas(inner, width=S(31), height=H_MD, bg=PANEL_BG, highlightthickness=0)
        bolt.pack(side="left", padx=(S(1), SP_SM))
        paint_icon(bolt, "bolt", S(15), S(16), S(29), GREEN, PANEL_BG)
        copy = tk.Frame(inner, bg=PANEL_BG)
        copy.pack(side="left")
        self.title_label = tk.Label(copy, text="快速连点", bg=PANEL_BG, fg=INK, font=F(19, "bold"))
        self.title_label.pack(anchor="w")
        self.subtitle_label = tk.Label(copy, text="自定义点击位置与间隔，轻松实现自动连点", bg=PANEL_BG, fg=GRAY, font=F(14))
        self.subtitle_label.pack(anchor="w", pady=(SP_XS, 0))
        bar = tk.Frame(parent, bg=MAIN_BG)
        bar.pack(fill="x", padx=SP_XXL, pady=(SP_LG, 0))
        Select(bar, self.mode_var, ("单点连点", "多点任务", "录制操作"), width=S(108), height=H_LG, command=self._mode_changed, font=F(15, "bold")).pack(side="left")
        self.capture_button = PillButton(bar, "捕获位置 (F2)", self.arm_capture, width=S(115), height=H_LG, radius=S(8), bg=COMMAND_SLAB, fg=GREEN_BRIGHT, border=COMMAND_BORDER, hover_bg=COMMAND_HOVER, font=F(14, "bold"))
        self.capture_button.pack(side="left", padx=(SP_SM, 0))
        self.clear_position_button = PillButton(bar, "清除位置", self.clear_position, width=S(84), height=H_LG, radius=S(8), bg=COMMAND_SLAB, fg=COMMAND_DANGER, border=COMMAND_BORDER, hover_bg=COMMAND_HOVER, font=F(14, "bold"))
        self.clear_position_button.pack(side="left", padx=(SP_XS, 0))
        self.record_button = PillButton(bar, "开始录制", self.toggle_recording, width=S(84), height=H_LG, radius=S(8), bg=COMMAND_SLAB, fg=COMMAND_MUTED, border=COMMAND_BORDER, hover_bg=COMMAND_HOVER, font=F(14, "bold"))
        self.record_button.pack(side="left", padx=(SP_SM, 0))
        self.save_button = PillButton(bar, "保存任务", self.save_task, width=S(106), height=H_LG, radius=S(8), bg=COMMAND_SLAB, fg=COMMAND_FG, border=COMMAND_BORDER, hover_bg=COMMAND_HOVER, icon="floppy", icon_color=COMMAND_MUTED, font=F(12, "bold"), icon_size=S(14))
        self.save_button.pack(side="right", padx=(SP_XS, 0))
        self.export_button = PillButton(bar, "导出", self.export_task, width=S(72), height=H_LG, radius=S(8), bg=COMMAND_SLAB, fg=COMMAND_FG, border=COMMAND_BORDER, hover_bg=COMMAND_HOVER, font=F(12, "bold"))
        self.export_button.pack(side="right", padx=(SP_XS, 0))
        self.import_button = PillButton(bar, "导入", self.import_task, width=S(72), height=H_LG, radius=S(8), bg=COMMAND_SLAB, fg=COMMAND_FG, border=COMMAND_BORDER, hover_bg=COMMAND_HOVER, font=F(12, "bold"))
        self.import_button.pack(side="right", padx=(SP_XS, 0))
        self.run_button = PillButton(bar, "开始运行 (F6)", self.toggle_run, width=S(150), height=H_LG, radius=S(8), bg=GREEN, fg=ON_COLOR_FG, hover_bg=GREEN_HOVER, icon="power", icon_color=ON_COLOR_FG, font=F(12, "bold"), icon_size=S(14))
        self.run_button.pack(side="right", padx=(0, SP_SM))

    def _build_workspace(self, parent: tk.Frame) -> None:
        # 最大的面板用 45° 切角（faceplate 签名几何），其余面板保持圆角两档
        card = RoundedCard(parent, chamfer=S(20))
        card.pack(side="left", fill="both", expand=True, padx=(0, SP_MD))
        body = card.body
        head = tk.Frame(body, bg=CARD_BG)
        head.pack(fill="x", padx=SP_XL, pady=(SP_XL, 0))
        pin = tk.Canvas(head, width=S(18), height=S(18), bg=CARD_BG, highlightthickness=0)
        pin.pack(side="left")
        paint_icon(pin, "pin", S(9), S(9), S(16), GREEN, CARD_BG)
        self.workspace_kind = tk.Label(head, text="定位工作区", bg=CARD_BG, fg=INFO_BLUE, font=F(12, "bold"))
        self.workspace_kind.pack(side="left", padx=(SP_SM, 0))
        self.workspace_heading = tk.Label(body, text="在真实桌面上捕获位置", bg=CARD_BG, fg=INK, font=F(22, "bold"))
        self.workspace_heading.pack(anchor="w", padx=SP_XL, pady=(SP_SM, 0))
        self.workspace_desc = tk.Label(body, text="按 F2 或点击下方预览区捕获，捕获时点点会暂时隐藏", bg=CARD_BG, fg=GRAY, font=F(13))
        self.workspace_desc.pack(anchor="w", padx=SP_XL, pady=(SP_XS, 0))
        # 底部信息卡先占住卡片底沿，任何模式下都不会被后续内容顶出
        self.readout_frame = tk.Frame(body, bg=CARD_BG)
        self.readout_frame.pack(side="bottom", fill="x", padx=SP_XL, pady=(SP_MD, 0))
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
        path_head.pack(fill="x", pady=(0, SP_XS))
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
            PillButton(self.step_toolbar, label, command, width=text_width + S(14), height=H_MD, radius=S(8), bg=TOOLBAR_BG, fg=TOOLBAR_FG, hover_bg=TOOLBAR_HOVER, font=F(11)).pack(side="left", padx=(0, SP_XXS))
        self.step_list = tk.Listbox(self.path_panel, height=1, selectmode=tk.EXTENDED, bg=LIST_BG, fg=TOOLBAR_FG, selectbackground=PILL_ACTIVE, selectforeground=GREEN_TEXT, highlightthickness=1, highlightbackground=LIST_BORDER, relief="flat", font=M(12))
        self.step_list.pack(fill="both", expand=True, pady=(SP_MD, SP_MD))
        self.step_list.bind("<Double-Button-1>", self.edit_step)
        self.step_list.bind("<Delete>", lambda _event: self.delete_step())
        self.step_list.bind("<ButtonPress-1>", self._step_drag_start)
        self.step_list.bind("<ButtonRelease-1>", self._step_drag_end)
        self.step_list.bind("<<ListboxSelect>>", self._update_step_readout)
        self.step_list.bind("<Button-3>", self._show_step_context_menu)
        ThinScrollbar(self.path_panel, self.step_list)
        self.step_hint = tk.Label(self.path_panel, text="双击编辑 · Del 删除 · 拖动排序 · 右键更多", bg=CARD_BG, fg=GRAY, font=F(11), anchor="w")
        self.step_hint.pack(fill="x", pady=(0, SP_SM))
        # 空状态覆盖层：无步骤时盖在列表上给出引导（创建在最后，保证叠在列表之上）
        self.step_list_empty = tk.Label(self.path_panel, text="还没有步骤 · 用上方按钮添加，或按 F2 捕获位置", bg=LIST_BG, fg=ICON_MUTED, font=F(12))
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

    def _companion_greeting(self) -> None:
        """启动时说一句：归来 > 连击里程碑 > 时段问候；首次启动让位给新手引导。"""
        if self.closing or not (Path(self.repository.base_dir) / ".onboarded").exists():
            return
        if self._absent_days >= 3:
            self.show_toast(voice.welcome_back(self._absent_days))
        elif self._startup_milestones:
            kind, _, value = self._startup_milestones[-1].partition(":")
            self.show_toast(voice.milestone_line(kind, int(value)))
        else:
            self.show_toast(voice.startup_greeting(time.localtime().tm_hour))

    def _show_onboarding(self) -> None:
        """显示首次启动引导浮层：选模式 → F2捕获 → F6运行。"""
        self._onboarding_step = 0

        dialog = tk.Toplevel(self.root)
        dialog.overrideredirect(True)
        dialog.configure(bg=OVERLAY_BG)
        dialog.minsize(S(460), S(220))
        dialog.maxsize(S(460), S(220))
        self._onboarding_dialog = dialog

        card = tk.Frame(dialog, bg=CARD_BG, padx=SP_XXL, pady=SP_LG)
        card.pack(fill="both", expand=True, padx=SP_XXS, pady=SP_XXS)

        # 进度点
        dots = tk.Frame(card, bg=CARD_BG)
        dots.pack(anchor="w", pady=(0, SP_LG))
        self._onboarding_dots = []
        for i in range(3):
            dot = tk.Canvas(dots, width=S(10), height=S(10), bg=CARD_BG, highlightthickness=0)
            dot.pack(side="left", padx=(0, SP_XS))
            self._onboarding_dots.append(dot)

        title_label = tk.Label(card, text="", bg=CARD_BG, fg=INK, font=F(18, "bold"), anchor="w")
        title_label.pack(fill="x", pady=(0, SP_SM))
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
                             padx=SP_XL, pady=SP_XS, command=self._onboarding_next)
        next_btn.pack(side="right")
        self._onboarding_next_btn = next_btn

        self._onboarding_steps = self._ONBOARDING_STEPS
        self._update_onboarding_step()

        # 居中显示（固定大小），与弹窗同规则落在可视区内
        dialog.update_idletasks()
        w, h = S(460), S(220)
        x, y = self._centered_position(w, h)
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
        stack.pack(side="left", padx=(SP_SM, 0))
        title_label = tk.Label(stack, text=title, bg=CARD_BG, fg=GRAY, font=F(11))
        title_label.pack(anchor="w")
        value = tk.Label(stack, text="—", bg=CARD_BG, fg=INK, font=F(13, "bold"))
        value.pack(anchor="w", pady=(SP_XXS, 0))
        return value, title_label

    @staticmethod
    def _readout_divider(parent: tk.Frame) -> None:
        # dotted-divider：Y2K 铬面细节，用点线代替实线分隔读数项
        divider = tk.Canvas(parent, width=S(2), height=1, bg=CARD_BG, highlightthickness=0)
        divider.pack(side="left", fill="y", padx=SP_XS)

        def paint(_event=None) -> None:
            divider.delete("all")
            height = divider.winfo_height()
            if height > 4:
                divider.create_line(S(1), 2, S(1), height - 2, fill=BORDER_SOFT, dash=(2, 3))

        divider.bind("<Configure>", paint)

    def _build_settings(self, parent: tk.Frame) -> None:
        card = RoundedCard(parent, width=S(450))
        card.pack(side="right", fill="y")
        body = card.body
        head = tk.Frame(body, bg=CARD_BG)
        head.pack(fill="x", padx=SP_XL, pady=(SP_XL, 0))
        gear = tk.Canvas(head, width=S(20), height=S(20), bg=CARD_BG, highlightthickness=0)
        gear.pack(side="left")
        paint_icon(gear, "gear", S(10), S(10), S(17), INK_SOFT, CARD_BG)
        tk.Label(head, text="任务设置", bg=CARD_BG, fg=INK, font=F(16, "bold")).pack(side="left", padx=(SP_SM, 0))
        # 表单直接平铺：不滚动、不裁切，全部字段一屏完整展示
        form = tk.Frame(body, bg=CARD_BG)
        form.pack(fill="x", padx=SP_XL, pady=(SP_LG, SP_XL))
        self._field_label(form, "任务名称", "任务在左侧列表中的显示名称，最多 20 个字符。")
        TextField(form, self.task_name_var, width=S(378), height=H_XL, maxlength=20).pack(pady=(0, SP_MD))
        self._field_label(form, "鼠标动作", "每次点击使用的鼠标按键：\n· 左键单击：最常用，适用绝大多数场景\n· 右键单击：触发目标的右键菜单\n· 中键单击：按下鼠标滚轮，较少使用")
        Select(form, self.button_var, ("左键单击", "右键单击", "中键单击"), width=S(378), height=H_XL, icon="cursor", command=self._button_changed).pack(pady=(0, SP_MD))
        self._field_label(form, "点击方式", "点击位置的定位方式：\n· 窗口相对：目标窗口移动后，点击位置自动跟随窗口；跨程序任务会在每步执行前自动切换到目标窗口\n· 屏幕坐标：始终点击屏幕上的固定位置，不做窗口切换")
        Select(form, self.position_var, ("窗口相对", "屏幕坐标"), width=S(378), height=H_XL, icon="windows").pack(pady=(0, SP_MD))
        self._field_label(form, "点击间隔（秒）", "两次点击之间的等待时间，支持小数（如 0.5）。\n多点与录制任务中，作为每个步骤之间的等待间隔。")
        NumberField(form, self.interval_var, width=S(378), height=H_XL, minimum=0.05, maximum=60, step=0.1).pack(pady=(0, SP_MD))
        self._field_label(form, "执行次数（0 = 持续运行）", "整套动作重复执行的次数。\n填 0 表示一直运行，直到手动停止。")
        NumberField(form, self.repeat_var, width=S(378), height=H_XL, minimum=0, maximum=999999).pack(pady=(0, SP_LG))
        countdown_row = tk.Frame(form, bg=CARD_BG)
        countdown_row.pack(fill="x", pady=(0, SP_MD))
        CheckBox(countdown_row, "启动前倒计时（秒）", self.countdown_var).pack(side="left")
        HelpIcon(countdown_row, "点击「开始运行」后先倒计时再执行，留出时间把焦点切换到目标窗口。支持小数。").pack(side="left", padx=(SP_XS, 0))
        NumberField(countdown_row, self.countdown_seconds_var, width=S(56), height=H_XS, minimum=0, maximum=10, step=0.5, radius=S(6), compact=True).pack(side="left", padx=(SP_MD, SP_XS))
        random_row = tk.Frame(form, bg=CARD_BG)
        random_row.pack(fill="x", pady=(0, SP_XS))
        CheckBox(random_row, "随机间隔", self.random_var).pack(side="left")
        HelpIcon(random_row, "每次等待在设定幅度内随机浮动，模拟真人节奏。\n例：间隔 1 秒、幅度 20% 时，实际间隔约 0.8～1.2 秒。").pack(side="left", padx=(SP_XS, 0))
        tk.Label(random_row, text="±", bg=CARD_BG, fg=INK_SOFT, font=F(12)).pack(side="left", padx=(SP_SM, SP_XS))
        NumberField(random_row, self.random_percent_var, width=S(56), height=H_XS, minimum=0, maximum=100, step=5, radius=S(6), compact=True).pack(side="left")
        tk.Label(random_row, text="%", bg=CARD_BG, fg=INK_SOFT, font=F(12)).pack(side="left", padx=(SP_XS, 0))

    def _field_label(self, parent: tk.Widget, text: str, help_text: str | None = None) -> None:
        row = tk.Frame(parent, bg=CARD_BG)
        row.pack(anchor="w", pady=(0, SP_SM))
        tk.Label(row, text=text, bg=CARD_BG, fg=INK_SOFT, font=F(12)).pack(side="left")
        if help_text:
            HelpIcon(row, help_text).pack(side="left", padx=(SP_XS, 0), pady=(S(1), 0))

    def _build_statusbar(self, parent: tk.Frame) -> None:
        bar = tk.Frame(parent, bg=MAIN_BG, height=S(31))
        bar.pack(fill="x", padx=SP_XXL, pady=(SP_MD, SP_MD))
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
        status_dot.pack(side="right", padx=(0, SP_XS))
        paint_icon(status_dot, "target", S(7), S(7), S(10), ICON_MUTED, MAIN_BG)

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

    def _render_stats_card(self) -> None:
        """侧栏今日战果卡：今日点击 / 累计省时。信息读数用冷铬文字，不占暖色配额。"""
        card = getattr(self, "stats_card", None)
        if card is None:
            return
        card.delete("all")
        rounded_rect(card, 0, 0, S(259), S(59), S(12), fill=PANEL_BG, outline="")
        card.create_text(S(16), S(16), text="今日点击", anchor="w", fill=ICON_MUTED, font=F(10))
        card.create_text(S(16), S(38), text=voice.format_count(self.stats.today_clicks), anchor="w", fill=INK, font=F(14, "bold"))
        card.create_text(S(136), S(16), text="累计省时", anchor="w", fill=ICON_MUTED, font=F(10))
        card.create_text(S(136), S(38), text=voice.format_duration(self.stats.saved_ms), anchor="w", fill=INK, font=F(14, "bold"))

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
                    width=S(252), height=H_LG, radius=S(8),
                    bg=PILL_ACTIVE if active else SIDEBAR_BG, hover_bg=ROW_HOVER,
                    fg=TASK_ACTIVE_FG if active else TASK_FG,
                    icon="bolt" if active else "dot",
                    icon_color=GREEN if active else ICON_MUTED,
                    font=F(12, "bold") if active else F(12),
                    align="left", padx=SP_MD,
                    # 行尾前进箭头（DESIGN.md news-row 的 chevron chip 语义）：激活行暖色，其余静音
                    trailing="chevron-right",
                    trailing_color=GREEN if active else ICON_MUTED,
                )
                button.pack(padx=SP_XL, pady=S(1))
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
            self.step_list_empty.place_forget()
        else:
            self.step_list_empty.place(relx=0.5, rely=0.5, anchor="center")
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
            self.workspace.pack(side="bottom", fill="x", padx=SP_XL, pady=(0, SP_MD))
            self.readout_frame.pack(side="bottom", fill="x", padx=SP_XL, pady=(SP_MD, SP_MD))
            self.path_panel.pack(side="top", fill="both", expand=True, padx=SP_XL, pady=(SP_MD, 0))
        else:
            self.workspace_kind.configure(text="定位工作区")
            self.path_panel.pack_forget()
            self.workspace.pack_forget()
            self.readout_frame.pack_forget()
            self.readout_frame.pack(side="bottom", fill="x", padx=SP_XL, pady=(SP_MD, SP_MD))
            self.workspace.pack(side="top", fill="both", expand=True, padx=SP_XL, pady=(SP_SM, SP_SM))
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

    def arm_capture(self) -> None:
        if self.running:
            return
        self.capture_armed = True
        self.capture_button.configure(text="移动鼠标后按 F2", bg=COMMAND_HOVER)
        self._set_status("等待捕获位置")
        self._show_capture_overlay()

    def _show_capture_overlay(self) -> None:
        self._hide_capture_overlay(restore_main=False)
        overlay = tk.Toplevel(self.root)
        overlay.overrideredirect(True)
        overlay.configure(bg=OVERLAY_BG)
        overlay.attributes("-topmost", True)
        overlay.geometry(self._overlay_geometry(S(560), S(66)))
        overlay_frame = tk.Frame(overlay, bg=OVERLAY_BG, padx=SP_LG, pady=SP_SM)
        overlay_frame.pack(fill="both", expand=True)
        tk.Label(overlay_frame, text="⌖", bg=OVERLAY_BG, fg=GREEN_BRIGHT, font=("Segoe UI Symbol", -S(24), "bold")).pack(side="left", padx=(0, SP_MD))
        copy = tk.Frame(overlay_frame, bg=OVERLAY_BG)
        copy.pack(side="left", fill="y")
        tk.Label(copy, text="定位模式", bg=OVERLAY_BG, fg=ON_COLOR_FG, font=F(13, "bold")).pack(anchor="w")
        tk.Label(copy, text="把鼠标移到目标位置，按 F2 确认；Esc 取消", bg=OVERLAY_BG, fg=OVERLAY_INK_SOFT, font=F(12)).pack(anchor="w", pady=(SP_XS, 0))
        cancel = tk.Button(overlay_frame, text="取消  Esc", command=self.cancel_capture, bg=OVERLAY_BUTTON, fg=ON_COLOR_FG, activebackground=OVERLAY_BUTTON_HOVER, activeforeground=ON_COLOR_FG, relief="flat", bd=0, padx=SP_MD, pady=SP_SM, font=F(12, "bold"))
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
        self.capture_button.configure(text="捕获位置 (F2)", bg=COMMAND_SLAB)
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
            self.capture_button.configure(text="捕获位置 (F2)", bg=COMMAND_SLAB)
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
        body = tk.Frame(dlg, bg=MAIN_BG, padx=SP_XL, pady=SP_XL)
        body.pack(fill="both", expand=True)
        tk.Label(body, text="条件：图像", bg=MAIN_BG, fg=INK, font=F(12)).grid(row=0, column=0, sticky="w", pady=(0, SP_MD))
        cond_var = tk.StringVar(value="存在")
        tk.Radiobutton(body, text="存在时继续", variable=cond_var, value="存在", bg=MAIN_BG, fg=INK, font=F(12), activebackground=MAIN_BG).grid(row=0, column=1, sticky="w", padx=(SP_MD, 0))
        tk.Radiobutton(body, text="不存在时继续", variable=cond_var, value="不存在", bg=MAIN_BG, fg=INK, font=F(12), activebackground=MAIN_BG).grid(row=0, column=2, sticky="w", padx=(SP_MD, 0))
        tk.Label(body, text="不满足时跳过步数：", bg=MAIN_BG, fg=INK, font=F(12)).grid(row=1, column=0, sticky="w", pady=(0, SP_MD))
        skip_var = tk.IntVar(value=1)
        tk.Spinbox(body, from_=1, to=20, textvariable=skip_var, width=6, font=F(12)).grid(row=1, column=1, sticky="w", padx=(SP_MD, 0))
        tk.Label(body, text="匹配阈值：", bg=MAIN_BG, fg=INK, font=F(12)).grid(row=2, column=0, sticky="w", pady=(0, SP_MD))
        th_var = tk.DoubleVar(value=0.86)
        th_value = tk.Label(body, text="0.86", bg=MAIN_BG, fg=GREEN_TEXT, font=F(12, "bold"))
        th_value.grid(row=2, column=1, sticky="w", padx=(SP_MD, 0))
        def update_th_label(*_a):
            th_value.configure(text=f"{th_var.get():.2f}")
        th_var.trace_add("write", update_th_label)
        tk.Scale(body, from_=0.5, to=1.0, resolution=0.01, orient="horizontal", variable=th_var, length=S(220), bg=MAIN_BG, highlightthickness=0).grid(row=3, column=0, columnspan=3, sticky="ew", pady=(0, SP_LG))
        btn_row = tk.Frame(body, bg=MAIN_BG)
        btn_row.grid(row=4, column=0, columnspan=3, sticky="ew")
        def confirm():
            params["condition"] = cond_var.get()
            params["skip_count"] = skip_var.get()
            params["threshold"] = th_var.get()
            params["confirmed"] = True
            self._if_image_dialog = None
            dlg.destroy()
        PillButton(btn_row, "确定", confirm, width=S(80), height=H_LG, radius=S(8), bg=GREEN, fg=ON_COLOR_FG, hover_bg=GREEN_HOVER, font=F(12, "bold")).pack(side="right")
        PillButton(btn_row, "取消", lambda: (setattr(self, "_if_image_dialog", None), dlg.destroy()), width=S(80), height=H_LG, radius=S(8), bg=CARD_BG, fg=INK, border=FIELD_BORDER, hover_bg=NEUTRAL_HOVER, font=F(12)).pack(side="right", padx=(0, S(8)))
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
            self.record_button.configure(text="停止录制", bg=COMMAND_SLAB, fg=COMMAND_DANGER)
            self._show_record_overlay()
            self._set_status("正在录制鼠标、键盘和滚轮输入")

    def _stop_recording(self, restore_main: bool = True) -> None:
        self.recording = False
        self.input_recorder.stop()
        self._hide_record_overlay(restore_main=restore_main)
        self.record_button.configure(text="开始录制", bg=COMMAND_SLAB, fg=COMMAND_MUTED)
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
        self._run_started_at = time.monotonic()
        self._run_interval_ms = max(0, int(task.settings.interval_ms))
        with self._stats_lock:
            self._run_clicks = 0  # 清掉上一次运行可能的残留点击，避免串账
        self._schedule_care_reminder()
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

    def _step_hwnd(self, step: Step) -> int:
        """解析步骤当前可用的目标窗口句柄。

        句柄存活就直接用（标题变化视为正常，如浏览器切标签页改标题）；
        句柄失效（应用重启/被关）则按记录的标题找回新窗口；
        两者都失败抛 StepWindowMissing，由运行引擎停运报错。
        """
        if step.hwnd and USER32.IsWindow(step.hwnd):
            return step.hwnd
        recovered = find_window_by_title(step.title)
        if recovered and USER32.IsWindow(recovered):
            return recovered
        raise StepWindowMissing(step.title)

    def _activate_step_window(self, step: Step) -> int:
        """执行动作前把目标窗口切到前台，跨程序任务的调度关键。

        屏幕坐标模式与未绑定窗口的步骤跳过（沿用用户显式选择的语义）；
        前台切换失败抛 RuntimeError（窗口还在，只是切不上去，与窗口缺失区分）。
        返回解析出的窗口句柄供坐标换算。
        """
        if self.run_position_mode != "窗口相对" or not step.hwnd:
            return 0
        hwnd = self._step_hwnd(step)
        if not activate_window(hwnd):
            raise RuntimeError(f"无法把窗口切到前台：{step.title or '未知窗口'}")
        return hwnd

    def _execute_step(self, step: Step) -> None:
        if step.type == "click":
            hwnd = self._activate_step_window(step)
            x, y = self._resolve_position(step, hwnd)
            click_at(x, y, self._mouse_button(step.button))
            self._count_click()
        elif step.type == "scroll":
            hwnd = self._activate_step_window(step)
            x, y = self._resolve_position(step, hwnd)
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
            self._count_click()
        elif step.type == "type_text":
            if step.x or step.y or step.hwnd:
                hwnd = self._activate_step_window(step)
                x, y = self._resolve_position(step, hwnd)
                click_at(x, y, "left")
                time.sleep(0.15)
            paste_text(step.text)

    def _count_click(self) -> None:
        """worker 线程内的点击计数；锁内自增，stop_run 时一次性结算。

        「测试步骤」不走运行流程（self.running 为 False），不计入统计。
        """
        if not self.running:
            return
        with self._stats_lock:
            self._run_clicks += 1

    def _settle_run_stats(self, message: str) -> None:
        """运行结束的陪伴感结算：累计统计、保存、刷战果卡、按优先级说话。

        说话优先级（同一时刻只说一句）：里程碑 > 收工仪式（≥1 分钟的完整运行）> 沉默。
        """
        with self._stats_lock:
            clicks, run_ms = self._run_clicks, int((time.monotonic() - self._run_started_at) * 1000)
            self._run_clicks = 0
        fired = self.stats.note_run(clicks, run_ms, self._run_interval_ms, dt.date.today().isoformat())
        self.stats.save(self.app_data_dir / "stats.json")
        self._render_stats_card()
        if fired:
            kind, _, value = fired[-1].partition(":")
            self.show_toast(voice.milestone_line(kind, int(value)))
        elif message == "任务已完成" and run_ms >= 60_000:
            self.show_toast(voice.task_completed(voice.format_duration(run_ms)))

    @staticmethod
    def _mouse_button(button: str) -> str:
        return {"左": "left", "右": "right", "中": "middle"}.get(button, "left")

    def _resolve_position(self, step: Step, hwnd: int = 0) -> tuple[int, int]:
        """窗口相对模式按目标窗口当前矩形重算坐标，其余情形沿用捕获时的绝对坐标。

        hwnd 传入 _activate_step_window 解析出的活句柄；直接调用（未经激活）时
        自行解析一次，窗口缺失由 _step_hwnd 抛出明确异常。
        """
        if self.run_position_mode != "窗口相对" or not step.hwnd:
            return step.x, step.y
        if not hwnd:
            hwnd = self._step_hwnd(step)
        rect = window_rect(hwnd)
        if not rect or step.relative_x is None or step.relative_y is None:
            return step.x, step.y
        return rect.left + step.relative_x, rect.top + step.relative_y

    def stop_run(self, message: str = "已停止运行") -> None:
        if not self.running:
            return  # 重入守卫：worker 正常收尾与手动停止同帧到达时只结算一次
        self._countdown_generation += 1
        self.stop_event.set()
        self.pause_event.clear()
        for key_code in tuple(self.run_keys_down):
            try:
                send_key(key_code, key_up=True)
            except OSError:
                pass
        self.run_keys_down.clear()
        self._cancel_care_reminder()
        self.running = False
        self.paused = False
        self._settle_run_stats(message)
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

    def _schedule_care_reminder(self) -> None:
        """连续运行 30 分钟的休息关怀：每次运行至多一次，主窗隐藏期间走托盘气泡。"""
        self._cancel_care_reminder()
        self._care_job = self.root.after(30 * 60 * 1000, self._care_break_tick)

    def _care_break_tick(self) -> None:
        self._care_job = None
        if self.running and not self.closing:
            self._notify("点点", voice.care_break())

    def _cancel_care_reminder(self) -> None:
        if self._care_job:
            try:
                self.root.after_cancel(self._care_job)
            except tk.TclError:
                pass
            self._care_job = None

    def toggle_pause(self) -> None:
        if not self.running:
            return
        self.paused = not self.paused
        if self.paused:
            self.pause_event.set()
            # 暂停不计入"连续运行"：关怀计时在暂停期间挂起、恢复时重新起算
            self._cancel_care_reminder()
        else:
            self.pause_event.clear()
            self._schedule_care_reminder()
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

    def _regrab_schedule_list(self) -> None:
        """添加/编辑弹层关闭后，把模态抓取交还给定时任务列表弹层。"""
        dialog = self._schedule_dialog
        if dialog is None or not dialog.winfo_exists():
            return
        try:
            dialog.grab_set()
        except tk.TclError:
            pass

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
        # 关闭时把模态抓取交还给定时任务列表弹层，保持列表弹层的模态不被打断
        dialog.bind("<Destroy>", lambda _event: self._regrab_schedule_list())
        body = tk.Frame(dialog, bg=MAIN_BG, padx=SP_XL, pady=SP_XL)
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
        field_height = H_LG

        tk.Label(body, text="任务", bg=MAIN_BG, fg=INK_SOFT, font=F(12)).grid(row=0, column=0, sticky="w", pady=(0, SP_XS))
        Select(body, task_var, task_names, width=field_width, height=field_height, radius=S(8), border=FIELD_BORDER, font=F(12)).grid(row=1, column=0, sticky="w", pady=(0, SP_LG))

        tk.Label(body, text="触发方式", bg=MAIN_BG, fg=INK_SOFT, font=F(12)).grid(row=2, column=0, sticky="w", pady=(0, SP_XS))
        Select(body, kind_var, list(kind_labels.values()), width=field_width, height=field_height, radius=S(8), border=FIELD_BORDER, font=F(12)).grid(row=3, column=0, sticky="w", pady=(0, SP_LG))

        value_label = tk.Label(body, text="时间", bg=MAIN_BG, fg=INK_SOFT, font=F(12))
        value_label.grid(row=4, column=0, sticky="w", pady=(0, SP_XS))
        value_entry = tk.Entry(body, textvariable=value_var, width=30, font=F(12), bg=CARD_BG, fg=INK, relief="flat", highlightthickness=1, highlightbackground=FIELD_BORDER, highlightcolor=GREEN)
        value_entry.grid(row=5, column=0, sticky="w", pady=(0, SP_SM), ipady=SP_XS)
        error_label = tk.Label(body, textvariable=error_var, bg=MAIN_BG, fg=DANGER_FG, font=F(11), justify="left", wraplength=field_width)
        error_label.grid(row=6, column=0, sticky="w", pady=(0, SP_SM))

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
        PillButton(actions, "确定", confirm, width=S(84), height=H_LG, radius=S(8), bg=GREEN, fg=ON_COLOR_FG, hover_bg=GREEN_HOVER, font=F(12, "bold")).pack(side="right")
        PillButton(actions, "取消", cancel, width=S(84), height=H_LG, radius=S(8), bg=CARD_BG, fg=INK, border=FIELD_BORDER, hover_bg=NEUTRAL_HOVER, font=F(12)).pack(side="right", padx=(0, S(8)))
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
        # 应用内模态：弹层打开期间锁定主窗口交互，杜绝"切页面后弹窗仍悬浮残留"
        dialog.grab_set()

        tk.Label(dialog, text="定时任务", bg=MAIN_BG, fg=INK, font=F(18, "bold")).pack(anchor="w", padx=SP_XL, pady=(SP_LG, SP_XS))
        tk.Label(dialog, text="应用保持运行时按计划触发；关闭主窗口会最小化到托盘，任务继续执行。", bg=MAIN_BG, fg=GRAY, font=F(11)).pack(anchor="w", padx=SP_XL, pady=(0, SP_MD))
        listing = tk.Listbox(dialog, bg=LIST_BG, fg=INK, selectbackground=PILL_ACTIVE, selectforeground=GREEN_TEXT, relief="flat", highlightthickness=1, highlightbackground=BORDER, font=F(11))
        listing.pack(fill="both", expand=True, padx=SP_XL, pady=(0, SP_MD))
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
        actions.pack(fill="x", padx=SP_XL, pady=(0, SP_LG))
        PillButton(actions, "添加", lambda: self._schedule_add_dialog(refresh), width=S(84), height=H_LG, radius=S(8), bg=GREEN, fg=ON_COLOR_FG, hover_bg=GREEN_HOVER, font=F(12, "bold")).pack(side="left")
        PillButton(actions, "启用/停用", toggle_selected, width=S(100), height=H_LG, radius=S(8), bg=CARD_BG, fg=INK, border=FIELD_BORDER, hover_bg=NEUTRAL_HOVER, font=F(12)).pack(side="left", padx=SP_SM)
        PillButton(actions, "删除", delete_selected, width=S(84), height=H_LG, radius=S(8), bg=DANGER_BG, fg=DANGER_FG, border=FIELD_BORDER, hover_bg=DANGER_HOVER, font=F(12)).pack(side="left")
        def close_dialog() -> None:
            self._schedule_dialog = None
            self._schedule_listing = None
            dialog.destroy()
        PillButton(actions, "关闭", close_dialog, width=S(84), height=H_LG, radius=S(8), bg=SUBTLE_BG, fg=INK_SOFT, hover_bg=SUBTLE_HOVER, font=F(12)).pack(side="right")
        dialog.protocol("WM_DELETE_WINDOW", close_dialog)
        dialog.bind("<Escape>", lambda _event: close_dialog())

        autostart_row = tk.Frame(dialog, bg=MAIN_BG)
        autostart_row.pack(fill="x", padx=SP_XL, pady=(0, SP_LG))
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
        PillButton(autostart_row, "开机自启", toggle_autostart, width=S(96), height=H_SM, radius=S(8), bg=CARD_BG, fg=INK, border=FIELD_BORDER, hover_bg=NEUTRAL_HOVER, font=F(12, "bold")).pack(side="right")
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
            self._notify("定时任务", voice.night_watch(f"{name}：{message}", time.localtime().tm_hour))

    # ------------------------------------------------------------------
    # 自动更新
    # ------------------------------------------------------------------

    def _startup_update_check(self) -> None:
        """启动数秒后后台检查更新；24 小时节流，强制更新待办除外，绝不阻塞启动。"""
        if self.closing:
            return
        state = self.repository.load_update_state()
        required = str(state.get("required_version") or "")
        forced = bool(required) and updater.is_newer_version(required, APP_VERSION)
        try:
            last_check = float(state.get("last_check") or 0.0)
        except (TypeError, ValueError):
            last_check = 0.0
        if not forced and not updater.should_check(last_check, time.time()):
            return
        self._spawn_update_check(manual=False)

    def _check_updates_manually(self) -> None:
        if self._update_checking or self._update_busy:
            return
        self._set_status("正在检查更新…")
        self._spawn_update_check(manual=True)

    def _spawn_update_check(self, manual: bool) -> None:
        self._update_checking = True

        def worker() -> None:
            try:
                info = updater.fetch_update()
            except updater.UpdateError as error:
                self.ui_events.put((self._on_update_checked, (None, str(error), manual)))
            except Exception as error:  # 兜底：任何异常都必须回投，否则检查标志永久卡死
                self.ui_events.put((self._on_update_checked, (None, f"{type(error).__name__}: {error}", manual)))
            else:
                self.ui_events.put((self._on_update_checked, (info, None, manual)))

        threading.Thread(target=worker, name="update-check", daemon=True).start()

    def _on_update_checked(self, info: updater.UpdateInfo | None, error: str | None, manual: bool) -> None:
        self._update_checking = False
        if self.closing:
            return
        state = self.repository.load_update_state()
        state["last_check"] = time.time()
        if error is not None:
            self._save_update_state(state)
            if manual:
                self.show_toast(f"检查更新失败：{error}", kind="error")
                self._set_status("检查更新失败")
            return
        if info is None or not updater.is_newer_version(info.version, APP_VERSION):
            state.pop("required_version", None)
            self._save_update_state(state)
            if manual:
                self.show_toast(f"当前已是最新版本 v{APP_VERSION}")
                self._set_status("当前已是最新版本")
            return
        self.update_info = info
        if updater.is_update_required(info):
            # 重要缺陷修复：记录待办版本，绕过 24 小时节流，每次启动都提醒
            state["required_version"] = info.min_version
            self._save_update_state(state)
            self._show_force_update_dialog(info)
            return
        if state.pop("required_version", None) is not None:
            self._save_update_state(state)
        if info.version == state.get("skipped_version"):
            return
        self._show_update_banner(info)

    def _save_update_state(self, state: dict) -> None:
        try:
            self.repository.save_update_state(state)
        except OSError:
            pass

    def _update_action_label(self) -> str:
        """安装版按钮为「立即更新」，绿色版/源码运行降级为「前往下载」。"""
        return "前往下载" if not updater.is_installed_build() else "立即更新"

    def _show_update_banner(self, info: updater.UpdateInfo) -> None:
        """主窗口内容区顶部插入更新横幅：与任务设置卡同语言的圆角卡片。"""
        self._dismiss_update_banner()
        notes = info.notes.splitlines()[0] if info.notes else "修复已知问题，优化使用体验"
        if len(notes) > 48:
            notes = notes[:47] + "…"
        card = RoundedCard(self.main_area, height=S(84))
        card.pack(fill="x", padx=SP_XXL, pady=(SP_MD, 0), before=self.body_area)
        row = card.body
        badge = tk.Canvas(row, width=S(38), height=S(38), bg=CARD_BG, highlightthickness=0)
        badge.pack(side="left", padx=(SP_LG, SP_MD), pady=SP_MD)
        rounded_rect(badge, 0, 0, S(38), S(38), S(12), fill=SUBTLE_BG, outline="")
        paint_icon(badge, "download", S(19), S(19), S(20), GREEN, SUBTLE_BG)
        text = tk.Frame(row, bg=CARD_BG)
        text.pack(side="left")
        tk.Label(text, text=f"发现新版本 v{info.version}", bg=CARD_BG, fg=INK, font=F(13, "bold")).pack(anchor="w")
        tk.Label(text, text=notes, bg=CARD_BG, fg=GRAY, font=F(11)).pack(anchor="w", pady=(S(1), 0))
        # 主按钮先 pack 才能位于最右，与确认对话框的按钮次序一致
        PillButton(
            row, self._update_action_label(), self._install_from_banner,
            width=S(92), height=H_LG, radius=S(8), bg=GREEN, fg=ON_COLOR_FG,
            hover_bg=GREEN_HOVER, font=F(12, "bold"),
        ).pack(side="right")
        PillButton(
            row, "跳过此版本", self._skip_update_version,
            width=S(92), height=H_LG, radius=S(8), bg=CARD_BG, fg=INK_SOFT,
            border=FIELD_BORDER, hover_bg=NEUTRAL_HOVER, font=F(12),
        ).pack(side="right", padx=(0, S(16)))
        self.update_banner = card

    def _dismiss_update_banner(self) -> None:
        if self.update_banner is None:
            return
        try:
            self.update_banner.destroy()
        except tk.TclError:
            pass
        self.update_banner = None

    def _skip_update_version(self) -> None:
        info = self.update_info
        if info is not None:
            state = self.repository.load_update_state()
            state["skipped_version"] = info.version
            self._save_update_state(state)
        self._dismiss_update_banner()
        self.show_toast("已跳过该版本，后续版本发布时会再次提醒")

    def _install_from_banner(self) -> None:
        info = self.update_info
        if info is None or self._update_busy or self._update_checking:
            return
        if not info.installer_url or not updater.is_installed_build():
            # 绿色版/源码运行，或清单未提供可校验的安装包：统一引导到下载页
            webbrowser.open(info.page_url)
            self.show_toast("已在浏览器打开下载页面")
            return
        self._begin_update_download(info)

    def _begin_update_download(self, info: updater.UpdateInfo) -> None:
        """弹模态下载对话框，后台线程下载安装包，进度经 ui_events 回主线程。"""
        self._update_busy = True
        cancel = threading.Event()
        dialog = tk.Toplevel(self.root)
        dialog.title("正在更新点点")
        dialog.configure(bg=MAIN_BG)
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.protocol("WM_DELETE_WINDOW", cancel.set)
        self._update_dialog = dialog
        body = tk.Frame(dialog, bg=MAIN_BG, padx=SP_XL, pady=SP_XL)
        body.pack(fill="both", expand=True)
        tk.Label(body, text=f"正在下载点点 v{info.version}", bg=MAIN_BG, fg=INK, font=F(15, "bold")).pack(anchor="w")
        bar_width, bar_height = S(320), S(8)
        bar = tk.Canvas(body, width=bar_width, height=bar_height, bg=MAIN_BG, highlightthickness=0)
        bar.pack(anchor="w", pady=(SP_LG, SP_SM))
        status = tk.Label(body, text="准备下载…", bg=MAIN_BG, fg=GRAY, font=F(11))
        status.pack(anchor="w")

        def render_progress(received: int, total: int | None) -> None:
            if self._update_dialog is not dialog:
                return
            ratio = min(1.0, received / total) if total else 0.0
            bar.delete("all")
            rounded_rect(bar, 0, 0, bar_width, bar_height, bar_height / 2, fill=FIELD_BORDER, outline="")
            if ratio > 0:
                rounded_rect(bar, 0, 0, max(bar_height, bar_width * ratio), bar_height, bar_height / 2, fill=GREEN, outline="")
            if total:
                status.configure(text=f"已下载 {received / 1048576:.1f} / {total / 1048576:.1f} MB（{round(ratio * 100)}%）")
            else:
                status.configure(text=f"已下载 {received / 1048576:.1f} MB")

        def request_cancel() -> None:
            cancel.set()

        PillButton(
            body, "取消", request_cancel,
            width=S(88), height=H_LG, radius=S(8), bg=CARD_BG, fg=INK,
            border=FIELD_BORDER, hover_bg=NEUTRAL_HOVER, font=F(12),
        ).pack(anchor="e", pady=(SP_MD, 0))

        def worker() -> None:
            last_report = time.monotonic()

            def progress(received: int, total: int | None) -> None:
                nonlocal last_report
                now = time.monotonic()
                if now - last_report < 0.1:
                    return
                last_report = now
                self.ui_events.put((render_progress, (received, total)))

            try:
                installer_path = updater.download_installer(info, progress=progress, cancel=cancel)
            except updater.UpdateCancelled:
                self.ui_events.put((self._on_update_aborted, ()))
            except updater.UpdateError as error:
                self.ui_events.put((self._on_update_failed, (str(error),)))
            except Exception as error:  # 兜底：任何异常都必须回投，否则下载对话框会永久卡死
                self.ui_events.put((self._on_update_failed, (f"{type(error).__name__}: {error}",)))
            else:
                self.ui_events.put((render_progress, (1, 1)))
                self.ui_events.put((self._on_update_downloaded, (installer_path,)))

        threading.Thread(target=worker, name="update-download", daemon=True).start()
        self._center_dialog(dialog)

    def _on_update_downloaded(self, installer_path: Path) -> None:
        self._close_update_dialog()
        version = self.update_info.version if self.update_info else ""
        confirmed = self._ask_confirm(
            "准备安装更新",
            f"v{version} 安装包已下载并通过完整性校验。\n\n点击「立即安装」后点点会退出，安装完成后将自动启动新版。",
            confirm_text="立即安装",
            cancel_text="稍后再说",
        )
        if not confirmed:
            self.show_toast("已取消安装，可随时点击「立即更新」重试")
            return
        self._install_and_exit(installer_path)

    def _install_and_exit(self, installer_path: Path) -> None:
        """校验通过的安装包就位后：拉起静默安装器，退出点点交由安装器接管。"""
        info = self.update_info
        if info is not None and info.sha256 is not None and not updater.verify_installer(installer_path, info.sha256):
            # 下载校验与实际执行之间存在窗口期，执行前必须重算哈希
            updater.discard_installer(info)
            self.show_toast("安装包校验失败，已放弃安装", kind="error")
            return
        try:
            updater.launch_installer(installer_path)
        except OSError as error:
            self.show_toast(f"启动安装程序失败：{error}", kind="error")
            return
        # 与主题重启同理：先释放互斥体，避免安装器拉起的新版误判双开而退出
        release_mutex()
        self._exit_requested = True
        self.close()

    def _on_update_failed(self, message: str) -> None:
        self._close_update_dialog()
        self.show_toast(f"更新失败：{message}", kind="error")
        self._set_status(f"更新失败：{message}")

    def _on_update_aborted(self) -> None:
        self._close_update_dialog()
        self.show_toast("已取消更新")

    def _close_update_dialog(self) -> None:
        dialog, self._update_dialog = self._update_dialog, None
        if dialog is not None:
            try:
                dialog.destroy()
            except tk.TclError:
                pass
        self._update_busy = False

    def _show_force_update_dialog(self, info: updater.UpdateInfo) -> None:
        """重要缺陷修复的强制提醒：安装版主按钮为「立即更新」，红色「退出点点」为真实退出；绿色版引导前往下载页。"""
        installed = updater.is_installed_build()
        top = tk.Toplevel(self.root)
        top.title("发现重要更新")
        top.configure(bg=MAIN_BG)
        top.resizable(False, False)
        top.transient(self.root)
        top.grab_set()
        body = tk.Frame(top, bg=MAIN_BG, padx=SP_XL, pady=SP_XL)
        body.pack(fill="both", expand=True)
        tk.Label(body, text=f"需要更新到 v{info.version}", bg=MAIN_BG, fg=INK, font=F(16, "bold")).pack(anchor="w")
        for line in (info.notes or "该版本修复了重要问题，建议尽快更新").splitlines():
            tk.Label(body, text=line or " ", bg=MAIN_BG, fg=INK_SOFT, font=F(12), wraplength=S(380), justify="left").pack(anchor="w", pady=(SP_XS, 0))
        btn_row = tk.Frame(body, bg=MAIN_BG)
        btn_row.pack(fill="x", pady=(SP_LG, 0))

        def main_action() -> None:
            top.destroy()
            self._install_from_banner()

        def exit_application() -> None:
            top.destroy()
            # 文案承诺退出应用，必须走真实退出（先置标志避免被拦成最小化到托盘）
            self._exit_requested = True
            self.close()

        PillButton(
            btn_row, self._update_action_label(), main_action,
            width=S(96), height=H_LG, radius=S(8), bg=GREEN, fg=ON_COLOR_FG,
            hover_bg=GREEN_HOVER, font=F(13, "bold"),
        ).pack(side="right")
        PillButton(
            btn_row, "退出点点" if installed else "稍后再说",
            exit_application if installed else top.destroy,
            width=S(96), height=H_LG, radius=S(8), bg=CARD_BG,
            fg=DANGER_FG if installed else INK, border=FIELD_BORDER,
            hover_bg=DANGER_BG if installed else NEUTRAL_HOVER, font=F(13),
        ).pack(side="right", padx=(0, SP_SM))
        top.bind("<Return>", lambda _event: main_action())
        top.bind("<Escape>", lambda _event: top.destroy())
        top.protocol("WM_DELETE_WINDOW", top.destroy)
        self._center_dialog(top)

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

        self.show_toast(f"已删除“{task.name}”", action=undo)

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
        # 应用内模态：弹层打开期间锁定主窗口交互，杜绝"切页面后弹窗仍悬浮残留"
        top.grab_set()
        top.focus_force()
        top.configure(bg=MAIN_BG)
        tk.Label(top, text="任务回收站", bg=MAIN_BG, fg=INK, font=F(18, "bold")).pack(anchor="w", padx=SP_XL, pady=(SP_LG, SP_MD))
        listing = tk.Listbox(top, bg=LIST_BG, fg=INK, selectbackground=PILL_ACTIVE, selectforeground=GREEN_TEXT, relief="flat", highlightthickness=1, highlightbackground=BORDER, font=F(12))
        listing.pack(fill="both", expand=True, padx=SP_XL, pady=(0, SP_MD))

        def refresh() -> None:
            listing.delete(0, tk.END)
            for item in self.trash:
                deleted = time.strftime("%Y-%m-%d %H:%M", time.localtime(item.deleted_at)) if item.deleted_at else "未知时间"
                listing.insert(tk.END, f"{item.name}（{deleted} 删除）")
            purge_all_button.configure(state="normal" if self.trash else "disabled")

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

        def purge_items(targets: list) -> None:
            """批量永久删除：先落盘成功再清理模板文件，失败按原位置回滚内存列表。"""
            removed = sorted(((i, item) for i, item in enumerate(self.trash) if item in targets), reverse=True)
            for i, _item in removed:
                self.trash.pop(i)
            if not self._persist_trash():
                # 落盘失败先回滚，模板文件绝不能在回收站记录尚未更新时被删除
                for i, item in sorted(removed):
                    self.trash.insert(i, item)
                refresh()
                return
            for _i, item in removed:
                candidates = list(item.steps)
                if item.target:
                    candidates.append(item.target)
                for step in candidates:
                    self.repository.delete_template_if_unused(step.template_file, self.tasks, self.trash)
            refresh()
            self._render_task_list()

        def purge() -> None:
            index = selected()
            if index is None:
                return
            task = self.trash[index]
            if not self._ask_confirm("永久删除", f"永久删除“{task.name}”？此操作无法撤销。", danger=True, confirm_text="永久删除"):
                return
            top.grab_set()  # 确认弹层关闭后交还模态抓取
            purge_items([task])

        def purge_all() -> None:
            if not self.trash:
                return
            if not self._ask_confirm("清空回收站", f"永久删除回收站里的全部 {len(self.trash)} 个任务？此操作无法撤销。", danger=True, confirm_text="全部删除"):
                return
            top.grab_set()  # 确认弹层关闭后交还模态抓取
            purge_items(list(self.trash))
            self._set_status("回收站已清空")

        actions = tk.Frame(top, bg=MAIN_BG)
        actions.pack(fill="x", padx=SP_XL, pady=(0, SP_LG))
        tk.Button(actions, text="恢复", command=restore, bg=GREEN, fg=ON_COLOR_FG, activebackground=GREEN_HOVER, relief="flat", padx=SP_LG, pady=SP_SM, font=F(11, "bold")).pack(side="left")
        tk.Button(actions, text="永久删除", command=purge, bg=DANGER_BG, fg=DANGER_FG, activebackground=DANGER_HOVER, relief="flat", padx=SP_LG, pady=SP_SM, font=F(11)).pack(side="left", padx=SP_SM)
        purge_all_button = tk.Button(actions, text="删除全部", command=purge_all, bg=DANGER_BG, fg=DANGER_FG, activebackground=DANGER_HOVER, relief="flat", padx=SP_LG, pady=SP_SM, font=F(11))
        purge_all_button.pack(side="left")
        def close_trash() -> None:
            self._trash_dialog = None
            top.destroy()
        tk.Button(actions, text="关闭", command=close_trash, bg=SUBTLE_BG, fg=INK_SOFT, activebackground=SUBTLE_HOVER, relief="flat", padx=SP_LG, pady=SP_SM, font=F(11)).pack(side="right")
        top.protocol("WM_DELETE_WINDOW", close_trash)
        top.bind("<Escape>", lambda _event: close_trash())
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
        tk.Frame(bubble, bg=TOAST_BG, padx=SP_XS, pady=SP_XS).pack()
        for stamp, text, kind in self._status_history[-8:][::-1]:
            row = tk.Frame(bubble, bg=TOAST_BG)
            row.pack(fill="x", padx=SP_SM)
            tk.Label(row, text=time.strftime("%H:%M:%S", time.localtime(stamp)), bg=TOAST_BG, fg=ICON_MUTED, font=M(10)).pack(side="left")
            tk.Label(row, text=text, bg=TOAST_BG, fg=GREEN_BRIGHT if kind == "running" else TOAST_FG, font=F(11)).pack(side="left", padx=(SP_SM, 0))
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
        frame = tk.Frame(overlay, bg=OVERLAY_BG, padx=SP_LG, pady=SP_MD)
        frame.pack(fill="both", expand=True)
        tk.Label(frame, text="●", bg=OVERLAY_BG, fg=OVERLAY_ACCENT, font=F(20, "bold")).pack(side="left", padx=(0, SP_MD))
        copy = tk.Frame(frame, bg=OVERLAY_BG)
        copy.pack(side="left", fill="y", expand=True)
        tk.Label(copy, text="点点正在执行", bg=OVERLAY_BG, fg=ON_COLOR_FG, font=F(13, "bold")).pack(anchor="w")
        self.run_overlay_status = tk.Label(copy, text="准备中…", bg=OVERLAY_BG, fg=OVERLAY_INK_SOFT, font=F(12))
        self.run_overlay_status.pack(anchor="w", pady=(SP_XS, 0))
        self.run_overlay_pause = tk.Button(frame, text="暂停  F7", command=self.toggle_pause, bg=OVERLAY_BUTTON, fg=ON_COLOR_FG, activebackground=OVERLAY_BUTTON_HOVER, activeforeground=ON_COLOR_FG, relief="flat", bd=0, padx=SP_MD, pady=SP_SM, font=F(12, "bold"))
        self.run_overlay_pause.pack(side="left", padx=(SP_MD, SP_SM))
        tk.Button(frame, text="停止  Esc", command=lambda: self.stop_run("已停止运行"), bg=RUNNING_BG, fg=ON_COLOR_FG, activebackground=DANGER_DEEP, activeforeground=ON_COLOR_FG, relief="flat", bd=0, padx=SP_MD, pady=SP_SM, font=F(12, "bold")).pack(side="left")
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
        frame = tk.Frame(overlay, bg=OVERLAY_BG, padx=SP_LG, pady=SP_SM)
        frame.pack(fill="both", expand=True)
        tk.Label(frame, text="●", bg=OVERLAY_BG, fg=OVERLAY_ACCENT, font=F(20, "bold")).pack(side="left", padx=(0, SP_SM))
        copy_frame = tk.Frame(frame, bg=OVERLAY_BG)
        copy_frame.pack(side="left", fill="y", expand=True)
        tk.Label(copy_frame, text="正在录制桌面输入", bg=OVERLAY_BG, fg=ON_COLOR_FG, font=F(13, "bold")).pack(anchor="w")
        tk.Label(copy_frame, text="记录点击、滚轮和键盘 · Esc 停止", bg=OVERLAY_BG, fg=OVERLAY_INK_SOFT, font=F(12)).pack(anchor="w", pady=(SP_XS, 0))
        tk.Button(frame, text="停止录制  Esc", command=self._stop_recording, bg=RUNNING_BG, fg=ON_COLOR_FG, activebackground=DANGER_DEEP, activeforeground=ON_COLOR_FG, relief="flat", bd=0, padx=SP_MD, pady=SP_SM, font=F(12, "bold")).pack(side="right")
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
                on_check_update=lambda: self.ui_events.put((self._check_updates_manually, ())),
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
            self.stats.save(self.app_data_dir / "stats.json")
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
