"""Materialise the session's current (possibly unsaved) state for the real tools.

kicad-cli and pcbnew read files from disk.  When the session holds unsaved
edits (REPL mode, ``--dry-run``, or a multi-step command), we mirror the
project into a temp directory, overlay the in-memory documents and point the
tools there, so what KiCad renders is exactly the session state.
"""

from __future__ import annotations

import contextlib
import os
import shutil
from typing import Iterator, Optional

from .. import STATE_DIR
from ..utils import kicad_backend as kb

SKIP_DIRS = {STATE_DIR, "outputs", "__pycache__", ".git"}
KICAD_SUFFIXES = (".kicad_pro", ".kicad_sch", ".kicad_pcb", ".kicad_sym", ".kicad_dru", ".kicad_wks",
                  ".kicad_jobset", ".kicad_prl")


def _link_or_copy(src: str, dst: str) -> None:
    if os.name != "nt" and not src.endswith(KICAD_SUFFIXES) and os.path.basename(src) not in (
            "sym-lib-table", "fp-lib-table"):
        try:
            os.symlink(src, dst)
            return
        except OSError:
            pass
    shutil.copy2(src, dst)


def mirror_project(src_dir: str, dst_dir: str) -> None:
    for root, dirs, files in os.walk(src_dir):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.endswith("-backups")]
        rel = os.path.relpath(root, src_dir)
        tgt = os.path.join(dst_dir, rel) if rel != "." else dst_dir
        os.makedirs(tgt, exist_ok=True)
        for f in files:
            if f.endswith(".lck"):
                continue
            _link_or_copy(os.path.join(root, f), os.path.join(tgt, f))


@contextlib.contextmanager
def materialize(sess, force: bool = False) -> Iterator[str]:
    """Yield a project directory reflecting the session state.

    Without dirty documents this is the real project directory (zero cost).
    """
    proj = sess.require_project()
    dirty = sess.dirty_docs()
    if not dirty and not force:
        yield proj.dir
        return
    td = kb.mkdtemp("ws")
    try:
        mirror_project(proj.dir, td)
        for d in dirty:
            try:
                rel = os.path.relpath(d.path, proj.dir)
            except ValueError:
                continue
            if rel == os.pardir or rel.startswith(os.pardir + os.sep):
                continue
            tgt = os.path.join(td, rel)
            os.makedirs(os.path.dirname(tgt), exist_ok=True)
            if os.path.islink(tgt) or os.path.exists(tgt):
                os.unlink(tgt)
            if getattr(d, "_deleted", False):
                continue
            with open(tgt, "w", encoding="utf-8", newline="\n") as f:  # KiCad files are LF on every OS
                f.write(d.text())
        yield td
    finally:
        shutil.rmtree(td, ignore_errors=True)


def remap(path: str, proj_dir: str, ws_dir: str) -> str:
    """Path of ``path`` inside the workspace mirror (unchanged if outside the project)."""
    ap = os.path.abspath(path)
    try:
        rel = os.path.relpath(ap, proj_dir)
    except ValueError:  # different drive on Windows
        return ap
    if rel == os.pardir or rel.startswith(os.pardir + os.sep):
        return ap
    return os.path.join(ws_dir, rel)
