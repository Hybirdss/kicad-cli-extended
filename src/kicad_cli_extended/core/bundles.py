"""Preview bundle store.

A bundle is an immutable directory::

    <root>/<recipe>/<YYYYmmddTHHMMSSZ>_<key8>/
        manifest.json      # schema, status, source fingerprint, artifacts, summary
        artifacts/...      # files rendered by kicad-cli

Bundles are cached by a key derived from the project fingerprint, recipe and
options, so re-capturing an unchanged project is free.  A *live session* is a
mutable ``session.json`` (current head) plus an append-only ``history.json``.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .. import APP_NAME, __version__
from .session import atomic_write_text

BUNDLE_SCHEMA = f"{APP_NAME}.preview-bundle/1"
SESSION_SCHEMA = f"{APP_NAME}.preview-session/1"
HISTORY_SCHEMA = f"{APP_NAME}.preview-history/1"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fingerprint(data: Any) -> str:
    blob = json.dumps(data, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def write_json(path: Path, data: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(str(path), json.dumps(data, indent=2, ensure_ascii=False, default=str) + '\n')
    return path


def read_json(path: Path) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _manifests(root: Path) -> Iterable[Path]:
    if not root.is_dir():
        return []
    return sorted(root.glob("*/*/manifest.json")) + sorted(root.glob("*/manifest.json"))


def _with_paths(manifest: Dict[str, Any], mpath: Path) -> Dict[str, Any]:
    out = dict(manifest)
    out["bundle_dir"] = str(mpath.parent)
    out["manifest"] = str(mpath)
    return out


def find_cached(root: Path, recipe: str, cache_key: str) -> Optional[Dict[str, Any]]:
    for mp in _manifests(root / recipe):
        try:
            m = read_json(mp)
        except (OSError, ValueError):
            continue
        if m.get("schema") == BUNDLE_SCHEMA and m.get("cache_key") == cache_key and m.get("status") in ("ok", "partial"):
            return _with_paths(m, mp)
    return None


def find_latest(root: Path, recipe: Optional[str] = None) -> Optional[Dict[str, Any]]:
    best = None
    for mp in _manifests(root / recipe if recipe else root):
        try:
            m = read_json(mp)
        except (OSError, ValueError):
            continue
        if m.get("schema") != BUNDLE_SCHEMA or m.get("status") not in ("ok", "partial"):
            continue
        if best is None or m.get("created", "") >= best[0].get("created", ""):
            best = (m, mp)
    return _with_paths(*best) if best else None


def new_bundle(root: Path, recipe: str, cache_key: str) -> Dict[str, Any]:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    bid = f"{stamp}_{cache_key.split(':', 1)[-1][:8]}"
    bdir = root / recipe / bid
    (bdir / "artifacts").mkdir(parents=True, exist_ok=False)
    return {"id": bid, "bundle_dir": bdir, "artifacts_dir": bdir / "artifacts", "cache_key": cache_key}


def artifact(bundle_dir: Path, path: Path, artifact_id: str, role: str, kind: str, label: str,
             media_type: Optional[str] = None, **extra: Any) -> Dict[str, Any]:
    path = Path(path)
    rec: Dict[str, Any] = {
        "id": artifact_id, "role": role, "kind": kind, "label": label,
        "media_type": media_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        "path": path.resolve().relative_to(Path(bundle_dir).resolve()).as_posix(),
    }
    if path.exists():
        rec["bytes"] = path.stat().st_size
    rec.update({k: v for k, v in extra.items() if v is not None})
    return rec


def finalize(bundle: Dict[str, Any], kind: str, recipe: str, status: str, source: Dict[str, Any],
             artifacts: List[Dict[str, Any]], summary: Dict[str, Any], command: str,
             backend: Optional[Dict[str, Any]] = None, warnings: Optional[List[str]] = None) -> Dict[str, Any]:
    manifest = {
        "schema": BUNDLE_SCHEMA, "id": bundle["id"], "kind": kind, "recipe": recipe, "status": status,
        "created": utc_now(), "cache_key": bundle["cache_key"],
        "tool": {"name": APP_NAME, "version": __version__, "command": command, **(backend or {})},
        "source": source, "summary": summary, "warnings": warnings or [], "artifacts": artifacts,
    }
    mp = write_json(Path(bundle["bundle_dir"]) / "manifest.json", manifest)
    return _with_paths(manifest, mp)


# ---------------------------------------------------------------------------
# Live sessions
# ---------------------------------------------------------------------------


def load_history(session_dir: Path) -> Dict[str, Any]:
    p = session_dir / "history.json"
    if not p.exists():
        return {"schema": HISTORY_SCHEMA, "steps": []}
    return read_json(p)


def append_step(session_dir: Path, step: Dict[str, Any]) -> Dict[str, Any]:
    hist = load_history(session_dir)
    steps = hist.setdefault("steps", [])
    step = dict(step, index=len(steps) + 1)
    steps.append(step)
    hist["updated"] = step.get("finished", utc_now())
    write_json(session_dir / "history.json", hist)
    return step


def history_summary(hist: Dict[str, Any], recent: int = 3) -> Dict[str, Any]:
    steps = hist.get("steps", [])
    last = steps[-1] if steps else {}
    return {"steps": len(steps), "latest_command": last.get("command"), "latest_reason": last.get("reason"),
            "latest_bundle": last.get("bundle_id"),
            "recent": [{k: s.get(k) for k in ("index", "reason", "stage", "bundle_id", "finished")}
                       for s in steps[-recent:]]}
