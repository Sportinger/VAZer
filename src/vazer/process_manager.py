from __future__ import annotations

import atexit
import ctypes
from ctypes import wintypes
import os
import subprocess
import threading
from typing import Any

_LOCK = threading.Lock()
_ACTIVE_PROCESSES: dict[int, subprocess.Popen[Any]] = {}
_JOB_HANDLE: int | None = None

if os.name == "nt":
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.restype = wintypes.BOOL
    JobObjectExtendedLimitInformation = 9
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]


def _job_handle() -> int | None:
    global _JOB_HANDLE
    if os.name != "nt":
        return None
    if _JOB_HANDLE is not None:
        return _JOB_HANDLE

    handle = kernel32.CreateJobObjectW(None, None)
    if not handle:
        return None

    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    ok = kernel32.SetInformationJobObject(
        handle,
        JobObjectExtendedLimitInformation,
        ctypes.byref(info),
        ctypes.sizeof(info),
    )
    if not ok:
        kernel32.CloseHandle(handle)
        return None

    _JOB_HANDLE = int(handle)
    return _JOB_HANDLE


def _assign_process_to_job(process: subprocess.Popen[Any]) -> None:
    if os.name != "nt":
        return
    handle = _job_handle()
    if handle is None:
        return
    try:
        kernel32.AssignProcessToJobObject(handle, wintypes.HANDLE(int(process._handle)))
    except Exception:
        return


def _apply_windows_no_window(kwargs: dict[str, Any]) -> dict[str, Any]:
    if os.name != "nt":
        return kwargs

    adjusted = dict(kwargs)
    creationflags = int(adjusted.get("creationflags") or 0)
    create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    adjusted["creationflags"] = creationflags | create_no_window

    startupinfo = adjusted.get("startupinfo")
    if startupinfo is None:
        startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
    adjusted["startupinfo"] = startupinfo
    return adjusted


def _taskkill_process_tree(pid: int) -> None:
    if os.name != "nt":
        return
    kwargs = _apply_windows_no_window(
        {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "check": False,
        }
    )
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            **kwargs,
        )
    except Exception:
        pass


def run_managed(
    args: list[str],
    *,
    check: bool = False,
    capture_output: bool = False,
    text: bool = False,
    encoding: str | None = None,
    errors: str | None = None,
    **kwargs: Any,
) -> subprocess.CompletedProcess[Any]:
    kwargs = _apply_windows_no_window(kwargs)
    if capture_output:
        kwargs.setdefault("stdout", subprocess.PIPE)
        kwargs.setdefault("stderr", subprocess.PIPE)

    process = subprocess.Popen(
        args,
        text=text,
        encoding=encoding,
        errors=errors,
        **kwargs,
    )
    _assign_process_to_job(process)
    with _LOCK:
        _ACTIVE_PROCESSES[process.pid] = process

    try:
        stdout, stderr = process.communicate()
    finally:
        with _LOCK:
            _ACTIVE_PROCESSES.pop(process.pid, None)

    completed = subprocess.CompletedProcess(args=args, returncode=process.returncode, stdout=stdout, stderr=stderr)
    if check and process.returncode != 0:
        raise subprocess.CalledProcessError(
            process.returncode,
            args,
            output=stdout,
            stderr=stderr,
        )
    return completed


def popen_managed(
    args: list[str],
    **kwargs: Any,
) -> subprocess.Popen[Any]:
    kwargs = _apply_windows_no_window(kwargs)
    process = subprocess.Popen(args, **kwargs)
    _assign_process_to_job(process)
    with _LOCK:
        _ACTIVE_PROCESSES[process.pid] = process
    return process


def unregister_process(process: subprocess.Popen[Any]) -> None:
    with _LOCK:
        _ACTIVE_PROCESSES.pop(process.pid, None)


def terminate_registered_processes(*, timeout_seconds: float = 3.0) -> None:
    with _LOCK:
        processes = list(_ACTIVE_PROCESSES.values())

    for process in processes:
        if process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass

    for process in processes:
        if process.poll() is not None:
            unregister_process(process)
            continue
        try:
            process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except OSError:
                pass
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                _taskkill_process_tree(process.pid)
                try:
                    process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    pass
        finally:
            unregister_process(process)

    if os.name == "nt":
        with _LOCK:
            lingering = list(_ACTIVE_PROCESSES.values())
        for process in lingering:
            if process.poll() is None:
                _taskkill_process_tree(process.pid)
            unregister_process(process)


def _shutdown_process_manager() -> None:
    terminate_registered_processes()
    if os.name != "nt":
        return
    global _JOB_HANDLE
    if _JOB_HANDLE is not None:
        try:
            kernel32.CloseHandle(_JOB_HANDLE)
        except Exception:
            pass
        _JOB_HANDLE = None


atexit.register(_shutdown_process_manager)
