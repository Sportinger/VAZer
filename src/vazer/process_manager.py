from __future__ import annotations

import os
import subprocess
import threading
from typing import Any

_LOCK = threading.Lock()
_ACTIVE_PROCESSES: dict[int, subprocess.Popen[Any]] = {}


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
                pass
        finally:
            unregister_process(process)
