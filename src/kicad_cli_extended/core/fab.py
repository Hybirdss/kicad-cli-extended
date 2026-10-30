"""One-shot manufacturing package (gerbers + drill + pos + BOM + netlist + STEP + zip).

Every artefact is produced by the real kicad-cli; vendor presets only choose
kicad-cli options and post-format the CSVs (column names) the vendor expects.
"""

from __future__ import annotations

import csv
import ctypes
import io
import fnmatch
import json
import math
import hashlib
import subprocess
from pathlib import Path
import os
import re
import shutil
import time
import tempfile
import sys
import zipfile
from typing import Any, Dict, List, Optional

from .. import __version__
from . import runtime, sexpr
from . import checks as checks_mod
from . import bom as bom_mod
from .workspace import materialize, remap
from ..utils import kicad_backend as kb
from ..utils.verify import describe_outputs

PRESETS: Dict[str, Dict[str, Any]] = {
    "generic": {
        "description": "Gerber X2 + Excellon (separate PTH/NPTH) + drill map, CSV pos, grouped BOM, IPC-D-356, STEP",
        "gerber_args": ["--subtract-soldermask"], "x2": True,
        "drill_args": ["--format", "excellon", "--excellon-units", "mm", "--excellon-separate-th",
                       "--generate-map", "--map-format", "pdf"],
        "pos_args": ["--format", "csv", "--units", "mm", "--side", "both"],
        "bom_fields": "Reference,Value,Footprint,${QUANTITY},${DNP}", "bom_labels": "Refs,Value,Footprint,Qty,DNP",
        "bom_group": "Value,Footprint", "step": True, "ipc356": True, "netlist": True,
    },
    "jlcpcb": {
        "description": "JLCPCB: Protel names, no X2/netlist attributes, merged Excellon mm, JLC CPL + BOM columns",
        "gerber_args": ["--no-x2", "--no-netlist", "--subtract-soldermask", "--precision", "6"], "x2": False,
        "drill_args": ["--format", "excellon", "--excellon-units", "mm", "--excellon-zeros-format", "decimal",
                       "--excellon-oval-format", "alternate"],
        "pos_args": ["--format", "csv", "--units", "mm", "--side", "both", "--exclude-dnp"],
        "bom_fields": "Value,Reference,Footprint,LCSC", "bom_labels": "Comment,Designator,Footprint,LCSC Part #",
        "bom_group": "Value,Footprint,LCSC", "cpl": "jlcpcb", "step": False, "ipc356": False, "netlist": False,
    },
    "pcbway": {
        "description": "PCBWay: Protel names, RS-274X (no X2), Excellon mm separate PTH/NPTH, PCBWay pos + BOM",
        "gerber_args": ["--no-x2", "--subtract-soldermask"], "x2": False,
        "drill_args": ["--format", "excellon", "--excellon-units", "mm", "--excellon-separate-th"],
        "pos_args": ["--format", "csv", "--units", "mm", "--side", "both"],
        "bom_fields": "${ITEM_NUMBER},${QUANTITY},Reference,Value,Footprint,Manufacturer,MPN",
        "bom_labels": "Item #,Qty,Designator,Value,Package,Manufacturer,Mfg Part #",
        "bom_group": "Value,Footprint,MPN", "cpl": "pcbway", "step": True, "ipc356": False, "netlist": False,
    },
    "oshpark": {
        "description": "OSH Park: Gerber X2 + merged Excellon (they also accept the .kicad_pcb directly)",
        "gerber_args": [], "x2": True,
        "drill_args": ["--format", "excellon", "--excellon-units", "in"],
        "pos_args": None, "bom_fields": None, "step": False, "ipc356": False, "netlist": False,
    },
}


def _gerber_layers(pcb_path: str) -> List[str]:
    """Copper + technical layers actually enabled on the board."""
    from . import runtime, sexpr
    t = sexpr.load(pcb_path)
    names = [str(c[1]) for c in (t.find("layers") or sexpr.S("layers")).children() if len(c) > 1]
    wanted = [n for n in names if n.endswith(".Cu")]
    for n in ("F.Paste", "B.Paste", "F.SilkS", "B.SilkS", "F.Mask", "B.Mask", "Edge.Cuts"):
        if n in names:
            wanted.append(n)
    return wanted


def _jlc_cpl(src_csv: str, dst_csv: str, rotation_fix=None) -> int:
    rows = list(csv.DictReader(open(src_csv, encoding="utf-8")))
    with open(dst_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Designator", "Mid X", "Mid Y", "Layer", "Rotation"])
        for r in rows:
            rotation = float(r['Rot'])
            for pattern, offset in (rotation_fix or {}).items():
                if fnmatch.fnmatchcase(r["Package"], pattern) or fnmatch.fnmatchcase(r["Ref"], pattern):
                    rotation += offset
            w.writerow([r["Ref"], f"{float(r['PosX']):.4f}mm", f"{float(r['PosY']):.4f}mm",
                        "Top" if r["Side"].lower().startswith("t") else "Bottom", f"{rotation % 360:.2f}"])
    return len(rows)


def _pcbway_cpl(src_csv: str, dst_csv: str) -> int:
    rows = list(csv.DictReader(open(src_csv, encoding="utf-8")))
    with open(dst_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Designator", "Footprint", "Mid X", "Mid Y", "Layer", "Rotation", "Comment"])
        for r in rows:
            w.writerow([r["Ref"], r["Package"], r["PosX"], r["PosY"],
                        "T" if r["Side"].lower().startswith("t") else "B", r["Rot"], r["Val"]])
    return len(rows)


def _make_package(sess, preset: str = "generic", output_dir: Optional[str] = None, zip_name: Optional[str] = None,
                 run_drc: bool = True, fail_on_drc: bool = False, step: Optional[bool] = None,
                 extra_gerber_args: Optional[List[str]] = None, bom_fields: Optional[str] = None,
                 parts: Optional[List[Dict[str, Any]]] = None, rotation_fix=None) -> Dict[str, Any]:
    if preset not in PRESETS:
        raise ValueError(f"preset must be one of {sorted(PRESETS)}")
    P = dict(PRESETS[preset])
    if bom_fields:
        P["bom_fields"] = P["bom_labels"] = bom_fields
        P["bom_group"] = ",".join(f for f in bom_fields.split(",") if f != "Reference")
    elif P.get("bom_fields"):
        for field in ("MPN", "LCSC"):
            if field not in P["bom_fields"].split(",") and any(field in c["fields"] for c in parts or []):
                P["bom_fields"] += "," + field
                P["bom_labels"] += "," + field
                P["bom_group"] += "," + field
    proj = sess.require_project()
    if not os.path.exists(proj.pcb_path) and not sess.has_doc(proj.pcb_path):
        raise FileNotFoundError("Project has no board to fabricate")
    out = os.path.abspath(output_dir or os.path.join(proj.output_dir, f"fab-{preset}"))
    gdir = os.path.join(out, "gerbers")
    os.makedirs(gdir)
    started = time.time()
    res: Dict[str, Any] = {"preset": preset, "description": P["description"], "output_dir": out, "steps": [], "failed_steps": [], "complete": False}
    if run_drc:
        d = checks_mod.run_drc(sess, output=os.path.join(out, "drc.json"))
        res["drc"] = {k: d[k] for k in ("total", "errors", "warnings", "unconnected", "clean", "by_type")}
        res["drc"]["rules_preset"] = None
        if fail_on_drc and d["errors"]:
            res.update(failed_steps=["drc"], files=[],
                       next=["Run kce check diagnose, fix the reported DRC errors, then rerun fab package --fail-on-drc"])
            return res
    with materialize(sess) as ws:
        pcb = remap(proj.pcb_path, proj.dir, ws)
        sch = remap(proj.sch_path, proj.dir, ws)
        layers = ",".join(_gerber_layers(pcb))
        steps = [("gerbers", ["pcb", "export", "gerbers", "--layers", layers, "--check-zones", *P["gerber_args"],
                              *(extra_gerber_args or []), "-o", gdir + os.sep, pcb]),
                 ("drill", ["pcb", "export", "drill", *P["drill_args"], "-o", gdir + os.sep, pcb])]
        if P.get("pos_args"):
            steps.append(("pos", ["pcb", "export", "pos", *P["pos_args"], "-o",
                                  os.path.join(out, f"{proj.name}-pos.csv"), pcb]))
        if P.get("bom_fields"):
            steps.append(("bom", ["sch", "export", "bom", "--fields", P["bom_fields"], "--labels", P["bom_labels"],
                                  "--group-by", P["bom_group"], "--exclude-dnp", "--ref-range-delimiter", "",
                                  "-o", os.path.join(out, f"{proj.name}-bom.csv"), sch]))
        if P.get("ipc356"):
            steps.append(("ipc356", ["pcb", "export", "ipcd356", "-o", os.path.join(out, f"{proj.name}.d356"), pcb]))
        if P.get("netlist"):
            steps.append(("netlist", ["sch", "export", "netlist", "-o", os.path.join(out, f"{proj.name}.net"), sch]))
        if (P.get("step") if step is None else step):
            steps.append(("step", ["pcb", "export", "step", "--force", "--subst-models", "-o",
                                   os.path.join(out, f"{proj.name}.step"), pcb]))
        for name, args in steps:
            r = kb.run_kicad_cli(args, check=False)
            target = args[args.index("-o") + 1]
            outputs = describe_outputs(target)
            valid = bool(outputs) and all(f["bytes"] > 0 for f in outputs)
            if name == "gerbers":
                valid = sum(f["format"] == "gerber" and not f["path"].endswith("-drl_map.gbr")
                            for f in outputs) >= len(layers.split(","))
            elif name == "drill":
                valid = any(f["format"] == "excellon" and f["bytes"] > 0 for f in outputs)
            elif name in ("bom", "pos") and valid:
                with open(target, encoding="utf-8-sig", newline="") as f:
                    header = next(csv.reader(f), [])
                required = {"Ref", "PosX", "PosY", "Rot", "Side"} if name == "pos" else set(P["bom_labels"].split(","))
                valid = required.issubset(header)
            elif name == "step":
                valid = valid and outputs[0]["format"] == "step"
            res["steps"].append({"step": name, "returncode": r["returncode"], "outputs_valid": valid,
                                 "stderr": r["stderr"][-800:] if r["returncode"] else ""})
            if r["returncode"] != 0 or not valid:
                res["failed_steps"].append(name)
    if res["failed_steps"]:
        res["files"] = []
        return res
    pos_csv = os.path.join(out, f"{proj.name}-pos.csv")
    if P.get("cpl") and os.path.exists(pos_csv):
        cpl = os.path.join(out, f"{proj.name}-cpl-{preset}.csv")
        n = _jlc_cpl(pos_csv, cpl, rotation_fix) if P["cpl"] == "jlcpcb" else _pcbway_cpl(pos_csv, cpl)
        with open(pos_csv, encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        review = [{"ref": r["Ref"], "why": "polarised" if r["Ref"].startswith("D") else "connector" if r["Ref"].startswith("J") else "pin 1 orientation",
                   "kicad_rotation": float(r["Rot"]), "check_in_jlc_viewer": True}
                  for r in rows if r["Ref"].startswith(("D", "J", "U"))]
        res["cpl"] = {"path": os.path.basename(cpl), "rows": n, "review": review}
        res["next"] = ["Review CPL polarity and pin 1 orientation in the fab placement viewer before ordering"]
    bom_csv = os.path.join(out, f"{proj.name}-bom.csv")
    if os.path.exists(bom_csv):
        with open(bom_csv, encoding="utf-8-sig", newline="") as f:
            res["bom_rows"] = len(list(csv.DictReader(f)))
    zpath = os.path.join(out, zip_name or f"{proj.name}-{preset}-gerbers.zip")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(os.listdir(gdir)):
            z.write(os.path.join(gdir, f), f)
    files = describe_outputs(out, since=started - 1, base=out)
    res["files"] = files  # paths relative to output_dir
    with zipfile.ZipFile(zpath) as zf:
        entries = len(zf.namelist())
    res["zip"] = {"path": os.path.basename(zpath), "bytes": os.path.getsize(zpath), "entries": entries}
    res["gerber_layers"] = layers.split(",")
    res["summary"] = {
        "gerbers": sum(1 for f in files if f["format"] == "gerber" and f["path"].startswith("gerbers/")
                       and not f["path"].endswith("-drl_map.gbr")),
        "drill_files": sum(1 for f in files if f["format"] == "excellon"),
        "total_files": len(files),
    }
    res["complete"] = True
    return res



def _publish(stage: str, target: str) -> None:
    """Publish one completed generation. Never move the accepted generation out of the way."""
    if not os.path.lexists(target):
        os.replace(stage, target)
        return
    # Linux rename exchange atomically swaps non-empty directories on the same filesystem.
    # Elsewhere fail closed for existing directories; a fresh output directory is portable.
    if sys.platform.startswith("linux"):
        libc = ctypes.CDLL(None, use_errno=True)
        exchange = getattr(libc, "renameat2", None)
        if exchange is not None:
            if exchange(-100, os.fsencode(stage), -100, os.fsencode(target), 2) == 0:
                return
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code), target)
    raise FileExistsError(f"Atomic replacement of an existing package is unavailable here; use a fresh --output-dir: {target}")


def make_package(sess, preset: str = "generic", output_dir: Optional[str] = None, zip_name: Optional[str] = None,
                 run_drc: bool = True, fail_on_drc: bool = False, step: Optional[bool] = None,
                 extra_gerber_args: Optional[List[str]] = None, require_lcsc: bool = False,
                 bom_fields: Optional[str] = None, rotation_fix: Optional[str] = None,
                 manifest: bool = False, name_suffix: Optional[str] = None, require_rev: Optional[str] = None) -> Dict[str, Any]:
    """Stage, verify all requested exports, then atomically publish the complete package."""
    proj = sess.require_project()
    source = _source_revision(proj.dir)
    title = sess.tree(proj.sch_path).find("title_block") if proj.has_sch() or sess.has_doc(proj.sch_path) else None
    rev = title.value("rev", "") if title is not None else ""
    if require_rev is not None and rev.removeprefix("v") != require_rev.removeprefix("v"):
        return {"preset": preset, "complete": False, "failed_steps": ["revision"], "files": [],
                "revision": {"expected": require_rev, "actual": rev}}
    if name_suffix:
        tokens = {"rev": rev, "preset": preset, "tag": source.get("git_tag") or "", "commit": source.get("git_commit") or ""}
        suffix = name_suffix.format(**tokens)
        if any("{" + k + "}" in name_suffix and not tokens[k] for k in tokens):
            raise ValueError("--name-suffix requires a nonempty revision, tag or commit for each requested token")
        if not suffix or any(c in suffix for c in '/\\:*?"<>|'):
            raise ValueError("--name-suffix must be a nonempty filename suffix")
        zip_name = f"{proj.name}-{suffix}-gerbers.zip"
    target = os.path.abspath(output_dir or os.path.join(proj.output_dir, f"fab-{preset}"))
    if zip_name and (os.path.basename(zip_name) != zip_name or zip_name in (".", "..")):
        raise ValueError("zip_name must be a filename inside the package")
    # Never exchange a project/source directory for an output package.
    if Path(os.path.realpath(proj.dir)).is_relative_to(os.path.realpath(target)):
        raise ValueError("--output-dir must not contain the source project")
    if os.path.islink(target):
        raise ValueError("--output-dir must not be a symbolic link")
    corrections = {}
    if rotation_fix:
        if preset != "jlcpcb":
            raise ValueError("--rotation-fix currently supports the JLCPCB CPL")
        with open(rotation_fix, encoding="utf-8") as f:
            corrections = json.load(f)
        if not isinstance(corrections, dict) or any(not isinstance(k, str) or not isinstance(v, (int, float)) or isinstance(v, bool)
                                                  or not math.isfinite(v) for k, v in corrections.items()):
            raise ValueError("--rotation-fix must map reference/footprint globs to finite angle offsets")
    parts = bom_mod.components(sess)
    bom = bom_mod.assembly_report(parts)
    warnings = ["No fab rules preset is recorded; verify project rules show against the manufacturer capabilities"]
    if not run_drc:
        warnings.append("DRC was skipped; this package is not a design-rule approval")
    if bom["missing_lcsc"]:
        warnings.append("Missing LCSC fields: " + ", ".join(bom["missing_lcsc"]) + "; verify supplier choices with fab parts check")
    if bom["duplicate_values_differing_lcsc"]:
        warnings.append("Identical value/footprint groups have different LCSC parts; review the BOM")
    if require_lcsc and (bom["missing_lcsc"] or not parts):
        if not parts:
            warnings.append("No assembly components available")
        return {"preset": preset, "output_dir": target, "complete": False, "failed_steps": ["bom-check"],
                "files": [], "bom": bom, "warnings": warnings or ["No assembly components available"]}
    os.makedirs(os.path.dirname(target), exist_ok=True)
    stage = tempfile.mkdtemp(prefix="." + os.path.basename(target) + "-stage-", dir=os.path.dirname(target))
    try:
        result = _make_package(sess, preset, stage, zip_name, run_drc, fail_on_drc, step, extra_gerber_args, bom_fields, parts, corrections)
        bom["rows"] = result.pop("bom_rows", None)
        result.update(bom=bom, warnings=warnings)
        result["output_dir"] = target
        if result["complete"]:
            for entry in result["files"]:
                entry["sha256"] = hashlib.sha256(Path(stage, entry["path"]).read_bytes()).hexdigest()
            result["zip"]["sha256"] = next(f["sha256"] for f in result["files"] if f["path"] == result["zip"]["path"])
            if manifest:
                data = {"tool": "kce", "kce_version": __version__, "kicad_version": kb.kicad_version(),
                        "preset": preset, "source": source, "revision": rev, "drc": result.get("drc"), "files": result["files"]}
                path = Path(stage, "manifest.json")
                path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                entry = {"path": "manifest.json", "format": "json", "bytes": path.stat().st_size,
                         "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                result["files"].append(entry)
                result["manifest"] = entry
                result["summary"]["total_files"] = len(result["files"])
            _publish(stage, target)
        return result
    finally:
        if os.path.isdir(stage):
            shutil.rmtree(stage)


def _source_revision(directory):
    def git(*args):
        try:
            result = runtime.execute(["git", "-C", directory, *args], capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, timeout=10)
        except FileNotFoundError:
            return None
        return result.stdout.strip() if result.returncode == 0 else None
    commit = git("rev-parse", "HEAD")
    return {"git_commit": commit, "dirty": bool(git("status", "--porcelain")) if commit else None,
            "git_tag": git("describe", "--exact-match", "--tags", "HEAD") if commit else None}

def list_presets() -> List[Dict[str, Any]]:
    return [{"name": k, "description": v["description"], "x2": v["x2"], "step": v["step"],
             "bom_columns": {label: "field " + field for field, label in zip((v.get("bom_fields") or "").split(","),
                                                                                  (v.get("bom_labels") or "").split(",")) if field}}
            for k, v in PRESETS.items()]
