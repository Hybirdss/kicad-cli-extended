"""Expose *every* kicad-cli subcommand and option as ``kce`` commands.

The command surface is generated from ``utils/kicad_cli_spec.json`` (produced
by walking ``kicad-cli --help`` for KiCad 10.0.7, see scripts/gen_kicad_cli_spec.py).
The coverage test re-walks the live ``kicad-cli`` at test time and fails if any
subcommand/option is not reachable here, so KiCad updates cannot slip by.

Additions on top of the raw passthrough:
* the input file defaults to the current project's schematic/board/project,
* ``--output`` defaults to ``<project>/outputs/<command>/...``,
* the session's unsaved edits are materialised before KiCad runs,
* produced files are listed and format-checked (magic bytes),
* ``--show-command`` prints the exact kicad-cli argv without running it,
* commands that rewrite their input (upgrade, drc --save-board) are recorded
  in undo history.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import click

from . import importer
from . import checks as checks_mod
from .workspace import materialize, remap
from ..utils import kicad_backend as kb
from ..utils.paths import is_dir_hint, rel_inside
from ..utils.verify import describe_outputs, notable_log, output_base

SPEC_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "utils", "kicad_cli_spec.json")

# kicad-cli path -> kce command path
PATH_MAP: Dict[str, List[str]] = {
    "fp export svg": ["lib", "fp", "export", "svg"],
    "fp upgrade": ["lib", "fp", "upgrade"],
    "sym export svg": ["lib", "sym", "export", "svg"],
    "sym upgrade": ["lib", "sym", "upgrade"],
    "version": ["info", "kicad-version"],
}

# Default output file extensions for commands whose --output is a file.
OUT_EXT = {
    "pcb drc": ".json", "sch erc": ".json", "pcb export 3dpdf": ".pdf", "pcb export brep": ".brep",
    "pcb export glb": ".glb", "pcb export ply": ".ply", "pcb export step": ".step", "pcb export stl": ".stl",
    "pcb export stpz": ".stpz", "pcb export u3d": ".u3d", "pcb export xao": ".xao", "pcb export vrml": ".wrl",
    "pcb export ipc2581": ".xml", "pcb export ipcd356": ".d356", "pcb export odb": ".zip",
    "pcb export pos": ".pos", "pcb export stats": ".txt", "pcb render": ".png",
    "sch export netlist": ".net", "sch export bom": ".csv", "sch export python-bom": ".xml",
    "sch export pdf": ".pdf",
    "pcb import": ".kicad_pcb",
}


def load_spec() -> Dict[str, Any]:
    with open(SPEC_PATH, encoding="utf-8") as f:
        return json.load(f)


def command_path(kpath: str) -> List[str]:
    return PATH_MAP.get(kpath, kpath.split())


def input_kind(kpath: str) -> str:
    first = kpath.split()[0]
    if kpath == "jobset run":
        return "project"
    if kpath == "pcb import":
        return "foreign"
    return {"sch": "sch", "pcb": "pcb", "sym": "symlib", "fp": "fplib", "version": "none"}[first]


# kicad-cli help text says "Output directory" but the command actually wants a file path (10.0.7)
OUTPUT_IS_FILE = {"pcb export gencad"}
OUT_EXT_EXTRA = {"pcb export gencad": ".cad"}
# Plot commands whose legacy default (single-file) mode fails with a directory output
DIR_MODE_FLAG = {"pcb export pdf": "--mode-separate", "pcb export svg": "--mode-multi",
                 "pcb export ps": "--mode-multi", "pcb export dxf": "--mode-multi"}


def output_is_dir(spec: Dict[str, Any], kpath: Optional[str] = None) -> Optional[bool]:
    if kpath in OUTPUT_IS_FILE:
        return False
    for o in spec["options"]:
        if o["long"] == "--output":
            h = o["help"].lower()
            return "directory" in h or "dir" == h.split()[-1]
    return None


def _param_name(i: int) -> str:
    return f"kc_{i}"


# 3D exports exit 2 when models are missing or incompatible, although the file is written (10.0.7:
# EXPORTER_STEP::Export returns false on any error-severity report, see exporter_step.cpp)
EXPORTS_3D = {"pcb export step", "pcb export stpz", "pcb export brep", "pcb export xao", "pcb export glb",
              "pcb export stl", "pcb export ply", "pcb export u3d", "pcb export 3dpdf", "pcb export vrml"}
_MISSING_MODEL = re.compile(r"Could not add 3D model for (\S+?)\.?\s*\n\s*File not found: (.+)")
_FATAL_3D = ("Export aborted", "Unexpected error", "OpenCASCADE error", "Error exporting")


def missing_3d_models(text: str) -> List[Dict[str, str]]:
    """``[{reference, file}]`` from kicad-cli's "Could not add 3D model" messages."""
    seen, out = set(), []
    for ref, path in _MISSING_MODEL.findall(text.replace("\\n", "\n")):
        key = (ref, path.strip())
        if key not in seen:
            seen.add(key)
            out.append({"reference": ref, "file": path.strip()})
    return out


NO_DEFAULT_OUTPUT = {"jobset run", "sym upgrade", "fp upgrade", "pcb import"}
NETLIST_EXT = {"kicadxml": ".xml", "cadstar": ".frp", "orcadpcb2": ".net", "spice": ".cir", "spicemodel": ".cir",
               "pads": ".asc", "allegro": ".txt"}


def default_output(proj, kpath: str, spec: Dict[str, Any], opts: Dict[str, Any]) -> Optional[str]:
    if kpath == "pcb import":
        return proj.pcb_path if proj else None
    if kpath in NO_DEFAULT_OUTPUT:
        return None
    is_dir = output_is_dir(spec, kpath)
    if is_dir is None or proj is None:
        return None
    slug = kpath.replace(" export ", "-").replace(" ", "-")
    base = os.path.join(proj.output_dir, slug)
    if is_dir:
        return base + os.sep
    ext = OUT_EXT.get(kpath) or OUT_EXT_EXTRA.get(kpath, ".out")
    fmt = (opts.get("--format") or "").lower()
    if kpath in ("pcb drc", "sch erc") and fmt and fmt != "json":
        ext = ".rpt"
    if kpath == "pcb export pos" and fmt == "csv":
        ext = ".csv"
    if kpath == "pcb export pos" and fmt == "gerber":
        ext = ".gbr"
    if kpath == "sch export netlist" and fmt in NETLIST_EXT:
        ext = NETLIST_EXT[fmt]
    if kpath == "pcb export stats" and fmt == "json":
        ext = ".json"
    if kpath == "pcb render" and (opts.get("--output") or "").lower().endswith((".jpg", ".jpeg")):
        ext = ".jpg"
    if kpath == "pcb import":
        return None
    return os.path.join(proj.output_dir, f"{proj.name}-{slug}{ext}")


def build_argv(kpath: str, spec: Dict[str, Any], values: Dict[str, Any], input_file: Optional[str]) -> List[str]:
    argv = kpath.split()
    for o in spec["options"]:
        v = values.get(o["long"])
        if o["takes_value"]:
            if o["repeatable"]:
                for item in v or ():
                    argv += [o["long"], str(item)]
            elif v not in (None, ""):
                argv += [o["long"], str(v)]
        elif v:
            argv.append(o["long"])
    if spec["positionals"] and input_file:
        argv.append(input_file)
    return argv


def make_command(kpath: str, spec: Dict[str, Any], ctx_api: Dict[str, Callable]) -> click.Command:
    """Create the Click command mirroring one kicad-cli leaf command."""
    params: List[click.Parameter] = []
    opt_names: List[Tuple[str, str]] = []
    kind = input_kind(kpath)
    if spec["positionals"]:
        params.append(click.Argument(["input_file"], required=kind in ("symlib", "fplib", "foreign"),
                                     type=click.Path()))
    for i, o in enumerate(spec["options"]):
        pname = _param_name(i)
        opt_names.append((o["long"], pname))
        help_txt = o["help"] + (f" [kicad default: {o['default']}]" if o.get("default") not in (None, "", "{}") else "")
        if o["takes_value"]:
            params.append(click.Option([o["long"], pname], multiple=o["repeatable"], default=None if not o["repeatable"] else (),
                                       help=help_txt, show_default=False))
        else:
            params.append(click.Option([o["long"], pname], is_flag=True, default=False, help=help_txt))
    params.append(click.Option(["--show-command", "show_command"], is_flag=True, default=False,
                               help="Print the kicad-cli argv that would run, without running it."))
    params.append(click.Option(["--allow-fail", "allow_fail"], is_flag=True, default=False,
                               help="Do not treat a non-zero kicad-cli exit as an error (still reported)."))

    def callback(**kw):
        sess = ctx_api["session"]()
        proj = sess.project
        values = {long: kw.get(p) for long, p in opt_names}
        inp = kw.get("input_file")
        if inp is None and spec["positionals"]:
            if proj is None:
                raise RuntimeError(f"{' '.join(command_path(kpath))}: give INPUT_FILE or --project")
            inp = {"sch": proj.sch_path, "pcb": proj.pcb_path, "project": proj.pro_path}.get(kind)
            if kind == "project" and not values.get("--file"):
                cands = sorted(f for f in os.listdir(proj.dir) if f.endswith(".kicad_jobset"))
                if not cands:
                    raise FileNotFoundError("No .kicad_jobset in project; create one with `kce jobset new` or pass --file")
                values["--file"] = os.path.join(proj.dir, cands[0])
        if inp is not None:
            inp = os.path.abspath(inp)
        if kpath in ("sch erc", "pcb drc") and not values.get("--format"):
            values["--format"] = "json"  # agent-friendly default (kicad-cli's own default is 'report')
        has_output = any(o["long"] == "--output" for o in spec["options"])
        if has_output and not values.get("--output"):
            d = default_output(proj, kpath, spec, values)
            if d:
                values["--output"] = d
        if values.get("--output") and kpath != "jobset run":
            out = values["--output"]
            trailing = is_dir_hint(out)
            out = os.path.abspath(out) + (os.sep if trailing else "")
            values["--output"] = out
            as_dir = trailing or (output_is_dir(spec, kpath) and not os.path.splitext(out)[1])
            mode_flags = [o["long"] for o in spec["options"] if o["long"].startswith("--mode-")]
            if as_dir and kpath in DIR_MODE_FLAG and not any(values.get(m) for m in mode_flags):
                values[DIR_MODE_FLAG[kpath]] = True  # directory output => one file per layer
            os.makedirs(out if as_dir else (os.path.dirname(out) or "."), exist_ok=True)
        if kpath == "pcb import" and not values.get("--output"):
            raise ValueError("pcb import requires --output TARGET.kicad_pcb or --project TARGET; use project import for a new project")
        mutates = kpath in ("sch upgrade", "pcb upgrade", "sym upgrade", "fp upgrade") and not values.get("--output") \
            or (kpath == "pcb drc" and values.get("--save-board"))
        argv_preview = build_argv(kpath, spec, values, inp)
        if kw.get("show_command"):
            ctx_api["output"]({"command": [*kb.kicad_cli_command(), *argv_preview], "kicad_cli": kpath},
                              "kicad-cli " + " ".join(argv_preview))
            return
        started = time.time()
        in_project = proj is not None and inp is not None and rel_inside(inp, proj.dir) is not None
        import_read_back = None
        if kpath == "pcb import":
            into_session = proj is not None and rel_inside(values["--output"], proj.dir) is not None
            res, import_read_back = importer.import_into(argv_preview, inp, values["--output"],
                                                         cwd=proj.dir if proj else None,
                                                         exclude=proj.dir if proj else os.path.dirname(values["--output"]),
                                                         publish=not into_session)
            if into_session:
                text = import_read_back.pop("_text")
                sess.snapshot("import PCB", [values["--output"]])
                sess.doc(values["--output"]).set_text(text)
        elif mutates and in_project:
            sess.snapshot(f"kicad-cli {kpath}", [inp])
            res = kb.run_kicad_cli(argv_preview, check=False)
            if os.path.exists(inp) and os.path.isfile(inp):
                sess.doc(inp).set_text(open(inp, encoding="utf-8").read())
        elif in_project and sess.dirty_docs():
            with materialize(sess) as ws:
                argv = build_argv(kpath, spec, values, remap(inp, proj.dir, ws))
                res = kb.run_kicad_cli(argv, check=False, cwd=ws)
        else:
            # run from the project dir so relative paths (jobset destinations, ${KIPRJMOD}) behave like the GUI
            res = kb.run_kicad_cli(argv_preview, check=False, cwd=proj.dir if proj is not None else None)
        result: Dict[str, Any] = {"kicad_cli": kpath, "returncode": res["returncode"],
                                  "seconds": round(time.time() - started, 2)}
        if import_read_back is not None:
            result["read_back"] = import_read_back
        if inp is not None:
            result["input"] = inp
        if values.get("--output") and kpath != "jobset run":
            base = output_base(values["--output"])
            result["output_dir"] = base
            result["outputs"] = describe_outputs(values["--output"], since=started - 1, base=base)
            if import_read_back and import_read_back["scope"] == "session":
                result["outputs"] = [{"path": os.path.basename(values["--output"]), "bytes": len(text.encode("utf-8")),
                                      "format": "kicad-sexpr", "sha256": import_read_back["sha256"], "scope": "session"}]
        if not has_output or kpath in ("jobset run", "version"):
            result["stdout"] = res["stdout"][-4000:]  # the console text is the product here
        log = notable_log(res["stdout"] if "stdout" not in result else "", res["stderr"])
        if log:
            result["log"] = log
        if kpath == "pcb render" and inp:
            from . import library
            tree = sess.tree(inp)
            models = [dict(model, reference=fp.get_property('Reference', ''))
                      for fp in tree.find_all('footprint')
                      for model in library.model_details(proj, [m.value() for m in fp.find_all('model')], os.path.dirname(inp))]
            result['models'] = {'resolved': sum(m['exists'] for m in models),
                                'missing': [m for m in models if not m['exists']]}
        if kpath in ("sch erc", "pcb drc") and (values.get("--output") or "").endswith(".json") and \
                os.path.exists(values["--output"]):
            rep = json.load(open(values["--output"], encoding="utf-8"))
            s = checks_mod.summarize(rep, "erc" if kpath == "sch erc" else "drc")
            result["summary"] = {k: s[k] for k in ("total", "errors", "warnings", "by_type", "clean", "hints")}
        ok = res["returncode"] == 0 or (kpath in ("sch erc", "pcb drc") and not values.get("--exit-code-violations"))
        if kpath in EXPORTS_3D:
            text = res["stdout"] + "\n" + res["stderr"]
            missing = missing_3d_models(text)
            if missing:
                result["missing_3d_models"] = missing[:200]
                result["hint"] = ("Some 3D model files were not found; the export contains the board without them. "
                                  "List them with `kce pcb models --missing`; install KiCad's 3D library or fix "
                                  "the paths with `kce pcb model set`.")
            skipped_vrml = "Cannot use VRML models when exporting to non-mesh formats." in text
            if skipped_vrml:
                result["skipped_vrml_models"] = True
                result["hint"] = ((result["hint"] + " ") if result.get("hint") else "") + (
                    "Some footprints use VRML (.wrl) 3D models, which this format cannot contain; the export "
                    "omits them. Give those footprints STEP models with `kce pcb model set`, or use "
                    "`kce pcb export vrml`, which keeps them.")
            if not ok and res["returncode"] == 2 and (missing or skipped_vrml) and result.get("outputs") \
                    and not any(f in text for f in _FATAL_3D):
                ok = True  # the file was written; missing/incompatible models were omitted
        if not ok and not kw.get("allow_fail"):
            details = dict(result, command=res["command"], stdout=res["stdout"][-4000:], stderr=res["stderr"][-4000:])
            raise kb.KicadError(f"kicad-cli {kpath} failed (exit {res['returncode']}): "
                                f"{(res['stderr'] or res['stdout']).strip()[-1500:]}", details)
        if result.get("outputs") is not None and has_output and not result["outputs"] and res["returncode"] == 0 \
                and kpath not in ("jobset run",):
            result["warning"] = "kicad-cli reported success but produced no files in the output location"
        ctx_api["output"](result, f"kicad-cli {kpath}: exit {res['returncode']}"
                          + (f", {len(result.get('outputs') or [])} file(s)" if has_output else ""))

    cb = ctx_api["handle_error"](callback)
    doc = spec.get("description") or kpath
    if kpath == "pcb import":
        doc += ". Output defaults to the selected project board; otherwise --output is required. Read back target hash/footprints and verify source membership."
    return click.Command(command_path(kpath)[-1], params=params, callback=cb,
                         help=f"{doc}\n\nWraps `kicad-cli {kpath}` (all options forwarded verbatim).",
                         short_help=doc[:80])


def attach_all(root: click.Group, ctx_api: Dict[str, Callable]) -> Dict[str, click.Command]:
    """Attach every spec command under the right group of ``root`` (creating groups as needed)."""
    spec = load_spec()["commands"]
    made = {}
    for kpath in sorted(spec):
        path = command_path(kpath)
        grp = root
        for name in path[:-1]:
            sub = grp.commands.get(name)
            if sub is None:
                sub = click.Group(name, help=f"{name} commands")
                grp.add_command(sub)
            grp = sub
        if path[-1] in grp.commands:
            continue  # a curated command already owns this name (must still forward all options)
        cmd = make_command(kpath, spec[kpath], ctx_api)
        grp.add_command(cmd)
        made[kpath] = cmd
    return made
