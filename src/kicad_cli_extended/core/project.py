"""KiCad project model: .kicad_pro (JSON), files, templates, text variables,
net classes, board setup / design rules, library tables, archive."""

from __future__ import annotations

import copy
import fnmatch
import glob
import json
import os
import re
import shutil
import time
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional

from . import sexpr
from .sexpr import Q, S
from ..utils import kicad_backend as kb
from ..utils.paths import same_path

SCH_VERSION = "20260306"
PCB_VERSION = "20260206"
SYM_VERSION = "20251024"

KICAD_EXTS = (".kicad_pro", ".kicad_sch", ".kicad_pcb", ".kicad_sym", ".kicad_mod",
              ".kicad_dru", ".kicad_wks", ".kicad_jobset", ".kicad_prl")


class KicadProject:
    """Paths of a KiCad project. ``name`` is the .kicad_pro basename."""

    def __init__(self, pro_path: str):
        self.pro_path = os.path.abspath(pro_path)
        self.dir = os.path.dirname(self.pro_path)
        self.name = os.path.splitext(os.path.basename(self.pro_path))[0]

    @property
    def sch_path(self) -> str:
        return os.path.join(self.dir, self.name + ".kicad_sch")

    @property
    def pcb_path(self) -> str:
        return os.path.join(self.dir, self.name + ".kicad_pcb")

    @property
    def dru_path(self) -> str:
        return os.path.join(self.dir, self.name + ".kicad_dru")

    @property
    def sym_lib_table(self) -> str:
        return os.path.join(self.dir, "sym-lib-table")

    @property
    def fp_lib_table(self) -> str:
        return os.path.join(self.dir, "fp-lib-table")

    @property
    def output_dir(self) -> str:
        return os.path.join(self.dir, "outputs")

    def has_sch(self) -> bool:
        return os.path.exists(self.sch_path)

    def has_pcb(self) -> bool:
        return os.path.exists(self.pcb_path)

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "dir": self.dir, "pro": self.pro_path,
                "sch": self.sch_path if self.has_sch() else None,
                "pcb": self.pcb_path if self.has_pcb() else None}


def resolve_project(path: Optional[str]) -> Optional[KicadProject]:
    """Resolve --project argument (pro file, any project file, or directory)."""
    if path is None:
        cands = glob.glob(os.path.join(os.getcwd(), "*.kicad_pro"))
        return KicadProject(cands[0]) if len(cands) == 1 else None
    p = os.path.abspath(os.path.expanduser(path))
    if os.path.isdir(p):
        cands = sorted(glob.glob(os.path.join(p, "*.kicad_pro")))
        if not cands:
            raise FileNotFoundError(f"No .kicad_pro file in directory {p}")
        if len(cands) > 1:
            base = os.path.basename(p.rstrip("/"))
            for c in cands:
                if os.path.splitext(os.path.basename(c))[0] == base:
                    return KicadProject(c)
            raise ValueError(f"Several .kicad_pro files in {p}: {[os.path.basename(c) for c in cands]}; "
                             "pass one explicitly.")
        return KicadProject(cands[0])
    root, ext = os.path.splitext(p)
    ext = ext.lower()
    if ext == ".kicad_pro":
        if not os.path.exists(p):
            raise FileNotFoundError(f"Project file not found: {p}")
        return KicadProject(p)
    if ext in (".kicad_sch", ".kicad_pcb"):
        if not os.path.exists(p):
            raise FileNotFoundError(f"File not found: {p}")
        pro = root + ".kicad_pro"
        if os.path.exists(pro):
            return KicadProject(pro)
        cands = glob.glob(os.path.join(os.path.dirname(p), "*.kicad_pro"))
        if len(cands) == 1:
            return KicadProject(cands[0])
        # A lone board/schematic: KiCad tolerates a missing .kicad_pro
        return KicadProject(pro)
    raise ValueError(f"--project must be a .kicad_pro, .kicad_sch, .kicad_pcb or directory, got {path}")


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------


def new_uuid() -> str:
    return str(uuid.uuid4())


def empty_schematic_tree(paper: str = "A4", title: Optional[str] = None) -> sexpr.SList:
    root = S("kicad_sch", S("version", SCH_VERSION), S("generator", Q("eeschema")),
             S("generator_version", Q("10.0")), S("uuid", Q(new_uuid())), S("paper", Q(paper)))
    if title:
        root.append(S("title_block", S("title", Q(title))))
    root.append(S("lib_symbols"))
    root.append(S("sheet_instances", S("path", Q("/"), S("page", Q("1")))))
    root.append(S("embedded_fonts", "no"))
    return root


def list_templates() -> List[Dict[str, Any]]:
    tdir = kb.template_dir()
    res = [{"name": "default", "path": os.path.join(tdir, "kicad.kicad_pro"),
            "description": "KiCad default empty project"}]
    for d in sorted(os.listdir(tdir)):
        full = os.path.join(tdir, d)
        if not os.path.isdir(full):
            continue
        pros = glob.glob(os.path.join(full, "*.kicad_pro"))
        desc = ""
        info = os.path.join(full, "meta", "info.html")
        if os.path.exists(info):
            txt = open(info, encoding="utf-8", errors="replace").read()
            m = re.search(r"<title>(.*?)</title>", txt, re.S | re.I)
            desc = re.sub(r"\s+", " ", re.sub("<[^>]+>", " ", m.group(1) if m else txt)).strip()[:160]
        res.append({"name": d, "path": full, "has_project": bool(pros),
                    "files": sorted(os.listdir(full)), "description": desc})
    return res


def create_project(directory: str, name: Optional[str] = None, template: str = "default",
                   title: Optional[str] = None, overwrite: bool = False) -> Dict[str, Any]:
    """Create a project from a KiCad template (default or a folder of KiCad's template dir).

    The schematic and board are created then re-saved by the *real* KiCad
    (``kicad-cli sch upgrade --force`` and pcbnew) so they are canonical.
    """
    directory = os.path.abspath(os.path.expanduser(directory))
    name = name or os.path.basename(directory.rstrip("/"))
    reserved = re.fullmatch(r"CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³]", name.split(".", 1)[0], re.I)
    if (not re.fullmatch(r"[\w.\- ]+", name) or reserved
            or name in (".", "..") or name.endswith((".", " "))):
        raise ValueError(f"Invalid project name {name!r}")
    os.makedirs(directory, exist_ok=True)
    pro = os.path.join(directory, name + ".kicad_pro")
    if os.path.exists(pro) and not overwrite:
        raise FileExistsError(f"Project already exists: {pro} (use --overwrite)")
    tdir = kb.template_dir()
    created = []
    if template == "default":
        data = json.load(open(os.path.join(tdir, "kicad.kicad_pro"), encoding="utf-8"))
        src_dir = None
    else:
        src_dir = os.path.join(tdir, template) if not os.path.isdir(template) else template
        if not os.path.isdir(src_dir):
            raise FileNotFoundError(
                f"Template {template!r} not found. Available: "
                + ", ".join(t["name"] for t in list_templates()))
        pros = glob.glob(os.path.join(src_dir, "*.kicad_pro"))
        data = json.load(open(pros[0], encoding="utf-8")) if pros else json.load(open(os.path.join(tdir, "kicad.kicad_pro"), encoding="utf-8"))
        src_base = os.path.splitext(os.path.basename(pros[0]))[0] if pros else None
        for entry in os.listdir(src_dir):
            if entry == "meta":
                continue
            s = os.path.join(src_dir, entry)
            base, ext = os.path.splitext(entry)
            if src_base and base == src_base and ext in (".kicad_sch", ".kicad_pcb", ".kicad_pro", ".kicad_prl"):
                dst = os.path.join(directory, name + ext)
            else:
                dst = os.path.join(directory, entry)
            if ext == ".kicad_pro":
                continue
            if os.path.isdir(s):
                shutil.copytree(s, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(s, dst)
            created.append(dst)
    data.setdefault("meta", {})["filename"] = name + ".kicad_pro"
    with open(pro, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    created.append(pro)
    proj = KicadProject(pro)
    if not proj.has_sch():
        tree = empty_schematic_tree(title=title or name)
        sexpr.dump(tree, proj.sch_path)
        created.append(proj.sch_path)
    # Let KiCad itself normalise the schematic (also validates it loads).
    kb.run_kicad_cli(["sch", "upgrade", "--force", proj.sch_path])
    if not proj.has_pcb():
        kb.run_pcbnew("new_board", {"path": proj.pcb_path})
        created.append(proj.pcb_path)
    ignore = os.path.join(directory, ".gitignore")
    if not os.path.exists(ignore):
        with open(ignore, "w", encoding="utf-8", newline="\n") as f:
            f.write(GITIGNORE)
        created.append(ignore)
    return {"project": proj.to_dict(), "template": template, "created": sorted(set(created))}


# ---------------------------------------------------------------------------
# Inspection
# ---------------------------------------------------------------------------


def load_pro(proj: KicadProject, sess=None) -> Dict[str, Any]:
    if sess is not None:
        if not os.path.exists(proj.pro_path) and not sess.has_doc(proj.pro_path):
            return {}
        return sess.doc(proj.pro_path, "json").json()
    if not os.path.exists(proj.pro_path):
        return {}
    return json.load(open(proj.pro_path, encoding="utf-8"))


def list_files(proj: KicadProject) -> List[Dict[str, Any]]:
    out = []
    for root, dirs, files in os.walk(proj.dir):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("outputs", "__pycache__")
                   and not d.endswith("-backups")]
        for f in sorted(files):
            p = os.path.join(root, f)
            ext = os.path.splitext(f)[1]
            kind = {
                ".kicad_pro": "project", ".kicad_sch": "schematic", ".kicad_pcb": "board",
                ".kicad_sym": "symbol-library", ".kicad_mod": "footprint", ".kicad_dru": "design-rules",
                ".kicad_wks": "drawing-sheet", ".kicad_jobset": "jobset", ".kicad_prl": "local-settings",
            }.get(ext)
            if f in ("sym-lib-table", "fp-lib-table"):
                kind = "lib-table"
            if kind is None:
                continue
            out.append({"path": os.path.relpath(p, proj.dir), "kind": kind, "bytes": os.path.getsize(p)})
    return out


def project_info(proj: KicadProject, sess=None) -> Dict[str, Any]:
    pro = load_pro(proj, sess)
    info = proj.to_dict()
    info["exists"] = os.path.exists(proj.pro_path)
    info["files"] = list_files(proj)
    info["text_variables"] = pro.get("text_variables", {})
    info["net_classes"] = [c.get("name") for c in pro.get("net_settings", {}).get("classes", [])]
    info["kicad_version"] = kb.kicad_version()
    if proj.has_sch():
        t = sess.tree(proj.sch_path) if sess else sexpr.load(proj.sch_path)
        info["schematic"] = {
            "version": t.value("version"), "generator_version": t.value("generator_version"),
            "symbols": len(t.find_all("symbol")), "sheets": len(t.find_all("sheet")),
            "wires": len(t.find_all("wire")), "labels": len(t.find_all("label"))
            + len(t.find_all("global_label")) + len(t.find_all("hierarchical_label")),
            "title_block": {c.tag: c.value() for c in (t.find("title_block") or S("x")).children()},
        }
    if proj.has_pcb():
        t = sess.tree(proj.pcb_path) if sess else sexpr.load(proj.pcb_path)
        info["board"] = {
            "version": t.value("version"), "generator_version": t.value("generator_version"),
            "footprints": len(t.find_all("footprint")), "tracks": len(t.find_all("segment"))
            + len(t.find_all("arc")), "vias": len(t.find_all("via")), "zones": len(t.find_all("zone")),
            "nets": len(t.find_all("net")),
            "copper_layers": len([l for l in (t.find("layers") or S("x")).children()
                                  if len(l) > 1 and str(l[1]).endswith(".Cu")]),
        }
    return info


# ---------------------------------------------------------------------------
# .kicad_pro editing (JSON) helpers
# ---------------------------------------------------------------------------


def _pro_doc(sess):
    proj = sess.require_project()
    d = sess.doc(proj.pro_path, "json")
    if not os.path.exists(proj.pro_path) and d._json is None:
        d._json = json.load(open(os.path.join(kb.template_dir(), "kicad.kicad_pro"), encoding="utf-8"))
        d._json.setdefault("meta", {})["filename"] = proj.name + ".kicad_pro"
    return d


def get_path(data: Any, dotted: str) -> Any:
    cur = data
    for part in dotted.split(".") if dotted else []:
        if isinstance(cur, list):
            cur = cur[int(part)]
        elif isinstance(cur, dict):
            if part not in cur:
                raise KeyError(f"Key {dotted!r} not found (missing {part!r})")
            cur = cur[part]
        else:
            raise KeyError(f"Cannot descend into {type(cur).__name__} at {part!r}")
    return cur


def set_path(data: Any, dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    cur = data
    for part in parts[:-1]:
        if isinstance(cur, list):
            cur = cur[int(part)]
        else:
            cur = cur.setdefault(part, {})
    last = parts[-1]
    if isinstance(cur, list):
        cur[int(last)] = value
    else:
        cur[last] = value


def parse_value(v: str) -> Any:
    try:
        return json.loads(v)
    except (json.JSONDecodeError, TypeError):
        return v


def pro_get(sess, key: str = "") -> Any:
    return get_path(_pro_doc(sess).json(), key)


def pro_set(sess, key: str, value: Any) -> Dict[str, Any]:
    d = _pro_doc(sess)
    sess.snapshot(f"project set {key}", [d.path])
    data = d.json()
    old = None
    try:
        old = copy.deepcopy(get_path(data, key))
    except (KeyError, IndexError):
        pass
    set_path(data, key, value)
    d.mark_dirty()
    return {"key": key, "old": old, "new": value}


# text variables -------------------------------------------------------------

def textvar_list(sess) -> Dict[str, str]:
    return dict(_pro_doc(sess).json().get("text_variables", {}))


def textvar_set(sess, name: str, value: str) -> Dict[str, Any]:
    d = _pro_doc(sess)
    sess.snapshot(f"text variable {name}={value}", [d.path])
    tv = d.json().setdefault("text_variables", {})
    old = tv.get(name)
    tv[name] = value
    d.mark_dirty()
    return {"name": name, "old": old, "value": value}


def textvar_delete(sess, name: str) -> Dict[str, Any]:
    d = _pro_doc(sess)
    tv = d.json().get("text_variables", {})
    if name not in tv:
        raise KeyError(f"Text variable {name!r} not defined. Existing: {sorted(tv)}")
    sess.snapshot(f"delete text variable {name}", [d.path])
    old = tv.pop(name)
    d.mark_dirty()
    return {"name": name, "deleted": old}


# net classes ----------------------------------------------------------------

NETCLASS_KEYS = {
    "clearance": float, "track_width": float, "via_diameter": float, "via_drill": float,
    "microvia_diameter": float, "microvia_drill": float, "diff_pair_width": float,
    "diff_pair_gap": float, "diff_pair_via_gap": float, "wire_width": float, "bus_width": float,
    "line_style": int, "pcb_color": str, "schematic_color": str, "priority": int, "tuning_profile": str,
}

NET_SETTINGS_VERSION = 5  # KiCad 10.0 .kicad_pro net_settings schema

DEFAULT_NETCLASS = {
    "name": "Default", "bus_width": 12, "clearance": 0.2, "diff_pair_gap": 0.25,
    "diff_pair_via_gap": 0.25, "diff_pair_width": 0.2, "line_style": 0, "microvia_diameter": 0.3,
    "microvia_drill": 0.1, "pcb_color": "rgba(0, 0, 0, 0.000)", "priority": 2147483647,
    "schematic_color": "rgba(0, 0, 0, 0.000)", "track_width": 0.2, "via_diameter": 0.6,
    "via_drill": 0.3, "wire_width": 6, "tuning_profile": "",
}


def _net_settings(data: Dict) -> Dict:
    ns = data.setdefault("net_settings", {})
    ns.setdefault("classes", [])
    meta = ns.setdefault("meta", {"version": NET_SETTINGS_VERSION})
    if int(meta.get("version", 0)) < NET_SETTINGS_VERSION:
        # KiCad 10 treats version<5 as legacy and drops netclass_patterns on migration
        meta["version"] = NET_SETTINGS_VERSION
    if not any(c.get("name") == "Default" for c in ns["classes"]):
        ns["classes"].insert(0, dict(DEFAULT_NETCLASS))
    return ns


def netclass_list(sess) -> Dict[str, Any]:
    data = _pro_doc(sess).json()
    ns = data.get("net_settings", {})
    return {"classes": ns.get("classes", []),
            "assignments": ns.get("netclass_assignments") or {},
            "patterns": ns.get("netclass_patterns") or []}


def netclass_set(sess, name: str, values: Dict[str, Any]) -> Dict[str, Any]:
    d = _pro_doc(sess)
    for k in values:
        if k not in NETCLASS_KEYS:
            raise KeyError(f"Unknown net class field {k!r}. Valid: {sorted(NETCLASS_KEYS)}")
    sess.snapshot(f"net class {name}", [d.path])
    ns = _net_settings(d.json())
    cls = next((c for c in ns["classes"] if c.get("name") == name), None)
    created = cls is None
    if cls is None:
        cls = dict(DEFAULT_NETCLASS)
        cls["name"] = name
        cls["priority"] = max([c.get("priority", 0) for c in ns["classes"]
                               if c.get("name") != "Default"] + [-1]) + 1
        ns["classes"].append(cls)
    for k, v in values.items():
        cls[k] = NETCLASS_KEYS[k](v)
    d.mark_dirty()
    return {"name": name, "created": created, "class": cls}


def netclass_delete(sess, name: str) -> Dict[str, Any]:
    if name == "Default":
        raise ValueError("The Default net class cannot be deleted")
    d = _pro_doc(sess)
    ns = _net_settings(d.json())
    if not any(c.get("name") == name for c in ns["classes"]):
        raise KeyError(f"Net class {name!r} not found")
    sess.snapshot(f"delete net class {name}", [d.path])
    ns["classes"] = [c for c in ns["classes"] if c.get("name") != name]
    ns["netclass_patterns"] = [p for p in (ns.get("netclass_patterns") or []) if p.get("netclass") != name]
    d.mark_dirty()
    return {"deleted": name}


def netclass_assign(sess, pattern: str, netclass: str) -> Dict[str, Any]:
    d = _pro_doc(sess)
    ns = _net_settings(d.json())
    if not any(c.get("name") == netclass for c in ns["classes"]):
        raise KeyError(f"Net class {netclass!r} does not exist; create it with `kce project netclass set`")
    sess.snapshot(f"assign {pattern} -> {netclass}", [d.path])
    pats = ns.get("netclass_patterns") or []
    pats = [p for p in pats if p.get("pattern") != pattern]
    pats.append({"netclass": netclass, "pattern": pattern})
    ns["netclass_patterns"] = pats
    d.mark_dirty()
    return {"pattern": pattern, "netclass": netclass, "patterns": pats}


# board setup / design rules -------------------------------------------------

RULE_KEYS = [
    "min_clearance", "min_track_width", "min_via_diameter", "min_via_annular_width",
    "min_through_hole_diameter", "min_hole_clearance", "min_hole_to_hole", "min_copper_edge_clearance",
    "min_microvia_diameter", "min_microvia_drill", "min_silk_clearance", "min_text_height",
    "min_text_thickness", "solder_mask_to_copper_clearance", "min_resolved_spokes",
    "min_groove_width", "max_error", "solder_mask_min_width", "use_height_for_length_calcs",
    "allow_blind_buried_vias", "allow_microvias",
]


def rules_get(sess) -> Dict[str, Any]:
    data = _pro_doc(sess).json()
    ds = data.get("board", {}).get("design_settings", {})
    return {"rules": ds.get("rules", {}), "track_widths": ds.get("track_widths", []),
            "via_dimensions": ds.get("via_dimensions", []),
            "diff_pair_dimensions": ds.get("diff_pair_dimensions", []),
            "rule_severities": ds.get("rule_severities", {}),
            "custom_rules_file": sess.project.dru_path if os.path.exists(sess.project.dru_path) else None}


def rules_set(sess, values: Dict[str, Any]) -> Dict[str, Any]:
    d = _pro_doc(sess)
    sess.snapshot("design rules: " + ", ".join(f"{k}={v}" for k, v in values.items()), [d.path])
    ds = d.json().setdefault("board", {}).setdefault("design_settings", {})
    rules = ds.setdefault("rules", {})
    for k, v in values.items():
        if k not in RULE_KEYS:
            raise KeyError(f"Unknown rule {k!r}. Valid: {RULE_KEYS}")
        rules[k] = v
    d.mark_dirty()
    return {"rules": rules}


def severity_set(sess, check: str, severity: str, schematic: bool = False) -> Dict[str, Any]:
    if severity not in ("error", "warning", "ignore"):
        raise ValueError("severity must be error|warning|ignore")
    d = _pro_doc(sess)
    sess.snapshot(f"severity {check}={severity}", [d.path])
    data = d.json()
    if schematic:
        sev = data.setdefault("erc", {}).setdefault("rule_severities", {})
    else:
        sev = data.setdefault("board", {}).setdefault("design_settings", {}).setdefault("rule_severities", {})
    sev[check] = severity
    d.mark_dirty()
    return {"check": check, "severity": severity, "scope": "erc" if schematic else "drc"}


def presets_add(sess, kind: str, values: List[float]) -> Dict[str, Any]:
    """Add a track width / via / diff-pair preset to board setup."""
    d = _pro_doc(sess)
    sess.snapshot(f"add {kind} preset {values}", [d.path])
    ds = d.json().setdefault("board", {}).setdefault("design_settings", {})
    if kind == "track":
        lst = ds.setdefault("track_widths", [0.0])
        if values[0] not in lst:
            lst.append(values[0])
    elif kind == "via":
        lst = ds.setdefault("via_dimensions", [{"diameter": 0.0, "drill": 0.0}])
        lst.append({"diameter": values[0], "drill": values[1]})
    elif kind == "diff_pair":
        lst = ds.setdefault("diff_pair_dimensions", [{"gap": 0.0, "via_gap": 0.0, "width": 0.0}])
        lst.append({"width": values[0], "gap": values[1], "via_gap": values[2] if len(values) > 2 else values[1]})
    else:
        raise ValueError("kind must be track|via|diff_pair")
    d.mark_dirty()
    return {"kind": kind, "list": lst}


# custom rules (.kicad_dru) ---------------------------------------------------

def dru_read(sess) -> Dict[str, Any]:
    p = sess.project.dru_path
    if not os.path.exists(p) and not sess.has_doc(p):
        return {"path": p, "exists": False, "rules": []}
    txt = sess.doc(p, "text").text()
    nodes = sexpr.loads_many(txt) if txt.strip() else []
    rules = []
    for n in nodes:
        if n.tag == "rule":
            rules.append({"name": str(n[1]) if len(n) > 1 else "",
                          "condition": n.value("condition"),
                          "layer": n.value("layer"),
                          "constraints": [sexpr.compact(c) for c in n.find_all("constraint")],
                          "text": sexpr.compact(n)})
    return {"path": p, "exists": True, "version": next((n.value() for n in nodes if n.tag == "version"), None),
            "rules": rules, "text": txt}


def dru_add_rule(sess, name: str, constraints: List[str], condition: Optional[str] = None,
                 layer: Optional[str] = None) -> Dict[str, Any]:
    """Append a custom rule. ``constraints`` are S-expr snippets like
    ``"(constraint clearance (min 0.3mm))"`` or shorthand ``"clearance min 0.3mm"``."""
    p = sess.project.dru_path
    d = sess.doc(p, "text")
    txt = d.text() if os.path.exists(p) or d._text is not None else "(version 1)\n"
    sess.snapshot(f"add custom rule {name}", [p])
    parts = [f"(rule {sexpr.quote(name)}"]
    if layer:
        parts.append(f"\t(layer {sexpr.quote(layer) if not re.fullmatch(r'[A-Za-z.]+', layer) else layer})")
    for c in constraints:
        c = c.strip()
        if not c.startswith("("):
            toks = c.split()
            ctype, rest = toks[0], toks[1:]
            inner = " ".join(f"({rest[i]} {rest[i + 1]})" for i in range(0, len(rest) - 1, 2))
            c = f"(constraint {ctype} {inner})".replace(" )", ")")
        sexpr.loads(c)  # validate syntax
        parts.append("\t" + c)
    if condition:
        parts.append(f"\t(condition {sexpr.quote(condition)})")
    rule = "\n".join(parts) + ")\n"
    new = txt.rstrip("\n") + "\n\n" + rule
    sexpr.loads_many(new)
    d.set_text(new)
    return {"path": p, "rule": rule}


def dru_remove_rule(sess, name: str) -> Dict[str, Any]:
    info = dru_read(sess)
    if not any(r["name"] == name for r in info["rules"]):
        raise KeyError(f"Custom rule {name!r} not found in {info['path']}")
    sess.snapshot(f"remove custom rule {name}", [info["path"]])
    nodes = sexpr.loads_many(info["text"])
    keep = [n for n in nodes if not (n.tag == "rule" and len(n) > 1 and str(n[1]) == name)]
    text = "\n".join(sexpr.dumps(n).rstrip("\n") for n in keep) + "\n"
    sess.doc(info["path"], "text").set_text(text)
    return {"removed": name}


# ---------------------------------------------------------------------------
# Library tables
# ---------------------------------------------------------------------------


def _table_path(proj: Optional[KicadProject], kind: str, scope: str) -> str:
    fname = "sym-lib-table" if kind == "sym" else "fp-lib-table"
    if scope == "global":
        return os.path.join(kb.kicad_config_dir(), fname)
    if proj is None:
        raise RuntimeError("Project scope requires a project (--project)")
    return os.path.join(proj.dir, fname)


def read_lib_table(path: str) -> List[Dict[str, str]]:
    if not os.path.exists(path):
        return []
    t = sexpr.load(path)
    rows = []
    for lib in t.find_all("lib"):
        row = {c.tag: c.value() for c in lib.children()}
        rows.append(row)
    return rows


def default_global_table(kind: str) -> str:
    return os.path.join(kb.template_dir(), "sym-lib-table" if kind == "sym" else "fp-lib-table")


def lib_tables(proj: Optional[KicadProject]) -> Dict[str, Any]:
    """Effective library tables (project rows override global rows)."""
    out = {}
    for kind in ("sym", "fp"):
        gpath = _table_path(None, kind, "global")
        gsrc = gpath if os.path.exists(gpath) else default_global_table(kind)
        rows = []
        for r in read_lib_table(gsrc):
            r = dict(r, scope="global")
            rows.append(r)
        ppath = None
        if proj is not None:
            ppath = _table_path(proj, kind, "project")
            prow = read_lib_table(ppath)
            names = {r["name"] for r in prow}
            rows = [r for r in rows if r["name"] not in names] + [dict(r, scope="project") for r in prow]
        out[kind] = {"global_table": gpath, "global_table_exists": os.path.exists(gpath),
                     "global_source": gsrc, "project_table": ppath, "libraries": rows}
    return out


def resolve_library(proj: Optional[KicadProject], kind: str, nickname: str) -> str:
    """Return the filesystem path of a symbol/footprint library nickname."""
    tables = lib_tables(proj)[kind]["libraries"]
    env = kb.kicad_env_vars(proj.dir if proj else None)
    for r in tables:
        if r.get("name") == nickname:
            path = kb.expand_vars(r.get("uri", ""), env)
            if not os.path.exists(path):
                raise FileNotFoundError(f"Library {nickname!r} resolves to missing path {path}")
            return path
    try:
        fallback = os.path.join(kb.symbol_dir(), nickname + ".kicad_sym") if kind == "sym" else \
            os.path.join(kb.footprint_dir(), nickname + ".pretty")
    except kb.KicadError:
        fallback = ""
    if fallback and os.path.exists(fallback):
        return fallback
    raise KeyError(f"{'Symbol' if kind == 'sym' else 'Footprint'} library {nickname!r} is not in any "
                   f"lib table. Add it with `kce project libtable add {kind} {nickname} <path>`.")


def libtable_add(sess, kind: str, name: str, uri: str, scope: str = "project",
                 descr: str = "", options: str = "", lib_type: str = "KiCad") -> Dict[str, Any]:
    proj = sess.project
    path = _table_path(proj, kind, scope)
    root_tag = "sym_lib_table" if kind == "sym" else "fp_lib_table"
    if scope == "project":
        sess.snapshot(f"libtable add {kind} {name}", [path])
        d = sess.doc(path)
        if os.path.exists(path) or d._tree is not None:
            tree = d.tree()
        else:
            tree = S(root_tag, S("version", 7))
    else:
        tree = sexpr.load(path) if os.path.exists(path) else sexpr.load(default_global_table(kind))
    tree[:] = [c for c in tree if not (isinstance(c, sexpr.SList) and c.tag == "lib" and c.value("name") == name)]
    tree.append(S("lib", S("name", Q(name)), S("type", Q(lib_type)), S("uri", Q(uri)),
                  S("options", Q(options)), S("descr", Q(descr))))
    if scope == "project":
        sess.doc(path).set_tree(tree)
    else:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        sexpr.dump(tree, path, "library_table")
    return {"table": path, "scope": scope, "added": {"name": name, "uri": uri, "type": lib_type}}


def libtable_remove(sess, kind: str, name: str, scope: str = "project") -> Dict[str, Any]:
    path = _table_path(sess.project, kind, scope)
    if not os.path.exists(path):
        raise FileNotFoundError(f"No {scope} {kind}-lib-table at {path}")
    if scope == "project":
        sess.snapshot(f"libtable remove {kind} {name}", [path])
        tree = sess.doc(path).tree()
    else:
        tree = sexpr.load(path)
    before = len(tree)
    tree[:] = [c for c in tree if not (isinstance(c, sexpr.SList) and c.tag == "lib" and c.value("name") == name)]
    if len(tree) == before:
        raise KeyError(f"Library {name!r} not in {path}")
    if scope == "project":
        sess.doc(path).mark_dirty()
    else:
        sexpr.dump(tree, path, "library_table")
    return {"table": path, "removed": name}


def init_global_tables(force: bool = False) -> Dict[str, Any]:
    """Install KiCad's default global library tables (what the GUI's first-run wizard does).

    KiCad 10 has three: symbols, footprints and design blocks; its setup wizard runs until all
    three exist (LIBRARY_MANAGER::GlobalTablesValid).  The design-block table chains KiCad's stock
    table when the install ships one, otherwise it is created empty, like the wizard does.
    """
    res = {}
    for kind in ("sym", "fp"):
        dst = _table_path(None, kind, "global")
        if os.path.exists(dst) and not force:
            res[kind] = {"path": dst, "action": "kept"}
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(default_global_table(kind), dst)
        res[kind] = {"path": dst, "action": "installed", "libraries": len(read_lib_table(dst))}
    dst = os.path.join(kb.kicad_config_dir(), "design-block-lib-table")
    if os.path.exists(dst) and not force:
        res["design_block"] = {"path": dst, "action": "kept"}
    else:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        stock = os.path.join(kb.template_dir(), "design-block-lib-table")
        rows = ""
        if os.path.isfile(stock):
            rows = (f'\t(lib\n\t\t(name "KiCad")\n\t\t(type "Table")\n\t\t(uri {sexpr.quote(Path(stock).as_posix())})\n'
                    '\t\t(options "")\n\t\t(descr "KiCad Default Libraries")\n\t)\n')
        with open(dst, "w", encoding="utf-8", newline="\n") as f:
            f.write("(design_block_lib_table\n\t(version 7)\n" + rows + ")\n")
        res["design_block"] = {"path": dst, "action": "installed", "chained_stock_table": bool(rows)}
    return res


# ---------------------------------------------------------------------------
# Archive
# ---------------------------------------------------------------------------


def archive(proj: KicadProject, output: Optional[str] = None, include_outputs: bool = False) -> Dict[str, Any]:
    output = output or os.path.join(proj.dir, f"{proj.name}-{time.strftime('%Y%m%d-%H%M%S')}.zip")
    output = os.path.abspath(output)
    n = 0
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(proj.dir):
            dirs[:] = [d for d in dirs if not d.startswith(".") and not d.endswith("-backups")
                       and (include_outputs or d != "outputs")]
            for f in files:
                p = os.path.join(root, f)
                if same_path(p, output) or f.endswith((".zip", ".lck")) or f.startswith("~"):
                    continue
                z.write(p, os.path.relpath(p, proj.dir))
                n += 1
    return {"archive": output, "files": n, "bytes": os.path.getsize(output)}


def unarchive(archive_path: str, directory: str) -> Dict[str, Any]:
    with zipfile.ZipFile(archive_path) as z:
        seen = set()
        for m in z.namelist():
            parts = PurePosixPath(m.replace("\\", "/")).parts
            if m.startswith(("/", "\\")) or ".." in parts or (parts and ":" in parts[0]):
                raise ValueError(f"Unsafe path in archive: {m}")
            if os.name == "nt":
                for component in parts:
                    stem = component.split(".", 1)[0].rstrip(" ")
                    if (re.search(r'[<>:"|?*\x00-\x1f]', component)
                            or component.endswith((".", " "))
                            or re.fullmatch(r"CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³]", stem, re.I)):
                        raise ValueError(f"Non-portable archive path: {m}")
                key = "/".join(parts).casefold()
                if key in seen:
                    raise ValueError(f"Colliding archive path: {m}")
                seen.add(key)
        z.extractall(directory)
        names = z.namelist()
    pros = [n for n in names if n.endswith(".kicad_pro")]
    return {"directory": os.path.abspath(directory), "files": len(names), "projects": pros}


def match_any(name: str, patterns: List[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, p) for p in patterns)


GITIGNORE = "outputs/\n*.kicad_prl\n~*.lck\n*-backups/\nfp-info-cache\n.kicad-cli-extended/\n"
