# -*- coding: utf-8 -*-
"""ATK 无头启动辅助：--headless 只关 2D/3D 视图，主窗口仍可见，这里启动后主动隐藏。

只隐藏本次启动新增的 ATK 进程窗口，不影响用户手动打开的 ATK GUI。
"""
import ctypes, os, socket, subprocess, time

ATK_ROOT = r"D:\ATK航天任务设计工作箱\ATK-v4.0.1-rc.5-windows-x64\ATK-4.0.1-rc.5"
ATK_EXE = os.path.join(ATK_ROOT, "ATK.exe")
_user32 = ctypes.windll.user32


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
    """启动无头 ATK 并隐藏其窗口，返回 Popen。"""
    before = _atk_pids()
    si = subprocess.STARTUPINFO(); si.dwFlags |= subprocess.STARTF_USESHOWWINDOW; si.wShowWindow = 0
    args = [ATK_EXE, "--headless", "--port", str(port),
            "--hide-startup", "--hide-welcome", "--no-crash-report"]
    if new_scenario:
        args.insert(2, "--new-scenario")
    if extra_args:
        args.extend(extra_args)
    proc = subprocess.Popen(args, cwd=ATK_ROOT, startupinfo=si)
    if not wait_port(port):
        raise RuntimeError(f"ATK 端口 {port} 未就绪")
    hide_new_atk_windows(before)
    return proc
