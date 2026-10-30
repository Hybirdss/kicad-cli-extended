"""Board operations: orchestrates the pcbnew worker against session state.

Read operations run the worker on the materialised board; mutations run the
worker's batched ``edit`` op on a working copy and load the result back into
the session document (so undo/redo, --dry-run and validation all apply).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import uuid
from typing import Any, Dict, List, Optional

from . import library as lib_mod
from . import netlist as nl_mod
from . import project as proj_mod
from . import sexpr
from .workspace import materialize, remap
from . import runtime
from ..utils import kicad_backend as kb


def board_path(sess) -> str:
    proj = sess.require_project()
    if not os.path.exists(proj.pcb_path) and not sess.has_doc(proj.pcb_path):
        raise FileNotFoundError(
            f"No board {proj.pcb_path}. Create one with `kce pcb new` or `kce project new`.")
    return proj.pcb_path


def read(sess, op: str, **params) -> Any:
    proj = sess.require_project()
    path = board_path(sess)
    with materialize(sess) as ws:
        params["path"] = remap(path, proj.dir, ws)
        return kb.run_pcbnew(op, params)


def edit(sess, actions: List[Dict[str, Any]], description: str) -> List[Dict[str, Any]]:
    """Apply worker actions; result board text goes into the session doc."""
    from . import safety
    if any(a.get('action') == 'script' for a in actions) and (sess._defer_save or safety._sandbox):
        raise ValueError('Arbitrary Python cannot run inside a dry-run or transaction; use `kce raw pcbnew --help`.')
    proj = sess.require_project()
    path = board_path(sess)
    sess.snapshot(description, [path])
    thermal_check = any(a.get("action") in ("via_stitch", "via_fence") for a in actions)
    with materialize(sess, force=thermal_check) as ws:
        inp = remap(path, proj.dir, ws)
        out_dir = kb.mkdtemp("out")
        try:
            out = os.path.join(out_dir, "edited.kicad_pcb")
            res = kb.run_pcbnew("edit", {"path": inp, "out": out, "actions": actions})
            if thermal_check:
                _filter_thermal_vias(inp, out, res["results"])
            with open(out, encoding="utf-8") as f:
                text = f.read()
        finally:
            shutil.rmtree(out_dir, ignore_errors=True)
    sess.doc(path).set_text(text)
    return res["results"]


def _filter_thermal_vias(inp: str, out: str, results: List[Dict[str, Any]]) -> None:
    candidates = [v for r in results for v in r["result"].get("_new_vias", [])]
    if not candidates:
        for r in results:
            if r["result"].pop("_new_vias", None) is not None:
                r["result"]["rejected_thermal"] = 0
        return
    report = os.path.join(os.path.dirname(out), "thermal-drc.json")

    def thermals():
        if os.path.exists(report):
            os.unlink(report)
        kb.run_kicad_cli(["pcb", "drc", "--format", "json", "--units", "mm", "--refill-zones",
                          "-o", report, inp], check=False)
        if not os.path.exists(report):
            raise kb.KicadError("Via placement produced no DRC report; run `kce check drc`.")
        with open(report, encoding="utf-8") as f:
            data = json.load(f)
        return {tuple(sorted(it["uuid"] for it in v.get("items", [])))
                for v in data.get("violations", [])
                if v.get("type") == "starved_thermal" and not v.get("excluded", False)}

    baseline = thermals()
    with open(out, encoding="utf-8") as f:
        proposed = f.read()
    with open(inp, "w", encoding="utf-8", newline="\n") as f:
        f.write(proposed)
    if not (thermals() - baseline):
        for r in results:
            if r["result"].pop("_new_vias", None) is not None:
                r["result"]["rejected_thermal"] = 0
        return

    ids = [v["uuid"] for v in candidates]

    def evaluate(keep):
        with open(inp, "w", encoding="utf-8", newline="\n") as f:
            f.write(proposed)
        kb.run_pcbnew("edit", {"path": inp, "out": out, "actions": [
            {"action": "filter_vias", "candidates": ids, "keep": keep}]})
        shutil.copyfile(out, inp)
        return not (thermals() - baseline)

    # Via holes can change zone island connectivity and the number of usable thermal spokes.
    # Keep legal batches, splitting a failing batch down to the individual offending vias.
    accepted = []

    def accept(batch):
        if evaluate(accepted + batch):
            accepted.extend(batch)
        elif len(batch) > 1:
            mid = len(batch) // 2
            accept(batch[:mid])
            accept(batch[mid:])

    mid = len(ids) // 2
    if mid:
        accept(ids[:mid])
        accept(ids[mid:])
    if not evaluate(accepted):
        raise kb.KicadError("Via placement cannot preserve thermal connections; run `kce check drc`.")
    kept = set(accepted)
    for r in results:
        generated = r["result"].pop("_new_vias", None)
        if generated is not None:
            vias = [v["at"] for v in generated if v["uuid"] in kept]
            r["result"].update(added=len(vias), vias=vias[:200],
                               rejected_thermal=len(generated) - len(vias))


def edit1(sess, action: Dict[str, Any], description: str) -> Dict[str, Any]:
    return edit(sess, [action], description)[0]["result"]


# ---------------------------------------------------------------------------
# Board creation
# ---------------------------------------------------------------------------


def new_board(sess, copper_layers: int = 2, overwrite: bool = False) -> Dict[str, Any]:
    proj = sess.require_project()
    if os.path.exists(proj.pcb_path) and not overwrite:
        raise FileExistsError(f"{proj.pcb_path} exists (use --overwrite)")
    sess.snapshot("new board", [proj.pcb_path])
    td = kb.mkdtemp("board")
    try:
        tmp = os.path.join(td, proj.name + ".kicad_pcb")
        kb.run_pcbnew("new_board", {"path": tmp, "copper_layers": copper_layers})
        sess.doc(proj.pcb_path).set_text(open(tmp, encoding="utf-8").read())
    finally:
        shutil.rmtree(td, ignore_errors=True)
    return {"board": proj.pcb_path, "copper_layers": copper_layers}


# ---------------------------------------------------------------------------
# Footprint library resolution helpers
# ---------------------------------------------------------------------------


def resolve_fp_lib(proj, fpid: str) -> Dict[str, str]:
    lib, name = lib_mod.split_lib_id(fpid)
    pretty = proj_mod.resolve_library(proj, "fp", lib)
    if not os.path.exists(os.path.join(pretty, name + ".kicad_mod")):
        raise KeyError(f"Footprint {fpid} not found ({pretty}). Search with `kce lib fp search`.")
    return {"lib_path": pretty, "name": name, "lib_nickname": lib}


def add_footprint(sess, fpid: str, ref: str, x: float, y: float, rotation: float = 0, side: str = "top",
                  value: Optional[str] = None) -> Dict[str, Any]:
    info = resolve_fp_lib(sess.project, fpid)
    act = dict(info, action="add_fp", ref=ref, x=x, y=y, rotation=rotation, side=side,
               value=value if value is not None else info["name"])
    return edit1(sess, act, f"add footprint {ref} ({fpid})")


# ---------------------------------------------------------------------------
# Update PCB from schematic (netlist -> board)
# ---------------------------------------------------------------------------


def schematic_netlist(sess) -> Dict[str, Any]:
    proj = sess.require_project()
    if not os.path.exists(proj.sch_path) and not sess.has_doc(proj.sch_path):
        raise FileNotFoundError(f"No schematic {proj.sch_path}")
    with materialize(sess) as ws:
        sch = remap(proj.sch_path, proj.dir, ws)
        path = nl_mod.export_netlist(sch)
        try:
            return nl_mod.parse_netlist(path)
        finally:
            os.unlink(path)


#: namespace for `pcb update --stable-uuids` (uuid.NAMESPACE_URL, spelled out on purpose)
STABLE_UUID_NAMESPACE = uuid.UUID("6ba7b811-9dad-11d1-80b4-00c04fd430c8")


def stable_footprint_uuid(symbol_path: str) -> str:
    """Deterministic footprint uuid for a schematic symbol path (same on every run)."""
    return str(uuid.uuid5(STABLE_UUID_NAMESPACE, symbol_path))


def _apply_footprint_uuids(sess, path: str, wanted: Dict[str, str]) -> List[Dict[str, str]]:
    """Rewrite the uuid of footprints whose reference is in ``wanted`` (board text, lossless)."""
    tree = sexpr.loads(sess.current_text(path))
    changed = []
    for fp in tree.find_all("footprint"):
        ref = fp.get_property("Reference")
        want = wanted.get(ref)
        if not want:
            continue
        node = fp.find("uuid")
        if node is None:
            fp.append(sexpr.S("uuid", sexpr.Q(want)))
        elif str(node[1]) != want:
            node[1] = sexpr.Q(want)
            changed.append({"ref": ref, "uuid": want})
    sess.doc(path).set_text(sexpr.dumps(tree, sexpr.mode_for_path(path)))
    return changed


def unmatched_pins(proj, components: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Symbol pins whose number has no copper pad in the assigned footprint.

    Checked from the libraries, so it works before the board is updated and catches the
    symbol/footprint pin-map drift that a board DRC otherwise reports late.
    """
    rows = []
    for c in components:
        ref, fpid, lib_id = c.get("ref"), c.get("footprint"), c.get("lib_id")
        if not ref or ref.startswith("#") or not fpid or not lib_id:
            continue
        try:
            pads = lib_mod.footprint_info(proj, fpid)["pads"]
        except (KeyError, FileNotFoundError, ValueError):
            continue
        pad_numbers = set(lib_mod.copper_pad_numbers(pads))
        try:
            pins = lib_mod.symbol_info(proj, lib_id)["pins"]
        except (KeyError, FileNotFoundError, ValueError):
            continue
        seen = set()
        for pin in pins:
            number = pin["number"]
            if number in seen:
                continue
            seen.add(number)
            if number not in pad_numbers:
                rows.append({"ref": ref, "pin": number, "name": pin["name"]})
    rows.sort(key=lambda r: (nl_mod.natural_key(r["ref"]), nl_mod.natural_key(r["pin"])))
    return rows


def update_from_schematic(sess, delete_extra: bool = True, replace_footprints: bool = True,
                          place: bool = True, dry_run_report: bool = False,
                          stable_uuids: bool = False, lock_existing: bool = False) -> Dict[str, Any]:
    """KiCad 'Update PCB from Schematic' implemented on top of kicad-cli + pcbnew."""
    proj = sess.require_project()
    nl = schematic_netlist(sess)
    comps = []
    problems = []
    for c in nl["components"]:
        ref = c["ref"]
        if ref.startswith("#"):
            continue
        if ref.endswith("?"):
            problems.append({"ref": ref, "problem": "not annotated (run `kce sch annotate`)"})
        entry = {"ref": ref, "lib_id": f"{c['lib']}:{c['part']}" if c.get("lib") else "",
                 "value": c["value"], "footprint": c["footprint"],
                 "path": nl_mod.symbol_uuid_path(c), "fields": c["fields"],
                 "sheetname": c["sheet_names"], "sheetfile": c["properties"].get("Sheetfile", ""),
                 "dnp": c["dnp"], "exclude_from_bom": c["exclude_from_bom"],
                 "exclude_from_board": c["exclude_from_board"], "variants": c.get("variants") or {}}
        if c["footprint"]:
            try:
                info = resolve_fp_lib(proj, c["footprint"])
                entry["lib_path"], entry["fp_name"] = info["lib_path"], info["name"]
            except (KeyError, FileNotFoundError, ValueError) as e:
                entry["lib_error"] = str(e)
        comps.append(entry)
    nets = [{"name": n["name"], "nodes": n["nodes"]} for n in nl["nets"]]
    if not os.path.exists(proj.pcb_path) and not sess.has_doc(proj.pcb_path):
        new_board(sess)
    actions = [{"action": "update_from_netlist", "components": comps, "nets": nets, "variants": nl.get("variants") or [],
                "delete_extra": delete_extra, "replace_footprints": replace_footprints, "lock_existing": lock_existing}]
    results = edit(sess, actions, "update PCB from schematic")
    report = results[0]["result"]
    if stable_uuids:
        # a path that ends in "/" has no symbol uuid; hashing it would give every such part one id
        wanted = {c["ref"]: stable_footprint_uuid(c["path"]) for c in comps
                  if c.get("path") and not c["path"].endswith("/")}
        report["stable_uuids"] = {"namespace": str(STABLE_UUID_NAMESPACE),
                                  "written": _apply_footprint_uuids(sess, proj.pcb_path, wanted)}
    if place and report["added"]:
        added = [a["ref"] for a in report["added"]]
        try:
            pr = edit1(sess, {"action": "place_grid", "refs": added, "spacing": 1.0, "margin": 2.0},
                       "place new footprints")
            report["placed"] = pr
            report["unplaced_inside_outline"] = pr.get("unplaced_inside_outline") or []
            if report["unplaced_inside_outline"]:
                report["warning"] = (f"{len(report['unplaced_inside_outline'])} new footprint(s) do not fit "
                                     f"inside the outline ({', '.join(report['unplaced_inside_outline'])}); "
                                     "placed next to the board")
        except Exception as e:  # no outline yet: footprints stay near the origin
            report["placed"] = {"skipped": str(e).splitlines()[0]}
    report["would_place"] = [{"ref": f["reference"], "x": f["x"], "y": f["y"]}
                             for f in read(sess, "footprints") if f["reference"] in {c["ref"] for c in report["added"]}]
    report["schematic_problems"] = problems
    report["unmatched_pins"] = unmatched_pins(proj, comps)
    report["components"] = len(comps)
    report["nets"] = len(nets)
    return report


def score(sess, net: Optional[str] = None, bin_size: float = 5.0) -> Dict[str, Any]:
    """Measure the placed board (HPWL, courtyard overlaps, out-of-outline, crossings, congestion)."""
    return read(sess, "score", net=net, bin_size=bin_size)


def legalize(sess, clearance: float = 0.2, grid: float = 0.5, max_distance: float = 10.0, strict: bool = False, refs=None) -> Dict[str, Any]:
    """Move overlapping / out-of-outline footprints to the nearest legal spot (locked ones stay)."""
    return edit1(sess, {"action": "legalize", "clearance": clearance, "grid": grid,
                        "max_distance": max_distance, "strict": strict, "refs": refs}, "legalize placement")


def place_near(sess, ref: str, target: str, max_distance: float = 10.0, side: Optional[str] = None,
               clearance: float = 0.2, grid: float = 0.5, net=None, pad=None, rotation=None) -> Dict[str, Any]:
    """Put ``ref`` at the nearest legal spot to a footprint or pad ``target``."""
    if pad and (":" in target or "." in target):
        raise ValueError("Use --to REF --pad N or --to REF:N, not both")
    if pad:
        target += ":" + pad
    if net:
        target_ref = target.split(":")[0]
        footprint = read(sess, "footprint", ref=target_ref)
        candidates = [p for p in footprint["pads"] if p["net"] == net and (":" not in target or p["number"] == target.split(":", 1)[1])]
        if not candidates:
            raise ValueError(f"{target} has no pad on net {net!r}; see pcb footprint {target_ref}")
        moving = read(sess, "footprint", ref=ref)
        chosen = min(candidates, key=lambda p: ((p["x"] - moving["x"]) ** 2 + (p["y"] - moving["y"]) ** 2, p["number"]))
        target = target_ref + ":" + chosen["number"]
    a = {"action": "place_near", "ref": ref, "target": target, "rotation": rotation, "max_distance": max_distance,
         "clearance": clearance, "grid": grid}
    target_ref, sep, pin = target.partition(":") if ":" in target else target.partition(".")
    proj = sess.require_project()
    if sep and not pin.isdecimal() and (os.path.exists(proj.sch_path) or sess.has_doc(proj.sch_path)):
        pins = sorted({nd["pin"] for net in schematic_netlist(sess)["nets"] for nd in net["nodes"]
                       if nd["ref"] == target_ref and nd["pinfunction"] in (pin, f"{pin}_{nd['pin']}")})
        if len(pins) > 1:
            raise ValueError(f"Pin name {pin!r} is ambiguous on {target_ref}; use a pad number {pins}. "
                             f"See `kce pcb footprint {target_ref}`.")
        if pins:
            a["target_pad"] = pins[0]
    if side:
        a["side"] = side
    return edit1(sess, a, f"place {ref} near {target}")


# ---------------------------------------------------------------------------
# Specctra DSN / SES and autorouting
# ---------------------------------------------------------------------------


def export_dsn(sess, output: str) -> Dict[str, Any]:
    proj = sess.require_project()
    with materialize(sess) as ws:
        return kb.run_pcbnew("export_dsn", {"path": remap(board_path(sess), proj.dir, ws),
                                            "out": os.path.abspath(output)})


def import_ses(sess, ses: str) -> Dict[str, Any]:
    proj = sess.require_project()
    path = board_path(sess)
    sess.snapshot(f"import SES {os.path.basename(ses)}", [path])
    with materialize(sess) as ws:
        out_dir = kb.mkdtemp("out")
        try:
            out = os.path.join(out_dir, "routed.kicad_pcb")
            res = kb.run_pcbnew("import_ses", {"path": remap(path, proj.dir, ws), "ses": os.path.abspath(ses),
                                               "out": out})
            sess.doc(path).set_text(open(out, encoding="utf-8").read())
        finally:
            shutil.rmtree(out_dir, ignore_errors=True)
    return res


def autoroute(sess, engine: str = "builtin", **opts) -> Dict[str, Any]:
    if engine == "builtin":
        act = {"action": "autoroute_builtin"}
        act.update({k: v for k, v in opts.items() if v is not None})
        return edit1(sess, act, "autoroute (builtin)")
    if engine == "freerouting":
        fr = kb.find_freerouting()
        if not fr:
            raise RuntimeError(
                "Freerouting not found. Install Java 17+ and download freerouting.jar from "
                "https://github.com/freerouting/freerouting/releases, then set FREEROUTING_JAR=/path/freerouting.jar "
                "(or put `freerouting` on PATH). Alternatively use `--engine builtin`.")
        td = kb.mkdtemp("freerouting")
        try:
            dsn = os.path.join(td, "board.dsn")
            ses = os.path.join(td, "board.ses")
            export_dsn(sess, dsn)
            cmd = [fr["command"]] if "command" in fr else [fr["java"], "-jar", fr["jar"]]
            cmd += ["-de", dsn, "-do", ses, "-mp", str(opts.get("passes") or 20), "--gui.enabled=false"]
            r = runtime.execute(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=int(opts.get("timeout") or 1800),
                               stdin=subprocess.DEVNULL)
            if not os.path.exists(ses):
                raise RuntimeError(f"Freerouting produced no SES (exit {r.returncode}): {r.stderr[-2000:]}")
            res = import_ses(sess, ses)
            res["engine"] = "freerouting"
            return res
        finally:
            shutil.rmtree(td, ignore_errors=True)
    raise ValueError("engine must be builtin|freerouting")


# ---------------------------------------------------------------------------
# Raw stackup (sexpr: pcbnew doesn't wrap BOARD_STACKUP)
# ---------------------------------------------------------------------------


def stackup(sess) -> Dict[str, Any]:
    t = sess.tree(board_path(sess))
    setup = t.find("setup")
    st = setup.find("stackup") if setup is not None else None
    layers = []
    if st is not None:
        for l in st.find_all("layer"):
            layers.append({"name": str(l[1]), "type": l.value("type"), "thickness": l.fvalue("thickness"),
                           "material": l.value("material"), "epsilon_r": l.fvalue("epsilon_r"),
                           "color": l.value("color")})
    general = t.find("general")
    return {"thickness": general.fvalue("thickness") if general is not None else None,
            "layers": layers, "copper_finish": st.value("copper_finish") if st is not None else None,
            "board_layers": [{"id": int(c[0]) if str(c[0]).isdigit() else c[0], "name": str(c[1]), "type": str(c[2]),
                              "user_name": str(c[3]) if len(c) > 3 else None}
                             for c in (t.find("layers") or sexpr.S("layers")).children()]}
