"""Schematic (.kicad_sch) editing on the native S-expression format.

KiCad has no Python API for Eeschema, so every operation here edits the
file tree directly using KiCad 10's exact structures, and the session
validates the result by re-loading it through ``kicad-cli`` on save.

Coordinates are millimetres, schematic Y axis points down (KiCad
convention).  Library symbol coordinates are Y-up; ``pin_position`` applies
KiCad's TRANSFORM (sch_symbol.cpp ``SetOrientation``) exactly.
"""

from __future__ import annotations

import copy
import collections
import fnmatch
import math
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import uuid as _uuid

from . import library as lib_mod
from . import sexpr
from .netlist import natural_key
from .project import new_uuid
from .sexpr import Q, S, SList
from ..utils.paths import same_path

GRID = 1.27
EPS = 1e-4


def snap(v: float, grid: float = GRID) -> float:
    return round(round(v / grid) * grid, 4)


def _f(v) -> float:
    return float(v)


# ---------------------------------------------------------------------------
# Geometry: KiCad symbol TRANSFORM
# ---------------------------------------------------------------------------

_ROT = {0: (1, 0, 0, 1), 90: (0, 1, -1, 0), 180: (-1, 0, 0, -1), 270: (0, -1, 1, 0)}
_MIRROR = {"x": (1, 0, 0, -1), "y": (-1, 0, 0, 1)}


def _compose(m, t):
    x1, y1, x2, y2 = m
    tx1, ty1, tx2, ty2 = t
    return (x1 * tx1 + x2 * ty1, y1 * tx1 + y2 * ty1, x1 * tx2 + x2 * ty2, y1 * tx2 + y2 * ty2)


def symbol_transform(angle: float, mirror: Optional[str]) -> Tuple[int, int, int, int]:
    a = int(round(angle)) % 360
    if a not in _ROT:
        raise ValueError(f"Symbol angle must be 0/90/180/270, got {angle}")
    m = _ROT[a]
    if mirror:
        for ax in mirror:
            m = _compose(m, _MIRROR[ax])
    return m


def apply_transform(m, x: float, y: float) -> Tuple[float, float]:
    x1, y1, x2, y2 = m
    return (x1 * x + y1 * y, x2 * x + y2 * y)


def lib_to_sch(sym_node: SList, lx: float, ly: float) -> Tuple[float, float]:
    """Map a library-coordinate point (Y up) to absolute schematic coordinates."""
    at = sym_node.find("at")
    sx, sy = _f(at[1]), _f(at[2])
    ang = _f(at[3]) if len(at) > 3 else 0.0
    mirror = sym_node.value("mirror")
    m = symbol_transform(ang, mirror)
    tx, ty = apply_transform(m, lx, -ly)
    return (round(sx + tx, 4), round(sy + ty, 4))


_DIR = {0: (1, 0), 90: (0, 1), 180: (-1, 0), 270: (0, -1)}  # lib pin orientation, Y-up: direction from connection point towards the body


def pin_outward(sym_node: SList, pin: Dict[str, Any]) -> Tuple[float, float]:
    """Unit vector pointing *away* from the symbol body at the pin's connection end."""
    dx, dy = _DIR.get(int(round(pin["orientation"])) % 360, (1, 0))
    # pin body extends along (dx,dy) from the connection point; outward is the opposite
    ox, oy = -dx, -dy
    at = sym_node.find("at")
    ang = _f(at[3]) if len(at) > 3 else 0.0
    m = symbol_transform(ang, sym_node.value("mirror"))
    tx, ty = apply_transform(m, ox, -oy)
    return (round(tx), round(ty))


# ---------------------------------------------------------------------------
# Symbol helpers
# ---------------------------------------------------------------------------


def placed_symbols(tree: SList) -> List[SList]:
    return tree.find_all("symbol")


def lib_symbols(tree: SList) -> SList:
    ls = tree.find("lib_symbols")
    if ls is None:
        ls = S("lib_symbols")
        idx = next((i for i, c in enumerate(tree) if isinstance(c, SList) and c.tag in ("title_block", "paper")), 1)
        last = max((i for i, c in enumerate(tree) if isinstance(c, SList) and c.tag in
                    ("uuid", "paper", "title_block")), default=idx)
        tree.insert(last + 1, ls)
    return ls


def cached_lib_symbol(tree: SList, lib_id: str) -> Optional[SList]:
    for s in lib_symbols(tree).find_all("symbol"):
        if str(s[1]) == lib_id:
            return s
    return None


def sym_ref(sym: SList) -> str:
    return sym.get_property("Reference", "?")


def find_symbol(tree: SList, ref: str) -> SList:
    matches = [s for s in placed_symbols(tree) if sym_ref(s) == ref]
    if not matches:
        import difflib
        refs = sorted({sym_ref(s) for s in placed_symbols(tree) if not sym_ref(s).startswith("#")})
        closest = difflib.get_close_matches(ref, refs, n=5, cutoff=0)
        raise KeyError(f"No symbol with reference {ref!r} in schematic. Closest: {closest}")
    return matches[0]


def find_symbols(tree: SList, ref: str) -> List[SList]:
    """All units with the given reference (multi-unit parts share a reference)."""
    return [s for s in placed_symbols(tree) if sym_ref(s) == ref]


def symbol_dict(sym: SList, tree: Optional[SList] = None) -> Dict[str, Any]:
    at = sym.find("at")
    props = {}
    for p in sym.find_all("property"):
        if len(p) > 2:
            props[str(p[1])] = str(p[2])
    d = {
        "reference": props.get("Reference"), "value": props.get("Value"),
        "footprint": props.get("Footprint", ""), "lib_id": sym.value("lib_id"),
        "x": _f(at[1]), "y": _f(at[2]), "angle": _f(at[3]) if len(at) > 3 else 0.0,
        "mirror": sym.value("mirror"), "unit": int(sym.value("unit", "1")),
        "uuid": sym.value("uuid"),
        "in_bom": sym.value("in_bom", "yes") == "yes", "on_board": sym.value("on_board", "yes") == "yes",
        "dnp": sym.value("dnp", "no") == "yes", "exclude_from_sim": sym.value("exclude_from_sim", "no") == "yes",
        "fields": {k: v for k, v in props.items() if k not in ("Reference", "Value", "Footprint")},
        "power": False,
    }
    if tree is not None:
        ls = cached_lib_symbol(tree, d["lib_id"])
        d["power"] = bool(ls is not None and ls.find("power") is not None)
    return d


def list_symbols(tree: SList, include_power: bool = True, pattern: Optional[str] = None) -> List[Dict]:
    out = []
    for s in placed_symbols(tree):
        d = symbol_dict(s, tree)
        if not include_power and (d["power"] or (d["reference"] or "").startswith("#")):
            continue
        if pattern and not (fnmatch.fnmatchcase(d["reference"] or "", pattern)
                            or fnmatch.fnmatchcase(d["value"] or "", pattern)
                            or fnmatch.fnmatchcase(d["lib_id"] or "", pattern)):
            continue
        out.append(d)
    return out


def symbol_pins(tree: SList, sym: SList) -> List[Dict[str, Any]]:
    """Pins of a placed symbol with absolute connection-point coordinates."""
    lib_id = sym.value("lib_id")
    ls = cached_lib_symbol(tree, lib_id)
    if ls is None:
        raise KeyError(f"lib_symbols cache has no entry for {lib_id!r}")
    unit = int(sym.value("unit", "1"))
    style = int(sym.value("body_style", sym.value("convert", "1")) or 1)
    out = []
    for p in lib_mod.pin_list(ls):
        if p["unit"] not in (0, unit) or p["body_style"] not in (0, style):
            continue
        x, y = lib_to_sch(sym, p["x"], p["y"])
        q = dict(p)
        q["x"], q["y"] = x, y
        q["lib_x"], q["lib_y"] = p["x"], p["y"]
        q["outward"] = pin_outward(sym, p)
        out.append(q)
    return out


def find_pin(tree: SList, ref: str, pin: str) -> Tuple[SList, Dict[str, Any]]:
    """Resolve ``REF:PIN`` where PIN is a pin number or pin name."""
    syms = find_symbols(tree, ref)
    if not syms:
        find_symbol(tree, ref)
    candidates = []
    for s in syms:
        for p in symbol_pins(tree, s):
            if p["number"] == pin:
                return s, p
            if p["name"] == pin:
                candidates.append((s, p))
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        raise ValueError(f"Pin name {pin!r} is ambiguous on {ref}; use the pin number "
                         f"({[c[1]['number'] for c in candidates]})")
    allp = [f"{p['number']}({p['name']})" for s in syms for p in symbol_pins(tree, s)]
    raise KeyError(f"{ref} has no pin {pin!r}. Pins: {allp}")


def parse_pin_ref(s: str) -> Tuple[str, str]:
    if ":" not in s:
        raise ValueError(f"Expected REF:PIN (e.g. R1:2 or U1:VCC), got {s!r}")
    ref, pin = s.split(":", 1)
    return ref, pin


def parse_point(s: str) -> Tuple[float, float]:
    try:
        x, y = s.split(",")
        return float(x), float(y)
    except ValueError:
        raise ValueError(f"Expected x,y in mm, got {s!r}")


def resolve_point(tree: SList, s: str) -> Tuple[float, float]:
    """``x,y`` coordinates or ``REF:PIN`` pin connection point."""
    if re.fullmatch(r"\s*-?[\d.]+\s*,\s*-?[\d.]+\s*", s):
        return parse_point(s)
    ref, pin = parse_pin_ref(s)
    return tuple(find_pin(tree, ref, pin)[1][k] for k in ("x", "y"))  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Hierarchy
# ---------------------------------------------------------------------------


def root_uuid(tree: SList) -> str:
    return tree.value("uuid")


def sheet_dict(sheet: SList) -> Dict[str, Any]:
    at, size = sheet.find("at"), sheet.find("size")
    return {
        "name": sheet.get_property("Sheetname") or sheet.get_property("Sheet name"),
        "file": sheet.get_property("Sheetfile") or sheet.get_property("Sheet file"),
        "uuid": sheet.value("uuid"), "x": _f(at[1]), "y": _f(at[2]),
        "width": _f(size[1]), "height": _f(size[2]),
        "pins": [{"name": str(p[1]), "type": str(p[2]), "x": _f(p.find("at")[1]), "y": _f(p.find("at")[2]),
                  "uuid": p.value("uuid")} for p in sheet.find_all("pin")],
    }


def hierarchy(load_tree, root_path: str) -> List[Dict[str, Any]]:
    """Walk the sheet hierarchy. ``load_tree(path)`` returns a parsed tree.

    Returns one entry per sheet *instance*: path (uuid path), file, name, depth.
    """
    root = load_tree(root_path)
    ru = root_uuid(root)
    out = [{"file": root_path, "name": "/", "path": f"/{ru}", "sheet_path": "/", "depth": 0,
            "page": None}]

    def rec(tree, file_path, uuid_path, name_path, depth, seen):
        for sh in tree.find_all("sheet"):
            d = sheet_dict(sh)
            child = os.path.join(os.path.dirname(file_path), d["file"] or "")
            entry = {"file": child, "name": d["name"], "path": f"{uuid_path}/{d['uuid']}",
                     "sheet_path": f"{name_path}{d['name']}/", "depth": depth + 1,
                     "exists": os.path.exists(child), "sheet_uuid": d["uuid"], "parent_file": file_path}
            out.append(entry)
            if os.path.exists(child) and child not in seen:
                rec(load_tree(child), child, entry["path"], entry["sheet_path"], depth + 1, seen | {child})

    rec(root, root_path, f"/{ru}", "/", 0, {root_path})
    return out


def instance_paths_for(load_tree, root_path: str, file_path: str) -> List[str]:
    return [h["path"] for h in hierarchy(load_tree, root_path) if same_path(h["file"], file_path)]


# ---------------------------------------------------------------------------
# Adding / editing symbols
# ---------------------------------------------------------------------------


_UUID_TEXT = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def normalize_uuid(value: str) -> str:
    """Canonical lower-case 8-4-4-4-12 form of a caller-supplied UUID, or a clear ValueError.

    Only the plain hyphenated form is accepted (not ``{...}``, ``urn:uuid:``, bare hex or
    non-ASCII digits, which :class:`uuid.UUID` would take) and not the all-zero nil UUID,
    which KiCad uses for "no id".
    """
    text = str(value).strip()
    if not _UUID_TEXT.fullmatch(text) or int(text.replace("-", ""), 16) == 0:
        raise ValueError(f"{value!r} is not a usable UUID (expected non-zero 8-4-4-4-12 hex digits, "
                         "e.g. 3f2b8c1e-9d4a-5b6c-8e7f-0123456789ab)")
    return text.lower()


def project_uuids(load_tree, root_path: str) -> set:
    """Every item uuid in the schematic hierarchy (root + sub-sheets that exist on disk)."""
    out = set()
    try:
        files = [h["file"] for h in hierarchy(load_tree, root_path)]
    except (KeyError, OSError):
        files = [root_path]
    for f in files:
        if not os.path.exists(f):
            continue
        for n in sexpr.walk(load_tree(f)):
            v = n.value("uuid")
            if v:
                out.add(v)
    return out


def _ensure_cached(tree: SList, proj, lib_id: str) -> SList:
    cached = cached_lib_symbol(tree, lib_id)
    if cached is not None:
        return cached
    sym, _ = lib_mod.resolve_symbol(proj, lib_id)
    sym = copy.deepcopy(sym)
    sym[1] = Q(lib_id)
    lib_symbols(tree).append(sym)
    return sym


def _insert_item(tree: SList, node: SList) -> None:
    """Insert a schematic item before sheet_instances/embedded_fonts (KiCad order)."""
    for i, c in enumerate(tree):
        if isinstance(c, SList) and c.tag in ("sheet_instances", "symbol_instances", "embedded_fonts"):
            tree.insert(i, node)
            return
    tree.append(node)


def _field_effects(src_prop: Optional[SList], hide: bool = False) -> SList:
    eff = copy.deepcopy(src_prop.find("effects")) if src_prop is not None and src_prop.find("effects") is not None \
        else S("effects", S("font", S("size", 1.27, 1.27)))
    eff.remove_all("hide")
    return eff


def _make_field(name: str, value: str, x: float, y: float, angle: float, src: Optional[SList],
                hide: bool) -> SList:
    p = S("property", Q(name), Q(value), S("at", round(x, 4), round(y, 4), angle))
    if hide:
        p.append(S("hide", "yes"))
    p.append(S("show_name", "no"))
    p.append(S("do_not_autoplace", "no"))
    p.append(_field_effects(src, hide))
    return p


def _prop_hidden(p: SList) -> bool:
    h = p.find("hide")
    if h is not None:
        return h.value() != "no"
    eff = p.find("effects")
    if eff is not None:
        h2 = eff.find("hide")
        if h2 is not None:
            return h2.value() != "no" if len(h2) > 1 else True
        return "hide" in [str(a) for a in eff.atoms()]
    return False


def add_symbol(tree: SList, proj, lib_id: str, reference: Optional[str], x: float, y: float,
               angle: float = 0, mirror: Optional[str] = None, unit: int = 1,
               value: Optional[str] = None, footprint: Optional[str] = None,
               fields: Optional[Dict[str, str]] = None, instance_paths: Optional[List[str]] = None,
               project_name: str = "", snap_grid: bool = True, dnp: bool = False,
               in_bom: Optional[bool] = None, on_board: Optional[bool] = None,
               uuid: Optional[str] = None) -> SList:
    """Place a library symbol (copies it into lib_symbols if needed)."""
    libsym = _ensure_cached(tree, proj, lib_id)
    if snap_grid:
        x, y = snap(x), snap(y)
    lib_props = {str(p[1]): p for p in libsym.find_all("property")}
    is_power = libsym.find("power") is not None
    if reference is None:
        prefix = str(lib_props["Reference"][2]) if "Reference" in lib_props else ("#PWR" if is_power else "U")
        reference = (prefix or "U").rstrip("?") + "?"
    units = sorted({p["unit"] for p in lib_mod.pin_list(libsym) if p["unit"] > 0}) or [1]
    if unit not in units and unit != 1:
        raise ValueError(f"{lib_id} has units {units}; got unit {unit}")
    sym = S("symbol", S("lib_id", Q(lib_id)), S("at", x, y, int(angle) % 360))
    if mirror:
        if mirror not in ("x", "y"):
            raise ValueError("mirror must be x or y")
        sym.append(S("mirror", mirror))
    in_bom_v = libsym.value("in_bom", "yes") == "yes" if in_bom is None else in_bom
    on_board_v = libsym.value("on_board", "yes") == "yes" if on_board is None else on_board
    sym.extend([S("unit", unit), S("body_style", 1),
                S("exclude_from_sim", libsym.value("exclude_from_sim", "no")),
                S("in_bom", "yes" if in_bom_v else "no"), S("on_board", "yes" if on_board_v else "no"),
                S("in_pos_files", libsym.value("in_pos_files", "yes")),
                S("dnp", "yes" if dnp else "no"), S("uuid", Q(uuid or new_uuid()))])
    tmp_sym = S("symbol", S("at", x, y, int(angle) % 360))
    if mirror:
        tmp_sym.append(S("mirror", mirror))
    values = {"Reference": reference}
    if value is not None:
        values["Value"] = value
    if footprint is not None:
        values["Footprint"] = footprint
    for k, v in (fields or {}).items():
        values[k] = v
    done = set()
    for name, lp in lib_props.items():
        if name.startswith("ki_"):
            continue  # keywords / fp filters live only in lib_symbols
        lat = lp.find("at")
        fx, fy = lib_to_sch(tmp_sym, _f(lat[1]), _f(lat[2]))
        fang = (_f(lat[3]) if len(lat) > 3 else 0) + angle
        fang = 90 if int(round(fang)) % 180 == 90 else 0
        val = values.get(name, str(lp[2]))
        sym.append(_make_field(name, val, fx, fy, fang, lp, _prop_hidden(lp)))
        done.add(name)
    for name, v in values.items():
        if name not in done:
            sym.append(_make_field(name, v, x, y + 2.54, 0, None, True))
    style_pins = [p for p in lib_mod.pin_list(libsym) if p["unit"] in (0, unit)]
    seen = set()
    for p in style_pins:
        if p["number"] in seen:
            continue
        seen.add(p["number"])
        sym.append(S("pin", Q(p["number"]), S("uuid", Q(new_uuid()))))
    paths = instance_paths or [f"/{root_uuid(tree)}"]
    inst = S("instances", S("project", Q(project_name),
                            *[S("path", Q(pth), S("reference", Q(reference)), S("unit", unit)) for pth in paths]))
    sym.append(inst)
    _insert_item(tree, sym)
    return sym


def set_instance_reference(sym: SList, reference: str, path: Optional[str] = None) -> None:
    p = sym.property("Reference")
    if p is not None:
        p[2] = Q(reference)
    inst = sym.find("instances")
    if inst is None:
        return
    for proj in inst.find_all("project"):
        for pth in proj.find_all("path"):
            if path is None or str(pth[1]) == path:
                r = pth.find("reference")
                if r is not None:
                    r[1] = Q(reference)
                else:
                    pth.append(S("reference", Q(reference)))


def instance_references(sym: SList) -> Dict[str, str]:
    out = {}
    inst = sym.find("instances")
    if inst is None:
        return out
    for proj in inst.find_all("project"):
        for pth in proj.find_all("path"):
            out[str(pth[1])] = pth.value("reference")
    return out


def set_field(sym: SList, name: str, value: str, hide: Optional[bool] = None,
              x: Optional[float] = None, y: Optional[float] = None) -> SList:
    if name == "Reference":
        set_instance_reference(sym, value)
        p = sym.property("Reference")
    else:
        p = sym.property(name)
        if p is None:
            at = sym.find("at")
            p = _make_field(name, value, _f(at[1]), _f(at[2]) + 2.54, 0, None, True if hide is None else hide)
            last = max(i for i, c in enumerate(sym) if isinstance(c, SList) and c.tag in
                       ("property", "uuid", "dnp"))
            sym.insert(last + 1, p)
        else:
            p[2] = Q(value)
    if hide is not None and p is not None:
        p.remove_all("hide")
        eff = p.find("effects")
        if eff is not None:
            eff.remove_all("hide")
        if hide:
            p.insert(3, S("hide", "yes"))
    if p is not None and (x is not None or y is not None):
        at = p.find("at")
        if x is not None:
            at[1] = sexpr.fmt_num(x)
        if y is not None:
            at[2] = sexpr.fmt_num(y)
    return p


def remove_field(sym: SList, name: str) -> None:
    if name in ("Reference", "Value", "Footprint", "Datasheet", "Description"):
        raise ValueError(f"Mandatory field {name!r} cannot be removed (set it to '' instead)")
    p = sym.property(name)
    if p is None:
        raise KeyError(f"Field {name!r} not present on {sym_ref(sym)}")
    sym.remove_child(p)


def set_attr(sym: SList, attr: str, value: bool) -> None:
    if attr not in ("in_bom", "on_board", "dnp", "exclude_from_sim", "in_pos_files"):
        raise ValueError("attr must be in_bom|on_board|dnp|exclude_from_sim|in_pos_files")
    n = sym.find(attr)
    if n is None:
        sym.insert(4, S(attr, "yes" if value else "no"))
    else:
        n[1] = "yes" if value else "no"


def move_symbol(sym: SList, x: Optional[float] = None, y: Optional[float] = None,
                dx: float = 0.0, dy: float = 0.0, snap_grid: bool = True) -> Tuple[float, float]:
    at = sym.find("at")
    ox, oy = _f(at[1]), _f(at[2])
    nx = (x if x is not None else ox) + dx
    ny = (y if y is not None else oy) + dy
    if snap_grid:
        nx, ny = snap(nx), snap(ny)
    ddx, ddy = nx - ox, ny - oy
    at[1], at[2] = sexpr.fmt_num(round(nx, 4)), sexpr.fmt_num(round(ny, 4))
    for p in sym.find_all("property"):
        pa = p.find("at")
        pa[1] = sexpr.fmt_num(round(_f(pa[1]) + ddx, 4))
        pa[2] = sexpr.fmt_num(round(_f(pa[2]) + ddy, 4))
    return nx, ny


def rotate_symbol(sym: SList, angle: Optional[float] = None, by: Optional[float] = None) -> float:
    """Set absolute angle or rotate by a multiple of 90° (CCW). Fields follow."""
    at = sym.find("at")
    cur = _f(at[3]) if len(at) > 3 else 0
    new = (angle if angle is not None else cur + (by or 90)) % 360
    if int(new) % 90:
        raise ValueError("Rotation must be a multiple of 90 degrees")
    sx, sy = _f(at[1]), _f(at[2])
    old_m = symbol_transform(cur, sym.value("mirror"))
    new_m = symbol_transform(new, sym.value("mirror"))
    # move fields so they keep their position relative to the body
    inv = (old_m[0], old_m[2], old_m[1], old_m[3])  # orthogonal matrix inverse = transpose
    for p in sym.find_all("property"):
        pa = p.find("at")
        rx, ry = _f(pa[1]) - sx, _f(pa[2]) - sy
        lx, ly = apply_transform(inv, rx, ry)
        nx, ny = apply_transform(new_m, lx, ly)
        pa[1], pa[2] = sexpr.fmt_num(round(sx + nx, 4)), sexpr.fmt_num(round(sy + ny, 4))
        fang = _f(pa[3]) if len(pa) > 3 else 0
        fang = (fang + (new - cur)) % 180
        if len(pa) > 3:
            pa[3] = sexpr.fmt_num(90 if int(round(fang)) == 90 else 0)
    if len(at) > 3:
        at[3] = sexpr.fmt_num(int(new))
    else:
        at.append(sexpr.fmt_num(int(new)))
    return new


def mirror_symbol(sym: SList, axis: Optional[str]) -> None:
    sym.remove_all("mirror")
    if axis:
        if axis not in ("x", "y"):
            raise ValueError("axis must be x, y or none")
        idx = next(i for i, c in enumerate(sym) if isinstance(c, SList) and c.tag == "at")
        sym.insert(idx + 1, S("mirror", axis))


def delete_symbol(tree: SList, ref: str) -> int:
    syms = find_symbols(tree, ref)
    if not syms:
        find_symbol(tree, ref)
    for s in syms:
        tree.remove_child(s)
    prune_lib_symbols(tree)
    return len(syms)


def prune_lib_symbols(tree: SList) -> int:
    used = {s.value("lib_id") for s in placed_symbols(tree)}
    ls = lib_symbols(tree)
    before = len(ls)
    ls[:] = [c for c in ls if not (isinstance(c, SList) and c.tag == "symbol" and str(c[1]) not in used)]
    return before - len(ls)


class NetSplitError(RuntimeError):
    """A symbol replacement would change the existing endpoint partitions."""

    def __init__(self, report):
        self.report = report
        super().__init__("Symbol replacement would lose connections; inspect details.connections_lost. "
                         "Try --map-by number --rewire or an explicit --pin-map '1=3,2=1'.")


def replace_lib_symbol(tree: SList, proj, ref: str, new_lib_id: str, *,
                       pin_map: Optional[str] = None, map_by: str = "number",
                       rewire: bool = False) -> Dict[str, Any]:
    """Swap a library symbol, optionally carrying attached geometry to its mapped pins.

    The caller checks KiCad's netlist before publishing this candidate tree. Rewiring is a
    simultaneous coordinate substitution, so a permutation cannot move an item twice.
    """
    syms = find_symbols(tree, ref)
    if not syms:
        find_symbol(tree, ref)
    before = [p for sym in syms for p in symbol_pins(tree, sym)]
    _ensure_cached(tree, proj, new_lib_id)
    for sym in syms:
        sym.find("lib_id")[1] = Q(new_lib_id)
    after = [p for sym in syms for p in symbol_pins(tree, sym)]
    old = {p["number"]: p for p in before}
    new = {p["number"]: p for p in after}
    mapping = {}
    if pin_map:
        for pair in pin_map.split(","):
            a, sep, b = pair.partition("=")
            if not sep or a.strip() not in old or b.strip() not in new:
                raise ValueError(f"Invalid pin mapping {pair!r}; expected an existing OLD=NEW pin number")
            mapping[a.strip()] = b.strip()
    for num, p in old.items():
        if num in mapping:
            continue
        matches = [q["number"] for q in after if q["name"] == p["name"] and q["name"] not in ("", "~")]
        if map_by in ("auto", "name") and len(set(matches)) == 1:
            mapping[num] = matches[0]
        elif map_by in ("auto", "number") and num in new:
            mapping[num] = num
    if len(set(mapping.values())) != len(mapping):
        raise ValueError("Pin map must be one-to-one; resolve duplicate pin names with --pin-map")
    moves = {}
    for num, target in mapping.items():
        a, b = old[num], new[target]
        src, dst = (round(a["x"], 6), round(a["y"], 6)), (round(b["x"], 6), round(b["y"], 6))
        if src != dst:
            if src in moves and moves[src] != dst:
                raise ValueError("Stacked pins would separate; supply a compatible symbol")
            moves[src] = dst
    moved_wires, dangling = [], []
    for wire in tree.find_all("wire"):
        pts = wire.find("pts").find_all("xy")
        changed = False
        for xy in pts:
            dst = moves.get((round(_f(xy[1]), 6), round(_f(xy[2]), 6)))
            if dst:
                dangling.append(wire.value("uuid"))
                if rewire:
                    xy[1:3] = [sexpr.fmt_num(v) for v in dst]
                    changed = True
        if changed:
            moved_wires.append(wire.value("uuid"))
    if rewire:
        for tag in ("label", "global_label", "hierarchical_label", "no_connect", "junction"):
            for item in tree.find_all(tag):
                at = item.find("at")
                dst = moves.get((round(_f(at[1]), 6), round(_f(at[2]), 6))) if at is not None else None
                if dst:
                    at[1:3] = [sexpr.fmt_num(v) for v in dst]
    prune_lib_symbols(tree)
    return {"reference": ref, "lib_id": new_lib_id, "units": len(syms), "pin_map": mapping,
            "connections_lost": [], "dangling_wires": [] if rewire else sorted(set(dangling)),
            "moved_wires": sorted(set(moved_wires))}


def _pin_signature(sym: SList) -> Dict[Tuple[str, str], Tuple]:
    """(unit symbol, pin number) -> (x, y, angle, electrical type) of a library symbol."""
    out = {}
    for unit in [sym] + sym.find_all("symbol"):
        for pin in unit.find_all("pin"):
            at = pin.find("at")
            out[(str(unit[1]), pin.value("number") or "")] = (
                tuple(round(float(v), 4) for v in at.atoms()) if at is not None else (), str(pin[1]))
    return out


def update_lib_symbols(tree: SList, proj, only: Optional[str] = None) -> Dict[str, Any]:
    """Refresh the embedded lib_symbols cache from the libraries (Update Symbols from Library).

    Reports which symbols really changed and, for those, pins whose position, number or type
    changed (wires attached to them may need attention: check with `kce check erc`).
    """
    updated, unchanged, missing, pins_changed = [], [], [], {}
    ls = lib_symbols(tree)
    for i, c in enumerate(ls):
        if not (isinstance(c, SList) and c.tag == "symbol"):
            continue
        lid = str(c[1])
        if only and not fnmatch.fnmatchcase(lid, only):
            continue
        try:
            sym, _ = lib_mod.resolve_symbol(proj, lid)
        except (KeyError, FileNotFoundError, ValueError) as e:
            missing.append({"lib_id": lid, "error": str(e)})
            continue
        sym = copy.deepcopy(sym)
        sym[1] = Q(lid)
        if sexpr.dumps(sym) == sexpr.dumps(c):
            unchanged.append(lid)
            continue
        old_pins, new_pins = _pin_signature(c), _pin_signature(sym)
        diff = sorted({k[1] for k in set(old_pins) | set(new_pins) if old_pins.get(k) != new_pins.get(k)})
        if diff:
            pins_changed[lid] = diff
        ls[i] = sym
        updated.append(lid)
    return {"updated": updated, "unchanged": unchanged, "missing": missing, "pins_changed": pins_changed}


# ---------------------------------------------------------------------------
# Wires, junctions, labels, text, no-connects, buses
# ---------------------------------------------------------------------------


def _stroke(width: float = 0, kind: str = "default") -> SList:
    return S("stroke", S("width", width), S("type", kind))


def add_wire(tree: SList, points: List[Tuple[float, float]], bus: bool = False) -> List[SList]:
    """Add a polyline as individual 2-point wire segments (KiCad stores segments)."""
    if len(points) < 2:
        raise ValueError("A wire needs at least two points")
    out = []
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        if abs(x1 - x2) < EPS and abs(y1 - y2) < EPS:
            continue
        w = S("bus" if bus else "wire", S("pts", S("xy", round(x1, 4), round(y1, 4)), S("xy", round(x2, 4), round(y2, 4))),
              _stroke(0, "default"), S("uuid", Q(new_uuid())))
        _insert_item(tree, w)
        out.append(w)
    return out


def route_points(a: Tuple[float, float], b: Tuple[float, float], mode: str = "hv",
                 via: Optional[List[Tuple[float, float]]] = None) -> List[Tuple[float, float]]:
    """Orthogonal path between two points: hv (horizontal first), vh, or direct."""
    pts = [a] + list(via or []) + [b]
    out = [pts[0]]
    for p, q in zip(pts, pts[1:]):
        if mode == "direct" or abs(p[0] - q[0]) < EPS or abs(p[1] - q[1]) < EPS:
            out.append(q)
        elif mode == "vh":
            out.extend([(p[0], q[1]), q])
        else:
            out.extend([(q[0], p[1]), q])
    return out


def add_junction(tree: SList, x: float, y: float, diameter: float = 0) -> SList:
    j = S("junction", S("at", round(x, 4), round(y, 4)), S("diameter", diameter), S("color", 0, 0, 0, 0),
          S("uuid", Q(new_uuid())))
    _insert_item(tree, j)
    return j


def add_no_connect(tree: SList, x: float, y: float) -> SList:
    n = S("no_connect", S("at", round(x, 4), round(y, 4)), S("uuid", Q(new_uuid())))
    _insert_item(tree, n)
    return n


LABEL_KINDS = {"local": "label", "global": "global_label", "hierarchical": "hierarchical_label",
               "directive": "directive_label"}
LABEL_SHAPES = ["input", "output", "bidirectional", "tri_state", "passive"]


def add_label(tree: SList, text: str, x: float, y: float, angle: float = 0, kind: str = "local",
              shape: str = "bidirectional", size: float = 1.27) -> SList:
    if kind not in LABEL_KINDS:
        raise ValueError(f"label kind must be one of {list(LABEL_KINDS)}")
    tag = LABEL_KINDS[kind]
    a = int(angle) % 360
    justify = {0: "left", 90: "left", 180: "right", 270: "right"}[a]
    lbl = S(tag, Q(text))
    if kind in ("global", "hierarchical"):
        if shape not in LABEL_SHAPES:
            raise ValueError(f"shape must be one of {LABEL_SHAPES}")
        lbl.append(S("shape", shape))
    lbl.append(S("at", round(x, 4), round(y, 4), a))
    if kind == "global":
        lbl.append(S("fields_autoplaced", "yes"))
    eff = S("effects", S("font", S("size", size, size)), S("justify", justify) if kind != "local"
            else S("justify", justify, "bottom"))
    lbl.append(eff)
    lbl.append(S("uuid", Q(new_uuid())))
    if kind == "global":
        lbl.append(S("property", Q("Intersheetrefs"), Q("${INTERSHEET_REFS}"), S("at", round(x, 4), round(y, 4), 0),
                     S("hide", "yes"), S("show_name", "no"), S("do_not_autoplace", "no"),
                     S("effects", S("font", S("size", size, size)), S("justify", "left"))))
    _insert_item(tree, lbl)
    return lbl


def add_text(tree: SList, text: str, x: float, y: float, angle: float = 0, size: float = 1.27) -> SList:
    t = S("text", Q(text), S("exclude_from_sim", "no"), S("at", round(x, 4), round(y, 4), int(angle) % 360),
          S("effects", S("font", S("size", size, size)), S("justify", "left", "bottom")), S("uuid", Q(new_uuid())))
    _insert_item(tree, t)
    return t


def add_bus_entry(tree: SList, x: float, y: float, dx: float = 2.54, dy: float = 2.54) -> SList:
    e = S("bus_entry", S("at", x, y), S("size", dx, dy), _stroke(0, "default"), S("uuid", Q(new_uuid())))
    _insert_item(tree, e)
    return e


def label_pin(tree: SList, ref: str, pin: str, net: str, kind: str = "local", stub: float = 2.54,
              shape: str = "bidirectional") -> Dict[str, Any]:
    """Attach a net label to a pin through a short wire stub pointing away from the body."""
    sym, p = find_pin(tree, ref, pin)
    ox, oy = p["outward"]
    x0, y0 = p["x"], p["y"]
    x1, y1 = round(x0 + ox * stub, 4), round(y0 + oy * stub, 4)
    items = []
    if stub > 0:
        items += add_wire(tree, [(x0, y0), (x1, y1)])
    angle = {(1, 0): 0, (-1, 0): 180, (0, -1): 90, (0, 1): 270}.get((ox, oy), 0)
    lbl = add_label(tree, net, x1, y1, angle=angle, kind=kind, shape=shape)
    return {"pin": f"{ref}:{pin}", "net": net, "kind": kind, "at": [x1, y1], "pin_at": [x0, y0],
            "label_uuid": lbl.value("uuid"), "wires": len(items)}


def no_connect_pin(tree: SList, ref: str, pin: str) -> Dict[str, Any]:
    sym, p = find_pin(tree, ref, pin)
    n = add_no_connect(tree, p["x"], p["y"])
    return {"pin": f"{ref}:{pin}", "at": [p["x"], p["y"]], "uuid": n.value("uuid")}


def unconnected_pins(tree: SList, ref: Optional[str] = None) -> List[Dict[str, Any]]:
    """Pins with nothing attached, matching KiCad's ``pin_not_connected`` ERC check.

    A pin is connected when a wire touches its point, a label sits on it, a no-connect flag
    covers it, or another pin shares the point (stacked pins, a power symbol placed on it).
    Hidden pins are skipped, as KiCad's ERC skips them.
    """
    if ref is not None and not find_symbols(tree, ref):
        find_symbol(tree, ref)  # raises KeyError listing the available references
    segs = wire_segments(tree)
    label_pts = set()
    for tag in ("label", "global_label", "hierarchical_label", "directive_label"):
        for it in tree.find_all(tag):
            at = it.find("at")
            if at is not None:
                label_pts.add((round(_f(at[1]), 3), round(_f(at[2]), 3)))
    nc_pts = set()
    for it in tree.find_all("no_connect"):
        at = it.find("at")
        if at is not None:
            nc_pts.add((round(_f(at[1]), 3), round(_f(at[2]), 3)))

    pins = []
    for sym in placed_symbols(tree):
        try:
            sym_pins = symbol_pins(tree, sym)
        except KeyError:
            continue  # no library cache entry: nothing reliable to test
        for p in sym_pins:
            pins.append((sym, p, (round(p["x"], 3), round(p["y"], 3))))
    point_counts = collections.Counter(pt for _, p, pt in pins if not p.get("hidden"))

    out = []
    for sym, p, pt in pins:
        r = sym_ref(sym)
        if ref is not None and r != ref:
            continue
        if ref is None and r.startswith("#"):
            continue
        if p.get("hidden") or p.get("type") == "no_connect":
            continue
        if point_counts[pt] > 1 or pt in nc_pts or pt in label_pts:
            continue
        if any(_on_segment(pt, a, b) for a, b, _ in segs):
            continue
        out.append({"ref": r, "pin": p["number"], "name": p.get("name"), "type": p.get("type"),
                    "at": [p["x"], p["y"]]})
    out.sort(key=lambda x: (natural_key(x["ref"]), natural_key(x["pin"])))
    return out


def connect_pins(tree: SList, a: str, b: str, mode: str = "hv",
                 via: Optional[List[Tuple[float, float]]] = None, allow_merge: bool = False) -> Dict[str, Any]:
    """Wire two pins/points, choosing a path that joins nothing but those two.

    A path that runs over an existing wire (overlap or T), or through another pin, label,
    junction or no-connect, silently merges nets in KiCad.  Candidate L routes are tried
    (both orientations, then staircases offset by grid steps) and the shortest clean one wins.
    """
    pa = resolve_point(tree, a)
    pb = resolve_point(tree, b)
    pts = clean_route(tree, pa, pb, mode, via, allow_merge)
    wires = add_wire(tree, pts)
    juncs = auto_junctions(tree)
    return {"from": a, "to": b, "points": [list(p) for p in pts], "wires": [w.value("uuid") for w in wires],
            "junctions_added": juncs}


def wire_segments(tree: SList, bus: bool = False) -> List[Tuple[Tuple[float, float], Tuple[float, float], SList]]:
    segs = []
    for w in tree.find_all("bus" if bus else "wire"):
        xy = w.find("pts").find_all("xy")
        for a, b in zip(xy, xy[1:]):
            segs.append(((_f(a[1]), _f(a[2])), (_f(b[1]), _f(b[2])), w))
    return segs


def _on_segment(p, a, b) -> bool:
    (x, y), (x1, y1), (x2, y2) = p, a, b
    if abs((x2 - x1) * (y - y1) - (y2 - y1) * (x - x1)) > EPS * max(1, abs(x2 - x1) + abs(y2 - y1)):
        return False
    return min(x1, x2) - EPS <= x <= max(x1, x2) + EPS and min(y1, y2) - EPS <= y <= max(y1, y2) + EPS


class RouteBlockedError(RuntimeError):
    """No clean route exists between two points (every candidate would touch other geometry)."""


class NetMergeError(RuntimeError):
    """An edit would join nets that the command did not ask to join."""

    def __init__(self, merges: List[Dict[str, Any]], command: str = ""):
        self.merges = merges
        lines = []
        for m in merges:
            pins = ", ".join(f"{p['ref']}:{p['pin']}" for p in m["pins"])
            lines.append(f"{' + '.join(m['nets'])} ({pins})")
        super().__init__(
            (f"{command + ': ' if command else ''}this edit would merge nets that were separate "
             f"before: " + "; ".join(lines) + ". The edit was rolled back; nothing was saved. "
             "Separate the geometry, or pass --allow-merge to join them on purpose."))


def _pts_near(p, q, tol: float = 0.01) -> bool:
    return abs(p[0] - q[0]) <= tol and abs(p[1] - q[1]) <= tol


def _seg_contacts(a, b, c, d, tol: float = 0.01) -> List[Tuple[float, float]]:
    """Where segment a-b touches segment c-d: [] (no), one point, or both ends of an overlap."""
    rx, ry = b[0] - a[0], b[1] - a[1]
    sx, sy = d[0] - c[0], d[1] - c[1]
    rlen = math.hypot(rx, ry)
    slen = math.hypot(sx, sy)
    if rlen < tol or slen < tol:
        p = a if rlen < tol else c
        other = (c, d) if rlen < tol else (a, b)
        return [p] if _on_segment(p, *other) else []
    denom = rx * sy - ry * sx
    if abs(denom) > tol * rlen * slen:
        t = ((c[0] - a[0]) * sy - (c[1] - a[1]) * sx) / denom
        u = ((c[0] - a[0]) * ry - (c[1] - a[1]) * rx) / denom
        if -tol / rlen <= t <= 1 + tol / rlen and -tol / slen <= u <= 1 + tol / slen:
            return [(a[0] + t * rx, a[1] + t * ry)]
        return []
    # parallel: collinear if c lies on a-b
    if abs((c[0] - a[0]) * ry - (c[1] - a[1]) * rx) > tol * rlen:
        return []
    rr = rx * rx + ry * ry
    t0 = ((c[0] - a[0]) * rx + (c[1] - a[1]) * ry) / rr
    t1 = ((d[0] - a[0]) * rx + (d[1] - a[1]) * ry) / rr
    lo, hi = max(0.0, min(t0, t1)), min(1.0, max(t0, t1))
    if lo > hi + tol / rlen:
        return []
    p0 = (a[0] + lo * rx, a[1] + lo * ry)
    if hi - lo <= tol / rlen:
        return [p0]
    return [p0, (a[0] + hi * rx, a[1] + hi * ry)]


def _path_length(pts: List[Tuple[float, float]]) -> float:
    return sum(math.hypot(q[0] - p[0], q[1] - p[1]) for p, q in zip(pts, pts[1:]))


def _forbidden_points(tree: SList) -> List[Tuple[float, float]]:
    """Connection points that a new wire must not touch: pins, labels, junctions, no-connects."""
    pts: List[Tuple[float, float]] = []
    for s in placed_symbols(tree):
        try:
            pts += [(p["x"], p["y"]) for p in symbol_pins(tree, s)]
        except KeyError:
            pass
    for tag in ("junction", "no_connect", "label", "global_label", "hierarchical_label", "directive_label"):
        for n in tree.find_all(tag):
            at = n.find("at")
            if at is not None:
                pts.append((_f(at[1]), _f(at[2])))
    for sh in tree.find_all("sheet"):
        for p in sh.find_all("pin"):
            at = p.find("at")
            if at is not None:
                pts.append((_f(at[1]), _f(at[2])))
    return pts


def wire_cluster(tree: SList, anchors: List[Tuple[float, float]]) -> Tuple[set, List[Tuple[float, float]]]:
    """Wires attached to ``anchors`` and every end point they reach, branches that end
    mid-segment (T joins) included.  Returns (``id()`` of each wire, points)."""
    segs = wire_segments(tree)
    reached: set = set()
    queue = list(anchors)
    while queue:
        p = queue.pop()
        for i, (c, d, _) in enumerate(segs):
            if i in reached or not (_pts_near(p, c) or _pts_near(p, d) or _on_segment(p, c, d)):
                continue
            reached.add(i)
            queue += [c, d]
            for j, (c2, d2, _) in enumerate(segs):  # a wire ending on this segment joins it
                if j not in reached:
                    queue += [q for q in (c2, d2) if _on_segment(q, c, d)]
    points = list(anchors) + [q for i in reached for q in segs[i][:2]]
    return {id(segs[i][2]) for i in reached}, points


def own_geometry(tree: SList, anchors: List[Tuple[float, float]]
                 ) -> Tuple[set, List[Tuple[float, float]]]:
    """Wires and labels attached to ``anchors`` — the net(s) a route starts and ends on.

    A ``sch label-pin`` draws a stub wire from the pin with its label at the far end.  That
    stub is part of the pin's net, so a route may run over it and past its label; treating
    them as foreign would make a pin-to-pin connection across its own stub impossible.
    """
    wires, points = wire_cluster(tree, anchors)
    attached = []
    for tag in ("label", "global_label", "hierarchical_label", "directive_label", "junction",
                "no_connect"):
        for n in tree.find_all(tag):
            at = n.find("at")
            if at is not None and any(_pts_near((_f(at[1]), _f(at[2])), p) for p in points):
                attached.append((_f(at[1]), _f(at[2])))
    return wires, attached


def _route_context(tree: SList, anchors: List[Tuple[float, float]]):
    """What every candidate path is checked against; computed once per route, not per path."""
    own_wires, own_points = own_geometry(tree, anchors)
    return own_wires, own_points, _forbidden_points(tree), wire_segments(tree)


def path_conflicts(tree: SList, points: List[Tuple[float, float]],
                   allowed: Optional[List[Tuple[float, float]]] = None,
                   own_anchors: Optional[List[Tuple[float, float]]] = None,
                   context=None) -> List[str]:
    """Why ``points`` (a polyline) would touch existing geometry, ignoring contacts at ``allowed``
    and geometry attached to ``own_anchors`` (the route's own net, see :func:`own_geometry`)."""
    allowed = allowed or []
    own_wires, own_points, forbidden, segments = context or _route_context(tree, own_anchors or [])
    problems: List[str] = []

    def permitted(p) -> bool:
        return any(_pts_near(p, q) for q in allowed) or any(_pts_near(p, q) for q in own_points)

    for point in forbidden:
        if permitted(point):
            continue
        for a, b in zip(points, points[1:]):
            if _on_segment(point, a, b):
                problems.append(f"touches ({point[0]:g},{point[1]:g})")
                break
    for a, b in zip(points, points[1:]):
        for c, d, w in segments:
            if id(w) in own_wires:
                continue
            contacts = _seg_contacts(a, b, c, d)
            if not contacts:
                continue
            # an overlap of positive length is always a conflict (a duplicate/merged segment)
            if len(contacts) == 2:
                problems.append(f"overlaps the wire ({c[0]:g},{c[1]:g})-({d[0]:g},{d[1]:g})")
                break
            if not permitted(contacts[0]):
                problems.append(f"crosses the wire ({c[0]:g},{c[1]:g})-({d[0]:g},{d[1]:g})")
                break
    return problems


def route_candidates(a: Tuple[float, float], b: Tuple[float, float], mode: str = "hv",
                     via: Optional[List[Tuple[float, float]]] = None,
                     max_detour_steps: int = 16) -> List[List[Tuple[float, float]]]:
    """L routes between a and b: the requested orientation, the other one, then grid detours."""
    if via:
        return [route_points(a, b, mode, via)]
    if mode == "direct":
        return [route_points(a, b, "direct")]
    out: List[List[Tuple[float, float]]] = []
    orders = ["hv", "vh"] if mode != "vh" else ["vh", "hv"]
    for m in orders:
        out.append(route_points(a, b, m))
    (x1, y1), (x2, y2) = a, b
    if abs(x1 - x2) < EPS or abs(y1 - y2) < EPS:
        return out  # a straight run has no corner to move
    for m in orders:
        for k in range(1, max_detour_steps + 1):
            for sign in (1, -1):
                d = sign * k * GRID
                if m == "hv":
                    out.append([a, (x1, y1 + d), (x2, y1 + d), b])
                    out.append([a, (x2 + d, y1), (x2 + d, y2), b])
                else:
                    out.append([a, (x1 + d, y1), (x1 + d, y2), b])
                    out.append([a, (x1, y2 + d), (x2, y2 + d), b])
    return out


def clean_route(tree: SList, a: Tuple[float, float], b: Tuple[float, float], mode: str = "hv",
                via: Optional[List[Tuple[float, float]]] = None,
                allow_merge: bool = False) -> List[Tuple[float, float]]:
    """Shortest candidate path that touches nothing but its two endpoints."""
    cands = route_candidates(a, b, mode, via)
    if allow_merge:
        return cands[0]
    context = _route_context(tree, [a, b])
    # shortest first, ties in candidate order: the first clean one is the one to draw
    for _, pts in sorted(enumerate(cands), key=lambda c: (_path_length(c[1]), c[0])):
        if not path_conflicts(tree, pts, [a, b], [a, b], context):
            return pts
    raise RouteBlockedError(
        f"No clean orthogonal route from ({a[0]:g},{a[1]:g}) to ({b[0]:g},{b[1]:g}): every candidate "
        "touches an existing wire, pin, label or junction. Move the parts, draw the wire explicitly "
        "with `kce sch wire 'x1,y1;x2,y2;...'`, or pass --allow-merge to draw it anyway.")


def connection_points(tree: SList) -> List[Tuple[float, float]]:
    pts = []
    for a, b, _ in wire_segments(tree):
        pts += [a, b]
    for s in placed_symbols(tree):
        try:
            pts += [(p["x"], p["y"]) for p in symbol_pins(tree, s)]
        except KeyError:
            pass
    return pts


def auto_junctions(tree: SList) -> int:
    """Add junctions where >=3 connection items meet (like KiCad's auto-junction)."""
    segs = wire_segments(tree)
    existing = {(round(_f(j.find("at")[1]), 3), round(_f(j.find("at")[2]), 3)) for j in tree.find_all("junction")}
    pin_pts = []
    for s in placed_symbols(tree):
        try:
            pin_pts += [(p["x"], p["y"]) for p in symbol_pins(tree, s)]
        except KeyError:
            pass
    cands = set()
    for a, b, _ in segs:
        cands.add((round(a[0], 3), round(a[1], 3)))
        cands.add((round(b[0], 3), round(b[1], 3)))
    added = 0
    for c in cands:
        if c in existing:
            continue
        n = 0
        for a, b, _ in segs:
            if (abs(a[0] - c[0]) < EPS and abs(a[1] - c[1]) < EPS) or (abs(b[0] - c[0]) < EPS and abs(b[1] - c[1]) < EPS):
                n += 1
            elif _on_segment(c, a, b):
                n += 2  # T into middle of a segment
        n += sum(1 for p in pin_pts if abs(p[0] - c[0]) < EPS and abs(p[1] - c[1]) < EPS)
        if n >= 3:
            add_junction(tree, c[0], c[1])
            existing.add(c)
            added += 1
    return added


def items_at(tree: SList, x: float, y: float, tol: float = 0.01) -> List[Dict[str, Any]]:
    out = []
    for tag in ("junction", "no_connect", "label", "global_label", "hierarchical_label", "text", "bus_entry"):
        for n in tree.find_all(tag):
            at = n.find("at")
            if at is not None and abs(_f(at[1]) - x) <= tol and abs(_f(at[2]) - y) <= tol:
                out.append({"type": tag, "uuid": n.value("uuid"), "text": str(n[1]) if len(n) > 1 and not isinstance(n[1], SList) else None})
    for a, b, w in wire_segments(tree) + wire_segments(tree, True):
        if _on_segment((x, y), a, b):
            out.append({"type": w.tag, "uuid": w.value("uuid"), "from": list(a), "to": list(b)})
    for s in placed_symbols(tree):
        try:
            for p in symbol_pins(tree, s):
                if abs(p["x"] - x) <= tol and abs(p["y"] - y) <= tol:
                    out.append({"type": "pin", "reference": sym_ref(s), "pin": p["number"], "name": p["name"]})
        except KeyError:
            pass
    return out


ITEM_TAGS = ("wire", "bus", "junction", "no_connect", "label", "global_label", "hierarchical_label",
             "directive_label", "text", "text_box", "bus_entry", "symbol", "sheet", "polyline", "rectangle",
             "circle", "arc", "image", "table", "rule_area")


def find_by_uuid(tree: SList, uid: str) -> Tuple[SList, SList]:
    for n in sexpr.walk(tree):
        if n.value("uuid") == uid and n.tag in ITEM_TAGS + ("pin", "property"):
            # find parent
            for parent in sexpr.walk(tree):
                for c in parent:
                    if c is n:
                        return parent, n
    raise KeyError(f"No item with uuid {uid}")


def delete_item(tree: SList, uid: str) -> str:
    parent, n = find_by_uuid(tree, uid)
    parent.remove_child(n)
    if n.tag == "symbol":
        prune_lib_symbols(tree)
    return n.tag


def list_items(tree: SList, kinds: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    out = []
    for c in tree.children():
        if c.tag not in ITEM_TAGS or c.tag == "symbol":
            continue
        if kinds and c.tag not in kinds:
            continue
        d = {"type": c.tag, "uuid": c.value("uuid")}
        if c.tag in ("wire", "bus", "polyline"):
            d["points"] = [[_f(xy[1]), _f(xy[2])] for xy in c.find("pts").find_all("xy")]
        at = c.find("at")
        if at is not None:
            d["at"] = [_f(v) for v in at.atoms()]
        if c.tag in ("label", "global_label", "hierarchical_label", "text", "directive_label"):
            d["text"] = str(c[1])
            if c.find("shape") is not None:
                d["shape"] = c.value("shape")
        if c.tag == "sheet":
            d.update(sheet_dict(c))
        out.append(d)
    return out


def labels(tree: SList) -> List[Dict[str, Any]]:
    return list_items(tree, ["label", "global_label", "hierarchical_label", "directive_label"])


# ---------------------------------------------------------------------------
# Sheets
# ---------------------------------------------------------------------------


def add_sheet(tree: SList, name: str, filename: str, x: float, y: float, w: float, h: float,
              project_name: str, parent_path: str, page: str) -> SList:
    uid = new_uuid()
    sh = S("sheet", S("at", snap(x), snap(y)), S("size", snap(w), snap(h)), S("exclude_from_sim", "no"),
           S("in_bom", "yes"), S("on_board", "yes"), S("dnp", "no"), S("fields_autoplaced", "yes"),
           _stroke(0.1524, "solid"), S("fill", S("color", 0, 0, 0, 0.0)), S("uuid", Q(uid)),
           S("property", Q("Sheetname"), Q(name), S("at", snap(x), round(snap(y) - 0.7116, 4), 0),
             S("show_name", "no"), S("do_not_autoplace", "no"),
             S("effects", S("font", S("size", 1.27, 1.27)), S("justify", "left", "bottom"))),
           S("property", Q("Sheetfile"), Q(filename), S("at", snap(x), round(snap(y) + snap(h) + 0.5754, 4), 0),
             S("show_name", "no"), S("do_not_autoplace", "no"),
             S("effects", S("font", S("size", 1.27, 1.27)), S("justify", "left", "top"))),
           S("instances", S("project", Q(project_name), S("path", Q(parent_path), S("page", Q(page))))))
    _insert_item(tree, sh)
    return sh


def find_sheet(tree: SList, name_or_uuid: str) -> SList:
    for sh in tree.find_all("sheet"):
        d = sheet_dict(sh)
        if name_or_uuid in (d["name"], d["uuid"], d["file"]):
            return sh
    raise KeyError(f"No sheet {name_or_uuid!r}. Sheets: {[sheet_dict(s)['name'] for s in tree.find_all('sheet')]}")


def add_sheet_pin(sheet: SList, name: str, shape: str = "bidirectional", side: str = "right",
                  offset: Optional[float] = None) -> SList:
    if shape not in LABEL_SHAPES:
        raise ValueError(f"shape must be one of {LABEL_SHAPES}")
    d = sheet_dict(sheet)
    n = len(d["pins"])
    off = offset if offset is not None else 2.54 * (n + 1)
    if side == "right":
        x, y, ang, just = d["x"] + d["width"], d["y"] + off, 0, "right"
    elif side == "left":
        x, y, ang, just = d["x"], d["y"] + off, 180, "left"
    elif side == "top":
        x, y, ang, just = d["x"] + off, d["y"], 90, "right"
    elif side == "bottom":
        x, y, ang, just = d["x"] + off, d["y"] + d["height"], 270, "left"
    else:
        raise ValueError("side must be left|right|top|bottom")
    pin = S("pin", Q(name), shape, S("at", snap(x), snap(y), ang), S("uuid", Q(new_uuid())),
            S("effects", S("font", S("size", 1.27, 1.27)), S("justify", just)))
    idx = max(i for i, c in enumerate(sheet) if isinstance(c, SList) and c.tag in ("property", "pin"))
    sheet.insert(idx + 1, pin)
    return pin


def next_page_number(load_tree, root_path: str) -> str:
    pages = []
    for h in hierarchy(load_tree, root_path):
        pages.append(h)
    return str(len(pages) + 1)


# ---------------------------------------------------------------------------
# Annotation
# ---------------------------------------------------------------------------


def ref_prefix(ref: str) -> str:
    m = re.match(r"^(#?[A-Za-z_]+?)(\d+|\?)?$", ref or "")
    return m.group(1) if m else re.sub(r"[\d?]+$", "", ref or "U")


def ref_number(ref: str) -> Optional[int]:
    m = re.search(r"(\d+)$", ref or "")
    return int(m.group(1)) if m and not (ref or "").endswith("?") else None


def annotate(trees: Dict[str, SList], instances: List[Dict[str, Any]], mode: str = "unannotated",
             order: str = "x", start: int = 1, sheet_offset: int = 0) -> List[Dict[str, Any]]:
    """Annotate symbols across the hierarchy.

    ``trees``: file path -> tree. ``instances``: hierarchy entries (file, path).
    ``mode``: ``unannotated`` (only ``X?``), ``all`` (renumber everything).
    ``order``: ``x`` (sort by X then Y) or ``y``.
    ``sheet_offset``: if >0, sheet N starts numbering at N*offset (e.g. 100).
    Multi-unit symbols sharing a reference keep sharing it.
    """
    changes = []
    used: Dict[str, set] = {}
    entries = []
    multi_unit: set = set()
    norm_trees = {os.path.normcase(os.path.abspath(k)): v for k, v in trees.items()}
    for tree in trees.values():
        for ls in lib_symbols(tree).find_all("symbol"):
            units = {p["unit"] for p in lib_mod.pin_list(ls)} - {0}
            if len(units) > 1:
                multi_unit.add(str(ls[1]))
    for page_idx, inst in enumerate(instances):
        tree = norm_trees[os.path.normcase(os.path.abspath(inst["file"]))]
        for s in placed_symbols(tree):
            refs = instance_references(s)
            ref = refs.get(inst["path"]) or sym_ref(s)
            at = s.find("at")
            entries.append({"sym": s, "path": inst["path"], "ref": ref, "page": page_idx,
                            "x": _f(at[1]), "y": _f(at[2]), "unit": int(s.value("unit", "1")),
                            "file": inst["file"], "lib_id": s.value("lib_id"),
                            "value": s.get_property("Value", "")})
    for e in entries:
        n = ref_number(e["ref"])
        if mode == "unannotated" and n is not None:
            used.setdefault(ref_prefix(e["ref"]), set()).add(n)
    key = (lambda e: (e["page"], e["x"], e["y"])) if order == "x" else (lambda e: (e["page"], e["y"], e["x"]))
    entries.sort(key=key)
    assigned: Dict[Tuple[str, str], str] = {}
    # multi-unit packages: (path, lib_id, value) -> list of [ref, set(units)]
    packages: Dict[Tuple[str, str, str], List[list]] = {}
    if mode == "unannotated":
        for e in entries:
            if ref_number(e["ref"]) is not None and e["lib_id"] in multi_unit:
                k = (e["path"], e["lib_id"], e["value"])
                pk = next((p for p in packages.get(k, []) if p[0] == e["ref"]), None)
                if pk is None:
                    packages.setdefault(k, []).append([e["ref"], {e["unit"]}])
                else:
                    pk[1].add(e["unit"])

    def new_ref(prefix, page):
        nums = used.setdefault(prefix, set())
        n = start if not sheet_offset else max(start, page * sheet_offset + start)
        while n in nums:
            n += 1
        nums.add(n)
        return f"{prefix}{n}"

    for e in entries:
        prefix = ref_prefix(e["ref"])
        cur_n = ref_number(e["ref"])
        if mode == "unannotated" and cur_n is not None:
            continue
        group_key = (e["path"], e["ref"]) if cur_n is not None else None
        pkg_key = (e["path"], e["lib_id"], e["value"])
        if group_key and group_key in assigned:
            new = assigned[group_key]
        elif cur_n is None and e["lib_id"] in multi_unit and any(
                e["unit"] not in p[1] for p in packages.get(pkg_key, [])):
            pk = next(p for p in packages[pkg_key] if e["unit"] not in p[1])
            new = pk[0]
        else:
            new = new_ref(prefix, e["page"])
            if group_key:
                assigned[group_key] = new
        if e["lib_id"] in multi_unit:
            pk = next((p for p in packages.get(pkg_key, []) if p[0] == new), None)
            if pk is None:
                packages.setdefault(pkg_key, []).append([new, {e["unit"]}])
            else:
                pk[1].add(e["unit"])
        if new != e["ref"]:
            changes.append({"file": e["file"], "path": e["path"], "old": e["ref"], "new": new, "unit": e["unit"]})
        set_instance_reference(e["sym"], new, e["path"])
        if len(instance_references(e["sym"])) <= 1:
            p = e["sym"].property("Reference")
            if p is not None:
                p[2] = Q(new)
        else:
            first = list(instance_references(e["sym"]).values())[0]
            e["sym"].property("Reference")[2] = Q(first)
    return changes


def clear_annotation(trees: Dict[str, SList]) -> int:
    n = 0
    for tree in trees.values():
        for s in placed_symbols(tree):
            ref = sym_ref(s)
            new = ref_prefix(ref) + "?"
            if new != ref:
                inst = s.find("instances")
                if inst is not None:
                    for proj in inst.find_all("project"):
                        for pth in proj.find_all("path"):
                            r = pth.find("reference")
                            if r is not None:
                                r[1] = Q(new)
                s.property("Reference")[2] = Q(new)
                n += 1
    return n


# ---------------------------------------------------------------------------
# Title block / page
# ---------------------------------------------------------------------------

TITLE_FIELDS = ("title", "date", "rev", "company")


def set_title_block(tree: SList, values: Dict[str, str], comments: Dict[int, str]) -> SList:
    tb = tree.find("title_block")
    if tb is None:
        tb = S("title_block")
        idx = next(i for i, c in enumerate(tree) if isinstance(c, SList) and c.tag == "paper")
        tree.insert(idx + 1, tb)
    for k, v in values.items():
        if k not in TITLE_FIELDS:
            raise KeyError(f"title block field must be one of {TITLE_FIELDS} or comment N")
        tb.set(k, Q(v))
    for n, v in comments.items():
        found = False
        for c in tb.find_all("comment"):
            if c.value() == str(n):
                c[2] = Q(v)
                found = True
        if not found:
            tb.append(S("comment", n, Q(v)))
    order = {t: i for i, t in enumerate(TITLE_FIELDS + ("comment",))}
    head, kids = tb[:1], tb.children()
    kids.sort(key=lambda c: (order.get(c.tag, 99), int(c.value() or 0) if c.tag == "comment" else 0))
    tb[:] = head + kids
    return tb


def set_paper(tree: SList, paper: str, portrait: bool = False) -> None:
    p = tree.find("paper")
    node = S("paper", Q(paper), "portrait" if portrait else None)
    if p is None:
        tree.insert(5, node)
    else:
        p[:] = node
