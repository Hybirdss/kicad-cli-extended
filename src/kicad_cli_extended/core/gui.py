"""Live control of a running KiCad GUI through its IPC API (``kicad-python`` / ``kipy``).

Optional: needs the ``kicad-python`` package in kce's environment (``pip install
kicad-python``, or the ``gui`` extra) and a KiCad 10 GUI with *Preferences > Plugins > Enable
KiCad API* turned on.  Only the project manager (``kicad``) hosts the API server; editors
started from it are reachable, the standalone ``pcbnew`` / ``eeschema`` programs are not.  KiCad listens on ``ipc:///tmp/kicad/api.sock`` (Windows
``%TEMP%\\kicad\\api.sock``; Flatpak under ``~/.var/app/org.kicad.KiCad/cache/tmp``) or on
``$KICAD_API_SOCKET``.

Typical loop for an agent: edit files with kce, then ``kce gui reload`` so the open editor
shows the change (KiCad re-reads the board from disk), inspect ``kce gui selection`` for what
the user is pointing at, run GUI actions with ``kce gui action``.
"""

from __future__ import annotations

import os
import sys
import tempfile
from typing import Any, Dict, List, Optional

INSTALL = "pip install kicad-python"
ENABLE = ("Start the KiCad 10 project manager (the standalone pcbnew/eeschema programs do not serve the API), "
          "enable Preferences > Plugins > 'Enable KiCad API', open the project and its PCB editor. "
          "Without a GUI use the file-based commands (they need no running KiCad).")


def _kipy():
    try:
        import kipy  # noqa: F401
        from kipy import KiCad
        return KiCad
    except ImportError as e:
        raise RuntimeError(f"The live GUI commands need the optional kicad-python package: run `{INSTALL}` in the "
                           "environment kce is installed in (uv: `uv tool install --with kicad-python "
                           f"kicad-cli-extended`). ({e})") from e


def socket_path() -> str:
    """Where KiCad's API server listens (KICAD_API_SERVER::Start): ``$KICAD_API_SOCKET``, else
    ``<temp dir>/kicad/api.sock`` (macOS: ``/tmp``; Flatpak: the sandbox's cache tmp)."""
    if os.environ.get("KICAD_API_SOCKET"):
        return os.environ["KICAD_API_SOCKET"]
    if sys.platform == "darwin":
        return "ipc:///tmp/kicad/api.sock"
    if os.name == "nt":
        return f"ipc://{tempfile.gettempdir()}\\kicad\\api.sock"
    flat = os.path.join(os.path.expanduser("~"), ".var", "app", "org.kicad.KiCad", "cache", "tmp", "kicad", "api.sock")
    if os.path.exists(flat):
        return f"ipc://{flat}"
    return f"ipc://{os.path.join(tempfile.gettempdir(), 'kicad', 'api.sock')}"


def availability() -> Dict[str, Any]:
    """Cheap check for `kce info doctor` (no connection attempt)."""
    try:
        import kipy
        ver = getattr(kipy, "__version__", None)
        if ver is None:
            from importlib.metadata import version
            ver = version("kicad-python")
        return {"kicad_python": ver, "socket": socket_path()}
    except Exception:
        return {"kicad_python": None, "hint": f"optional: `{INSTALL}` for `kce gui ...`"}


def connect(timeout_ms: int = 3000):
    KiCad = _kipy()
    sock = socket_path()
    try:
        k = KiCad(socket_path=sock, client_name="kce", timeout_ms=timeout_ms)
        k.ping()
        return k
    except Exception as e:
        raise RuntimeError(f"No KiCad GUI answering on {sock} ({type(e).__name__}: {e}). {ENABLE} "
                           "Check with `kce gui status`.") from e


def _docs(k, kind) -> List[Dict[str, Any]]:
    from kipy.proto.common.types import DocumentType
    out = []
    try:
        for d in k.get_open_documents(getattr(DocumentType, kind)):
            e = {"type": kind.replace("DOCTYPE_", "").lower()}
            if d.board_filename:
                e["file"] = d.board_filename
            if getattr(d, "project", None) is not None and d.project.path:
                e["project"] = d.project.path
                e["project_name"] = d.project.name
            out.append(e)
    except Exception:
        pass
    return out


def status() -> Dict[str, Any]:
    res = availability()
    if not res.get("kicad_python"):
        res["connected"] = False
        res["error"] = f"kicad-python not installed (`{INSTALL}`)"
        return res
    try:
        k = connect()
    except RuntimeError as e:
        res.update({"connected": False, "error": str(e)})
        return res
    v = k.get_version()
    res.update({"connected": True, "kicad_version": v.full_version,
                "documents": _docs(k, "DOCTYPE_PCB") + _docs(k, "DOCTYPE_SCHEMATIC")})
    return res


def _board(k):
    try:
        return k.get_board()
    except Exception as e:
        raise RuntimeError(f"KiCad has no board open in the PCB editor ({e}). Open it, then retry "
                           "(`kce gui status` lists open documents).") from e


def _xy(v) -> Optional[List[float]]:
    try:
        return [round(v.x / 1e6, 6), round(v.y / 1e6, 6)]
    except AttributeError:
        return None


def describe(item) -> Dict[str, Any]:
    """Compact, JSON-safe summary of a kipy board item."""
    e: Dict[str, Any] = {"type": type(item).__name__}
    try:
        e["uuid"] = item.id.value
    except AttributeError:
        pass
    for attr, key in (("reference_field", "reference"), ("value_field", "value")):
        f = getattr(item, attr, None)
        if f is not None:
            try:
                e[key] = f.text.value
            except AttributeError:
                pass
    net = getattr(item, "net", None)
    if net is not None and getattr(net, "name", None) is not None:
        e["net"] = net.name
    for attr in ("position", "start", "end"):
        if hasattr(item, attr):
            p = _xy(getattr(item, attr))
            if p:
                e[attr] = p
    for attr in ("layer", "number", "name"):
        v = getattr(item, attr, None)
        if isinstance(v, (str, int)) and not isinstance(v, bool):
            e[attr] = v
    return e


def selection() -> Dict[str, Any]:
    b = _board(connect())
    items = list(b.get_selection())
    return {"count": len(items), "items": [describe(i) for i in items[:500]]}


def select(refs: List[str], clear: bool = False) -> Dict[str, Any]:
    b = _board(connect())
    if clear:
        b.clear_selection()
    if not refs:
        return {"selected": 0}
    fps = [f for f in b.get_footprints() if f.reference_field.text.value in refs]
    missing = sorted(set(refs) - {f.reference_field.text.value for f in fps})
    if fps:
        b.add_to_selection(fps)
    return {"selected": len(fps), "missing": missing}


def reload() -> Dict[str, Any]:
    """Make the open PCB editor re-read the board file from disk (after kce edited it)."""
    b = _board(connect())
    b.revert()
    return {"reloaded": True, "board": getattr(b, "name", None)}


def save() -> Dict[str, Any]:
    b = _board(connect())
    b.save()
    return {"saved": True, "board": getattr(b, "name", None)}


def refill() -> Dict[str, Any]:
    b = _board(connect())
    b.refill_zones()
    return {"refilled": True}


def action(name: str) -> Dict[str, Any]:
    k = connect()
    try:
        r = k.run_action(name)
    except Exception as e:
        raise RuntimeError(f"KiCad rejected action {name!r}: {e}. Action names look like "
                           "'pcbnew.InteractiveSelection.ClearSelection' or 'common.Control.zoomFitScreen'.") from e
    return {"action": name, "status": str(getattr(r, "status", r)).strip() or "ok"}
