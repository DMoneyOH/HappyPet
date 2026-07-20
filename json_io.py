#!/usr/bin/env python3
"""Crash-safe JSON I/O for pipeline state (products.json above all).

Why this exists: several workflows read-modify-write products.json, sometimes in
overlapping windows, on ephemeral CI runners that can be cancelled or OOM-killed
mid-step. A bare ``path.write_text(json.dumps(...))`` truncates the file first,
so a kill during the write leaves a half-written products.json that the very next
run then fails to parse -- taking down the whole pipeline until a human repairs
it by hand.

- ``atomic_write_json`` writes to a temp file in the same directory and atomically
  renames it into place (``os.replace`` is atomic on POSIX and on Windows for a
  same-volume rename), so a reader never sees a partial file and a crash never
  corrupts the target.
- ``read_json`` raises a clear, actionable ``CorruptJSONError`` (not a bare
  ``json.JSONDecodeError``) when the on-disk file exists but cannot be parsed,
  and returns ``default`` when the file is simply absent.
"""

import json
import os
import tempfile
from pathlib import Path


class CorruptJSONError(ValueError):
    """Raised when a JSON file exists on disk but cannot be parsed."""


def atomic_write_json(path, data, *, indent: int = 2, trailing_newline: bool = False) -> None:
    """Serialize ``data`` to ``path`` atomically (temp file + rename)."""
    path = Path(path)
    text = json.dumps(data, indent=indent)
    if trailing_newline:
        text += "\n"
    # Temp file in the same directory guarantees a same-volume (atomic) rename.
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        # Never leave scratch behind on failure.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path, default=None):
    """Load JSON from ``path``.

    Returns ``default`` if the file does not exist. Raises ``CorruptJSONError``
    if the file exists but is not valid JSON (truncated/corrupt), so callers get
    a clear failure instead of a bare ``json.JSONDecodeError`` deep in a run.
    """
    path = Path(path)
    if not path.exists():
        return default
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise CorruptJSONError(
            f"{path} exists but is not valid JSON (truncated or corrupt?): {exc}"
        ) from exc
