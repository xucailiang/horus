import fcntl
import hashlib
import json
import os
import re
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any


class HorusError(Exception):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def atomic_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".pending-")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(canonical(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def file_lock(path: Path, *, blocking: bool = True) -> Iterator[int]:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        yield fd
    finally:
        os.close(fd)


class AssetStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def path(self, category: str, ref: str) -> Path:
        if not re.fullmatch(r"[a-z]+", category):
            raise HorusError("invalid_category")
        if not re.fullmatch(category + r"_[0-9a-f]{64}", ref):
            raise HorusError("invalid_ref", ref)
        return self.root / category / f"{ref}.json"

    def put(self, category: str, value: Any) -> str:
        ref = f"{category}_{digest(value)}"
        path = self.path(category, ref)
        with file_lock(self.root / ".assets.lock"):
            if path.exists():
                self.get(category, ref)
            else:
                atomic_write(path, value)
        return ref

    def get(self, category: str, ref: str) -> Any:
        path = self.path(category, ref)
        try:
            value = json.loads(path.read_bytes())
        except (OSError, ValueError) as exc:
            raise HorusError("asset_unavailable", ref) from exc
        if ref != f"{category}_{digest(value)}":
            raise HorusError("integrity_error", ref)
        return value
