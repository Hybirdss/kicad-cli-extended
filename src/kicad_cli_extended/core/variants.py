"""KiCad 10 design variants (assembly variants).

Where KiCad keeps them:

* the list of variants: ``.kicad_pro`` ``schematic.variants`` = ``[{name, description}]``
  (eeschema/schematic_settings.cpp) and the board's ``(variants (variant (name ..)))``;
* per-symbol differences: under each symbol instance, ``(path "/.." (reference ..) (unit ..)
  (variant (name "V") (dnp yes) (in_bom no) (on_board no) (in_pos_files no)
  (exclude_from_sim yes) (field (name "Value") (value "10k"))))`` (sch_io_kicad_sexpr.cpp).

kicad-cli exports a variant with ``--variant NAME`` (BOM, netlist, plots, position files, ...),
which kce passes through unchanged.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import project as proj_mod
from . import schematic as sch_mod
from .sexpr import Q, S, SList

FLAGS = {"dnp": "dnp", "in_bom": "in_bom", "on_board": "on_board", "in_pos_files": "in_pos_files",
         "exclude_from_sim": "exclude_from_sim"}


def _pro_variants(sess) -> List[Dict[str, Any]]:
    try:
        v = proj_mod.pro_get(sess, "schematic.variants")
    except (KeyError, IndexError, TypeError):
        return []
    return list(v) if isinstance(v, list) else []


def list_variants(sess, trees: Dict[str, SList]) -> Dict[str, Any]:
    names = {v["name"]: v.get("description", "") for v in _pro_variants(sess)}
    symbols: Dict[str, Dict[str, Any]] = {}
    for path, tree in trees.items():
        for sym in sch_mod.placed_symbols(tree):
            for inst in _instance_paths(sym):
                for var in inst.find_all("variant"):
                    vname = var.value("name")
                    names.setdefault(vname, "")
                    d = {k: var.value(k) for k in FLAGS if var.find(k) is not None}
                    d["fields"] = {f.value("name"): f.value("value") for f in var.find_all("field")}
                    symbols.setdefault(vname, {})[inst.value("reference") or sch_mod.sym_ref(sym)] = d
    return {"variants": [{"name": n, "description": d, "symbols": symbols.get(n, {})} for n, d in sorted(names.items())]}


def add_variant(sess, name: str, description: str = "") -> Dict[str, Any]:
    vs = _pro_variants(sess)
    if any(v.get("name") == name for v in vs):
        raise ValueError(f"Variant {name!r} exists. List them with `kce project variant list`.")
    vs.append({"name": name, "description": description} if description else {"name": name})
    proj_mod.pro_set(sess, "schematic.variants", vs)
    return {"added": name, "variants": [v["name"] for v in vs]}


def remove_variant(sess, name: str, trees: Dict[str, SList]) -> Dict[str, Any]:
    vs = _pro_variants(sess)
    keep = [v for v in vs if v.get("name") != name]
    removed_overrides = 0
    for tree in trees.values():
        for sym in sch_mod.placed_symbols(tree):
            for inst in _instance_paths(sym):
                before = len(inst)
                inst[:] = [c for c in inst if not (isinstance(c, SList) and c.tag == "variant" and c.value("name") == name)]
                removed_overrides += before - len(inst)
    if len(keep) == len(vs) and not removed_overrides:
        raise KeyError(f"No variant {name!r}. List them with `kce project variant list`.")
    proj_mod.pro_set(sess, "schematic.variants", keep)
    return {"removed": name, "symbol_overrides_removed": removed_overrides}


def _instance_paths(sym: SList) -> List[SList]:
    inst = sym.find("instances")
    out = []
    if inst is not None:
        for proj in inst.find_all("project"):
            out += proj.find_all("path")
    return out


def _yes(v: bool) -> str:
    return "yes" if v else "no"


def set_symbol_variant(sess, tree: SList, ref: str, variant: str, flags: Dict[str, Optional[bool]],
                       fields: Dict[str, str], clear: bool = False) -> Dict[str, Any]:
    """Set one symbol's differences in ``variant`` (every instance of the symbol with that reference)."""
    if not any(v.get("name") == variant for v in _pro_variants(sess)):
        raise KeyError(f"No variant {variant!r} in the project. Create it with `kce project variant add {variant}`.")
    syms = sch_mod.find_symbols(tree, ref) or [sch_mod.find_symbol(tree, ref)]
    touched = 0
    for sym in syms:
        for inst in _instance_paths(sym):
            if (inst.value("reference") or ref) != ref:
                continue
            var = next((c for c in inst.find_all("variant") if c.value("name") == variant), None)
            if var is not None and clear:
                inst.remove(var)
                var = None
            if var is None:
                var = S("variant", S("name", Q(variant)))
                inst.append(var)
            for key, val in flags.items():
                if val is None:
                    continue
                node = var.find(FLAGS[key])
                if node is None:
                    var.append(S(FLAGS[key], _yes(val)))
                else:
                    node[1] = _yes(val)
            for fname, fval in fields.items():
                f = next((c for c in var.find_all("field") if c.value("name") == fname), None)
                if f is None:
                    var.append(S("field", S("name", Q(fname)), S("value", Q(fval))))
                else:
                    f.find("value")[1] = Q(fval)
            if len(var) == 2:  # only the name: no difference, KiCad drops it too
                inst.remove(var)
            touched += 1
    if not touched:
        raise KeyError(f"{ref} has no instance data in this sheet (annotate first: `kce sch annotate`).")
    return {"reference": ref, "variant": variant, "instances": touched,
            "overrides": {k: v for k, v in flags.items() if v is not None}, "fields": fields}
