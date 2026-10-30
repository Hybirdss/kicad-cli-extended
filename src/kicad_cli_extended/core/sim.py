"""SPICE simulation: KiCad's SPICE netlist (kicad-cli) run through ngspice, results as JSON.

Engines, first hit wins:

1. an ``ngspice`` executable: ``$KCE_NGSPICE``, else ``ngspice`` / ``ngspice_con`` on PATH
   (``ngspice -b -r out.raw``);
2. the ngspice *shared library* KiCad's own simulator uses: ``$KCE_LIBNGSPICE``, else the one
   in the KiCad install (Windows ``bin/ngspice.dll``, macOS ``KiCad.app/Contents/PlugIns/sim/
   libngspice*.dylib``), else the system library (Linux ``libngspice.so.0``), driven by
   ``utils/ngspice_shared.py`` in a child process.

Simulation setup lives in the schematic, as in KiCad: analysis directives are text items that
start with ``.`` (``.tran 10u 5m``), source and model parameters are the ``Sim.*`` fields of a
symbol (``Sim.Device``, ``Sim.Type``, ``Sim.Params``, ``Sim.Pins``, ``Sim.Library``, ``Sim.Name``).
"""

from __future__ import annotations

import csv
import ctypes.util
import glob
import os
import re
import shutil
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

from . import schematic as sch_mod
from . import sexpr
from .workspace import materialize, remap
from .. import ENV_PREFIX, env as app_env
from . import runtime
from ..utils import kicad_backend as kb

RUNNER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "utils", "ngspice_shared.py")
ANALYSES = (".op", ".tran", ".ac", ".dc", ".noise", ".tf", ".disto", ".pz", ".sens", ".sp", ".fft")
SIM_FIELDS = ("Sim.Device", "Sim.Type", "Sim.Params", "Sim.Pins", "Sim.Library", "Sim.Name", "Sim.Enable")
INSTALL_HINT = ("No ngspice found. KiCad's installer ships the ngspice library (Windows bin\\ngspice.dll, macOS "
                "KiCad.app/Contents/PlugIns/sim); on Linux install the 'ngspice' package (libngspice.so). "
                f"Overrides: {ENV_PREFIX}NGSPICE=<ngspice executable>, "
                f"{ENV_PREFIX}LIBNGSPICE=<ngspice shared library>.")

# KiCad's names for the parameters of a V/I source waveform (make*ParamInfos in
# eeschema/sim/sim_model_source.cpp). KiCad's Sim.Params parser throws on the first name its model
# does not know (SIM_MODEL::SetParamValue), and the netlist exporter then falls back to the symbol's
# raw Value/params text: the element line becomes "V1 /IN GND y1=0 ... pw=1 per=2", which ngspice
# rejects as "circuit not parsed". Reject those names here instead. Some of KiCad's names differ
# from ngspice's spelling: KiCad "tw" (pulse width) is ngspice's "pw", KiCad "y1"/"y2" were
# "v1"/"v2". Every source also takes the AC parameters and, for voltages, the S-parameter port.
SOURCE_PARAMS = {
    "DC": ("dc",),
    "SIN": ("dc", "ampl", "f", "td", "theta", "phase"),
    "PULSE": ("y1", "y2", "td", "tr", "tf", "tw", "per", "np"),
    "EXP": ("y1", "y2", "td1", "tau1", "td2", "tau2"),
    "AM": ("vo", "vmo", "vma", "fm", "fc", "td", "phasem", "phasec"),
    "SFFM": ("vo", "va", "fm", "mdi", "fc", "phasem", "phasec"),
    "PWL": ("pwl",),
    "WHITENOISE": ("rms", "dt"),
    "PINKNOISE": ("rms", "slope", "dt"),
    "BURSTNOISE": ("ampl", "tcapt", "temit"),
    "RANDUNIFORM": ("ts", "td", "range", "offset"),
    "RANDGAUSSIAN": ("ts", "td", "stddev", "mean"),
    "RANDEXP": ("ts", "td", "mean", "offset"),
    "RANDPOISSON": ("ts", "td", "lambda", "offset"),
}
SOURCE_AC_PARAMS = ("ac", "ph")
SOURCE_SP_PARAMS = ("portnum", "z0")  # voltage sources only
_NGSPICE_NAMES = {"pw": "tw", "v1": "y1", "v2": "y2"}

_PARAM_NAME = re.compile(r"(?:^|\s)([A-Za-z_]\w*)\s*=")


# ---------------------------------------------------------------------------
# Engine discovery
# ---------------------------------------------------------------------------


def _kicad_bin_dir() -> Optional[str]:
    try:
        cmd = kb.kicad_cli_command()
    except kb.KicadError:
        return None
    if kb.is_flatpak():
        return None
    return os.path.dirname(os.path.realpath(cmd[0]))


def _shared_candidates() -> List[str]:
    cands: List[str] = []
    bindir = _kicad_bin_dir()
    if kb.IS_WINDOWS:
        if bindir:
            cands += [os.path.join(bindir, n) for n in ("ngspice.dll", "libngspice-0.dll")]
        for root in kb._windows_kicad_roots():
            cands.append(os.path.join(root, "bin", "ngspice.dll"))
    elif kb.IS_MAC:
        apps = kb._mac_app_dirs()
        if bindir and ".app" in bindir:
            apps.insert(0, bindir[: bindir.index(".app") + 4])
        for app in apps:
            for sub in ("PlugIns/sim", "PlugIns/sim/ngspice", "Frameworks"):
                cands += sorted(glob.glob(os.path.join(app, "Contents", *sub.split("/"), "libngspice*.dylib")))
        cands += ["/opt/homebrew/lib/libngspice.0.dylib", "/usr/local/lib/libngspice.0.dylib"]
    else:
        for d in ("/usr/lib", "/usr/lib64", "/usr/local/lib", "/usr/lib/x86_64-linux-gnu", "/usr/lib/aarch64-linux-gnu"):
            cands += [os.path.join(d, "libngspice.so.0"), os.path.join(d, "libngspice.so")]
        found = ctypes.util.find_library("ngspice")
        if found:
            cands.append(found)
    return cands


def find_ngspice() -> Optional[Dict[str, str]]:
    """``{"kind": "executable"|"shared", "path": ...}`` or None."""
    exe = app_env("NGSPICE")
    if exe:
        return {"kind": "executable", "path": exe} if os.path.isfile(exe) else None
    for name in ("ngspice_con", "ngspice") if kb.IS_WINDOWS else ("ngspice",):
        w = shutil.which(name)
        if w:
            return {"kind": "executable", "path": w}
    lib = app_env("LIBNGSPICE")
    if lib:
        return {"kind": "shared", "path": lib} if os.path.isfile(lib) else None
    for c in _shared_candidates():
        if os.path.isfile(c) or (not os.path.isabs(c) and c):
            return {"kind": "shared", "path": c}
    return None


# ---------------------------------------------------------------------------
# ASCII raw files
# ---------------------------------------------------------------------------


def _num(tok: str):
    if "," in tok:
        re_, im = tok.split(",", 1)
        return complex(float(re_), float(im))
    return float(tok)


def parse_raw(text: str) -> List[Dict[str, Any]]:
    """Parse an ngspice ASCII raw file (several plots) into ``[{name, title, vars, data}]``."""
    plots: List[Dict[str, Any]] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if not lines[i].startswith("Title:"):
            i += 1
            continue
        plot: Dict[str, Any] = {"title": lines[i].split(":", 1)[1].strip(), "vars": []}
        i += 1
        npts = nvars = 0
        while i < len(lines) and not lines[i].startswith("Values:"):
            key, _, val = lines[i].partition(":")
            if key == "Plotname":
                plot["name"] = val.strip()
            elif key == "No. Variables":
                nvars = int(val)
            elif key == "No. Points":
                npts = int(val)
            elif key == "Variables":
                for j in range(nvars):
                    parts = lines[i + 1 + j].split()
                    plot["vars"].append({"name": parts[1], "type": parts[2] if len(parts) > 2 else ""})
                i += nvars
            i += 1
        i += 1
        vals: List[Any] = []
        while i < len(lines) and len(vals) < npts * nvars and not lines[i].startswith("Title:"):
            toks = lines[i].split()
            if toks:
                vals.append(_num(toks[-1]))
            i += 1
        plot["data"] = [vals[k::nvars] for k in range(nvars)] if nvars else []
        plots.append(plot)
    return plots


def _summary(values: List[Any]) -> Dict[str, Any]:
    if values and isinstance(values[0], complex):
        mags = [abs(v) for v in values]
        return {"first": [values[0].real, values[0].imag], "last": [values[-1].real, values[-1].imag],
                "min_magnitude": min(mags), "max_magnitude": max(mags)}
    return {"first": values[0], "last": values[-1], "min": min(values), "max": max(values)} if values else {}


def _downsample(values: List[Any], n: int) -> List[Any]:
    if n <= 0 or len(values) <= n:
        idx = range(len(values))
    else:
        idx = sorted({round(k * (len(values) - 1) / (n - 1)) for k in range(n)})
    return [[values[k].real, values[k].imag] if isinstance(values[k], complex) else values[k] for k in idx]


# ---------------------------------------------------------------------------
# Netlist, directives, models
# ---------------------------------------------------------------------------


def export_spice(sess, out_path: str) -> str:
    proj = sess.require_project()
    with materialize(sess) as ws:
        kb.run_kicad_cli(["sch", "export", "netlist", "--format", "spice", "-o", out_path,
                          remap(proj.sch_path, proj.dir, ws)], cwd=ws)
    return out_path


def netlist_info(text: str) -> Dict[str, Any]:
    lines = [l.strip() for l in text.splitlines() if l.strip() and not l.lstrip().startswith("*")]
    directives = [l for l in lines if l.startswith(".") and l.split()[0].lower() not in (".title", ".end")]
    elements = [l for l in lines if not l.startswith(".") and not l.startswith("+")]
    return {"elements": len(elements), "directives": directives,
            "analyses": [d for d in directives if d.split()[0].lower() in ANALYSES]}


def unparsable_elements(text: str) -> List[str]:
    """Element lines ngspice cannot parse: KiCad exports a source's raw ``name=value`` params when
    its Sim.Params holds a name KiCad's model parser does not know, instead of a waveform call."""
    bad = []
    for line in text.splitlines():
        if not re.match(r"\s*[viVI]", line):
            continue
        if "=" in re.sub(r"\([^)]*\)|\"[^\"]*\"", "", line):  # ignore function args and strings
            bad.append(line.strip())
    return bad


def _apply_analyses(text: str, analyses: List[str]) -> str:
    """Replace the netlist's analysis directives with ``analyses`` (the schematic is untouched)."""
    out, inserted = [], False
    for line in text.splitlines():
        head = line.strip().split()[0].lower() if line.strip() else ""
        if head in ANALYSES:
            continue
        if head == ".end" and not inserted:
            out += analyses
            inserted = True
        out.append(line)
    if not inserted:
        out += analyses + [".end"]
    return "\n".join(out) + "\n"


def directives(tree) -> List[Dict[str, Any]]:
    out = []
    for t in tree.find_all("text"):
        txt = str(t[1]) if len(t) > 1 else ""
        if txt.lstrip().startswith("."):
            at = t.find("at")
            out.append({"text": txt, "uuid": t.value("uuid"), "at": [float(at[1]), float(at[2])] if at else None})
    return out


def add_directive(tree, text: str, at=None) -> Dict[str, Any]:
    if not text.strip().startswith("."):
        raise ValueError(f"A SPICE directive starts with '.', e.g. '.tran 10u 5m' (got {text!r})")
    if at is None:
        ys = [float(n[2]) for n in sexpr.walk(tree) if isinstance(n, sexpr.SList) and n.tag == "at" and len(n) > 2]
        at = (25.4, sch_mod.snap(max(ys or [25.4]) + 10.16))
    node = sch_mod.add_text(tree, text.strip(), at[0], at[1])
    return {"text": text.strip(), "uuid": node.value("uuid"), "at": list(at)}


def remove_directive(tree, key: str) -> int:
    n = 0
    for d in directives(tree):
        if key in (d["text"], d["uuid"]) or key.lower() == d["text"].split()[0].lower():  # '.tran' = every .tran
            sch_mod.delete_item(tree, d["uuid"])
            n += 1
    if not n:
        raise KeyError(f"No directive {key!r}. List them with `kce sim directives`.")
    return n


def check_source_params(device: Optional[str], type_: Optional[str], params: Optional[str]) -> None:
    """Reject Sim.Params names KiCad would silently drop (see ``SOURCE_PARAMS``)."""
    if not params or (device or "").upper() not in ("V", "I"):
        return
    allowed = SOURCE_PARAMS.get((type_ or "DC").upper())
    if allowed is None:
        return
    valid = allowed + SOURCE_AC_PARAMS + (SOURCE_SP_PARAMS if device.upper() == "V" else ())
    bad = [n for n in dict.fromkeys(_PARAM_NAME.findall(params)) if n not in valid]
    if bad:
        renamed = [f"{n} -> {_NGSPICE_NAMES[n]}" for n in bad if n in _NGSPICE_NAMES]
        raise ValueError(f"Sim.Params for a {device} {type_ or 'DC'} source has no parameter "
                         f"{', '.join(map(repr, bad))}"
                         + (f" (ngspice spelling: {', '.join(renamed)})" if renamed else "")
                         + f". Valid: {', '.join(valid)}. See `kce sim models`.")


def set_model(sym, device: Optional[str], type_: Optional[str], params: Optional[str], pins: Optional[str],
              library: Optional[str], name: Optional[str], clear: bool = False) -> Dict[str, Any]:
    """Set (or clear) the Sim.* fields KiCad's netlister reads for one symbol."""
    current = sim_fields(sym)
    if params is not None:  # only the params being written: a type change replaces them
        check_source_params(device or current.get("Sim.Device"),
                            type_ if type_ is not None else current.get("Sim.Type"), params)
    if clear:
        for f in SIM_FIELDS:
            if f in sim_fields(sym):
                sch_mod.remove_field(sym, f)
    for field, val in (("Sim.Device", device), ("Sim.Type", type_), ("Sim.Params", params), ("Sim.Pins", pins),
                       ("Sim.Library", library), ("Sim.Name", name)):
        if val is not None:
            sch_mod.set_field(sym, field, val, hide=True)
    return {"reference": sch_mod.sym_ref(sym), "sim_fields": sim_fields(sym)}


def sim_fields(sym) -> Dict[str, str]:
    return {str(p[1]): str(p[2]) for p in sym.find_all("property") if str(p[1]).startswith("Sim.")}


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


def netlist_warnings(text: str) -> List[Dict[str, Any]]:
    """Catch valid SPICE that produces misleading results before trusting its plots."""
    info = netlist_info(text)
    nodes, sources = [], []
    node_counts = {"r": 2, "c": 2, "l": 2, "v": 2, "i": 2, "d": 2,
                   "b": 2, "q": 3, "m": 4, "j": 3, "e": 4, "g": 4, "f": 2, "h": 2}
    for line in text.splitlines():
        words = line.lower().split()
        if not words or words[0][0] not in node_counts:
            continue
        nodes.extend(words[1:1 + node_counts[words[0][0]]])
        if words[0][0] in ("v", "i"):
            sources.append(line)
    warnings = []
    if nodes and "0" not in nodes:
        warnings.append({"code": "no_ground_node", "message": "SPICE needs node 0; a net named GND is not node 0.",
                         "fix": "kce sch label-pin REF:PIN 0 --kind global"})
    if any(a.lower().startswith(".ac ") for a in info["analyses"]):
        def has_ac(line):
            m = re.search(r"\bac(?:\s*=\s*|\s+)([^\s]+)", line, re.I)
            if not m:
                return False
            try:
                return float(m[1]) != 0
            except ValueError:
                return True  # ngspice evaluates expressions and scaled values
        if not any(has_ac(line) for line in sources):
            warnings.append({"code": "ac_source_missing", "message": "AC analysis has no nonzero AC source.",
                             "fix": 'kce sim model V1 --params "dc=0 ac=1"'})
    return warnings


def floating_pins(sess) -> List[str]:
    proj = sess.require_project()
    pins = []
    for sheet in sch_mod.hierarchy(sess.tree, proj.sch_path):
        tree = sess.tree(sheet["file"])
        excluded = {sch_mod.sym_ref(s) for s in sch_mod.placed_symbols(tree)
                    if s.value("exclude_from_sim") == "yes" or s.get_property("Sim.Enable") == "0"}
        pins.extend(f"{p['ref']}:{p['pin']}" for p in sch_mod.unconnected_pins(tree) if p["ref"] not in excluded)
    return sorted(set(pins))


def run(sess, analyses: Optional[List[str]] = None, probes: Optional[List[str]] = None, points: int = 50,
        out_dir: Optional[str] = None, timeout: int = 600) -> Dict[str, Any]:
    proj = sess.require_project()
    eng = find_ngspice()
    if eng is None:
        raise FileNotFoundError(INSTALL_HINT)
    out_dir = os.path.abspath(out_dir or os.path.join(proj.output_dir, "sim"))
    os.makedirs(out_dir, exist_ok=True)
    cir = os.path.join(out_dir, proj.name + ".cir")
    floating = floating_pins(sess)
    if floating:
        raise ValueError("floating_pin: " + ", ".join(floating) +
                         "; wire or label these pins before exporting SPICE (`kce sch label-pin REF:PIN NET`).")
    export_spice(sess, cir)
    with open(cir, encoding="utf-8", errors="replace") as f:
        text = f.read()
    if analyses:
        text = _apply_analyses(text, analyses)
        with open(cir, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    bad = unparsable_elements(text)
    if bad:
        raise ValueError(f"KiCad exported an element line ngspice cannot parse: {bad[0]}. The symbol's "
                         "Sim.Params holds a name KiCad's model parser does not know, so KiCad wrote the "
                         "raw parameters instead of a waveform; fix it with `kce sim model`. "
                         f"Netlist: {cir}")
    info = netlist_info(text)
    warnings = netlist_warnings(text)
    if not info["analyses"]:
        raise ValueError("The netlist has no analysis directive. Add one to the schematic with "
                         "`kce sim directive add \".tran 10u 5m\"` or pass --analysis \".op\".")
    raw, log = os.path.join(out_dir, proj.name + ".raw"), os.path.join(out_dir, proj.name + ".log")
    for p in (raw, log):
        if os.path.exists(p):
            os.unlink(p)
    started = time.time()
    if eng["kind"] == "executable":
        env = dict(os.environ, SPICE_ASCIIRAWFILE="1")
        r = runtime.execute([eng["path"], "-b", "-r", raw, cir], cwd=proj.dir, capture_output=True, timeout=timeout,
                           stdin=subprocess.DEVNULL, env=env, text=True, encoding="utf-8", errors="replace")
        with open(log, "w", encoding="utf-8", newline="\n") as f:
            f.write(r.stdout + r.stderr)
        rc = r.returncode
    else:
        r = runtime.execute([sys.executable, "-I", RUNNER, eng["path"], cir, raw, log], cwd=proj.dir,
                           capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL, text=True,
                           encoding="utf-8", errors="replace")
        rc = r.returncode
        if not os.path.exists(log):
            with open(log, "w", encoding="utf-8", newline="\n") as f:
                f.write(r.stdout + r.stderr)
    logtext = open(log, encoding="utf-8", errors="replace").read() if os.path.exists(log) else ""
    errors = [l.strip() for l in logtext.splitlines() if re.search(r"\berror\b", l, re.I)][:20]
    if not os.path.exists(raw) or os.path.getsize(raw) == 0:
        raise RuntimeError(f"ngspice produced no results (exit {rc}). " + (" | ".join(errors) or logtext[-1500:])
                           + f"\nNetlist: {cir}. Check the Sim.* fields (`kce sim models`) and directives.")
    plots = parse_raw(open(raw, encoding="utf-8", errors="replace").read())
    want = [p.lower() for p in probes or []]
    available = sorted({v["name"] for p in plots for v in p["vars"]})
    for probe in probes or []:
        if probe.lower() not in {name.lower() for name in available}:
            warnings.append({"code": "probe_not_found", "probe": probe, "available": available,
                             "fix": "Use an available vector exactly, e.g. --probe 'v(/out)'."})
    res_plots, outputs = [], [cir, raw, log]
    for k, p in enumerate(plots):
        names = [v["name"] for v in p["vars"]]
        csv_path = os.path.join(out_dir, f"{proj.name}-{k}-{re.sub(r'[^A-Za-z0-9]+', '_', p.get('name', 'plot')).strip('_').lower()}.csv")
        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(names)
            for row in zip(*p["data"]):
                w.writerow([f"{v.real}{v.imag:+}j" if isinstance(v, complex) else repr(v) for v in row])
        outputs.append(csv_path)
        npts = len(p["data"][0]) if p["data"] else 0
        vectors = []
        for v, values in zip(p["vars"], p["data"]):
            scale = v is p["vars"][0] and npts > 1  # time / frequency axis
            if want and v["name"].lower() not in want and not scale:
                continue
            e = {"name": v["name"], "type": v["type"]}
            if npts == 1:
                e["value"] = _downsample(values, 1)[0]
            else:
                e.update(_summary(values))
                if points:
                    e["values"] = _downsample(values, points)
            vectors.append(e)
        res_plots.append({"name": p.get("name"), "points": npts, "vectors": vectors, "csv": csv_path})
    return {"engine": eng, "analyses": info["analyses"], "seconds": round(time.time() - started, 2),
            "plots": res_plots, "errors": errors, "warnings": warnings, "outputs": outputs}
