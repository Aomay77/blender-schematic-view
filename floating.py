# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Aomay Huang
# Schematic View for Blender. Modified versions must say so and keep this notice.
"""Initial placement for the add-on's own floating Blender window.⁠​‌​‌‌​‌‌‌​‌​‌‌‌​​​‌​‌‌​​‌​‌‌‌‌‌‌‌‌​‌​​‌​‌‌​​​‌‌‌​‌‌​‌​​​‌​‌​​​‌‌​​​​​​‌​‌​‌​‌‌​‌‌​‌‌‌​‌​​​‌​‌​​‌⁠"""

import sys


def portrait_bounds(owner, work):
    left, top = max(owner[0], work[0]), max(owner[1], work[1])
    right, bottom = min(owner[2], work[2]), min(owner[3], work[3])
    if right <= left or bottom <= top:
        left, top, right, bottom = work
    width, height = right - left, bottom - top
    x, y = left + round(width * 0.025), top + round(height * 0.04)
    return x, y, max(1, round(width * 0.31)), max(1, round(height * 0.92))


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes as wt

    WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    class MonitorInfo(ctypes.Structure):
        _fields_ = [
            ("size", wt.DWORD), ("monitor", wt.RECT), ("work", wt.RECT), ("flags", wt.DWORD),
        ]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.EnumThreadWindows.argtypes = (wt.DWORD, WNDENUMPROC, wt.LPARAM)
    user32.EnumThreadWindows.restype = wt.BOOL
    user32.GetWindow.argtypes = (wt.HWND, wt.UINT)
    user32.GetWindow.restype = wt.HWND
    user32.GetWindowRect.argtypes = (wt.HWND, ctypes.POINTER(wt.RECT))
    user32.GetWindowRect.restype = wt.BOOL
    user32.MonitorFromWindow.argtypes = (wt.HWND, wt.DWORD)
    user32.MonitorFromWindow.restype = wt.HMONITOR
    user32.GetMonitorInfoW.argtypes = (wt.HMONITOR, ctypes.POINTER(MonitorInfo))
    user32.GetMonitorInfoW.restype = wt.BOOL
    user32.SetWindowPos.argtypes = (wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, wt.UINT)
    user32.SetWindowPos.restype = wt.BOOL
    kernel32.GetCurrentThreadId.restype = wt.DWORD


def thread_windows():
    if sys.platform != "win32":
        return set()
    handles = set()

    @WNDENUMPROC
    def collect(hwnd, _):
        handles.add(hwnd)
        return True

    user32.EnumThreadWindows(kernel32.GetCurrentThreadId(), collect, 0)
    return handles


def place_new_window(before):
    if not before:
        return False
    new = thread_windows() - before
    # Only touch the top-level child created by Blender during window_new().
    owned = [(hwnd, owner) for hwnd in new if (owner := user32.GetWindow(hwnd, 4)) in before]
    if len(owned) != 1:
        return False
    hwnd, owner = owned[0]
    rect = wt.RECT()
    if not user32.GetWindowRect(owner, ctypes.byref(rect)):
        return False
    owner_bounds = rect.left, rect.top, rect.right, rect.bottom
    monitor = user32.MonitorFromWindow(owner, 2)
    info = MonitorInfo()
    info.size = ctypes.sizeof(info)
    if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        work = info.work
        work_bounds = work.left, work.top, work.right, work.bottom
    else:
        work_bounds = owner_bounds
    bounds = portrait_bounds(owner_bounds, work_bounds)
    return bool(user32.SetWindowPos(hwnd, None, *bounds, 0x0014))
