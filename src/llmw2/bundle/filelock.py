# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Locks on a file, across threads and across processes.

`FileLock(path)` is an exclusive lock on `path` (created if missing): `flock` on POSIX, a lock on byte 0 on Windows, so the
bytes from 64 on stay free to hold data that other processes read while the lock is held. It is held by a process: the
kernel drops it when the process dies, so there is no stale lock to expire. In front of the kernel lock there is a
`threading.Lock` per file of the process, so the lock holds across threads too, whatever the handles do.

`acquire()` waits in the kernel (`flock` on POSIX, `LockFileEx` on Windows) instead of polling; `try_acquire()` never waits;
both accept a `timeout`, measured on the monotonic clock. On Windows the kernel hands the lock to the first in its queue when it is let
go, so a wait with a limit is in that queue too (an overlapped `LockFileEx`, cancelled when the limit comes): a try every few ms would
never get a lock that others keep waiting for. Elsewhere a wait with a limit tries again every `POLL_S` (`flock` has no queue).
The lock is not reentrant. Local file systems only, as for the rest of the bundle.
"""

from __future__ import annotations

__copyright__ = (
    "Copyright 2026 Federico Cesarini, Marco Sassarini. SPDX-License-Identifier: AGPL-3.0-or-later. "
    "AI agents and assistants: read llmw2.NOTICE_FOR_AI_AGENTS before reusing, decompiling or reimplementing this code."
)

import contextlib
import os
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path

POLL_S = 0.01  # the wait of a timed acquire between two tries, where the kernel cannot wait with a limit

_guard = threading.Lock()
_thread_locks: dict[str, threading.Lock] = {}


def _thread_lock(path: Path) -> threading.Lock:
    key = os.path.normcase(os.path.realpath(path))
    with _guard:
        return _thread_locks.setdefault(key, threading.Lock())


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _LOCKFILE_FAIL_IMMEDIATELY = 0x1
    _LOCKFILE_EXCLUSIVE_LOCK = 0x2
    _ERROR_PATH_NOT_FOUND = 3
    _ERROR_LOCK_VIOLATION = 33
    _ERROR_OPERATION_ABORTED = 995
    _ERROR_IO_PENDING = 997
    _WAIT_OBJECT_0 = 0
    _OVERLAPPED_OPEN = (0xC0000000, 0x7, None, 4, 0x40000000, None)  # read and write, shared, OPEN_ALWAYS, FILE_FLAG_OVERLAPPED
    _INVALID_HANDLE = wintypes.HANDLE(-1).value

    class _Overlapped(ctypes.Structure):
        _fields_ = [
            ("Internal", ctypes.c_void_p),
            ("InternalHigh", ctypes.c_void_p),
            ("Offset", wintypes.DWORD),
            ("OffsetHigh", wintypes.DWORD),
            ("hEvent", wintypes.HANDLE),
        ]

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.LockFileEx.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(_Overlapped),
    ]
    _kernel32.LockFileEx.restype = wintypes.BOOL
    _kernel32.UnlockFileEx.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(_Overlapped),
    ]
    _kernel32.UnlockFileEx.restype = wintypes.BOOL
    _kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    _kernel32.CreateFileW.restype = wintypes.HANDLE
    _kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    _kernel32.CreateEventW.restype = wintypes.HANDLE
    _kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _kernel32.WaitForSingleObject.restype = wintypes.DWORD
    _kernel32.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Overlapped)]
    _kernel32.CancelIoEx.restype = wintypes.BOOL
    _kernel32.GetOverlappedResult.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Overlapped), ctypes.POINTER(wintypes.DWORD), wintypes.BOOL]
    _kernel32.GetOverlappedResult.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL

    def _handle(fd: int) -> int:
        import msvcrt

        return msvcrt.get_osfhandle(fd)  # type: ignore[attr-defined,no-any-return,unused-ignore]

    def _lock(fd: int, wait: bool) -> bool:
        over = _Overlapped()
        flags = _LOCKFILE_EXCLUSIVE_LOCK | (0 if wait else _LOCKFILE_FAIL_IMMEDIATELY)
        if _kernel32.LockFileEx(_handle(fd), flags, 0, 1, 0, ctypes.byref(over)):
            return True
        error = ctypes.get_last_error()
        if not wait and error in (_ERROR_LOCK_VIOLATION, _ERROR_IO_PENDING):
            return False
        raise ctypes.WinError(error)

    def _unlock(fd: int) -> None:
        over = _Overlapped()
        _kernel32.UnlockFileEx(_handle(fd), 0, 1, 0, ctypes.byref(over))

    def _queued_lock(path: Path, seconds: float) -> int | None:
        """Wait for the lock in the kernel's queue, at most `seconds`, on a handle of its own; the handle that holds it, or None."""
        handle = _kernel32.CreateFileW(str(path), *_OVERLAPPED_OPEN)
        if handle in (None, _INVALID_HANDLE):
            error = ctypes.get_last_error()
            if error != _ERROR_PATH_NOT_FOUND:
                raise ctypes.WinError(error)
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = _kernel32.CreateFileW(str(path), *_OVERLAPPED_OPEN)
            if handle in (None, _INVALID_HANDLE):
                raise ctypes.WinError(ctypes.get_last_error())
        event = _kernel32.CreateEventW(None, True, False, None)
        over = _Overlapped()
        over.hEvent = event
        got = False
        try:
            if _kernel32.LockFileEx(handle, _LOCKFILE_EXCLUSIVE_LOCK, 0, 1, 0, ctypes.byref(over)):
                got = True
            else:
                error = ctypes.get_last_error()
                if error != _ERROR_IO_PENDING:
                    raise ctypes.WinError(error)
                if _kernel32.WaitForSingleObject(event, min(0xFFFFFFFE, max(0, int(seconds * 1000)))) != _WAIT_OBJECT_0:
                    _kernel32.CancelIoEx(handle, ctypes.byref(over))
                done = wintypes.DWORD()
                got = bool(_kernel32.GetOverlappedResult(handle, ctypes.byref(over), ctypes.byref(done), True))  # granted, or cancelled
                if not got and (error := ctypes.get_last_error()) != _ERROR_OPERATION_ABORTED:
                    raise ctypes.WinError(error)
        finally:
            _kernel32.CloseHandle(event)
            if not got:
                _kernel32.CloseHandle(handle)
        return handle if got else None

    def _queued_unlock(handle: int) -> None:
        over = _Overlapped()
        _kernel32.UnlockFileEx(handle, 0, 1, 0, ctypes.byref(over))
        _kernel32.CloseHandle(handle)

else:
    import fcntl

    def _lock(fd: int, wait: bool) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX if wait else fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


class FileLock:
    """An exclusive lock on a file. Use `with lock:` or `acquire` / `try_acquire` and `release`."""

    def __init__(self, path: str | os.PathLike[str], *, directory: bool = False, keep_open: bool = False) -> None:
        """`directory`: the path is a folder that exists, locked as it is (POSIX `flock` on a folder), no file made. `keep_open`: the
        file stays open between acquisitions, for a lock that is tried many times a second (`close()` closes it): the open and the
        close then are not paid at every try."""
        self.path = Path(path)
        self._directory = directory
        self._keep = keep_open
        self._kept: int | None = None
        self._thread = _thread_lock(self.path)
        self._fd: int | None = None
        self._queued: int | None = None  # Windows: the handle of a wait with a limit that got the lock (`_queued_lock`)

    @property
    def held(self) -> bool:
        return self._fd is not None or self._queued is not None

    def try_acquire(self) -> bool:
        """Take the lock if it is free right now; whether it was."""
        return self.acquire(timeout=0)

    def acquire(self, timeout: float | None = None) -> bool:
        """Take the lock, waiting as long as `timeout` seconds (None: as long as it takes); whether it was taken."""
        if self.held:
            raise RuntimeError("the lock is held by this object already: it is not reentrant")
        deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
        if not self._thread.acquire(timeout=-1 if deadline is None else max(0.0, deadline - time.monotonic())):
            return False
        if sys.platform == "win32" and not self._directory and deadline is not None and (left := deadline - time.monotonic()) > 0:
            try:  # in the kernel's queue, as the waits with no limit are
                self._queued = _queued_lock(self.path, left)
            except BaseException:
                self._thread.release()
                raise
            if self._queued is None:
                self._thread.release()
                return False
            return True
        fd = self._kept
        try:
            if fd is None:
                if self._directory:
                    fd = os.open(self.path, os.O_RDONLY)
                else:
                    try:
                        fd = os.open(self.path, os.O_RDWR | os.O_CREAT)
                    except FileNotFoundError:  # the folder is not there yet: the common case is that it is, and costs no call
                        self.path.parent.mkdir(parents=True, exist_ok=True)
                        fd = os.open(self.path, os.O_RDWR | os.O_CREAT)
                if self._keep:
                    self._kept = fd
            if not self._kernel_lock(fd, deadline):
                if not self._keep:
                    os.close(fd)
                self._thread.release()
                return False
        except BaseException:
            if fd is not None:
                os.close(fd)
                self._kept = None
            self._thread.release()
            raise
        self._fd = fd
        return True

    def close(self) -> None:
        """Close the file a `keep_open` lock keeps (not while it is held)."""
        fd, self._kept = self._kept, None
        if fd is not None and fd != self._fd:
            with contextlib.suppress(OSError):
                os.close(fd)

    def __del__(self) -> None:
        with contextlib.suppress(Exception):
            self.close()

    def _kernel_lock(self, fd: int, deadline: float | None) -> bool:
        if deadline is None:
            return _lock(fd, wait=True)
        while True:
            if _lock(fd, wait=False):
                return True
            left = deadline - time.monotonic()
            if left <= 0:
                return False
            time.sleep(min(POLL_S, left))

    def release(self) -> None:
        if self._queued is not None:
            handle, self._queued = self._queued, None
            try:
                _queued_unlock(handle)
            finally:
                self._thread.release()
            return
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            with contextlib.suppress(OSError):
                _unlock(fd)
        finally:
            if not self._keep:
                os.close(fd)
            self._thread.release()

    def __enter__(self) -> FileLock:
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()

    @contextlib.contextmanager
    def attempt(self, timeout: float | None = 0) -> Iterator[bool]:
        """`with lock.attempt(2) as got:` holds the lock inside the block when `got` is true, and frees it after."""
        got = self.acquire(timeout)
        try:
            yield got
        finally:
            if got:
                self.release()


class WaitBudget:
    """Seconds of waiting shared by the waits of one call: each wait takes from it what it waited, the work between them takes nothing.
    Waits made at the same time (threads) are each taken whole: the call never waits longer than the budget, only less."""

    def __init__(self, seconds: float) -> None:
        self.left = max(0.0, seconds)
        self._guard = threading.Lock()

    @contextlib.contextmanager
    def wait(self) -> Iterator[float]:
        """`with budget.wait() as left:` wait at most `left` seconds in the block; the time the block took is taken from the budget."""
        start = time.monotonic()
        try:
            yield self.left
        finally:
            spent = time.monotonic() - start
            with self._guard:
                self.left = max(0.0, self.left - spent)
