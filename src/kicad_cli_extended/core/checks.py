"""ERC / DRC through the real kicad-cli, with structured summaries and fix hints."""

from __future__ import annotations

import collections
import json
import os
import re
import tempfile
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

from .workspace import materialize, remap
from ..utils import kicad_backend as kb

# Actionable guidance per KiCad violation key (erc/drc "type").
HINTS = {
    # ERC
    "pin_not_connected": "Wire the pin (`kce sch connect`), label it (`kce sch label-pin`) or mark it unused (`kce sch no-connect REF:PIN`). For many pins: `kce sch no-connect --all-unconnected --ref REF`.",
    "power_pin_not_driven": "Power input pins need a driver: add a PWR_FLAG on that net (`kce sch power PWR_FLAG --at REF:PIN`) or a power-output pin.",
    "pin_not_driven": "An input pin has no driving output on its net; connect a driver or change pin types.",
    "pin_to_pin": "Conflicting pin types on one net (e.g. two outputs). Check the net with `kce sch net-of REF:PIN`.",
    "label_dangling": "A label is not attached to a wire/pin. Move it onto a wire end or delete it (`kce sch items --type label`, then `kce sch delete-item UUID`).",
    "isolated_pin_label": "A label connects to only one pin; another pin must share this net name.",
    "wire_dangling": "A wire end is unconnected; extend it to a pin or delete it.",
    "unconnected_wire_endpoint": "A wire end is unconnected; extend it to a pin or delete it.",
    "endpoint_off_grid": "Symbols/wires are off the 1.27 mm grid; move with `kce sch move` (snaps by default).",
    "duplicate_reference": "Two symbols share a reference; run `kce sch annotate --mode all`.",
    "unannotated": "Symbols still have '?' references; run `kce sch annotate`.",
    "lib_symbol_issues": "Symbol library not found/changed: check `kce project libtable list sym`, or `kce lib init-global-tables`, or `kce sch update-symbols`.",
    "lib_symbol_mismatch": "Embedded symbol differs from library; run `kce sch update-symbols` if the library version is wanted.",
    "footprint_link_issues": "Footprint library missing from fp-lib-table: `kce lib init-global-tables` or `kce project libtable add fp ...`.",
    "footprint_filter": "Assigned footprint doesn't match the symbol's ki_fp_filters; check `kce lib suggest-footprints LIB:SYM`.",
    "multiple_net_names": "A net has two different labels; rename one so each net has one name.",
    "different_unit_net": "Units of one part use different nets on a shared pin.",
    "missing_unit": "A multi-unit part has unplaced units; place them (`kce sch add ... --unit N`) or ignore.",
    "missing_power_pin": "A unit with power pins is not placed; place the power unit.",
    "hier_label_mismatch": "Sheet pins and hierarchical labels in the child sheet differ; add/rename them.",
    "no_connect_connected": "A no-connect flag sits on a connected pin; remove the flag.",
    "no_connect_dangling": "A no-connect flag is not on a pin; delete it.",
    "similar_labels": "Labels differ only in case; KiCad treats them as different nets.",
    "simulation_model_issue": "SPICE model problem (ignore if not simulating).",
    "four_way_junction": "Four wires meet at a point; usually fine.",
    "global_label_dangling": "Global label used only once; another global label of the same name is needed.",
    "bus_to_net_conflict": "A bus and a net share a connection.",
    # DRC
    "unconnected_items": "Unrouted connections: `kce pcb ratsnest` lists them; route with `kce pcb route`, `kce pcb autoroute` or zones.",
    "clearance": "Copper items too close: move footprints (`kce pcb move`), reroute, or adjust rules (`kce project rules set`).",
    "copper_edge_clearance": "Copper too close to board edge: move items inward or enlarge `kce pcb outline`.",
    "courtyards_overlap": "Footprint courtyards overlap: move parts apart (`kce pcb move`) or `kce pcb place grid`.",
    "track_width": "Track narrower than the rule minimum; widen or change `min_track_width`.",
    "hole_clearance": "Drill too close to copper/another hole.",
    "hole_to_hole": "Holes too close together.",
    "annular_width": "Via/pad annular ring too small: larger via diameter or smaller drill.",
    "drill_out_of_range": "Drill smaller than `min_through_hole_diameter`.",
    "via_diameter": "Via diameter below the minimum rule.",
    "silk_overlap": "Silkscreen overlaps; cosmetic (warning).",
    "silk_over_copper": "Silkscreen over exposed copper; move reference text.",
    "silk_edge_clearance": "Silkscreen too close to board edge.",
    "lib_footprint_issues": "Footprint not found in library tables: `kce lib init-global-tables` or add the library.",
    "lib_footprint_mismatch": "Board footprint differs from library version (often harmless).",
    "invalid_outline": "Board outline (Edge.Cuts) not closed: recreate with `kce pcb outline rect`.",
    "malformed_courtyard": "Footprint courtyard is not a closed shape.",
    "missing_courtyard": "Footprint has no courtyard.",
    "shorting_items": "Items of different nets touch: delete/reroute (`kce pcb tracks --net`, `kce pcb delete-tracks`).",
    "tracks_crossing": "Tracks of different nets cross on one layer.",
    "zones_intersect": "Zones of different nets overlap with the same priority; set priorities.",
    "starved_thermal": "Zone thermal spokes insufficient; use `pcb zone add --pad-connection auto` for solid SMD / thermal THT, or --pad-connection solid.",
    "isolated_copper": "Zone island not connected; set island removal or add vias.",
    "track_dangling": "A track end is unconnected; delete it.",
    "via_dangling": "A via is connected on one layer only.",
    "text_height": "Text smaller than `min_text_height`.",
    "text_thickness": "Text thinner than `min_text_thickness`.",
    "solder_mask_bridge": "Solder mask bridge between pads of different nets.",
    "extra_footprint": "Footprint on the board but not in the schematic (parity): `kce pcb update`.",
    "missing_footprint": "Schematic symbol without footprint on board (parity): `kce pcb update`.",
    "net_conflict": "Pad net differs from schematic (parity): `kce pcb update`. If it says 'No pad found for pin', the symbol and footprint pin maps differ: `kce lib link-check LIB:SYM`.",
    "footprint_symbol_field_mismatch": "Schematic field differs from the board footprint: `kce pcb update` syncs fields (`kce sch set-field` changes the schematic side).",
    "footprint_symbol_mismatch": "Footprint differs from the one assigned in the schematic: `kce pcb update`.",
    "footprint_type_mismatch": "Footprint SMD/THT attribute mismatch with pads.",
    "items_not_allowed": "Item placed inside a keepout/rule area.",
    "npth_inside_courtyard": "NPTH hole inside a courtyard.",
    "pth_inside_courtyard": "PTH hole inside another footprint's courtyard.",
}


def _violations_flat(report: Dict[str, Any], kind: str) -> List[Dict[str, Any]]:
    out = []
    if kind == "erc":
        for sh in report.get("sheets", []):
            for v in sh.get("violations", []):
                out.append(dict(v, sheet=sh.get("path")))
    else:
        for k in ("violations", "unconnected_items", "schematic_parity"):
            for v in report.get(k, []):
                out.append(dict(v, category=k))
    return out


_PIN_ITEM = re.compile(r"Symbol\s+(\S+)\s+Pin\s+([^,\s\[]+)")


def _violation_fix(v: Dict[str, Any]) -> Optional[str]:
    """Exact command that clears this violation, when one exists."""
    if v.get("type") == "pin_not_connected":
        for it in v.get("items", []):
            m = _PIN_ITEM.search(it.get("description") or "")
            if m:
                return f"kce sch no-connect {m.group(1)}:{m.group(2)}"
    return None


def _violation_hint(v: Dict[str, Any]) -> str:
    kind = v.get("type")
    text = " ".join([v.get("description") or ""] + [it.get("description") or ""
                                                    for it in v.get("items", [])]).lower()
    if "no pad found for pin" in text:
        return ("Symbol pin has no pad in the footprint: compare `kce lib sym show` and "
                "`kce lib fp show` (or `kce lib link-check`), fix the library, then "
                "`kce sch update-symbols --match 'LIB:*'` and `kce pcb update-footprints`.")
    if kind == "footprint_symbol_field_mismatch":
        return HINTS[kind]
    return HINTS.get(kind, "See the KiCad manual for this check.")


def summarize(report: Dict[str, Any], kind: str) -> Dict[str, Any]:
    vs = _violations_flat(report, kind)
    by_sev = collections.Counter(v.get("severity", "?") for v in vs)
    by_type = collections.Counter(v.get("type", "?") for v in vs)
    hints = {t: HINTS.get(t, "See the KiCad manual for this check.") for t in by_type}
    simplified = []
    for v in vs:
        simplified.append({
            "type": v.get("type"), "severity": v.get("severity"), "description": v.get("description"),
            "hint": _violation_hint(v), "fix": _violation_fix(v),
            "sheet": v.get("sheet"), "category": v.get("category"), "excluded": v.get("excluded", False),
            "items": [{"description": it.get("description"), "pos": it.get("pos"), "uuid": it.get("uuid")}
                      for it in v.get("items", [])],
        })
    errors = by_sev.get("error", 0)
    res = {
        "kind": kind, "total": len(vs), "errors": errors, "warnings": by_sev.get("warning", 0),
        "by_severity": dict(by_sev), "by_type": dict(by_type.most_common()), "hints": hints,
        "violations": simplified, "clean": len([v for v in vs if not v.get("excluded")]) == 0,
        "kicad_version": report.get("kicad_version"),
    }
    if kind == "drc":
        res["unconnected"] = len(report.get("unconnected_items", []))
        res["parity"] = len(report.get("schematic_parity", []))
    return res


@contextmanager
def _fresh_report(kind, output):
    """Never let a previous report stand in for the current engine run."""
    if output:
        fd, path = tempfile.mkstemp(prefix=f".{kind}-", suffix=".json",
                                    dir=os.path.dirname(os.path.abspath(output)))
        os.close(fd)
    else:
        path = kb.mkstemp(kind, ".json")
    try:
        yield path
        if output:
            os.replace(path, os.path.abspath(output))
    finally:
        if os.path.exists(path):
            os.unlink(path)


def _read_check_report(path, result, kind, extra):
    code = result["returncode"]
    detail = result.get("stderr") or result.get("stdout") or "(no engine output)"
    # KiCad uses 5 only when explicitly asked to fail on rule violations.
    if code != 0 and not (code == 5 and "--exit-code-violations" in (extra or [])):
        raise kb.KicadError(f"{kind.upper()} failed: kicad-cli exited with code {code}: {detail}", result)
    try:
        with open(path, encoding="utf-8") as stream:
            report = json.load(stream)
        key = "sheets" if kind == "erc" else "violations"
        if not isinstance(report, dict) or not isinstance(report.get(key), list):
            raise ValueError(f"missing {key} array")
    except (OSError, ValueError) as exc:
        raise kb.KicadError(f"{kind.upper()} produced no valid fresh report: {exc}; {detail}", result) from exc
    return report


def run_erc(sess, output: Optional[str] = None, severity: str = "all", units: str = "mm",
            extra: Optional[List[str]] = None) -> Dict[str, Any]:
    proj = sess.require_project()
    with _fresh_report("erc", output) as out, materialize(sess) as ws:
        sch = remap(proj.sch_path, proj.dir, ws)
        args = ["sch", "erc", "--format", "json", "--units", units, "-o", out]
        args += _severity_args(severity)
        args += extra or []
        res = kb.run_kicad_cli(args + [sch], check=False)
        s = summarize(_read_check_report(out, res, "erc", extra), "erc")
    s["report"] = os.path.abspath(output) if output else None
    return s


def run_drc(sess, output: Optional[str] = None, severity: str = "all", units: str = "mm",
            parity: bool = True, refill: bool = True, save_board: bool = False,
            extra: Optional[List[str]] = None) -> Dict[str, Any]:
    proj = sess.require_project()
    with _fresh_report("drc", output) as out, materialize(sess, force=save_board) as ws:
        pcb_path = remap(proj.pcb_path, proj.dir, ws)
        args = ["pcb", "drc", "--format", "json", "--units", units, "-o", out]
        args += _severity_args(severity)
        if parity and os.path.exists(remap(proj.sch_path, proj.dir, ws)):
            args.append("--schematic-parity")
        if refill:
            args.append("--refill-zones")
            if save_board:
                args.append("--save-board")
        args += extra or []
        res = kb.run_kicad_cli(args + [pcb_path], check=False)
        s = summarize(_read_check_report(out, res, "drc", extra), "drc")
        if save_board and refill:
            sess.snapshot("DRC refill zones (save board)", [proj.pcb_path])
            sess.doc(proj.pcb_path).set_text(open(pcb_path, encoding="utf-8").read())
    s["report"] = os.path.abspath(output) if output else None
    return s


def _severity_args(severity: str) -> List[str]:
    m = {"all": ["--severity-all"], "error": ["--severity-error"], "warning": ["--severity-warning"],
         "error+warning": ["--severity-error", "--severity-warning"],
         "exclusions": ["--severity-exclusions"]}
    if severity not in m:
        raise ValueError(f"severity must be one of {list(m)}")
    return m[severity]


def diagnose(sess, what: str = "both", *, include_violations=False) -> Dict[str, Any]:
    """Run ERC and/or DRC and produce a prioritised fix plan."""
    res: Dict[str, Any] = {}
    plan = []
    proj = sess.require_project()
    if what in ("erc", "both") and (os.path.exists(proj.sch_path) or sess.has_doc(proj.sch_path)):
        e = run_erc(sess)
        res["erc"] = e if include_violations else {k: e[k] for k in ("total", "errors", "warnings", "by_type", "clean")}
        for t, n in e["by_type"].items():
            sev = next((v["severity"] for v in e["violations"] if v["type"] == t), "?")
            ex = [it["description"] for v in e["violations"] if v["type"] == t for it in v["items"]][:5]
            plan.append({"check": "erc", "type": t, "count": n, "severity": sev, "hint": HINTS.get(t, ""),
                         "examples": ex})
    if what in ("drc", "both") and (os.path.exists(proj.pcb_path) or sess.has_doc(proj.pcb_path)):
        d = run_drc(sess)
        res["drc"] = d if include_violations else {k: d[k] for k in ("total", "errors", "warnings", "by_type", "clean", "unconnected", "parity")}
        for t, n in d["by_type"].items():
            sev = next((v["severity"] for v in d["violations"] if v["type"] == t), "?")
            ex = [it["description"] for v in d["violations"] if v["type"] == t for it in v["items"]][:5]
            plan.append({"check": "drc", "type": t, "count": n, "severity": sev, "hint": HINTS.get(t, ""),
                         "examples": ex})
    plan.sort(key=lambda p: (0 if p["severity"] == "error" else 1, -p["count"]))
    res["plan"] = plan
    res["clean"] = all(v.get("clean", True) for k, v in res.items() if isinstance(v, dict))
    return res


def report_policy(result, *, baseline=None, fail_on='none', summary=False, max_violations=None):
    """Mark baseline findings and decide the gate before truncating presentation."""
    result = dict(result)
    violations = [dict(v) for v in result.get('violations', [])]
    previous = []
    if baseline is not None:
        if not isinstance(baseline, dict):
            raise ValueError('Check baseline must be a JSON object')
        if baseline.get('kind'):
            if baseline['kind'] != result['kind']:
                raise ValueError(f"Baseline kind {baseline['kind']} does not match {result['kind']}")
            previous = baseline.get('violations', [])
        else:
            previous = summarize(baseline, result['kind'])['violations']
    def identity(v):
        uuids = tuple(sorted(str(it['uuid']) for it in v.get('items', []) if it.get('uuid')))
        return (v.get('type'), uuids or (v.get('description'), v.get('sheet')))
    known = {identity(v) for v in previous if not v.get('excluded')}
    active = [v for v in violations if not v.get('excluded')]
    current = {identity(v) for v in active}
    for violation in violations:
        violation['known'] = identity(violation) in known
    new = [v for v in active if identity(v) not in known]
    result['new_violations'] = len(new)
    result['fixed_violations'] = len(known - current)
    result['exit_if_strict'] = 0 if result['clean'] else 3
    if not result['clean']:
        result['next'] = f"kce check diagnose --only {result['kind']}"
    levels = {'none': set(), 'error': {'error'}, 'warning': {'error', 'warning'}}[fail_on]
    failed = [v for v in new if v.get('severity') in levels]
    if summary:
        result.pop('violations', None)
    elif max_violations is not None:
        result['violations'] = violations[:max_violations]
        result['truncated'] = len(violations) > max_violations
    else:
        result['violations'] = violations
    return result, failed
