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

from winapi import SCALE, USER32, VK_LBUTTON

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


def paint_icon(canvas: tk.Canvas, name: str, cx: float, cy: float, size: float, color: str, bg: str = "#ffffff") -> None:
    """在画布上绘制矢量小图标，(cx, cy) 为图标中心，size 为已缩放的边长。PIL 3x 超采样抗锯齿。"""
    key = (name, color, int(size), bg)
    if key not in _ICON_PHOTO_CACHE:
        _ICON_PHOTO_CACHE[key] = ImageTk.PhotoImage(_render_icon_image(name, color, int(size), bg))
    canvas.create_image(cx, cy, image=_ICON_PHOTO_CACHE[key])


def get_icon_photo(name: str, color: str, size: int, bg: str = "#ffffff") -> ImageTk.PhotoImage:
    """获取图标 PhotoImage（带缓存），供 Label 等非 Canvas 组件使用。"""
    key = (name, color, int(size), bg)
    if key not in _ICON_PHOTO_CACHE:
        _ICON_PHOTO_CACHE[key] = ImageTk.PhotoImage(_render_icon_image(name, color, int(size), bg))
    return _ICON_PHOTO_CACHE[key]


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


class PillButton(tk.Frame):
    """按钮：支持图标、悬停、禁用与运行中改写文本/配色。使用 Frame+Label 实现，避免 Canvas 重绘闪烁。"""

    def __init__(self, master, text: str, command=None, *, width, height, radius=10,
                 bg="#ffffff", fg=INK, border=None, hover_bg=None, icon=None,
                 icon_color=None, font=None, align="center", padx=18, trailing=None, icon_size=None):
        self.command = command
        self._width = width
        self._height = height
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
        super().__init__(master, width=width, height=height, bg=bg,
                         highlightthickness=1 if border else 0,
                         highlightbackground=border or bg, cursor="hand2")
        self.pack_propagate(False)
        self._content = tk.Frame(self, bg=bg)
        if align == "left":
            self._content.pack(side="left", padx=padx)
        else:
            self._content.place(relx=0.5, rely=0.5, anchor="center")
        self._icon_label = None
        if icon:
            self._icon_label = tk.Label(self._content, bg=bg)
            self._icon_label.pack(side="left", padx=(0, S(8)))
        self._text_label = tk.Label(self._content, text=text, fg=fg, font=font, bg=bg)
        self._text_label.pack(side="left")
        self._trailing_label = None
        if trailing:
            self._trailing_label = tk.Label(self, bg=bg)
            self._trailing_label.place(relx=1.0, rely=0.5, anchor="e", x=-S(20))
        for w in (self, self._content, self._icon_label, self._text_label, self._trailing_label):
            if w is not None:
                w.bind("<Enter>", self._on_enter)
                w.bind("<Leave>", self._on_leave)
                w.bind("<Button-1>", self._on_click)
        self._update_appearance()

    def _current_bg(self) -> str:
        return self._bg if self._state != "normal" else (self._hover_bg if self._hover else self._bg)

    def _current_fg(self) -> str:
        return self._fg if self._state == "normal" else mix(self._fg, self._bg, 0.45)

    def _current_icon_color(self) -> str:
        return self._icon_color if self._state == "normal" else mix(self._icon_color, self._bg, 0.45)

    def _update_appearance(self) -> None:
        bg = self._current_bg()
        fg = self._current_fg()
        super().configure(bg=bg)
        if self._border:
            super().configure(highlightbackground=self._border)
        self._content.configure(bg=bg)
        self._text_label.configure(bg=bg, fg=fg)
        if self._icon_label:
            self._icon_label.configure(bg=bg, image=get_icon_photo(self._icon, self._current_icon_color(), self._icon_size, bg))
        if self._trailing_label:
            self._trailing_label.configure(bg=bg, image=get_icon_photo(self._trailing, "#9db0aa", S(14), bg))

    def set_trailing(self, name: str) -> None:
        self._trailing = name
        if self._trailing_label:
            self._trailing_label.destroy()
            self._trailing_label = None
        if name:
            self._trailing_label = tk.Label(self, bg=self._current_bg())
            self._trailing_label.place(relx=1.0, rely=0.5, anchor="e", x=-S(20))
            self._trailing_label.bind("<Enter>", self._on_enter)
            self._trailing_label.bind("<Leave>", self._on_leave)
            self._trailing_label.bind("<Button-1>", self._on_click)
        self._update_appearance()

    def _on_enter(self, _event) -> None:
        self._hover = True
        self._update_appearance()

    def _on_leave(self, _event) -> None:
        self._hover = False
        self._update_appearance()

    def _on_click(self, _event) -> None:
        if self._state == "normal" and self.command:
            self.command()

    def configure(self, cnf=None, **kw):
        if cnf:
            kw.update(cnf)
        if "text" in kw:
            self._text = kw.pop("text")
            self._text_label.configure(text=self._text)
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
        self._update_appearance()


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
            rounded_rect(self, m - 2, m, w - m + 2, h - m + 3, self.radius + 2, fill=mix(ground, "#5d7a72", 0.10), outline="", tags="card")
            rounded_rect(self, m - 1, m + 1, w - m + 1, h - m + 5, self.radius + 1, fill=mix(ground, "#5d7a72", 0.16), outline="", tags="card")
            rounded_rect(self, m, m + 2, w - m, h - m + 7, self.radius, fill=mix(ground, "#5d7a72", 0.22), outline="", tags="card")
        ring = max(1, round(S(1.5)))
        rounded_rect(self, m, m, w - m, h - m, self.radius, fill=self._border, outline="", tags="card")
        rounded_rect(self, m + ring, m + ring, w - m - ring, h - m - ring, max(2, self.radius - ring), fill=self._bg, outline="", tags="card")
        self.tag_lower("card")
        self.coords(self._window, m + ring, m + ring)
        self.itemconfigure(self._window, width=w - 2 * m - 2 * ring, height=h - 2 * m - 2 * ring)
