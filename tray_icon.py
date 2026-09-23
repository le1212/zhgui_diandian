"""系统托盘图标：独立消息线程 + 弹出菜单。

回调由调用方提供（通常是 ui_events 投递闭包），本模块不直接触碰 Tk。
Explorer 重启后通过 TaskbarCreated 广播重建图标；右键菜单用
TrackPopupMenu(TPM_RETURNCMD) 同步取回选项。
"""

from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes
from pathlib import Path

USER32 = ctypes.WinDLL("user32", use_last_error=True)
KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
SHELL32 = ctypes.WinDLL("shell32", use_last_error=True)

WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_APP = 0x8000
NIM_ADD = 0
NIM_MODIFY = 1
NIM_DELETE = 2
NIF_MESSAGE = 0x0001
NIF_ICON = 0x0002
NIF_TIP = 0x0004
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100
TPM_NONOTIFY = 0x0080
MF_STRING = 0x0000
SM_CXSMICON = 49
SW_RESTORE = 9


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class MSG(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("message", wintypes.UINT), ("wParam", wintypes.WPARAM),
                ("lParam", wintypes.LPARAM), ("time", wintypes.DWORD), ("pt", POINT)]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT), ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128), ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256), ("uVersion", wintypes.UINT), ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
    ]


USER32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
USER32.DefWindowProcW.restype = LRESULT
USER32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
USER32.RegisterClassW.restype = wintypes.ATOM
USER32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
USER32.CreateWindowExW.restype = wintypes.HWND
USER32.DestroyWindow.argtypes = [wintypes.HWND]
USER32.DestroyWindow.restype = wintypes.BOOL
USER32.GetMessageW.argtypes = [ctypes.POINTER(MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
USER32.GetMessageW.restype = ctypes.c_long
USER32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
USER32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
USER32.PostQuitMessage.argtypes = [ctypes.c_int]
USER32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
USER32.PostMessageW.restype = wintypes.BOOL
USER32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
USER32.RegisterWindowMessageW.restype = wintypes.UINT
USER32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
USER32.GetCursorPos.restype = wintypes.BOOL
USER32.SetForegroundWindow.argtypes = [wintypes.HWND]
USER32.SetForegroundWindow.restype = wintypes.BOOL
USER32.DestroyIcon.argtypes = [wintypes.HANDLE]
USER32.DestroyIcon.restype = wintypes.BOOL
USER32.GetSystemMetrics.argtypes = [ctypes.c_int]
USER32.GetSystemMetrics.restype = ctypes.c_int
SHELL32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
SHELL32.Shell_NotifyIconW.restype = wintypes.BOOL
USER32.CreatePopupMenu.argtypes = []
USER32.CreatePopupMenu.restype = wintypes.HMENU
USER32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
USER32.AppendMenuW.restype = wintypes.BOOL
USER32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.LPVOID]
USER32.TrackPopupMenu.restype = wintypes.BOOL
USER32.DestroyMenu.argtypes = [wintypes.HMENU]
USER32.DestroyMenu.restype = wintypes.BOOL
USER32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT]
USER32.LoadImageW.restype = wintypes.HANDLE
KERNEL32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
KERNEL32.GetModuleHandleW.restype = wintypes.HMODULE


class TrayIcon:
    """托盘图标句柄；回调签名均无参数，由调用方保证线程安全。"""

    MENU_SHOW = 1
    MENU_EXIT = 2

    def __init__(self, tooltip: str, icon_path: Path, on_show, on_exit) -> None:
        self._tooltip = tooltip[:127]
        self._icon_path = str(icon_path)
        self._on_show = on_show
        self._on_exit = on_exit
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._error: Exception | None = None
        self._window = 0
        self._added = False
        self._icon_handle = 0
        self._callback_message = WM_APP + 1
        self._taskbar_created = 0
        self._proc = None  # WNDPROC 引用须常驻，防止回调被垃圾回收

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._ready.clear()
        self._error = None
        self._thread = threading.Thread(target=self._message_loop, name="tray-icon", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=3):
            raise RuntimeError("托盘图标启动超时")
        if self._error:
            raise RuntimeError(str(self._error)) from self._error

    def is_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and self._added)

    def stop(self) -> None:
        if self._window:
            USER32.PostMessageW(self._window, WM_CLOSE, 0, 0)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1)
        self._thread = None
        self._window = 0
        self._added = False

    def _message_loop(self) -> None:
        class_name = "DiandianTrayIcon"

        @WNDPROC
        def window_proc(hwnd, message, wparam, lparam) -> int:
            if message == self._taskbar_created and self._taskbar_created:
                self._add_icon()
                return 0
            if message == self._callback_message:
                if lparam == WM_LBUTTONUP:
                    self._on_show()
                elif lparam == WM_RBUTTONUP:
                    self._show_menu(hwnd)
                return 0
            if message == WM_CLOSE:
                USER32.DestroyWindow(hwnd)
                return 0
            if message == WM_DESTROY:
                self._remove_icon()
                USER32.PostQuitMessage(0)
                return 0
            return USER32.DefWindowProcW(hwnd, message, wparam, lparam)

        self._proc = window_proc
        try:
            instance = KERNEL32.GetModuleHandleW(None)
            window_class = WNDCLASSW(lpfnWndProc=window_proc, hInstance=instance, lpszClassName=class_name)
            if not USER32.RegisterClassW(ctypes.byref(window_class)):
                raise OSError(ctypes.get_last_error(), "无法注册托盘窗口类")
            # 普通隐藏窗口而非消息窗口：需要接收 TaskbarCreated 广播
            self._window = int(USER32.CreateWindowExW(0, class_name, class_name, 0, 0, 0, 0, 0, None, None, instance, None))
            if not self._window:
                raise OSError(ctypes.get_last_error(), "无法创建托盘窗口")
            self._taskbar_created = USER32.RegisterWindowMessageW("TaskbarCreated")
            if not self._add_icon():
                raise OSError(ctypes.get_last_error(), "无法添加托盘图标")
            self._ready.set()
            message = MSG()
            while USER32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                USER32.TranslateMessage(ctypes.byref(message))
                USER32.DispatchMessageW(ctypes.byref(message))
        except Exception as error:
            self._error = error
            self._ready.set()

    def _add_icon(self) -> bool:
        small_icon = max(16, USER32.GetSystemMetrics(SM_CXSMICON))
        hicon = USER32.LoadImageW(None, self._icon_path, IMAGE_ICON, small_icon, small_icon, LR_LOADFROMFILE)
        data = NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        data.hWnd = self._window
        data.uID = 1
        data.uCallbackMessage = self._callback_message
        data.szTip = self._tooltip
        data.uFlags = NIF_MESSAGE | NIF_TIP
        if hicon:
            data.hIcon = hicon
            data.uFlags |= NIF_ICON
        self._added = bool(SHELL32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(data)))
        if self._added and hicon:
            # 图标句柄由 Shell 持有引用，删除托盘图标后再销毁，避免句柄泄漏
            self._icon_handle = int(hicon)
        elif hicon:
            USER32.DestroyIcon(hicon)
        return self._added

    def _remove_icon(self) -> None:
        if not self._added:
            return
        data = NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        data.hWnd = self._window
        data.uID = 1
        SHELL32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(data))
        self._added = False
        if self._icon_handle:
            USER32.DestroyIcon(self._icon_handle)
            self._icon_handle = 0

    def _show_menu(self, hwnd: int) -> None:
        # 先把焦点给托盘窗口，否则点击外部无法收起菜单（经典 KB135788 行为）
        USER32.SetForegroundWindow(hwnd)
        menu = USER32.CreatePopupMenu()
        USER32.AppendMenuW(menu, MF_STRING, self.MENU_SHOW, "显示主窗口")
        USER32.AppendMenuW(menu, MF_STRING, self.MENU_EXIT, "退出点点")
        point = POINT()
        USER32.GetCursorPos(ctypes.byref(point))
        selected = USER32.TrackPopupMenu(menu, TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY,
                                         point.x, point.y, 0, hwnd, None)
        USER32.DestroyMenu(menu)
        if selected == self.MENU_SHOW:
            self._on_show()
        elif selected == self.MENU_EXIT:
            self._on_exit()
