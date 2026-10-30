"""Advanced board features on top of the pcbnew worker: 3D model reports, footprint updates
from the libraries, length/differential-pair rules and the KiCad 10 extras (variants,
component classes, net ties).

Geometry-heavy work (teardrops, stitching, fences, diff-pair routing, meanders) runs in the
worker (``utils/pcbnew_worker.py``); this module resolves paths and libraries, which need
the CLI's view of KiCad's path variables and library tables.
"""

from __future__ import annotations

import fnmatch
import os
from pathlib import PurePath
from typing import Any, Dict, List, Optional

from . import pcb as pcb_mod
from . import project as proj_mod
from ..utils import kicad_backend as kb
from ..utils.paths import rel_inside

MODEL_EXTS = (".step", ".stp", ".wrl", ".wrz", ".stpz", ".iges", ".igs")


# ---------------------------------------------------------------------------
# 3D models
# ---------------------------------------------------------------------------


def resolve_model_path(file: str, variables: Dict[str, str], project_dir: str) -> str:
    """Absolute path of a footprint's 3D model reference (KiCad's FILENAME_RESOLVER, simplified:
    path variables, then relative to the project)."""
    p = kb.expand_vars(file, variables)
    if not os.path.isabs(p):
        p = os.path.join(project_dir, p)
    return os.path.normpath(p)


def models_report(sess, missing_only: bool = False, ref: Optional[str] = None) -> Dict[str, Any]:
    proj = sess.require_project()
    variables = kb.kicad_env_vars(proj.dir)
    rows, missing, total = [], [], 0
    for fp in pcb_mod.read(sess, "models"):
        if ref and not fnmatch.fnmatchcase(fp["reference"], ref):
            continue
        models = []
        for m in fp["models"]:
            total += 1
            path = resolve_model_path(m["file"], variables, proj.dir)
            exists = os.path.isfile(path)
            entry = dict(m, path=path, exists=exists)
            if not exists:
                stem = os.path.splitext(path)[0]
                alts = [stem + e for e in MODEL_EXTS if os.path.isfile(stem + e)]
                if alts:
                    entry["alternatives"] = alts
                missing.append({"reference": fp["reference"], "file": m["file"], "path": path,
                                "alternatives": alts})
            models.append(entry)
        row = {"reference": fp["reference"], "fpid": fp["fpid"], "models": models}
        if not missing_only or any(not m["exists"] for m in models):
            rows.append(row)
    no_model = sorted(fp["reference"] for fp in rows if not fp["models"])
    res = {"footprints": rows, "models": total, "missing": len(missing), "missing_models": missing,
           "without_model": no_model if not missing_only else []}
    if missing:
        res["hint"] = ("Install KiCad's 3D model library (KICAD10_3DMODEL_DIR) or point the footprints at "
                       "existing files with `kce pcb model set REF FILE`.")
    return res


def model_file_ref(path: str, proj) -> str:
    """How a 3D model file is stored in the board: ``${KIPRJMOD}/...`` inside the project,
    ``${KICAD10_3DMODEL_DIR}/...`` inside KiCad's library, else as given (forward slashes)."""
    if "${" in path or "$(" in path:
        return path.replace("\\", "/")
    ap = os.path.abspath(path)
    rel = rel_inside(ap, proj.dir)
    if rel is not None:
        return "${KIPRJMOD}/" + PurePath(rel).as_posix()
    for name, root in kb.kicad_env_vars().items():
        if name.endswith("3DMODEL_DIR") and root and os.path.isdir(root):
            rel = rel_inside(ap, root)
            if rel is not None:
                return "${" + name + "}/" + PurePath(rel).as_posix()
    return PurePath(ap).as_posix()


def set_model(sess, ref: str, file: Optional[str], mode: str, index: Optional[int] = None,
              offset=None, rotation=None, scale=None, hide: Optional[bool] = None,
              opacity: Optional[float] = None, check: bool = True) -> Dict[str, Any]:
    proj = sess.require_project()
    act: Dict[str, Any] = {"action": "set_model", "ref": ref, "mode": mode, "index": index, "offset": offset,
                           "rotation": rotation, "scale": scale, "hide": hide, "opacity": opacity}
    if file:
        act["file"] = model_file_ref(file, proj)
        path = resolve_model_path(act["file"], kb.kicad_env_vars(proj.dir), proj.dir)
        if check and not os.path.isfile(path):
            raise FileNotFoundError(f"3D model file not found: {path} (from {act['file']}). Pass --no-check to "
                                    "store the reference anyway.")
    elif mode in ("replace", "add"):
        raise ValueError("Give the model FILE (or use --index with offset/rotation/scale options to adjust)")
    res = pcb_mod.edit1(sess, act, f"3D model {ref}")
    res["file_ref"] = act.get("file")
    return res


# ---------------------------------------------------------------------------
# Update footprints from library
# ---------------------------------------------------------------------------


def update_footprints(sess, refs: List[str], match: Optional[str] = None, reset_text: bool = False,
                      reset_models: bool = False, force: bool = False) -> Dict[str, Any]:
    proj = sess.require_project()
    items, problems = [], []
    for fp in pcb_mod.read(sess, "footprints"):
        r = fp["reference"]
        if refs and r not in refs:
            continue
        if match and not (fnmatch.fnmatchcase(r, match) or fnmatch.fnmatchcase(fp["fpid"], match)):
            continue
        if ":" not in fp["fpid"]:
            continue  # board-only footprint without a library (holes, fiducials made by kce)
        try:
            info = pcb_mod.resolve_fp_lib(proj, fp["fpid"])
        except (KeyError, FileNotFoundError, ValueError) as e:
            problems.append({"ref": r, "fpid": fp["fpid"], "problem": str(e).splitlines()[0]})
            continue
        items.append({"ref": r, "fpid": fp["fpid"], "lib_path": info["lib_path"], "fp_name": info["name"]})
    missing = sorted(set(refs) - {i["ref"] for i in items} - {p["ref"] for p in problems})
    if missing and refs:
        problems += [{"ref": r, "problem": "not on the board or has no library footprint"} for r in missing]
    if not items:
        return {"updated": [], "unchanged": [], "problems": problems}
    res = pcb_mod.edit1(sess, {"action": "update_footprints", "items": items, "reset_text": reset_text,
                               "reset_models": reset_models, "force": force}, "update footprints from library")
    res["problems"] = problems + res.get("problems", [])
    wanted = {i["ref"] for i in items}
    comps = []
    if os.path.exists(proj.sch_path) or sess.has_doc(proj.sch_path):
        comps = [{"ref": c["ref"], "lib_id": f"{c['lib']}:{c['part']}" if c.get("lib") else "",
                  "footprint": c["footprint"]}
                 for c in pcb_mod.schematic_netlist(sess)["components"] if c["ref"] in wanted]
    res["unmatched_pins"] = pcb_mod.unmatched_pins(proj, comps)
    return res


# ---------------------------------------------------------------------------
# Length / differential-pair rules (custom DRC rules in <project>.kicad_dru)
# ---------------------------------------------------------------------------


def _mm(v: float) -> str:
    return f"{v:g}mm"


def _range(kind: str, lo: Optional[float], opt: Optional[float], hi: Optional[float]) -> str:
    parts = [f"({k} {_mm(v)})" for k, v in (("min", lo), ("opt", opt), ("max", hi)) if v is not None]
    if not parts:
        raise ValueError(f"{kind}: give at least one of min/opt/max")
    return f"(constraint {kind} {' '.join(parts)})"


def length_rule(sess, name: str, nets: Optional[str], netclass: Optional[str], min_len: Optional[float],
                max_len: Optional[float], opt_len: Optional[float] = None,
                max_skew: Optional[float] = None) -> Dict[str, Any]:
    """Matched-length rule: KiCad's DRC checks routed length (and skew within the group)."""
    if not nets and not netclass:
        raise ValueError("Give --nets PATTERN or --netclass NAME")
    # KiCad rule expressions compare text with wildcards: A.NetName == '/DDR_D*'
    conds = ([f"A.NetName == '{nets}'"] if nets else []) + ([f"A.NetClass == '{netclass}'"] if netclass else [])
    cond = " && ".join(conds)
    constraints = []
    if any(v is not None for v in (min_len, opt_len, max_len)):
        constraints.append(_range("length", min_len, opt_len, max_len))
    if max_skew is not None:
        constraints.append(_range("skew", None, None, max_skew))
    if not constraints:
        raise ValueError("Give --min/--max/--opt length and/or --max-skew")
    return proj_mod.dru_add_rule(sess, name, constraints, cond)


def diffpair_rule(sess, name: str, pair: str, gap_min: Optional[float] = None, gap_opt: Optional[float] = None,
                  gap_max: Optional[float] = None, width: Optional[float] = None,
                  max_uncoupled: Optional[float] = None, max_skew: Optional[float] = None,
                  min_len: Optional[float] = None, max_len: Optional[float] = None) -> Dict[str, Any]:
    """Differential-pair rule (gap, width, uncoupled length, skew, length) for pairs matching ``pair``
    (the base name, wildcards allowed: '/USB_D', '/LVDS*')."""
    constraints = []
    if any(v is not None for v in (gap_min, gap_opt, gap_max)):
        constraints.append(_range("diff_pair_gap", gap_min, gap_opt, gap_max))
    if width is not None:
        constraints.append(_range("track_width", width, width, None))
    if max_uncoupled is not None:
        constraints.append(_range("diff_pair_uncoupled", None, None, max_uncoupled))
    if max_skew is not None:
        constraints.append(_range("skew", None, None, max_skew))
    if min_len is not None or max_len is not None:
        constraints.append(_range("length", min_len, None, max_len))
    if not constraints:
        raise ValueError("Give at least one of --gap/--width/--max-uncoupled/--max-skew/--min/--max")
    return proj_mod.dru_add_rule(sess, name, constraints, f"A.inDiffPair('{pair}')")


def diffpair_nets(sess, pair: str) -> Dict[str, str]:
    """P/N net names of a pair given its base name ('/USB_D') or one member ('/USB_D+')."""
    pairs = pcb_mod.read(sess, "diffpairs")
    for p in pairs:
        if pair in (p["pair"], p["p"], p["n"]):
            return {"p": p["p"], "n": p["n"], "pair": p["pair"]}
    raise KeyError(f"No differential pair {pair!r} on the board. Pairs: {[p['pair'] for p in pairs]} "
                   "(KiCad pairs nets ending in +/- or P/N). List them with `kce pcb diffpair list`.")
