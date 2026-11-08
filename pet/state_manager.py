"""Shared JSON I/O. Writers lock a stable sidecar, then atomically replace the payload."""
import errno
import json
import os
import tempfile
import time
from contextlib import contextmanager

if os.name == "nt":
    import msvcrt
else:
    import fcntl


@contextmanager
def file_lock(path, timeout=0.25):
    deadline = time.monotonic() + timeout
    with path.open("a+b") as handle:
        while True:
            handle.seek(0)
            try:
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise
                if time.monotonic() >= deadline:
                    raise TimeoutError("Another process holds the file lock") from exc
                time.sleep(0.01)
        # Closing the descriptor releases the OS lock, even after a crash.
        yield


def read_json(path):
    with path.open(encoding="utf-8-sig") as file:
        return json.load(file)


def atomic_bytes(path, data):
    fd, name = tempfile.mkstemp(prefix=".pet-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(data)
        for attempt in range(4):
            try:
                os.replace(name, path)
                break
            except PermissionError:
                if attempt == 3:
                    raise
                # Windows readers can hold a transient handle that denies deletion.
                time.sleep(0.01)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def write_json(path, data):
    atomic_bytes(path, (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
