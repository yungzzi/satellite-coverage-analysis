# -*- coding: utf-8 -*-
"""ATK 无头启动辅助：--headless 只关 2D/3D 视图，主窗口仍可见，这里启动后主动隐藏。

只隐藏本次启动新增的 ATK 进程窗口，不影响用户手动打开的 ATK GUI。
ATK 安装目录可通过环境变量 ATK_ROOT 覆盖（默认本机实验路径）。

真正无头（2026-08-15，§10.22）：把 ATK 启动到独立的非交互桌面（CreateDesktopW），
窗口永远不会出现在用户桌面上（不会闪窗）。桌面创建失败时回退到 SW_HIDE + 启动后
枚举隐藏（旧机制，可能短暂闪窗）。
"""
import ctypes
import os
import socket
import subprocess
import threading
import time
from ctypes import wintypes

DEFAULT_ATK_ROOT = r"D:\ATK航天任务设计工作箱\ATK-v4.0.1-rc.5-windows-x64\ATK-4.0.1-rc.5"


def get_atk_root() -> str:
    """ATK 根目录：环境变量 ATK_ROOT 优先，否则用默认路径。"""
    return os.environ.get("ATK_ROOT") or DEFAULT_ATK_ROOT


ATK_ROOT = get_atk_root()
ATK_EXE = os.path.join(ATK_ROOT, "ATK.exe")
_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32

STARTF_USESHOWWINDOW = 0x00000001
SW_HIDE = 0
STILL_ACTIVE = 259
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
DESKTOP_CREATEWINDOW = 0x0002
DESKTOP_ENUMERATE = 0x0040
DESKTOP_WRITEOBJECTS = 0x0080
DESKTOP_READOBJECTS = 0x0001
DESKTOP_SWITCHDESKTOP = 0x0100


class STARTUPINFO(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(wintypes.BYTE)),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


class HiddenDesktopProcess:
    """CreateProcess 到独立桌面启动的进程（Popen 兼容的最小接口：pid/kill）。"""

    def __init__(self, pid: int, hdesk):
        self.pid = pid
        self._hdesk = hdesk

    def kill(self):
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.pid)],
                           capture_output=True, timeout=30)
        except Exception:
            pass

    def wait(self, timeout=None):
        t0 = time.time()
        while True:
            if not _pid_alive(self.pid):
                return 0
            if timeout is not None and time.time() - t0 >= timeout:
                return None
            time.sleep(0.5)


def _pid_alive(pid: int) -> bool:
    h = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return False
    try:
        code = wintypes.DWORD()
        _kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        return code.value == STILL_ACTIVE
    finally:
        _kernel32.CloseHandle(h)


def _watch_close_desktop(pid: int, hdesk) -> None:
    """进程退出后自动关闭隐藏桌面句柄，避免每次启动泄漏一个桌面。"""
    try:
        while _pid_alive(pid):
            time.sleep(2)
    finally:
        try:
            _user32.CloseDesktop(hdesk)
        except Exception:
            pass


def launch_on_hidden_desktop(args, cwd):
    """把 ATK 启动到独立非交互桌面；成功返回 HiddenDesktopProcess，失败返回 None。"""
    try:
        name = f"ATKHidden_{os.getpid()}_{int(time.time() * 1000)}"
        hdesk = _user32.CreateDesktopW(
            name, None, None, 0,
            DESKTOP_CREATEWINDOW | DESKTOP_ENUMERATE | DESKTOP_WRITEOBJECTS
            | DESKTOP_READOBJECTS | DESKTOP_SWITCHDESKTOP,
            None)
        if not hdesk:
            return None
        si = STARTUPINFO()
        si.cb = ctypes.sizeof(STARTUPINFO)
        si.lpDesktop = f"Winsta0\\{name}"
        si.dwFlags = STARTF_USESHOWWINDOW
        si.wShowWindow = SW_HIDE
        pi = PROCESS_INFORMATION()
        cmdline = subprocess.list2cmdline(args)
        ok = _kernel32.CreateProcessW(
            None, cmdline, None, None, False, 0, None, cwd,
            ctypes.byref(si), ctypes.byref(pi))
        if not ok:
            _user32.CloseDesktop(hdesk)
            return None
        _kernel32.CloseHandle(pi.hThread)
        _kernel32.CloseHandle(pi.hProcess)
        threading.Thread(target=_watch_close_desktop, args=(pi.dwProcessId, hdesk),
                         daemon=True).start()
        return HiddenDesktopProcess(pi.dwProcessId, hdesk)
    except Exception:
        return None


def _atk_pids():
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-Process | Where-Object { $_.ProcessName -match '^ATK$|UiATK' } | Select-Object -ExpandProperty Id"],
        capture_output=True, text=True).stdout
    return {int(x) for x in out.split() if x.strip().isdigit()}


def wait_port(port, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        s = socket.socket(); s.settimeout(0.5)
        try:
            s.connect(("127.0.0.1", port)); s.close(); return True
        except OSError:
            pass
        finally:
            s.close()
        time.sleep(0.5)
    return False


def _hide_process_windows(pid):
    GetWindowThreadProcessId = _user32.GetWindowThreadProcessId
    IsWindowVisible = _user32.IsWindowVisible
    ShowWindow = _user32.ShowWindow
    targets = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(hwnd, _lp):
        p = ctypes.c_ulong()
        GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid and IsWindowVisible(hwnd):
            targets.append(hwnd)
        return True

    _user32.EnumWindows(cb, 0)
    for hwnd in targets:
        ShowWindow(hwnd, 0)  # SW_HIDE


def hide_new_atk_windows(before_pids):
    """隐藏本次启动新增 ATK 进程的可见主窗口。"""
    for pid in _atk_pids() - before_pids:
        try:
            _hide_process_windows(pid)
        except Exception:
            pass


def launch_headless(port, new_scenario=True, extra_args=None):
    """启动真正无头的 ATK（独立隐藏桌面），返回 Popen 兼容对象。"""
    before = _atk_pids()
    args = [ATK_EXE, "--headless", "--port", str(port),
            "--hide-startup", "--hide-welcome", "--no-crash-report"]
    if new_scenario:
        args.insert(2, "--new-scenario")
    if extra_args:
        args.extend(extra_args)
    proc = launch_on_hidden_desktop(args, ATK_ROOT)
    if proc is None:
        # 回退：SW_HIDE 启动 + 启动后枚举隐藏（旧机制，可能有短暂闪窗）
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        proc = subprocess.Popen(args, cwd=ATK_ROOT, startupinfo=si)
        if not wait_port(port):
            raise RuntimeError(f"ATK 端口 {port} 未就绪")
        hide_new_atk_windows(before)
        return proc
    if not wait_port(port):
        proc.kill()
        raise RuntimeError(f"ATK 端口 {port} 未就绪")
    hide_new_atk_windows(before)  # 兜底；隐藏桌面下交互桌面本来就没有窗口
    return proc
