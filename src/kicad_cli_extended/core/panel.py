"""Panelization: N x M copies of the project's board in a new, standalone panel board.

Two engines:

* ``builtin`` (pcbnew worker ``panelize`` op): grid of copies, nets renamed ``Board_<n>-<net>``
  so copies never short, tabs with mouse-bite NPTH holes (spacing > 0) or butted boards with
  V-score lines (spacing 0), optional rails with fiducials and tooling holes.  The panel outline
  is one polygon union (boards + tabs + rails) written to Edge.Cuts, so KiCad's DRC sees a
  valid board outline.
* ``kikit`` (when the ``kikit`` command is installed): the same options mapped to
  ``kikit panelize`` presets.

The source project is never modified; the panel gets its own ``.kicad_pro`` (copied, so design
rules and net classes carry over) next to the panel board.
"""

from __future__ import annotations

import json
import hashlib
import os
import shutil
import subprocess
from typing import Any, Dict, Optional

from . import checks as checks_mod
from .workspace import materialize, remap
from . import runtime
from ..utils import kicad_backend as kb
from ..utils.verify import describe_outputs


def _copy_project_files(proj, out_board: str) -> None:
    base = os.path.splitext(out_board)[0]
    for ext in (".kicad_pro", ".kicad_dru"):
        src = os.path.join(proj.dir, proj.name + ext)
        if os.path.exists(src):
            shutil.copyfile(src, base + ext)


def panelize(sess, rows: int, cols: int, spacing: float = 2.0, mode: Optional[str] = None, rails: str = "tb",
             rail_width: float = 5.0, tab_width: float = 3.0, tabs: Optional[int] = None, bite_drill: float = 0.5,
             bite_pitch: float = 0.8, fiducials: bool = True, tooling_holes: bool = True,
             vscore_layer: str = "Cmts.User", output: Optional[str] = None, engine: str = "builtin",
             drc: bool = False, strict: bool = False) -> Dict[str, Any]:
    proj = sess.require_project()
    if not os.path.exists(proj.pcb_path) and not sess.has_doc(proj.pcb_path):
        raise FileNotFoundError(f"No board {proj.pcb_path} to panelize.")
    out = os.path.abspath(output or os.path.join(proj.output_dir, "panel", f"{proj.name}-panel.kicad_pcb"))
    if not out.endswith(".kicad_pcb"):
        out = os.path.join(out, f"{proj.name}-panel.kicad_pcb")
    if os.path.realpath(out) == os.path.realpath(proj.pcb_path):
        raise ValueError("Panel output must be separate from the source PCB")
    warnings = []
    requested_spacing = spacing
    if mode == "vscore" and spacing != 0:
        warning = "V-score needs butted boards; spacing was changed to 0. For a gap use --mode mousebites"
        if strict:
            raise ValueError(warning)
        warnings.append(warning)
        spacing = 0
    with open(proj.pcb_path, "rb") as f:
        source_hash = hashlib.sha256(f.read()).hexdigest()
    os.makedirs(os.path.dirname(out), exist_ok=True)
    if engine == "kikit":
        res = _kikit(sess, out, rows, cols, spacing, mode, rails, rail_width, tab_width, tabs, bite_drill,
                     bite_pitch, fiducials, tooling_holes)
    elif engine == "builtin":
        with materialize(sess) as ws:
            res = kb.run_pcbnew("panelize", {
                "path": remap(proj.pcb_path, proj.dir, ws), "out": out, "rows": rows, "cols": cols,
                "spacing": spacing, "mode": mode, "rails": rails, "rail_width": rail_width, "tab_width": tab_width,
                "tabs": tabs, "bite_drill": bite_drill, "bite_pitch": bite_pitch, "fiducials": fiducials,
                "tooling_holes": tooling_holes, "vscore_layer": vscore_layer})
    else:
        raise ValueError("engine must be builtin or kikit")
    with open(proj.pcb_path, "rb") as f:
        if hashlib.sha256(f.read()).hexdigest() != source_hash:
            raise kb.KicadError("Panelization changed the source PCB; stop and restore it")
    res.update(warnings=warnings, spacing=spacing, requested_spacing=requested_spacing, source_sha256=source_hash)
    _copy_project_files(proj, out)
    # KiCad itself must load the result
    td = kb.mkdtemp("panel")
    try:
        kb.run_kicad_cli(["pcb", "export", "stats", "--format", "json", "-o", os.path.join(td, "s.json"), out])
        res["validated"] = "kicad-cli pcb export stats"
    finally:
        shutil.rmtree(td, ignore_errors=True)
    res["engine"] = engine
    res["outputs"] = describe_outputs(out, base=os.path.dirname(out))
    if drc:
        rep = os.path.join(os.path.dirname(out), os.path.basename(out)[:-10] + "-drc.json")
        kb.run_kicad_cli(["pcb", "drc", "--format", "json", "-o", rep, out], check=False)
        with open(rep, encoding="utf-8") as f:
            s = checks_mod.summarize(json.load(f), "drc")
        res["drc"] = {k: s[k] for k in ("errors", "warnings", "by_type", "clean")}
    res["hint"] = (f"Fab outputs for the panel: `kce --project {os.path.splitext(out)[0]}.kicad_pro fab package "
                   "--preset jlcpcb`")
    return res


def _kikit(sess, out, rows, cols, spacing, mode, rails, rail_width, tab_width, tabs, bite_drill, bite_pitch,
           fiducials, tooling_holes) -> Dict[str, Any]:
    exe = shutil.which("kikit")
    if not exe:
        raise RuntimeError("KiKit is not installed (`pip install kikit` into the Python that has KiCad's "
                                "pcbnew), or use the builtin engine: `kce pcb panelize --engine builtin`.")
    proj = sess.require_project()
    mode = mode or ("vscore" if spacing == 0 else "mousebites")
    framing = {"tb": "railstb", "lr": "railslr", "all": "frame", "none": "none"}[rails]
    args = [exe, "panelize",
            "--layout", f"grid; rows: {rows}; cols: {cols}; space: {spacing}mm",
            "--tabs", f"fixed; width: {tab_width}mm" + (f"; hcount: {tabs}; vcount: {tabs}" if tabs else ""),
            "--cuts", (f"mousebites; drill: {bite_drill}mm; spacing: {bite_pitch}mm" if mode == "mousebites"
                       else "vcuts"),
            "--framing", f"{framing}; width: {rail_width}mm; space: {spacing}mm" if framing != "none" else "none"]
    if fiducials and framing != "none":
        args += ["--fiducials", "3fid; hoffset: 12mm; voffset: 2.5mm; coppersize: 1mm; opening: 2mm"]
    if tooling_holes and framing != "none":
        args += ["--tooling", "4hole; hoffset: 5mm; voffset: 2.5mm; size: 2mm"]
    with materialize(sess) as ws:
        args += [remap(proj.pcb_path, proj.dir, ws), out]
        r = runtime.execute(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           stdin=subprocess.DEVNULL, timeout=1800)
    if r.returncode != 0 or not os.path.exists(out):
        raise RuntimeError(f"kikit panelize failed (exit {r.returncode}): {(r.stderr or r.stdout)[-2000:]}")
    return {"out": out, "rows": rows, "cols": cols, "mode": mode, "kikit_command": args}
