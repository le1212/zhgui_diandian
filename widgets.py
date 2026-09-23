"""界面控件层：配色、DPI 尺寸工具与自研 Tk 控件（圆角按钮/下拉/输入框等）。

控件刻意用 Frame/Label/Canvas 组合而非原生 ttk，以获得统一的圆角视觉；
图标用 PIL 3x 超采样预渲染并缓存，避免 Canvas 频繁重绘闪烁。
"""

from __future__ import annotations

import math
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path

from PIL import Image, ImageDraw, ImageTk

from theme import color as theme_color
from winapi import SCALE, USER32, VK_LBUTTON

FONT = "Microsoft YaHei UI"
MONO = "Consolas"

# 调色板统一收敛到 theme.py（浅/深双主题，启动时读定，切换后重启生效）。
# 这里只做“主题令牌 → 模块常量”的转出，控件与业务层一律用常量名取色。
INK = theme_color("ink")
INK_STRONG = theme_color("ink_strong")
INK_SOFT = theme_color("ink_soft")
GRAY = theme_color("gray")
ON_COLOR_FG = theme_color("on_color_fg")
GREEN = theme_color("green")
GREEN_HOVER = theme_color("green_hover")
GREEN_TEXT = theme_color("green_text")
GREEN_DEEP = theme_color("green_deep")
GREEN_BRIGHT = theme_color("green_bright")
SIDEBAR_BG = theme_color("sidebar_bg")
MAIN_BG = theme_color("main_bg")
HEADER_BG = theme_color("header_bg")
CARD_BG = theme_color("card_bg")
PANEL_BG = theme_color("panel_bg")
LIST_BG = theme_color("list_bg")
LIST_BORDER = theme_color("list_border")
BORDER = theme_color("border")
FIELD_BORDER = theme_color("field_border")
PILL_ACTIVE = theme_color("pill_active")
BORDER_SOFT = theme_color("border_soft")
NEUTRAL_HOVER = theme_color("neutral_hover")
SUBTLE_BG = theme_color("subtle_bg")
SUBTLE_HOVER = theme_color("subtle_hover")
TOOLBAR_BG = theme_color("toolbar_bg")
TOOLBAR_FG = theme_color("toolbar_fg")
TOOLBAR_HOVER = theme_color("toolbar_hover")
ROW_HOVER = theme_color("row_hover")
POPUP_HOVER = theme_color("popup_hover")
TASK_ACTIVE_FG = theme_color("task_active_fg")
TASK_FG = theme_color("task_fg")
ICON_MUTED = theme_color("icon_muted")
RED = theme_color("red")
DANGER_FG = theme_color("danger_fg")
DANGER_BG = theme_color("danger_bg")
DANGER_HOVER = theme_color("danger_hover")
DANGER_SOFT_BG = theme_color("danger_soft_bg")
DANGER_DEEP = theme_color("danger_deep")
RUNNING_BG = theme_color("running_bg")
RUNNING_PEAK = theme_color("running_peak")
TOAST_BG = theme_color("toast_bg")
TOAST_FG = theme_color("toast_fg")
OVERLAY_BG = theme_color("overlay_bg")
OVERLAY_INK = theme_color("overlay_ink")
OVERLAY_INK_SOFT = theme_color("overlay_ink_soft")
OVERLAY_GREEN = theme_color("overlay_green")
OVERLAY_ACCENT = theme_color("overlay_accent")
OVERLAY_BUTTON = theme_color("overlay_button")
OVERLAY_BUTTON_HOVER = theme_color("overlay_button_hover")
INFO_BLUE = theme_color("info_blue")
SHADOW_1 = theme_color("shadow_1")
SHADOW_2 = theme_color("shadow_2")


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


def paint_icon(canvas: tk.Canvas, name: str, cx: float, cy: float, size: float, color: str, bg: str = CARD_BG) -> None:
    """在画布上绘制矢量小图标，(cx, cy) 为图标中心，size 为已缩放的边长。PIL 3x 超采样抗锯齿。"""
    key = (name, color, int(size), bg)
    if key not in _ICON_PHOTO_CACHE:
        _ICON_PHOTO_CACHE[key] = ImageTk.PhotoImage(_render_icon_image(name, color, int(size), bg))
    canvas.create_image(cx, cy, image=_ICON_PHOTO_CACHE[key])


_ICON_PHOTO_CACHE: dict[tuple, ImageTk.PhotoImage] = {}


def _render_icon_image(name: str, color: str, size: int, bg: str = CARD_BG) -> Image.Image:
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
    elif name == "clock":
        draw.ellipse(R(0.18, 0.18, 0.82, 0.82), outline=color, width=sw)
        draw.line(P([(0.5, 0.5), (0.5, 0.28)]), fill=color, width=sw)
        draw.line(P([(0.5, 0.5), (0.66, 0.58)]), fill=color, width=sw)
    elif name == "scroll":
        draw.rounded_rectangle(R(0.3, 0.1, 0.7, 0.9), radius=0.2 * big, outline=color, width=sw)
        draw.line(P([(0.5, 0.2), (0.5, 0.42)]), fill=color, width=sw)
    elif name == "keyboard":
        draw.rounded_rectangle(R(0.08, 0.28, 0.92, 0.72), radius=0.1 * big, outline=color, width=sw)
        for fx in (0.2, 0.36, 0.52, 0.68):
            draw.line(P([(fx, 0.46), (fx + 0.07, 0.46)]), fill=color, width=sw)
        draw.line(P([(0.3, 0.62), (0.7, 0.62)]), fill=color, width=sw)
    elif name == "type":
        draw.line(P([(0.5, 0.18), (0.28, 0.82)]), fill=color, width=sw)
        draw.line(P([(0.5, 0.18), (0.72, 0.82)]), fill=color, width=sw)
        draw.line(P([(0.36, 0.58), (0.64, 0.58)]), fill=color, width=sw)
    elif name == "image":
        draw.rounded_rectangle(R(0.12, 0.2, 0.88, 0.8), radius=0.08 * big, outline=color, width=sw)
        draw.ellipse(R(0.6, 0.3, 0.72, 0.42), fill=color)
        draw.polygon(P([(0.2, 0.72), (0.4, 0.48), (0.56, 0.64), (0.7, 0.52), (0.82, 0.72)]), fill=color)
    elif name == "branch":
        draw.line(P([(0.3, 0.2), (0.3, 0.8)]), fill=color, width=sw)
        draw.line(P([(0.3, 0.35), (0.68, 0.2)]), fill=color, width=sw)
        draw.line(P([(0.3, 0.65), (0.68, 0.8)]), fill=color, width=sw)
        draw.ellipse(R(0.62, 0.14, 0.76, 0.28), fill=color)
        draw.ellipse(R(0.62, 0.72, 0.76, 0.86), fill=color)
    elif name == "edit":
        draw.line(P([(0.24, 0.76), (0.62, 0.38)]), fill=color, width=sw)
        draw.line(P([(0.62, 0.38), (0.74, 0.5)]), fill=color, width=sw)
        draw.line(P([(0.36, 0.88), (0.24, 0.76)]), fill=color, width=sw)
    elif name == "copy":
        draw.rounded_rectangle(R(0.32, 0.22, 0.82, 0.72), radius=0.06 * big, outline=color, width=sw)
        draw.rounded_rectangle(R(0.18, 0.34, 0.68, 0.84), radius=0.06 * big, outline=color, width=sw, fill=bg)
    elif name == "toggle":
        draw.rounded_rectangle(R(0.14, 0.32, 0.86, 0.68), radius=0.18 * big, outline=color, width=sw)
        draw.ellipse(R(0.56, 0.38, 0.8, 0.62), fill=color)
    elif name == "trash":
        draw.line(P([(0.38, 0.18), (0.62, 0.18)]), fill=color, width=sw)
        draw.line(P([(0.22, 0.28), (0.78, 0.28)]), fill=color, width=sw)
        draw.rounded_rectangle(R(0.28, 0.28, 0.72, 0.84), radius=0.06 * big, outline=color, width=sw)
        draw.line(P([(0.42, 0.4), (0.42, 0.72)]), fill=color, width=sw)
        draw.line(P([(0.58, 0.4), (0.58, 0.72)]), fill=color, width=sw)
    elif name == "broom":
        draw.line(P([(0.62, 0.18), (0.38, 0.56)]), fill=color, width=sw)
        draw.polygon(P([(0.3, 0.5), (0.52, 0.68), (0.42, 0.86), (0.16, 0.7)]), outline=color, width=sw)
    elif name == "download":
        draw.line(P([(0.5, 0.08), (0.5, 0.6)]), fill=color, width=sw, joint="curve")
        draw.line(P([(0.26, 0.4), (0.5, 0.64), (0.74, 0.4)]), fill=color, width=sw, joint="curve")
        draw.line(P([(0.16, 0.86), (0.84, 0.86)]), fill=color, width=sw)

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
                 bg=CARD_BG, fg=INK, border=None, hover_bg=None, icon=None,
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
        self._focused = False
        super().__init__(master, width=width, height=height, bg=master["bg"], highlightthickness=0, bd=0, cursor="hand2", takefocus=True)
        self._render()
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)
        self.bind("<FocusIn>", lambda _e: self._set_focused(True))
        self.bind("<FocusOut>", lambda _e: self._set_focused(False))
        for keys in ("<Return>", "<KP_Enter>", "<space>"):
            self.bind(keys, self._activate)
        self.bind("<Expose>", lambda e: self._render())

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
            paint_icon(self, self._trailing, w - S(20), h / 2, S(14), ICON_MUTED, bg)
        if self._focused:
            rounded_rect(self, 1, 1, w - 2, h - 2, self.radius, outline=GREEN_DEEP, width=S(2))

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

    def _set_focused(self, focused: bool) -> None:
        self._focused = focused
        self._render()

    def _activate(self, _event=None) -> str:
        """键盘（回车/空格）触发，与鼠标点击同语义。"""
        if self._state == "normal" and self.command:
            self.command()
        return "break"

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
            kw["takefocus"] = 1 if self._state == "normal" else 0
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
        self._focused = False
        self._kb_index: int | None = None
        super().__init__(master, width=width, height=height, bg=master["bg"], highlightthickness=0, bd=0, cursor="hand2", takefocus=True)
        self._render()
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._toggle)
        self.bind("<FocusIn>", lambda _e: self._set_focused(True))
        self.bind("<FocusOut>", lambda _e: self._set_focused(False))
        for keys in ("<Return>", "<KP_Enter>", "<space>", "<Down>"):
            self.bind(keys, self._toggle)
        # 常驻监听：任何按钮释放都解除"抑制重新展开"标记，见 _close_popup_on_focusout
        self.bind_all("<ButtonRelease-1>", self._clear_reopen_guard, add="+")
        variable.trace_add("write", lambda *_: self._render())

    def _render(self) -> None:
        self.delete("all")
        w, h = self._width, self._height
        border = shade(self._border, 0.86) if (self._hover or self._popup) else self._border
        rounded_rect(self, 0, 0, w - 1, h - 1, self.radius, fill=border, outline="")
        rounded_rect(self, 1, 1, w - 2, h - 2, max(2, self.radius - 1), fill=CARD_BG, outline="")
        x = S(16)
        if self._icon:
            paint_icon(self, self._icon, x + S(8), h / 2, S(16), self._icon_color)
            x += S(26)
        self.create_text(x, h / 2, text=self.variable.get(), anchor="w", fill=INK, font=self._font)
        paint_icon(self, "chevron-up" if self._popup else "chevron-down", w - S(24), h / 2, S(14), ICON_MUTED)
        if self._focused:
            rounded_rect(self, 1, 1, w - 2, h - 2, self.radius, outline=GREEN_DEEP, width=S(2))

    def _set_focused(self, focused: bool) -> None:
        self._focused = focused
        self._render()

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
        canvas = tk.Canvas(top, width=width, height=height, bg=CARD_BG, highlightthickness=0)
        canvas.pack()
        rounded_rect(canvas, 0, 0, width - 1, height - 1, self.radius, fill=FIELD_BORDER, outline="")
        rounded_rect(canvas, 1, 1, width - 2, height - 2, max(2, self.radius - 1), fill=CARD_BG, outline="")
        rows = []
        for index, value in enumerate(self.values):
            y0 = pad + index * row_h
            selected = value == self.variable.get()
            rounded_rect(canvas, S(6), y0 + 2, width - S(6), y0 + row_h - 2, S(8), fill=PILL_ACTIVE if selected else CARD_BG, outline="", tags=f"bg{index}")
            canvas.create_text(S(18), y0 + row_h / 2, text=value, anchor="w", fill=GREEN_TEXT if selected else INK, font=self._font, tags=f"row{index}")
            rows.append((y0, y0 + row_h, value))
        canvas.bind("<Motion>", self._on_popup_motion)
        canvas.bind("<Button-1>", self._on_popup_click)
        self._kb_index = self._selected_index()
        for keys, handler in (
            ("<Up>", lambda _e: self._move_kb(-1)),
            ("<Down>", lambda _e: self._move_kb(1)),
            ("<Return>", self._apply_kb),
            ("<KP_Enter>", self._apply_kb),
        ):
            top.bind(keys, handler)
        self._paint_kb()
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

    def _selected_index(self) -> int:
        value = self.variable.get()
        return self.values.index(value) if value in self.values else 0

    def _paint_kb(self) -> None:
        """按当前键盘高亮重绘弹层各行；鼠标悬停覆盖键盘高亮。"""
        if not self._popup_canvas:
            return
        for index, (_y0, _y1, value) in enumerate(self._rows):
            if index == self._kb_index:
                fill = POPUP_HOVER
            elif value == self.variable.get():
                fill = PILL_ACTIVE
            else:
                fill = CARD_BG
            self._popup_canvas.itemconfig(f"bg{index}", fill=fill)

    def _move_kb(self, delta: int) -> str:
        if not self._rows:
            return "break"
        self._kb_index = ((self._kb_index if self._kb_index is not None else 0) + delta) % len(self._rows)
        self._paint_kb()
        return "break"

    def _apply_kb(self, _event=None) -> str:
        if self._kb_index is not None:
            self._apply_selection(self._kb_index)
        return "break"

    def _on_popup_motion(self, event) -> None:
        self._kb_index = None
        hover = self._row_at(event.y)
        for index, (_y0, _y1, value) in enumerate(self._rows):
            if index == hover:
                fill = POPUP_HOVER
            elif value == self.variable.get():
                fill = PILL_ACTIVE
            else:
                fill = CARD_BG
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
        try:
            self.focus_set()  # 焦点回到选择框本身，键盘 Tab 链不中断
        except tk.TclError:
            pass
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


class ContextMenu:
    """自定义圆角右键菜单：图标+文字、分隔线、危险操作红色、悬停高亮。

    交互契约：全局同一时间只保留一个菜单，弹出时自动收起上一个；
    菜单打开期间左键/右键/滚轮点击主窗口、主窗口失焦、最小化、移动，
    或按 Esc，都会立即收起——菜单只属于本应用，绝不会悬浮到其他应用之上。
    """

    _TRANSPARENT = "#ff00fe"
    _HOVER = POPUP_HOVER
    _DANGER = DANGER_FG
    _DANGER_HOVER = DANGER_BG
    _OUTSIDE_EVENTS = ("<Button-1>", "<Button-3>", "<MouseWheel>")

    _active: "ContextMenu | None" = None

    def __init__(self, master):
        self._master = master
        self._toplevel = master.winfo_toplevel()
        self._items: list = []
        self._top: tk.Toplevel | None = None
        self._canvas: tk.Canvas | None = None
        self._rows: list = []
        self._outside_bind_ids: dict[str, str] = {}

    def add_command(self, label: str, command=None, icon: str | None = None, danger: bool = False) -> None:
        self._items.append(("command", label, icon, command, danger))

    def add_separator(self) -> None:
        self._items.append(("separator", None, None, None, False))

    def clear(self) -> None:
        """复用实例重建菜单前清空旧菜单项。"""
        self._items.clear()

    def is_open(self) -> bool:
        return self._top is not None

    def popup(self, x_root: int, y_root: int, workarea: tuple[int, int, int, int] | None = None) -> None:
        self.close()
        # self.close() 已清掉自身登记，剩下的 _active 必然是别的实例
        if ContextMenu._active is not None:
            ContextMenu._active.close()
        row_h, sep_h, pad = S(40), S(9), S(8)
        width = S(200)
        height = pad * 2 + sum(sep_h if item[0] == "separator" else row_h for item in self._items)
        # 优先按点击点所在显示器的工作区裁剪；取不到时退回主屏，避免多显示器下菜单跳屏
        if workarea is not None:
            wx, wy, ww, wh = workarea
        else:
            wx, wy = 0, 0
            ww, wh = self._master.winfo_screenwidth(), self._master.winfo_screenheight()
        x_root = min(x_root, wx + ww - width - S(8))
        y_root = max(wy + S(6), min(y_root, wy + wh - height - S(8)))
        top = tk.Toplevel(self._master)
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        top.configure(bg=self._TRANSPARENT)
        try:
            top.attributes("-transparentcolor", self._TRANSPARENT)
        except tk.TclError:
            pass
        top.geometry(f"{width}x{height}+{int(x_root)}+{int(y_root)}")
        canvas = tk.Canvas(top, width=width, height=height, bg=self._TRANSPARENT, highlightthickness=0)
        canvas.pack()
        # 柔和阴影：两层偏移灰色圆角矩形
        rounded_rect(canvas, S(3), S(4), width - S(2), height - S(2), S(12), fill=SHADOW_1, outline="")
        rounded_rect(canvas, S(2), S(2), width - S(3), height - S(4), S(12), fill=SHADOW_2, outline="")
        # 卡片主体
        rounded_rect(canvas, S(1), S(1), width - S(4), height - S(6), S(12), fill=CARD_BG, outline=BORDER)
        y = pad
        rows = []
        for item in self._items:
            if item[0] == "separator":
                canvas.create_line(S(16), y + sep_h / 2, width - S(16), y + sep_h / 2, fill=BORDER)
                y += sep_h
                continue
            _, label, icon, command, danger = item
            y0, y1 = y, y + row_h
            rounded_rect(canvas, S(6), y0 + 2, width - S(7), y1 - 2, S(7), fill=CARD_BG, outline="", tags=f"bg{len(rows)}")
            text_x = S(18)
            if icon:
                paint_icon(canvas, icon, S(20), (y0 + y1) / 2, S(16), self._DANGER if danger else INK_SOFT)
                text_x = S(42)
            canvas.create_text(text_x, (y0 + y1) / 2, text=label, anchor="w",
                               fill=self._DANGER if danger else INK, font=F(12), tags=f"txt{len(rows)}")
            rows.append((y0, y1, item))
            y = y1
        canvas.bind("<Motion>", self._on_motion)
        canvas.bind("<Button-1>", self._on_click)
        top.bind("<Escape>", lambda _e: self.close())
        top.bind("<FocusOut>", lambda _e: self.close())
        self._bind_outside_closes()
        top.lift()
        try:
            top.focus_force()
        except tk.TclError:
            pass
        self._top = top
        self._canvas = canvas
        self._rows = rows
        ContextMenu._active = self

    def _bind_outside_closes(self) -> None:
        """在主窗口上挂“点外收起”绑定：任意点击/滚轮、最小化、移动尺寸都收起菜单。

        弹出菜单的处理函数需返回 "break" 阻止事件冒泡到这些绑定，否则刚弹出的菜单会被同一次点击立即关闭。
        Unmap/Configure 会被子控件冒泡（状态栏文字变化等布局调整），因此只认主窗口自身的事件。
        """
        toplevel = self._toplevel
        for event in self._OUTSIDE_EVENTS:
            self._outside_bind_ids[event] = toplevel.bind(event, lambda _e: self.close(), add="+")
        only_self = lambda e: self.close() if e.widget is toplevel else None
        self._outside_bind_ids["<Unmap>"] = toplevel.bind("<Unmap>", only_self, add="+")
        self._outside_bind_ids["<Configure>"] = toplevel.bind("<Configure>", only_self, add="+")
        self._outside_bind_ids["<FocusOut>"] = toplevel.bind("<FocusOut>", self._on_root_focus_out, add="+")

    def _on_root_focus_out(self, _event) -> None:
        if self._top is not None:
            self._toplevel.after_idle(self._close_if_focus_left_menu)

    def _close_if_focus_left_menu(self) -> None:
        """主窗口失焦后判定焦点去向：焦点只是移进菜单自身则保留，切换到其他应用则收起。

        覆盖 focus_force 未能把焦点交给菜单的残局——此时切换应用只触发主窗口 FocusOut，
        菜单自身的 FocusOut 不会触发，缺少这一步菜单就会 topmost 悬浮到别的应用上。
        """
        top = self._top
        if top is None:
            return
        try:
            focused = top.focus_get()
        except Exception:
            focused = None
        if focused is not None and focused.winfo_toplevel() is top:
            return
        self.close()

    def _on_motion(self, event) -> None:
        hover = next((i for i, (y0, y1, _item) in enumerate(self._rows) if y0 <= event.y < y1), None)
        for i, (_y0, _y1, item) in enumerate(self._rows):
            if i == hover:
                fill = self._DANGER_HOVER if item[4] else self._HOVER
            else:
                fill = CARD_BG
            self._canvas.itemconfig(f"bg{i}", fill=fill)

    def _on_click(self, event) -> str:
        for y0, y1, item in self._rows:
            if y0 <= event.y < y1:
                self.close()
                if item[3]:
                    item[3]()
                return "break"
        self.close()
        return "break"

    def close(self) -> None:
        if ContextMenu._active is self:
            ContextMenu._active = None
        if self._outside_bind_ids:
            for event, bind_id in self._outside_bind_ids.items():
                try:
                    self._toplevel.unbind(event, bind_id)
                except Exception:
                    pass
            self._outside_bind_ids = {}
        if self._top is not None:
            try:
                self._top.destroy()
            except Exception:
                pass
            self._top = None
            self._canvas = None
            self._rows = []


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
        self._flashing = False
        self._flash_job = None
        self.canvas = tk.Canvas(self, width=width, height=height, bg=master["bg"], highlightthickness=0)
        self.canvas.pack()
        self.entry = tk.Entry(self.canvas, textvariable=self._text, bg=CARD_BG, fg=INK, relief="flat", bd=0, insertbackground=GREEN, highlightthickness=0, font=M(12 if compact else 13), justify="left")
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
        rounded_rect(cv, 0, 0, w - 1, h - 1, r, fill=RED if self._flashing else FIELD_BORDER, outline="", tags="field")
        rounded_rect(cv, 1, 1, w - 2, h - 2, max(2, r - 1), fill=CARD_BG, outline="", tags="field")
        zone = self._zone
        if not self.compact:
            cv.create_line(zone, S(9), zone, h - S(9), fill=LIST_BORDER, tags="field")
            cx, mid = zone + S(13), h / 2
            off, span = S(10), S(6)
            cv.create_line(cx - span, mid - off + 3, cx, mid - off - 3, cx + span, mid - off + 3, fill=ICON_MUTED, width=S(2), joinstyle="round", capstyle="round", tags="field")
            cv.create_line(cx - span, mid + off - 3, cx, mid + off + 3, cx + span, mid + off - 3, fill=ICON_MUTED, width=S(2), joinstyle="round", capstyle="round", tags="field")
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
        clamped = value < self.minimum or value > self.maximum
        value = round(max(self.minimum, min(self.maximum, value)), 6)
        self.variable.set(int(value) if not self._allow_float else value)
        self._text.set(self._format(value))
        if clamped:
            self.flash_invalid()

    def flash_invalid(self) -> None:
        """输入越界被钳制时边框闪红，给出内联反馈而不是静默改值。"""
        if self._flash_job:
            try:
                self.after_cancel(self._flash_job)
            except tk.TclError:
                pass
        self._flashing = True
        self._render()
        self._flash_job = self.after(600, self._end_flash)

    def _end_flash(self) -> None:
        self._flashing = False
        self._flash_job = None
        self._render()

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
        self.entry = tk.Entry(self.canvas, textvariable=textvariable, bg=CARD_BG, fg=INK, relief="flat", bd=0, insertbackground=GREEN, highlightthickness=0, font=F(12))
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
        rounded_rect(cv, 1, 1, w - 2, h - 2, S(9), fill=CARD_BG, outline="", tags="field")
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
        self._focused = False
        self.box = tk.Canvas(self, width=S(16), height=S(16), bg=master["bg"], highlightthickness=0, cursor="hand2")
        self.box.pack(side="left")
        self.label = tk.Label(self, text=text, bg=master["bg"], fg=INK_SOFT, font=F(12), cursor="hand2")
        self.label.pack(side="left", padx=(S(8), 0))
        for widget in (self.box, self.label):
            widget.bind("<Button-1>", lambda _e: self.toggle())
        self.configure(takefocus=True)
        for keys in ("<Return>", "<KP_Enter>", "<space>"):
            self.bind(keys, self._toggle_key)
        self.bind("<FocusIn>", lambda _e: self._set_focused(True))
        self.bind("<FocusOut>", lambda _e: self._set_focused(False))
        variable.trace_add("write", lambda *_: self._draw())
        self._draw()

    def toggle(self) -> None:
        self.variable.set(not self.variable.get())

    def _toggle_key(self, _event=None) -> str:
        self.toggle()
        return "break"

    def _set_focused(self, focused: bool) -> None:
        self._focused = focused
        self._draw()

    def _draw(self) -> None:
        self.box.delete("all")
        if self.variable.get():
            rounded_rect(self.box, 1, 1, S(15), S(15), S(4), fill=GREEN, outline="")
            self.box.create_line(S(4), S(8.5), S(6.5), S(11), S(12), S(5), fill=ON_COLOR_FG, width=S(2), joinstyle="round", capstyle="round")
        else:
            rounded_rect(self.box, 1, 1, S(15), S(15), S(4), fill=CARD_BG, outline=BORDER_SOFT)
        if self._focused:
            self.box.create_rectangle(0, 0, S(15), S(15), outline=INK_SOFT, width=S(1))


class ToolTip:
    """悬浮说明气泡：延迟出现、移开即逝，自动避开屏幕右缘并支持贴底上翻。"""

    BG = TOAST_BG

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
        text_id = cv.create_text(S(12), S(9), text=self.text, anchor="nw", width=S(252), fill=TOAST_FG, font=F(11))
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
        color = GREEN if self._hover else ICON_MUTED
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
        self._last_layout: tuple[int, int] | None = None
        self.body = tk.Frame(self, bg=bg)
        self._window = self.create_window(1, 1, window=self.body, anchor="nw")
        self._relayout_job = None
        self.bind("<Configure>", self._schedule_relayout)
        self.bind("<Expose>", lambda e: self._schedule_relayout())

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
        if (w, h) == self._last_layout:
            # Expose、弹层开合等扰动也会触发本方法，尺寸未变时内容无需重画
            return
        self._last_layout = (w, h)
        self.delete("card")
        m = self._margin
        ground = self["bg"]
        if self._shadow:
            # 投影向下方偏移，逐层加深，模拟海拔；上左右只露出细微边缘
            rounded_rect(self, m - 2, m, w - m + 2, h - m + 3, self.radius + 2, fill=mix(ground, BORDER_SOFT, 0.10), outline="", tags="card")
            rounded_rect(self, m - 1, m + 1, w - m + 1, h - m + 5, self.radius + 1, fill=mix(ground, BORDER_SOFT, 0.16), outline="", tags="card")
            rounded_rect(self, m, m + 2, w - m, h - m + 7, self.radius, fill=mix(ground, BORDER_SOFT, 0.22), outline="", tags="card")
        ring = max(1, round(S(1.5)))
        rounded_rect(self, m, m, w - m, h - m, self.radius, fill=self._border, outline="", tags="card")
        rounded_rect(self, m + ring, m + ring, w - m - ring, h - m - ring, max(2, self.radius - ring), fill=self._bg, outline="", tags="card")
        self.tag_lower("card")
        self.coords(self._window, m + ring, m + ring)
        self.itemconfigure(self._window, width=w - 2 * m - 2 * ring, height=h - 2 * m - 2 * ring)


class ThinScrollbar(tk.Canvas):
    """悬浮式细滚动条：贴在滚动区域右缘内侧，内容不溢出时自动隐藏。

    target 需支持 yview / yscrollcommand（tk.Canvas、tk.Listbox 均可）。
    """

    def __init__(self, master, target: tk.Widget):
        super().__init__(master, width=S(8), height=S(60), bg=target["bg"], highlightthickness=0, bd=0)
        self._target = target
        self._fraction = (0.0, 1.0)
        self._placed = False
        self._active = False
        self._drag_origin: tuple[float, float] | None = None
        target.configure(yscrollcommand=self._on_target_scroll)
        self.bind("<Enter>", lambda _e: self._repaint(active=True))
        self.bind("<Leave>", lambda _e: self._repaint(active=False))
        self.bind("<Button-1>", self._on_drag_start)
        self.bind("<B1-Motion>", self._on_drag_motion)
        self.bind("<ButtonRelease-1>", self._on_drag_end)
        self._on_target_scroll(*target.yview())

    @property
    def _span(self) -> float:
        return max(0.05, self._fraction[1] - self._fraction[0])

    def _on_target_scroll(self, first: str, last: str) -> None:
        self._fraction = (float(first), float(last))
        scrollable = self._fraction[0] > 0.001 or self._fraction[1] < 0.999
        if scrollable and not self._placed:
            self._placed = True
            self.place(in_=self._target, relx=1.0, x=-S(2), rely=0, y=S(2), relheight=1.0, height=-S(4), anchor="ne")
        elif not scrollable and self._placed:
            self._placed = False
            self.place_forget()
        self._repaint()

    def _thumb_geometry(self) -> tuple[int, int, int]:
        """返回（滑块高度, 可滑动行程, 滑块纵向偏移）。"""
        height = max(1, self.winfo_height())
        thumb = max(S(28), round(height * self._span))
        travel = max(1, height - thumb)
        offset = round(travel * (self._fraction[0] / max(0.001, 1 - self._span)))
        return thumb, travel, offset

    def _repaint(self, active: bool | None = None) -> None:
        if active is not None:
            self._active = active
        self.delete("all")
        width, height = self.winfo_width(), self.winfo_height()
        if not self._placed or width < 2 or height < 2:
            return
        thumb, _travel, offset = self._thumb_geometry()
        bar_w = S(7) if self._active else S(5)
        color = mix(str(self["bg"]), INK_SOFT, 0.8 if self._active else 0.5)
        rounded_rect(self, (width - bar_w) // 2, offset, (width - bar_w) // 2 + bar_w, offset + thumb, S(3), fill=color, outline="")

    def _on_drag_start(self, event) -> None:
        _thumb, travel, _offset = self._thumb_geometry()
        self._drag_origin = (event.y, self._fraction[0], travel)

    def _on_drag_motion(self, event) -> None:
        if self._drag_origin is None:
            return
        origin_y, origin_first, travel = self._drag_origin
        movable = max(0.001, 1 - self._span)
        delta = (event.y - origin_y) / travel * movable
        self._target.yview_moveto(min(movable, max(0.0, origin_first + delta)))

    def _on_drag_end(self, _event) -> None:
        self._drag_origin = None
