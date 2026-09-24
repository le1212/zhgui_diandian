"""画布绘制域：预览区、hero 空状态、背景点缀与各类 Canvas 纹理。

依赖宿主属性：root、workspace、_screen_thumb、_preview_point、_thumb_photo、
_thumb_render_size、_thumb_display_rect、_dot_photo、_dot_photo_key、
_f2_photo、_f2_photo_key、_empty_btn_rect。
"""

from __future__ import annotations

import math
import tkinter as tk

from PIL import Image, ImageDraw, ImageFont, ImageTk

from image_locator import virtual_screen_origin
from theme import THEME_NAME
from winapi import USER32
from widgets import (
    BORDER, BORDER_SOFT, CARD_BG, COMMAND_SLAB, F, GREEN, GREEN_BRIGHT, GREEN_DEEP,
    HEADER_BG, HERO_MULTI, HERO_RECORD, HERO_SINGLE, ICON_MUTED, INK, MAIN_BG,
    ON_COLOR_FG, OVERLAY_BG, PILL_ACTIVE, S, SP_MD, TOOLBAR_BG, mix, rounded_rect,
    shade, text_font,
)


class CanvasPaintingMixin:
    """预览画布与装饰纹理的全部绘制逻辑，均只读宿主状态、不改业务数据。"""

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

    def _f2_watermark(self, box_h: float):
        """空状态 hero 的描边大字水印；Tk 文字无描边能力，用 PIL stroke 预渲染（Arial Black）。"""
        size = int(box_h * 0.45)
        if size < 40:
            return None
        if self._f2_photo_key != size:
            font = None
            for name in ("ariblk.ttf", "arialbd.ttf"):  # Arial Black 优先，回退 Arial Bold
                try:
                    font = ImageFont.truetype(name, size)
                    break
                except OSError:
                    continue
            if font is None:
                self._f2_photo_key, self._f2_photo = size, None
                return None
            # 墨色随主题：浅色主题用碳墨，深色主题用亮铬文字色，水印在两套 hero 场上都可辨
            ink = (33, 36, 46) if THEME_NAME == "light" else (221, 227, 242)
            img = Image.new("RGBA", (size * 3, size * 2), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            draw.text((img.width / 2, img.height / 2), "F2", font=font, anchor="mm",
                      fill=ink + (22,), stroke_width=max(2, size // 28), stroke_fill=ink + (48,))
            self._f2_photo = ImageTk.PhotoImage(img.crop(img.getbbox()))
            self._f2_photo_key = size
        return self._f2_photo

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
        # hero 着色场随模式换色（DESIGN.md 页面专属色调：单点=长春花/多点=电路青/录制=赛道红）
        hero_tint = {"单点连点": HERO_SINGLE, "多点任务": HERO_MULTI, "录制操作": HERO_RECORD}.get(self.mode_var.get(), PILL_ACTIVE)
        rounded_rect(cv, ox, oy, ox + box_w, oy + box_h, S(10), fill=hero_tint, outline=BORDER)
        if point:
            vx, vy = virtual_screen_origin()
            fx = min(.96, max(.04, (point[0] - vx) / virtual_w))
            fy = min(.96, max(.04, (point[1] - vy) / virtual_h))
            self._draw_marker(cv, ox + fx * box_w, oy + fy * box_h, point[2])
        else:
            # hero 着色场：无落点时铺电路走线与描边水印，与卡带、捕获按钮组成待机面
            if box_h > S(220):
                self._paint_circuit_traces(cv, ox, oy, box_w, box_h)
            photo = self._f2_watermark(box_h)
            if photo is not None:
                cv.create_image(ox + box_w / 2, oy + box_h / 2, image=photo)
            btn_w, btn_h = min(S(186), width * 0.5), S(36)
            bx, by = (width - btn_w) / 2, (height - btn_h) / 2
            self._empty_btn_rect = (bx, by, bx + btn_w, by + btn_h)
            # 待机面：卡带是 hero 的主装饰（体量压过按钮形成层级；高度不够时省略，避免顶到显示器轮廓）
            cw, ch = S(96), S(68)
            cx0, cy0 = width / 2 - cw / 2, by - S(16) - ch
            if cy0 > oy + S(16):
                rounded_rect(cv, cx0, cy0, cx0 + cw, cy0 + ch, S(8), fill=CARD_BG, outline=BORDER_SOFT)
                rounded_rect(cv, cx0 + S(13), cy0 + S(9), cx0 + cw - S(13), cy0 + S(34), S(4), fill=GREEN_BRIGHT, outline="")
                cv.create_rectangle(cx0 + cw / 2 - S(16), cy0 + ch - S(8), cx0 + cw / 2 + S(16), cy0 + ch - S(3), fill=BORDER_SOFT, outline="")
            rounded_rect(cv, bx, by, bx + btn_w, by + btn_h, S(8), fill=GREEN, outline="")
            # 圆形前进箭头（DESIGN.md button-icon-arrow）：hero CTA 右缘的"前进"强符号
            disc = S(18)
            dcx, dcy = bx + btn_w - S(13), by + btn_h / 2
            cv.create_oval(dcx - disc / 2, dcy - disc / 2, dcx + disc / 2, dcy + disc / 2, fill=shade(GREEN, 0.78), outline="")
            cv.create_line(dcx - S(2), dcy - S(3), dcx + S(3), dcy, dcx - S(2), dcy + S(3),
                           fill=ON_COLOR_FG, width=S(2), joinstyle="round", capstyle="round")
            cv.create_text(width / 2 - S(8), height / 2, text="点击捕获第一个位置  F2", fill=ON_COLOR_FG, font=F(11, "bold"))
            cv.create_text(width / 2, by + btn_h + S(18), text="捕获时点点会暂时隐藏，点击目标位置即可", fill=ICON_MUTED, font=F(11))

    def _paint_bg_deco(self, cv: tk.Canvas) -> None:
        """主区左右留白边的竖向点轨（背景板点缀，约 6% 对比度，不与内容争抢）。"""
        cv.delete("all")
        w, h = cv.winfo_width(), cv.winfo_height()
        if w < S(60) or h < S(320):
            return
        color = mix(MAIN_BG, INK, 0.06)
        r = S(1.5)
        for x in (S(18), w - S(18)):
            yy = S(330)
            while yy < h - S(50):
                cv.create_oval(x - r, yy - r, x + r, yy + r, fill=color, outline="")
                yy += S(16)

    def _halftone_canvas(self, parent: tk.Frame) -> tk.Canvas:
        """碳素带上的半调网点纹理块（扬声器格栅隐喻）：逐列渐隐的圆点，随尺寸变化重绘。"""
        cv = tk.Canvas(parent, bg=HEADER_BG, highlightthickness=0)

        def repaint(_event=None) -> None:
            cv.delete("all")
            w, h = cv.winfo_width(), cv.winfo_height()
            if w < 4 or h < 4:
                return
            step, color = S(9), COMMAND_SLAB
            key = (w, h, step, color)
            if key != getattr(cv, "_halftone_key", None):
                img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                draw = ImageDraw.Draw(img)
                r_max = max(1.0, step / 4.5)
                xx = step / 2
                while xx < w:
                    # 越靠右网点越大，形成向左渐隐的格栅
                    r = max(0.6, r_max * (xx / w))
                    yy = step / 2
                    while yy < h:
                        draw.ellipse((xx - r, yy - r, xx + r, yy + r), fill=color)
                        yy += step
                    xx += step
                cv._halftone_photo = ImageTk.PhotoImage(img)
                cv._halftone_key = key
            cv.create_image(0, 0, image=cv._halftone_photo, anchor="nw")

        cv.bind("<Configure>", repaint)
        return cv

    def _paint_circuit_traces(self, cv: tk.Canvas, x: float, y: float, w: float, h: float) -> None:
        """hero 着色场里的电路走线纹理：带 45° 转角的走线与节点圆点，极低对比度。"""
        color = BORDER_SOFT
        for i, ratio in enumerate((0.16, 0.5, 0.84)):
            ly = y + h * ratio
            x0, x1 = x + w * 0.05, x + w * 0.95
            mid = x0 + (x1 - x0) * (0.35 if i % 2 == 0 else 0.65)
            jog = S(14) if i % 2 == 0 else -S(14)
            cv.create_line(x0, ly, mid, ly, mid, ly + jog, x1, ly + jog, fill=color, width=S(1))
            for nx, ny in ((x0, ly), (x1, ly + jog)):
                cv.create_oval(nx - S(3), ny - S(3), nx + S(3), ny + S(3), outline=color, width=S(1))

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

    def _clear_preview_point(self) -> None:
        self._preview_point = None
        self._screen_thumb = None
        self._thumb_render_size = None

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
        canvas.pack(padx=SP_MD, pady=SP_MD)
        canvas.create_image(0, 0, image=photo, anchor="nw")
        canvas._large_photo = photo
        if self._preview_point:
            vx, vy = virtual_screen_origin()
            fx = min(.98, max(.02, (self._preview_point[0] - vx) / img.width))
            fy = min(.98, max(.02, (self._preview_point[1] - vy) / img.height))
            self._draw_marker(canvas, fx * disp_w, fy * disp_h, self._preview_point[2])
        x, y = self._centered_position(disp_w + S(24), disp_h + S(24))
        top.geometry(f"+{x}+{y}")
        top.bind("<Escape>", lambda _e: top.destroy())
        canvas.bind("<Button-1>", lambda _e: top.destroy())
        top.focus_set()
