"""Small, cooperative file-publication guards for analysis-only writers.

Locks fail fast and cover cooperating writers, not hostile filesystem changes.
Stale locks after a killed process require operator inspection/removal. Paired
publication rolls back ordinary exceptions, but is not crash-atomic across files.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


def require_distinct_paths(*paths: Path) -> None:
    """Reject lexical, symlink, case-normalized and existing hardlink aliases."""
    for path in paths:
        _require_user_path(path)
    _require_no_aliases(*paths)


def _require_user_path(path: Path) -> None:
    # Reserve sidecars for locks, including aliases and directory components.
    # Compatibility: unusual user paths named .*.lock are no longer accepted.
    for candidate in (path.absolute(), path.resolve()):
        if any(part.startswith(".") and part.lower().endswith(".lock")
               for part in candidate.parts):
            raise ValueError(f"Reserved output lock namespace: {path}")


def _require_no_aliases(*paths: Path) -> None:
    for index, path in enumerate(paths):
        for other in paths[:index]:
            if path.resolve() == other.resolve() or (
                path.exists() and other.exists() and path.samefile(other)
            ):
                raise ValueError(f"Paths must differ (alias): {path} and {other}")


@contextmanager
def staged_path(destination: Path) -> Iterator[Path]:
    """Reserve an exclusive temporary in the destination's filesystem."""
    _require_user_path(destination)
    fd, name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    os.close(fd)  # Windows cannot replace an open NamedTemporaryFile.
    path = Path(name)
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)


@contextmanager
def output_lock(destination: Path, *protected: Path) -> Iterator[None]:
    """Exclusive, portable sidecar; never remove a lock owned by another call."""
    require_distinct_paths(destination, *protected)
    resolved = destination.resolve()
    lock = resolved.with_name(f".{resolved.name}.lock")
    _require_no_aliases(lock, destination, *protected)
    token = uuid.uuid4().hex.encode("ascii")
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ValueError(f"Output is locked by another writer: {destination}") from exc
    identity = os.fstat(fd)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(token)
        yield
    finally:
        try:
            stat = lock.lstat()
            if (stat.st_dev, stat.st_ino) == (identity.st_dev, identity.st_ino) and lock.read_bytes() == token:
                lock.unlink()
        except FileNotFoundError:
            pass


def publish_pair(output_stage: Path, output: Path, manifest_stage: Path, manifest: Path) -> None:
    """Publish validated artifacts, restoring the old output if manifest replace fails.

    The old manifest is not touched until the last replace. Both destinations
    must be locked by the caller. No work that can fail follows that replace.
    """
    with staged_path(output) as backup:
        existed = output.exists()
        if existed:
            shutil.copyfile(output, backup)
        os.replace(output_stage, output)
        try:
            os.replace(manifest_stage, manifest)
        except BaseException:
            if existed:
                os.replace(backup, output)
            else:
                output.unlink(missing_ok=True)
            raise