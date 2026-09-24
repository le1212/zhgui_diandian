"""主题层：浅色/深色调色板。

配色在进程启动时读定（widgets 的常量在导入期生成），切换主题后重启生效；
偏好保存在 ui-state.json 的 "theme" 字段，与窗口几何等 UI 状态同文件。
两个调色板的键集合必须一致（tests/test_theme.py 守护），新增颜色时两边都要加。

配色体系源自根目录 DESIGN.md（Nintendo.com 2001 设计语言）：
冷铬（长春花蓝/靛青）承载结构与表面，暖色（信号橙/琥珀/任天堂红）只用于
动作与状态指示——暖色永远意味着"在这里行动"，冷铬绝不携带动作色。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

THEME_LIGHT: dict[str, str] = {
    # 基础表面与文字（碳素海军蓝墨水 + 铬靛青次级文字）
    "ink": "#21242e",            # carbon navy — 主文字
    "ink_strong": "#14161d",
    "ink_soft": "#3d4f97",       # chrome indigo — 次级文字/铬标签
    "gray": "#5e6c9c",           # muted indigo（从墨色色相派生，tests 守护 ≤10° 偏差）
    "on_color_fg": "#ffffff",
    "on_amber": "#14161d",       # 琥珀底上的文字：碳字配暖底是文档钦定配对，双主题同值
    # 品牌动作色（暖色只留给动作：信号橙 = 前进/提交；键名沿用 green_* 不改调用点）
    "green": "#f68d1f",          # signal orange — 主行动
    "green_hover": "#e48600",    # nav gold — 按压加深
    "green_text": "#c26200",     # 浅底上的正向状态文字（深橙保对比度）
    "green_deep": "#c05a00",     # 焦点描边/屏幕标记
    "green_bright": "#ecab37",   # amber — 深底浮层上的高亮
    # 表面（淡长春花蓝冷铬底盘）
    "sidebar_bg": "#e9edf8",
    "main_bg": "#f5f7fc",
    "header_bg": "#21242e",      # carbon — 顶部命令带
    "command_fg": "#f2f4fb",     # 命令带上的主要文字/按钮文字
    "command_muted": "#9aa3c2",  # 命令带上的次级文字
    "command_slab": "#2b3048",   # 命令带上的暗色按钮底
    "command_hover": "#363c58",  # 暗色按钮悬停
    "command_border": "#14161d", # 命令带描边/分隔
    "command_danger": "#f28087", # 命令带上的危险文字（浅红保证对比度）
    "card_bg": "#ffffff",
    "panel_bg": "#e8ecf6",
    "list_bg": "#f8f9fd",
    "list_border": "#dfe4f2",
    "border": "#e2e6f2",
    "field_border": "#ccd4e8",
    "pill_active": "#d7dff2",
    "border_soft": "#c3cde4",
    # 预览区 hero 着色场（文档的页面专属色调，随模式切换：长春花/电路青/赛道红）
    "hero_single": "#d1d1f2",    # 单点连点 — pale lavender（呼应 home lavender）
    "hero_multi": "#c8d8de",     # 多点任务 — 电路青（systems-teal 淡洗）
    "hero_record": "#ecd0d0",    # 录制操作 — 赛道红（games-red 淡洗）
    # 悬停与浅色底
    "neutral_hover": "#eef2fa",
    "subtle_bg": "#edf1f9",
    "subtle_hover": "#e2e8f5",
    "toolbar_bg": "#edf1f9",
    "toolbar_fg": "#4a5578",
    "toolbar_hover": "#dfe6f4",
    "row_hover": "#e9eef8",
    "popup_hover": "#e4eaf7",
    "task_active_fg": "#e60012",  # Nintendo red — 激活指示（活跃任务）
    "task_fg": "#5b6480",
    "icon_muted": "#97a1c0",
    # 危险色系（错误/破坏性复用品牌红）
    "red": "#e60012",
    "danger_fg": "#ab0f18",
    "danger_bg": "#fdeaea",
    "danger_hover": "#f7d6d6",
    "danger_soft_bg": "#fff1f1",
    "running_bg": "#a7282b",      # games red — 运行中（赛道红）
    # 深色浮层（toast、运行遮罩、录制遮罩等自带深底的场景）
    "toast_bg": "#23273a",        # carbon
    "toast_fg": "#f2f4fb",
    "overlay_bg": "#171923",
    "overlay_ink": "#f2f4fb",
    "overlay_ink_soft": "#9aa3c2",
    "overlay_green": "#ecab37",
    "overlay_accent": "#f0353f",
    "overlay_button": "#2b3048",
    "overlay_button_hover": "#3c4260",
    "danger_deep": "#8f1018",
    "running_peak": "#e60012",    # 呼吸动画峰值 = 品牌红
    "info_blue": "#206479",       # systems teal
    # 菜单阴影
    "shadow_1": "#c9cfe0",
    "shadow_2": "#dadeec",
}

THEME_DARK: dict[str, str] = {
    # 基础表面与文字（碳素命令层：靛青调冷灰文字）
    "ink": "#dde3f2",
    "ink_strong": "#f2f4fb",
    "ink_soft": "#9fb0d8",
    "gray": "#7b86ab",
    "on_color_fg": "#ffffff",
    "on_amber": "#14161d",
    # 品牌动作色（与浅色同一套暖色语义，深底上提亮）
    "green": "#f68d1f",
    "green_hover": "#f79c38",
    "green_text": "#f0a04a",
    "green_deep": "#d97706",
    "green_bright": "#f6b04b",
    # 表面（碳素 + 靛青暗铬）
    "sidebar_bg": "#191c2b",
    "main_bg": "#141622",
    "header_bg": "#1b1f30",      # 命令带（深色下比主底略提亮）
    "command_fg": "#f2f4fb",
    "command_muted": "#8a93b8",
    "command_slab": "#232842",
    "command_hover": "#2a3050",
    "command_border": "#0f1120",
    "command_danger": "#f28087",
    "card_bg": "#1f2334",
    "panel_bg": "#232840",
    "list_bg": "#1a1e2e",
    "list_border": "#2c3150",
    "border": "#2e3352",
    "field_border": "#333960",
    "pill_active": "#2e3557",
    "border_soft": "#3c4368",
    # 预览区 hero 着色场（深色下压暗的同族色洗，保持三模式可辨）
    "hero_single": "#3f4059",
    "hero_multi": "#182d3c",
    "hero_record": "#3d1b25",
    # 悬停与浅色底
    "neutral_hover": "#232842",
    "subtle_bg": "#212640",
    "subtle_hover": "#282e4c",
    "toolbar_bg": "#1b1f30",
    "toolbar_fg": "#aab4d4",
    "toolbar_hover": "#2e3557",
    "row_hover": "#232946",
    "popup_hover": "#2e3557",
    "task_active_fg": "#ff5b63",
    "task_fg": "#99a3c2",
    "icon_muted": "#5f6a8f",
    # 危险色系
    "red": "#f0353f",
    "danger_fg": "#f28087",
    "danger_bg": "#3a2327",
    "danger_hover": "#4a2a30",
    "danger_soft_bg": "#33202a",
    "running_bg": "#b03038",
    # 深色浮层（自带深底的场景在深色模式下略微提亮以区分背景）
    "toast_bg": "#20243a",
    "toast_fg": "#e8ecf8",
    "overlay_bg": "#0f1120",
    "overlay_ink": "#f2f4fb",
    "overlay_ink_soft": "#8890b0",
    "overlay_green": "#f6b04b",
    "overlay_accent": "#f0353f",
    "overlay_button": "#2a2f4a",
    "overlay_button_hover": "#3a4162",
    "danger_deep": "#a8353f",
    "running_peak": "#e60012",
    "info_blue": "#4d9db5",
    # 菜单阴影
    "shadow_1": "#0b0d16",
    "shadow_2": "#10121e",
}


def data_dir() -> Path:
    """与 desktop_app 相同的数据目录解析，供导入期读取主题偏好。"""
    configured = os.environ.get("DIANDIAN_DATA_DIR")
    if configured:
        return Path(configured)
    return Path(os.environ.get("APPDATA", Path.home())) / "Diandian"


def load_theme_name() -> str:
    try:
        payload = json.loads((data_dir() / "ui-state.json").read_text(encoding="utf-8"))
        name = payload.get("theme")
    except (OSError, ValueError, TypeError, AttributeError):
        return "light"
    return name if name in ("light", "dark") else "light"


THEME_NAME = load_theme_name()
_COLORS = THEME_DARK if THEME_NAME == "dark" else THEME_LIGHT


def color(token: str) -> str:
    return _COLORS[token]
