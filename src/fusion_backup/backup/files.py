"""File helpers."""

import hashlib
import os
import tempfile
from pathlib import Path


def sha256(data: bytes) -> str:
    """Hex SHA-256 of some bytes."""
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: Path) -> str | None:
    """Hex SHA-256 of a file, or None if it does not exist."""
    return sha256(path.read_bytes()) if path.is_file() else None


def write_atomic(path: Path, data: bytes) -> None:
    """Write via a temporary file in the same folder, so a crash never leaves a half-written file.

    An existing file keeps its permissions; a new one gets 0644.
    """
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
