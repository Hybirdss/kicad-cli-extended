"""Symbol & footprint libraries: indexing, search, inspection, flattening,
footprint suggestion and creation of simple symbols / footprints."""

from __future__ import annotations

import concurrent.futures as cf
import copy
import fnmatch
import glob
import hashlib
import json
import math
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from .. import APP_NAME
from . import sexpr
from .session import atomic_write_text
from . import project as proj_mod
from .netlist import natural_key
from .sexpr import Q, S, SList
from ..utils import kicad_backend as kb
from ..utils.paths import kicad_uri

CACHE_DIR = kb.cache_dir()


def split_lib_id(lib_id: str) -> Tuple[str, str]:
    if ":" not in lib_id:
        raise ValueError(f"Expected 'Library:Name', got {lib_id!r}")
    lib, name = lib_id.split(":", 1)
    return lib, name


# ---------------------------------------------------------------------------
# Symbol library parsing
# ---------------------------------------------------------------------------


def _symbol_pins(sym: SList) -> List[SList]:
    pins = []
    for sub in sym.find_all("symbol"):
        pins.extend(sub.find_all("pin"))
    pins.extend(sym.find_all("pin"))
    return pins


def _sym_entry(lib: str, sym: SList, by_name: Dict[str, SList]) -> Dict[str, Any]:
    name = str(sym[1])
    base = sym
    ext = sym.value("extends")
    seen = set()
    while ext and ext in by_name and ext not in seen:
        seen.add(ext)
        base = by_name[ext]
        ext = base.value("extends")
    pins = _symbol_pins(base)
    units = set()
    for sub in base.find_all("symbol"):
        m = re.search(r"_(\d+)_(\d+)$", str(sub[1]))
        if m and m.group(1) != "0":
            units.add(int(m.group(1)))
    props = {str(p[1]): str(p[2]) for p in sym.find_all("property") if len(p) > 2}
    bprops = {str(p[1]): str(p[2]) for p in base.find_all("property") if len(p) > 2}
    for k, v in bprops.items():
        props.setdefault(k, v)
    pw = sym.find("power") or base.find("power")
    numbers = {p.value("number") for p in pins}
    return {
        "lib": lib, "name": name, "lib_id": f"{lib}:{name}",
        "extends": sym.value("extends"),
        "description": props.get("Description", ""),
        "keywords": props.get("ki_keywords", ""),
        "footprint": props.get("Footprint", ""),
        "fp_filters": props.get("ki_fp_filters", ""),
        "datasheet": props.get("Datasheet", ""),
        "reference": props.get("Reference", ""),
        "value": props.get("Value", name),
        "pin_count": len(numbers),
        "pin_total": len(pins),
        "units": max(units) if units else 1,
        "power": pw is not None,
    }


def parse_symbol_lib(path: str) -> List[Dict[str, Any]]:
    lib = os.path.splitext(os.path.basename(path))[0]
    tree = sexpr.load(path)
    syms = tree.find_all("symbol")
    by_name = {str(s[1]): s for s in syms}
    return [_sym_entry(lib, s, by_name) for s in syms]


def _parse_symbol_lib_for_index(args):
    lib, path = args
    try:
        entries = parse_symbol_lib(path)
        for e in entries:
            e["lib"] = lib
            e["lib_id"] = f"{lib}:{e['name']}"
            e["path"] = path
        return entries
    except Exception as ex:  # pragma: no cover - corrupt lib
        return [{"lib": lib, "error": str(ex), "path": path}]


_FP_RE = {
    "descr": re.compile(r'^\t\(descr "((?:[^"\\]|\\.)*)"\)', re.M),
    "tags": re.compile(r'^\t\(tags "((?:[^"\\]|\\.)*)"\)', re.M),
    "attr": re.compile(r"^\t\(attr ([^)]*)\)", re.M),
    "pad": re.compile(r'^\t\(pad "((?:[^"\\]|\\.)*)" (\w+)', re.M),
    "model": re.compile(r'^\t\(model "((?:[^"\\]|\\.)*)"', re.M),
}


def _parse_fp_for_index(args):
    lib, path = args
    out = []
    for f in sorted(glob.glob(os.path.join(path, "*.kicad_mod"))):
        try:
            txt = open(f, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        if not re.search(r"^\t\(", txt, re.M):
            # not KiCad-formatted: full parse
            try:
                t = sexpr.loads(txt)
                txt = sexpr.dumps(t)
            except sexpr.SexprError:
                continue
        name = os.path.splitext(os.path.basename(f))[0]
        pads = _FP_RE["pad"].findall(txt)
        numbers = {p[0] for p in pads if p[0] and p[1] != "np_thru_hole"}
        m = _FP_RE["descr"].search(txt)
        t = _FP_RE["tags"].search(txt)
        a = _FP_RE["attr"].search(txt)
        out.append({
            "lib": lib, "name": name, "fpid": f"{lib}:{name}", "path": f,
            "description": m.group(1).replace('\\"', '"') if m else "",
            "tags": t.group(1) if t else "",
            "attr": a.group(1).split() if a else [],
            "pad_count": len(numbers),
            "pad_total": len(pads),
            "smd": sum(1 for p in pads if p[1] == "smd"),
            "tht": sum(1 for p in pads if p[1] == "thru_hole"),
            "models": _FP_RE["model"].findall(txt),
        })
    return out


def _libs_signature(rows: List[Tuple[str, str]]) -> str:
    h = hashlib.sha256()
    h.update(kb.kicad_version().encode())
    for lib, path in rows:
        try:
            h.update(f"{lib}|{path}|{os.path.getmtime(path)}".encode())
        except OSError:
            h.update(f"{lib}|{path}|missing".encode())
    return h.hexdigest()[:16]


def _lib_rows(proj, kind: str, libs: Optional[List[str]] = None) -> List[Tuple[str, str]]:
    tables = proj_mod.lib_tables(proj)[kind]["libraries"]
    env = kb.kicad_env_vars(proj.dir if proj else None)
    rows = []
    for r in tables:
        if libs and r.get("name") not in libs:
            continue
        if r.get("type", "KiCad") not in ("KiCad", "kicad"):
            continue
        path = kb.expand_vars(r.get("uri", ""), env)
        if os.path.exists(path):
            rows.append((r["name"], path))
    return rows


def build_index(proj, kind: str, rebuild: bool = False, libs: Optional[List[str]] = None) -> List[Dict]:
    """Return the symbol ('sym') or footprint ('fp') index, cached on disk."""
    rows = _lib_rows(proj, kind, libs)
    sig = _libs_signature(rows)
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache = os.path.join(CACHE_DIR, f"{kind}-index-{sig}.json")
    if not rebuild and os.path.exists(cache):
        with open(cache, encoding="utf-8") as f:
            return json.load(f)
    fn = _parse_symbol_lib_for_index if kind == "sym" else _parse_fp_for_index
    entries: List[Dict] = []
    workers = min(len(rows), os.cpu_count() or 2) or 1
    if os.name == 'nt':
        workers = min(workers, 61)
    if len(rows) > 4 and workers > 1:
        with cf.ProcessPoolExecutor(max_workers=workers) as ex:
            for res in ex.map(fn, rows, chunksize=1):
                entries.extend(res)
    else:
        for r in rows:
            entries.extend(fn(r))
    atomic_write_text(cache, json.dumps(entries))
    return entries


def index_status(proj) -> Dict[str, Any]:
    res = {"cache_dir": CACHE_DIR}
    for kind in ("sym", "fp"):
        rows = _lib_rows(proj, kind)
        sig = _libs_signature(rows)
        cache = os.path.join(CACHE_DIR, f"{kind}-index-{sig}.json")
        res[kind] = {"libraries": len(rows), "cache": cache, "cached": os.path.exists(cache)}
    return res


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def search_terms(query: str) -> List[str]:
    """Treat hyphens, underscores and spaces as the same word boundary."""
    return [part for word in query.lower().split()
            for part in ([word] if any(c in word for c in "*?[") else re.sub(r"[-_]", " ", word).split())]


def _score(terms: List[str], fields: Dict[str, str]) -> float:
    name = " ".join(search_terms(fields["name"]))
    score = 0.0
    for t in terms:
        t = t.lower()
        if any(c in t for c in "*?["):
            if fnmatch.fnmatchcase(fields["name"].lower(), t):
                score += 10
                continue
            return 0.0
        hit = 0.0
        if name == t:
            hit = 20
        elif name.startswith(t):
            hit = 10
        elif t in name:
            hit = 6
        elif t == "dfn" and re.search(r"\b[a-z]*son(?=\b|[0-9])", name):
            hit = 3  # SON/VSON are alternate vendor names for the dual-flat no-lead family
        for k, w in (("keywords", 4), ("lib", 3), ("description", 2), ("tags", 3), ("extra", 1)):
            v = " ".join(search_terms(fields.get(k, "")))
            if v and re.search(r"(?<![a-z0-9])" + re.escape(t), v):
                hit = max(hit, w + 0.5)
            elif v and t in v:
                hit = max(hit, w)
        if hit == 0:
            return 0.0
        score += hit
    return score - len(name) * 0.01


def _parse_range(spec: Optional[str]) -> Optional[Tuple[int, int]]:
    if spec is None or spec == "":
        return None
    s = str(spec).lower()
    if s.endswith("+ep"):
        n = int(s[:-3])
        return n, n + 1
    if "-" in s:
        a, b = s.split("-", 1)
        return int(a or 0), int(b or 10 ** 9)
    if s.endswith("+"):
        return int(s[:-1]), 10 ** 9
    return int(s), int(s)


def search_symbols(proj, query: str = "", lib: Optional[str] = None, pins: Optional[str] = None,
                   power: Optional[bool] = None, limit: int = 25, footprint: Optional[str] = None) -> List[Dict]:
    idx = build_index(proj, "sym")
    terms = search_terms(query)
    rng = _parse_range(pins)
    res = []
    for e in idx:
        if "error" in e:
            continue
        if lib and not fnmatch.fnmatchcase(e["lib"], lib):
            continue
        if rng and not (rng[0] <= e["pin_count"] <= rng[1]):
            continue
        if power is not None and e["power"] != power:
            continue
        if footprint and footprint.lower() not in (e["footprint"] + " " + e["fp_filters"]).lower():
            continue
        if terms:
            sc = _score(terms, {"name": e["name"], "keywords": e["keywords"], "lib": e["lib"],
                                "description": e["description"], "extra": e["footprint"]})
            if sc <= 0:
                continue
        else:
            sc = 0
        res.append((sc, e))
    res.sort(key=lambda x: (-x[0], x[1]["lib_id"]))
    return [dict(e, score=round(s, 2)) for s, e in res[:limit]]


def search_footprints(proj, query: str = "", lib: Optional[str] = None, pads: Optional[str] = None,
                      kind: Optional[str] = None, limit: int = 25) -> List[Dict]:
    idx = build_index(proj, "fp")
    terms = search_terms(query)
    rng = _parse_range(pads)
    res = []
    for e in idx:
        if lib and not fnmatch.fnmatchcase(e["lib"], lib):
            continue
        if rng and not (rng[0] <= e["pad_count"] <= rng[1]):
            continue
        if kind == "smd" and "smd" not in e["attr"]:
            continue
        if kind == "tht" and "through_hole" not in e["attr"]:
            continue
        if terms:
            sc = _score(terms, {"name": e["name"], "tags": e["tags"], "lib": e["lib"],
                                "description": e["description"]})
            if sc <= 0:
                continue
        else:
            sc = 0
        res.append((sc, e))
    res.sort(key=lambda x: (-x[0], x[1]["fpid"]))
    return [dict(e, score=round(s, 2)) for s, e in res[:limit]]


def suggest_footprints(proj, lib_id: str, limit: int = 30, query: str = "",
                       size: Optional[str] = None) -> Dict[str, Any]:
    """Footprints matching a symbol's filters, pin count and imperial package size."""
    if size is not None and not re.fullmatch(r"[0-9]{4}", size):
        raise ValueError("--size needs a four-digit imperial code, e.g. 0603 (1608 metric)")
    info = symbol_info(proj, lib_id)
    filters = info["fp_filters"].split() if info["fp_filters"] else []
    idx = build_index(proj, "fp")
    pin_count = info["pin_count"]
    res = []
    terms = search_terms(query)
    for e in idx:
        if filters:
            ok = any(fnmatch.fnmatchcase(e["name"], f) or fnmatch.fnmatchcase(e["fpid"], f) for f in filters)
            if not ok:
                continue
        elif terms == []:
            continue
        if pin_count and e["pad_count"] and e["pad_count"] != pin_count:
            continue
        if terms and _score(terms, {"name": e["name"], "tags": e["tags"], "lib": e["lib"],
                                    "description": e["description"]}) <= 0:
            continue
        hit = dict(e)
        code = re.search(r"_([0-9]{4})_([0-9]{4})Metric(?:_|$)", e["name"])
        hit.update(imperial_size=code[1] if code else None, metric_code=code[2] if code else None)
        if size and (not code or code[1] != size):
            continue
        if code and code[2] in terms and code[1] not in terms:
            hit["note"] = f"{code[2]} is the metric code; this is imperial {code[1]}. Use --size for imperial sizing."
        res.append(hit)
    res.sort(key=lambda e: (bool(e.get("note")), "handsolder" in e["name"].lower(), e["lib"], e["name"]))
    return {"symbol": lib_id, "fp_filters": filters, "pin_count": pin_count,
            "default_footprint": info["properties"].get("Footprint", ""),
            "count": len(res), "footprints": res[:limit]}


# ---------------------------------------------------------------------------
# Symbol loading / inspection
# ---------------------------------------------------------------------------

_LIB_CACHE: Dict[str, SList] = {}


def load_symbol_lib(path: str) -> SList:
    key = f"{path}:{os.path.getmtime(path)}"
    if key not in _LIB_CACHE:
        _LIB_CACHE[key] = sexpr.load(path)
    return _LIB_CACHE[key]


def find_symbol_in_lib(tree: SList, name: str) -> Optional[SList]:
    for s in tree.find_all("symbol"):
        if str(s[1]) == name:
            return s
    return None


def flatten_symbol(tree: SList, name: str) -> SList:
    """Return a self-contained copy of a library symbol (resolving ``extends``)."""
    sym = find_symbol_in_lib(tree, name)
    if sym is None:
        names = [str(s[1]) for s in tree.find_all("symbol")]
        close = [n for n in names if name.lower() in n.lower()][:10]
        raise KeyError(f"Symbol {name!r} not found in library. Similar: {close}")
    parent_name = sym.value("extends")
    if not parent_name:
        return copy.deepcopy(sym)
    parent = flatten_symbol(tree, parent_name)
    out = copy.deepcopy(parent)
    out[1] = Q(name)
    # child properties override parent's, keep parent's order
    child_props = {str(p[1]): p for p in sym.find_all("property")}
    new_items = []
    for c in out:
        if isinstance(c, SList) and c.tag == "property" and str(c[1]) in child_props:
            new_items.append(copy.deepcopy(child_props.pop(str(c[1]))))
        elif isinstance(c, SList) and c.tag == "symbol":
            sub = copy.deepcopy(c)
            sub[1] = Q(re.sub(r"^" + re.escape(parent_name) + r"(?=_\d+_\d+$)", name, str(sub[1])))
            new_items.append(sub)
        else:
            new_items.append(c)
    # remaining child-only properties go after the last property
    last_prop = max((i for i, c in enumerate(new_items) if isinstance(c, SList) and c.tag == "property"),
                    default=1)
    for p in child_props.values():
        last_prop += 1
        new_items.insert(last_prop, copy.deepcopy(p))
    for tag in ("pin_names", "pin_numbers", "exclude_from_sim", "in_bom", "on_board", "power"):
        cv = sym.find(tag)
        if cv is not None:
            for i, c in enumerate(new_items):
                if isinstance(c, SList) and c.tag == tag:
                    new_items[i] = copy.deepcopy(cv)
                    break
    out[:] = new_items
    return out


def resolve_symbol(proj, lib_id: str) -> Tuple[SList, str]:
    # A multi-symbol file uses FILE.kicad_sym:NAME; a one-symbol file needs no selector.
    if ".kicad_sym:" in lib_id.lower():
        path, name = lib_id.rsplit(":", 1)
        path = os.path.abspath(os.path.expanduser(path))
        return flatten_symbol(load_symbol_lib(path), name), path
    if lib_id.lower().endswith(".kicad_sym"):
        path = os.path.abspath(os.path.expanduser(lib_id))
        tree = load_symbol_lib(path)
        names = [str(s[1]) for s in tree.find_all("symbol")]
        if len(names) != 1:
            raise ValueError(f"{path} contains {len(names)} symbols; use FILE.kicad_sym:NAME. Available: {names[:30]}")
        return flatten_symbol(tree, names[0]), path
    lib, name = split_lib_id(lib_id)
    path = proj_mod.resolve_library(proj, "sym", lib)
    return flatten_symbol(load_symbol_lib(path), name), path


def pin_list(sym: SList) -> List[Dict[str, Any]]:
    pins = []
    for sub in sym.find_all("symbol"):
        m = re.search(r"_(\d+)_(\d+)$", str(sub[1]))
        unit, style = (int(m.group(1)), int(m.group(2))) if m else (0, 1)
        for p in sub.find_all("pin"):
            at = p.find("at")
            pins.append({
                "number": p.value("number"), "name": p.value("name"),
                "type": str(p[1]) if len(p) > 1 else "", "shape": str(p[2]) if len(p) > 2 else "",
                "unit": unit, "body_style": style,
                "x": float(at[1]), "y": float(at[2]), "orientation": float(at[3]) if len(at) > 3 else 0.0,
                "length": p.fvalue("length", 0.0),
                "hidden": (p.find("hide") is not None and p.value("hide") != "no") or "hide" in [str(a) for a in p.atoms()],
            })
    return pins


def symbol_info(proj, lib_id: str) -> Dict[str, Any]:
    sym, path = resolve_symbol(proj, lib_id)
    props = {str(p[1]): str(p[2]) for p in sym.find_all("property") if len(p) > 2}
    pins = pin_list(sym)
    units = sorted({p["unit"] for p in pins if p["unit"] > 0}) or [1]
    lib_tree = load_symbol_lib(path)
    raw = find_symbol_in_lib(lib_tree, str(sym[1]))
    return {
        "lib_id": lib_id, "library_path": path,
        "extends": raw.value("extends") if raw is not None else None,
        "properties": props, "description": props.get("Description", ""),
        "keywords": props.get("ki_keywords", ""), "fp_filters": props.get("ki_fp_filters", ""),
        "power": sym.find("power") is not None,
        "units": units, "unit_count": len(units),
        "pin_count": len({p["number"] for p in pins}),
        "pins": pins,
    }


def list_library(proj, kind: str, lib: str) -> List[Dict[str, Any]]:
    if kind == "sym":
        path = os.path.abspath(os.path.expanduser(lib)) if lib.lower().endswith(".kicad_sym") else proj_mod.resolve_library(proj, "sym", lib)
        return [{k: e[k] for k in ("name", "lib_id", "description", "pin_count", "units", "power",
                                   "footprint", "extends")} for e in parse_symbol_lib(path)]
    path = os.path.abspath(os.path.expanduser(lib)) if os.path.isdir(os.path.expanduser(lib)) else proj_mod.resolve_library(proj, "fp", lib)
    return [{k: e[k] for k in ("name", "fpid", "description", "pad_count", "attr")}
            for e in _parse_fp_for_index((lib, path))]


def list_libraries(proj, kind: str) -> List[Dict[str, Any]]:
    rows = proj_mod.lib_tables(proj)[kind]["libraries"]
    env = kb.kicad_env_vars(proj.dir if proj else None)
    out = []
    for r in rows:
        p = kb.expand_vars(r.get("uri", ""), env)
        out.append({"name": r.get("name"), "scope": r.get("scope"), "type": r.get("type"),
                    "uri": r.get("uri"), "path": p, "exists": os.path.exists(p), "descr": r.get("descr", "")})
    return out


# ---------------------------------------------------------------------------
# Footprints
# ---------------------------------------------------------------------------


def resolve_footprint(proj, fpid: str) -> Tuple[SList, str]:
    if fpid.lower().endswith(".kicad_mod"):
        path = os.path.abspath(os.path.expanduser(fpid))
        return sexpr.load(path), path
    lib, name = split_lib_id(fpid)
    pretty = proj_mod.resolve_library(proj, "fp", lib)
    path = os.path.join(pretty, name + ".kicad_mod")
    if not os.path.exists(path):
        cands = [os.path.splitext(os.path.basename(p))[0] for p in glob.glob(os.path.join(pretty, "*.kicad_mod"))]
        close = [c for c in cands if name.lower() in c.lower()][:10]
        raise KeyError(f"Footprint {name!r} not in {lib} ({pretty}). Similar: {close}")
    return sexpr.load(path), path


def _has_copper(pad: Dict[str, Any]) -> bool:
    return any(layer == "*.Cu" or layer.endswith(".Cu") for layer in pad["layers"])


def _pad_records(fp: SList) -> List[Dict[str, Any]]:
    """Pads with their tree node, so callers can rewrite the number in place."""
    out = []
    for p in fp.find_all("pad"):
        at = p.find("at")
        size = p.find("size")
        out.append({
            "node": p, "number": str(p[1]), "type": str(p[2]),
            "layers": [str(a) for a in (p.find("layers") or S("layers")).atoms()],
            "x": float(at[1]), "y": float(at[2]),
            "w": float(size[1]) if size is not None else 0.0,
            "h": float(size[2]) if size is not None else 0.0,
        })
    return out


def renumber_paste_only(fp: SList) -> List[Dict[str, str]]:
    """Give paste-only apertures the number of the copper pad that contains them (or "").

    KiCad's QFN wizard emits exposed-pad paste slivers numbered "1"; stock footprints leave
    paste apertures empty. Either is honest, "1" is not.
    """
    pads = _pad_records(fp)
    copper = [p for p in pads if p["type"] != "np_thru_hole" and _has_copper(p)]
    changes = []
    for p in pads:
        if p["type"] == "np_thru_hole" or _has_copper(p) or not p["number"]:
            continue
        covering = [c for c in copper
                    if abs(p["x"] - c["x"]) <= c["w"] / 2 + 1e-6
                    and abs(p["y"] - c["y"]) <= c["h"] / 2 + 1e-6]
        target = max(covering, key=lambda c: c["w"] * c["h"])["number"] if covering else ""
        if target != p["number"]:
            changes.append({"from": p["number"], "to": target})
            p["node"][1] = Q(target)
    return changes


def set_footprint_metadata(fp: SList, description: Optional[str] = None, tags: Optional[str] = None,
                           datasheet: Optional[str] = None) -> None:
    """Set ``descr``/``tags`` and the matching KiCad 10 properties (DRC compares them)."""
    if description is not None:
        fp.set("descr", Q(description), after="layer")
        prop = fp.property("Description")
        if prop is not None and len(prop) > 2:
            prop[2] = Q(description)
    if tags is not None:
        fp.set("tags", Q(tags), after="descr")
    if datasheet is not None:
        prop = fp.property("Datasheet")
        if prop is not None and len(prop) > 2:
            prop[2] = Q(datasheet)


def pad_roles(pads: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """Group pad numbers by role: signal, thermal (unnumbered copper), paste_only, npth."""
    roles: Dict[str, List[str]] = {"signal": [], "thermal": [], "paste_only": [], "npth": []}
    for pad in pads:
        number = pad["number"]
        if pad["type"] == "np_thru_hole":
            roles["npth"].append(number)
        elif not _has_copper(pad):
            roles["paste_only"].append(number)
        elif number:
            roles["signal"].append(number)
        else:
            roles["thermal"].append(number)
    return roles


def footprint_info(proj, fpid: str) -> Dict[str, Any]:
    fp, path = resolve_footprint(proj, fpid)
    pads = []
    for p in fp.find_all("pad"):
        at = p.find("at")
        size = p.find("size")
        drill = p.find("drill")
        pads.append({
            "number": str(p[1]), "type": str(p[2]), "shape": str(p[3]),
            "x": float(at[1]), "y": float(at[2]), "angle": float(at[3]) if len(at) > 3 else 0.0,
            "size": [float(size[1]), float(size[2])] if size is not None else None,
            "drill": [a for a in (drill.atoms() if drill is not None else [])],
            "layers": [str(a) for a in (p.find("layers") or S("layers")).atoms()],
        })
    xs, ys = [], []
    for tag in ("fp_line", "fp_rect", "fp_poly", "fp_circle"):
        for g in fp.find_all(tag):
            if g.value("layer") not in ("F.CrtYd", "B.CrtYd"):
                continue
            for k in ("start", "end", "center"):
                n = g.find(k)
                if n is not None:
                    xs.append(float(n[1]))
                    ys.append(float(n[2]))
            for xy in g.deep_find_all("xy"):
                xs.append(float(xy[1]))
                ys.append(float(xy[2]))
    props = {str(p[1]): str(p[2]) for p in fp.find_all("property") if len(p) > 2}
    return {
        "fpid": fpid, "path": path, "name": str(fp[1]),
        "description": fp.value("descr", ""), "tags": fp.value("tags", ""),
        "attr": fp.find("attr").atoms() if fp.find("attr") is not None else [],
        "layer": fp.value("layer"), "properties": props,
        "pad_count": len({p["number"] for p in pads if p["number"] and p["type"] != "np_thru_hole"}),
        "pad_object_count": len(pads),
        "pads_by_role": pad_roles(pads),
        "pads": pads,
        "models": [m.value() for m in fp.find_all("model")],
        "model_details": model_entries(proj, fp, os.path.dirname(path)),
        "courtyard": {"min": [min(xs), min(ys)], "max": [max(xs), max(ys)],
                      "size": [round(max(xs) - min(xs), 4), round(max(ys) - min(ys), 4)]} if xs else None,
    }


def copper_pad_numbers(pads: List[Dict[str, Any]]) -> List[str]:
    """Numbers of pads that carry copper (paste apertures and holes excluded)."""
    return sorted({p["number"] for p in pads
                   if p["number"] and p["type"] != "np_thru_hole" and _has_copper(p)}, key=natural_key)


def _pad_box(p: Dict[str, Any]) -> Tuple[float, float, float, float]:
    w, h = (p["size"] or [0.0, 0.0])[:2]
    radians = math.radians(p.get("angle") or 0)
    w, h = abs(w * math.cos(radians)) + abs(h * math.sin(radians)), abs(w * math.sin(radians)) + abs(h * math.cos(radians))
    return p["x"] - w / 2, p["y"] - h / 2, p["x"] + w / 2, p["y"] + h / 2


def _split_pad_numbers(pads: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """Signal numbers vs everything else; a lone pad much larger than its neighbours is the EP."""
    copper = [p for p in pads if p["number"] and p["type"] != "np_thru_hole" and _has_copper(p)]
    signal = [p["number"] for p in copper]
    other = sorted({p["number"] for p in pads
                    if p["number"] and (p["type"] == "np_thru_hole" or not _has_copper(p))}, key=natural_key)
    areas = sorted((p["size"] or [0.0, 0.0])[0] * (p["size"] or [0.0, 0.0])[1] for p in copper)
    if len(copper) >= 3:
        median = areas[len(areas) // 2]
        big = [p["number"] for p in copper
               if median > 0 and (p["size"] or [0.0, 0.0])[0] * (p["size"] or [0.0, 0.0])[1] > 2.5 * median]
        if len(big) == 1:
            signal = [n for n in signal if n != big[0]]
            other = sorted(set(other) | {big[0]}, key=natural_key)
    return {"signal": sorted(set(signal), key=natural_key), "other": other}


def footprint_check(sess, fpid: str, rules: str = "project", ipc_class: Optional[str] = None) -> Dict[str, Any]:
    """Check a library footprint without a board: pad numbering, courtyard, pad-to-pad clearance."""
    proj = sess.require_project()
    info = footprint_info(proj, fpid)
    pads = info["pads"]
    if rules == "project":
        classes = proj_mod.netclass_list(sess)["classes"]
        default = next((c for c in classes if c.get("name") == "Default"), None)
        required = float((default or proj_mod.DEFAULT_NETCLASS).get("clearance", 0.2))
    else:
        required = float(proj_mod.DEFAULT_NETCLASS["clearance"])

    copper = [p for p in pads if p["number"] and p["type"] != "np_thru_hole" and _has_copper(p)]
    numbers = [p["number"] for p in copper]
    duplicates = sorted({n for n in numbers if numbers.count(n) > 1}, key=natural_key)

    violations = []
    for i, a in enumerate(copper):
        for b in copper[i + 1:]:
            if a["number"] == b["number"]:
                continue  # jumpers and stacked pads share a number on purpose
            ax1, ay1, ax2, ay2 = _pad_box(a)
            bx1, by1, bx2, by2 = _pad_box(b)
            dx = max(ax1 - bx2, bx1 - ax2, 0.0)
            dy = max(ay1 - by2, by1 - ay2, 0.0)
            actual = math.hypot(dx, dy)
            if actual < required - 1e-9:
                violations.append({"a": a["number"], "b": b["number"],
                                   "actual": round(actual, 4), "required": round(required, 4)})
    violations.sort(key=lambda v: (natural_key(v["a"]), natural_key(v["b"])))

    courtyard = info["courtyard"]
    courtyard_out: Dict[str, Any] = {"present": courtyard is not None}
    if courtyard is not None:
        boxes = [_pad_box(p) for p in pads if p["type"] != "np_thru_hole"]
        margin = None
        if boxes:
            margin = round(min(min(b[0] for b in boxes) - courtyard["min"][0],
                               courtyard["max"][0] - max(b[2] for b in boxes),
                               min(b[1] for b in boxes) - courtyard["min"][1],
                               courtyard["max"][1] - max(b[3] for b in boxes)), 4)
        courtyard_out.update(size=courtyard["size"], margin_to_pads=margin)

    layers = set()
    fp, _ = resolve_footprint(proj, fpid)
    for tag in ("fp_line", "fp_rect", "fp_poly", "fp_circle", "fp_arc", "fp_text"):
        layers |= {str(g.value("layer")) for g in fp.find_all(tag)}
    missing_fab = [layer for layer in ("Fab", "CrtYd")
                   if not any(l.endswith("." + layer) for l in layers)]
    paste_numbered = sorted({p["number"] for p in pads if p["number"] and not _has_copper(p)},
                            key=natural_key)

    unsupported = [{"code": "unsupported_pad_geometry", "pad": p["number"], "shape": p.get("shape"),
                    "message": "This pad shape requires KiCad DRC; size-based bounding boxes cannot verify its primitives."}
                   for p in copper if p.get("shape") not in ("rect", "roundrect", "circle", "oval")]
    if ipc_class:
        unsupported.append({"code": "ipc_class_not_evaluated", "value": ipc_class,
                            "message": "IPC land-pattern compliance needs a package/datasheet model; this check does not evaluate it."})
    ok = not duplicates and not violations and courtyard is not None and not missing_fab and not unsupported
    return {
        "fpid": fpid, "ok": ok, "rules": rules, "ipc_class": ipc_class,
        "required_clearance": round(required, 4),
        "geometry_method": "conservative rotated pad bounding boxes",
        "coverage": {"clearance": "Default netclass only; no custom design-rule evaluation",
                     "models": "path existence only; STEP geometry is not verified",
                     "courtyard": "bounding box only", "source_equivalence": False},
        "unsupported": unsupported,
        "pad_numbers": _split_pad_numbers(pads),
        "duplicate_numbers": duplicates,
        "paste_only_pads_with_numbers": paste_numbered,
        "courtyard": courtyard_out,
        "clearance_violations": violations,
        "missing_fab_layers": missing_fab,
        "models": info["model_details"],
    }


def link_check(proj, lib_id: str, fpid: Optional[str] = None) -> Dict[str, Any]:
    """Compare a symbol's pin numbers with a footprint's pad numbers."""
    info = symbol_info(proj, lib_id)
    fpid = fpid or (info["properties"].get("Footprint") or "").strip()
    if not fpid:
        raise ValueError(f"{lib_id} has no Footprint property; pass the footprint id explicitly")
    fp_info = footprint_info(proj, fpid)
    pad_numbers = copper_pad_numbers(fp_info["pads"])
    pin_numbers = sorted({p["number"] for p in info["pins"]}, key=natural_key)
    missing = sorted(set(pin_numbers) - set(pad_numbers), key=natural_key)
    extra = sorted(set(pad_numbers) - set(pin_numbers), key=natural_key)
    mechanical = sorted({p["number"] for p in fp_info["pads"]
                         if not p["number"] or p["type"] == "np_thru_hole"}, key=natural_key)
    return {
        "lib_id": lib_id, "fpid": fpid, "ok": not missing and not extra,
        "sym_pins": pin_numbers, "sym_pin_count": len(pin_numbers),
        "fp_pads": pad_numbers, "fp_pad_count": len(pad_numbers),
        "pins_without_pad": missing, "pads_without_pin": extra,
        "mechanical_pads": mechanical,
    }


# ---------------------------------------------------------------------------
# 3D models of a library footprint
# ---------------------------------------------------------------------------

_DEFAULT_OFFSET = [0.0, 0.0, 0.0]
_DEFAULT_SCALE = [1.0, 1.0, 1.0]
_DEFAULT_ROTATION = [0.0, 0.0, 0.0]


def _xyz(node: Optional[SList], default: List[float]) -> List[float]:
    if node is None:
        return list(default)
    xyz = node.find("xyz")
    if xyz is None:
        return list(default)
    vals = [float(a) for a in xyz.atoms()]
    return (vals + list(default))[:3]


def model_entries(proj, fp: SList, base: Optional[str] = None) -> List[Dict[str, Any]]:
    """3D model references of a footprint, with the path resolved and checked."""
    models = fp.find_all("model")
    details = model_details(proj, [m.value() or "" for m in models], base or (proj.dir if proj else os.getcwd()))
    out = []
    for m, detail in zip(models, details):
        out.append({**detail,
                    "offset": _xyz(m.find("offset"), _DEFAULT_OFFSET),
                    "rotation": _xyz(m.find("rotate"), _DEFAULT_ROTATION),
                    "scale": _xyz(m.find("scale"), _DEFAULT_SCALE)})
    return out


def model_report(proj, fpid: str) -> Dict[str, Any]:
    fp, path = resolve_footprint(proj, fpid)
    return {"fpid": fpid, "models": model_entries(proj, fp, os.path.dirname(path))}


def _parse_xyz_option(text: Optional[str], name: str, default: List[float]) -> List[float]:
    if text is None:
        return default
    parts = [p.strip() for p in text.replace(" ", ",").split(",") if p.strip()]
    if len(parts) != 3:
        raise ValueError(f"--{name} needs three numbers x,y,z (got {text!r})")
    try:
        return [float(p) for p in parts]
    except ValueError:
        raise ValueError(f"--{name} needs three numbers x,y,z (got {text!r})")


def _model_node(file: str, offset: List[float], rotation: List[float], scale: List[float]) -> SList:
    return S("model", Q(file),
             S("offset", S("xyz", *offset)),
             S("scale", S("xyz", *scale)),
             S("rotate", S("xyz", *rotation)))


def set_footprint_model(fp: SList, file: str, *, offset: Optional[str] = None,
                        rotation: Optional[str] = None, scale: Optional[str] = None,
                        index: Optional[int] = None, add: bool = False) -> Dict[str, Any]:
    """Set or append a ``(model ...)`` node in a footprint tree; returns the index written.

    Without ``--add`` the first model is replaced (the common case: one body per footprint).
    Unspecified offset/rotation/scale keep the values of the model being replaced.
    """
    models = fp.find_all("model")
    if index is not None and add:
        raise ValueError("--index and --add are mutually exclusive")
    if not models and index not in (None, 0):
        raise ValueError(f"Model index {index} out of range; the footprint has 0 model(s)")
    if add or not models:
        idx = len(models)
        node = _model_node(file, _parse_xyz_option(offset, "offset", _DEFAULT_OFFSET),
                           _parse_xyz_option(rotation, "rotation", _DEFAULT_ROTATION),
                           _parse_xyz_option(scale, "scale", _DEFAULT_SCALE))
        at = next((i for i, c in enumerate(fp) if isinstance(c, SList) and c.tag in ("embedded_fonts", "group")),
                  len(fp))
        fp.insert(at, node)
        return {"index": idx, "created": True}
    idx = 0 if index is None else index
    if idx < 0 or idx >= len(models):
        raise ValueError(f"Model index {idx} out of range; the footprint has {len(models)} model(s)")
    old = models[idx]
    node = _model_node(file,
                       _parse_xyz_option(offset, "offset", _xyz(old.find("offset"), _DEFAULT_OFFSET)),
                       _parse_xyz_option(rotation, "rotation", _xyz(old.find("rotate"), _DEFAULT_ROTATION)),
                       _parse_xyz_option(scale, "scale", _xyz(old.find("scale"), _DEFAULT_SCALE)))
    for pos, child in enumerate(fp):
        if child is old:
            fp[pos] = node
            break
    return {"index": idx, "created": False}


def remove_footprint_model(fp: SList, index: int = 0) -> Dict[str, Any]:
    models = fp.find_all("model")
    if not models:
        raise KeyError("This footprint has no 3D model")
    if index < 0 or index >= len(models):
        raise ValueError(f"Model index {index} out of range; the footprint has {len(models)} model(s)")
    fp.remove_child(models[index])
    return {"index": index, "removed": models[index].value() or ""}


def model_file_ref(proj, source: str, copy_into: Optional[str] = None) -> Tuple[str, Optional[str]]:
    """Path to write into ``(model ...)`` and the copy destination (if any).

    A file inside the project becomes ``${KIPRJMOD}/...`` so the library travels with the
    project. This helper only plans a copy; the command stages its bytes in the session.
    """
    src = os.path.abspath(os.path.expanduser(source)) if not source.startswith("${") else source
    if copy_into:
        if source.startswith("${"):
            raise ValueError("--copy-into needs a real file, not a ${...} path")
        if not os.path.isfile(src):
            raise FileNotFoundError(f"3D model not found: {source}")
        dest_dir = copy_into if os.path.isabs(copy_into) else os.path.join(proj.dir, copy_into)
        dest_dir = os.path.abspath(dest_dir)
        dest = os.path.join(dest_dir, os.path.basename(src))
        return kicad_uri(dest, proj.dir), dest
    return kicad_uri(src, proj.dir), None


# ---------------------------------------------------------------------------
# Creating simple symbols
# ---------------------------------------------------------------------------

PIN_TYPES = ["input", "output", "bidirectional", "tri_state", "passive", "free", "unspecified",
             "power_in", "power_out", "open_collector", "open_emitter", "no_connect"]


def _effects(size=1.27, justify=None, hide=False):
    e = S("effects", S("font", S("size", size, size)))
    if justify:
        e.append(S("justify", *justify.split()))
    if hide:
        e.append(S("hide", "yes"))
    return e


def _prop(name, value, x, y, angle=0, hide=False):
    p = S("property", Q(name), Q(value), S("at", x, y, angle), S("show_name", "no"),
          S("do_not_autoplace", "no"))
    if hide:
        p.append(S("hide", "yes"))
    p.append(_effects())
    return p


def parse_pin_specs(specs: List[str]) -> List[Dict[str, Any]]:
    """Pin spec: ``number:name[:type[:side]]`` e.g. ``1:VCC:power_in:top``."""
    pins = []
    for s in specs:
        for item in s.split(","):
            item = item.strip()
            if not item:
                continue
            parts = item.split(":")
            num = parts[0]
            name = parts[1] if len(parts) > 1 and parts[1] else num
            ptype = parts[2] if len(parts) > 2 and parts[2] else "passive"
            side = parts[3] if len(parts) > 3 and parts[3] else None
            if ptype not in PIN_TYPES:
                raise ValueError(f"Pin type {ptype!r} invalid; choose from {PIN_TYPES}")
            if side and side not in ("left", "right", "top", "bottom"):
                raise ValueError("pin side must be left|right|top|bottom")
            pins.append({"number": num, "name": name, "type": ptype, "side": side})
    if not pins:
        raise ValueError("At least one pin is required")
    nums = [p["number"] for p in pins]
    if len(set(nums)) != len(nums):
        raise ValueError(f"Duplicate pin numbers: {sorted(n for n in nums if nums.count(n) > 1)}")
    return pins


def make_box_symbol(name: str, pins: List[Dict[str, Any]], reference: str = "U", value: Optional[str] = None,
                    footprint: str = "", datasheet: str = "", description: str = "",
                    keywords: str = "", fp_filters: str = "", pitch: float = 2.54,
                    pin_length: float = 2.54) -> SList:
    """A rectangular multi-pin symbol in KiCad 10 format (pins on a 2.54mm grid)."""
    sides = {"left": [], "right": [], "top": [], "bottom": []}
    auto = [p for p in pins if not p["side"]]
    for p in pins:
        if p["side"]:
            sides[p["side"]].append(p)
    half = math.ceil(len(auto) / 2)
    for i, p in enumerate(auto):
        (sides["left"] if i < half else sides["right"]).append(p)
    nv = max(len(sides["left"]), len(sides["right"]), 1)
    nh = max(len(sides["top"]), len(sides["bottom"]), 0)
    longest = max([len(p["name"]) for p in pins] + [1])
    width = max((nh + 1) * pitch, math.ceil((longest * 1.27 * 2 + 2.54) / pitch) * pitch, 4 * pitch)
    width = math.ceil(width / (2 * pitch)) * 2 * pitch
    height = (nv + 1) * pitch
    height = math.ceil(height / (2 * pitch)) * 2 * pitch
    hw, hh = width / 2, height / 2
    gfx = S("symbol", Q(f"{name}_0_1"),
            S("rectangle", S("start", -hw, hh), S("end", hw, -hh),
              S("stroke", S("width", 0.254), S("type", "default")), S("fill", S("type", "background"))))
    pin_nodes = S("symbol", Q(f"{name}_1_1"))

    def add(p, x, y, angle):
        pin_nodes.append(S("pin", p["type"], "line", S("at", x, y, angle), S("length", pin_length),
                           S("name", Q(p["name"]), _effects()), S("number", Q(p["number"]), _effects())))

    for i, p in enumerate(sides["left"]):
        add(p, -hw - pin_length, hh - pitch * (i + 1), 0)
    for i, p in enumerate(sides["right"]):
        add(p, hw + pin_length, hh - pitch * (i + 1), 180)
    for i, p in enumerate(sides["top"]):
        add(p, -hw + pitch * (i + 1), hh + pin_length, 270)
    for i, p in enumerate(sides["bottom"]):
        add(p, -hw + pitch * (i + 1), -hh - pin_length, 90)
    sym = S("symbol", Q(name), S("pin_names", S("offset", 1.016)), S("exclude_from_sim", "no"),
            S("in_bom", "yes"), S("on_board", "yes"), S("in_pos_files", "yes"),
            S("duplicate_pin_numbers_are_jumpers", "no"),
            _prop("Reference", reference, 0, hh + 1.27),
            _prop("Value", value or name, 0, -hh - 1.27),
            _prop("Footprint", footprint, 0, -hh - 3.81, hide=True),
            _prop("Datasheet", datasheet, 0, 0, hide=True),
            _prop("Description", description, 0, 0, hide=True))
    if keywords:
        sym.append(_prop("ki_keywords", keywords, 0, 0, hide=True))
    if fp_filters:
        sym.append(_prop("ki_fp_filters", fp_filters, 0, 0, hide=True))
    sym.append(gfx)
    sym.append(pin_nodes)
    sym.append(S("embedded_fonts", "no"))
    return sym


def new_symbol_library() -> SList:
    return S("kicad_symbol_lib", S("version", proj_mod.SYM_VERSION), S("generator", Q("kicad_symbol_editor")),
             S("generator_version", Q("10.0")))


def add_symbol_to_lib(lib_tree: SList, sym: SList, overwrite: bool = False) -> None:
    name = str(sym[1])
    for i, c in enumerate(lib_tree):
        if isinstance(c, SList) and c.tag == "symbol" and str(c[1]) == name:
            if not overwrite:
                raise FileExistsError(f"Symbol {name!r} already exists in library (use --overwrite)")
            lib_tree[i] = sym
            return
    lib_tree.append(sym)


def delete_symbol_from_lib(lib_tree: SList, name: str) -> None:
    for c in lib_tree.find_all("symbol"):
        if str(c[1]) == name:
            lib_tree.remove_child(c)
            return
    raise KeyError(f"Symbol {name!r} not in library")


# ---------------------------------------------------------------------------
# Creating simple footprints
# ---------------------------------------------------------------------------


def _fp_text(name, value, x, y, layer, hide=False, size=1.0, thickness=0.15):
    p = S("property", Q(name), Q(value), S("at", x, y, 0), S("layer", Q(layer)))
    if hide:
        p.append(S("hide", "yes"))
    p.append(S("uuid", Q(proj_mod.new_uuid())))
    p.append(S("effects", S("font", S("size", size, size), S("thickness", thickness))))
    return p


def _fp_rect(x1, y1, x2, y2, layer, width):
    return S("fp_rect", S("start", x1, y1), S("end", x2, y2), S("stroke", S("width", width), S("type", "solid")),
             S("fill", "no"), S("layer", Q(layer)), S("uuid", Q(proj_mod.new_uuid())))


def make_footprint(name: str, pads: List[Dict[str, Any]], description: str = "", tags: str = "",
                   smd: Optional[bool] = None, courtyard_margin: float = 0.25) -> SList:
    """Footprint from explicit pads: {number,x,y,w,h,shape,type,drill,layers,angle}."""
    if not pads:
        raise ValueError("A footprint needs at least one pad")
    if smd is None:
        smd = all(p.get("type", "smd") == "smd" for p in pads)
    xs1 = [p["x"] - p["w"] / 2 for p in pads]
    xs2 = [p["x"] + p["w"] / 2 for p in pads]
    ys1 = [p["y"] - p["h"] / 2 for p in pads]
    ys2 = [p["y"] + p["h"] / 2 for p in pads]
    x1, x2, y1, y2 = min(xs1), max(xs2), min(ys1), max(ys2)
    fp = S("footprint", Q(name), S("version", proj_mod.PCB_VERSION), S("generator", Q(APP_NAME)),
           S("generator_version", Q("10.0")), S("layer", Q("F.Cu")))
    if description:
        fp.append(S("descr", Q(description)))
    if tags:
        fp.append(S("tags", Q(tags)))
    fp.append(_fp_text("Reference", "REF**", 0, y1 - 1.2, "F.SilkS"))
    fp.append(_fp_text("Value", name, 0, y2 + 1.2, "F.Fab"))
    fp.append(_fp_text("Datasheet", "", 0, 0, "F.Fab", hide=True))
    fp.append(_fp_text("Description", description, 0, 0, "F.Fab", hide=True))
    fp.append(S("attr", "smd" if smd else "through_hole"))
    fp.append(S("duplicate_pad_numbers_are_jumpers", "no"))
    m = courtyard_margin
    fp.append(_fp_rect(round(x1 - m, 4), round(y1 - m, 4), round(x2 + m, 4), round(y2 + m, 4), "F.CrtYd", 0.05))
    fp.append(_fp_rect(round(x1, 4), round(y1, 4), round(x2, 4), round(y2, 4), "F.Fab", 0.1))
    s = 0.12
    fp.append(_fp_rect(round(x1 - m + s, 4), round(y1 - m + s, 4), round(x2 + m - s, 4), round(y2 + m - s, 4),
                       "F.SilkS", 0.12))
    fp.append(S("fp_text", "user", Q("${REFERENCE}"), S("at", 0, 0, 0), S("layer", Q("F.Fab")),
                S("uuid", Q(proj_mod.new_uuid())),
                S("effects", S("font", S("size", 0.5, 0.5), S("thickness", 0.08)))))
    for p in pads:
        ptype = p.get("type", "smd" if smd else "thru_hole")
        shape = p.get("shape", "roundrect" if ptype == "smd" else "circle")
        node = S("pad", Q(p["number"]), ptype, shape, S("at", p["x"], p["y"], p.get("angle", 0) or None),
                 S("size", p["w"], p["h"]))
        if ptype in ("thru_hole", "np_thru_hole"):
            node.append(S("drill", p.get("drill", 0.8)))
            layers = p.get("layers") or ["*.Cu", "*.Mask"]
        else:
            layers = p.get("layers") or ["F.Cu", "F.Mask", "F.Paste"]
        node.append(S("layers", *[Q(l) for l in layers]))
        if shape == "roundrect":
            node.append(S("roundrect_rratio", 0.25))
        if ptype == "thru_hole":
            node.append(S("remove_unused_layers", "no"))
        node.append(S("uuid", Q(proj_mod.new_uuid())))
        fp.append(node)
    fp.append(S("embedded_fonts", "no"))
    return fp


def generate_pads(kind: str, count: int, pitch: float, rows: int = 1, pad_w: float = 1.7, pad_h: float = 1.7,
                  drill: float = 1.0, row_spacing: Optional[float] = None) -> List[Dict[str, Any]]:
    """Pad layouts: ``header`` (THT grid, like PinHeader), ``dip`` (2-row THT, counter-clockwise),
    ``smd-row`` / ``soic`` (2-row SMD gull-wing, counter-clockwise), ``chip`` (2-pad SMD)."""
    pads: List[Dict[str, Any]] = []
    if kind == "chip":
        gap = row_spacing if row_spacing is not None else pitch
        for i, x in enumerate((-gap / 2, gap / 2)):
            pads.append({"number": str(i + 1), "x": x, "y": 0, "w": pad_w, "h": pad_h, "type": "smd",
                         "shape": "roundrect"})
        return pads
    if kind == "header":
        cols = math.ceil(count / rows)
        n = 1
        for c in range(cols):
            for r in range(rows):
                if n > count:
                    break
                pads.append({"number": str(n), "x": r * (row_spacing or pitch), "y": c * pitch,
                             "w": pad_w, "h": pad_h, "type": "thru_hole",
                             "shape": "rect" if n == 1 else "oval", "drill": drill})
                n += 1
        return pads
    if kind in ("dip", "soic", "smd-row"):
        if count % 2:
            raise ValueError(f"{kind} needs an even pad count")
        half = count // 2
        span = row_spacing if row_spacing is not None else (7.62 if kind == "dip" else 5.4)
        y0 = -(half - 1) * pitch / 2
        tht = kind == "dip"
        for i in range(half):
            pads.append({"number": str(i + 1), "x": -span / 2, "y": round(y0 + i * pitch, 4), "w": pad_w,
                         "h": pad_h, "type": "thru_hole" if tht else "smd",
                         "shape": ("rect" if i == 0 else "oval") if tht else "roundrect", "drill": drill})
        for i in range(half):
            pads.append({"number": str(half + i + 1), "x": span / 2, "y": round(y0 + (half - 1 - i) * pitch, 4),
                         "w": pad_w, "h": pad_h, "type": "thru_hole" if tht else "smd",
                         "shape": "oval" if tht else "roundrect", "drill": drill})
        return pads
    raise ValueError("kind must be header|dip|soic|smd-row|chip")


def model_details(proj, files, base):
    variables = kb.kicad_env_vars(proj.dir if proj else None)
    result = []
    for file in files:
        resolved = kb.expand_vars(file, variables)
        if not os.path.isabs(resolved):
            resolved = os.path.join(proj.dir if proj else base, resolved)
        result.append({'file': file, 'resolved_path': os.path.normpath(resolved), 'exists': os.path.isfile(resolved)})
    return result
