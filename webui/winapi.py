# -*- coding: utf-8 -*-
"""Windows 原生调用底座（ctypes，只用标准库）。

为什么有这个文件：老的桌面控制是「Python 拼一段 PowerShell 脚本 → 起一个
powershell.exe → 解析它打印的 OK|/ERR| 一行」，每次调用光进程启动就 0.46 秒，
一次 UIA 快照 0.88 秒、一次查找 3 秒，多步任务全靠模型来回等。

这一层改成 **进程内 ctypes 直调 user32/gdi32/kernel32/shell32/advapi32**：
窗口枚举、屏幕抓取、鼠标键盘、剪贴板、进程、注册表、启动程序，全都不再起进程，
单次调用是微秒到毫秒级。只有一个例外是 UIA（无障碍树）——那块仍然需要一个常驻的
PowerShell worker，见 webui\\uia.py。

坐标约定：整个项目统一用 **屏幕物理像素（虚拟桌面坐标系）**。导入时就把当前进程
设成 DPI 感知（Win7 用 SetProcessDPIAware，Win8.1+ 再尝试 PerMonitorV2），避免
系统替我们做 96 DPI 缩放导致「看到的坐标」和「点到的位置」对不上。
"""

import ctypes
import os
import subprocess
import time
from ctypes import wintypes

# ---------------------------------------------------------------- 动态库

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

try:
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
except OSError:  # pragma: no cover - Win7 一定在
    psapi = None


class WinApiError(RuntimeError):
    """原生调用失败。"""

    def __init__(self, message, code=None):
        RuntimeError.__init__(self, message)
        self.code = code


def _kind(name):
    """64 位下 ULONG_PTR / LONG_PTR 是 8 字节。"""
    return ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


ULONG_PTR = _kind("ptr")
LRESULT = _kind("lresult")


def _error(what):
    code = ctypes.get_last_error()
    return WinApiError("%s 失败（Win32 错误 %d：%s）" % (what, code, _message(code)), code)


def _message(code):
    try:
        return ctypes.FormatError(code).strip()
    except Exception:  # pragma: no cover - 极少数码没有文案
        return "未知错误"


# ---------------------------------------------------------------- 常量

SW_HIDE = 0
SW_SHOWNORMAL = 1
SW_SHOWMINIMIZED = 2
SW_SHOWMAXIMIZED = 3
SW_RESTORE = 9

GW_OWNER = 4
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
SM_CXSCREEN = 0
SM_CYSCREEN = 1
SM_CXDOUBLECLK = 36
SM_CYDOUBLECLK = 37
SM_MOUSEPRESENT = 19

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000

WHEEL_DELTA = 120

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

SRCCOPY = 0x00CC0020
CAPTUREBLT = 0x40000000
DIB_RGB_COLORS = 0

TH32CS_SNAPPROCESS = 0x00000002
PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010

KEY_READ = 0x20019
KEY_WRITE = 0x20006
KEY_ALL_ACCESS = 0xF003F
KEY_WOW64_64KEY = 0x0100
KEY_WOW64_32KEY = 0x0200

HKEY_CLASSES_ROOT = 0x80000000
HKEY_CURRENT_USER = 0x80000001
HKEY_LOCAL_MACHINE = 0x80000002
HKEY_USERS = 0x80000003

REG_SZ = 1
REG_EXPAND_SZ = 2
REG_BINARY = 3
REG_DWORD = 4
REG_MULTI_SZ = 7
REG_QWORD = 11

SEE_MASK_NOCLOSEPROCESS = 0x00000040
SEE_MASK_FLAG_NO_UI = 0x00000400
SW_SHOWNOACTIVATE = 4

MONITOR_DEFAULTTONEAREST = 2

# ---------------------------------------------------------------- 结构体


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]

    @property
    def width(self):
        return int(self.right - self.left)

    @property
    def height(self):
        return int(self.bottom - self.top)

    def box(self):
        return (int(self.left), int(self.top), int(self.right), int(self.bottom))


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ULONG_PTR),
                ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]


class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]


class SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong), ("hwnd", wintypes.HWND),
                ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR),
                ("lpParameters", wintypes.LPCWSTR), ("lpDirectory", wintypes.LPCWSTR),
                ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
                ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR),
                ("hkeyClass", wintypes.HKEY), ("dwHotKey", wintypes.DWORD),
                ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT), ("rcWork", RECT),
                ("dwFlags", wintypes.DWORD), ("szDevice", wintypes.WCHAR * 32)]


# ---------------------------------------------------------------- 函数签名

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
MONITORENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                                     ctypes.POINTER(RECT), wintypes.LPARAM)

user32.EnumWindows.argtypes = (WNDENUMPROC, wintypes.LPARAM)
user32.EnumWindows.restype = wintypes.BOOL
user32.IsWindow.argtypes = (wintypes.HWND,)
user32.IsWindow.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = (wintypes.HWND,)
user32.IsWindowVisible.restype = wintypes.BOOL
user32.IsIconic.argtypes = (wintypes.HWND,)
user32.IsIconic.restype = wintypes.BOOL
user32.IsZoomed.argtypes = (wintypes.HWND,)
user32.IsZoomed.restype = wintypes.BOOL
user32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
user32.GetClassNameW.restype = ctypes.c_int
user32.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(RECT))
user32.GetWindowRect.restype = wintypes.BOOL
user32.GetClientRect.argtypes = (wintypes.HWND, ctypes.POINTER(RECT))
user32.GetClientRect.restype = wintypes.BOOL
user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetForegroundWindow.argtypes = ()
user32.GetForegroundWindow.restype = wintypes.HWND
user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.BringWindowToTop.argtypes = (wintypes.HWND,)
user32.BringWindowToTop.restype = wintypes.BOOL
user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
user32.ShowWindow.restype = wintypes.BOOL
user32.SetWindowPos.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.UINT)
user32.SetWindowPos.restype = wintypes.BOOL
user32.MoveWindow.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                              ctypes.c_int, wintypes.BOOL)
user32.MoveWindow.restype = wintypes.BOOL
user32.GetWindow.argtypes = (wintypes.HWND, wintypes.UINT)
user32.GetWindow.restype = wintypes.HWND
user32.GetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int)
user32.GetWindowLongW.restype = wintypes.LONG
user32.SetCursorPos.argtypes = (ctypes.c_int, ctypes.c_int)
user32.SetCursorPos.restype = wintypes.BOOL
user32.GetCursorPos.argtypes = (ctypes.POINTER(POINT),)
user32.GetCursorPos.restype = wintypes.BOOL
user32.GetSystemMetrics.argtypes = (ctypes.c_int,)
user32.GetSystemMetrics.restype = ctypes.c_int
user32.GetDoubleClickTime.argtypes = ()
user32.GetDoubleClickTime.restype = wintypes.UINT
user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wintypes.UINT
user32.GetDC.argtypes = (wintypes.HWND,)
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = (wintypes.HWND, wintypes.HDC)
user32.ReleaseDC.restype = ctypes.c_int
user32.AttachThreadInput.argtypes = (wintypes.DWORD, wintypes.DWORD, wintypes.BOOL)
user32.AttachThreadInput.restype = wintypes.BOOL
user32.OpenClipboard.argtypes = (wintypes.HWND,)
user32.OpenClipboard.restype = wintypes.BOOL
user32.CloseClipboard.argtypes = ()
user32.CloseClipboard.restype = wintypes.BOOL
user32.EmptyClipboard.argtypes = ()
user32.EmptyClipboard.restype = wintypes.BOOL
user32.GetClipboardData.argtypes = (wintypes.UINT,)
user32.GetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.argtypes = (wintypes.UINT, wintypes.HANDLE)
user32.SetClipboardData.restype = wintypes.HANDLE
user32.IsClipboardFormatAvailable.argtypes = (wintypes.UINT,)
user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
user32.EnumDisplayMonitors.argtypes = (wintypes.HDC, ctypes.POINTER(RECT), MONITORENUMPROC, wintypes.LPARAM)
user32.EnumDisplayMonitors.restype = wintypes.BOOL
user32.GetMonitorInfoW.argtypes = (wintypes.HMONITOR, ctypes.POINTER(MONITORINFOEXW))
user32.GetMonitorInfoW.restype = wintypes.BOOL
try:
    user32.SetProcessDPIAware.argtypes = ()
    user32.SetProcessDPIAware.restype = wintypes.BOOL
except AttributeError:  # pragma: no cover
    pass
try:
    user32.SetProcessDpiAwarenessContext.argtypes = (ctypes.c_void_p,)
    user32.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
except AttributeError:  # pragma: no cover - Win7 没有
    pass

kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
kernel32.TerminateProcess.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
kernel32.Process32FirstW.argtypes = (wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W))
kernel32.Process32FirstW.restype = wintypes.BOOL
kernel32.Process32NextW.argtypes = (wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W))
kernel32.Process32NextW.restype = wintypes.BOOL
kernel32.GlobalAlloc.argtypes = (wintypes.UINT, ctypes.c_size_t)
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalLock.argtypes = (wintypes.HGLOBAL,)
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = (wintypes.HGLOBAL,)
kernel32.GlobalUnlock.restype = wintypes.BOOL
kernel32.GlobalFree.argtypes = (wintypes.HGLOBAL,)
kernel32.GlobalFree.restype = wintypes.HGLOBAL
kernel32.GetCurrentThreadId.argtypes = ()
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
kernel32.WaitForSingleObject.restype = wintypes.DWORD
kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
kernel32.GetExitCodeProcess.restype = wintypes.BOOL

gdi32.CreateCompatibleDC.argtypes = (wintypes.HDC,)
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleBitmap.argtypes = (wintypes.HDC, ctypes.c_int, ctypes.c_int)
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = (wintypes.HDC, wintypes.HGDIOBJ)
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.DeleteObject.argtypes = (wintypes.HGDIOBJ,)
gdi32.DeleteObject.restype = wintypes.BOOL
gdi32.DeleteDC.argtypes = (wintypes.HDC,)
gdi32.DeleteDC.restype = wintypes.BOOL
gdi32.BitBlt.argtypes = (wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD)
gdi32.BitBlt.restype = wintypes.BOOL
gdi32.GetDIBits.argtypes = (wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                            ctypes.c_void_p, ctypes.POINTER(BITMAPINFO), wintypes.UINT)
gdi32.GetDIBits.restype = ctypes.c_int
gdi32.StretchBlt.argtypes = (wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                             wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                             wintypes.DWORD)
gdi32.StretchBlt.restype = wintypes.BOOL
gdi32.SetStretchBltMode.argtypes = (wintypes.HDC, ctypes.c_int)
gdi32.SetStretchBltMode.restype = ctypes.c_int
gdi32.SetBrushOrgEx.argtypes = (wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_void_p)
gdi32.SetBrushOrgEx.restype = wintypes.BOOL

shell32.ShellExecuteExW.argtypes = (ctypes.POINTER(SHELLEXECUTEINFOW),)
shell32.ShellExecuteExW.restype = wintypes.BOOL
if hasattr(shell32, "IsUserAnAdmin"):
    shell32.IsUserAnAdmin.argtypes = ()
    shell32.IsUserAnAdmin.restype = wintypes.BOOL

advapi32.RegOpenKeyExW.argtypes = (wintypes.HKEY, wintypes.LPCWSTR, wintypes.DWORD,
                                   wintypes.DWORD, ctypes.POINTER(wintypes.HKEY))
advapi32.RegOpenKeyExW.restype = wintypes.LONG
advapi32.RegCloseKey.argtypes = (wintypes.HKEY,)
advapi32.RegCloseKey.restype = wintypes.LONG
advapi32.RegQueryValueExW.argtypes = (wintypes.HKEY, wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD),
                                      ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
                                      ctypes.POINTER(wintypes.DWORD))
advapi32.RegQueryValueExW.restype = wintypes.LONG
advapi32.RegSetValueExW.argtypes = (wintypes.HKEY, wintypes.LPCWSTR, wintypes.DWORD,
                                    wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD)
advapi32.RegSetValueExW.restype = wintypes.LONG
advapi32.RegDeleteValueW.argtypes = (wintypes.HKEY, wintypes.LPCWSTR)
advapi32.RegDeleteValueW.restype = wintypes.LONG
advapi32.RegEnumValueW.argtypes = (wintypes.HKEY, wintypes.DWORD, wintypes.LPWSTR,
                                   ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
                                   ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
                                   ctypes.POINTER(wintypes.DWORD))
advapi32.RegEnumValueW.restype = wintypes.LONG

if psapi is not None:
    psapi.GetProcessMemoryInfo.argtypes = (wintypes.HANDLE, ctypes.POINTER(PROCESS_MEMORY_COUNTERS),
                                           wintypes.DWORD)
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL


# ---------------------------------------------------------------- DPI

def make_dpi_aware():
    """让本进程拿到物理像素坐标（幂等，失败不抛）。"""
    try:
        # Win10 1703+：PerMonitorV2，值为 -4
        if hasattr(user32, "SetProcessDpiAwarenessContext"):
            if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
                return "per-monitor-v2"
    except Exception:
        pass
    try:
        if user32.SetProcessDPIAware():
            return "system"
    except Exception:
        pass
    return "unaware"


DPI_MODE = make_dpi_aware()


# ---------------------------------------------------------------- 窗口

def window_title(hwnd):
    length = user32.GetWindowTextLengthW(hwnd)
    buffer = ctypes.create_unicode_buffer(length + 2)
    user32.GetWindowTextW(hwnd, buffer, length + 2)
    return buffer.value


def window_class(hwnd):
    buffer = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buffer, 256)
    return buffer.value


def window_pid(hwnd):
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def window_rect(hwnd):
    rect = RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise _error("读取窗口位置")
    return rect.box()


def client_rect(hwnd):
    rect = RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        raise _error("读取窗口客户区")
    point = POINT(0, 0)
    user32.ClientToScreen.argtypes = (wintypes.HWND, ctypes.POINTER(POINT))
    user32.ClientToScreen.restype = wintypes.BOOL
    user32.ClientToScreen(hwnd, ctypes.byref(point))
    return (int(point.x), int(point.y), int(point.x + rect.right), int(point.y + rect.bottom))


def window_info(hwnd):
    left, top, right, bottom = window_rect(hwnd)
    return {
        "handle": int(hwnd),
        "title": window_title(hwnd),
        "class_name": window_class(hwnd),
        "pid": window_pid(hwnd),
        "rect": (left, top, right, bottom),
        "width": right - left,
        "height": bottom - top,
        "minimized": bool(user32.IsIconic(hwnd)),
        "maximized": bool(user32.IsZoomed(hwnd)),
        "visible": bool(user32.IsWindowVisible(hwnd)),
        "foreground": int(user32.GetForegroundWindow() or 0) == int(hwnd),
        "owner": int(user32.GetWindow(hwnd, GW_OWNER) or 0),
    }


def list_windows(visible_only=True, titled_only=True):
    """枚举顶层窗口，按 z 序（EnumWindows 的顺序就是前台到后台）。"""
    found = []

    @WNDENUMPROC
    def callback(hwnd, _param):
        try:
            visible = bool(user32.IsWindowVisible(hwnd))
            if visible_only and not visible:
                return True
            title = window_title(hwnd)
            if titled_only and not title:
                return True
            style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            if style & WS_EX_TOOLWINDOW and not style & WS_EX_APPWINDOW:
                return True
            found.append(window_info(hwnd))
        except Exception:
            return True
        return True

    if not user32.EnumWindows(callback, 0):
        raise _error("枚举窗口")
    return found


def find_windows(keyword, exact=False, limit=None):
    """按标题找窗口：先精确、再忽略大小写的子串，最后忽略标点的宽松匹配。"""
    keyword = (keyword or "").strip()
    if not keyword:
        return []
    windows = list_windows(visible_only=False, titled_only=True)
    if keyword.isdigit():
        handle = int(keyword)
        by_handle = [item for item in windows if item["handle"] == handle]
        if by_handle:
            return by_handle
    exact_hits = [item for item in windows if item["title"] == keyword]
    if exact:
        return exact_hits[:limit] if limit else exact_hits
    folded = keyword.casefold()
    loose = [item for item in windows if folded in item["title"].casefold()]
    hits = exact_hits or loose
    if not hits:
        compact = folded.replace(" ", "")
        hits = [item for item in windows if compact in item["title"].casefold().replace(" ", "")]
    return hits[:limit] if limit else hits


def resolve_window(name_or_handle, visible_only=False):
    """窗口标识（标题子串 / 句柄数字 / handle:123）→ hwnd，找不到给 (None, 原因)。"""
    if isinstance(name_or_handle, int):
        if user32.IsWindow(name_or_handle):
            return name_or_handle, ""
        return None, "句柄 %s 不是有效窗口。" % name_or_handle
    text = (name_or_handle or "").strip()
    if text.lower().startswith("handle:"):
        text = text.split(":", 1)[1].strip()
    if text.isdigit():
        handle = int(text)
        if user32.IsWindow(handle):
            return handle, ""
    if not text or text.lower() in ("desktop", "桌面", "全屏"):
        return None, ""
    hits = find_windows(text)
    if not hits:
        return None, "没有找到标题包含「%s」的窗口。" % text
    return hits[0]["handle"], ""


def foreground_window():
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    try:
        return window_info(hwnd)
    except Exception:
        return {"handle": int(hwnd), "title": window_title(hwnd)}


def bring_to_front(hwnd):
    """把窗口拿到前台。前台锁（foreground lock）靠 AttachThreadInput 绕过。

    返回 (是否成功, 说明)；成功与否以「1 秒内 GetForegroundWindow 真的变了」为准。
    """
    if not user32.IsWindow(hwnd):
        return False, "窗口句柄无效。"
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
        time.sleep(0.08)
    target_thread = user32.GetWindowThreadProcessId(hwnd, None)
    current_thread = kernel32.GetCurrentThreadId()
    foreground = user32.GetForegroundWindow()
    foreground_thread = user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
    attached = []
    try:
        for thread in (foreground_thread, target_thread):
            if thread and thread != current_thread:
                if user32.AttachThreadInput(current_thread, thread, True):
                    attached.append(thread)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040)
    finally:
        for thread in attached:
            user32.AttachThreadInput(current_thread, thread, False)
    deadline = time.time() + 1.0
    while time.time() < deadline:
        if int(user32.GetForegroundWindow() or 0) == int(hwnd):
            return True, ""
        time.sleep(0.05)
    return False, "1 秒内没能把窗口拿到前台。"


def show_window(hwnd, action):
    mapping = {"normal": SW_SHOWNORMAL, "minimize": SW_SHOWMINIMIZED, "minimized": SW_SHOWMINIMIZED,
               "maximize": SW_SHOWMAXIMIZED, "maximized": SW_SHOWMAXIMIZED, "restore": SW_RESTORE,
               "hide": SW_HIDE, "show": SW_SHOWNORMAL}
    code = mapping.get((action or "").lower())
    if code is None:
        return False, "不认识的窗口动作：%s" % action
    if not user32.ShowWindow(hwnd, code):
        return False, _message(ctypes.get_last_error())
    return True, ""


def move_window(hwnd, x, y, width, height):
    if not user32.MoveWindow(hwnd, int(x), int(y), int(width), int(height), True):
        return False, _message(ctypes.get_last_error())
    return True, ""


def close_window(hwnd):
    WM_CLOSE = 0x0010
    return bool(user32.PostMessageW(hwnd, WM_CLOSE, 0, 0))


# ---------------------------------------------------------------- 屏幕

def screen_size():
    """主屏尺寸（Windows-MCP 用它归一化 mouse_event，我们只做展示）。"""
    return (int(user32.GetSystemMetrics(SM_CXSCREEN)), int(user32.GetSystemMetrics(SM_CYSCREEN)))


def virtual_screen():
    return (int(user32.GetSystemMetrics(SM_XVIRTUALSCREEN)),
            int(user32.GetSystemMetrics(SM_YVIRTUALSCREEN)),
            int(user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)),
            int(user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)))


def cursor_position():
    point = POINT()
    if not user32.GetCursorPos(ctypes.byref(point)):
        raise _error("读取光标位置")
    return (int(point.x), int(point.y))


def displays():
    """显示器清单（虚拟桌面物理像素）。"""
    result = []

    @MONITORENUMPROC
    def callback(monitor, _dc, _rect, _param):
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            result.append({
                "index": len(result),
                "device": info.szDevice,
                "rect": info.rcMonitor.box(),
                "work_rect": info.rcWork.box(),
                "primary": bool(info.dwFlags & 1),
                "width": info.rcMonitor.width,
                "height": info.rcMonitor.height,
            })
        return True

    if not user32.EnumDisplayMonitors(None, None, callback, 0):
        raise _error("枚举显示器")
    result.sort(key=lambda item: (not item["primary"], item["rect"][0], item["rect"][1]))
    for position, item in enumerate(result):
        item["index"] = position
    return result


def capture_raw(region=None, scale=1.0, capture_layered=False, max_size=None):
    """抓屏，返回 (width, height, BGRA 字节, 区域)。

    region 是虚拟桌面物理像素的 (left, top, right, bottom)；不给就抓整个虚拟桌面。
    scale ∈ (0,1] 时用 StretchBlt 缩图（GDI 里做，比 Python 循环快两个数量级）；
    max_size 形如 (1920, 1080)，超过就按比例再压一次。
    """
    virtual = virtual_screen()
    if region is None:
        left, top, width, height = virtual
    else:
        left, top, right, bottom = [int(value) for value in region]
        if right <= left or bottom <= top:
            raise WinApiError("抓屏区域不合法：%r" % (region,))
        width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        raise WinApiError("屏幕尺寸异常：%dx%d" % (width, height))

    try:
        factor = float(scale)
    except (TypeError, ValueError):
        factor = 1.0
    factor = max(0.1, min(1.0, factor))
    if max_size:
        limit_width, limit_height = [int(value) for value in max_size]
        if limit_width > 0 and limit_height > 0:
            factor = min(factor, limit_width / float(width), limit_height / float(height))
    target_width = max(1, int(round(width * factor)))
    target_height = max(1, int(round(height * factor)))

    screen_dc = user32.GetDC(None)
    if not screen_dc:
        raise _error("获取屏幕 DC")
    memory_dc = gdi32.CreateCompatibleDC(screen_dc)
    bitmap = gdi32.CreateCompatibleBitmap(screen_dc, target_width, target_height)
    if not memory_dc or not bitmap:
        user32.ReleaseDC(None, screen_dc)
        raise _error("创建抓屏位图")
    previous = None
    try:
        previous = gdi32.SelectObject(memory_dc, bitmap)
        flags = SRCCOPY | (CAPTUREBLT if capture_layered else 0)
        if target_width == width and target_height == height:
            if not gdi32.BitBlt(memory_dc, 0, 0, width, height, screen_dc, left, top, flags):
                raise _error("BitBlt 抓屏")
        else:
            gdi32.SetStretchBltMode(memory_dc, 4)  # HALFTONE：缩小时做平均，别出摩尔纹
            gdi32.SetBrushOrgEx(memory_dc, 0, 0, None)
            if not gdi32.StretchBlt(memory_dc, 0, 0, target_width, target_height,
                                    screen_dc, left, top, width, height, flags):
                raise _error("StretchBlt 抓屏")
        info = BITMAPINFO()
        header = info.bmiHeader
        header.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        header.biWidth = target_width
        header.biHeight = -target_height  # 负数 = 自上而下
        header.biPlanes = 1
        header.biBitCount = 32
        header.biCompression = 0
        buffer = ctypes.create_string_buffer(target_width * target_height * 4)
        copied = gdi32.GetDIBits(memory_dc, bitmap, 0, target_height, buffer,
                                 ctypes.byref(info), DIB_RGB_COLORS)
        if copied != target_height:
            raise _error("GetDIBits 取像素（拿到 %s 行）" % copied)
        return target_width, target_height, buffer.raw, (left, top, left + width, top + height)
    finally:
        if previous:
            gdi32.SelectObject(memory_dc, previous)
        if bitmap:
            gdi32.DeleteObject(bitmap)
        if memory_dc:
            gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(None, screen_dc)


# ---------------------------------------------------------------- 鼠标键盘

def _send_inputs(inputs):
    count = len(inputs)
    array = (INPUT * count)(*inputs)
    sent = user32.SendInput(count, array, ctypes.sizeof(INPUT))
    if sent != count:
        raise _error("SendInput（发出 %d/%d 个事件）" % (sent, count))
    return sent


def _mouse_input(flags, dx=0, dy=0, data=0):
    item = INPUT()
    item.type = INPUT_MOUSE
    item.mi = MOUSEINPUT(int(dx), int(dy), int(data), int(flags), 0, 0)
    return item


def _key_input(vk=0, scan=0, flags=0):
    item = INPUT()
    item.type = INPUT_KEYBOARD
    item.ki = KEYBDINPUT(int(vk), int(scan), int(flags), 0, 0)
    return item


def move_mouse(x, y, duration=None, steps=None):
    """移动光标；给了 duration 就插值走过去（拖动时更好用）。"""
    x, y = int(x), int(y)
    if not duration:
        if not user32.SetCursorPos(x, y):
            raise _error("移动光标")
        return
    duration = max(0.0, min(10.0, float(duration)))
    start = cursor_position()
    if steps is None:
        steps = max(2, min(200, int(duration / 0.01) or 2))
    for index in range(1, steps + 1):
        ratio = index / float(steps)
        user32.SetCursorPos(int(round(start[0] + (x - start[0]) * ratio)),
                            int(round(start[1] + (y - start[1]) * ratio)))
        time.sleep(duration / steps)
    user32.SetCursorPos(x, y)


def click(x, y, button="left", clicks=1):
    """在 (x,y) 点鼠标。clicks=0 只移动不点（hover）。"""
    x, y = int(x), int(y)
    if clicks is None or int(clicks) <= 0:
        move_mouse(x, y)
        return
    clicks = int(clicks)
    down, up = {
        "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
        "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
        "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
    }.get(button, (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP))
    if not user32.SetCursorPos(x, y):
        raise _error("移动光标")
    time.sleep(0.02)
    if clicks > 1:
        gap = max(0.05, user32.GetDoubleClickTime() / 2000.0)
        for index in range(clicks):
            _send_inputs([_mouse_input(down), _mouse_input(up)])
            if index < clicks - 1:
                time.sleep(gap)
    else:
        _send_inputs([_mouse_input(down), _mouse_input(up)])


def mouse_down(button="left", x=None, y=None):
    """只按下不抬（配合 mouse_up 做拖拽 / 连续笔画；draw_path 要用）。"""
    if x is not None and y is not None:
        move_mouse(x, y)
        time.sleep(0.02)
    down = {"left": MOUSEEVENTF_LEFTDOWN, "right": MOUSEEVENTF_RIGHTDOWN,
            "middle": MOUSEEVENTF_MIDDLEDOWN}.get(button, MOUSEEVENTF_LEFTDOWN)
    _send_inputs([_mouse_input(down)])


def mouse_up(button="left", x=None, y=None):
    """松开鼠标键（配合 mouse_down）。"""
    if x is not None and y is not None:
        user32.SetCursorPos(int(x), int(y))
        time.sleep(0.02)
    up = {"left": MOUSEEVENTF_LEFTUP, "right": MOUSEEVENTF_RIGHTUP,
          "middle": MOUSEEVENTF_MIDDLEUP}.get(button, MOUSEEVENTF_LEFTUP)
    _send_inputs([_mouse_input(up)])


def scroll(x, y, direction="down", wheel_times=1, horizontal=False):
    x, y = int(x), int(y)
    times = max(1, int(wheel_times or 1))
    if horizontal:
        if direction not in ("left", "right"):
            raise WinApiError('横向滚动方向只能是 "left" 或 "right"。')
        flag = MOUSEEVENTF_HWHEEL
        delta = -WHEEL_DELTA if direction == "left" else WHEEL_DELTA
    else:
        if direction not in ("up", "down"):
            raise WinApiError('纵向滚动方向只能是 "up" 或 "down"。')
        flag = MOUSEEVENTF_WHEEL
        delta = WHEEL_DELTA if direction == "up" else -WHEEL_DELTA
    if x >= 0 and y >= 0:
        user32.SetCursorPos(x, y)
        time.sleep(0.02)
    for _ in range(times):
        _send_inputs([_mouse_input(flag, data=delta)])
        time.sleep(0.03)


def drag(from_x, from_y, to_x, to_y, duration=None, button="left"):
    down, up = {
        "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
        "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
        "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
    }.get(button, (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP))
    move_mouse(from_x, from_y)
    time.sleep(0.08)
    _send_inputs([_mouse_input(down)])
    try:
        move_mouse(to_x, to_y, duration=duration if duration else 0.35)
    finally:
        _send_inputs([_mouse_input(up)])
    time.sleep(0.05)


# 键名 → 虚拟键码
VK_CODES = {
    "backspace": 0x08, "tab": 0x09, "clear": 0x0C, "enter": 0x0D, "return": 0x0D,
    "shift": 0x10, "ctrl": 0x11, "control": 0x11, "alt": 0x12, "pause": 0x13,
    "capslock": 0x14, "esc": 0x1B, "escape": 0x1B, "space": 0x20, "pageup": 0x21,
    "pagedown": 0x22, "end": 0x23, "home": 0x24, "left": 0x25, "up": 0x26,
    "right": 0x27, "down": 0x28, "printscreen": 0x2C, "insert": 0x2D, "delete": 0x2E,
    "del": 0x2E, "help": 0x2F, "win": 0x5B, "windows": 0x5B, "apps": 0x5D,
    "num0": 0x60, "num1": 0x61, "num2": 0x62, "num3": 0x63, "num4": 0x64, "num5": 0x65,
    "num6": 0x66, "num7": 0x67, "num8": 0x68, "num9": 0x69, "multiply": 0x6A,
    "add": 0x6B, "separator": 0x6C, "subtract": 0x6D, "decimal": 0x6E, "divide": 0x6F,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74, "f6": 0x75,
    "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
    "numlock": 0x90, "scrolllock": 0x91, "semicolon": 0xBA, "equals": 0xBB,
    "comma": 0xBC, "minus": 0xBD, "period": 0xBE, "slash": 0xBF, "grave": 0xC0,
    "bracketleft": 0xDB, "backslash": 0xDC, "bracketright": 0xDD, "quote": 0xDE,
    "cmd": 0x5B, "command": 0x5B, "option": 0x12,
    # 多媒体键（旧的 media_key 工具并进了 Shortcut，按名字直接用）
    "volume_mute": 0xAD, "mute": 0xAD, "volume_down": 0xAE, "volume_up": 0xAF,
    "prev": 0xB1, "prev_track": 0xB1, "media_prev": 0xB1,
    "next": 0xB0, "next_track": 0xB0, "media_next": 0xB0,
    "stop": 0xB2, "media_stop": 0xB2,
    "play_pause": 0xB3, "media_play_pause": 0xB3, "playpause": 0xB3,
}
EXTENDED_VK = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x5B, 0x5C, 0x5D, 0x6F}
ALIASES = {"escape": "esc", "return": "enter", "control": "ctrl", "del": "delete",
           "windows": "win", "command": "win", "cmd": "win"}


def key_code(name):
    """键名 → (虚拟键码, 是否扩展键)；不认识的键返回 (None, False)。"""
    key = (name or "").strip().lower()
    key = ALIASES.get(key, key)
    if key in VK_CODES:
        code = VK_CODES[key]
        return code, code in EXTENDED_VK
    if len(key) == 1:
        char = key.upper()
        if "A" <= char <= "Z" or "0" <= char <= "9":
            return ord(char), False
        # 常见符号走 VkKeyScanW 拿「哪个键 + 需不需要 Shift」
        scan = user32.VkKeyScanW(ctypes.c_wchar(char))
        if scan != -1:
            return scan & 0xFF, False
    return None, False


def press_keys(combo):
    """按一个组合键，如 "ctrl+s" / "alt+tab" / "enter" / "win+r"。"""
    parts = [part for part in str(combo or "").replace(" ", "").split("+") if part]
    if not parts:
        raise WinApiError("没有给出按键。")
    pressed = []
    shift_needed = False
    try:
        for part in parts[:-1]:
            code, extended = key_code(part)
            if code is None:
                raise WinApiError("不认识的组合键：%s" % combo)
            flags = KEYEVENTF_EXTENDEDKEY if extended else 0
            _send_inputs([_key_input(vk=code, flags=flags)])
            pressed.append((code, flags))
            if code == 0x10:
                shift_needed = True
        final = parts[-1]
        lower = final.lower()
        upper = lower.upper()
        if len(lower) == 1 and "A" <= upper <= "Z" and not shift_needed:
            _send_inputs([_key_input(vk=ord(upper), flags=0), _key_input(vk=ord(upper), flags=KEYEVENTF_KEYUP)])
            return
        code, extended = key_code(final)
        if code is None:
            raise WinApiError("不认识的组合键：%s" % combo)
        flags = KEYEVENTF_EXTENDEDKEY if extended else 0
        _send_inputs([_key_input(vk=code, flags=flags), _key_input(vk=code, flags=flags | KEYEVENTF_KEYUP)])
    finally:
        for code, flags in reversed(pressed):
            _send_inputs([_key_input(vk=code, flags=flags | KEYEVENTF_KEYUP)])


def key_down(name):
    """按住一个键（MultiSelect 要按住 Ctrl 连点，press_keys 只能一整组按完就放）。"""
    code, extended = key_code(name)
    if code is None:
        raise WinApiError("不认识的键：%s" % name)
    _send_inputs([_key_input(vk=code, flags=KEYEVENTF_EXTENDEDKEY if extended else 0)])


def key_up(name):
    """松开一个键（配合 key_down）。"""
    code, extended = key_code(name)
    if code is None:
        raise WinApiError("不认识的键：%s" % name)
    flags = KEYEVENTF_EXTENDEDKEY if extended else 0
    _send_inputs([_key_input(vk=code, flags=flags | KEYEVENTF_KEYUP)])


def type_text(text, delay=0.0):
    """把文本当 Unicode 逐字打进去（不受键盘布局影响，支持中文）。"""
    data = str(text if text is not None else "")
    batch = []
    for char in data:
        if char == "\n":
            batch.append(_key_input(vk=VK_CODES["enter"]))
            batch.append(_key_input(vk=VK_CODES["enter"], flags=KEYEVENTF_KEYUP))
            continue
        if char == "\t":
            batch.append(_key_input(vk=VK_CODES["tab"]))
            batch.append(_key_input(vk=VK_CODES["tab"], flags=KEYEVENTF_KEYUP))
            continue
        if char == "\r":
            continue
        code = ord(char)
        batch.append(_key_input(scan=code, flags=KEYEVENTF_UNICODE))
        batch.append(_key_input(scan=code, flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
    if not batch:
        return 0
    sent = 0
    for start in range(0, len(batch), 64):
        chunk = batch[start:start + 64]
        sent += _send_inputs(chunk)
        if delay:
            time.sleep(delay)
    return sent


# ---------------------------------------------------------------- 剪贴板

def clipboard_get_text():
    if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
        return None
    for _ in range(10):
        if user32.OpenClipboard(None):
            break
        time.sleep(0.03)
    else:
        raise WinApiError("剪贴板被别的程序占着，打不开。")
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            raise _error("读取剪贴板")
        try:
            return ctypes.c_wchar_p(pointer).value or ""
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def clipboard_set_text(text):
    data = str(text if text is not None else "")
    payload = data.encode("utf-16-le") + b"\x00\x00"
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(payload))
    if not handle:
        raise _error("分配剪贴板内存")
    pointer = kernel32.GlobalLock(handle)
    if not pointer:
        kernel32.GlobalFree(handle)
        raise _error("锁定剪贴板内存")
    ctypes.memmove(pointer, payload, len(payload))
    kernel32.GlobalUnlock(handle)
    for _ in range(10):
        if user32.OpenClipboard(None):
            break
        time.sleep(0.03)
    else:
        kernel32.GlobalFree(handle)
        raise WinApiError("剪贴板被别的程序占着，打不开。")
    try:
        user32.EmptyClipboard()
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)
            raise _error("写入剪贴板")
        handle = None  # 所有权交给系统
        return True
    finally:
        user32.CloseClipboard()


# ---------------------------------------------------------------- 进程

def list_processes(sort_by="memory", limit=20):
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == wintypes.HANDLE(-1).value or not snapshot:
        raise _error("枚举进程")
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
    items = []
    try:
        if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            raise _error("读取第一个进程")
        while True:
            items.append({"pid": int(entry.th32ProcessID), "name": entry.szExeFile,
                          "threads": int(entry.cntThreads),
                          "parent_pid": int(entry.th32ParentProcessID),
                          "memory": _process_memory(int(entry.th32ProcessID))})
            if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                break
    finally:
        kernel32.CloseHandle(snapshot)
    keys = {"memory": lambda item: item["memory"] or 0,
            "pid": lambda item: item["pid"],
            "name": lambda item: (item["name"] or "").lower(),
            "threads": lambda item: item["threads"]}
    items.sort(key=keys.get(sort_by, keys["memory"]), reverse=(sort_by != "name"))
    if limit:
        items = items[:int(limit)]
    return items


def _process_memory(pid):
    if psapi is None:
        return None
    handle = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not handle:
        return None
    try:
        counters = PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
        if psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            return int(counters.WorkingSetSize)
        return None
    finally:
        kernel32.CloseHandle(handle)


def kill_process(pid, force=True):
    handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, int(pid))
    if not handle:
        return False, _message(ctypes.get_last_error())
    try:
        if not kernel32.TerminateProcess(handle, 1 if force else 0):
            return False, _message(ctypes.get_last_error())
        return True, ""
    finally:
        kernel32.CloseHandle(handle)


# ---------------------------------------------------------------- 注册表

def _root_key(text):
    name = (text or "").strip().upper()
    table = {"HKEY_CLASSES_ROOT": HKEY_CLASSES_ROOT, "HKCR": HKEY_CLASSES_ROOT,
             "HKEY_CURRENT_USER": HKEY_CURRENT_USER, "HKCU": HKEY_CURRENT_USER,
             "HKEY_LOCAL_MACHINE": HKEY_LOCAL_MACHINE, "HKLM": HKEY_LOCAL_MACHINE,
             "HKEY_USERS": HKEY_USERS, "HKU": HKEY_USERS}
    for prefix in sorted(table, key=len, reverse=True):
        if name == prefix:
            return table[prefix], ""
        if name.startswith(prefix + "\\"):
            return table[prefix], text.strip()[len(prefix) + 1:]
    return None, ""


def _open_key(path, access):
    root, rest = _root_key(path)
    if root is None:
        raise WinApiError("注册表路径要以 HKEY_CURRENT_USER / HKEY_LOCAL_MACHINE 等开头：%s" % path)
    handle = wintypes.HKEY()
    code = advapi32.RegOpenKeyExW(root, rest, 0, access, ctypes.byref(handle))
    if code:
        raise WinApiError("打开注册表项失败（错误 %d：%s）：%s" % (code, _message(code), path))
    return handle


def registry_get(path, name=None):
    handle = _open_key(path, KEY_READ)
    try:
        return _registry_read_value(handle, path, name)
    finally:
        advapi32.RegCloseKey(handle)


def _registry_read_value(handle, path, name):
    value_type = wintypes.DWORD()
    size = wintypes.DWORD()
    code = advapi32.RegQueryValueExW(handle, name, None, ctypes.byref(value_type), None, ctypes.byref(size))
    if code:
        raise WinApiError("读取注册表值失败（错误 %d：%s）：%s\\%s" % (code, _message(code), path, name))
    buffer = ctypes.create_string_buffer(int(size.value) + 8)
    code = advapi32.RegQueryValueExW(handle, name, None, ctypes.byref(value_type),
                                     buffer, ctypes.byref(size))
    if code:
        raise WinApiError("读取注册表值失败（错误 %d：%s）" % (code, _message(code)))
    raw = buffer.raw[:int(size.value)]
    if value_type.value in (REG_SZ, REG_EXPAND_SZ):
        return raw.decode("utf-16-le", "replace").rstrip("\x00")
    if value_type.value == REG_DWORD and len(raw) >= 4:
        return int.from_bytes(raw[:4], "little")
    if value_type.value == REG_QWORD and len(raw) >= 8:
        return int.from_bytes(raw[:8], "little")
    if value_type.value == REG_MULTI_SZ:
        text = raw.decode("utf-16-le", "replace")
        return [part for part in text.split("\x00") if part]
    return raw


def registry_set(path, name, value, value_type="string"):
    handle = _open_key(path, KEY_WRITE)
    try:
        if value_type in ("dword", "int", "number"):
            data = ctypes.c_ulong(int(value))
            code = advapi32.RegSetValueExW(handle, name, 0, REG_DWORD, ctypes.byref(data),
                                           ctypes.sizeof(data))
        elif value_type in ("qword", "int64"):
            data = ctypes.c_ulonglong(int(value))
            code = advapi32.RegSetValueExW(handle, name, 0, REG_QWORD, ctypes.byref(data),
                                           ctypes.sizeof(data))
        elif value_type in ("multi", "multi_sz", "list"):
            text = "\x00".join(str(part) for part in value) + "\x00\x00"
            payload = text.encode("utf-16-le")
            code = advapi32.RegSetValueExW(handle, name, 0, REG_MULTI_SZ, payload, len(payload))
        else:
            payload = (str(value) + "\x00").encode("utf-16-le")
            code = advapi32.RegSetValueExW(handle, name, 0, REG_SZ, payload, len(payload))
        if code:
            raise WinApiError("写注册表失败（错误 %d：%s）" % (code, _message(code)))
        return True
    finally:
        advapi32.RegCloseKey(handle)


def registry_delete(path, name=None):
    handle = _open_key(path, KEY_WRITE)
    try:
        code = advapi32.RegDeleteValueW(handle, name)
        if code:
            raise WinApiError("删除注册表值失败（错误 %d：%s）" % (code, _message(code)))
        return True
    finally:
        advapi32.RegCloseKey(handle)


def registry_list(path):
    handle = _open_key(path, KEY_READ)
    try:
        names = []
        index = 0
        name_buffer = ctypes.create_unicode_buffer(512)
        while True:
            length = wintypes.DWORD(512)
            code = advapi32.RegEnumValueW(handle, index, name_buffer, ctypes.byref(length),
                                          None, None, None, None)
            if code == 259:  # ERROR_NO_MORE_ITEMS
                break
            if code:
                raise WinApiError("枚举注册表值失败（错误 %d：%s）" % (code, _message(code)))
            names.append(name_buffer.value)
            index += 1
        result = {}
        for name in names:
            try:
                result[name or "(默认)"] = _registry_read_value(handle, path, name)
            except WinApiError as error:
                result[name or "(默认)"] = "<读不到：%s>" % error
        return result
    finally:
        advapi32.RegCloseKey(handle)


# ---------------------------------------------------------------- 启动程序

def launch_executable(executable, args=None, cwd=None, wait=False):
    """直接起一个 exe，返回 (pid, 说明)。"""
    path = os.path.abspath(os.path.expanduser(str(executable)))
    if not os.path.isfile(path):
        raise WinApiError("可执行文件不存在：%s" % path)
    if args:
        if isinstance(args, str):
            argv = [path] + [args]
        else:
            argv = [path] + [str(item) for item in args]
    else:
        argv = [path]
    if cwd and not os.path.isdir(cwd):
        raise WinApiError("工作目录不存在：%s" % cwd)
    flags = 0x08000000 if path.lower().endswith((".exe", ".com", ".bat", ".cmd")) else 0
    process = subprocess.Popen(argv, cwd=cwd or os.path.dirname(path), close_fds=True,
                               creationflags=flags)
    if wait:
        process.wait()
    return process.pid, ""


def shell_execute(target, args=None, cwd=None, verb="open", show=SW_SHOWNORMAL):
    """走 ShellExecuteEx：.lnk / shell:AppsFolder\\... / 文档 / URL 都能开。"""
    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(SHELLEXECUTEINFOW)
    info.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_FLAG_NO_UI
    info.lpVerb = verb
    info.lpFile = str(target)
    info.lpParameters = " ".join(str(item) for item in args) if args else None
    info.lpDirectory = cwd
    info.nShow = show
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        raise _error("启动 %s" % target)
    pid = None
    if info.hProcess:
        pid = int(info.hProcess)
        kernel32.CloseHandle(info.hProcess)
    return pid


def start_menu_apps():
    """开始菜单里的程序：显示名 → 可直接启动的 .lnk 路径。"""
    folders = []
    program_data = os.environ.get("ProgramData")
    app_data = os.environ.get("APPDATA")
    if program_data:
        folders.append(os.path.join(program_data, "Microsoft", "Windows", "Start Menu", "Programs"))
    if app_data:
        folders.append(os.path.join(app_data, "Microsoft", "Windows", "Start Menu", "Programs"))
    apps = {}
    for folder in folders:
        if not os.path.isdir(folder):
            continue
        for base, _dirs, files in os.walk(folder):
            for item in files:
                if not item.lower().endswith(".lnk"):
                    continue
                display = os.path.splitext(item)[0]
                apps.setdefault(display, os.path.join(base, item))
    return apps


def is_admin():
    try:
        return bool(shell32.IsUserAnAdmin())
    except Exception:
        return False


if __name__ == "__main__":  # 手工自检：python webui\winapi.py
    import sys
    for _stream in (sys.stdout, sys.stderr):  # 窗口标题里可能有 GBK 打不出的字符
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    print("DPI:", DPI_MODE, "管理员:", is_admin())
    print("主屏:", screen_size(), "虚拟屏:", virtual_screen())
    print("光标:", cursor_position())
    print("显示器:")
    for item in displays():
        print("   ", item)
    print("窗口（前 8 个）:")
    for item in list_windows()[:8]:
        print("   ", item["handle"], item["title"][:40], item["rect"], "pid=%s" % item["pid"])
    print("进程（按内存前 5）:")
    for item in list_processes(limit=5):
        print("   ", item["pid"], item["name"], item["memory"])
    start = time.time()
    width, height, raw, box = capture_raw()
    print("抓屏: %dx%d %.0f ms 区域=%s 字节=%d" % (width, height, (time.time() - start) * 1000, box, len(raw)))
