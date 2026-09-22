"""Asynchronous Windows Raw Input recorder for clicks, wheel, and keyboard."""

from __future__ import annotations

import ctypes
import threading
import time
import uuid
from ctypes import wintypes
from typing import Callable


USER32 = ctypes.WinDLL("user32", use_last_error=True)
KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
HCURSOR = wintypes.HANDLE

WM_INPUT = 0x00FF
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
RID_INPUT = 0x10000003
RIM_TYPEMOUSE = 0
RIM_TYPEKEYBOARD = 1
RIDEV_INPUTSINK = 0x00000100
RI_MOUSE_LEFT_BUTTON_DOWN = 0x0001
RI_MOUSE_RIGHT_BUTTON_DOWN = 0x0004
RI_MOUSE_MIDDLE_BUTTON_DOWN = 0x0010
RI_MOUSE_WHEEL = 0x0400
HWND_MESSAGE = -3


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [("usUsagePage", wintypes.USHORT), ("usUsage", wintypes.USHORT), ("dwFlags", wintypes.DWORD), ("hwndTarget", wintypes.HWND)]


class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [("dwType", wintypes.DWORD), ("dwSize", wintypes.DWORD), ("hDevice", wintypes.HANDLE), ("wParam", wintypes.WPARAM)]


class RAWMOUSE_BUTTONS(ctypes.Structure):
    _fields_ = [("usButtonFlags", wintypes.USHORT), ("usButtonData", wintypes.USHORT)]


class RAWMOUSE_BUTTON_UNION(ctypes.Union):
    _anonymous_ = ("buttons",)
    _fields_ = [("ulButtons", wintypes.ULONG), ("buttons", RAWMOUSE_BUTTONS)]


class RAWMOUSE(ctypes.Structure):
    _anonymous_ = ("button_union",)
    _fields_ = [
        ("usFlags", wintypes.USHORT),
        ("button_union", RAWMOUSE_BUTTON_UNION),
        ("ulRawButtons", wintypes.ULONG),
        ("lLastX", wintypes.LONG),
        ("lLastY", wintypes.LONG),
        ("ulExtraInformation", wintypes.ULONG),
    ]


class RAWKEYBOARD(ctypes.Structure):
    _fields_ = [
        ("MakeCode", wintypes.USHORT),
        ("Flags", wintypes.USHORT),
        ("Reserved", wintypes.USHORT),
        ("VKey", wintypes.USHORT),
        ("Message", wintypes.UINT),
        ("ExtraInformation", wintypes.ULONG),
    ]


class RAWINPUT_DATA(ctypes.Union):
    _fields_ = [("mouse", RAWMOUSE), ("keyboard", RAWKEYBOARD)]


class RAWINPUT(ctypes.Structure):
    _anonymous_ = ("data",)
    _fields_ = [("header", RAWINPUTHEADER), ("data", RAWINPUT_DATA)]


LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", HCURSOR),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


USER32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
USER32.DefWindowProcW.restype = LRESULT
USER32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
USER32.RegisterClassW.restype = wintypes.ATOM
USER32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
]
USER32.CreateWindowExW.restype = wintypes.HWND
USER32.RegisterRawInputDevices.argtypes = [ctypes.POINTER(RAWINPUTDEVICE), wintypes.UINT, wintypes.UINT]
USER32.RegisterRawInputDevices.restype = wintypes.BOOL
USER32.GetRawInputData.restype = wintypes.UINT
USER32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
USER32.GetCursorPos.restype = wintypes.BOOL
USER32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
USER32.PostMessageW.restype = wintypes.BOOL
USER32.DestroyWindow.argtypes = [wintypes.HWND]
USER32.DestroyWindow.restype = wintypes.BOOL
USER32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
USER32.GetMessageW.restype = wintypes.BOOL
USER32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
USER32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
USER32.PostQuitMessage.argtypes = [ctypes.c_int]
USER32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
USER32.MapVirtualKeyW.restype = wintypes.UINT
USER32.GetKeyNameTextW.argtypes = [wintypes.LONG, wintypes.LPWSTR, ctypes.c_int]
USER32.GetKeyNameTextW.restype = ctypes.c_int
KERNEL32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
KERNEL32.GetModuleHandleW.restype = wintypes.HMODULE


EventCallback = Callable[[dict, float], None]


class RawInputRecorder:
    def __init__(self, callback: EventCallback) -> None:
        self.callback = callback
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._window = 0
        self._error: Exception | None = None
        self._pressed_keys: set[int] = set()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._ready.clear()
        self._stop.clear()
        self._error = None
        self._thread = threading.Thread(target=self._message_loop, name="raw-input-recorder", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=2):
            raise RuntimeError("原始输入录制器启动超时")
        if self._error:
            raise RuntimeError(str(self._error)) from self._error

    def stop(self) -> None:
        self._stop.set()
        if self._window:
            USER32.PostMessageW(self._window, WM_CLOSE, 0, 0)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1)
        self._thread = None
        self._window = 0
        self._pressed_keys.clear()

    def _message_loop(self) -> None:
        class_name = f"DiandianRawInput_{uuid.uuid4().hex}"

        @WNDPROC
        def window_proc(hwnd: int, message: int, wparam: int, lparam: int) -> int:
            if message == WM_INPUT and not self._stop.is_set():
                self._handle_raw_input(lparam)
                return 0
            if message == WM_CLOSE:
                USER32.DestroyWindow(hwnd)
                return 0
            if message == WM_DESTROY:
                USER32.PostQuitMessage(0)
                return 0
            return USER32.DefWindowProcW(hwnd, message, wparam, lparam)

        self._window_proc = window_proc
        try:
            instance = KERNEL32.GetModuleHandleW(None)
            window_class = WNDCLASSW(lpfnWndProc=window_proc, hInstance=instance, lpszClassName=class_name)
            if not USER32.RegisterClassW(ctypes.byref(window_class)):
                raise OSError(ctypes.get_last_error(), "无法注册 Raw Input 窗口")
            self._window = int(USER32.CreateWindowExW(0, class_name, class_name, 0, 0, 0, 0, 0, HWND_MESSAGE, None, instance, None))
            if not self._window:
                raise OSError(ctypes.get_last_error(), "无法创建 Raw Input 窗口")
            devices = (RAWINPUTDEVICE * 2)(
                RAWINPUTDEVICE(1, 2, RIDEV_INPUTSINK, self._window),
                RAWINPUTDEVICE(1, 6, RIDEV_INPUTSINK, self._window),
            )
            if not USER32.RegisterRawInputDevices(devices, len(devices), ctypes.sizeof(RAWINPUTDEVICE)):
                raise OSError(ctypes.get_last_error(), "无法注册鼠标和键盘原始输入")
            self._ready.set()
            message = wintypes.MSG()
            while USER32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                USER32.TranslateMessage(ctypes.byref(message))
                USER32.DispatchMessageW(ctypes.byref(message))
        except Exception as error:
            self._error = error
            self._ready.set()

    def _handle_raw_input(self, handle: int) -> None:
        size = wintypes.UINT()
        header_size = ctypes.sizeof(RAWINPUTHEADER)
        if USER32.GetRawInputData(handle, RID_INPUT, None, ctypes.byref(size), header_size) == 0xFFFFFFFF:
            return
        buffer = ctypes.create_string_buffer(size.value)
        if USER32.GetRawInputData(handle, RID_INPUT, buffer, ctypes.byref(size), header_size) == 0xFFFFFFFF:
            return
        raw = ctypes.cast(buffer, ctypes.POINTER(RAWINPUT)).contents
        timestamp = time.perf_counter()
        if raw.header.dwType == RIM_TYPEMOUSE:
            self._handle_mouse(raw.mouse, timestamp)
        elif raw.header.dwType == RIM_TYPEKEYBOARD:
            self._handle_keyboard(raw.keyboard, timestamp)

    def _handle_mouse(self, mouse: RAWMOUSE, timestamp: float) -> None:
        flags = mouse.usButtonFlags
        if not flags:
            # 纯移动事件不含按键/滚轮标志：高报点率鼠标下每次移动都会进入这里，
            # 提前返回避免无意义的 GetCursorPos 系统调用
            return
        point = POINT()
        USER32.GetCursorPos(ctypes.byref(point))
        button = None
        if flags & RI_MOUSE_LEFT_BUTTON_DOWN:
            button = "左"
        elif flags & RI_MOUSE_RIGHT_BUTTON_DOWN:
            button = "右"
        elif flags & RI_MOUSE_MIDDLE_BUTTON_DOWN:
            button = "中"
        if button:
            self.callback({"type": "click", "button": button, "x": point.x, "y": point.y}, timestamp)
        if flags & RI_MOUSE_WHEEL:
            delta = ctypes.c_short(mouse.usButtonData).value
            if 0 < abs(delta) < 120:
                delta *= 120
            self.callback({"type": "scroll", "scroll_delta": delta, "x": point.x, "y": point.y}, timestamp)

    def _handle_keyboard(self, keyboard: RAWKEYBOARD, timestamp: float) -> None:
        key_code = int(keyboard.VKey)
        if key_code in {0x1B, 0x71, 0x75, 0x76}:
            return
        if keyboard.Message in {WM_KEYDOWN, WM_SYSKEYDOWN}:
            if key_code in self._pressed_keys:
                return
            self._pressed_keys.add(key_code)
            self.callback({"type": "key_down", "key_code": key_code, "key_name": key_name(key_code, keyboard.MakeCode)}, timestamp)
        elif keyboard.Message in {WM_KEYUP, WM_SYSKEYUP}:
            self._pressed_keys.discard(key_code)
            self.callback({"type": "key_up", "key_code": key_code, "key_name": key_name(key_code, keyboard.MakeCode)}, timestamp)


def key_name(key_code: int, scan_code: int = 0) -> str:
    known = {
        0x08: "Backspace", 0x09: "Tab", 0x0D: "Enter", 0x10: "Shift", 0x11: "Ctrl", 0x12: "Alt",
        0x20: "Space", 0x21: "Page Up", 0x22: "Page Down", 0x23: "End", 0x24: "Home",
        0x25: "←", 0x26: "↑", 0x27: "→", 0x28: "↓", 0x2D: "Insert", 0x2E: "Delete",
    }
    if key_code in known:
        return known[key_code]
    if 0x30 <= key_code <= 0x39 or 0x41 <= key_code <= 0x5A:
        return chr(key_code)
    if 0x70 <= key_code <= 0x87:
        return f"F{key_code - 0x6F}"
    buffer = ctypes.create_unicode_buffer(64)
    lparam = (scan_code or USER32.MapVirtualKeyW(key_code, 0)) << 16
    if USER32.GetKeyNameTextW(lparam, buffer, len(buffer)):
        return buffer.value
    return f"VK {key_code}"
