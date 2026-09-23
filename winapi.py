"""Win32 互操作层：DPI 感知、输入注入、剪贴板与窗口过程子类化。

全部为无 Tk 依赖的底层能力，便于独立测试；界面层（widgets）与应用层
（desktop_app）只通过本模块访问系统 API。
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

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
GMEM_MOVEABLE = 0x2000
CF_UNICODETEXT = 13
HWND_TOPMOST = -1
SWP_SHOWWINDOW = 0x0040
GWLP_WNDPROC = -4
WM_DESTROY = 0x0002
WM_SIZE = 0x0005
WM_SETREDRAW = 0x000B
WM_CAPTURECHANGED = 0x0215
WM_SIZING = 0x0214
WM_EXITSIZEMOVE = 0x0233
RDW_INVALIDATE = 0x0001
RDW_ERASE = 0x0004
RDW_ALLCHILDREN = 0x0080
RDW_FRAME = 0x0400
RDW_UPDATENOW = 0x0100


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
USER32.GetKeyState.argtypes = [ctypes.c_int]
USER32.GetKeyState.restype = ctypes.c_short
USER32.OpenClipboard.argtypes = [wintypes.HWND]
USER32.OpenClipboard.restype = wintypes.BOOL
USER32.EmptyClipboard.argtypes = []
USER32.EmptyClipboard.restype = wintypes.BOOL
USER32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
USER32.SetClipboardData.restype = wintypes.HANDLE
USER32.CloseClipboard.argtypes = []
USER32.CloseClipboard.restype = wintypes.BOOL
KERNEL32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
KERNEL32.GlobalAlloc.restype = wintypes.HGLOBAL
KERNEL32.GlobalLock.argtypes = [wintypes.HGLOBAL]
KERNEL32.GlobalLock.restype = wintypes.LPVOID
KERNEL32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
KERNEL32.GlobalUnlock.restype = wintypes.BOOL
KERNEL32.GlobalFree.argtypes = [wintypes.HGLOBAL]
KERNEL32.GlobalFree.restype = wintypes.HGLOBAL
USER32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
USER32.GetAncestor.restype = wintypes.HWND
USER32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
USER32.MapVirtualKeyW.restype = wintypes.UINT
USER32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
USER32.SetWindowPos.restype = wintypes.BOOL
LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
USER32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
USER32.CallWindowProcW.restype = LRESULT
USER32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
USER32.SetWindowLongPtrW.restype = LRESULT
USER32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
USER32.SendMessageW.restype = LRESULT
USER32.RedrawWindow.argtypes = [wintypes.HWND, ctypes.c_void_p, wintypes.HWND, wintypes.UINT]
USER32.RedrawWindow.restype = wintypes.BOOL
USER32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
USER32.FindWindowW.restype = wintypes.HWND
USER32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
USER32.ShowWindow.restype = wintypes.BOOL
USER32.SetForegroundWindow.argtypes = [wintypes.HWND]
USER32.SetForegroundWindow.restype = wintypes.BOOL
KERNEL32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
KERNEL32.CreateMutexW.restype = wintypes.HANDLE
KERNEL32.CloseHandle.argtypes = [wintypes.HANDLE]
KERNEL32.CloseHandle.restype = wintypes.BOOL
SW_RESTORE = 9
ERROR_ALREADY_EXISTS = 183


def acquire_mutex(name: str) -> int | None:
    """获取命名互斥体；已有实例持有同名互斥体时返回 None。

    返回的句柄须由调用方保持存活到进程退出，否则互斥体立即失效。
    """
    handle = KERNEL32.CreateMutexW(None, False, name)
    if not handle:
        return None
    if KERNEL32.GetLastError() == ERROR_ALREADY_EXISTS:
        KERNEL32.CloseHandle(handle)
        return None
    return handle


def activate_window_by_title(title: str) -> bool:
    """把已运行实例的主窗口还原并置于前台（用于二次启动时唤醒）。"""
    hwnd = USER32.FindWindowW(None, title)
    if not hwnd:
        return False
    USER32.ShowWindow(hwnd, SW_RESTORE)
    USER32.SetForegroundWindow(hwnd)
    return True


def enable_per_monitor_dpi_awareness() -> None:
    """按系统能力从高到低启用 DPI 感知，逐级回退。

    设置失败（清单已声明、组策略限制等）时若不检查返回值，进程会停留
    在 DPI unaware：高 DPI 下界面被系统拉伸模糊，坐标也会整体偏移。
    顺序：Per-Monitor V2（Win10 1703+）→ Per-Monitor（Win 8.1）→ 系统级。
    """
    try:
        if USER32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except (AttributeError, OSError):
        pass
    try:
        shcore = ctypes.WinDLL("shcore")
        shcore.SetProcessDpiAwareness.argtypes = [ctypes.c_int]
        shcore.SetProcessDpiAwareness.restype = ctypes.HRESULT
        if shcore.SetProcessDpiAwareness(2) == 0:  # S_OK
            return
    except (AttributeError, OSError):
        pass
    try:
        USER32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


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
    """把文字写入剪贴板后发送 Ctrl+V 粘贴，中英文通用。

    剪贴板常被输入法、聊天工具等短暂占用，打开失败时短暂重试；
    始终无法打开则明确报错，而不是静默粘贴出上一次复制的内容。
    """
    if not text:
        return
    if not _set_clipboard_text(text):
        raise OSError("剪贴板被其他程序占用，文字写入失败")
    time.sleep(0.05)
    ctrl, vk = 0x11, 0x56
    for code, up in ((ctrl, False), (vk, False), (vk, True), (ctrl, True)):
        send_key(code, key_up=up)
        time.sleep(0.02)


def _set_clipboard_text(text: str) -> bool:
    """写入 CF_UNICODETEXT，成功后内存归剪贴板所有；任何失败路径都自行释放。"""
    data = ctypes.create_unicode_buffer(text)
    for _attempt in range(5):
        if not USER32.OpenClipboard(None):
            time.sleep(0.02)
            continue
        try:
            USER32.EmptyClipboard()
            handle = KERNEL32.GlobalAlloc(GMEM_MOVEABLE, ctypes.sizeof(data))
            if not handle:
                return False
            locked = KERNEL32.GlobalLock(handle)
            if not locked:
                KERNEL32.GlobalFree(handle)
                return False
            ctypes.memmove(locked, data, ctypes.sizeof(data))
            KERNEL32.GlobalUnlock(handle)
            if not USER32.SetClipboardData(CF_UNICODETEXT, handle):
                KERNEL32.GlobalFree(handle)  # 所有权未转移给剪贴板，需自己释放
                return False
            return True
        finally:
            USER32.CloseClipboard()
    return False


def _system_dpi_scale() -> float:
    try:
        USER32.GetDpiForSystem.restype = wintypes.UINT
        return max(1.0, USER32.GetDpiForSystem() / 96)
    except (AttributeError, OSError):
        return 1.0


SCALE = _system_dpi_scale()
