"""开机自启管理：写入当前用户注册表 Run 键，无需管理员权限。

命令按安装形态生成：打包版直接指向 exe；源码运行优先用 pythonw 避免
启动时闪现控制台。仅操作本应用自己的注册表值，不触碰其他条目。
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from pathlib import Path

HKEY_CURRENT_USER = 0x80000001
REG_SZ = 1
RRF_RT_REG_SZ = 0x0002
ERROR_FILE_NOT_FOUND = 2

ADVAPI32 = ctypes.WinDLL("advapi32", use_last_error=True)
ADVAPI32.RegGetValueW.argtypes = [wintypes.HKEY, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPDWORD, wintypes.LPVOID, wintypes.LPDWORD]
ADVAPI32.RegGetValueW.restype = ctypes.c_long
ADVAPI32.RegSetKeyValueW.argtypes = [wintypes.HKEY, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPCVOID, wintypes.DWORD]
ADVAPI32.RegSetKeyValueW.restype = ctypes.c_long
ADVAPI32.RegDeleteKeyValueW.argtypes = [wintypes.HKEY, wintypes.LPCWSTR, wintypes.LPCWSTR]
ADVAPI32.RegDeleteKeyValueW.restype = ctypes.c_long

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE_NAME = "Diandian"


def autostart_command() -> str:
    """生成写入 Run 键的启动命令；路径一律加引号，兼容空格与中文目录。"""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    python = Path(sys.executable)
    pythonw = python.with_name("pythonw.exe")
    exe = pythonw if pythonw.exists() else python
    script = Path(__file__).parent / "desktop_app.py"
    return f'"{exe}" "{script}"'


def is_enabled() -> bool:
    size = wintypes.DWORD(0)
    result = ADVAPI32.RegGetValueW(HKEY_CURRENT_USER, _RUN_KEY, _VALUE_NAME, RRF_RT_REG_SZ, None, None, ctypes.byref(size))
    return result == 0


def set_enabled(enable: bool) -> bool:
    """开启或关闭自启；关闭时值不存在也视为成功。"""
    if enable:
        data = ctypes.create_unicode_buffer(autostart_command())
        return ADVAPI32.RegSetKeyValueW(HKEY_CURRENT_USER, _RUN_KEY, _VALUE_NAME, REG_SZ, data, ctypes.sizeof(data)) == 0
    result = ADVAPI32.RegDeleteKeyValueW(HKEY_CURRENT_USER, _RUN_KEY, _VALUE_NAME)
    return result == 0 or result == ERROR_FILE_NOT_FOUND
