from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path


class AlreadyRunning(RuntimeError):
    pass


class SingleInstance:
    """Prevent two checker processes from writing state/sending duplicates."""

    def __init__(self, data_dir: Path, name: str = "Local\\UITCheckerMutex"):
        self.data_dir = Path(data_dir)
        self.name = name
        self._handle = None
        self._file = None

    def acquire(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.CreateMutexW(None, False, self.name)
            if not handle:
                raise AlreadyRunning("Không tạo được mutex")
            if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
                kernel32.CloseHandle(handle)
                raise AlreadyRunning("Đã có một instance UIT Checker đang chạy")
            self._handle = handle
            return

        import fcntl

        lock_path = self.data_dir / "run.lock"
        self._file = open(lock_path, "a+")
        try:
            fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._file.close()
            self._file = None
            raise AlreadyRunning("Đã có một instance UIT Checker đang chạy") from exc
        self._file.seek(0)
        self._file.truncate()
        self._file.write(str(os.getpid()))
        self._file.flush()

    def release(self) -> None:
        if sys.platform == "win32":
            if self._handle:
                ctypes.windll.kernel32.CloseHandle(self._handle)
                self._handle = None
            return
        if self._file:
            try:
                import fcntl

                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
            finally:
                self._file.close()
                self._file = None
