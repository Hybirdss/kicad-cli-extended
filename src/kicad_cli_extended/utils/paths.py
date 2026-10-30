"""Path helpers that behave the same on Windows, macOS and Linux."""

from __future__ import annotations

import os
from pathlib import Path, PurePath
from typing import Optional


def same_path(a: str, b: str) -> bool:
    """True if both name the same file (case-insensitive on Windows)."""
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def rel_inside(path: str, base: str) -> Optional[str]:
    """``path`` relative to ``base`` if it lies inside it, else None (never raises across drives)."""
    try:
        rel = os.path.relpath(os.path.abspath(path), os.path.abspath(base))
    except ValueError:  # different drives on Windows
        return None
    if rel == os.pardir or rel.startswith(os.pardir + os.sep) or os.path.isabs(rel):
        return None
    return rel


def kicad_uri(path: str, project_dir: str) -> str:
    """Library-table URI: ``${KIPRJMOD}/...`` inside the project, else an absolute path.

    KiCad stores forward slashes on every OS.
    """
    rel = rel_inside(path, project_dir)
    if rel is not None:
        return "${KIPRJMOD}/" + PurePath(rel).as_posix()
    return Path(path).resolve().as_posix()


def is_dir_hint(value: str) -> bool:
    """Does a user-supplied path end with a separator (i.e. "this is a directory")?"""
    return value.endswith(("/", os.sep)) or bool(os.altsep and value.endswith(os.altsep))
