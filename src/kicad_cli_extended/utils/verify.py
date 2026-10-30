"""Output verification: identify produced files by content (magic bytes)."""

from __future__ import annotations

import os
import re
from pathlib import PurePath
import struct
import zipfile
from typing import Any, Dict, List, Optional


def sniff(path: str) -> str:
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
    except OSError:
        return "unreadable"
    low = path.lower()
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"PK\x03\x04"):
        return "zip"
    if head.startswith(b"glTF"):
        return "glb"
    if head.startswith(b"ISO-10303-21"):
        return "step"
    if head[:2] == b"\x1f\x8b":
        return "gzip"
    if head.startswith(b"%!PS"):
        return "postscript"
    if head.startswith(b"ply"):
        return "ply"
    if head.startswith(b"U3D\x00"):
        return "u3d"
    txt = head.decode("utf-8", errors="replace")
    stripped = txt.lstrip()
    if "%TF." in txt or stripped.startswith(("G04", "%FS", "%MO", "%TF")):
        return "gerber"
    if stripped.startswith("M48"):
        return "excellon"
    if "<svg" in txt:
        return "svg"
    if stripped.startswith("#VRML"):
        return "vrml"
    if stripped.startswith("solid") or (low.endswith(".stl") and len(head) >= 84):
        return "stl"
    if stripped.startswith("0\nSECTION") or stripped.startswith("0\r\nSECTION") or "SECTION\n  2\nHEADER" in txt \
            or stripped.startswith("999") or (low.endswith(".dxf") and "SECTION" in txt):
        return "dxf"
    if stripped.startswith(("DBRep_DrawableShape", "CASCADE Topology")) or low.endswith(".brep"):
        return "brep"
    if stripped.startswith("<?xml") or stripped.startswith("<"):
        return "xml"
    if stripped.startswith(("{", "[")):
        return "json"
    if stripped.startswith("(") and "kicad" in stripped[:80]:
        return "kicad-sexpr"
    if stripped.startswith("(export"):
        return "kicad-netlist"
    if stripped.startswith("$HEADER") or "GENCAD" in txt[:200]:
        return "gencad"
    if stripped.startswith("P  ") or stripped.startswith("C  ") or "IPC-D-356" in txt[:400]:
        return "ipc-d-356"
    first = stripped.splitlines()[0] if stripped else ""
    if low.endswith(".csv") or (first.count(",") >= 2 and not first.startswith(("(", "#", "*"))):
        return "csv"
    return "text"


def gerber_info(path: str) -> Dict[str, Any]:
    """Parse Gerber X2 header attributes (%TF.*%)."""
    attrs = {}
    with open(path, encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f):
            if i > 200:
                break
            line = line.strip()
            if line.startswith("%TF.") and line.endswith("*%"):
                body = line[4:-2]
                k, _, v = body.partition(",")
                attrs[k] = v
    return attrs


def output_base(output: str) -> str:
    """Directory that ``outputs[].path`` values are relative to."""
    trimmed = os.path.normpath(output) if output else output
    return trimmed if os.path.isdir(trimmed) else (os.path.dirname(os.path.abspath(trimmed)) or ".")


def describe_outputs(output: str, since: Optional[float] = None, base: Optional[str] = None) -> List[Dict[str, Any]]:
    """Files produced at ``output`` (a file or directory), identified by content.

    ``path`` is relative to ``base`` (default: :func:`output_base`), with forward slashes.
    Gerbers carry their X2 ``FileFunction`` as ``function``.
    """
    paths: List[str] = []
    trimmed = os.path.normpath(output) if output else output
    if os.path.isdir(trimmed):
        for r, _, files in os.walk(trimmed):
            for f in files:
                paths.append(os.path.join(r, f))
    elif os.path.exists(output):
        paths = [output]
    base = base or output_base(output)
    out = []
    for p in sorted(paths):
        st = os.stat(p)
        if since is not None and st.st_mtime < since:
            continue
        kind = sniff(p)
        try:
            rel = PurePath(os.path.relpath(p, base)).as_posix()
        except ValueError:  # other drive (Windows)
            rel = p
        d: Dict[str, Any] = {"path": rel, "bytes": st.st_size, "format": kind}
        if kind == "gerber":
            fn = gerber_info(p).get("FileFunction")
            if fn:
                d["function"] = fn
        if kind == "png":
            with open(p, "rb") as f:
                hdr = f.read(24)
            d["width"], d["height"] = struct.unpack(">II", hdr[16:24])
        if kind == "zip":
            try:
                with zipfile.ZipFile(p) as z:
                    d["entries"] = len(z.namelist())
            except zipfile.BadZipFile:
                d["format"] = "corrupt-zip"
        out.append(d)
    return out


_ROUTINE = re.compile(r"^(Plotted to |Done\.|Created file |Wrote |Saved |Successfully created |Export time |"
                      r"STEP file .* created|Generating |Loading |Found \d+ (violations|unconnected items))")


def notable_log(*texts: str, limit: int = 20) -> List[str]:
    """kicad-cli output lines worth showing an agent (routine progress lines dropped)."""
    lines = []
    for t in texts:
        for line in (t or "").splitlines():
            line = line.strip()
            if line and not _ROUTINE.match(line):
                lines.append(line)
    return lines[:limit] + ([f"... {len(lines) - limit} more lines"] if len(lines) > limit else [])
