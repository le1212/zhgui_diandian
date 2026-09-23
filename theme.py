"""主题层：浅色/深色调色板。

配色在进程启动时读定（widgets 的常量在导入期生成），切换主题后重启生效；
偏好保存在 ui-state.json 的 "theme" 字段，与窗口几何等 UI 状态同文件。
两个调色板的键集合必须一致（tests/test_theme.py 守护），新增颜色时两边都要加。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

THEME_LIGHT: dict[str, str] = {
    # 基础表面与文字
    "ink": "#22333d",
    "ink_strong": "#181d1a",
    "ink_soft": "#5d7280",
    "gray": "#8a9aa0",
    "on_color_fg": "#ffffff",
    # 品牌绿
    "green": "#4a8a7a",
    "green_hover": "#3d7a6c",
    "green_text": "#3d7a6c",
    "green_deep": "#356a5e",
    "green_bright": "#79c99f",
    # 表面
    "sidebar_bg": "#f2f5f4",
    "main_bg": "#f5f7f6",
    "header_bg": "#fafcfb",
    "card_bg": "#ffffff",
    "panel_bg": "#e6ebe8",
    "list_bg": "#fbfdfc",
    "list_border": "#e4eeee",
    "border": "#e8eceb",
    "field_border": "#e7ebea",
    "pill_active": "#dde6e2",
    "border_soft": "#d0ddd8",
    # 悬停与浅色底
    "neutral_hover": "#f1f9f6",
    "subtle_bg": "#f1f5f3",
    "subtle_hover": "#e7efeb",
    "toolbar_bg": "#f4faf7",
    "toolbar_fg": "#49625b",
    "toolbar_hover": "#dcf0e7",
    "row_hover": "#e6f3ee",
    "popup_hover": "#e3f3ec",
    "task_active_fg": "#157a5e",
    "task_fg": "#5c6f6a",
    "icon_muted": "#9db0aa",
    # 危险色系
    "red": "#e5484d",
    "danger_fg": "#b3423f",
    "danger_bg": "#fdeceb",
    "danger_hover": "#f8d8d5",
    "danger_soft_bg": "#fff1f0",
    "running_bg": "#a84e48",
    # 深色浮层（toast、运行遮罩、录制遮罩等自带深底的场景）
    "toast_bg": "#31434c",
    "toast_fg": "#eef6f2",
    "overlay_bg": "#1a1a1a",
    "overlay_ink": "#f2f7f4",
    "overlay_ink_soft": "#b8c3bc",
    "overlay_green": "#79c99f",
    "overlay_accent": "#ef8c82",
    "overlay_button": "#303a33",
    "overlay_button_hover": "#414d45",
    "danger_deep": "#8d3f3a",
    "running_peak": "#c85a50",
    "info_blue": "#5d8290",
    # 菜单阴影
    "shadow_1": "#d4d9d7",
    "shadow_2": "#e2e6e4",
}

THEME_DARK: dict[str, str] = {
    # 基础表面与文字
    "ink": "#e6ede9",
    "ink_strong": "#f2f7f4",
    "ink_soft": "#a7b7b0",
    "gray": "#7e9089",
    "on_color_fg": "#ffffff",
    # 品牌绿
    "green": "#4fa891",
    "green_hover": "#5bb9a0",
    "green_text": "#6cc4ac",
    "green_deep": "#3f8d7a",
    "green_bright": "#79c99f",
    # 表面
    "sidebar_bg": "#1b2320",
    "main_bg": "#171d1b",
    "header_bg": "#1e2623",
    "card_bg": "#202925",
    "panel_bg": "#242e2a",
    "list_bg": "#1c2422",
    "list_border": "#2c3733",
    "border": "#2f3a35",
    "field_border": "#2f3a35",
    "pill_active": "#2c4038",
    "border_soft": "#3a4640",
    # 悬停与浅色底
    "neutral_hover": "#24312c",
    "subtle_bg": "#232d29",
    "subtle_hover": "#2a3631",
    "toolbar_bg": "#1f2a26",
    "toolbar_fg": "#b7c6c0",
    "toolbar_hover": "#2a4a3e",
    "row_hover": "#253630",
    "popup_hover": "#2a4a3e",
    "task_active_fg": "#55c9a5",
    "task_fg": "#9db3ab",
    "icon_muted": "#6c8078",
    # 危险色系
    "red": "#e5484d",
    "danger_fg": "#ef8c82",
    "danger_bg": "#3a2320",
    "danger_hover": "#4a2a26",
    "danger_soft_bg": "#33211f",
    "running_bg": "#a84e48",
    # 深色浮层（自带深底的场景在深色模式下略微提亮以区分背景）
    "toast_bg": "#263530",
    "toast_fg": "#e6f0ea",
    "overlay_bg": "#101412",
    "overlay_ink": "#f2f7f4",
    "overlay_ink_soft": "#8fa09a",
    "overlay_green": "#79c99f",
    "overlay_accent": "#ef8c82",
    "overlay_button": "#2c3733",
    "overlay_button_hover": "#3a4640",
    "danger_deep": "#a34a44",
    "running_peak": "#c85a50",
    "info_blue": "#6aa9bd",
    # 菜单阴影
    "shadow_1": "#0c100e",
    "shadow_2": "#121714",
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
