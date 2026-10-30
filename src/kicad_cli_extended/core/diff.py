"""Revision comparison: schematic components & connectivity (via kicad-cli netlists)
and board footprints/tracks/zones (via the native S-expression files)."""

from __future__ import annotations

import collections
import json
import math
import os
import shutil
import subprocess
import zipfile
from pathlib import PurePath
from typing import Any, Dict, List, Optional, Tuple

from . import runtime
from ..utils import kicad_backend as kb
from . import netlist as nl_mod
from . import project as proj_mod
from . import sexpr


def checkout_revision(proj_dir: str, rev: str) -> Tuple[str, str]:
    """Extract ``git rev`` of the project directory. Returns (temp_root, project_dir_in_it)."""
    git = {"capture_output": True, "text": True, "encoding": "utf-8", "errors": "replace",
           "stdin": subprocess.DEVNULL}
    top = runtime.execute(["git", "-C", proj_dir, "rev-parse", "--show-toplevel"], timeout=kb.default_timeout(), **git)
    if top.returncode != 0:
        raise RuntimeError(f"{proj_dir} is not inside a git repository (needed for git:REV)")
    root = os.path.normpath(top.stdout.strip())
    rel = os.path.relpath(os.path.abspath(proj_dir), root)
    td = kb.mkdtemp("rev")
    arc = os.path.join(td, "rev.zip")
    pathspec = "." if rel == "." else PurePath(rel).as_posix()  # git wants forward slashes
    r = runtime.execute(["git", "-C", root, "archive", "--format=zip", "-o", arc, rev, "--", pathspec], timeout=kb.default_timeout(), **git)
    if r.returncode != 0:
        shutil.rmtree(td, ignore_errors=True)
        raise RuntimeError(f"git archive {rev} failed: {r.stderr.strip()}")
    with zipfile.ZipFile(arc) as z:
        z.extractall(td)
    os.unlink(arc)
    return td, (td if rel == "." else os.path.join(td, rel))


def resolve_side(spec: str, base_proj=None) -> Tuple[proj_mod.KicadProject, Optional[str]]:
    """``spec``: project path/dir/file, an archive .zip, or ``git:REV``. Returns (project, temp dir to remove)."""
    if spec.startswith("checkpoint:"):
        if base_proj is None:
            raise ValueError("checkpoint:NAME needs --project")
        from .plan import read_checkpoint, restore_files
        cp = read_checkpoint(base_proj.dir, spec.partition(':')[2],
                             state_dir=getattr(base_proj, 'session_state_dir', None))
        td = kb.mkdtemp('checkpoint')
        try:
            restore_files(td, cp['files'])
            return proj_mod.resolve_project(td), td
        except BaseException:
            shutil.rmtree(td, ignore_errors=True)
            raise
    if spec.startswith("git:"):
        if base_proj is None:
            raise ValueError("git:REV needs --project for the repository location")
        td, d = checkout_revision(base_proj.dir, spec[4:])
        pro = os.path.join(d, base_proj.name + ".kicad_pro")
        return proj_mod.resolve_project(pro if os.path.exists(pro) else d), td
    if spec.endswith(".zip") and os.path.isfile(spec):
        d = kb.mkdtemp("zip")
        proj_mod.unarchive(spec, d)
        return proj_mod.resolve_project(d), d
    return proj_mod.resolve_project(spec), None


def _netlist(proj) -> Optional[Dict[str, Any]]:
    if not proj.has_sch():
        return None
    p = nl_mod.export_netlist(proj.sch_path)
    try:
        return nl_mod.parse_netlist(p)
    finally:
        os.unlink(p)


def schematic_diff(a, b) -> Dict[str, Any]:
    na, nb = _netlist(a), _netlist(b)
    if na is None or nb is None:
        return {"available": False}
    ca = {c["ref"]: c for c in na["components"]}
    cb = {c["ref"]: c for c in nb["components"]}
    added = sorted(set(cb) - set(ca))
    removed = sorted(set(ca) - set(cb))
    changed = []
    for ref in sorted(set(ca) & set(cb)):
        x, y = ca[ref], cb[ref]
        diffs = {}
        for k in ("value", "footprint", "lib", "part"):
            if x.get(k) != y.get(k):
                diffs[k] = [x.get(k), y.get(k)]
        fx, fy = x.get("fields", {}), y.get("fields", {})
        for k in sorted(set(fx) | set(fy)):
            if fx.get(k) != fy.get(k):
                diffs[f"field:{k}"] = [fx.get(k), fy.get(k)]
        if x.get("dnp") != y.get("dnp"):
            diffs["dnp"] = [x.get("dnp"), y.get("dnp")]
        if diffs:
            changed.append({"ref": ref, "changes": diffs})
    pa = nl_mod.netlist_summary(na)["pin_to_net"]
    pb = nl_mod.netlist_summary(nb)["pin_to_net"]

    def norm(n):
        return "<unconnected>" if n and n.startswith("unconnected-") else n

    conn = []
    for pin in sorted(set(pa) | set(pb)):
        x, y = pa.get(pin), pb.get(pin)
        if norm(x) != norm(y):
            conn.append({"pin": pin, "before": x, "after": y})
    partitions_a = {n["name"]: {f"{p['ref']}:{p['pin']}" for p in n["nodes"]} for n in na["nets"]}
    partitions_b = {n["name"]: {f"{p['ref']}:{p['pin']}" for p in n["nodes"]} for n in nb["nets"]}
    net_changes = [{"net": name, "pins_added": sorted(partitions_b.get(name, set()) - partitions_a.get(name, set())),
                    "pins_removed": sorted(partitions_a.get(name, set()) - partitions_b.get(name, set()))}
                   for name in sorted(partitions_a.keys() | partitions_b.keys())
                   if partitions_a.get(name, set()) != partitions_b.get(name, set())]
    nets_a = {n["name"] for n in na["nets"] if not n["name"].startswith("unconnected-")}
    nets_b = {n["name"] for n in nb["nets"] if not n["name"].startswith("unconnected-")}
    return {"available": True, "components_added": added, "components_removed": removed,
            "components_changed": changed, "connection_changes": conn, "net_changes": net_changes,
            "nets_added": sorted(nets_b - nets_a), "nets_removed": sorted(nets_a - nets_b),
            "counts": {"before": {"components": len(ca), "nets": len(na["nets"])},
                       "after": {"components": len(cb), "nets": len(nb["nets"])}}}



def _geometry(node, names=None):
    """Canonical native geometry, with object UUIDs omitted and net codes resolved."""
    if not isinstance(node, sexpr.SList):
        return str(node)
    if node.tag == "net":
        return ["net", (names or {}).get(node.value(), node.value())]
    return [_geometry(child, names) for child in node
            if not isinstance(child, sexpr.SList) or child.tag not in ("uuid", "tstamp")]


def _fingerprint(nodes, names=None):
    return sorted(json.dumps(_geometry(node, names), separators=(",", ":")) for node in nodes)

def _board_facts(proj) -> Optional[Dict[str, Any]]:
    if not proj.has_pcb():
        return None
    t = sexpr.load(proj.pcb_path)
    names = {str(n[1]): str(n[2]) for n in t.find_all("net") if len(n) > 2}
    fps = {}
    for fp in t.find_all("footprint"):
        ref = fp.get_property("Reference") or "@" + str(fp.value("uuid", "unknown"))
        if ref in fps:
            raise ValueError(f"Duplicate footprint reference {ref}; ECO comparison requires unique refs")
        at = fp.find("at")
        native = sexpr.SList([child for child in fp if not isinstance(child, sexpr.SList) or child.tag not in ("at", "layer")])
        fps[ref] = {"fpid": str(fp[1]), "x": float(at[1]), "y": float(at[2]),
                    "rot": float(at[3]) if len(at) > 3 else 0.0, "layer": fp.value("layer"),
                    "native": _geometry(native, names), "value": fp.get_property("Value"), "locked": fp.find("locked") is not None
                    or "locked" in [str(a) for a in fp.atoms()]}
    tracks = collections.Counter()
    length = collections.defaultdict(float)
    routing_geometry = collections.defaultdict(list)
    for seg in t.find_all("segment") + t.find_all("arc"):
        net = names.get(seg.value("net", ""), seg.value("net", ""))
        routing_geometry[net].append(seg)
        s, e = seg.find("start"), seg.find("end")
        tracks[net] += 1
        length[net] += math.hypot(float(e[1]) - float(s[1]), float(e[2]) - float(s[2]))
    vias = collections.Counter(names.get(v.value("net", ""), v.value("net", "")) for v in t.find_all("via"))
    for via in t.find_all("via"):
        routing_geometry[names.get(via.value("net", ""), via.value("net", ""))].append(via)
    zones = sorted((z.value("net", ""), " ".join(str(a) for a in (z.find("layers") or z.find("layer") or sexpr.S("l")).atoms()))
                   for z in t.find_all("zone"))
    edge = [g for tag in ("gr_line", "gr_rect", "gr_arc", "gr_circle", "gr_poly") for g in t.find_all(tag)
            if g.value("layer") == "Edge.Cuts"]
    return {"footprints": fps, "tracks": tracks, "length": length, "vias": vias, "zones": zones,
            "edge_items": len(edge), "edge_geometry": _fingerprint(edge, names),
            "routing_geometry": {n: _fingerprint(items, names) for n, items in routing_geometry.items()},
            "zone_geometry": _fingerprint(t.find_all("zone"), names),
            "rules_geometry": _fingerprint([n for n in t.children() if n.tag in ("setup", "layers", "general", "netclass", "net_class")], names)}


def board_diff(a, b, move_tol: float = 0.001, geometry: bool = False) -> Dict[str, Any]:
    fa, fb = _board_facts(a), _board_facts(b)
    if fa is None or fb is None:
        return {"available": False}
    A, B = fa["footprints"], fb["footprints"]
    moved, changed = [], []
    for ref in sorted(set(A) & set(B)):
        x, y = A[ref], B[ref]
        d = math.hypot(y["x"] - x["x"], y["y"] - x["y"])
        if d > move_tol or abs(((y["rot"] - x["rot"]) + 180) % 360 - 180) > 1e-6 or x["layer"] != y["layer"]:
            moved.append({"ref": ref, "from": [x["x"], x["y"], x["rot"], x["layer"]],
                          "to": [y["x"], y["y"], y["rot"], y["layer"]], "distance": round(d, 4)})
        c = {k: [x[k], y[k]] for k in ("fpid", "value", "locked") if x[k] != y[k]}
        if x["native"] != y["native"]:
            c["details_changed"] = True
        if c:
            changed.append({"ref": ref, "changes": c})
    pad_refs = {c["ref"] for c in changed if "fpid" in c["changes"]}
    if geometry:
        pad_refs.update(m["ref"] for m in moved)
    if pad_refs:
        old = {f["reference"] or "@" + f["uuid"]: f for f in kb.run_pcbnew("footprints", {"path": a.pcb_path, "pads": True})}
        new = {f["reference"] or "@" + f["uuid"]: f for f in kb.run_pcbnew("footprints", {"path": b.pcb_path, "pads": True})}
        for ref in sorted(pad_refs):
            x = {p["number"]: p for p in old[ref]["pads"]}
            y = {p["number"]: p for p in new[ref]["pads"]}
            pads_moved = []
            for number in sorted(x.keys() | y.keys()):
                start = [x[number]["x"], x[number]["y"]] if number in x else None
                end = [y[number]["x"], y[number]["y"]] if number in y else None
                if start != end:
                    pads_moved.append({"pad": number, "from": start, "to": end,
                                       "dist_mm": round(math.dist(start, end), 6) if start and end else None})
            entry = next((c for c in changed if c["ref"] == ref), None)
            if entry is None:
                entry = {"ref": ref, "changes": {}}
                changed.append(entry)
            entry["pads_moved"] = pads_moved
    nets = sorted(set(fa["tracks"]) | set(fb["tracks"]) | set(fa["vias"]) | set(fb["vias"]))
    routing = []
    for n in nets:
        ta, tb = fa["tracks"].get(n, 0), fb["tracks"].get(n, 0)
        va, vb = fa["vias"].get(n, 0), fb["vias"].get(n, 0)
        la, lb = round(fa["length"].get(n, 0), 3), round(fb["length"].get(n, 0), 3)
        if (ta, va, la) != (tb, vb, lb) or fa["routing_geometry"].get(n) != fb["routing_geometry"].get(n):
            routing.append({"net": n, "segments": [ta, tb], "vias": [va, vb], "length_mm": [la, lb]})
    return {"available": True, "footprints_added": sorted(set(B) - set(A)),
            "footprints_removed": sorted(set(A) - set(B)), "footprints_moved": moved,
            "footprints_changed": changed, "routing_changes": routing,
            "zones": {"before": fa["zones"], "after": fb["zones"]} if fa["zones"] != fb["zones"] else None,
            "outline_changed": fa["edge_geometry"] != fb["edge_geometry"],
            "zones_changed": fa["zone_geometry"] != fb["zone_geometry"],
            "rules_changed": fa["rules_geometry"] != fb["rules_geometry"],
            "totals": {"segments": [sum(fa["tracks"].values()), sum(fb["tracks"].values())],
                       "vias": [sum(fa["vias"].values()), sum(fb["vias"].values())],
                       "footprints": [len(A), len(B)]}}



def unexpected_changes(result, allow):
    allowed = set(allow)
    changes = []
    s, b = result["schematic"], result["board"]
    for section in ("schematic", "board"):
        before, after = result["before"], result["after"]
        key = "sch" if section == "schematic" else "pcb"
        if bool(before.get(key)) != bool(after.get(key)):
            changes.append({"kind": section + "_availability", "ref": section})
    for key, kind in (("components_added", "component_added"), ("components_removed", "component_removed")):
        changes.extend({"kind": kind, "ref": ref} for ref in s.get(key, []))
    changes.extend({"kind": "component_changed", "ref": c["ref"]} for c in s.get("components_changed", []))
    changes.extend({"kind": "net_changed", "ref": "net:" + n["net"]} for n in s.get("net_changes", []))
    for key, kind in (("footprints_added", "footprint_added"), ("footprints_removed", "footprint_removed")):
        changes.extend({"kind": kind, "ref": ref} for ref in b.get(key, []))
    for key, kind in (("footprints_moved", "footprint_moved"), ("footprints_changed", "footprint_changed")):
        changes.extend({"kind": kind, "ref": c["ref"]} for c in b.get(key, []))
    changes.extend({"kind": "routing_changed", "ref": "net:" + r["net"]} for r in b.get("routing_changes", []))
    for key, token in (("zones_changed", "zones"), ("outline_changed", "outline"), ("rules_changed", "rules")):
        if b.get(key):
            changes.append({"kind": key, "ref": token})
    changes.extend({"kind": "project_file_changed", "ref": "project:" + name} for name in result.get("project_files_changed", []))
    return [c for c in changes if c["ref"] not in allowed]

def compare(before: str, after: str, base_proj=None, geometry: bool = False, allow=None) -> Dict[str, Any]:
    pa, ca = resolve_side(before, base_proj)
    pb, cb = resolve_side(after, base_proj)
    try:
        res = {"before": pa.to_dict(), "after": pb.to_dict(),
               "schematic": schematic_diff(pa, pb), "board": board_diff(pa, pb, geometry=geometry)}
        changed_project_files = []
        for filename in (pa.name + ".kicad_pro", pa.name + ".kicad_dru", "fp-lib-table", "sym-lib-table"):
            left = os.path.join(pa.dir, filename)
            right = os.path.join(pb.dir, pb.name + filename[len(pa.name):] if filename.startswith(pa.name + ".") else filename)
            def content(path):
                if not os.path.exists(path):
                    return None
                with open(path, encoding="utf-8") as f:
                    return json.load(f) if path.endswith(".kicad_pro") else f.read()
            if content(left) != content(right):
                changed_project_files.append(filename)
        res["project_files_changed"] = changed_project_files
        res["coverage"] = {"included": ["schematic components and net endpoints", "footprints", "routing geometry", "zones", "outline", "board rules/layers", "project JSON and rule/lib tables"],
                           "excluded": ["source-format equivalence", "external library contents", "models", "schematic drawing geometry"]}
        s, b = res["schematic"], res["board"]
        res["identical"] = (
            (not s.get("available") or not any(s.get(k) for k in ("components_added", "components_removed",
                                                                   "components_changed", "connection_changes")))
            and (not b.get("available") or not any(b.get(k) for k in (
                "footprints_added", "footprints_removed", "footprints_moved", "footprints_changed",
                "routing_changes", "zones", "zones_changed", "rules_changed")) and not b.get("outline_changed")))
        res["identical"] = res["identical"] and not changed_project_files and all(
            bool(res["before"].get(k)) == bool(res["after"].get(k)) for k in ("sch", "pcb"))
        res["unexpected"] = unexpected_changes(res, allow or []) if allow is not None else []
        res["ok"] = not res["unexpected"]
        return res
    finally:
        for c in (ca, cb):
            if c:
                shutil.rmtree(c, ignore_errors=True)
