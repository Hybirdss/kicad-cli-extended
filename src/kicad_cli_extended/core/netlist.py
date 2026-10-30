"""Netlist generation (via real kicad-cli) and parsing of the KiCad S-expression netlist."""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

from . import sexpr
from ..utils import kicad_backend as kb


def export_netlist(sch_path: str, output: Optional[str] = None, fmt: str = "kicadsexpr") -> str:
    """Run ``kicad-cli sch export netlist`` and return the output path."""
    if output is None:
        output = kb.mkstemp("netlist", ".net")
    kb.run_kicad_cli(["sch", "export", "netlist", "--format", fmt, "-o", output, sch_path])
    return output


def parse_netlist(path_or_text: str) -> Dict[str, Any]:
    text = path_or_text
    if os.path.exists(path_or_text):
        text = open(path_or_text, encoding="utf-8").read()
    t = sexpr.loads(text)
    comps = []
    for c in (t.find("components") or sexpr.S("components")).find_all("comp"):
        fields = {}
        for f in (c.find("fields") or sexpr.S("fields")).find_all("field"):
            name = f.find("name")
            vals = [a for a in f.atoms()]
            fields[name.value() if name is not None else "?"] = str(vals[0]) if vals else ""
        props = {}
        for p in c.find_all("property"):
            props[p.value("name")] = p.value("value", "")
        ls = c.find("libsource")
        sp = c.find("sheetpath")
        units = []
        for u in (c.find("units") or sexpr.S("units")).find_all("unit"):
            units.append({"name": u.value("name"),
                          "pins": [p.value("num") for p in (u.find("pins") or sexpr.S("pins")).find_all("pin")]})
        tst = c.find("tstamps")
        comps.append({
            "ref": c.value("ref"), "value": c.value("value", ""), "footprint": c.value("footprint", ""),
            "datasheet": c.value("datasheet", ""), "fields": fields, "properties": props,
            "lib": ls.value("lib") if ls is not None else None, "part": ls.value("part") if ls is not None else None,
            "description": ls.value("description", "") if ls is not None else "",
            "sheet_names": sp.value("names") if sp is not None else "/",
            "sheet_tstamps": sp.value("tstamps") if sp is not None else "/",
            "tstamps": [str(a) for a in tst.atoms()] if tst is not None else [],
            "units": units,
            "dnp": "dnp" in props, "exclude_from_board": "exclude_from_board" in props,
            "exclude_from_bom": "exclude_from_bom" in props,
            "variants": _comp_variants(c),
        })
    nets = []
    for n in (t.find("nets") or sexpr.S("nets")).find_all("net"):
        nodes = [{"ref": nd.value("ref"), "pin": nd.value("pin"), "pinfunction": nd.value("pinfunction", ""),
                  "pintype": nd.value("pintype", "")} for nd in n.find_all("node")]
        nets.append({"code": n.value("code"), "name": n.value("name"), "class": n.value("class", "Default"),
                     "nodes": nodes})
    variants = [{"name": v.value("name"), "description": v.value("description", "")}
                for v in (t.find("variants") or sexpr.S("variants")).find_all("variant")]
    return {"components": comps, "nets": nets, "variants": variants, "libpins": _libpins(t),
            "source": (t.find("design") or sexpr.S("d")).value("source"),
            "tool": (t.find("design") or sexpr.S("d")).value("tool")}


def _comp_variants(c) -> Dict[str, Dict[str, Any]]:
    """KiCad 10 design-variant overrides of one component: {variant: {dnp, exclude_from_bom, ...,
    fields}} (kicad netlist ``(variants (variant (name ..) (property (name ..) (value "1")) (fields ..)))``)."""
    out = {}
    for v in (c.find("variants") or sexpr.S("variants")).find_all("variant"):
        d: Dict[str, Any] = {p.value("name"): p.value("value", "") in ("1", "yes", "true")
                             for p in v.find_all("property")}
        d["fields"] = {f.value("name"): (f.atoms()[0] if f.atoms() else "")
                       for f in (v.find("fields") or sexpr.S("fields")).find_all("field")}
        out[v.value("name")] = d
    return out


PIN_TYPES = ("input", "output", "bidirectional", "tri_state", "passive", "free", "unspecified",
             "power_in", "power_out", "open_collector", "open_emitter", "no_connect")


def natural_key(value: Any) -> tuple:
    """Sort key with numbers in numeric order: R2 before R10, pin 2 before pin 10."""
    out = []
    for part in re.split(r"(\d+)", str(value)):
        if not part:
            continue
        out.append((1, int(part)) if part.isdecimal() else (0, part.lower()))  # "²".isdigit() but int("²") raises
    return tuple(out) + ((2, str(value)),)  # the raw text breaks ties: R01 vs R1, GND vs gnd


def _libpins(netlist: "sexpr.SList") -> Dict[tuple, Dict[str, str]]:
    """(library, part, pin number) -> {name, type} from the netlist's libparts section."""
    table = (netlist.find("libparts") or sexpr.S("libparts"))
    out = {}
    for lp in table.find_all("libpart"):
        key = (lp.value("lib"), lp.value("part"))
        for pin in (lp.find("pins") or sexpr.S("pins")).find_all("pin"):
            out[key + (str(pin.value("num")),)] = {"name": pin.value("name", "") or "",
                                                   "type": pin.value("type", "") or ""}
    return out


def contract_nets(nl: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The stable machine view of a netlist's nets (``kce sch nets --json``).

    Nets are sorted by name; pins by reference then number in natural order, so the same
    project always produces the same bytes.  ``sheet_path`` is the KiCad sheet path name
    (``/``, ``/Power/``, ...) of the first pin's component.
    """
    comp_by_ref = {c["ref"]: c for c in nl.get("components", [])}
    libpins = nl.get("libpins") or {}
    out = []
    for n in nl.get("nets", []):
        pins = []
        for nd in n["nodes"]:
            ref, num = nd["ref"], str(nd["pin"])
            comp = comp_by_ref.get(ref) or {}
            lib = (comp.get("lib"), comp.get("part"), num)
            meta = libpins.get(lib, {})
            name = meta.get("name")
            if name is None:
                pf = nd.get("pinfunction") or ""
                suffix = "_" + num
                name = pf[:-len(suffix)] if pf.endswith(suffix) else pf
            # KiCad writes a pin carrying a no-connect flag as "passive+no_connect"
            ptype, *flags = (nd.get("pintype") or meta.get("type") or "unspecified").split("+")
            if ptype not in PIN_TYPES:
                ptype = "unspecified"
            pins.append({"ref": ref, "pin": num, "pin_name": name, "pin_type": ptype,
                         "no_connect": "no_connect" in flags})
        pins.sort(key=lambda p: (natural_key(p["ref"]), natural_key(p["pin"])))
        sheet = (comp_by_ref.get(pins[0]["ref"], {}).get("sheet_names") if pins else None) or "/"
        code = str(n.get("code") or "")
        out.append({
            "name": n["name"],
            "code": int(code) if code.isdigit() else None,
            "sheet_path": sheet,
            "class": n.get("class", "Default"),
            "pins": pins,
            # kept for existing callers: "REF:PIN(pinfunction)" in KiCad's netlist order
            "nodes": [f"{nd['ref']}:{nd['pin']}" + (f"({nd['pinfunction']})" if nd.get("pinfunction") else "")
                      for nd in n["nodes"]],
        })
    out.sort(key=lambda n: natural_key(n["name"]))
    return out


def netlist_summary(nl: Dict[str, Any]) -> Dict[str, Any]:
    pin_net = {}
    for n in nl["nets"]:
        for nd in n["nodes"]:
            pin_net[f"{nd['ref']}:{nd['pin']}"] = n["name"]
    unconnected = [n["name"] for n in nl["nets"] if len(n["nodes"]) == 1]
    return {"components": len(nl["components"]), "nets": len(nl["nets"]),
            "single_pin_nets": unconnected, "pin_to_net": pin_net}


def symbol_uuid_path(comp: Dict[str, Any]) -> str:
    """KiCad footprint path (sheet tstamps + symbol uuid) used to link PCB <-> schematic."""
    sheet = comp.get("sheet_tstamps") or "/"
    if not sheet.endswith("/"):
        sheet += "/"
    return sheet + (comp["tstamps"][0] if comp["tstamps"] else "")
