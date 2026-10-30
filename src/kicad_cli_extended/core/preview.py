"""Previews rendered by the real kicad-cli, stored as immutable bundles.

Recipes
-------
* ``schematic`` – every sheet as SVG (``kicad-cli sch export svg``); hero = root sheet.
* ``board``     – board / front / back SVG (``kicad-cli pcb export svg --mode-single``).
* ``3d``        – ``kicad-cli pcb render`` PNGs: isometric (hero), top, bottom.
* ``quick``     – board SVG + 3D top PNG + schematic SVGs at low resolution.

Bundles live in ``<project>/<STATE_DIR>/previews/<recipe>/<bundle_id>/``
(see ``core/bundles.py``).  Live sessions keep ``session.json`` (current head)
and ``history.json`` (append-only) in ``<project>/<STATE_DIR>/previews/live/<recipe>/``.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import STATE_DIR
from . import bundles
from . import diff as diff_mod
from . import schematic as sch_mod
from .workspace import materialize, remap
from ..utils import kicad_backend as kb
from ..utils.verify import sniff

RECIPES = {
    "schematic": {"description": "All schematic sheets as SVG (kicad-cli sch export svg)", "needs": ["sch"],
                  "artifacts": ["hero: root sheet SVG", "gallery: sub-sheet SVGs"]},
    "board": {"description": "Board 2D plots as SVG (kicad-cli pcb export svg)", "needs": ["pcb"],
              "artifacts": ["hero: F.Cu+B.Cu+F.SilkS+B.SilkS+Edge.Cuts", "gallery: front, back"]},
    "3d": {"description": "3D raytrace renders (kicad-cli pcb render)", "needs": ["pcb"],
           "artifacts": ["hero: isometric PNG", "gallery: top, bottom PNG"]},
    "quick": {"description": "Cheap overview: board SVG + 3D top PNG + schematic SVGs", "needs": [],
              "artifacts": ["hero: board SVG (or schematic SVG)", "gallery: others"]},
}


def preview_root(sess, root_dir: Optional[str] = None) -> Path:
    proj = sess.require_project()
    return Path(root_dir) if root_dir else Path(proj.dir) / STATE_DIR / "previews"


def project_fingerprint(sess) -> str:
    proj = sess.require_project()
    h = hashlib.sha256()
    paths = {os.path.normcase(str(p)): str(p) for p in Path(proj.dir).glob("*.kicad_*")}
    for d in sess.docs.values():
        paths.setdefault(os.path.normcase(d.path), d.path)
    for key in sorted(paths):
        p = paths[key]
        if p.endswith(".kicad_prl"):
            continue
        txt = sess.current_text(p)
        if txt is not None:
            h.update(Path(p).name.encode("utf-8"))
            h.update(txt.encode("utf-8"))
    return "sha256:" + h.hexdigest()


# ---------------------------------------------------------------------------
# Renderers (all output produced by kicad-cli)
# ---------------------------------------------------------------------------


def _render_schematic(sch: str, art: Path, bdir: Path, artifacts: List[Dict], warnings: List[str]) -> None:
    out = art / "schematic"
    out.mkdir(parents=True, exist_ok=True)
    r = kb.run_kicad_cli(["sch", "export", "svg", "--exclude-drawing-sheet", "-o", str(out) + os.sep, sch],
                         check=False)
    if r["returncode"]:
        warnings.append("schematic SVG export failed: " + r["stderr"][-300:])
        return
    files = sorted(out.glob("*.svg"), key=lambda p: (len(p.name), p.name))
    for i, f in enumerate(files):
        artifacts.append(bundles.artifact(bdir, f, f"sch_{i}", "hero" if i == 0 else "gallery", "image",
                                          "Schematic: " + f.name, "image/svg+xml", view="schematic"))


def _render_board_svg(pcb: str, art: Path, bdir: Path, artifacts: List[Dict], warnings: List[str],
                      hero: bool = True, sides: bool = True) -> None:
    jobs = [("board", "F.Cu,B.Cu,F.SilkS,B.SilkS,Edge.Cuts", False, "Board (copper + silk)")]
    if sides:
        jobs += [("front", "F.Cu,F.SilkS,F.Mask,Edge.Cuts", False, "Front"),
                 ("back", "B.Cu,B.SilkS,B.Mask,Edge.Cuts", True, "Back (mirrored)")]
    for i, (name, layers, mirror, label) in enumerate(jobs):
        f = art / f"{name}.svg"
        args = ["pcb", "export", "svg", "--mode-single", "--layers", layers, "--page-size-mode", "2",
                "--exclude-drawing-sheet", "--drill-shape-opt", "1", "-o", str(f)]
        if mirror:
            args.append("--mirror")
        r = kb.run_kicad_cli(args + [pcb], check=False)
        if r["returncode"] or not f.exists():
            warnings.append(f"board SVG {name} failed: " + r["stderr"][-300:])
            continue
        role = "hero" if (i == 0 and hero) else "gallery"
        artifacts.append(bundles.artifact(bdir, f, f"pcb_{name}", role, "image", label, "image/svg+xml", view=name))


def _render_3d(pcb: str, art: Path, bdir: Path, artifacts: List[Dict], warnings: List[str],
               views: List[str], width: int, height: int, hero_view: str) -> None:
    for v in views:
        f = art / f"render_{v}.png"
        args = ["pcb", "render", "-o", str(f), "--width", str(width), "--height", str(height), "--quality", "basic"]
        if v == "iso":
            args += ["--side", "top", "--rotate", "-45,0,45", "--zoom", "0.9"]
        else:
            args += ["--side", v]
        r = kb.run_kicad_cli(args + [pcb], check=False)
        if r["returncode"] or not f.exists() or sniff(str(f)) != "png":
            warnings.append(f"3D render {v} failed: " + (r["stderr"] or r["stdout"])[-300:])
            continue
        artifacts.append(bundles.artifact(bdir, f, f"render_{v}", "hero" if v == hero_view else "gallery",
                                          "image", f"3D render ({v})", "image/png", width=width, height=height,
                                          view=v))


def _has(sess, path: str) -> bool:
    return os.path.exists(path) or sess.has_doc(path)


def _bundle_result(m: Dict[str, Any], cached: bool) -> Dict[str, Any]:
    return {"cached": cached, "status": m.get("status"), "bundle_id": m.get("id"), "recipe": m.get("recipe"),
            "bundle_dir": m["bundle_dir"], "manifest": m["manifest"], "created": m.get("created"),
            "artifact_count": len(m.get("artifacts", [])), "artifacts": m.get("artifacts", []),
            "summary": m.get("summary", {}), "warnings": m.get("warnings", [])}


def capture(sess, recipe: str = "quick", force: bool = False, root_dir: Optional[str] = None,
            width: int = 1280, height: int = 720, label: Optional[str] = None, command: str = "") -> Dict[str, Any]:
    if recipe not in RECIPES:
        raise ValueError(f"Unknown recipe {recipe!r}; available: {sorted(RECIPES)}")
    proj = sess.require_project()
    has_sch, has_pcb = _has(sess, proj.sch_path), _has(sess, proj.pcb_path)
    for need in RECIPES[recipe]["needs"]:
        if need == "sch" and not has_sch:
            raise FileNotFoundError("Recipe needs a schematic")
        if need == "pcb" and not has_pcb:
            raise FileNotFoundError("Recipe needs a board")
    root = preview_root(sess, root_dir)
    fp = project_fingerprint(sess)
    key = bundles.fingerprint({"source": fp, "recipe": recipe, "width": width, "height": height,
                               "schema": bundles.BUNDLE_SCHEMA})
    if not force:
        cached = bundles.find_cached(root, recipe, key)
        if cached:
            return _bundle_result(cached, True)
    b = bundles.new_bundle(root, recipe, key)
    bdir, art = b["bundle_dir"], b["artifacts_dir"]
    artifacts: List[Dict] = []
    warnings: List[str] = []
    started = time.time()
    with materialize(sess) as ws:
        sch, pcb = remap(proj.sch_path, proj.dir, ws), remap(proj.pcb_path, proj.dir, ws)
        if recipe == "schematic":
            _render_schematic(sch, art, bdir, artifacts, warnings)
        elif recipe == "board":
            _render_board_svg(pcb, art, bdir, artifacts, warnings)
        elif recipe == "3d":
            _render_3d(pcb, art, bdir, artifacts, warnings, ["iso", "top", "bottom"], width, height, "iso")
        else:
            if has_pcb:
                _render_board_svg(pcb, art, bdir, artifacts, warnings, hero=True, sides=False)
                _render_3d(pcb, art, bdir, artifacts, warnings, ["top"], min(width, 800), min(height, 450), "")
            if has_sch:
                before = len(artifacts)
                _render_schematic(sch, art, bdir, artifacts, warnings)
                if has_pcb:
                    for a in artifacts[before:]:
                        a["role"] = "gallery"
    facts: Dict[str, Any] = {"render_seconds": round(time.time() - started, 2)}
    if has_sch:
        t = sess.tree(proj.sch_path)
        facts["symbols"] = len([s for s in sch_mod.list_symbols(t) if not s["power"]])
        facts["wires"] = len(t.find_all("wire"))
    if has_pcb:
        t = sess.tree(proj.pcb_path)
        for tag, k in (("footprint", "footprints"), ("segment", "segments"), ("via", "vias"), ("zone", "zones")):
            facts[k] = len(t.find_all(tag))
    status = "ok" if artifacts and not warnings else ("partial" if artifacts else "error")
    summary = {"headline": f"{recipe} preview of {proj.name}: {len(artifacts)} artifact(s)", "facts": facts,
               "label": label}
    m = bundles.finalize(b, "capture", recipe, status,
                         source={"project": proj.pro_path, "fingerprint": fp,
                                 "unsaved_changes": bool(sess.dirty_docs())},
                         artifacts=artifacts, summary=summary, command=command,
                         backend={"kicad_version": kb.kicad_version()}, warnings=warnings)
    return _bundle_result(m, False)


def latest(sess, recipe: Optional[str] = None, root_dir: Optional[str] = None) -> Dict[str, Any]:
    m = bundles.find_latest(preview_root(sess, root_dir), recipe)
    if not m:
        raise FileNotFoundError(f"No preview bundle yet for recipe {recipe or '*'}; run `kce preview capture`.")
    res = _bundle_result(m, True)
    res["stale"] = m.get("source", {}).get("fingerprint") != project_fingerprint(sess)
    return res


def diff(sess, against: str, recipe: str = "board", root_dir: Optional[str] = None,
         command: str = "") -> Dict[str, Any]:
    """Diff bundle: before/after renders + structural diff.json."""
    proj = sess.require_project()
    other, cleanup = diff_mod.resolve_side(against, proj)
    try:
        structural = diff_mod.compare(other.pro_path if os.path.exists(other.pro_path) else other.dir,
                                      proj.pro_path if os.path.exists(proj.pro_path) else proj.dir, proj)
        key = bundles.fingerprint({"a": project_fingerprint(sess), "b": against, "t": time.time()})
        b = bundles.new_bundle(preview_root(sess, root_dir), f"diff-{recipe}", key)
        bdir, art = b["bundle_dir"], b["artifacts_dir"]
        artifacts: List[Dict] = []
        warnings: List[str] = []
        for role, p in (("before", other), ("after", proj)):
            sub = art / role
            sub.mkdir()
            tmp: List[Dict] = []
            if recipe in ("board", "quick") and p.has_pcb():
                _render_board_svg(p.pcb_path, sub, bdir, tmp, warnings, sides=False)
            if recipe in ("schematic", "quick") and p.has_sch():
                _render_schematic(p.sch_path, sub, bdir, tmp, warnings)
            if recipe == "3d" and p.has_pcb():
                _render_3d(p.pcb_path, sub, bdir, tmp, warnings, ["top"], 800, 450, "top")
            for a in tmp:
                a["role"] = role
                a["id"] = f"{role}_{a['id']}"
                artifacts.append(a)
        dj = bundles.write_json(art / "diff.json", structural)
        artifacts.append(bundles.artifact(bdir, dj, "diff", "diff", "json", "Structural diff", "application/json"))
        s, bd = structural["schematic"], structural["board"]
        facts = {"identical": structural["identical"],
                 "components_added": len(s.get("components_added", [])),
                 "components_removed": len(s.get("components_removed", [])),
                 "connection_changes": len(s.get("connection_changes", [])),
                 "footprints_moved": len(bd.get("footprints_moved", [])),
                 "routing_changes": len(bd.get("routing_changes", []))}
        summary = {"headline": f"Diff {against} -> working copy: "
                               + ("identical" if structural["identical"] else "changes found"), "facts": facts}
        m = bundles.finalize(b, "diff", f"diff-{recipe}", "ok" if not warnings else "partial",
                             source={"project": proj.pro_path, "against": against,
                                     "fingerprint": project_fingerprint(sess)},
                             artifacts=artifacts, summary=summary, command=command, warnings=warnings)
        res = _bundle_result(m, False)
        res["facts"] = facts
        res["diff"] = structural
        return res
    finally:
        if cleanup:
            shutil.rmtree(cleanup, ignore_errors=True)


# ---------------------------------------------------------------------------
# Live sessions
# ---------------------------------------------------------------------------


def _live_dir(sess, recipe: str, root_dir: Optional[str]) -> Path:
    return preview_root(sess, root_dir) / "live" / recipe


def _publish(sess, recipe: str, sdir: Path, reason: str, command: str, root_dir: Optional[str],
             stage: Optional[str] = None, note: Optional[str] = None) -> Dict[str, Any]:
    started = bundles.utc_now()
    cap = capture(sess, recipe=recipe, root_dir=root_dir, command=command)
    step = bundles.append_step(sdir, {"reason": reason, "command": command, "started": started,
                                      "finished": bundles.utc_now(), "bundle_id": cap["bundle_id"],
                                      "bundle_dir": cap["bundle_dir"], "manifest": cap["manifest"],
                                      "cached": cap["cached"], "status": cap["status"], "stage": stage,
                                      "note": note,
                                      "fingerprint": project_fingerprint(sess)})
    sp = sdir / "session.json"
    head = bundles.read_json(sp) if sp.exists() else {"schema": bundles.SESSION_SCHEMA,
                                                      "created": started, "recipe": recipe}
    head.update({"status": "active", "project": sess.project.pro_path, "updated": step["finished"],
                 "current": {"bundle_id": cap["bundle_id"], "bundle_dir": cap["bundle_dir"],
                             "manifest": cap["manifest"], "step": step["index"]},
                 "latest_reason": reason, "latest_command": command})
    bundles.write_json(sp, head)
    return status(sess, recipe, root_dir)


def live_start(sess, recipe: str = "quick", root_dir: Optional[str] = None, command: str = "") -> Dict[str, Any]:
    sdir = _live_dir(sess, recipe, root_dir)
    sdir.mkdir(parents=True, exist_ok=True)
    return _publish(sess, recipe, sdir, "start", command, root_dir)


def live_push(sess, recipe: str = "quick", root_dir: Optional[str] = None, command: str = "",
              stage: Optional[str] = None, note: Optional[str] = None) -> Dict[str, Any]:
    sdir = _live_dir(sess, recipe, root_dir)
    if not (sdir / "session.json").exists():
        raise FileNotFoundError("No live session; run `kce preview live start` first")
    return _publish(sess, recipe, sdir, "push", command, root_dir, stage, note)


def status(sess, recipe: str = "quick", root_dir: Optional[str] = None) -> Dict[str, Any]:
    """Cheap, read-only: current head + compact history summary."""
    sdir = _live_dir(sess, recipe, root_dir)
    sp = sdir / "session.json"
    if not sp.exists():
        return {"status": "none", "active": False, "session_dir": str(sdir)}
    head = bundles.read_json(sp)
    head.update({"active": head.get("status") == "active", "session_dir": str(sdir), "session_file": str(sp),
                 "history_file": str(sdir / "history.json"),
                 "history": bundles.history_summary(bundles.load_history(sdir))})
    return head


def live_stop(sess, recipe: str = "quick", root_dir: Optional[str] = None) -> Dict[str, Any]:
    sdir = _live_dir(sess, recipe, root_dir)
    sp = sdir / "session.json"
    if not sp.exists():
        raise FileNotFoundError("No live session to stop")
    head = bundles.read_json(sp)
    head["status"] = "stopped"
    head["stopped"] = bundles.utc_now()
    bundles.write_json(sp, head)
    return status(sess, recipe, root_dir)


def recipes() -> List[Dict[str, Any]]:
    return [dict(v, name=k) for k, v in RECIPES.items()]
