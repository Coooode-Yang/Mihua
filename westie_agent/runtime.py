"""Shared process ownership, archive validation and local-only HTTP helpers."""
from __future__ import annotations
import ctypes
import os
import subprocess
import urllib.request
import zipfile
from pathlib import Path

LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

class SetupRequired(RuntimeError):
    pass

class Cancelled(RuntimeError):
    pass

def safe_extract(archive: Path, destination: Path):
    destination = destination.resolve()
    with zipfile.ZipFile(archive) as z:
        for entry in z.infolist():
            resolved = (destination / entry.filename).resolve()
            if not resolved.is_relative_to(destination) or (entry.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("运行时压缩包中包含不安全的路径。")
        z.extractall(destination)



class WindowsJob:
    """Kill only our child process tree if the application exits or crashes."""
    def __init__(self, process: subprocess.Popen):
        self.handle = None
        if os.name != "nt":
            return
        from ctypes import wintypes as w
        class BASIC(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                        ("flags", w.DWORD), ("min_ws", ctypes.c_size_t), ("max_ws", ctypes.c_size_t),
                        ("active", w.DWORD), ("affinity", ctypes.c_size_t),
                        ("priority", w.DWORD), ("scheduling", w.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in ("r", "w", "o", "rb", "wb", "ob")]
        class EXTENDED(ctypes.Structure):
            _fields_ = [("basic", BASIC), ("io", IO), ("pm", ctypes.c_size_t),
                        ("jm", ctypes.c_size_t), ("ppm", ctypes.c_size_t), ("pjm", ctypes.c_size_t)]
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.restype = w.HANDLE
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
        kernel.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        kernel.CloseHandle.argtypes = [w.HANDLE]
        handle = kernel.CreateJobObjectW(None, None)
        limits = EXTENDED()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if (handle and kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits))
                and kernel.AssignProcessToJobObject(handle, int(process._handle))):
            self.handle, self.kernel = handle, kernel
        elif handle:
            kernel.CloseHandle(handle)

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None

