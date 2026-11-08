"""Windows integration for the verified MSIX application and monitor work areas."""
import ctypes as ct
import os
import subprocess
import winreg
from ctypes import wintypes as wt
from pathlib import Path

PACKAGE_FAMILY = "OpenAI.Codex_2p2nqsd0c76g0"
APP_ID = PACKAGE_FAMILY + "!App"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "CodexDeskPet"

user = ct.WinDLL("user32", use_last_error=True)
kernel = ct.WinDLL("kernel32", use_last_error=True)


def bind(dll, name, result, *args):
    function = getattr(dll, name)
    function.restype = result
    function.argtypes = args
    return function


class MonitorInfo(ct.Structure):
    _fields_ = [("size", wt.DWORD), ("monitor", wt.RECT), ("work", wt.RECT), ("flags", wt.DWORD)]


EnumProc = ct.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
enum_windows = bind(user, "EnumWindows", wt.BOOL, EnumProc, wt.LPARAM)
foreground = bind(user, "GetForegroundWindow", wt.HWND)
visible = bind(user, "IsWindowVisible", wt.BOOL, wt.HWND)
title_length = bind(user, "GetWindowTextLengthW", ct.c_int, wt.HWND)
thread_pid = bind(user, "GetWindowThreadProcessId", wt.DWORD, wt.HWND, ct.POINTER(wt.DWORD))
ancestor = bind(user, "GetAncestor", wt.HWND, wt.HWND, wt.UINT)
is_iconic = bind(user, "IsIconic", wt.BOOL, wt.HWND)
show_window = bind(user, "ShowWindowAsync", wt.BOOL, wt.HWND, ct.c_int)
set_foreground = bind(user, "SetForegroundWindow", wt.BOOL, wt.HWND)
set_position = bind(user, "SetWindowPos", wt.BOOL, wt.HWND, wt.HWND,
                    ct.c_int, ct.c_int, ct.c_int, ct.c_int, wt.UINT)
monitor_from_rect = bind(user, "MonitorFromRect", wt.HANDLE, ct.POINTER(wt.RECT), wt.DWORD)
get_monitor = bind(user, "GetMonitorInfoW", wt.BOOL, wt.HANDLE, ct.POINTER(MonitorInfo))
get_window_rect = bind(user, "GetWindowRect", wt.BOOL, wt.HWND, ct.POINTER(wt.RECT))
open_process = bind(kernel, "OpenProcess", wt.HANDLE, wt.DWORD, wt.BOOL, wt.DWORD)
close_handle = bind(kernel, "CloseHandle", wt.BOOL, wt.HANDLE)
process_image = bind(kernel, "QueryFullProcessImageNameW", wt.BOOL, wt.HANDLE, wt.DWORD,
                     wt.LPWSTR, ct.POINTER(wt.DWORD))
process_family = bind(kernel, "GetPackageFamilyName", wt.LONG, wt.HANDLE,
                      ct.POINTER(wt.UINT), wt.LPWSTR)
dwm = ct.WinDLL("dwmapi")
window_attribute = bind(dwm, "DwmGetWindowAttribute", wt.LONG, wt.HWND, wt.DWORD, wt.LPVOID, wt.DWORD)


def set_dpi_awareness():
    try:
        return bool(bind(user, "SetProcessDpiAwarenessContext", wt.BOOL, wt.HANDLE)(-4))
    except AttributeError:
        bind(user, "SetProcessDPIAware", wt.BOOL)()
        return False


def top_level(widget):
    return ancestor(widget.winfo_id(), 2) or widget.winfo_id()


def window_position(hwnd):
    rect = wt.RECT()
    get_window_rect(hwnd, ct.byref(rect))
    return rect.left, rect.top


def move(hwnd, x, y):
    # Native coordinates preserve negative positions on secondary monitors.
    set_position(hwnd, None, x, y, 0, 0, 0x0001 | 0x0004 | 0x0010)


def work_area(x, y, width, height):
    rect = wt.RECT(x, y, x + width, y + height)
    monitor = monitor_from_rect(ct.byref(rect), 2)  # nearest monitor
    info = MonitorInfo(ct.sizeof(MonitorInfo))
    if not get_monitor(monitor, ct.byref(info)):
        return 0, 0, user.GetSystemMetrics(0), user.GetSystemMetrics(1)
    return info.work.left, info.work.top, info.work.right, info.work.bottom


def clamp_position(x, y, width, height):
    left, top, right, bottom = work_area(x, y, width, height)
    return max(left, min(x, right - width)), max(top, min(y, bottom - height))


def window_scale(hwnd):
    try:
        dpi = bind(user, "GetDpiForWindow", wt.UINT, wt.HWND)(hwnd)
        return (dpi or 96) / 96
    except AttributeError:
        return 1.0


def window_pid(hwnd):
    pid = wt.DWORD()
    thread_pid(hwnd, ct.byref(pid))
    return pid.value


def is_codex_process(pid):
    handle = open_process(0x1000, False, pid)
    if not handle:
        return False
    try:
        family = ct.create_unicode_buffer(256)
        family_size = wt.UINT(len(family))
        if process_family(handle, ct.byref(family_size), family) != 0 or family.value != PACKAGE_FAMILY:
            return False
        image = ct.create_unicode_buffer(32768)
        image_size = wt.DWORD(len(image))
        return (
            bool(process_image(handle, 0, image, ct.byref(image_size))) and
            os.path.basename(image.value).casefold() == "chatgpt.exe"
        )
    finally:
        close_handle(handle)


def codex_windows():
    windows = []
    processes = {}

    @EnumProc
    def visit(hwnd, _):
        if visible(hwnd) and title_length(hwnd):
            cloaked = wt.DWORD()
            window_attribute(hwnd, 14, ct.byref(cloaked), ct.sizeof(cloaked))
            if not cloaked.value:
                pid = window_pid(hwnd)
                if pid not in processes:
                    processes[pid] = is_codex_process(pid)
                if processes[pid]:
                    windows.append((hwnd, pid))
        return True

    enum_windows(visit, 0)
    return windows


def autostart_command(interpreter, release, directory):
    pythonw = Path(interpreter).with_name("pythonw.exe")
    executable = pythonw if pythonw.is_file() else interpreter
    return subprocess.list2cmdline([str(executable), str(release / "pet/pet.pyw"), str(directory)])


def autostart_value():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, access=winreg.KEY_QUERY_VALUE) as key:
            value, _ = winreg.QueryValueEx(key, RUN_VALUE)
            return value
    except FileNotFoundError:
        return None


def set_autostart(command, owned):
    current = autostart_value()
    if current is not None and current not in owned:
        raise ValueError("The startup entry belongs to another installation")
    if current == command:
        return
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, access=winreg.KEY_SET_VALUE) as key:
        if command is None:
            winreg.DeleteValue(key, RUN_VALUE)
        else:
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, command)


class CodexWindows:
    def __init__(self):
        self.last_seen = None
        self._foreground = None
        self._matched = False

    def in_foreground(self):
        hwnd = foreground()
        pair = (hwnd, window_pid(hwnd)) if hwnd else None
        if pair != self._foreground:
            self._foreground = pair
            self._matched = bool(pair and is_codex_process(pair[1]))
            if self._matched:
                self.last_seen = pair
        return self._matched

    def activate(self):
        matches = codex_windows()
        if len(matches) == 1:
            hwnd, _ = matches[0]
        elif self.last_seen in matches:
            hwnd, _ = self.last_seen
        else:
            return "ambiguous" if matches else "missing"
        if hwnd == foreground() and not is_iconic(hwnd):
            return "active"
        if is_iconic(hwnd):
            show_window(hwnd, 9)  # SW_RESTORE
        return "active" if set_foreground(hwnd) else "denied"

    @staticmethod
    def launch():
        os.startfile("shell:AppsFolder\\" + APP_ID)
