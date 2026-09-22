"""点点：免安装 Windows 桌面连点器。

The application intentionally uses Win32 APIs directly so the packaged executable
can control real desktop windows without requiring Python or third-party runtimes.
"""

from __future__ import annotations

import ctypes
import copy
import json
import math
import os
import queue
import random
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import uuid
from ctypes import wintypes
from pathlib import Path
from tkinter import messagebox, simpledialog, filedialog

from PIL import Image, ImageDraw, ImageGrab, ImageTk

from image_locator import locate_template, virtual_screen_origin
from models import Step, Task
from raw_input import RawInputRecorder, key_name
from task_repository import TaskRepository


USER32 = ctypes.WinDLL("user32", use_last_error=True)
KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)

VK_ESCAPE = 0x1B
VK_F2 = 0x71
VK_F6 = 0x75
VK_F7 = 0x76
VK_LBUTTON = 0x01
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_SCANCODE = 0x0008
EXTENDED_KEYS = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x5B, 0x5C}


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG), ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("union",)
    _fields_ = [("type", wintypes.DWORD), ("union", INPUT_UNION)]


USER32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
USER32.GetCursorPos.restype = wintypes.BOOL
USER32.GetForegroundWindow.restype = wintypes.HWND
USER32.WindowFromPoint.argtypes = [POINT]
USER32.WindowFromPoint.restype = wintypes.HWND
USER32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
USER32.GetWindowTextW.restype = ctypes.c_int
USER32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
USER32.GetWindowRect.restype = wintypes.BOOL
USER32.IsWindow.argtypes = [wintypes.HWND]
USER32.IsWindow.restype = wintypes.BOOL
USER32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
USER32.SetCursorPos.restype = wintypes.BOOL
USER32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
USER32.SendInput.restype = wintypes.UINT
USER32.GetAsyncKeyState.argtypes = [ctypes.c_int]
USER32.GetAsyncKeyState.restype = ctypes.c_short
USER32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
USER32.GetAncestor.restype = wintypes.HWND
USER32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
USER32.MapVirtualKeyW.restype = wintypes.UINT
USER32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
USER32.SetWindowPos.restype = wintypes.BOOL
HWND_TOPMOST = -1
SWP_SHOWWINDOW = 0x0040

GWLP_WNDPROC = -4
WM_DESTROY = 0x0002
WM_SIZE = 0x0005
WM_CAPTURECHANGED = 0x0215
WM_SIZING = 0x0214
WM_EXITSIZEMOVE = 0x0233
LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
USER32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
USER32.CallWindowProcW.restype = LRESULT
USER32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
USER32.SetWindowLongPtrW.restype = LRESULT


def enable_per_monitor_dpi_awareness() -> None:
    try:
        USER32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        USER32.SetProcessDPIAware()


class ResizeThrottle:
    """拖拽缩放时冻结 Tk 的逐帧重排，松手后一次性应用最终尺寸。

    Tk 会为每一次 WM_SIZE 同步重排并重绘全部子窗口（本应用约 150 个，
    实测单步 200ms+），拖动边框时表现为严重卡顿。这里在 Win32 层把
    拖拽期间连续到达的 WM_SIZE 拦下、只保留最后一条，拖拽过程由系统
    拉伸旧画面呈现平滑的冻结预览；拖拽结束时把最终尺寸交付给 Tk，
    完成一次整体重排。暂存尺寸始终对应当前真实窗口尺寸（Esc 取消时
    系统恢复原尺寸也会补发 WM_SIZE），因此交付永远得到正确布局。
    """

    def __init__(self, hwnd: int) -> None:
        self._hwnd = int(hwnd)
        self._dragging = False
        self._pending: tuple[int, int] | None = None
        self._proc = WNDPROC(self._window_proc)  # 引用须常驻，防止回调被垃圾回收
        self._default_proc = USER32.SetWindowLongPtrW(self._hwnd, GWLP_WNDPROC, ctypes.cast(self._proc, ctypes.c_void_p))
        if not self._default_proc:
            raise OSError(ctypes.get_last_error(), "无法替换窗口过程")

    def _window_proc(self, hwnd, msg, wparam, lparam) -> int:
        if msg == WM_SIZING:
            self._dragging = True
        elif msg == WM_SIZE and self._dragging:
            self._pending = (wparam, lparam)
            return 0
        elif msg in (WM_EXITSIZEMOVE, WM_CAPTURECHANGED):
            pending, self._pending = self._pending, None
            self._dragging = False
            if pending is not None:
                USER32.CallWindowProcW(self._default_proc, hwnd, WM_SIZE, *pending)
        elif msg == WM_DESTROY:
            USER32.SetWindowLongPtrW(self._hwnd, GWLP_WNDPROC, ctypes.c_void_p(self._default_proc))
        return USER32.CallWindowProcW(self._default_proc, hwnd, msg, wparam, lparam)


def cursor_position() -> tuple[int, int]:
    point = POINT()
    if not USER32.GetCursorPos(ctypes.byref(point)):
        raise OSError(ctypes.get_last_error(), "无法读取鼠标位置")
    return point.x, point.y


def window_title(hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(512)
    USER32.GetWindowTextW(hwnd, buffer, len(buffer))
    return buffer.value.strip() or "未命名窗口"


def window_rect(hwnd: int) -> RECT | None:
    rect = RECT()
    return rect if USER32.GetWindowRect(hwnd, ctypes.byref(rect)) else None


def click_at(x: int, y: int, button: str = "left") -> None:
    flags = {"left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP), "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP), "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP)}[button]
    USER32.SetCursorPos(x, y)
    inputs = (INPUT * 2)(
        INPUT(type=INPUT_MOUSE, mi=MOUSEINPUT(dwFlags=flags[0])),
        INPUT(type=INPUT_MOUSE, mi=MOUSEINPUT(dwFlags=flags[1])),
    )
    if USER32.SendInput(2, inputs, ctypes.sizeof(INPUT)) != 2:
        raise OSError(ctypes.get_last_error(), "Windows 未接受输入事件")


def send_key(key_code: int, key_up: bool = False) -> None:
    flags = KEYEVENTF_SCANCODE
    if key_up:
        flags |= KEYEVENTF_KEYUP
    if key_code in EXTENDED_KEYS:
        flags |= KEYEVENTF_EXTENDEDKEY
    scan_code = USER32.MapVirtualKeyW(key_code, 0)
    entry = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wScan=scan_code, dwFlags=flags))
    if USER32.SendInput(1, ctypes.byref(entry), ctypes.sizeof(INPUT)) != 1:
        raise OSError(ctypes.get_last_error(), "Windows 未接受键盘输入事件")


def scroll_at(x: int, y: int, delta: int) -> None:
    USER32.SetCursorPos(x, y)
    entry = INPUT(type=INPUT_MOUSE, mi=MOUSEINPUT(mouseData=ctypes.c_ulong(delta).value, dwFlags=MOUSEEVENTF_WHEEL))
    if USER32.SendInput(1, ctypes.byref(entry), ctypes.sizeof(INPUT)) != 1:
        raise OSError(ctypes.get_last_error(), "Windows 未接受滚轮输入事件")


def paste_text(text: str) -> None:
    """把文字写入剪贴板后发送 Ctrl+V 粘贴，中英文通用。"""
    if not text:
        return
    USER32.OpenClipboard(0)
    USER32.EmptyClipboard()
    try:
        data = ctypes.create_unicode_buffer(text)
        handle = KERNEL32.GlobalAlloc(0x2000, ctypes.sizeof(data))
        locked = KERNEL32.GlobalLock(handle)
        ctypes.memmove(locked, data, ctypes.sizeof(data))
        KERNEL32.GlobalUnlock(handle)
        USER32.SetClipboardData(13, handle)
    finally:
        USER32.CloseClipboard()
    time.sleep(0.05)
    ctrl, vk = 0x11, 0x56
    for code, up in ((ctrl, False), (vk, False), (vk, True), (ctrl, True)):
        send_key(code, key_up=up)
        time.sleep(0.02)


# ---------------------------------------------------------------------------
# 界面调色板、DPI 缩放与绘制工具
# ---------------------------------------------------------------------------

FONT = "Microsoft YaHei UI"
MONO = "Consolas"

INK = "#22333d"
INK_SOFT = "#5d7280"
GRAY = "#8a9aa0"
GREEN = "#4a8a7a"
GREEN_HOVER = "#3d7a6c"
GREEN_TEXT = "#3d7a6c"
GREEN_DEEP = "#356a5e"
SIDEBAR_BG = "#f2f5f4"
MAIN_BG = "#f5f7f6"
HEADER_BG = "#fafcfb"
CARD_BG = "#ffffff"
BORDER = "#e8eceb"
FIELD_BORDER = "#e7ebea"
PILL_ACTIVE = "#dde6e2"
PANEL_BG = "#e6ebe8"
RED = "#e5484d"

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


def _system_dpi_scale() -> float:
    try:
        USER32.GetDpiForSystem.restype = wintypes.UINT
        return max(1.0, USER32.GetDpiForSystem() / 96)
    except (AttributeError, OSError):
        return 1.0


SCALE = _system_dpi_scale()


def S(value: float) -> int:
    """按系统 DPI 缩放界面尺寸。"""
    return round(value * SCALE)


def F(size: float, *styles: str) -> tuple:
    """按系统 DPI 缩放的界面字体（像素字号）。"""
    return (FONT, -round(size * SCALE), *styles)


def M(size: float, *styles: str) -> tuple:
    """按系统 DPI 缩放的等宽字体（像素字号）。"""
    return (MONO, -round(size * SCALE), *styles)


def _rgb(color: str) -> tuple[float, float, float]:
    value = color.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def _hex(rgb: tuple[float, float, float]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(max(0, min(255, round(v))) for v in rgb))


def shade(color: str, factor: float) -> str:
    return _hex(tuple(v * factor for v in _rgb(color)))


def mix(color: str, other: str, ratio: float) -> str:
    a, b = _rgb(color), _rgb(other)
    return _hex(tuple(a[i] + (b[i] - a[i]) * ratio for i in range(3)))


_FONTS: dict[tuple, tkfont.Font] = {}


def text_font(spec: tuple) -> tkfont.Font:
    key = tuple(spec)
    if key not in _FONTS:
        _FONTS[key] = tkfont.Font(family=spec[0], size=spec[1], weight=spec[2] if len(spec) > 2 else "normal")
    return _FONTS[key]


def rounded_rect(canvas: tk.Canvas, x1: float, y1: float, x2: float, y2: float, radius: float, **kw):
    radius = max(0.0, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
    points = (
        x1 + radius, y1, x2 - radius, y1,
        x2, y1, x2, y1 + radius,
        x2, y2 - radius, x2, y2,
        x2 - radius, y2, x1 + radius, y2,
        x1, y2, x1, y2 - radius,
        x1, y1 + radius, x1, y1,
    )
    return canvas.create_polygon(points, smooth=True, **kw)


def dot_grid(canvas: tk.Canvas, x1: float, y1: float, x2: float, y2: float, step: float, color: str, radius: float = 1) -> None:
    """在矩形范围内铺一层点阵网格，类似设计工具画布，暗示这是可定位的空间。"""
    start_x = x1 + step / 2
    start_y = y1 + step / 2
    column = start_x
    while column < x2:
        row = start_y
        while row < y2:
            canvas.create_oval(column - radius, row - radius, column + radius, row + radius, fill=color, outline="")
            row += step
        column += step


def paint_icon(canvas: tk.Canvas, name: str, cx: float, cy: float, size: float, color: str, bg: str = "#ffffff") -> None:
    """在画布上绘制矢量小图标，(cx, cy) 为图标中心，size 为已缩放的边长。PIL 3x 超采样抗锯齿。"""
    key = (name, color, int(size), bg)
    if key not in _ICON_PHOTO_CACHE:
        _ICON_PHOTO_CACHE[key] = ImageTk.PhotoImage(_render_icon_image(name, color, int(size), bg))
    canvas.create_image(cx, cy, image=_ICON_PHOTO_CACHE[key])


_ICON_PHOTO_CACHE: dict[tuple, ImageTk.PhotoImage] = {}


def _render_icon_image(name: str, color: str, size: int, bg: str = "#ffffff") -> Image.Image:
    """用 PIL 3x 超采样渲染图标，返回抗锯齿的 RGBA 图像。"""
    scale = 3
    big = size * scale
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    sw = max(2, round(size * 0.12)) * scale

    def P(points):
        return [(p[0] * big, p[1] * big) for p in points]

    def R(fx1, fy1, fx2, fy2):
        return [fx1 * big, fy1 * big, fx2 * big, fy2 * big]

    if name == "bolt":
        draw.polygon(P([(0.58, 0.04), (0.22, 0.54), (0.44, 0.54), (0.34, 0.96), (0.78, 0.4), (0.52, 0.4), (0.66, 0.04)]), fill=color)
    elif name == "cursor":
        draw.polygon(P([(0.22, 0.06), (0.22, 0.74), (0.4, 0.58), (0.5, 0.92), (0.6, 0.86), (0.5, 0.54), (0.74, 0.52)]), fill=color)
    elif name == "tasks":
        draw.rounded_rectangle(R(0.08, 0.08, 0.92, 0.92), radius=0.18 * big, outline=color, width=sw)
        for fy in (0.32, 0.52, 0.72):
            draw.line(P([(0.26, fy), (0.74, fy)]), fill=color, width=sw)
            draw.ellipse(R(0.14, fy - 0.04, 0.2, fy + 0.04), fill=color)
    elif name == "crosshair":
        draw.ellipse(R(0.22, 0.22, 0.78, 0.78), outline=color, width=sw)
        draw.line(P([(0.5, 0.08), (0.5, 0.3)]), fill=color, width=sw)
        draw.line(P([(0.5, 0.7), (0.5, 0.92)]), fill=color, width=sw)
        draw.line(P([(0.08, 0.5), (0.3, 0.5)]), fill=color, width=sw)
        draw.line(P([(0.7, 0.5), (0.92, 0.5)]), fill=color, width=sw)
    elif name == "pin":
        draw.polygon(P([(0.5, 0.96), (0.2, 0.5), (0.2, 0.3), (0.5, 0.06), (0.8, 0.3), (0.8, 0.5)]), fill=color)
        draw.ellipse(R(0.36, 0.26, 0.64, 0.5), fill=bg)
    elif name == "windows":
        draw.rounded_rectangle(R(0.08, 0.1, 0.92, 0.9), radius=0.1 * big, outline=color, width=sw)
        draw.line(P([(0.08, 0.32), (0.92, 0.32)]), fill=color, width=sw)
        draw.line(P([(0.5, 0.32), (0.5, 0.9)]), fill=color, width=sw)
    elif name == "target":
        draw.ellipse(R(0.14, 0.14, 0.86, 0.86), outline=color, width=sw)
        draw.ellipse(R(0.34, 0.34, 0.66, 0.66), outline=color, width=sw)
        draw.ellipse(R(0.44, 0.44, 0.56, 0.56), fill=color)
    elif name == "play":
        draw.polygon(P([(0.32, 0.14), (0.84, 0.5), (0.32, 0.86)]), fill=color)
    elif name == "floppy":
        draw.rounded_rectangle(R(0.12, 0.08, 0.88, 0.92), radius=0.1 * big, outline=color, width=sw)
        draw.rounded_rectangle(R(0.3, 0.08, 0.7, 0.38), radius=0.06 * big, fill=color)
        draw.rounded_rectangle(R(0.24, 0.5, 0.76, 0.88), radius=0.06 * big, outline=color, width=sw)
    elif name in ("chevron-up", "chevron-down"):
        up = name == "chevron-up"
        pts = [(0.24, 0.64 if up else 0.36), (0.5, 0.36 if up else 0.64), (0.76, 0.64 if up else 0.36)]
        draw.line(P(pts), fill=color, width=sw, joint="curve")
    elif name == "plus":
        draw.line(P([(0.5, 0.18), (0.5, 0.82)]), fill=color, width=sw)
        draw.line(P([(0.18, 0.5), (0.82, 0.5)]), fill=color, width=sw)
    elif name == "dot":
        draw.ellipse(R(0.36, 0.36, 0.64, 0.64), fill=color)
    elif name == "gear":
        draw.ellipse(R(0.3, 0.3, 0.7, 0.7), outline=color, width=sw)
        draw.ellipse(R(0.42, 0.42, 0.58, 0.58), fill=color)
        for angle in range(0, 360, 45):
            rad = math.radians(angle)
            dx, dy = math.cos(rad), math.sin(rad)
            draw.line(P([(0.5 + dx * 0.28, 0.5 + dy * 0.28), (0.5 + dx * 0.42, 0.5 + dy * 0.42)]), fill=color, width=sw)

    return image.resize((size, size), Image.Resampling.LANCZOS)

def brand_icon_image(size: int = 256) -> Image.Image:
    """应用图标：优先加载 assets/app_icon.png，自动加圆角蒙版；不存在时回退到绘制默认图标。"""
    icon_path = Path(__file__).parent / "assets" / "app_icon.png"
    if icon_path.exists():
        with Image.open(icon_path) as src:
            img = src.convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
        # 加圆角蒙版，半径为尺寸的 22%（iOS 风格）
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=round(size * 0.22), fill=255)
        img.putalpha(mask)
        return img
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=round(size * 14 / 48), fill=GREEN)
    margin = size * 0.22
    box = size - margin * 2
    points = [(0.24, 0.05), (0.24, 0.77), (0.42, 0.61), (0.53, 0.95), (0.64, 0.89), (0.53, 0.58), (0.76, 0.55)]
    draw.polygon([(margin + fx * box, margin + fy * box) for fx, fy in points], fill="#ffffff")
    return image


class PillButton(tk.Canvas):
    """圆角按钮：支持图标、悬停、禁用与运行中改写文本/配色。"""

    def __init__(self, master, text: str, command=None, *, width, height, radius=10,
                 bg="#ffffff", fg=INK, border=None, hover_bg=None, icon=None,
                 icon_color=None, font=None, align="center", padx=18, trailing=None, icon_size=None):
        self.command = command
        self._width = width
        self._height = height
        self.radius = radius
        self._bg = bg
        self._fg = fg
        self._border = border
        self._hover_bg = hover_bg or (shade(bg, 0.94) if border else shade(bg, 0.9))
        self._icon = icon
        self._icon_color = icon_color or fg
        self._icon_size = icon_size or S(18)
        self._text = text
        self._font = font or F(13)
        self._align = align
        self._padx = padx
        self._trailing = trailing
        self._state = "normal"
        self._hover = False
        self._hover_progress = 0.0
        self._hover_job = None
        super().__init__(master, width=width, height=height, bg=master["bg"], highlightthickness=0, bd=0, cursor="hand2")
        self._render()
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)

    def _content_width(self) -> float:
        total = text_font(self._font).measure(self._text)
        if self._icon:
            total += self._icon_size + S(8)
        return total

    def _render(self) -> None:
        self.delete("all")
        w, h = self._width, self._height
        enabled = self._state == "normal"
        bg = mix(self._bg, self._hover_bg, self._hover_progress) if enabled else self._bg
        if self._border:
            rounded_rect(self, 0, 0, w - 1, h - 1, self.radius, fill=self._border, outline="")
            rounded_rect(self, 1, 1, w - 2, h - 2, max(2, self.radius - 1), fill=bg, outline="")
        else:
            rounded_rect(self, 0, 0, w - 1, h - 1, self.radius, fill=bg, outline="")
        fg = self._fg if enabled else mix(self._fg, self._bg, 0.45)
        icon_color = self._icon_color if enabled else mix(self._icon_color, self._bg, 0.45)
        x = self._padx if self._align == "left" else (w - self._content_width()) / 2
        if self._icon:
            paint_icon(self, self._icon, x + self._icon_size / 2, h / 2, self._icon_size, icon_color, bg)
            x += self._icon_size + S(8)
        self.create_text(x, h / 2, text=self._text, anchor="w", fill=fg, font=self._font)
        if self._trailing:
            paint_icon(self, self._trailing, w - S(20), h / 2, S(14), "#9db0aa", bg)

    def set_trailing(self, name: str) -> None:
        self._trailing = name
        self._render()

    def _on_enter(self, _event) -> None:
        self._hover = True
        self._animate_hover(1.0)

    def _on_leave(self, _event) -> None:
        self._hover = False
        self._animate_hover(0.0)

    def _animate_hover(self, target: float) -> None:
        if self._hover_job is not None:
            self.after_cancel(self._hover_job)
            self._hover_job = None
        if self._state != "normal" or abs(self._hover_progress - target) < 0.01:
            self._hover_progress = target
            self._render()
            return
        step = 0.34 if target > self._hover_progress else -0.34
        self._hover_progress = max(0.0, min(1.0, self._hover_progress + step))
        self._render()
        if abs(self._hover_progress - target) >= 0.01:
            self._hover_job = self.after(20, lambda: self._animate_hover(target))

    def _on_click(self, _event) -> None:
        if self._state == "normal" and self.command:
            self.command()

    def configure(self, cnf=None, **kw):
        if cnf:
            kw.update(cnf)
        if "text" in kw:
            self._text = kw.pop("text")
        if "bg" in kw:
            self._bg = kw.pop("bg")
            self._hover_bg = shade(self._bg, 0.94) if self._border else shade(self._bg, 0.9)
        if "fg" in kw:
            self._fg = kw.pop("fg")
        if "state" in kw:
            self._state = kw.pop("state")
            super().configure(cursor="hand2" if self._state == "normal" else "arrow")
        if kw:
            super().configure(kw)
        self._render()


class Select(tk.Canvas):
    """圆角下拉选择框，含弹出选项列表。"""

    def __init__(self, master, variable: tk.StringVar, values, *, width, height, radius=10,
                 icon=None, icon_color=GRAY, command=None, border=FIELD_BORDER, font=None):
        self.variable = variable
        self.values = tuple(values)
        self.command = command
        self._width = width
        self._height = height
        self.radius = radius
        self._icon = icon
        self._icon_color = icon_color
        self._border = border
        self._font = font or F(12)
        self._hover = False
        self._popup: tk.Toplevel | None = None
        self._popup_canvas: tk.Canvas | None = None
        self._rows: tuple[tuple[float, float, str], ...] = ()
        self._outside_bind_id: str | None = None
        self._reopen_guard = False
        super().__init__(master, width=width, height=height, bg=master["bg"], highlightthickness=0, bd=0, cursor="hand2")
        self._render()
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._toggle)
        # 常驻监听：任何按钮释放都解除"抑制重新展开"标记，见 _close_popup_on_focusout
        self.bind_all("<ButtonRelease-1>", self._clear_reopen_guard, add="+")
        variable.trace_add("write", lambda *_: self._render())

    def _render(self) -> None:
        self.delete("all")
        w, h = self._width, self._height
        border = shade(self._border, 0.86) if (self._hover or self._popup) else self._border
        rounded_rect(self, 0, 0, w - 1, h - 1, self.radius, fill=border, outline="")
        rounded_rect(self, 1, 1, w - 2, h - 2, max(2, self.radius - 1), fill="#ffffff", outline="")
        x = S(16)
        if self._icon:
            paint_icon(self, self._icon, x + S(8), h / 2, S(16), self._icon_color)
            x += S(26)
        self.create_text(x, h / 2, text=self.variable.get(), anchor="w", fill=INK, font=self._font)
        paint_icon(self, "chevron-up" if self._popup else "chevron-down", w - S(24), h / 2, S(14), "#9db0aa")

    def _on_enter(self, _event) -> None:
        self._hover = True
        self._render()

    def _on_leave(self, _event) -> None:
        self._hover = False
        self._render()

    def _toggle(self, _event=None) -> None:
        if self._popup:
            self._close_popup()
        elif self._reopen_guard:
            # 忽略引发收起的同一次物理点击（见 _close_popup_on_focusout）
            self._clear_reopen_guard()
        else:
            self._open_popup()

    def _open_popup(self) -> None:
        if self._popup:
            return
        row_h, pad = S(42), S(6)
        width, height = self._width, pad * 2 + row_h * len(self.values)
        x, y = self.winfo_rootx(), self.winfo_rooty() + self._height + S(6)
        if y + height > self.winfo_screenheight() - S(8):
            y = self.winfo_rooty() - height - S(6)
        top = tk.Toplevel(self)
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        top.geometry(f"{width}x{height}+{x}+{y}")
        canvas = tk.Canvas(top, width=width, height=height, bg="#ffffff", highlightthickness=0)
        canvas.pack()
        rounded_rect(canvas, 0, 0, width - 1, height - 1, self.radius, fill=FIELD_BORDER, outline="")
        rounded_rect(canvas, 1, 1, width - 2, height - 2, max(2, self.radius - 1), fill="#ffffff", outline="")
        rows = []
        for index, value in enumerate(self.values):
            y0 = pad + index * row_h
            selected = value == self.variable.get()
            rounded_rect(canvas, S(6), y0 + 2, width - S(6), y0 + row_h - 2, S(8), fill=PILL_ACTIVE if selected else "#ffffff", outline="", tags=f"bg{index}")
            canvas.create_text(S(18), y0 + row_h / 2, text=value, anchor="w", fill=GREEN_TEXT if selected else INK, font=self._font, tags=f"row{index}")
            rows.append((y0, y0 + row_h, value))
        canvas.bind("<Motion>", self._on_popup_motion)
        canvas.bind("<Button-1>", self._on_popup_click)
        top.bind("<Escape>", lambda _e: self._close_popup())
        top.bind("<FocusOut>", lambda _e: self._close_popup_on_focusout())
        # 不加模态抓取：抓取在 Windows 上对真实点击的派发不可靠，一旦收起路径
        # 失效整个应用就会假死。应用内任意点击由根窗口兜底绑定收起，
        # 焦点被其他应用夺走时由 FocusOut 收起
        self._outside_bind_id = self.winfo_toplevel().bind("<Button-1>", self._on_app_click, add="+")
        self._popup = top
        self._popup_canvas = canvas
        self._rows = tuple(rows)
        self._render()
        top.lift()
        try:
            top.focus_force()
        except tk.TclError:
            pass

    def _row_at(self, y: float) -> int | None:
        for index, (y0, y1, _value) in enumerate(self._rows):
            if y0 <= y < y1:
                return index
        return None

    def _on_popup_motion(self, event) -> None:
        hover = self._row_at(event.y)
        for index, (_y0, _y1, value) in enumerate(self._rows):
            if index == hover:
                fill = "#e3f3ec"
            elif value == self.variable.get():
                fill = PILL_ACTIVE
            else:
                fill = "#ffffff"
            self._popup_canvas.itemconfig(f"bg{index}", fill=fill)

    def _on_popup_click(self, event):
        index = self._row_at(event.y)
        if index is not None:
            self._apply_selection(index)
        else:
            self._close_popup()
        # 阻断向根窗口兜底绑定的传播，避免行选择后再次触发收起逻辑
        return "break"

    def _apply_selection(self, index: int) -> None:
        self.variable.set(self._rows[index][2])
        self._close_popup()
        if self.command:
            self.command()

    def _on_app_click(self, _event) -> None:
        """收起弹层：应用内任何位置的点击都会经 bindtag 传播到这里。"""
        self._close_popup()

    def _close_popup_on_focusout(self) -> None:
        """FocusOut 收起。物理按下会先引发窗口激活迁移再派发点击：
        若此刻左键仍被按住，说明收起由本次按下引发，需抑制同一物理点击
        随后派发到下拉框的 Button-1 重新展开弹层。"""
        if self._popup and USER32.GetKeyState(VK_LBUTTON) & 0x8000:
            self._reopen_guard = True
        self._close_popup()

    def _clear_reopen_guard(self, _event=None) -> None:
        self._reopen_guard = False

    def _close_popup(self) -> None:
        if self._popup:
            self._popup.destroy()
            self._popup = None
            self._popup_canvas = None
            self._rows = ()
            if self._outside_bind_id is not None:
                self.winfo_toplevel().unbind("<Button-1>", self._outside_bind_id)
                self._outside_bind_id = None
            self._render()


class NumberField(tk.Frame):
    """圆角数字输入框，右侧带步进按钮。"""

    def __init__(self, master, variable: tk.IntVar | tk.DoubleVar, *, width, height, minimum=0,
                 maximum=999999, step=1, radius=10, compact=False):
        super().__init__(master, bg=master["bg"], width=width, height=height)
        self.pack_propagate(False)
        self.variable = variable
        self._allow_float = isinstance(variable, tk.DoubleVar)
        self._width = width
        self._height = height
        self.minimum = minimum
        self.maximum = maximum
        self.step = step
        self.radius = radius
        self.compact = compact
        self._text = tk.StringVar(value=self._format(variable.get()))
        self.canvas = tk.Canvas(self, width=width, height=height, bg=master["bg"], highlightthickness=0)
        self.canvas.pack()
        self.entry = tk.Entry(self.canvas, textvariable=self._text, bg="#ffffff", fg=INK, relief="flat", bd=0, insertbackground=GREEN, highlightthickness=0, font=M(12 if compact else 13), justify="left")
        self._entry_window = self.canvas.create_window(S(14), height / 2, window=self.entry, anchor="w")
        self.entry.configure(validate="key", validatecommand=(self.register(self._validate), "%P"))
        self.entry.bind("<FocusOut>", lambda _e: self.normalize())
        self._text.trace_add("write", self._on_text)
        self.variable.trace_add("write", self._on_variable)
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Motion>", self._on_motion)
        self._render()

    @property
    def _zone(self) -> int:
        """步进按钮区的左边界 x 坐标；紧凑模式没有步进按钮，整框都是输入区。"""
        return self._width if self.compact else self._width - S(34)

    def _render(self) -> None:
        cv = self.canvas
        cv.delete("field")
        w, h, r = self._width, self._height, self.radius
        rounded_rect(cv, 0, 0, w - 1, h - 1, r, fill=FIELD_BORDER, outline="", tags="field")
        rounded_rect(cv, 1, 1, w - 2, h - 2, max(2, r - 1), fill="#ffffff", outline="", tags="field")
        zone = self._zone
        if not self.compact:
            cv.create_line(zone, S(9), zone, h - S(9), fill="#eef4f2", tags="field")
            cx, mid = zone + S(13), h / 2
            off, span = S(10), S(6)
            cv.create_line(cx - span, mid - off + 3, cx, mid - off - 3, cx + span, mid - off + 3, fill="#7d938e", width=S(2), joinstyle="round", capstyle="round", tags="field")
            cv.create_line(cx - span, mid + off - 3, cx, mid + off + 3, cx + span, mid + off - 3, fill="#7d938e", width=S(2), joinstyle="round", capstyle="round", tags="field")
        # Entry 的文字永远贴组件顶，不能拉伸窗口高度；保持原生高度由 anchor="w" 垂直居中
        cv.itemconfigure(self._entry_window, width=zone - S(20))
        cv.tag_lower("field")

    def _validate(self, value: str) -> bool:
        if value == "":
            return True
        if not self._allow_float:
            return value.isdigit()
        return value.count(".") <= 1 and all(char.isdigit() or char == "." for char in value)

    @staticmethod
    def _format(value: float) -> str:
        text = f"{value:.6f}".rstrip("0").rstrip(".")
        return text or "0"

    def _current(self) -> float:
        try:
            return float(self.variable.get())
        except (tk.TclError, ValueError):
            return self.minimum

    def _apply(self, value: float) -> None:
        value = round(max(self.minimum, min(self.maximum, value)), 6)
        self.variable.set(int(value) if not self._allow_float else value)
        self._text.set(self._format(value))

    def normalize(self) -> None:
        self._apply(self._current())

    def _on_text(self, *_args) -> None:
        text = self._text.get().strip()
        if not text or text == ".":
            return
        if self._allow_float:
            try:
                self.variable.set(float(text))
            except ValueError:
                pass
        elif text.isdigit():
            self.variable.set(int(text))

    def _on_variable(self, *_args) -> None:
        try:
            typed = float(self._text.get())
        except ValueError:
            typed = None
        if typed != float(self.variable.get()):
            self._text.set(self._format(self.variable.get()))

    def _on_click(self, event) -> None:
        if self.compact or event.x < self._zone:
            return
        step = self.step if event.y < self._height / 2 else -self.step
        self._apply(self._current() + step)

    def _on_motion(self, event) -> None:
        self.canvas.configure(cursor="hand2" if not self.compact and event.x >= self._zone else "xterm")


class TextField(tk.Frame):
    """圆角文本输入框，可选字数计数。"""

    def __init__(self, master, textvariable: tk.StringVar, *, width, height, maxlength=None):
        super().__init__(master, bg=master["bg"], width=width, height=height)
        self.pack_propagate(False)
        self.textvariable = textvariable
        self.maxlength = maxlength
        self._width = width
        self._height = height
        self.canvas = tk.Canvas(self, width=width, height=height, bg=master["bg"], highlightthickness=0)
        self.canvas.pack()
        self.entry = tk.Entry(self.canvas, textvariable=textvariable, bg="#ffffff", fg=INK, relief="flat", bd=0, insertbackground=GREEN, highlightthickness=0, font=F(12))
        self.canvas.create_window(S(14), height / 2, window=self.entry, anchor="w", width=width - (S(80) if maxlength else S(28)))
        self.entry.bind("<FocusIn>", lambda _e: self._draw(focus=True))
        self.entry.bind("<FocusOut>", lambda _e: self._draw(focus=False))
        textvariable.trace_add("write", self._on_change)
        self._draw()

    def _on_change(self, *_args) -> None:
        value = self.textvariable.get()
        if self.maxlength and len(value) > self.maxlength:
            self.textvariable.set(value[: self.maxlength])
            return
        self._draw_counter()

    def _draw(self, focus: bool = False) -> None:
        cv, w, h = self.canvas, self._width, self._height
        cv.delete("field")
        border = GREEN if focus else FIELD_BORDER
        rounded_rect(cv, 0, 0, w - 1, h - 1, S(10), fill=border, outline="", tags="field")
        rounded_rect(cv, 1, 1, w - 2, h - 2, S(9), fill="#ffffff", outline="", tags="field")
        cv.tag_lower("field")
        self._draw_counter()

    def _draw_counter(self) -> None:
        if not self.maxlength:
            return
        self.canvas.delete("counter")
        self.canvas.create_text(self._width - S(14), self._height / 2, text=f"{len(self.textvariable.get())}/{self.maxlength}", anchor="e", fill=GRAY, font=F(11), tags="counter")


class CheckBox(tk.Frame):
    """细长圆角复选框，点击方框或文本均可切换。"""

    def __init__(self, master, text: str, variable: tk.BooleanVar):
        super().__init__(master, bg=master["bg"])
        self.variable = variable
        self.box = tk.Canvas(self, width=S(16), height=S(16), bg=master["bg"], highlightthickness=0, cursor="hand2")
        self.box.pack(side="left")
        self.label = tk.Label(self, text=text, bg=master["bg"], fg="#3f5259", font=F(12), cursor="hand2")
        self.label.pack(side="left", padx=(S(8), 0))
        for widget in (self.box, self.label):
            widget.bind("<Button-1>", lambda _e: self.toggle())
        variable.trace_add("write", lambda *_: self._draw())
        self._draw()

    def toggle(self) -> None:
        self.variable.set(not self.variable.get())

    def _draw(self) -> None:
        self.box.delete("all")
        if self.variable.get():
            rounded_rect(self.box, 1, 1, S(15), S(15), S(4), fill=GREEN, outline="")
            self.box.create_line(S(4), S(8.5), S(6.5), S(11), S(12), S(5), fill="#ffffff", width=S(2), joinstyle="round", capstyle="round")
        else:
            rounded_rect(self.box, 1, 1, S(15), S(15), S(4), fill="#ffffff", outline="#c2d6d0")


class ToolTip:
    """悬浮说明气泡：延迟出现、移开即逝，自动避开屏幕右缘并支持贴底上翻。"""

    BG = "#31434c"

    def __init__(self, widget: tk.Widget, text: str, *, delay=400):
        self.widget = widget
        self.text = text
        self.delay = delay
        self._job: str | None = None
        self._bubble: tk.Toplevel | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<Button-1>", lambda _e: self._hide(), add="+")

    def _schedule(self, _event=None) -> None:
        self._cancel()
        try:
            self._job = self.widget.after(self.delay, self._show)
        except tk.TclError:
            pass

    def _cancel(self) -> None:
        if self._job:
            try:
                self.widget.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None

    def _hide(self, _event=None) -> None:
        self._cancel()
        if self._bubble:
            self._bubble.destroy()
            self._bubble = None

    def _show(self) -> None:
        self._job = None
        if self._bubble or not self.widget.winfo_exists():
            return
        bubble = tk.Toplevel(self.widget)
        bubble.overrideredirect(True)
        bubble.attributes("-topmost", True)
        # 透明色让圆角外的画布四角不显示，仅 Windows 桌面应用使用
        cv = tk.Canvas(bubble, highlightthickness=0, bd=0, bg="#010203")
        bubble.attributes("-transparentcolor", "#010203")
        text_id = cv.create_text(S(12), S(9), text=self.text, anchor="nw", width=S(252), fill="#eef6f2", font=F(11))
        left, top, right, bottom = cv.bbox(text_id)
        width, height = right - left + S(24), bottom - top + S(18)
        cv.configure(width=width, height=height)
        rounded_rect(cv, 0, 0, width - 1, height - 1, S(10), fill=self.BG, outline="")
        cv.tag_raise(text_id)
        cv.pack()
        x = self.widget.winfo_rootx() + self.widget.winfo_width() // 2 - width // 2
        x = max(S(8), min(x, self.widget.winfo_screenwidth() - width - S(8)))
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + S(8)
        if y + height > self.widget.winfo_screenheight() - S(8):
            y = self.widget.winfo_rooty() - height - S(8)
        bubble.geometry(f"{width}x{height}+{x}+{y}")
        self._bubble = bubble


class HelpIcon(tk.Canvas):
    """圆圈问号图标：悬浮变色并显示说明气泡。"""

    def __init__(self, master, text: str, *, size=None):
        size = size or S(16)
        super().__init__(master, width=size, height=size, bg=master["bg"], highlightthickness=0)
        self._size = size
        self._hover = False
        self._draw()
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        ToolTip(self, text)

    def _set_hover(self, hover: bool) -> None:
        self._hover = hover
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        color = GREEN if self._hover else "#a3b4b0"
        middle = self._size / 2
        self.create_oval(1, 1, self._size - 1, self._size - 1, outline=color, width=S(1))
        self.create_text(middle, middle, text="?", fill=color, font=F(9, "bold"))


class RoundedCard(tk.Canvas):
    """圆角卡片容器，body 为内容框架；带向下偏移的柔和投影与 1.5px 发丝边框。"""

    def __init__(self, master, *, width=None, height=None, radius=20, bg=CARD_BG, border=BORDER, shadow=True):
        super().__init__(master, width=width or 10, height=height or 10, bg=master["bg"], highlightthickness=0, bd=0)
        self.radius = radius
        self._bg = bg
        self._border = border
        self._shadow = shadow
        self._margin = S(6) if shadow else 0
        self.body = tk.Frame(self, bg=bg)
        self._window = self.create_window(1, 1, window=self.body, anchor="nw")
        self._relayout_job = None
        self.bind("<Configure>", self._schedule_relayout)

    def _schedule_relayout(self, _event=None) -> None:
        """缩放时合并高频 <Configure>，延迟重画圆角与阴影，避免连续 delete/create 卡顿。"""
        if self._relayout_job:
            try:
                self.after_cancel(self._relayout_job)
            except tk.TclError:
                pass
        self._relayout_job = self.after(80, self._relayout)

    def _relayout(self, _event=None) -> None:
        self._relayout_job = None
        w, h = max(2, self.winfo_width()), max(2, self.winfo_height())
        self.delete("card")
        m = self._margin
        ground = self["bg"]
        if self._shadow:
            # 投影向下方偏移，逐层加深，模拟海拔；上左右只露出细微边缘
            rounded_rect(self, m - 2, m, w - m + 2, h - m + 3, self.radius + 2, fill=mix(ground, "#5d7a72", 0.10), outline="", tags="card")
            rounded_rect(self, m - 1, m + 1, w - m + 1, h - m + 5, self.radius + 1, fill=mix(ground, "#5d7a72", 0.16), outline="", tags="card")
            rounded_rect(self, m, m + 2, w - m, h - m + 7, self.radius, fill=mix(ground, "#5d7a72", 0.22), outline="", tags="card")
        ring = max(1, round(S(1.5)))
        rounded_rect(self, m, m, w - m, h - m, self.radius, fill=self._border, outline="", tags="card")
        rounded_rect(self, m + ring, m + ring, w - m - ring, h - m - ring, max(2, self.radius - ring), fill=self._bg, outline="", tags="card")
        self.tag_lower("card")
        self.coords(self._window, m + ring, m + ring)
        self.itemconfigure(self._window, width=w - 2 * m - 2 * ring, height=h - 2 * m - 2 * ring)


class DesktopClicker:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("点点")
        self.root.geometry(f"{S(1500)}x{S(920)}")
        self.root.minsize(S(1180), S(760))
        self.root.configure(bg=MAIN_BG)
        self.root.state("zoomed")
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        configured_data_dir = os.environ.get("DIANDIAN_DATA_DIR")
        self.app_data_dir = Path(configured_data_dir) if configured_data_dir else Path(os.environ.get("APPDATA", Path.home())) / "Diandian"
        self.repository = TaskRepository(self.app_data_dir)
        self.thumbs_dir = self.app_data_dir / "thumbs"
        self.thumbs_dir.mkdir(parents=True, exist_ok=True)
        self.tasks = self.repository.load_tasks()
        self.trash = self.repository.load_trash()
        self.active_index = 0
        self.steps: list[Step] = []
        self.target: Step | None = None
        self.capture_armed = False
        self.recording = False
        self.running = False
        self.paused = False
        self.stop_event = threading.Event()
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
        self._screen_thumb: Image.Image | None = None
        self._step_thumbs: dict[str, Image.Image] = {}
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
        self.closing = False
        self.hotkey_state = {
            key: bool(USER32.GetAsyncKeyState(key) & 0x8000)
            for key in (VK_F2, VK_F6, VK_F7, VK_ESCAPE)
        }

        self._build_ui()
        self.message_window_handle = self.root.winfo_id()
        self.root_window_handle = int(USER32.GetAncestor(self.message_window_handle, 2) or self.message_window_handle)
        try:
            # 实例须挂在 self 上保活：WNDPROC 回调一旦被回收，下一条窗口消息就会崩溃
            self.resize_throttle = ResizeThrottle(self.root_window_handle)
        except OSError:
            # 个别环境下无法替换窗口过程：退回逐帧重排的默认行为，仅影响缩放流畅度
            self.resize_throttle = None
        self._load_active_task()
        self.root.after(15, self._drain_ui_events)
        self.root.after(15, self._poll_hotkeys)
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
        tk.Label(brand_text, text="By Zhgui", bg=SIDEBAR_BG, fg="#a8bab4", font=F(10)).pack(anchor="w", pady=(S(2), 0))
        self.tasks_item = PillButton(sidebar, "我的任务", self._toggle_task_list, width=S(272), height=S(44), radius=S(10), bg=SIDEBAR_BG, hover_bg="#e9f4ef", fg=INK, icon="tasks", icon_color="#33454e", font=F(13), align="left", trailing="chevron-up")
        self.tasks_item.pack(padx=S(18), pady=(S(30), S(4)))
        # 底栏固定在侧边栏底部，任务再多也不会把它顶出可视区
        footer = tk.Frame(sidebar, bg=SIDEBAR_BG)
        footer.pack(side="bottom", fill="x")
        shortcuts = tk.Canvas(footer, width=S(260), height=S(152), bg=SIDEBAR_BG, highlightthickness=0)
        shortcuts.pack(side="bottom", padx=S(24), pady=(0, S(24)))
        rounded_rect(shortcuts, 0, 0, S(259), S(151), S(12), fill=PANEL_BG, outline="")
        for index, (key, action) in enumerate((("F2", "捕获位置"), ("F6", "运行 / 停止"), ("F7", "暂停 / 继续"), ("Esc", "紧急停止"))):
            y = S(18) + index * S(31)
            shortcuts.create_text(S(20), y + S(8), text=key, anchor="w", fill="#7d938e", font=M(11))
            shortcuts.create_text(S(62), y + S(8), text=action, anchor="w", fill="#4d625c", font=F(11))
        task_actions = tk.Frame(footer, bg=SIDEBAR_BG)
        task_actions.pack(side="bottom", fill="x", padx=S(24), pady=(0, S(16)))
        PillButton(task_actions, "复制", self.duplicate_task, width=S(76), height=S(30), radius=S(8), bg=CARD_BG, fg=INK_SOFT, border=FIELD_BORDER, hover_bg="#f2f9f6", font=F(12)).pack(side="left")
        PillButton(task_actions, "删除", self.delete_task, width=S(76), height=S(30), radius=S(8), bg=CARD_BG, fg="#b3423f", border=FIELD_BORDER, hover_bg="#fdeceb", font=F(12)).pack(side="left", padx=(S(6), 0))
        self.trash_button = PillButton(task_actions, "回收站", self.show_trash, width=S(96), height=S(30), radius=S(8), bg=CARD_BG, fg=INK_SOFT, border=FIELD_BORDER, hover_bg="#f2f9f6", font=F(12))
        self.trash_button.pack(side="left", padx=(S(6), 0))
        self.new_button = PillButton(footer, "新建任务", self.new_task, width=S(260), height=S(46), radius=S(10), bg=GREEN, fg="#ffffff", hover_bg=GREEN_HOVER, icon="plus", icon_color="#ffffff", font=F(14, "bold"))
        self.new_button.pack(side="bottom", padx=S(24), pady=(S(16), S(8)))
        # 中间区域只放任务列表，超出时滚动
        self.task_scroll = tk.Canvas(sidebar, bg=SIDEBAR_BG, highlightthickness=0)
        self.task_scroll.pack(fill="both", expand=True, pady=(S(4), S(8)))
        self.task_column = tk.Frame(self.task_scroll, bg=SIDEBAR_BG)
        self.task_window = self.task_scroll.create_window((0, 0), window=self.task_column, anchor="nw")
        self.task_column.bind("<Configure>", lambda _event: self.task_scroll.configure(scrollregion=self.task_scroll.bbox("all")))
        self.task_scroll.bind("<Configure>", lambda event: self.task_scroll.itemconfigure(self.task_window, width=event.width))
        self.task_scroll.bind("<MouseWheel>", self._on_task_scroll)
        self._render_task_list()

    def _build_header(self, parent: tk.Frame) -> None:
        header = tk.Frame(parent, height=S(48), bg=HEADER_BG, highlightthickness=1, highlightbackground=BORDER)
        header.pack(fill="x")
        header.pack_propagate(False)
        crumb = tk.Frame(header, bg=HEADER_BG)
        crumb.place(x=S(36), rely=0.5, anchor="w")
        tk.Label(crumb, text="我的任务", bg=HEADER_BG, fg=GRAY, font=F(12)).pack(side="left")
        tk.Label(crumb, text="/", bg=HEADER_BG, fg="#c3d2cd", font=F(12)).pack(side="left", padx=S(14))
        self.breadcrumb_task = tk.Label(crumb, text="桌面连点", bg=HEADER_BG, fg=INK, font=F(12, "bold"))
        self.breadcrumb_task.pack(side="left")

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
        self.capture_button = PillButton(bar, "捕获位置 (F2)", self.arm_capture, width=S(115), height=S(38), radius=S(7), bg="#ffffff", fg=GREEN_TEXT, border=FIELD_BORDER, hover_bg="#f1f9f6", font=F(14, "bold"))
        self.capture_button.pack(side="left", padx=(S(10), 0))
        self.clear_position_button = PillButton(bar, "清除位置", self.clear_position, width=S(84), height=S(38), radius=S(7), bg="#ffffff", fg="#8a5250", border=FIELD_BORDER, hover_bg="#fff1f0", font=F(14, "bold"))
        self.clear_position_button.pack(side="left", padx=(S(6), 0))
        self.record_button = PillButton(bar, "开始录制", self.toggle_recording, width=S(84), height=S(38), radius=S(7), bg="#ffffff", fg="#3a4c55", border=FIELD_BORDER, hover_bg="#f1f9f6", font=F(14, "bold"))
        self.record_button.pack(side="left", padx=(S(10), 0))
        self.save_button = PillButton(bar, "保存任务", self.save_task, width=S(106), height=S(35), radius=S(7), bg="#ffffff", fg=INK, border=FIELD_BORDER, hover_bg="#f1f9f6", icon="floppy", icon_color="#3f5259", font=F(12, "bold"), icon_size=S(14))
        self.save_button.pack(side="right", padx=(S(6), 0))
        self.export_button = PillButton(bar, "导出", self.export_task, width=S(72), height=S(35), radius=S(7), bg="#ffffff", fg=INK, border=FIELD_BORDER, hover_bg="#f1f9f6", font=F(12, "bold"))
        self.export_button.pack(side="right", padx=(S(6), 0))
        self.import_button = PillButton(bar, "导入", self.import_task, width=S(72), height=S(35), radius=S(7), bg="#ffffff", fg=INK, border=FIELD_BORDER, hover_bg="#f1f9f6", font=F(12, "bold"))
        self.import_button.pack(side="right", padx=(S(6), 0))
        self.run_button = PillButton(bar, "开始运行 (F6)", self.toggle_run, width=S(150), height=S(35), radius=S(7), bg=GREEN, fg="#ffffff", hover_bg=GREEN_HOVER, icon="play", icon_color="#ffffff", font=F(12, "bold"), icon_size=S(14))
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
        self.workspace_kind = tk.Label(head, text="定位工作区", bg=CARD_BG, fg="#5d8290", font=F(12, "bold"))
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
        self.workspace = tk.Canvas(body, bg="#f4faf6", highlightthickness=0, relief="flat", cursor="hand2")
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
            PillButton(self.step_toolbar, label, command, width=text_width + S(14), height=S(32), radius=S(7), bg="#f4faf7", fg="#49625b", hover_bg="#dcf0e7", font=F(11)).pack(side="left", padx=(0, S(2)))
        self.step_list = tk.Listbox(self.path_panel, height=1, selectmode=tk.EXTENDED, bg="#fbfdfc", fg="#4f5c53", selectbackground=PILL_ACTIVE, selectforeground=GREEN_TEXT, highlightthickness=1, highlightbackground="#e4eeee", relief="flat", font=M(12))
        self.step_list.pack(fill="both", expand=True, pady=(S(12), S(12)))
        self.step_list.bind("<Double-Button-1>", self.edit_step)
        self.step_list.bind("<Delete>", lambda _event: self.delete_step())
        self.step_list.bind("<ButtonPress-1>", self._step_drag_start)
        self.step_list.bind("<ButtonRelease-1>", self._step_drag_end)
        self.step_list.bind("<<ListboxSelect>>", self._update_step_readout)
        self.step_list.bind("<Button-3>", self._show_step_context_menu)
        self._build_step_context_menu()

    def _build_step_context_menu(self) -> None:
        """步骤列表右键快捷菜单。"""
        menu = tk.Menu(self.root, tearoff=0, bg="#ffffff", fg=INK, font=F(12), activebackground="#e8f5ee", activeforeground=GREEN_TEXT)
        menu.add_command(label="添加位置 (F2)", command=self.arm_capture)
        menu.add_command(label="添加等待", command=self.add_wait_step)
        menu.add_command(label="添加滚轮", command=self.add_scroll_step)
        menu.add_command(label="添加按键", command=self.add_key_step)
        menu.add_command(label="添加文字", command=self.add_text_step)
        menu.add_command(label="添加图像点击", command=self.start_image_capture)
        menu.add_command(label="添加如果图像", command=self.add_if_image_step)
        menu.add_separator()
        menu.add_command(label="编辑步骤", command=self.edit_step)
        menu.add_command(label="复制步骤 (Ctrl+D)", command=self.duplicate_step)
        menu.add_command(label="启用/停用", command=self.toggle_step_enabled)
        menu.add_separator()
        menu.add_command(label="删除选中 (Del)", command=self.delete_step)
        menu.add_command(label="清空路径", command=self.clear_path)
        self._step_context_menu = menu

    def _show_step_context_menu(self, event) -> None:
        if not self._editing_allowed():
            return
        try:
            self._step_context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._step_context_menu.grab_release()



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
        dialog.configure(bg="#2a3a35")
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

        next_btn = tk.Button(btn_row, text="", bg=GREEN, fg="#ffffff", font=F(12, "bold"), relief="flat", cursor="hand2",
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
            color = GREEN if i <= idx else "#d0ddd8"
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
        tk.Frame(parent, width=1, bg="#e8f1ed").pack(side="left", fill="y", padx=S(6))

    def _build_settings(self, parent: tk.Frame) -> None:
        card = RoundedCard(parent, width=S(450), radius=S(20))
        card.pack(side="right", fill="y")
        body = card.body
        head = tk.Frame(body, bg=CARD_BG)
        head.pack(fill="x", padx=S(26), pady=(S(24), 0))
        gear = tk.Canvas(head, width=S(20), height=S(20), bg=CARD_BG, highlightthickness=0)
        gear.pack(side="left")
        paint_icon(gear, "gear", S(10), S(10), S(17), "#3f5259", CARD_BG)
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
        self.status_label = tk.Label(bar, text="●  准备就绪", bg=MAIN_BG, fg=GREEN, font=F(12, "bold"))
        self.status_label.pack(side="left")
        self.run_info = tk.Label(bar, text="运行状态：未启动", bg=MAIN_BG, fg=GRAY, font=F(11))
        self.run_info.pack(side="right")
        status_dot = tk.Canvas(bar, width=S(14), height=S(14), bg=MAIN_BG, highlightthickness=0)
        status_dot.pack(side="right", padx=(0, S(6)))
        paint_icon(status_dot, "target", S(7), S(7), S(10), "#9db0aa", MAIN_BG)

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
        rounded_rect(cv, 0, 0, width - 1, height - 1, S(12), fill="#f4faf6", outline="")
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
        self._paint_dot_grid(cv, margin, margin, width - margin, height - margin, S(24), "#e4efe9")
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
        self._paint_dot_grid(cv, margin, margin, width - margin, height - margin, S(24), "#e4efe9")
        rounded_rect(cv, ox, oy, ox + box_w, oy + box_h, S(10), fill="#eef7f1", outline=BORDER)
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
            cv.create_text(width / 2, height / 2, text="点击捕获第一个位置  F2", fill="#ffffff", font=F(13, "bold"))
            cv.create_text(width / 2, by + btn_h + S(18), text="捕获时点点会暂时隐藏，点击目标位置即可", fill="#9db0aa", font=F(11))

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
        cv.create_text(bx + box_w / 2, by + box_h / 2, text=label, fill="#ffffff", font=font)

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
        for child in self.task_column.winfo_children():
            child.destroy()
        if not self.tasks_expanded:
            return
        for index, task in enumerate(self.tasks):
            active = index == self.active_index
            PillButton(
                self.task_column, task.name,
                lambda picked=index: self.select_task(picked),
                width=S(252), height=S(38), radius=S(9),
                bg=PILL_ACTIVE if active else SIDEBAR_BG, hover_bg="#e6f3ee",
                fg="#157a5e" if active else "#5c6f6a",
                icon="bolt" if active else "dot",
                icon_color=GREEN if active else "#9db0aa",
                font=F(12, "bold") if active else F(12),
                align="left", padx=S(14),
            ).pack(padx=S(28), pady=1)
        for child in self.task_column.winfo_children():
            child.bind("<MouseWheel>", self._on_task_scroll)
        self.task_scroll.yview_moveto(0)

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
        for index, step in enumerate(self.steps, 1):
            state = "✓" if step.enabled else "○"
            self.step_list.insert(tk.END, f"{state} {index:02d}【{step.action_name}】 {step.target_summary} · 等 {step.wait_ms}ms")
        if self.steps:
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
                    self._step_thumbs[step.id] = loaded
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
        body = tk.Frame(top, bg=MAIN_BG, padx=S(20), pady=S(16))
        body.pack(fill="both", expand=True)
        tk.Label(body, text="选择按键（双击直接确认）：", bg=MAIN_BG, fg=INK, font=F(13)).pack(anchor="w")
        list_frame = tk.Frame(body, bg="#ffffff", highlightthickness=1, highlightbackground=FIELD_BORDER)
        list_frame.pack(fill="both", expand=True, pady=(S(8), S(12)))
        key_list = tk.Listbox(list_frame, height=10, font=F(12), bg="#ffffff", fg=INK,
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
                   bg="#ffffff", fg=INK, border=FIELD_BORDER, hover_bg="#f1f9f6", font=F(12)).pack(side="left")
        PillButton(btn_row, "确定", confirm, width=S(80), height=S(34), radius=S(8),
                   bg=GREEN, fg="#ffffff", hover_bg=GREEN_HOVER, font=F(12, "bold")).pack(side="right")
        PillButton(btn_row, "取消", cancel, width=S(80), height=S(34), radius=S(8),
                   bg="#ffffff", fg=INK, border=FIELD_BORDER, hover_bg="#f1f9f6", font=F(12)).pack(side="right", padx=(0, S(8)))
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
        body = tk.Frame(top, bg=MAIN_BG, padx=S(24), pady=S(20))
        body.pack(fill="both", expand=True)
        tk.Label(body, text=label, bg=MAIN_BG, fg=INK, font=F(13)).pack(anchor="w")
        entry = tk.Entry(body, width=36, font=F(13), bg="#ffffff", fg=INK, relief="flat", highlightthickness=1, highlightbackground=FIELD_BORDER, highlightcolor=GREEN)
        entry.pack(fill="x", pady=(S(10), S(6)), ipady=S(8))
        entry.insert(0, str(initial))
        entry.select_range(0, tk.END)
        entry.focus_set()
        error_label = tk.Label(body, text="", bg=MAIN_BG, fg="#c0392b", font=F(11))
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
        PillButton(btn_row, "确定", confirm, width=S(88), height=S(36), radius=S(8), bg=GREEN, fg="#ffffff", hover_bg=GREEN_HOVER, font=F(13, "bold")).pack(side="right")
        PillButton(btn_row, "取消", cancel, width=S(88), height=S(36), radius=S(8), bg="#ffffff", fg=INK, border=FIELD_BORDER, hover_bg="#f1f9f6", font=F(13)).pack(side="right", padx=(0, S(10)))
        top.bind("<Return>", confirm)
        top.bind("<Escape>", cancel)
        self._center_dialog(top)
        self.root.wait_window(top)
        return result["value"]

    def _ask_text_dialog(self, title: str, label: str, initial: str = "") -> str | None:
        return self._ask_input_dialog(title, label, initial, input_type="str")

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
        if not messagebox.askyesno("清空路径", f"确定清空当前路径吗？\n将移除全部 {len(self.steps)} 个步骤，任务设置会保留。", parent=self.root):
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
        top.configure(bg="#1a1a1a")
        top.attributes("-topmost", True)
        sw, sh = top.winfo_screenwidth(), top.winfo_screenheight()
        max_w, max_h = int(sw * 0.85), int(sh * 0.85)
        img = self._screen_thumb
        scale = min(max_w / img.width, max_h / img.height, 1.0)
        disp_w, disp_h = max(1, round(img.width * scale)), max(1, round(img.height * scale))
        resized = img.resize((disp_w, disp_h), Image.Resampling.BILINEAR) if scale < 1.0 else img
        photo = ImageTk.PhotoImage(resized)
        canvas = tk.Canvas(top, width=disp_w, height=disp_h, bg="#1a1a1a", highlightthickness=0, cursor="hand2")
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
        self.capture_button.configure(text="移动鼠标后按 F2", bg="#e2f0e7")
        self._set_status("等待捕获位置")
        self._show_capture_overlay()

    def _show_capture_overlay(self) -> None:
        self._hide_capture_overlay(restore_main=False)
        overlay = tk.Toplevel(self.root)
        overlay.overrideredirect(True)
        overlay.configure(bg="#18201b")
        overlay.attributes("-topmost", True)
        overlay.geometry(self._overlay_geometry(S(560), S(66)))
        overlay_frame = tk.Frame(overlay, bg="#18201b", padx=S(16), pady=S(10))
        overlay_frame.pack(fill="both", expand=True)
        tk.Label(overlay_frame, text="⌖", bg="#18201b", fg="#79c99f", font=("Segoe UI Symbol", -S(24), "bold")).pack(side="left", padx=(0, S(11)))
        copy = tk.Frame(overlay_frame, bg="#18201b")
        copy.pack(side="left", fill="y")
        tk.Label(copy, text="定位模式", bg="#18201b", fg="#ffffff", font=F(13, "bold")).pack(anchor="w")
        tk.Label(copy, text="把鼠标移到目标位置，按 F2 确认；Esc 取消", bg="#18201b", fg="#b8c3bc", font=F(12)).pack(anchor="w", pady=(S(3), 0))
        cancel = tk.Button(overlay_frame, text="取消  Esc", command=self.cancel_capture, bg="#303a33", fg="#ffffff", activebackground="#414d45", activeforeground="#ffffff", relief="flat", bd=0, padx=S(13), pady=S(7), font=F(12, "bold"))
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
        self.capture_button.configure(text="捕获位置 (F2)", bg="#ffffff")
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
            self.capture_button.configure(text="捕获位置 (F2)", bg="#ffffff")
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
                    self._step_thumbs[self.target.id] = captured_thumb
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
        canvas.create_rectangle(S(10), S(10), S(390), S(50), fill="#18201b", outline="")
        canvas.create_text(S(20), S(18), text="拖动框选要识别的图像区域 · Esc 取消", anchor="nw", fill="#ffffff", font=F(14, "bold"))
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
        PillButton(btn_row, "确定", confirm, width=S(80), height=S(34), radius=S(8), bg=GREEN, fg="#ffffff", hover_bg=GREEN_HOVER, font=F(12, "bold")).pack(side="right")
        PillButton(btn_row, "取消", lambda: (setattr(self, "_if_image_dialog", None), dlg.destroy()), width=S(80), height=S(34), radius=S(8), bg="#ffffff", fg=INK, border=FIELD_BORDER, hover_bg="#f1f9f6", font=F(12)).pack(side="right", padx=(0, S(8)))
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
            if self.steps and not messagebox.askyesno("重新录制", f"开始录制会替换当前 {len(self.steps)} 个步骤。继续吗？\n录制后仍可按 Ctrl+Z 恢复。", parent=self.root):
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
            self.record_button.configure(text="停止录制", bg="#fdeceb", fg="#b3423f")
            self._show_record_overlay()
            self._set_status("正在录制鼠标、键盘和滚轮输入")

    def _stop_recording(self) -> None:
        self.recording = False
        self.input_recorder.stop()
        self._hide_record_overlay()
        self.record_button.configure(text="开始录制", bg="#ffffff", fg="#3a4c55")
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
                self._step_thumbs[step.id] = shot
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
            messagebox.showinfo("还没有位置", "请先按 F2 捕获鼠标当前位置。")
            return
        if self.mode_var.get() != "单点连点" and not self.steps:
            messagebox.showinfo("还没有动作", "请先捕获位置或录制至少一个动作。")
            return
        self.save_task(silent=True)
        self.stop_event.clear()
        self.paused = False
        if self.mode_var.get() == "单点连点":
            single = copy.deepcopy(self.target)
            single.type = "click"
            single.wait_ms = self._interval_ms()
            single.button = self._selected_button()
            self.run_actions = (single,)
        else:
            self.run_actions = tuple(copy.deepcopy(step) for step in self.steps)
        self.run_repeats = max(0, self.repeat_var.get())
        self.run_random_interval = self.random_var.get()
        self.run_random_percent = self.random_percent_var.get()
        self.run_position_mode = self.position_var.get()
        self.running = True
        self.run_button.configure(text="停止运行 (F6)", bg="#a84e48")
        self._show_run_overlay()
        self._run_breath_phase = 0
        self._run_breath_tick()
        self._set_status("正在运行", "running")
        self.run_info.configure(fg=GREEN, font=F(12, "bold"))
        if self.countdown_var.get() and self.countdown_seconds_var.get() > 0:
            self.root.after(100, self._start_countdown, self.countdown_seconds_var.get())
        else:
            self._start_worker()

    def _start_countdown(self, seconds: float) -> None:
        if not self.running:
            return
        self._countdown_tick(time.monotonic() + max(0.0, seconds))

    def _countdown_tick(self, deadline: float) -> None:
        if not self.running:
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            self._start_worker()
            return
        text = f"{remaining:.1f}".rstrip("0").rstrip(".")
        self._set_status(f"{text} 秒后开始运行")
        self._update_run_overlay(f"准备中 · {text} 秒后开始")
        self.root.after(100, self._countdown_tick, deadline)

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
        count = 0
        last_progress = 0.0
        try:
            while self.running and (self.run_repeats == 0 or count < self.run_repeats):
                total = len(self.run_actions)
                skip_remaining = 0
                for index, step in enumerate(self.run_actions):
                    if not step.enabled:
                        continue
                    if skip_remaining > 0:
                        skip_remaining -= 1
                        continue
                    if step.type == "if_image":
                        self.ui_events.put((self._update_run_info, (f"判断图像 · 第 {count + 1} 次",)))
                        matched = self._check_image_exists(self.repository.template_dir / step.template_file, step.match_threshold)
                        condition_met = (matched and step.condition == "存在") or (not matched and step.condition == "不存在")
                        if not condition_met:
                            skip_remaining = step.skip_count
                        continue
                    self.ui_events.put((self._update_run_info, (f"第 {index + 1}/{total} 步 · 第 {count + 1} 次",)))
                    wait_ms = step.wait_ms if step.type == "wait" or count or index else 0
                    if self.run_random_interval and wait_ms:
                        spread = self.run_random_percent / 100
                        wait_ms = round(wait_ms * random.uniform(1 - spread, 1 + spread))
                    if not self._wait_or_stop(wait_ms):
                        return
                    self._execute_step(step)
                count += 1
                # 进度事件按 100ms 节流：持续运行且步骤等待极短时，避免事件队列无界增长拖垮主线程
                now = time.monotonic()
                if now - last_progress >= .1:
                    last_progress = now
                    self.ui_events.put((self._update_run_info, (f"运行中 · 第 {count} 次",)))
            self.ui_events.put((self.stop_run, ("任务已完成",)))
        except Exception as error:
            # 兜底未预期异常：线程静默死亡会让界面永远停在“运行中”假死状态，且按键无法松开
            self.ui_events.put((self.stop_run, (f"运行失败：{error}",)))

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

    def _wait_or_stop(self, milliseconds: int) -> bool:
        end = time.monotonic() + max(0, milliseconds) / 1000
        while time.monotonic() < end:
            if self.stop_event.is_set():
                return False
            while self.paused and not self.stop_event.is_set():
                time.sleep(.05)
            time.sleep(.01)
        return not self.stop_event.is_set()

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
        self.stop_event.set()
        for key_code in tuple(self.run_keys_down):
            try:
                send_key(key_code, key_up=True)
            except OSError:
                pass
        self.run_keys_down.clear()
        self.running = False
        self.paused = False
        self.run_button.configure(text="开始运行 (F6)", bg=GREEN, fg="#ffffff")
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
            messagebox.showerror("导出失败", str(error), parent=self.root)
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
            messagebox.showerror("导入失败", f"文件无法解析：{error}", parent=self.root)
            return
        if not imported:
            messagebox.showinfo("导入", "文件中没有可导入的任务", parent=self.root)
            return
        self.tasks.extend(imported)
        self._persist_tasks()
        self.active_index = len(self.tasks) - len(imported)
        self._render_task_list()
        self._load_active_task()
        self.show_toast(f"已导入 {len(imported)} 个任务")

    def show_toast(self, text: str) -> None:
        """在窗口中上部短暂浮出一条轻提示，自动消失。"""
        if hasattr(self, "_toast") and self._toast is not None:
            try:
                self._toast.destroy()
            except tk.TclError:
                pass
        toast = tk.Toplevel(self.root)
        toast.overrideredirect(True)
        label = tk.Label(toast, text=text, bg="#31434c", fg="#eef6f2",
                         font=F(12), padx=S(18), pady=S(10))
        label.pack()
        toast.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() - toast.winfo_width()) // 2
        y = self.root.winfo_y() + S(80)
        toast.geometry(f"+{x}+{y}")
        self._toast = toast
        self.root.after(1800, lambda: (toast.destroy(), setattr(self, "_toast", None)))

    def _interval_ms(self) -> int:
        """点击间隔（秒）换算为内部毫秒，下限 10ms。"""
        return max(10, round(self.interval_var.get() * 1000))

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
        if not self._editing_allowed() or not self.tasks:
            return
        self.save_task(silent=True)
        task = self.tasks[self.active_index]
        if not messagebox.askyesno("删除任务", f"将“{task.name}”移入回收站？\n可以稍后恢复。", parent=self.root):
            return
        task.deleted_at = time.time()
        self.trash.append(task)
        self.tasks.pop(self.active_index)
        if not self.tasks:
            self.tasks.append(Task())
        self.active_index = min(self.active_index, len(self.tasks) - 1)
        self._persist_tasks()
        self._persist_trash()
        self._render_task_list()
        self._load_active_task()
        self._set_status("任务已移入回收站")

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
        top.configure(bg=CARD_BG)
        tk.Label(top, text="任务回收站", bg=CARD_BG, fg=INK, font=F(18, "bold")).pack(anchor="w", padx=S(24), pady=(S(22), S(12)))
        listing = tk.Listbox(top, bg="#fbfdfc", fg=INK, selectbackground=PILL_ACTIVE, selectforeground=GREEN_TEXT, relief="flat", highlightthickness=1, highlightbackground=BORDER, font=F(12))
        listing.pack(fill="both", expand=True, padx=S(24), pady=(0, S(14)))

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
            if not messagebox.askyesno("永久删除", f"永久删除“{task.name}”？此操作无法撤销。", parent=top):
                return
            removed = self.trash.pop(index)
            self._persist_trash()
            candidates = list(removed.steps)
            if removed.target:
                candidates.append(removed.target)
            for step in candidates:
                self.repository.delete_template_if_unused(step.template_file, self.tasks, self.trash)
            refresh()
            self._render_task_list()

        actions = tk.Frame(top, bg=CARD_BG)
        actions.pack(fill="x", padx=S(24), pady=(0, S(20)))
        tk.Button(actions, text="恢复", command=restore, bg=GREEN, fg="#ffffff", activebackground=GREEN_HOVER, relief="flat", padx=S(20), pady=S(8), font=F(11, "bold")).pack(side="left")
        tk.Button(actions, text="永久删除", command=purge, bg="#fdeceb", fg="#b3423f", activebackground="#f8d8d5", relief="flat", padx=S(16), pady=S(8), font=F(11)).pack(side="left", padx=S(8))
        def close_trash() -> None:
            self._trash_dialog = None
            top.destroy()
        tk.Button(actions, text="关闭", command=close_trash, bg="#f1f5f3", fg=INK_SOFT, activebackground="#e7efeb", relief="flat", padx=S(16), pady=S(8), font=F(11)).pack(side="right")
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
        color = "#a84e48" if kind == "running" else GREEN
        self.status_label.configure(text=f"●  {text}", fg=color)

    def _overlay_geometry(self, width: int, height: int) -> str:
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        return f"{width}x{height}+{max(S(18), (screen_width - width) // 2)}+{max(S(18), screen_height - height - S(48))}"

    def _show_run_overlay(self) -> None:
        self._hide_run_overlay()
        w, h = S(540), S(72)
        overlay = tk.Toplevel(self.root)
        overlay.overrideredirect(True)
        overlay.configure(bg="#181d1a")
        overlay.attributes("-topmost", True)
        overlay.geometry(self._overlay_geometry(w, h))
        overlay.minsize(w, h)
        overlay.maxsize(w, h)
        frame = tk.Frame(overlay, bg="#181d1a", padx=S(18), pady=S(12))
        frame.pack(fill="both", expand=True)
        tk.Label(frame, text="●", bg="#181d1a", fg="#ef8c82", font=F(20, "bold")).pack(side="left", padx=(0, S(12)))
        copy = tk.Frame(frame, bg="#181d1a")
        copy.pack(side="left", fill="y", expand=True)
        tk.Label(copy, text="点点正在执行", bg="#181d1a", fg="#ffffff", font=F(13, "bold")).pack(anchor="w")
        self.run_overlay_status = tk.Label(copy, text="准备中…", bg="#181d1a", fg="#b8c3bc", font=F(12))
        self.run_overlay_status.pack(anchor="w", pady=(S(3), 0))
        self.run_overlay_pause = tk.Button(frame, text="暂停  F7", command=self.toggle_pause, bg="#303a33", fg="#ffffff", activebackground="#414d45", activeforeground="#ffffff", relief="flat", bd=0, padx=S(14), pady=S(7), font=F(12, "bold"))
        self.run_overlay_pause.pack(side="left", padx=(S(12), S(8)))
        tk.Button(frame, text="停止  Esc", command=lambda: self.stop_run("已停止运行"), bg="#a84e48", fg="#ffffff", activebackground="#8d3f3a", activeforeground="#ffffff", relief="flat", bd=0, padx=S(14), pady=S(7), font=F(12, "bold")).pack(side="left")
        overlay.update_idletasks()
        self.run_overlay = overlay
        self.run_overlay_handle = int(USER32.GetAncestor(overlay.winfo_id(), 2) or overlay.winfo_id())
        self.root.withdraw()
        overlay.lift()

    def _show_record_overlay(self) -> None:
        self._hide_record_overlay(restore_main=False)
        overlay = tk.Toplevel(self.root)
        overlay.overrideredirect(True)
        overlay.configure(bg="#181d1a")
        overlay.attributes("-topmost", True)
        overlay.geometry(self._overlay_geometry(S(510), S(70)))
        overlay.pack_propagate(False)
        frame = tk.Frame(overlay, bg="#181d1a", padx=S(15), pady=S(10))
        frame.pack(fill="both", expand=True)
        tk.Label(frame, text="●", bg="#181d1a", fg="#ef8c82", font=F(20, "bold")).pack(side="left", padx=(0, S(10)))
        copy_frame = tk.Frame(frame, bg="#181d1a")
        copy_frame.pack(side="left", fill="y", expand=True)
        tk.Label(copy_frame, text="正在录制桌面输入", bg="#181d1a", fg="#ffffff", font=F(13, "bold")).pack(anchor="w")
        tk.Label(copy_frame, text="记录点击、滚轮和键盘 · Esc 停止", bg="#181d1a", fg="#b8c3bc", font=F(12)).pack(anchor="w", pady=(S(3), 0))
        tk.Button(frame, text="停止录制  Esc", command=self._stop_recording, bg="#a84e48", fg="#ffffff", activebackground="#8d3f3a", activeforeground="#ffffff", relief="flat", bd=0, padx=S(13), pady=S(7), font=F(12, "bold")).pack(side="right")
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

    _RUN_BREATH_BASE = "#a84e48"
    _RUN_BREATH_PEAK = "#c85a50"

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
        self.root.deiconify()
        self.root.state("zoomed")
        self.root.lift()
        self.root.focus_force()

    def _is_own_window(self, hwnd: int) -> bool:
        return int(hwnd) in {self.root_window_handle, self.capture_overlay_handle, self.run_overlay_handle, self.record_overlay_handle}

    def close(self) -> None:
        self.closing = True
        self.capture_armed = False
        self._hide_capture_overlay(restore_main=False)
        if self.running:
            self.stop_run("已停止运行")
        if self.recording:
            self._stop_recording()
        self._hide_record_overlay(restore_main=False)
        self._hide_run_overlay()
        self.stop_event.set()
        if self.worker and self.worker.is_alive():
            # 等待正在执行的步骤（如整屏图像匹配）收尾，避免销毁窗口后仍注入输入
            self.worker.join(timeout=1.5)
        try:
            self.save_task(silent=True)
            self.repository.cleanup_orphan_templates(self.tasks, self.trash)
        except OSError:
            # 磁盘异常时也要保证窗口能正常退出
            pass
        self.root.destroy()


def main() -> None:
    enable_per_monitor_dpi_awareness()
    root = tk.Tk()
    root.iconphoto(True, ImageTk.PhotoImage(brand_icon_image(256)))
    DesktopClicker(root)
    root.mainloop()


if __name__ == "__main__":
    main()
