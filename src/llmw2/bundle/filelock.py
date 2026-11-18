# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Locks on a file, across threads and across processes.

`FileLock(path)` is an exclusive lock on `path` (created if missing): `flock` on POSIX, a lock on byte 0 on Windows, so the
bytes from 64 on stay free to hold data that other processes read while the lock is held. It is held by a process: the
kernel drops it when the process dies, so there is no stale lock to expire. In front of the kernel lock there is a
`threading.Lock` per file of the process, so the lock holds across threads too, whatever the handles do.

`acquire()` waits in the kernel (`flock` on POSIX, `LockFileEx` on Windows) instead of polling; `try_acquire()` never waits;
both accept a `timeout`, measured on the monotonic clock. Waiting is not ordered: whoever the kernel wakes takes the lock.
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
    _ERROR_LOCK_VIOLATION = 33
    _ERROR_IO_PENDING = 997

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

    @property
    def held(self) -> bool:
        return self._fd is not None

    def try_acquire(self) -> bool:
        """Take the lock if it is free right now; whether it was."""
        return self.acquire(timeout=0)

    def acquire(self, timeout: float | None = None) -> bool:
        """Take the lock, waiting as long as `timeout` seconds (None: as long as it takes); whether it was taken."""
        if self._fd is not None:
            raise RuntimeError("the lock is held by this object already: it is not reentrant")
        deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
        if not self._thread.acquire(timeout=-1 if deadline is None else max(0.0, deadline - time.monotonic())):
            return False
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
