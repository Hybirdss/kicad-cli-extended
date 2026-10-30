"""Import a foreign PCB (Altium, Eagle, CADSTAR, PADS, P-CAD, Fabmaster, SolidWorks PCB)
into a new KiCad project and report what came over.

The conversion is KiCad's own (``kicad-cli pcb import``).  Schematics of other tools have no
headless importer in KiCad 10 (kicad-cli and the Python API only read KiCad schematics), so
sibling schematic files are listed with the GUI path that imports them.
"""

from __future__ import annotations

import glob
import hashlib
import tempfile
from pathlib import Path
import json
import os
import re
from typing import Any, Dict, List, Optional

from . import project as proj_mod
from ..utils import kicad_backend as kb

# extension (lower case) -> kicad-cli --format and the tool it comes from
FORMATS = {
    ".pcbdoc": ("altium", "Altium Designer"), ".cmpcbdoc": ("altium", "Altium Circuit Maker"),
    ".cspcbdoc": ("altium", "Altium Circuit Studio"), ".swpcbdoc": ("solidworks", "SolidWorks PCB"),
    ".brd": ("eagle", "Eagle (or Allegro / Fabmaster)"), ".cpa": ("cadstar", "CADSTAR PCB archive"),
    ".asc": ("pads", "PADS ASCII"), ".pcb": ("pcad", "P-CAD (or older formats)"),
    ".alg": ("fabmaster", "Fabmaster"),
}
SCHEMATICS = {".schdoc": "Altium", ".sch": "Eagle/legacy", ".csa": "CADSTAR", ".asc": "LTspice/PADS",
              ".json": "EasyEDA"}



def source_inventory(directory: str, exclude: Optional[str] = None) -> Dict[str, str]:
    """Hash the source tree, including its membership, excluding only the destination subtree."""
    root = Path(directory).resolve()
    excluded = Path(exclude).resolve() if exclude else None
    result = {}
    for path in sorted(root.rglob("*")):
        if excluded and (path == excluded or excluded in path.parents):
            continue
        if path.is_file():
            result[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def import_into(argv: List[str], source: str, target: str, cwd: Optional[str] = None,
                exclude: Optional[str] = None, publish: bool = True):
    """Import to a fresh file, read it through pcbnew, then replace exactly the requested board."""
    source, target = os.path.abspath(source), os.path.abspath(target)
    if os.path.realpath(os.path.dirname(source)) == os.path.realpath(os.path.dirname(target)):
        raise ValueError("Import output must be outside the source directory; use project import --dir NEW or --output TARGET/board.kicad_pcb")
    if not os.path.isfile(source):
        raise FileNotFoundError(source)
    if exclude and Path(exclude).resolve() in Path(source).resolve().parents:
        exclude = None
    if "--report-file" in argv:
        report_path = Path(argv[argv.index("--report-file") + 1]).resolve()
        source_root = Path(source).parent.resolve()
        if source_root in report_path.parents and not (exclude and Path(exclude).resolve() in report_path.parents):
            raise ValueError("Import report must be outside the source tree")
    before = source_inventory(os.path.dirname(source), exclude)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".kce-import-", dir=os.path.dirname(target)) as stage:
        staged = os.path.join(stage, os.path.basename(target))
        args = list(argv)
        flag = "--output" if "--output" in args else "-o"
        args[args.index(flag) + 1] = staged
        result = kb.run_kicad_cli(args, check=False, cwd=cwd)
        after = source_inventory(os.path.dirname(source), exclude)
        if before != after:
            raise kb.KicadError("Import changed the source tree; stop and restore the source before retrying",
                                {"source_added": sorted(after.keys() - before.keys()),
                                 "source_removed": sorted(before.keys() - after.keys()),
                                 "source_changed": sorted(k for k in before.keys() & after.keys() if before[k] != after[k])})
        if result["returncode"] != 0:
            raise kb.KicadError(f"kicad-cli pcb import failed (exit {result['returncode']}): "
                                + (result["stderr"] or result["stdout"])[-1500:], result)
        if not os.path.isfile(staged) or not os.path.getsize(staged):
            raise kb.KicadError("Import reported success without producing the target board")
        info = kb.run_pcbnew("info", {"path": staged})
        digest = hashlib.sha256(Path(staged).read_bytes()).hexdigest()
        board_text = Path(staged).read_text(encoding="utf-8")
        if publish:
            os.replace(staged, target)
            read_back = kb.run_pcbnew("info", {"path": target})
            if read_back["footprints"] != info["footprints"] or hashlib.sha256(Path(target).read_bytes()).hexdigest() != digest:
                raise kb.KicadError("Imported target failed read-back assertion")
        else:
            read_back = info
    metadata = {"path": target, "sha256": digest, "footprints": read_back["footprints"],
                "source_unchanged": True, "source_files_checked": len(before), "scope": "disk" if publish else "session"}
    if not publish:
        metadata["_text"] = board_text
    return result, metadata

def import_board(src: str, directory: Optional[str] = None, name: Optional[str] = None, fmt: str = "auto",
                 overwrite: bool = False) -> Dict[str, Any]:
    src = os.path.abspath(src)
    if not os.path.isfile(src):
        raise FileNotFoundError(f"No such file: {src}")
    stem, ext = os.path.splitext(os.path.basename(src))
    name = re.sub(r"[^\w.\- ]+", "_", name or stem)
    directory = os.path.abspath(directory or os.path.join(os.path.dirname(src), name + "-kicad"))
    os.makedirs(directory, exist_ok=True)
    pro = os.path.join(directory, name + ".kicad_pro")
    pcb = os.path.join(directory, name + ".kicad_pcb")
    if (os.path.exists(pro) or os.path.exists(pcb)) and not overwrite:
        raise FileExistsError(f"{directory} already holds project {name} (use --overwrite or another --dir)")
    hint = FORMATS.get(ext.lower(), ("auto", None))
    if fmt == "auto" and hint[0] != "auto" and ext.lower() not in (".brd", ".pcb", ".asc"):
        fmt = hint[0]  # ambiguous extensions are left to KiCad's own detection
    report = os.path.join(directory, "outputs", "import-report.json")
    os.makedirs(os.path.dirname(report), exist_ok=True)
    res, read_back = import_into(["pcb", "import", "--format", fmt, "--report-format", "json", "--report-file", report,
                                 "-o", pcb, src], src, pcb, exclude=directory)
    data = json.load(open(os.path.join(kb.template_dir(), "kicad.kicad_pro"), encoding="utf-8"))
    data.setdefault("meta", {})["filename"] = name + ".kicad_pro"
    with open(pro, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    rep: Dict[str, Any] = {}
    if os.path.exists(report):
        with open(report, encoding="utf-8") as f:
            rep = json.load(f)
    info = kb.run_pcbnew("info", {"path": pcb})
    fps = kb.run_pcbnew("footprints", {"path": pcb})
    messages = [l.strip() for l in (res["stdout"] + "\n" + res["stderr"]).splitlines()
                if l.strip() and not l.startswith(("Importing", "Successfully"))]
    siblings: List[Dict[str, str]] = []
    for f in sorted(glob.glob(os.path.join(os.path.dirname(src), "*"))):
        e = os.path.splitext(f)[1].lower()
        if e in SCHEMATICS and os.path.abspath(f) != src:
            siblings.append({"file": f, "kind": SCHEMATICS[e] + " schematic"})
    out = {
        "project": proj_mod.KicadProject(pro).to_dict(), "source": src, "read_back": read_back,
        "source_format": rep.get("source_format") or hint[1], "kicad_format_hint": fmt,
        "board": {k: info[k] for k in ("copper_layers", "footprints", "tracks", "vias", "zones", "drawings", "nets",
                                       "unrouted", "outline_bbox", "thickness")},
        "footprint_libraries": sorted({f["fpid"].split(":")[0] for f in fps if ":" in f["fpid"]}),
        "import_statistics": rep.get("statistics"), "layer_mapping": rep.get("layer_mapping"),
        "import_errors": rep.get("errors") or [], "import_warnings": rep.get("warnings") or [],
        "messages": messages[:100], "report": report if os.path.exists(report) else None,
    }
    if siblings:
        out["not_imported"] = siblings
        out["not_imported_hint"] = ("KiCad 10 has no headless schematic importer; open the schematic in KiCad's "
                                    "schematic editor (File > Import > Non-KiCad Schematic) and save it as "
                                    f"{name}.kicad_sch in {directory}.")
    out["next"] = [f"kce --project {pro} pcb info", f"kce --project {pro} check drc --no-parity",
                   f"kce --project {pro} pcb models --missing"]
    return out
