"""``kce`` — KiCad's kicad-cli, extended for AI agents.

Backends (hard dependencies): the real ``kicad-cli`` and KiCad's ``pcbnew``
Python module (run in a worker under the interpreter that can import it).
Schematics and other native files are edited through a lossless
S-expression data layer and validated by re-loading through kicad-cli.

Run without a subcommand to enter the interactive shell.
"""

from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import uuid as uuid_mod
from functools import wraps
from typing import Any, Callable, Dict, List, Optional, Tuple

import click

from .core.safety import SafeGroup

from . import APP_NAME, COMMAND, __version__
from . import skill as skill_mod
from .core import checks as checks_mod
from .core import diff as diff_mod
from .core import fab as fab_mod
from .core import bom as bom_mod
from .core import intent as intent_mod
from .core import jobset as jobset_mod
from .core import kicad_cli_cmds
from .core import library as lib_mod
from .core import netlist as nl_mod
from .core import pcb as pcb_mod
from .core import plan as plan_mod
from .core import preview as preview_mod
from .core import project as proj_mod
from .core import schematic as sch_mod
from .core import sexpr
from .core.session import Session, ValidationError
from .core.workspace import materialize, remap
from .utils import kicad_backend as kb
from .utils.paths import kicad_uri, same_path

# ── Global state ─────────────────────────────────────────────────────────────

_session: Optional[Session] = None
_json_output = False
_repl_mode = False
_plan_mode = False
_dry_run = False
_progress = False
_had_error = False
_capture: Optional[List[Any]] = None
_raise_errors = False


def get_session() -> Session:
    global _session
    if _session is None:
        _session = Session()
    return _session


def progress_event(event, **fields):
    if _progress:
        from .core.runtime import current
        request = current.get()
        click.echo(json.dumps({"progress": event, "request_id": request.request_id if request else None,
                               **fields}), err=True)


def output(data: Any, message: str = "") -> None:
    try:
        _output(data, message)
    except BrokenPipeError:  # e.g. `| head`: the command itself succeeded
        try:
            sys.stdout = open(os.devnull, "w", encoding="utf-8")
        except OSError:
            pass


def _utf8_stream(stream) -> bool:
    enc = (getattr(stream, "encoding", None) or "").lower().replace("-", "").replace("_", "")
    return enc in ("utf8", "utf8sig")


def configure_stdio() -> None:
    """Never crash on non-ASCII output: a Windows console or pipe may use cp1252."""
    if (sys.stdin is not None and not sys.stdin.isatty()
            and hasattr(sys.stdin, "reconfigure")):
        sys.stdin.reconfigure(encoding="utf-8-sig", errors="strict")
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and not _utf8_stream(stream) and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(errors="backslashreplace")
            except (ValueError, OSError):
                pass


def _output(data: Any, message: str = "") -> None:
    ctx = click.get_current_context(silent=True)
    if isinstance(data, list) and ctx is not None:
        from .core.contract import effect
        path = " ".join(ctx.command_path.split()[1:])
        if path and effect(path, ctx.params) in {"write", "export"}:
            paths = sorted(d.path for d in get_session().dirty_docs())
            data = [dict(item, dry_run=_dry_run, would_write=paths) if isinstance(item, dict) else item for item in data]
    if isinstance(data, dict) and ctx is not None and ctx.command_path.endswith('lib init-global-tables'):
        data = {name: dict(item, dry_run=_dry_run,
                          would_write=[item['path']] if item.get('action') == 'installed' else [])
                for name, item in data.items()}
    if isinstance(data, dict) and ctx is not None:
        from .core.contract import effect
        path = " ".join(ctx.command_path.split()[1:])
        if path and path != "lib init-global-tables" and effect(path, ctx.params) in {"write", "export"}:
            data = dict(data)
            data.setdefault("dry_run", _dry_run)
            paths = [d.path for d in get_session().dirty_docs()]
            for key in ("created", "saved", "changed_files"):
                values = data.get(key, [])
                if isinstance(values, (list, tuple)):
                    paths.extend(p for p in values if isinstance(p, str))
            for key in ("out", "output", "output_dir", "library", "archive", "report"):
                if isinstance(data.get(key), str):
                    paths.append(data[key])
            artifacts = [item for key in ('outputs', 'files')
                         if isinstance(data.get(key), (list, tuple)) for item in data[key]]
            for item in artifacts:
                if not isinstance(item, dict):
                    continue
                if isinstance(item.get('files'), list):
                    paths.extend(os.path.join(item['path'], f['path']) for f in item['files'] if isinstance(f.get('path'), str))
                elif isinstance(item.get('path'), str):
                    base = data.get('output_dir', '')
                    paths.append(os.path.join(base, item['path']) if base else item['path'])
            data.setdefault("would_write", sorted(set(paths)))
    if _capture is not None:
        _capture.append(data)
        return
    if _json_output:
        # pretty on a terminal, compact when piped (fewer tokens for agents); JSON stays valid in any
        # console encoding: non-ASCII is escaped when stdout isn't UTF-8
        tty = getattr(sys.stdout, "isatty", lambda: False)()
        click.echo(json.dumps(data, default=str, ensure_ascii=not _utf8_stream(sys.stdout),
                              indent=2 if tty else None, separators=None if tty else (",", ":")))
        return
    if message:
        click.echo(message)
    _human(data, 1)


def _human(data: Any, indent: int) -> None:
    pad = "  " * indent
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, (dict, list)) and v and len(json.dumps(v, default=str)) > 100:
                click.echo(f"{pad}{k}:")
                _human(v, indent + 1)
            else:
                click.echo(f"{pad}{k}: {json.dumps(v, default=str, ensure_ascii=False) if isinstance(v, (dict, list)) else v}")
    elif isinstance(data, list):
        for i, item in enumerate(data[:200]):
            if isinstance(item, dict):
                click.echo(f"{pad}- " + ", ".join(f"{k}={v}" for k, v in list(item.items())[:8]))
            else:
                click.echo(f"{pad}- {item}")
        if len(data) > 200:
            click.echo(f"{pad}... ({len(data) - 200} more; use --json)")
    elif data is not None:
        click.echo(f"{pad}{data}")


_HINT = re.compile(r"`((?:kce|kicad-cli|pip|pipx|uv) [^`]+)`")
# message pattern -> command that shows the valid choices
_HINT_BY_MESSAGE = (
    ("No symbol with reference", f"{COMMAND} sch symbols"),
    ("No footprint", f"{COMMAND} pcb footprints"),
    ("has no pad", f"{COMMAND} pcb footprint REF"),
    ("Unknown layer", f"{COMMAND} pcb layers"),
    ("No board item with uuid", f"{COMMAND} pcb drawings"),
    ("Sheet ", f"{COMMAND} sch hierarchy"),
    ("No .kicad_pro", f"{COMMAND} project new DIR"),
)


def _error_hint(msg: str) -> str:
    """The next command to run: the one the message names, else a lookup, else the command's --help."""
    m = _HINT.search(msg)
    if m:
        return m.group(1)
    for needle, cmd in _HINT_BY_MESSAGE:
        if needle in msg:
            return cmd
    ctx = click.get_current_context(silent=True)
    return f"{ctx.command_path} --help" if ctx is not None else f"{COMMAND} --help"


# ── Failure signals ──────────────────────────────────────────────────────────
# One table maps every exception to a stable ``type`` name and a ``category`` with its exit
# code, so a caller can tell "my arguments are wrong" from "kce or KiCad broke".

EXIT_CODES = {"internal": 1, "input": 2, "design": 3, "validation": 4, "crash": 5, "timeout": 6,
              "environment": 7}


def _kicad_timeout(e) -> bool:
    return isinstance(e, kb.KicadError) and "timed out" in str(e).lower()


# kicad-cli's own exit codes (include/cli/exit_codes.h): 1 bad arguments, 2 unknown, 3 invalid
# input file, 4 output conflict, 5 ERC/DRC violations (--exit-code-violations), 6 jobset failed.
_KICAD_EXIT_INPUT = (1, 3, 4)
_KICAD_EXIT_DESIGN = (5, 6)


def _kicad_cli_exit(codes):
    """A failed ``kicad-cli`` run (the details carry its argv) that ended with one of ``codes``."""
    def test(e) -> bool:
        d = e.details if isinstance(e, kb.KicadError) else {}
        return "command" in d and d.get("returncode") in codes
    return test


_WORKER_NAME = re.compile(r"^pcbnew [\w-]+: (\w+): ")


def _worker_error(e) -> Optional[str]:
    """Exception name when the pcbnew worker raised it while running an op (not a crash)."""
    if isinstance(e, kb.KicadError) and e.details.get("worker_type"):
        return e.details["worker_type"]
    if isinstance(e, kb.KicadError) and "traceback" in (e.details or {}):
        m = _WORKER_NAME.match(str(e))
        return m.group(1) if m else "Exception"
    return None


def _kicad_crash(e) -> bool:
    return isinstance(e, kb.KicadError) and (
        bool((e.details or {}).get("returncode")) or "crashed" in str(e).lower()
        or "returned garbage" in str(e).lower())


# RuntimeErrors that describe a caller-visible state (a missing --project, a locked item, a
# failed ERC run, a missing tool), not a kce bug. Keyed by a message needle.
_STATE_ERRORS: Tuple[Tuple[str, str], ...] = (
    ("No KiCad project loaded", "input"),
    ("needs a project", "input"),
    ("Nothing to undo", "input"),
    ("Nothing to redo", "input"),
    ("Project is locked by", "input"),
    ("ERC failed", "design"),
    ("DRC failed", "design"),
    ("DRC has ", "design"),
    ("Fab package incomplete", "design"),
    ("BOM check failed", "design"),
    ("PCB score failed", "design"),
    ("Freerouting not found", "environment"),
    ("KiKit is not installed", "environment"),
    ("need the optional kicad-python package", "environment"),
    ("No KiCad GUI answering on", "environment"),
    ("KiCad has no board open in the PCB editor", "input"),
    ("KiCad rejected action", "input"),
)


def _state_error(category: str):
    def test(e) -> bool:
        if not isinstance(e, RuntimeError) or isinstance(e, kb.KicadError):
            return False
        return any(cat == category and needle in str(e) for needle, cat in _STATE_ERRORS)
    return test


# (test, type, category) — first match wins.
_ERROR_TABLE: Tuple[Tuple[Callable[[BaseException], bool], Optional[str], str], ...] = (
    (lambda e: isinstance(e, sch_mod.NetMergeError), "NetMergeError", "design"),
    (lambda e: isinstance(e, sch_mod.NetSplitError), "NetSplitError", "design"),
    (lambda e: isinstance(e, sch_mod.RouteBlockedError), "RouteBlocked", "design"),
    # sch verify --strict: the schematic is valid but does not realize the intent
    (lambda e: isinstance(e, intent_mod.DesignMismatch), "DesignMismatch", "design"),
    (lambda e: isinstance(e, plan_mod.AcceptanceFailed), "AcceptanceFailed", "design"),
    (lambda e: isinstance(e, jobset_mod.JobsetFailed), "JobsetFailed", "design"),
    (lambda e: isinstance(e, ValidationError), "ValidationError", "validation"),
    (lambda e: isinstance(e, subprocess.TimeoutExpired), "ToolTimeout", "timeout"),  # Freerouting, ngspice, kikit
    (_kicad_timeout, "KicadTimeout", "timeout"),
    # a locked item surfaces either directly or wrapped by the pcbnew worker's KicadError
    (lambda e: " is locked;" in str(e), "ItemLocked", "input"),
    (_kicad_cli_exit(_KICAD_EXIT_INPUT), "KicadInputError", "input"),
    (_kicad_cli_exit(_KICAD_EXIT_DESIGN), "KicadCheckFailed", "design"),
    (_kicad_crash, "KicadCrash", "crash"),
    (lambda e: _worker_error(e) == "PlacementBlocked", "PlacementBlocked", "design"),
    *((lambda e, n=n: _worker_error(e) == n, n, "input")
      for n in ("KeyError", "ValueError", "FileNotFoundError", "FileExistsError")),
    (lambda e: _worker_error(e) is not None, "KicadError", "internal"),
    (lambda e: isinstance(e, kb.KicadError) and any(x in str(e).lower() for x in
         ("not found", "missing file", "requires kicad", "cannot import pcbnew", "no python interpreter")), "KicadNotFound", "environment"),
    (lambda e: isinstance(e, kb.KicadError), "KicadError", "internal"),
    (_state_error("input"), "RuntimeError", "input"),
    (_state_error("design"), "RuntimeError", "design"),
    (_state_error("environment"), "RuntimeError", "environment"),
    (lambda e: isinstance(e, FileNotFoundError), "FileNotFoundError", "input"),
    (lambda e: isinstance(e, FileExistsError), "FileExistsError", "input"),
    (lambda e: isinstance(e, KeyError), "KeyError", "input"),
    (lambda e: isinstance(e, ValueError), None, "input"),
    (lambda e: isinstance(e, TypeError), "TypeError", "internal"),
    (lambda e: isinstance(e, IndexError), "IndexError", "internal"),
    (lambda e: isinstance(e, AttributeError), "InternalError", "internal"),
    (lambda e: isinstance(e, OSError), "OSError", "environment"),
    (lambda e: isinstance(e, RuntimeError), "RuntimeError", "internal"),
)


def _documented_name(exc: BaseException) -> str:
    """The documented name for a generic input exception (a ValueError stays ``ValueError``
    even when it is a subclass such as ``json.JSONDecodeError``)."""
    for base in (sexpr.SexprError, ValueError, TypeError, IndexError):
        if isinstance(exc, base):
            return base.__name__
    return type(exc).__name__


def classify_error(exc: BaseException) -> Tuple[str, str]:
    """(stable type name, category) for an exception; see the table in README."""
    if isinstance(exc, plan_mod.PlanError):
        return exc.type, exc.category
    for test, name, category in _ERROR_TABLE:
        if test(exc):
            return (name or _documented_name(exc)), category
    return "InternalError", "internal"


def error_payload(exc: BaseException) -> Dict[str, Any]:
    if hasattr(exc, "_kce_payload"):
        return dict(exc._kce_payload)
    msg = str(exc).strip("'\"") if isinstance(exc, KeyError) else str(exc)
    name, category = classify_error(exc)
    payload: Dict[str, Any] = {"error": msg, "type": name, "category": category}
    payload["hint"] = _error_hint(msg)
    if isinstance(exc, kb.KicadError) and exc.details:
        payload["details"] = {k: v for k, v in exc.details.items() if k != "traceback"}
    if hasattr(exc, "runtime_details"):
        payload["details"] = {**payload.get("details", {}), **exc.runtime_details}
    if getattr(exc, "rollback_failed", False):
        payload['details'] = {**payload.get('details', {}), 'rolled_back': False, 'commit_state': 'unknown',
                              'original_error': str(exc.rollback_cause)}
    if isinstance(exc, plan_mod.AcceptanceFailed):
        payload["details"] = exc.details
    if isinstance(exc, jobset_mod.JobsetFailed):
        payload["details"] = exc.details
    if isinstance(exc, sch_mod.NetSplitError):
        payload["details"] = exc.report
    if isinstance(exc, sch_mod.NetMergeError):
        payload["details"] = {"merges": exc.merges}
    if category == "internal":  # a kce bug: the report needs the stack
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        payload["details"] = {**payload.get("details", {}), "traceback": tb[-4000:]}
    if isinstance(exc, plan_mod.PlanError):
        payload["hint"] = exc.payload["hint"]
        payload["details"] = exc.details()
    return payload


def handle_error(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        global _had_error
        try:
            return f(*args, **kwargs)
        except (click.ClickException, click.exceptions.Exit, click.Abort):
            raise
        except Exception as e:
            _had_error = True
            if _raise_errors:
                e._kce_payload = error_payload(e)
                raise
            payload = error_payload(e)
            if _json_output:
                click.echo(json.dumps(payload, indent=2, default=str), err=True)
            else:
                click.echo(f"Error: {payload['error']}", err=True)
            if not _repl_mode:
                get_session().discard()
                sys.exit(EXIT_CODES[payload["category"]])
    return wrapper


def parse_kv(items) -> Dict[str, str]:
    out = {}
    for it in items or ():
        if "=" not in it:
            raise ValueError(f"Expected KEY=VALUE, got {it!r}")
        k, v = it.split("=", 1)
        out[k.strip()] = v
    return out


def parse_xy(s: str):
    return sch_mod.parse_point(s)


def parse_points(s: str) -> List[List[float]]:
    pts = []
    for part in s.replace(" ", "").split(";"):
        if part:
            x, y = parse_xy(part)
            pts.append([x, y])
    if len(pts) < 2:
        raise ValueError("Need at least two points 'x1,y1;x2,y2'")
    return pts


def _unique_paths(paths) -> List[str]:
    """Absolute paths without duplicates (case-insensitive on Windows), sorted."""
    out = {}
    for p in paths:
        out.setdefault(os.path.normcase(os.path.abspath(p)), os.path.abspath(p))
    return sorted(out.values())


def _invocation() -> str:
    return " ".join([COMMAND, *sys.argv[1:]])


def run_in_process(argv: List[str], project: Optional[str] = None, *, session: Optional[Session] = None) -> Any:
    """Return a command's CLI JSON, preserving the caller's invocation state."""
    global _session, _capture, _raise_errors, _had_error, _json_output, _dry_run, _progress
    previous = (_session, _capture, _raise_errors, _had_error, _json_output, _dry_run, _progress)
    captured: List[Any] = []
    _session = session if session is not None else Session()
    _capture, _raise_errors, _had_error = captured, True, False
    args = ["--json", *(["--project", project] if project else []), *argv]
    stdout = io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout):
            cli.main(args, standalone_mode=False, prog_name=COMMAND)
        return captured[-1] if captured else {"help": stdout.getvalue().rstrip()}
    except click.ClickException as e:
        raise ValueError(f"{e.format_message()}; see `kce {' '.join(argv[:2])} --help`.") from e
    finally:
        if session is None:
            _session.discard()
        (_session, _capture, _raise_errors, _had_error, _json_output, _dry_run, _progress) = previous


def _proj():
    return get_session().require_project()


def _sch_file(sheet: Optional[str]) -> str:
    """Target schematic file: project root or a sub-sheet file/name."""
    proj = _proj()
    if not sheet:
        return proj.sch_path
    cand = sheet if os.path.isabs(sheet) else os.path.join(proj.dir, sheet)
    if os.path.exists(cand) or cand in get_session().docs:
        return os.path.abspath(cand)
    if os.path.exists(cand + ".kicad_sch"):
        return os.path.abspath(cand + ".kicad_sch")
    for h in sch_mod.hierarchy(get_session().tree, proj.sch_path):
        if h["name"] == sheet:
            return os.path.abspath(h["file"])
    raise FileNotFoundError(f"Sheet {sheet!r} not found (file name, path or sheet name)")


def _sch_tree(sheet: Optional[str]):
    path = _sch_file(sheet)
    return path, get_session().tree(path)


def _sch_mutate(sheet: Optional[str], desc: str):
    path = _sch_file(sheet)
    sess = get_session()
    sess.snapshot(desc, [path])
    tree = sess.tree(path)
    sess.doc(path).mark_dirty()
    return path, tree


def _instance_paths(path: str) -> List[str]:
    proj = _proj()
    sess = get_session()
    if not os.path.exists(proj.sch_path) and not sess.has_doc(proj.sch_path):
        return []
    paths = sch_mod.instance_paths_for(sess.tree, proj.sch_path, path)
    return paths


# ── validation of saved files through the real KiCad ────────────────────────

def validate_files(paths: List[str]) -> List[Dict[str, Any]]:
    sess = get_session()
    proj = sess.project
    results = []
    sch_done = False
    td = kb.mkdtemp("validate")
    try:
        for p in paths:
            if not os.path.exists(p):
                continue
            ext = os.path.splitext(p)[1]
            base = os.path.basename(p)
            if ext == ".kicad_sch":
                if sch_done:
                    continue
                root = proj.sch_path if proj and os.path.exists(proj.sch_path) else p
                kb.run_kicad_cli(["sch", "export", "netlist", "-o", os.path.join(td, "v.net"), root])
                results.append({"file": root, "check": "kicad-cli sch export netlist", "ok": True})
                sch_done = True
            elif ext == ".kicad_pcb":
                kb.run_kicad_cli(["pcb", "export", "stats", "--format", "json", "-o", os.path.join(td, "s.json"), p])
                results.append({"file": p, "check": "kicad-cli pcb export stats", "ok": True})
            elif ext == ".kicad_sym":
                kb.run_kicad_cli(["sym", "upgrade", "--force", "-o", os.path.join(td, base), p])
                results.append({"file": p, "check": "kicad-cli sym upgrade (load)", "ok": True})
            elif ext == ".kicad_mod":
                kb.run_kicad_cli(["fp", "upgrade", "--force", "-o", os.path.join(td, "fp.pretty"),
                                  os.path.dirname(p)])
                results.append({"file": p, "check": "kicad-cli fp upgrade (load)", "ok": True})
            elif ext in (".kicad_pro", ".kicad_jobset"):
                json.load(open(p, encoding="utf-8"))
                results.append({"file": p, "check": "json", "ok": True})
            elif base in ("sym-lib-table", "fp-lib-table"):
                sexpr.load(p)
                results.append({"file": p, "check": "sexpr", "ok": True})
            elif ext == ".kicad_dru":
                sexpr.loads_many(open(p, encoding="utf-8").read())
                results.append({"file": p, "check": "sexpr rules", "ok": True})
    finally:
        import shutil
        shutil.rmtree(td, ignore_errors=True)
    return results


def save_now() -> Dict[str, Any]:
    sess = get_session()
    return sess.save_session(validator=validate_files)


# ── Schematic connectivity guard ────────────────────────────────────────────
# Writing geometry can silently short two nets in KiCad; every connectivity-changing command
# compares KiCad's own netlist before and after and rolls the edit back when an unrequested
# merge appeared.  ``--allow-merge`` switches the guard off (used to build test mutants).


def _pin_net_map(nl: Dict[str, Any]) -> Dict[Tuple[str, str], str]:
    return {(nd["ref"], str(nd["pin"])): n["name"] for n in nl["nets"] for nd in n["nodes"]}


def _pins_at_point(tree, point: Tuple[float, float]) -> set:
    out = set()
    for s in sch_mod.placed_symbols(tree):
        try:
            pins = sch_mod.symbol_pins(tree, s)
        except KeyError:
            continue
        for p in pins:
            if abs(p["x"] - point[0]) <= 0.01 and abs(p["y"] - point[1]) <= 0.01:
                out.add((sch_mod.sym_ref(s), str(p["number"])))
    return out


def _anchor_net(tree, spec, nl: Dict[str, Any]) -> Optional[str]:
    """Net a ``REF:PIN`` or ``x,y`` anchor was on before the edit (None when unconnected).

    A point that is not on a pin takes the net of the wires it touches, so a pin may be
    wired to the middle of an existing wire.
    """
    pm = _pin_net_map(nl)
    try:
        if isinstance(spec, str) and not re.fullmatch(r"\s*-?[\d.]+\s*,\s*-?[\d.]+\s*", spec):
            ref, pin = sch_mod.parse_pin_ref(spec)
            try:
                _, p = sch_mod.find_pin(tree, ref, pin)
            except (KeyError, ValueError):
                return None
            return pm.get((ref, str(p["number"])))
        point = spec if isinstance(spec, tuple) else sch_mod.parse_point(spec)
    except (ValueError, KeyError):
        return None
    pins = _pins_at_point(tree, point)
    if not pins:
        pins = set().union(*(_pins_at_point(tree, q) for q in sch_mod.wire_cluster(tree, [point])[1]))
    names = {pm[k] for k in pins if k in pm}
    return names.pop() if len(names) == 1 else None


def _merged_groups(before: Dict[str, Any], after: Dict[str, Any]) -> List[Dict[str, Any]]:
    """After-nets that contain pins from two or more separate before-nets."""
    pm = _pin_net_map(before)
    groups = []
    for n in after["nets"]:
        names, pins = set(), []
        for nd in n["nodes"]:
            bname = pm.get((nd["ref"], str(nd["pin"])))
            if bname:
                names.add(bname)
                pins.append({"ref": nd["ref"], "pin": str(nd["pin"]), "net": bname})
        if len(names) >= 2:
            pins.sort(key=lambda p: (nl_mod.natural_key(p["ref"]), nl_mod.natural_key(p["pin"])))
            groups.append({"nets": sorted(names, key=nl_mod.natural_key), "pins": pins,
                           "merged_into": n["name"]})
    return groups


_AUTO_NET = re.compile(r"(^|/)(Net-\(|unconnected-\()")


def _unexpected_merges(groups, allowed_pairs: Optional[set], any_mode: bool,
                       anchor: Optional[str]) -> List[Dict[str, Any]]:
    """Merges the command did not ask for.  A label or power symbol may name an auto-named net
    (``Net-(R1-Pad1)``) into a named one, but never join two nets that both carry a name
    (``+5V`` into ``GND`` is a short, not a rename)."""
    bad = []
    for g in groups:
        names = set(g["nets"])
        if any_mode:
            ok = (len(groups) == 1 and len(names) == 2 and (anchor is None or anchor in names)
                  and any(_AUTO_NET.search(n) for n in names))
        else:
            ok = allowed_pairs is not None and frozenset(names) in allowed_pairs
        if not ok:
            bad.append(g)
    return bad


def _guarded_sch_mutate(sheet: Optional[str], desc: str, mutate, *, allow_merge: bool = False,
                        merge_mode: str = "none", requested_pairs: Optional[list] = None,
                        anchor_spec=None):
    """Apply ``mutate(tree)`` to a copy of the sheet, refusing it when it merges foreign nets."""
    path = _sch_file(sheet)
    sess = get_session()
    doc = sess.doc(path)
    before_text = sess.current_text(path)
    pre_dirty = doc.dirty
    tree = sess.tree(path)
    work = copy.deepcopy(tree)
    allowed_pairs: Optional[set] = None
    anchor: Optional[str] = None
    before_nl = None
    if not allow_merge:
        before_nl = pcb_mod.schematic_netlist(sess)
        if merge_mode == "pairs" and requested_pairs and len(requested_pairs) == 2:
            na = _anchor_net(tree, requested_pairs[0], before_nl)
            nb = _anchor_net(tree, requested_pairs[1], before_nl)
            allowed_pairs = {frozenset((na, nb))} if na and nb and na != nb else set()
        elif merge_mode == "any" and anchor_spec:
            anchor = _anchor_net(tree, anchor_spec, before_nl)
    sess.snapshot(desc, [path])
    try:
        result = mutate(work)
    except BaseException:
        sess.rollback_last_snapshot()
        raise
    doc.set_tree(work)
    doc.mark_dirty()
    if allow_merge:
        return result

    def undo_edit():
        doc.set_text(before_text)
        doc.dirty = pre_dirty
        sess.rollback_last_snapshot()

    try:  # a failing netlist export must not leave an unchecked edit in the session either
        bad = _unexpected_merges(_merged_groups(before_nl, pcb_mod.schematic_netlist(sess)), allowed_pairs,
                                 merge_mode == "any", anchor)
    except BaseException:
        undo_edit()
        raise
    if bad:
        undo_edit()
        raise sch_mod.NetMergeError(bad, desc)
    return result


ALLOW_MERGE_OPT = click.option(
    "--allow-merge", is_flag=True,
    help="Do not refuse an edit that joins nets (build test mutants on purpose).")


# ── Main group ───────────────────────────────────────────────────────────────

@click.group(invoke_without_command=True, cls=SafeGroup)
@click.option("--json", "use_json", is_flag=True, help="Machine-readable JSON output.")
@click.option("--project", "-p", "project_path", type=click.Path(), default=None,
              help="KiCad project (.kicad_pro, .kicad_sch/.kicad_pcb, or directory). Default: the single "
                   ".kicad_pro in the current directory.")
@click.option("--dry-run", "dry_run", is_flag=True, default=False,
              help="Execute on isolated copies; return dry_run/would_write without changing files, history or outputs.")
@click.option("--no-validate", "no_validate", is_flag=True, default=False,
              help="Skip re-loading saved files through kicad-cli (faster, less safe).")
@click.option("--progress", is_flag=True, help="Emit request/plan progress JSON lines on stderr; stdout stays machine-readable.")
@click.option("--read-only", is_flag=True, help="Refuse write/export/control effects before execution.")
@click.option("--session-dir", type=click.Path(), help="Store state outside the project (also KCE_SESSION_DIR).")
@click.version_option(__version__, prog_name=COMMAND, message=f"%(prog)s ({APP_NAME}) %(version)s")
@click.pass_context
def cli(ctx, use_json, project_path, dry_run, no_validate, read_only, session_dir, progress):
    """kce (KiCad CLI Extended): every kicad-cli command plus schematic editing, PCB update and
    routing, fab packages, previews and diffs, built for AI agents.

    Every command supports --json. Mutations auto-save after one-shot commands
    (unless --dry-run) and are recorded for `kce session undo` / `kce session redo`.
    Run without a subcommand for an interactive shell.
    """
    global _json_output, _dry_run, _had_error, _progress
    _had_error = False
    if _raise_errors and ctx.invoked_subcommand in (None, "repl", "serve"):
        raise ValueError("Resident commands must name a one-shot command; see `kce --help`.")
    _json_output = use_json
    _dry_run = dry_run
    _progress = progress
    sess = get_session()
    if session_dir:
        sess.session_dir = os.path.abspath(session_dir)
    if no_validate:
        sess.validate = False
    proj = None
    if project_path:
        try:
            proj = proj_mod.resolve_project(project_path)
        except (FileNotFoundError, ValueError) as e:
            raise click.UsageError(f"--project: {e}")
    elif sess.project is None:
        try:
            proj = proj_mod.resolve_project(None)
        except (FileNotFoundError, ValueError):
            proj = None
    if proj is not None and (sess.project is None or sess.project.pro_path != proj.pro_path):
        sess.set_project(proj)
    if ctx.invoked_subcommand is None:
        ctx.invoke(repl, project_path=project_path)


@cli.result_callback()
def auto_save_on_exit(result, **kwargs):
    """Auto-save after one-shot commands (not in REPL, not with --dry-run, not after errors)."""
    from .core import runtime
    runtime.remaining(10**9)
    sess = get_session()
    if _repl_mode or _plan_mode or _had_error:
        return
    if _dry_run:
        if sess.dirty_docs() and not _json_output:
            click.echo(f"(dry-run: {len(sess.dirty_docs())} file(s) not saved)", err=True)
        sess.discard()
        return
    if sess.has_project() and sess.dirty_docs():
        try:
            res = save_now()
            if not _json_output:
                click.echo(f"Saved {len(res['saved'])} file(s)"
                           + (f", validated by KiCad ({len(res['validation'])} check(s))" if res["validation"] else ""),
                           err=True)
        except Exception as e:  # validation failed -> rolled back
            payload = error_payload(e)
            payload["error"] = f"Auto-save failed: {payload['error']}"
            if _raise_errors:
                e._kce_payload = payload
                raise
            click.echo(json.dumps(payload, default=str) if _json_output else f"Error: {payload['error']}", err=True)
            sys.exit(EXIT_CODES[payload["category"]])
    else:
        sess.release_lock()


@cli.command("errors")
@handle_error
def errors():
    """List stable error types, categories, exit codes and recovery commands."""
    categories = {name: category for _, name, category in _ERROR_TABLE if name}
    categories.update(SexprError="input", UsageError="input", InternalError="internal", RuntimeError="internal")
    meaning = {
        "input": "Fix arguments or references before retrying.",
        "design": "Design or acceptance gate rejected the change; review the result.",
        "validation": "Saved or checkpoint bytes failed validation; changes were rolled back.",
        "crash": "Native backend exited or returned invalid data; inspect details.",
        "timeout": "Deadline or cancellation stopped the native operation; inspect commit_state.",
        "environment": "Required binary, interpreter, library or capability is unavailable.",
        "internal": "Unexpected kce or worker fault; preserve details and report it.",
    }
    next_commands = {c: "kce info doctor" if c == "environment" else
                     "kce session restore NAME" if c in {"timeout", "crash", "validation"} else
                     "kce check diagnose" if c == "design" else "kce info describe COMMAND" for c in meaning}
    items = [{"type": name, "category": category, "exit_code": EXIT_CODES[category],
              "meaning": meaning[category] + (" RuntimeError can also describe input/design/environment state; use category." if name == "RuntimeError" else ""),
              "next_command_pattern": next_commands[category]}
             for name, category in sorted(categories.items())]
    output({"count": len(items), "items": items, "exit_codes": EXIT_CODES})


# ═════════════════════════════════════════════════════════════════════════════
# info
# ═════════════════════════════════════════════════════════════════════════════

@cli.group("info")
def info_group():
    """Backend discovery and environment introspection."""


@info_group.command("doctor")
@click.option("--require", default=None, help="Require capabilities: step,render,freerouting,ngspice,display (comma-separated); exit 7 if unavailable.")
@handle_error
def info_doctor(require):
    """Report kicad-cli, pcbnew interpreter, library dirs, lib tables (works on any OS)."""
    d = kb.doctor()
    requested = set(require.split(',')) if require else set()
    unknown = requested - set(d['capabilities'])
    if unknown:
        raise ValueError(f"Unknown capabilities {sorted(unknown)}; use `kce info doctor --help`.")
    missing = sorted(c for c in requested if not d['capabilities'][c])
    output(d, "KiCad backend: " + ("OK" if d.get("ok") else "PROBLEMS FOUND"))
    if missing:
        raise kb.KicadError(f"Required capabilities not found: {', '.join(missing)}; inspect `kce info doctor`.",
                            {'missing_capabilities': missing})


@info_group.command("paths")
@handle_error
def info_paths():
    """KiCad path variables (KICAD10_*_DIR, KIPRJMOD) as resolved for the project."""
    sess = get_session()
    output(kb.kicad_env_vars(sess.project.dir if sess.project else None))


@info_group.command("version")
@handle_error
def info_version():
    """Versions of this tool, KiCad and pcbnew."""
    output({"version": __version__, "kicad": kb.kicad_version(),
            "pcbnew": kb.find_pcbnew_python().get("pcbnew_version"),
            "kicad_cli_spec": kicad_cli_cmds.load_spec()["kicad_cli_version"]})


@info_group.command("commands")
@click.option("--full", is_flag=True, help="Include effects, parameters, schemas, examples and exit codes.")
@handle_error
def info_commands(full):
    """Discover every command. Use info describe COMMAND for its complete contract."""
    from .core.contract import leaves, describe
    items = [describe(path, cmd) if full else {"command": path,
             "help": (cmd.short_help or cmd.help or "").split("\n")[0]}
             for path, cmd in leaves(cli)]
    output(items, f"{len(items)} commands")


@info_group.command("schema")
@click.argument("name", required=False)
@handle_error
def info_schema(name):
    """List public schemas, or print NAME (e.g. plan, run-result, sch-nets, error). Works offline."""
    from pathlib import Path
    root = Path(__file__).resolve().parent / "schema_data"
    schemas = {p.name.removesuffix(".schema.json"): p for p in root.glob("*.schema.json")}
    if name is None:
        output({"schemas": sorted(schemas)})
    elif name not in schemas:
        raise ValueError(f"Unknown schema {name!r}; list names with `kce info schema`.")
    else:
        output(json.loads(schemas[name].read_text(encoding="utf-8")))


@info_group.command("describe")
@click.argument("command", nargs=-1, required=True)
@handle_error
def info_describe(command):
    """Describe COMMAND: effect, arguments, public result schema, example and exit codes."""
    from .core.contract import leaves, describe
    path = " ".join(command)
    commands = dict(leaves(cli))
    if path not in commands:
        raise ValueError(f"Unknown command {path!r}; run `kce info commands`.")
    output(describe(path, commands[path]))


# ═════════════════════════════════════════════════════════════════════════════
# project
# ═════════════════════════════════════════════════════════════════════════════

@cli.group("project")
def project_group():
    """Project files, templates, text variables, net classes, rules, lib tables, archives."""


@project_group.command("gitignore")
@click.option("--append", "append_", is_flag=True, help="Append missing rules to the project's .gitignore.")
@handle_error
def project_gitignore(append_):
    """Print recommended Git ignore rules, or append missing rules with --append."""
    rules = proj_mod.GITIGNORE.splitlines()
    path = os.path.join(_proj().dir, ".gitignore")
    if append_:
        sess = get_session()
        before = sess.current_text(path) or ""
        missing = [r for r in rules if r not in before.splitlines()]
        if missing:
            sess.snapshot("project gitignore", [path])
            sess.doc(path, "text").set_text(before + ("\n" if before and not before.endswith("\n") else "") + "\n".join(missing) + "\n")
    output({"path": path, "rules": rules, "appended": append_})


@project_group.command("new")
@click.argument("directory", type=click.Path())
@click.option("--name", "-n", default=None, help="Project name (default: directory name).")
@click.option("--template", "-t", default="default", help="KiCad template name (see `kce project templates`).")
@click.option("--title", default=None, help="Schematic title block title.")
@click.option("--overwrite", is_flag=True)
@handle_error
def project_new(directory, name, template, title, overwrite):
    """Create a project (KiCad itself normalises the new schematic and board)."""
    res = proj_mod.create_project(directory, name=name, template=template, title=title, overwrite=overwrite)
    get_session().set_project(proj_mod.KicadProject(res["project"]["pro"]))
    output(res, f"Created project {res['project']['name']} in {res['project']['dir']}")


@project_group.command("templates")
@handle_error
def project_templates():
    """List KiCad project templates."""
    output(proj_mod.list_templates())


@project_group.command("open")
@click.argument("path", type=click.Path(exists=True))
@handle_error
def project_open(path):
    """Open a project in this session (REPL)."""
    get_session().set_project(proj_mod.resolve_project(path))
    output(proj_mod.project_info(_proj(), get_session()), f"Opened {_proj().pro_path}")


@project_group.command("info")
@handle_error
def project_info():
    """Project summary: files, schematic/board counts, variables, net classes."""
    output(proj_mod.project_info(_proj(), get_session()))


@project_group.command("files")
@handle_error
def project_files():
    """KiCad files in the project directory."""
    output(proj_mod.list_files(_proj()))


@project_group.command("save")
@handle_error
def project_save():
    """Write unsaved changes (REPL) — validated by kicad-cli."""
    output(save_now(), "Saved")


@project_group.command("get")
@click.argument("key", required=False, default="")
@handle_error
def project_get(key):
    """Read a .kicad_pro JSON value by dotted key (e.g. board.design_settings.rules)."""
    output(proj_mod.pro_get(get_session(), key))


@project_group.command("set")
@click.argument("key")
@click.argument("value")
@handle_error
def project_set(key, value):
    """Set a .kicad_pro JSON value (VALUE parsed as JSON when possible)."""
    output(proj_mod.pro_set(get_session(), key, proj_mod.parse_value(value)))


@project_group.group("textvar")
def textvar_group():
    """Project text variables (${NAME} in schematic/board text)."""


@textvar_group.command("list")
@handle_error
def textvar_list():
    output(proj_mod.textvar_list(get_session()))


@textvar_group.command("set")
@click.argument("name")
@click.argument("value")
@handle_error
def textvar_set(name, value):
    output(proj_mod.textvar_set(get_session(), name, value))


@textvar_group.command("delete")
@click.argument("name")
@handle_error
def textvar_delete(name):
    output(proj_mod.textvar_delete(get_session(), name))


@project_group.group("netclass")
def netclass_group():
    """Net classes (track width, clearance, vias) and pattern assignments."""


@netclass_group.command("list")
@handle_error
def netclass_list():
    output(proj_mod.netclass_list(get_session()))


@netclass_group.command("set")
@click.argument("name")
@click.option("--set", "values", multiple=True, help="field=value, e.g. track_width=0.5 clearance=0.25")
@click.option("--track-width", type=float)
@click.option("--clearance", type=float)
@click.option("--via-diameter", type=float)
@click.option("--via-drill", type=float)
@click.option("--diff-pair-width", type=float)
@click.option("--diff-pair-gap", type=float)
@handle_error
def netclass_set(name, values, track_width, clearance, via_diameter, via_drill, diff_pair_width, diff_pair_gap):
    """Create or update a net class."""
    v = parse_kv(values)
    for k, val in (("track_width", track_width), ("clearance", clearance), ("via_diameter", via_diameter),
                   ("via_drill", via_drill), ("diff_pair_width", diff_pair_width), ("diff_pair_gap", diff_pair_gap)):
        if val is not None:
            v[k] = val
    output(proj_mod.netclass_set(get_session(), name, v))


@netclass_group.command("delete")
@click.argument("name")
@handle_error
def netclass_delete(name):
    output(proj_mod.netclass_delete(get_session(), name))


@netclass_group.command("assign")
@click.argument("pattern")
@click.argument("netclass")
@handle_error
def netclass_assign(pattern, netclass):
    """Assign nets matching PATTERN (wildcards, e.g. '/VCC*' or 'GND') to NETCLASS."""
    output(proj_mod.netclass_assign(get_session(), pattern, netclass))


@project_group.group("rules")
def rules_group():
    """Board setup: design-rule constraints, severities, presets, custom .kicad_dru rules."""


@rules_group.command("show")
@handle_error
def rules_show():
    output(proj_mod.rules_get(get_session()))


@rules_group.command("set")
@click.argument("values", nargs=-1, required=True)
@handle_error
def rules_set(values):
    """Set constraints: min_clearance=0.15 min_track_width=0.15 ... (mm)."""
    output(proj_mod.rules_set(get_session(), {k: proj_mod.parse_value(v) for k, v in parse_kv(values).items()}))


@rules_group.command("severity")
@click.argument("check")
@click.argument("severity", type=click.Choice(["error", "warning", "ignore"]))
@click.option("--erc", is_flag=True, help="Schematic ERC check instead of DRC.")
@handle_error
def rules_severity(check, severity, erc):
    """Set the severity of a DRC (or --erc ERC) check, e.g. `silk_overlap ignore`."""
    output(proj_mod.severity_set(get_session(), check, severity, schematic=erc))


@rules_group.command("preset-add")
@click.argument("kind", type=click.Choice(["track", "via", "diff_pair"]))
@click.argument("values", nargs=-1, type=float, required=True)
@handle_error
def rules_preset_add(kind, values):
    """Add a predefined size: track W | via DIAMETER DRILL | diff_pair W GAP [VIA_GAP]."""
    output(proj_mod.presets_add(get_session(), kind, list(values)))


@rules_group.command("custom-show")
@handle_error
def rules_custom_show():
    """Show custom rules from <project>.kicad_dru."""
    output(proj_mod.dru_read(get_session()))


@rules_group.command("custom-add")
@click.argument("name")
@click.option("--constraint", "-c", "constraints", multiple=True, required=True,
              help="'(constraint clearance (min 0.3mm))' or shorthand 'clearance min 0.3mm'")
@click.option("--condition", default=None, help="e.g. \"A.NetClass == 'HV'\"")
@click.option("--layer", default=None, help="outer | inner | F.Cu ...")
@handle_error
def rules_custom_add(name, constraints, condition, layer):
    """Append a custom DRC rule to <project>.kicad_dru."""
    output(proj_mod.dru_add_rule(get_session(), name, list(constraints), condition, layer))


@rules_group.command("custom-remove")
@click.argument("name")
@handle_error
def rules_custom_remove(name):
    output(proj_mod.dru_remove_rule(get_session(), name))


@project_group.group("libtable")
def libtable_group():
    """Symbol / footprint library tables (project and global)."""


@libtable_group.command("list")
@click.argument("kind", type=click.Choice(["sym", "fp", "all"]), default="all")
@handle_error
def libtable_list(kind):
    t = proj_mod.lib_tables(get_session().project)
    if kind != "all":
        t = t[kind]
    output(t)


@libtable_group.command("add")
@click.argument("kind", type=click.Choice(["sym", "fp"]))
@click.argument("name")
@click.argument("uri")
@click.option("--scope", type=click.Choice(["project", "global"]), default="project")
@click.option("--descr", default="")
@click.option("--type", "lib_type", default="KiCad")
@handle_error
def libtable_add(kind, name, uri, scope, descr, lib_type):
    """Add a library (URI may use ${KIPRJMOD}, ${KICAD10_SYMBOL_DIR}, ...)."""
    output(proj_mod.libtable_add(get_session(), kind, name, uri, scope, descr, lib_type=lib_type))


@libtable_group.command("remove")
@click.argument("kind", type=click.Choice(["sym", "fp"]))
@click.argument("name")
@click.option("--scope", type=click.Choice(["project", "global"]), default="project")
@handle_error
def libtable_remove(kind, name, scope):
    output(proj_mod.libtable_remove(get_session(), kind, name, scope))


@project_group.command("archive")
@click.option("--output", "-o", "out", default=None, help="Zip path (default <project>/<name>-<timestamp>.zip)")
@click.option("--include-outputs", is_flag=True)
@handle_error
def project_archive(out, include_outputs):
    """Zip the project (like File > Archive Project)."""
    output(proj_mod.archive(_proj(), out, include_outputs), "Archived")


@project_group.command("unarchive")
@click.argument("archive", type=click.Path(exists=True))
@click.argument("directory", type=click.Path())
@handle_error
def project_unarchive(archive, directory):
    output(proj_mod.unarchive(archive, directory))


@project_group.command("compare")
@click.argument("before")
@click.argument("after", required=False)
@click.option("--geometry", is_flag=True, help="Include actual pad displacement for moved footprints; replacements always include it.")
@click.option("--allow", "allowed", default=None, help="Comma list: refs, net:NAME, outline, zones, rules, project:FILENAME.")
@click.option("--fail-on-unexpected", is_flag=True, help="Emit the report, then exit 4 if anything outside --allow changes.")
@handle_error
def project_compare(before, after, geometry, allowed, fail_on_unexpected):
    """Compare revisions: BEFORE/AFTER are project paths, .zip archives or git:REV (AFTER default: current)."""
    sess = get_session()
    if after is None:
        after = _proj().pro_path if os.path.exists(_proj().pro_path) else _proj().dir
    result = diff_mod.compare(before, after, sess.project, geometry=geometry,
                              allow=[a.strip() for a in (allowed or "").split(",") if a.strip()] if allowed is not None or fail_on_unexpected else None)
    output(result)
    if fail_on_unexpected and not result["ok"]:
        raise ValidationError("Unexpected ECO changes; inspect unexpected[] or explicitly extend --allow")


# ═════════════════════════════════════════════════════════════════════════════
# sch
# ═════════════════════════════════════════════════════════════════════════════

@cli.group("sch")
def sch_group():
    """Schematic: inspect, edit (symbols, wires, labels, sheets), annotate, ERC, exports."""


SHEET_OPT = click.option("--sheet", default=None, help="Sub-sheet file or sheet name (default: root).")


@sch_group.command("info")
@SHEET_OPT
@handle_error
def sch_info(sheet):
    """Counts, title block, lib_symbols cache and sheets of a schematic file."""
    path, t = _sch_tree(sheet)
    syms = sch_mod.list_symbols(t)
    output({"file": path, "version": t.value("version"), "uuid": t.value("uuid"), "paper": t.value("paper"),
            "title_block": {c.tag if c.tag != "comment" else f"comment{c.value()}": c.value(None, index=1 if c.tag == "comment" else 0)
                            for c in (t.find("title_block") or sexpr.S("t")).children()},
            "symbols": len([s for s in syms if not s["power"]]), "power_symbols": len([s for s in syms if s["power"]]),
            "lib_symbols": [str(s[1]) for s in sch_mod.lib_symbols(t).find_all("symbol")],
            "wires": len(t.find_all("wire")), "junctions": len(t.find_all("junction")),
            "labels": len(sch_mod.labels(t)), "no_connects": len(t.find_all("no_connect")),
            "sheets": [sch_mod.sheet_dict(s) for s in t.find_all("sheet")]})


@sch_group.command("symbols")
@SHEET_OPT
@click.option("--no-power", is_flag=True, help="Hide power symbols / #refs.")
@click.option("--match", default=None, help="Wildcard on reference/value/lib_id.")
@handle_error
def sch_symbols(sheet, no_power, match):
    """List placed symbols."""
    _, t = _sch_tree(sheet)
    output(sch_mod.list_symbols(t, include_power=not no_power, pattern=match))


@sch_group.command("show")
@click.argument("ref")
@SHEET_OPT
@handle_error
def sch_show(ref, sheet):
    """Full detail of a placed symbol incl. absolute pin positions."""
    _, t = _sch_tree(sheet)
    out = []
    for s in sch_mod.find_symbols(t, ref) or [sch_mod.find_symbol(t, ref)]:
        d = sch_mod.symbol_dict(s, t)
        d["pins"] = sch_mod.symbol_pins(t, s)
        d["instances"] = sch_mod.instance_references(s)
        out.append(d)
    output(out[0] if len(out) == 1 else out)


@sch_group.command("pins")
@click.argument("ref")
@SHEET_OPT
@handle_error
def sch_pins(ref, sheet):
    """Pins of a placed symbol: number, name, type, absolute x/y, outward direction."""
    _, t = _sch_tree(sheet)
    out = []
    for s in sch_mod.find_symbols(t, ref) or [sch_mod.find_symbol(t, ref)]:
        out += [{k: p[k] for k in ("number", "name", "type", "x", "y", "outward", "unit")} for p in
                sch_mod.symbol_pins(t, s)]
    output(out)


@sch_group.command("add")
@click.argument("lib_id")
@click.option("--ref", "-r", default=None, help="Reference (default: prefix + '?', annotate later).")
@click.option("--at", "at", required=True, help="x,y in mm (snapped to 1.27 grid).")
@click.option("--angle", type=click.Choice(["0", "90", "180", "270"]), default="0")
@click.option("--mirror", type=click.Choice(["x", "y"]), default=None)
@click.option("--unit", type=int, default=1)
@click.option("--value", "-v", default=None)
@click.option("--footprint", "-f", default=None, help="Library:Footprint")
@click.option("--field", "fields", multiple=True, help="NAME=VALUE (e.g. MPN=..., LCSC=C25804)")
@click.option("--dnp", is_flag=True)
@click.option("--no-snap", is_flag=True)
@click.option("--uuid", "uid", default=None,
              help="Caller-assigned symbol uuid (8-4-4-4-12); must not exist in the project.")
@ALLOW_MERGE_OPT
@SHEET_OPT
@handle_error
def sch_add(lib_id, ref, at, angle, mirror, unit, value, footprint, fields, dnp, no_snap, uid, allow_merge, sheet):
    """Place a symbol from a library (e.g. Device:R, power:GND, Timer:NE555P)."""
    sess = get_session()
    proj = _proj()
    if uid:
        uid = sch_mod.normalize_uuid(uid)
        if uid in sch_mod.project_uuids(sess.tree, proj.sch_path):
            raise ValueError(f"uuid {uid} already exists in this project; caller ids must be unique "
                             "(derive a fresh one with uuid5, or let kce assign one)")
    path = _sch_file(sheet)
    x, y = parse_xy(at)
    extra = parse_kv(fields)

    def mutate(t):
        sym = sch_mod.add_symbol(t, proj, lib_id, ref, x, y, angle=int(angle), mirror=mirror, unit=unit,
                                 value=value, footprint=footprint, fields=extra,
                                 instance_paths=_instance_paths(path) or None, project_name=proj.name,
                                 snap_grid=not no_snap, dnp=dnp, uuid=uid)
        d = sch_mod.symbol_dict(sym, t)
        d["pins"] = [{k: p[k] for k in ("number", "name", "x", "y")} for p in sch_mod.symbol_pins(t, sym)]
        return d

    # a power symbol names the net under it, so it can short two rails; other parts only add pins
    guard = not allow_merge and lib_id.split(":")[0] == "power"
    d = _guarded_sch_mutate(sheet, f"add symbol {lib_id}", mutate, allow_merge=not guard,
                            merge_mode="any", anchor_spec=at)
    output(d, f"Added {d['reference']} ({lib_id}) at {d['x']},{d['y']}")


@sch_group.command("delete")
@click.argument("ref")
@SHEET_OPT
@handle_error
def sch_delete(ref, sheet):
    """Delete a symbol (all its units)."""
    _, t = _sch_mutate(sheet, f"delete {ref}")
    output({"deleted": ref, "units": sch_mod.delete_symbol(t, ref)})


@sch_group.command("move")
@click.argument("ref")
@click.option("--at", default=None, help="Absolute x,y")
@click.option("--by", default=None, help="Relative dx,dy")
@click.option("--no-snap", is_flag=True)
@ALLOW_MERGE_OPT
@SHEET_OPT
@handle_error
def sch_move(ref, at, by, no_snap, allow_merge, sheet):
    """Move a symbol (its fields follow). Wires are not dragged."""
    x = y = None
    dx = dy = 0.0
    if at:
        x, y = parse_xy(at)
    if by:
        dx, dy = parse_xy(by)
    if not at and not by:
        raise ValueError("Give --at x,y or --by dx,dy")

    def mutate(t):
        nx, ny = sch_mod.move_symbol(sch_mod.find_symbol(t, ref), x, y, dx, dy, snap_grid=not no_snap)
        return {"reference": ref, "x": nx, "y": ny}

    output(_guarded_sch_mutate(sheet, f"move {ref}", mutate, allow_merge=allow_merge, merge_mode="none"))


@sch_group.command("rotate")
@click.argument("ref")
@click.option("--angle", type=click.Choice(["0", "90", "180", "270"]), default=None, help="Absolute angle")
@click.option("--by", type=int, default=90, help="Relative CCW degrees (multiple of 90)")
@ALLOW_MERGE_OPT
@SHEET_OPT
@handle_error
def sch_rotate(ref, angle, by, allow_merge, sheet):
    def mutate(t):
        a = sch_mod.rotate_symbol(sch_mod.find_symbol(t, ref), angle=float(angle) if angle else None, by=by)
        return {"reference": ref, "angle": a}

    output(_guarded_sch_mutate(sheet, f"rotate {ref}", mutate, allow_merge=allow_merge, merge_mode="none"))


@sch_group.command("mirror")
@click.argument("ref")
@click.argument("axis", type=click.Choice(["x", "y", "none"]))
@ALLOW_MERGE_OPT
@SHEET_OPT
@handle_error
def sch_mirror(ref, axis, allow_merge, sheet):
    def mutate(t):
        sch_mod.mirror_symbol(sch_mod.find_symbol(t, ref), None if axis == "none" else axis)
        return {"reference": ref, "mirror": axis}

    output(_guarded_sch_mutate(sheet, f"mirror {ref} {axis}", mutate, allow_merge=allow_merge,
                               merge_mode="none"))


@sch_group.command("set-field")
@click.argument("ref")
@click.argument("name")
@click.argument("value")
@click.option("--hide/--show", "hide", default=None)
@click.option("--at", default=None, help="Field position x,y")
@SHEET_OPT
@handle_error
def sch_set_field(ref, name, value, hide, at, sheet):
    """Set/add a field (Reference, Value, Footprint, Datasheet, MPN, ...). Applies to all units."""
    sch_mod.find_symbol(get_session().tree(_sch_file(sheet)), ref)
    _, t = _sch_mutate(sheet, f"{ref}.{name}={value}")
    x = y = None
    if at:
        x, y = parse_xy(at)
    for s in sch_mod.find_symbols(t, ref) or [sch_mod.find_symbol(t, ref)]:
        sch_mod.set_field(s, name, value, hide=hide, x=x, y=y)
    output({"reference": value if name == "Reference" else ref, "field": name, "value": value})


@sch_group.command("set-value")
@click.argument("ref")
@click.argument("value")
@SHEET_OPT
@handle_error
def sch_set_value(ref, value, sheet):
    """Set all units of REF; unknown references fail with input/2 before recording undo."""
    sch_mod.find_symbol(get_session().tree(_sch_file(sheet)), ref)
    _, t = _sch_mutate(sheet, f"{ref} value={value}")
    for s in sch_mod.find_symbols(t, ref):
        sch_mod.set_field(s, "Value", value)
    output({"reference": ref, "value": value})


@sch_group.command("set-footprint")
@click.argument("ref")
@click.argument("footprint")
@click.option("--no-check", is_flag=True, help="Do not verify the footprint exists in the libraries.")
@SHEET_OPT
@handle_error
def sch_set_footprint(ref, footprint, no_check, sheet):
    """Assign a footprint (Library:Name), verified against the fp-lib-table."""
    sch_mod.find_symbol(get_session().tree(_sch_file(sheet)), ref)
    if not no_check:
        lib_mod.resolve_footprint(_proj(), footprint)
    _, t = _sch_mutate(sheet, f"{ref} footprint={footprint}")
    for s in sch_mod.find_symbols(t, ref) or [sch_mod.find_symbol(t, ref)]:
        sch_mod.set_field(s, "Footprint", footprint)
    output({"reference": ref, "footprint": footprint})


@sch_group.command("remove-field")
@click.argument("ref")
@click.argument("name")
@SHEET_OPT
@handle_error
def sch_remove_field(ref, name, sheet):
    sch_mod.find_symbol(get_session().tree(_sch_file(sheet)), ref)
    _, t = _sch_mutate(sheet, f"{ref} remove field {name}")
    for s in sch_mod.find_symbols(t, ref):
        sch_mod.remove_field(s, name)
    output({"reference": ref, "removed_field": name})


@sch_group.command("set-attr")
@click.argument("ref")
@click.argument("attr", type=click.Choice(["in_bom", "on_board", "dnp", "exclude_from_sim", "in_pos_files"]))
@click.argument("value", type=click.Choice(["yes", "no"]))
@SHEET_OPT
@handle_error
def sch_set_attr(ref, attr, value, sheet):
    sch_mod.find_symbol(get_session().tree(_sch_file(sheet)), ref)
    _, t = _sch_mutate(sheet, f"{ref} {attr}={value}")
    for s in sch_mod.find_symbols(t, ref):
        sch_mod.set_attr(s, attr, value == "yes")
    output({"reference": ref, attr: value})


@sch_group.command("replace-symbol")
@click.argument("ref")
@click.argument("lib_id")
@click.option("--pin-map", help="Explicit old=new pin numbers, e.g. '1=3,2=1,3=2'")
@click.option("--map-by", type=click.Choice(["auto", "number", "name"]), default="number",
              help="Default number preserves PCB pin identities; auto tries unique names, then numbers")
@click.option("--rewire", is_flag=True, help="Carry wire endpoints, labels and NC flags to the mapped pins")
@click.option("--require-same-nets/--no-require-same-nets", default=True,
              help="Default: refuse a change to existing endpoint partitions (design/3)")
@SHEET_OPT
@handle_error
def sch_replace_symbol(ref, lib_id, pin_map, map_by, rewire, require_same_nets, sheet):
    """Replace a symbol while protecting its connections (also under --dry-run).

    Inspect details.connections_lost on NetSplitError. Use --map-by number --rewire to
    retain pin numbers, or --pin-map to translate a vendor's numbering. KiCad's actual
    netlist verifies the candidate before any file or undo entry is written.
    """
    path, tree = _sch_tree(sheet)
    sess = get_session()
    work = copy.deepcopy(tree)
    before = pcb_mod.schematic_netlist(sess)
    result = sch_mod.replace_lib_symbol(work, _proj(), ref, lib_id,
                                       pin_map=pin_map, map_by=map_by, rewire=rewire)
    doc = sess.doc(path)
    text, dirty = sess.current_text(path), doc.dirty
    doc.set_tree(work)
    try:
        after = pcb_mod.schematic_netlist(sess)
        def groups(nl):
            return {(n["ref"], str(n["pin"])): frozenset((p["ref"], str(p["pin"])) for p in net["nodes"])
                    for net in nl["nets"] for n in net["nodes"]}
        old_groups, new_groups = groups(before), groups(after)
        old_names, new_names = _pin_net_map(before), _pin_net_map(after)
        def nc_pins(nl):
            return {(n["ref"], str(n["pin"])) for net in nl["nets"] for n in net["nodes"]
                    if "no_connect" in (n.get("pintype") or "")}
        old_nc, new_nc = nc_pins(before), nc_pins(after)
        def mapped(key):
            if key[0] == ref:
                return (ref, result["pin_map"].get(key[1], "<missing>"))
            return key
        for key, group in old_groups.items():
            if key[0] == ref:
                expected = frozenset(mapped(p) for p in group)
                found = new_groups.get(mapped(key), frozenset())
                name = old_names[key]
                named_lost = not name.startswith(("Net-(", "unconnected-")) and name != new_names.get(mapped(key))
                nc_lost = key in old_nc and mapped(key) not in new_nc
                if expected != found or named_lost or nc_lost:
                    result["connections_lost"].append({"pin": f"{ref}:{key[1]}",
                        "expected": sorted(f"{r}:{p}" for r, p in expected),
                        "actual": sorted(f"{r}:{p}" for r, p in found)})
        merges = _merged_groups(before, after) if all(k == v for k, v in result["pin_map"].items()) else []
        if require_same_nets and (result["connections_lost"] or merges):
            raise sch_mod.NetSplitError(result)
    finally:
        doc.set_text(text)
        doc.dirty = dirty
    sess.snapshot(f"replace {ref} -> {lib_id}", [path])
    doc.set_tree(work)
    output(result)


@sch_group.command("update-symbols")
@click.option("--match", default=None, help="Only lib_ids matching this wildcard.")
@SHEET_OPT
@handle_error
def sch_update_symbols(match, sheet):
    """Refresh embedded symbols from the libraries (Update Symbols from Library), every sheet
    unless --sheet; reports changed symbols and pins that moved."""
    proj = _proj()
    if sheet:
        files = [_sch_file(sheet)]
    else:
        files = []
        for h in sch_mod.hierarchy(get_session().tree, proj.sch_path):
            if h["file"] not in files and (os.path.exists(h["file"]) or get_session().has_doc(h["file"])):
                files.append(h["file"])
    sess = get_session()
    sess.snapshot("update symbols from library", files)
    res = {"updated": [], "unchanged": [], "missing": [], "pins_changed": {}, "sheets": []}
    for f in files:
        r = sch_mod.update_lib_symbols(sess.tree(f), proj, match)
        if r["updated"]:
            sess.doc(f).mark_dirty()
        res["sheets"].append({"file": f, "updated": len(r["updated"])})
        for k in ("updated", "unchanged", "missing"):
            res[k] += [x for x in r[k] if x not in res[k]]
        res["pins_changed"].update(r["pins_changed"])
    if res["pins_changed"]:
        res["hint"] = "Pins moved or changed type; check the wiring with `kce check erc`."
    output(res)


@sch_group.command("wire")
@click.argument("points")
@click.option("--bus", is_flag=True, help="Draw a bus instead of a wire.")
@ALLOW_MERGE_OPT
@SHEET_OPT
@handle_error
def sch_wire(points, bus, allow_merge, sheet):
    """Draw a polyline 'x1,y1;x2,y2;...' (points may also be REF:PIN)."""
    tree = get_session().tree(_sch_file(sheet))
    pts = [sch_mod.resolve_point(tree, p) for p in points.split(";") if p.strip()]

    def mutate(t):
        if not bus and not allow_merge:
            problems = sch_mod.path_conflicts(t, pts, [pts[0], pts[-1]], [pts[0], pts[-1]])
            if problems:
                raise sch_mod.RouteBlockedError(
                    "This wire would " + "; ".join(problems) + ". Kicad would join those nets silently; "
                    "the wire was not drawn. Draw it elsewhere or pass --allow-merge.")
        ws = sch_mod.add_wire(t, pts, bus=bus)
        j = sch_mod.auto_junctions(t) if not bus else 0
        return {"segments": [w.value("uuid") for w in ws], "points": pts, "junctions_added": j}

    output(_guarded_sch_mutate(sheet, "wire", mutate, allow_merge=allow_merge,
                               merge_mode="pairs" if len(pts) >= 2 else "none",
                               requested_pairs=[tuple(pts[0]), tuple(pts[-1])]))


@sch_group.command("connect")
@click.argument("a")
@click.argument("b")
@click.option("--mode", type=click.Choice(["hv", "vh", "direct"]), default="hv",
              help="Orthogonal routing: horizontal-first, vertical-first, or straight.")
@click.option("--via", "via", default=None, help="Intermediate points 'x,y;x,y'")
@ALLOW_MERGE_OPT
@SHEET_OPT
@handle_error
def sch_connect(a, b, mode, via, allow_merge, sheet):
    """Wire two pins/points: `kce sch connect R1:2 U1:THR` (adds junctions where needed)."""
    vias = [parse_xy(p) for p in via.split(";")] if via else None

    def mutate(t):
        return sch_mod.connect_pins(t, a, b, mode, vias, allow_merge=allow_merge)

    output(_guarded_sch_mutate(sheet, f"connect {a} {b}", mutate, allow_merge=allow_merge,
                               merge_mode="pairs", requested_pairs=[a, b]))


@sch_group.command("label-pin")
@click.argument("pin")
@click.argument("net")
@click.option("--kind", type=click.Choice(["local", "global", "hierarchical"]), default="local")
@click.option("--shape", type=click.Choice(sch_mod.LABEL_SHAPES), default="bidirectional")
@click.option("--stub", type=float, default=2.54, help="Wire stub length (mm, 0 = label on the pin).")
@ALLOW_MERGE_OPT
@SHEET_OPT
@handle_error
def sch_label_pin(pin, net, kind, shape, stub, allow_merge, sheet):
    """Connect REF:PIN to NET via a short wire + net label (the fastest way to wire a design)."""
    ref, p = sch_mod.parse_pin_ref(pin)

    def mutate(t):
        return sch_mod.label_pin(t, ref, p, net, kind=kind, stub=stub, shape=shape)

    output(_guarded_sch_mutate(sheet, f"label {pin} {net}", mutate, allow_merge=allow_merge,
                               merge_mode="any", anchor_spec=pin))


@sch_group.command("label")
@click.argument("text")
@click.option("--at", required=True)
@click.option("--angle", type=click.Choice(["0", "90", "180", "270"]), default="0")
@click.option("--kind", type=click.Choice(list(sch_mod.LABEL_KINDS)), default="local")
@click.option("--shape", type=click.Choice(sch_mod.LABEL_SHAPES), default="bidirectional")
@ALLOW_MERGE_OPT
@SHEET_OPT
@handle_error
def sch_label(text, at, angle, kind, shape, allow_merge, sheet):
    """Place a local/global/hierarchical label at x,y (or REF:PIN)."""
    x, y = sch_mod.resolve_point(get_session().tree(_sch_file(sheet)), at)

    def mutate(t):
        lbl = sch_mod.add_label(t, text, x, y, int(angle), kind, shape)
        return {"label": text, "kind": kind, "at": [x, y], "uuid": lbl.value("uuid")}

    output(_guarded_sch_mutate(sheet, f"label {text}", mutate, allow_merge=allow_merge,
                               merge_mode="any", anchor_spec=at))


@sch_group.command("power")
@click.argument("net")
@click.option("--at", required=True, help="x,y or REF:PIN (symbol is placed at the pin)")
@click.option("--lib-id", default=None, help="Power symbol (default power:<NET>, e.g. power:GND, power:+3V3)")
@click.option("--angle", type=click.Choice(["0", "90", "180", "270"]), default="0")
@ALLOW_MERGE_OPT
@SHEET_OPT
@handle_error
def sch_power(net, at, lib_id, angle, allow_merge, sheet):
    """Place a power symbol (power:GND, power:+5V, ...) — directly on a pin if --at REF:PIN."""
    path = _sch_file(sheet)
    tree = get_session().tree(path)
    x, y = sch_mod.resolve_point(tree, at)
    lid = lib_id or (net if ":" in net else f"power:{net}")

    def mutate(t):
        sym = sch_mod.add_symbol(t, _proj(), lid, None, x, y, angle=int(angle),
                                 instance_paths=_instance_paths(path) or None, project_name=_proj().name,
                                 snap_grid=False)
        pins = sch_mod.symbol_pins(t, sym)
        if pins and (abs(pins[0]["x"] - x) > 1e-6 or abs(pins[0]["y"] - y) > 1e-6):
            sch_mod.move_symbol(sym, dx=x - pins[0]["x"], dy=y - pins[0]["y"], snap_grid=False)
        d = sch_mod.symbol_dict(sym, t)
        return d

    d = _guarded_sch_mutate(sheet, f"power {net}", mutate, allow_merge=allow_merge,
                            merge_mode="any", anchor_spec=at)
    output(d, f"Placed {lid} at {x},{y}")


@sch_group.command("junction")
@click.option("--at", required=True)
@ALLOW_MERGE_OPT
@SHEET_OPT
@handle_error
def sch_junction(at, allow_merge, sheet):
    """Place a junction at x,y (or REF:PIN). Refused when it would join two crossing nets."""
    x, y = sch_mod.resolve_point(get_session().tree(_sch_file(sheet)), at)

    def mutate(t):
        return {"junction": sch_mod.add_junction(t, x, y).value("uuid"), "at": [x, y]}

    output(_guarded_sch_mutate(sheet, "junction", mutate, allow_merge=allow_merge, merge_mode="none"))


@sch_group.command("auto-junctions")
@SHEET_OPT
@handle_error
def sch_auto_junctions(sheet):
    """Add missing junctions where 3+ wire ends/pins meet."""
    _, t = _sch_mutate(sheet, "auto junctions")
    output({"junctions_added": sch_mod.auto_junctions(t)})


@sch_group.command("no-connect")
@click.argument("target", required=False)
@click.option("--all-unconnected", is_flag=True,
              help="Flag every pin with nothing attached (KiCad's pin_not_connected).")
@click.option("--ref", help="With --all-unconnected: only pins of this reference (e.g. J1).")
@SHEET_OPT
@handle_error
def sch_no_connect(target, all_unconnected, ref, sheet):
    """Place a no-connect flag on REF:PIN (or x,y).

    --all-unconnected flags every pin that has no wire, label, other pin or no-connect flag
    on it, and reports the list it flagged. Use --ref to limit it to one symbol (e.g. J1)
    and --dry-run to see the list before writing. Each ERC pin_not_connected violation
    carries the exact command in violations[].fix.
    """
    if all_unconnected:
        if target:
            raise ValueError("Give REF:PIN or --all-unconnected, not both")
        _, t = _sch_mutate(sheet, "no-connect all unconnected")
        pins = sch_mod.unconnected_pins(t, ref)
        added = [sch_mod.no_connect_pin(t, p["ref"], p["pin"]) for p in pins]
        output({"added": added, "count": len(added), "dry_run": _dry_run})
        return
    if not target:
        raise ValueError("Give REF:PIN, x,y, or --all-unconnected")
    if ref:
        raise ValueError("--ref only applies with --all-unconnected")
    _, t = _sch_mutate(sheet, f"no-connect {target}")
    if ":" in target and not target.replace(".", "").replace(",", "").replace("-", "").isdigit():
        ref_, pin = sch_mod.parse_pin_ref(target)
        output(sch_mod.no_connect_pin(t, ref_, pin))
    else:
        x, y = parse_xy(target)
        output({"uuid": sch_mod.add_no_connect(t, x, y).value("uuid"), "at": [x, y]})


@sch_group.command("text")
@click.argument("text")
@click.option("--at", required=True)
@click.option("--angle", type=click.Choice(["0", "90", "180", "270"]), default="0")
@click.option("--size", type=float, default=1.27)
@SHEET_OPT
@handle_error
def sch_text(text, at, angle, size, sheet):
    _, t = _sch_mutate(sheet, "text")
    x, y = parse_xy(at)
    output({"uuid": sch_mod.add_text(t, text, x, y, int(angle), size).value("uuid")})


@sch_group.command("bus-entry")
@click.option("--at", required=True)
@click.option("--size", default="2.54,2.54", help="dx,dy")
@SHEET_OPT
@handle_error
def sch_bus_entry(at, size, sheet):
    _, t = _sch_mutate(sheet, "bus entry")
    x, y = parse_xy(at)
    dx, dy = parse_xy(size)
    output({"uuid": sch_mod.add_bus_entry(t, x, y, dx, dy).value("uuid")})


@sch_group.command("items")
@click.option("--type", "kinds", multiple=True, help="wire, junction, label, global_label, no_connect, sheet, ...")
@SHEET_OPT
@handle_error
def sch_items(kinds, sheet):
    """List non-symbol items (wires, labels, junctions, sheets, ...)."""
    _, t = _sch_tree(sheet)
    output(sch_mod.list_items(t, list(kinds) or None))


@sch_group.command("at")
@click.argument("point")
@SHEET_OPT
@handle_error
def sch_at(point, sheet):
    """What is at x,y (or REF:PIN)? Wires, pins, labels, junctions."""
    _, t = _sch_tree(sheet)
    x, y = sch_mod.resolve_point(t, point)
    output(sch_mod.items_at(t, x, y))


@sch_group.command("delete-item")
@click.argument("uuid")
@SHEET_OPT
@handle_error
def sch_delete_item(uuid, sheet):
    """Delete any item by uuid (see `kce sch items`)."""
    _, t = _sch_mutate(sheet, f"delete item {uuid}")
    output({"deleted": uuid, "type": sch_mod.delete_item(t, uuid)})


@sch_group.command("annotate")
@click.option("--mode", type=click.Choice(["unannotated", "all"]), default="unannotated")
@click.option("--order", type=click.Choice(["x", "y"]), default="x")
@click.option("--start", type=int, default=1)
@click.option("--sheet-offset", type=int, default=0, help="e.g. 100: sheet N numbers from N*100+1")
@click.option("--clear", is_flag=True, help="Reset all references to 'X?' instead.")
@handle_error
def sch_annotate(mode, order, start, sheet_offset, clear):
    """Annotate references across the whole hierarchy (kicad-cli has no annotate)."""
    sess = get_session()
    proj = _proj()
    hier = sch_mod.hierarchy(sess.tree, proj.sch_path)
    files = _unique_paths(h["file"] for h in hier if os.path.exists(h["file"]) or sess.has_doc(h["file"]))
    sess.snapshot("annotate" if not clear else "clear annotation", files)
    trees = {f: sess.tree(f) for f in files}
    for f in files:
        sess.doc(f).mark_dirty()
    if clear:
        output({"cleared": sch_mod.clear_annotation(trees)})
        return
    changes = sch_mod.annotate(trees, hier, mode=mode, order=order, start=start, sheet_offset=sheet_offset)
    output({"changes": changes, "count": len(changes)}, f"Annotated {len(changes)} symbol(s)")


@sch_group.command("hierarchy")
@handle_error
def sch_hierarchy():
    """Sheet hierarchy (one entry per sheet instance)."""
    output(sch_mod.hierarchy(get_session().tree, _proj().sch_path))


@sch_group.command("add-sheet")
@click.argument("name")
@click.option("--file", "filename", default=None, help="Child file (default <name>.kicad_sch)")
@click.option("--at", default="25.4,25.4")
@click.option("--size", default="30.48,20.32", help="w,h mm")
@SHEET_OPT
@handle_error
def sch_add_sheet(name, filename, at, size, sheet):
    """Add a hierarchical sheet (creates the child schematic if missing)."""
    sess = get_session()
    proj = _proj()
    parent_path, t = _sch_mutate(sheet, f"add sheet {name}")
    filename = filename or (name.replace(" ", "_") + ".kicad_sch")
    child = os.path.join(os.path.dirname(parent_path), filename)
    parent_paths = _instance_paths(parent_path) or [f"/{sch_mod.root_uuid(t)}"]
    x, y = parse_xy(at)
    w, h = parse_xy(size)
    page = sch_mod.next_page_number(sess.tree, proj.sch_path)
    sh = sch_mod.add_sheet(t, name, filename, x, y, w, h, proj.name, parent_paths[0], page)
    created = False
    if not os.path.exists(child) and not sess.has_doc(child):
        sess.snapshot(f"create {filename}", [child])
        sess.doc(child).set_tree(proj_mod.empty_schematic_tree())
        created = True
    output({"sheet": sch_mod.sheet_dict(sh), "file": child, "created_file": created, "page": page})


@sch_group.command("sheet-pin")
@click.argument("sheet_name")
@click.argument("pin_name")
@click.option("--shape", type=click.Choice(sch_mod.LABEL_SHAPES), default="bidirectional")
@click.option("--side", type=click.Choice(["left", "right", "top", "bottom"]), default="right")
@click.option("--no-label", is_flag=True, help="Do not create the matching hierarchical label in the child.")
@SHEET_OPT
@handle_error
def sch_sheet_pin(sheet_name, pin_name, shape, side, no_label, sheet):
    """Add a sheet pin and the matching hierarchical label inside the child sheet."""
    sess = get_session()
    parent, t = _sch_mutate(sheet, f"sheet pin {sheet_name}.{pin_name}")
    sh = sch_mod.find_sheet(t, sheet_name)
    pin = sch_mod.add_sheet_pin(sh, pin_name, shape, side)
    res = {"sheet": sheet_name, "pin": pin_name, "at": [float(pin.find("at")[1]), float(pin.find("at")[2])]}
    if not no_label:
        child = os.path.join(os.path.dirname(parent), sch_mod.sheet_dict(sh)["file"])
        sess.snapshot(f"hier label {pin_name}", [child])
        ct = sess.tree(child)
        n = len(ct.find_all("hierarchical_label"))
        sch_mod.add_label(ct, pin_name, 25.4, 25.4 + 2.54 * n, 180, "hierarchical", shape)
        sess.doc(child).mark_dirty()
        res["child_label"] = {"file": child, "at": [25.4, 25.4 + 2.54 * n]}
    output(res)


@sch_group.command("title")
@click.option("--title", default=None)
@click.option("--date", default=None)
@click.option("--rev", default=None)
@click.option("--company", default=None)
@click.option("--comment", "comments", multiple=True, help="N=text (1..9)")
@SHEET_OPT
@handle_error
def sch_title(title, date, rev, company, comments, sheet):
    """Set title block fields."""
    _, t = _sch_mutate(sheet, "title block")
    vals = {k: v for k, v in (("title", title), ("date", date), ("rev", rev), ("company", company)) if v is not None}
    cm = {int(k): v for k, v in parse_kv(comments).items()}
    tb = sch_mod.set_title_block(t, vals, cm)
    output({c.tag if c.tag != "comment" else f"comment{c.value()}": c.value(None, index=1 if c.tag == "comment" else 0)
            for c in tb.children()})


@sch_group.command("paper")
@click.argument("size")
@click.option("--portrait", is_flag=True)
@SHEET_OPT
@handle_error
def sch_paper(size, portrait, sheet):
    """Page size: A4, A3, A2, A1, A0, A, B, C, D, E, USLetter, USLegal, USLedger, User."""
    _, t = _sch_mutate(sheet, f"paper {size}")
    sch_mod.set_paper(t, size, portrait)
    output({"paper": size, "portrait": portrait})


@sch_group.command("nets")
@click.option("--net", default=None, help="Only this net")
@handle_error
def sch_nets(net):
    """Connectivity from KiCad's own netlister, in a stable machine format.

    Nets are sorted by name; pins by reference then number in natural order (R2 before R10).
    Each net carries its KiCad sheet path and, per pin, the electrical type.
    """
    nl = pcb_mod.schematic_netlist(get_session())
    nets = [n for n in nl_mod.contract_nets(nl) if net is None or n["name"] == net]
    output({"components": len(nl["components"]), "net_count": len(nl["nets"]), "nets": nets})


@sch_group.command("verify")
@click.option("--expect", "expect", required=True, type=click.Path(),
              help="Intent file to compare against (see schemas/netlist-intent.schema.json).")
@click.option("--strict", is_flag=True,
              help="Exit with the design category (3) when the schematic does not match.")
@click.option("--subset", is_flag=True,
              help="Compare partitions on listed pins only; allow other pins on those nets and ignore extras.")
@click.option("--check-lib-id", is_flag=True, help="Require each intended symbol library id to match")
@click.option("--check-uuid", is_flag=True, help="Require supplied component UUIDs to match KiCad identities")
@click.option("--check-net-names", is_flag=True, help="Treat renamed nets as mismatches (names must match exactly)")
@handle_error
def sch_verify(expect, strict, subset, check_lib_id, check_uuid, check_net_names):
    """Compare KiCad's netlist with an intent file: match, mismatches, and why.

    Nets are matched by their pin sets, so KiCad's auto-generated net names are reported as
    `renamed` rather than as errors. No-connect flags and PWR_FLAGs listed in the intent are
    read back from the netlist (`no_connects_missing`, `power_flags_missing`). With --subset
    only the listed components and nets are checked, so a ten-line ECO intent verifies against
    a large board without every other part showing up as `extra_components`. Without --strict a
    mismatch still exits 0.
    """
    intent = intent_mod.parse_intent(expect)
    sess = get_session()
    result = intent_mod.verify_intent(intent, sess, _proj(),
                                      pcb_mod.schematic_netlist(sess), subset=subset, check_lib_id=check_lib_id,
                                      check_uuid=check_uuid, check_net_names=check_net_names)
    if strict and not result["match"]:
        raise intent_mod.mismatch_error(result, expect)
    output(result, "match" if result["match"] else "no match")


@sch_group.command("apply")
@click.argument("intent_file", type=click.Path())
@click.option("--power-flags", "power_flags", is_flag=True,
              help="Add a PWR_FLAG on nets that need one (power inputs, no power output).")
@click.option("--prune", is_flag=True,
              help="Remove symbols and labels the intent does not mention (otherwise they stay listed).")
@click.option("--patch", is_flag=True,
              help="Add or modify only: skip `remove` and never prune (an ECO edit).")
@handle_error
def sch_apply(intent_file, power_flags, prune, patch):
    """Make the schematic match an intent file: components, fields, nets, flags.

    Missing symbols are placed (at `at`, else on a deterministic free grid), footprint/value/
    fields/uuid/attributes are set, every net is joined with labels (`label-pin`), `no_connect`
    pins get a no-connect flag, `power_flags` get a PWR_FLAG on that pin, and `remove`
    references are deleted. Applying the same file twice changes nothing: the result reports
    `changes: 0`. With --patch nothing is deleted (`remove` is skipped, `--prune` is refused),
    which is the right call for an ECO. The result ends with the `sch verify` comparison, so one
    command both edits and reports.
    """
    if patch and prune:
        raise ValueError("--patch never deletes, so it conflicts with --prune; drop --prune")
    intent = intent_mod.parse_intent(intent_file)
    sess = get_session()
    result = intent_mod.apply_intent(intent, sess, _proj(), power_flags=power_flags,
                                     prune=prune, patch=patch)
    result.update(intent_mod.verify_intent(intent, sess, _proj(),
                                           pcb_mod.schematic_netlist(sess)))
    output(result, f"Applied {intent_file}: match" if result["match"]
           else f"Applied {intent_file}: the schematic still differs (see verify)")


@sch_group.command("net-of")
@click.argument("pin")
@handle_error
def sch_net_of(pin):
    """Which net is REF:PIN on (per KiCad's netlister)?"""
    nl = pcb_mod.schematic_netlist(get_session())
    ref, p = sch_mod.parse_pin_ref(pin)
    for n in nl["nets"]:
        for nd in n["nodes"]:
            if nd["ref"] == ref and (nd["pin"] == p or nd["pinfunction"] == p):
                output({"pin": pin, "net": n["name"], "nodes": [f"{x['ref']}:{x['pin']}" for x in n["nodes"]]})
                return
    raise KeyError(f"{pin} not found in netlist (unannotated or no such pin?)")


@sch_group.command("components")
@handle_error
def sch_components():
    """Components as KiCad's netlister sees them (whole hierarchy)."""
    nl = pcb_mod.schematic_netlist(get_session())
    output(nl["components"])


@sch_group.command("normalize")
@handle_error
def sch_normalize():
    """Let KiCad re-save the schematic hierarchy in canonical form (kicad-cli sch upgrade --force)."""
    sess = get_session()
    proj = _proj()
    if sess.dirty_docs():
        save_now()
    hier = sch_mod.hierarchy(sess.tree, proj.sch_path)
    files = _unique_paths(h["file"] for h in hier if os.path.exists(h["file"]))
    sess.snapshot("normalize schematic", files)
    kb.run_kicad_cli(["sch", "upgrade", "--force", proj.sch_path])
    for f in files:
        sess.doc(f).set_text(open(f, encoding="utf-8").read())
    output({"normalized": files})


# ═════════════════════════════════════════════════════════════════════════════
# pcb
# ═════════════════════════════════════════════════════════════════════════════

@cli.group("pcb")
def pcb_group():
    """Board: inspect, place, route, zones, outline, update from schematic, DRC, exports, 3D render."""


@pcb_group.command("new")
@click.option("--layers", "copper", type=int, default=2, help="Copper layer count")
@click.option("--overwrite", is_flag=True)
@handle_error
def pcb_new(copper, overwrite):
    """Create an empty board for the project (via pcbnew)."""
    output(pcb_mod.new_board(get_session(), copper, overwrite))


@pcb_group.command("info")
@handle_error
def pcb_info():
    """Board summary from pcbnew: layers, counts, outline, unrouted connections."""
    output(pcb_mod.read(get_session(), "info"))


@pcb_group.command("footprints")
@click.option("--pads", is_flag=True, help="Include pads")
@click.option("--match", default=None, help="Wildcard on reference/value")
@handle_error
def pcb_footprints(pads, match):
    import fnmatch
    res = pcb_mod.read(get_session(), "footprints", pads=pads)
    if match:
        res = [f for f in res if fnmatch.fnmatchcase(f["reference"], match) or fnmatch.fnmatchcase(f["value"], match)]
    output(res)


@pcb_group.command("footprint")
@click.argument("ref")
@handle_error
def pcb_footprint(ref):
    """One footprint with pads (absolute positions and nets) and fields."""
    output(pcb_mod.read(get_session(), "footprint", ref=ref))


@pcb_group.command("nets")
@handle_error
def pcb_nets():
    output(pcb_mod.read(get_session(), "nets"))


@pcb_group.command("tracks")
@click.option("--net", default=None)
@handle_error
def pcb_tracks(net):
    output(pcb_mod.read(get_session(), "tracks", net=net))


@pcb_group.command("zones")
@handle_error
def pcb_zones():
    output(pcb_mod.read(get_session(), "zones"))


@pcb_group.command("drawings")
@click.option("--layer", default=None)
@handle_error
def pcb_drawings(layer):
    output(pcb_mod.read(get_session(), "drawings", layer=layer))


@pcb_group.command("layers")
@handle_error
def pcb_layers():
    output(pcb_mod.read(get_session(), "layers"))


@pcb_group.command("stackup")
@handle_error
def pcb_stackup():
    """Physical stackup (from the board file) and layer table."""
    output(pcb_mod.stackup(get_session()))


@pcb_group.command("connectivity")
@handle_error
def pcb_connectivity():
    """Unrouted connection count and incomplete nets (pcbnew connectivity)."""
    output(pcb_mod.read(get_session(), "connectivity"))


@pcb_group.command("ratsnest")
@click.option("--net", default=None)
@handle_error
def pcb_ratsnest(net):
    """Pad pairs still to route (minimum spanning connections)."""
    output(pcb_mod.read(get_session(), "ratsnest", net=net))


@pcb_group.command("update")
@click.option("--keep-extra", is_flag=True, help="Do not delete footprints no longer in the schematic.")
@click.option("--no-replace", is_flag=True, help="Do not swap footprints whose library id changed.")
@click.option("--no-place", is_flag=True, help="Do not auto-arrange newly added footprints.")
@click.option("--stable-uuids", is_flag=True,
              help="Give every footprint a uuid5(namespace, symbol path), identical on every run.")
@click.option("--lock-existing", is_flag=True, help="Reject movement or pad displacement of any existing footprint.")
@handle_error
def pcb_update(keep_extra, no_replace, no_place, stable_uuids, lock_existing):
    """Sync footprints and nets, reporting adoption, displaced pads and missing pin/pad matches.

    For imported boards first use --dry-run pcb update --keep-extra. Review adopted,
    replaced[].pads_moved and unmatched_pins before applying; check DRC after sync.
    """
    r = pcb_mod.update_from_schematic(get_session(), delete_extra=not keep_extra,
                                      replace_footprints=not no_replace, place=not no_place,
                                      stable_uuids=stable_uuids, lock_existing=lock_existing)
    output(r, f"Added {len(r['added'])}, removed {len(r['removed'])}, updated {len(r['updated'])}, "
              f"nets assigned {r['nets_assigned']}, unrouted {r['unrouted']}")


@pcb_group.command("add-footprint")
@click.argument("fpid")
@click.option("--ref", "-r", required=True)
@click.option("--at", required=True, help="x,y mm")
@click.option("--rotation", type=float, default=0)
@click.option("--side", type=click.Choice(["top", "bottom"]), default="top")
@click.option("--value", default=None)
@handle_error
def pcb_add_footprint(fpid, ref, at, rotation, side, value):
    """Place a library footprint directly on the board (board-only parts: holes, logos, test points)."""
    x, y = parse_xy(at)
    output(pcb_mod.add_footprint(get_session(), fpid, ref, x, y, rotation, side, value))


@pcb_group.command("move")
@click.argument("ref")
@click.option("--at", default=None)
@click.option("--by", default=None)
@click.option("--rotation", type=float, default=None)
@click.option("--force", is_flag=True, help="Move even if locked")
@handle_error
def pcb_move(ref, at, by, rotation, force):
    """Move a footprint (absolute --at x,y or relative --by dx,dy), optionally set rotation."""
    a = {"action": "move_fp", "ref": ref, "force": force, "rotation": rotation}
    if at:
        a["x"], a["y"] = parse_xy(at)
    if by:
        a["dx"], a["dy"] = parse_xy(by)
    output(pcb_mod.edit1(get_session(), a, f"move {ref}"))


@pcb_group.command("rotate")
@click.argument("ref")
@click.option("--angle", type=float, default=None, help="Absolute degrees")
@click.option("--by", type=float, default=90, help="Relative degrees (CCW)")
@handle_error
def pcb_rotate(ref, angle, by):
    output(pcb_mod.edit1(get_session(), {"action": "rotate_fp", "ref": ref, "angle": angle, "by": by}, f"rotate {ref}"))


@pcb_group.command("flip")
@click.argument("ref")
@click.option("--side", type=click.Choice(["top", "bottom"]), default=None, help="Target side (default toggle)")
@handle_error
def pcb_flip(ref, side):
    output(pcb_mod.edit1(get_session(), {"action": "flip_fp", "ref": ref, "side": side}, f"flip {ref}"))


@pcb_group.command("lock")
@click.argument("refs", nargs=-1)
@click.option("--unlock", is_flag=True)
@click.option("--all", "all_parts", is_flag=True, help="Select every footprint.")
@click.option("--except", "except_refs", default=None, help="Comma-separated refs excluded from --all.")
@handle_error
def pcb_lock(refs, unlock, all_parts, except_refs):
    if all_parts:
        if refs:
            raise ValueError("Use refs or --all, not both")
        excluded = set((except_refs or "").split(",")) - {""}
        known = {f["reference"] for f in pcb_mod.read(get_session(), "footprints")}
        if excluded - known:
            raise ValueError("Unknown --except references: " + ", ".join(sorted(excluded - known)))
        refs = sorted(known - excluded)
    elif except_refs or not refs:
        raise ValueError("Give references or use --all [--except REFS]")
    output(pcb_mod.edit1(get_session(), {"action": "lock_fp", "refs": list(refs), "locked": not unlock},
                         ("unlock " if unlock else "lock ") + " ".join(refs)))


@pcb_group.command("set-field")
@click.argument("ref")
@click.argument("name")
@click.argument("value")
@click.option("--visible/--hidden", "visible", default=None)
@handle_error
def pcb_set_field(ref, name, value, visible):
    """Set a footprint field (Reference/Value/custom). Prefer editing the schematic + `kce pcb update`."""
    output(pcb_mod.edit1(get_session(), {"action": "set_fp_field", "ref": ref, "field": name, "value": value,
                                         "visible": visible}, f"{ref}.{name}"))


@pcb_group.command("set-attr")
@click.argument("ref")
@click.argument("attr", type=click.Choice(["dnp", "exclude_from_bom", "exclude_from_pos", "smd", "through_hole"]))
@click.argument("value", type=click.Choice(["yes", "no"]))
@handle_error
def pcb_set_attr(ref, attr, value):
    output(pcb_mod.edit1(get_session(), {"action": "set_fp_attr", "ref": ref, "attr": attr, "value": value == "yes"},
                         f"{ref} {attr}"))


@pcb_group.command("delete-footprint")
@click.argument("ref")
@handle_error
def pcb_delete_footprint(ref):
    output(pcb_mod.edit1(get_session(), {"action": "delete_fp", "ref": ref}, f"delete {ref}"))


@pcb_group.command("score")
@click.option("--net", default=None, help="Only this net (HPWL, ratsnest, congestion)")
@click.option("--bin-size", type=float, default=5.0, help="Congestion bin edge (mm)")
@handle_error
def pcb_score(net, bin_size):
    """Measure placement: per-net HPWL, courtyard overlaps, out-of-outline parts, ratsnest crossings,
    congestion. Schema: schemas/pcb-score.schema.json. Congestion density is sampled
    ratsnest + track length / bin area (mm/mm²). A closed outline is required (exit 3)."""
    result = pcb_mod.score(get_session(), net=net, bin_size=bin_size)
    output(result)
    if not result["outline"]["closed"]:
        raise RuntimeError("PCB score failed: " + result["error"])


@pcb_group.command("legalize")
@click.option("--clearance", type=float, default=0.2, help="Gap between courtyards (mm)")
@click.option("--grid", type=float, default=0.5, help="Candidate position step (mm)")
@click.option("--max-dist", type=float, default=10.0, help="Furthest a footprint may move (mm)")
@click.option("--strict", is_flag=True, help="Exit design/3 and save nothing if any footprint remains illegal.")
@click.option("--refs", default=None, help="Move only these comma-separated refs; all others stay obstacles.")
@click.option("--max-move", type=float, default=None, help="Alias for --max-dist.")
@handle_error
def pcb_legalize(clearance, grid, max_dist, refs, max_move, strict):
    """Move overlapping and out-of-outline footprints to the nearest legal spot (locked ones stay)."""
    output(pcb_mod.legalize(get_session(), clearance=clearance, grid=grid, max_distance=max_dist if max_move is None else max_move,
                            refs=refs.split(",") if refs else None, strict=strict))


@pcb_group.group("place")
def place_group():
    """Placement helpers."""


@place_group.command("grid")
@click.option("--refs", default=None, help="Comma list (default: all unlocked footprints)")
@click.option("--area", default=None, help="x,y,w,h (default: inside board outline)")
@click.option("--spacing", type=float, default=1.0, help="Gap between courtyards (mm)")
@click.option("--margin", type=float, default=2.0, help="Distance from outline (mm)")
@click.option("--order", type=click.Choice(["ref", "size"]), default="ref")
@click.option("--only-unplaced", is_flag=True, help="Only footprints outside the outline")
@handle_error
def place_grid(refs, area, spacing, margin, order, only_unplaced):
    """Pack footprints inside the outline without overlapping standing ones (initial placement).

    Footprints not selected never move. Ones that fit nowhere go next to the board and are listed
    under ``unplaced_inside_outline``."""
    a = {"action": "place_grid", "spacing": spacing, "margin": margin, "order": order, "only_unplaced": only_unplaced}
    if refs:
        a["refs"] = refs.split(",")
    if area:
        a["area"] = [float(v) for v in area.split(",")]
    output(pcb_mod.edit1(get_session(), a, "place grid"))


@place_group.command("near")
@click.argument("ref")
@click.option("--to", "target", required=True, help="Target footprint REF or pad REF:NUMBER/NAME")
@click.option("--max-dist", type=float, default=10.0, help="Search radius around the target (mm)")
@click.option("--side", type=click.Choice(["front", "back"]), default=None, help="Copper side of the part")
@click.option("--clearance", type=float, default=0.2, help="Gap between courtyards (mm)")
@click.option("--grid", type=float, default=0.5, help="Candidate position step (mm)")
@click.option("--net", default=None, help="Anchor to a target pad on this net.")
@click.option("--pad", default=None, help="Target pad number (with --to REF).")
@click.option("--rotation", default=None, help="Explicit degrees, or auto to try the current orientation and quarter turns.")
@handle_error
def place_near(ref, target, max_dist, side, clearance, grid, net, pad, rotation):
    """Put REF at the nearest legal spot to a footprint or pad (same rules as `pcb legalize`).

    The target is a footprint reference (its courtyard centre) or a pad ``REF:PIN`` (number or name).
    The moving footprint's courtyard centre is searched around the target. Every spot
    within ``--max-dist`` on ``--grid`` is tried nearest first; the first one that overlaps no
    other courtyard (plus ``--clearance``) and stays inside the outline wins."""
    output(pcb_mod.place_near(get_session(), ref, target, max_distance=max_dist, side=side,
                              clearance=clearance, grid=grid, net=net, pad=pad, rotation=rotation),
           f"Placed {ref} near {target}")


@pcb_group.group("outline")
def outline_group():
    """Board outline on Edge.Cuts."""


@outline_group.command("rect")
@click.option("--at", default="0,0", help="Top-left x,y")
@click.option("--size", required=True, help="w,h mm")
@click.option("--radius", type=float, default=0, help="Corner radius")
@click.option("--width", "line_width", type=float, default=0.1)
@click.option("--keep", is_flag=True, help="Keep existing Edge.Cuts items")
@handle_error
def outline_rect(at, size, radius, line_width, keep):
    x, y = parse_xy(at)
    w, h = parse_xy(size)
    output(pcb_mod.edit1(get_session(), {"action": "outline_rect", "x": x, "y": y, "width": w, "height": h,
                                         "corner_radius": radius, "line_width": line_width, "replace": not keep},
                         "outline rect"))


@outline_group.command("polygon")
@click.argument("points")
@click.option("--keep", is_flag=True)
@handle_error
def outline_polygon(points, keep):
    """Closed polygon 'x1,y1;x2,y2;...'."""
    output(pcb_mod.edit1(get_session(), {"action": "outline_polygon", "points": parse_points(points),
                                         "replace": not keep}, "outline polygon"))


@outline_group.command("circle")
@click.option("--center", required=True)
@click.option("--radius", type=float, required=True)
@click.option("--keep", is_flag=True)
@handle_error
def outline_circle(center, radius, keep):
    x, y = parse_xy(center)
    output(pcb_mod.edit1(get_session(), {"action": "outline_circle", "x": x, "y": y, "radius": radius,
                                         "replace": not keep}, "outline circle"))


@outline_group.command("clear")
@handle_error
def outline_clear():
    output(pcb_mod.edit1(get_session(), {"action": "clear_outline"}, "clear outline"))


@pcb_group.command("track")
@click.argument("points")
@click.option("--layer", default="F.Cu")
@click.option("--width", type=float, default=0.25)
@click.option("--net", default=None)
@handle_error
def pcb_track(points, layer, width, net):
    """Add a track polyline 'x1,y1;x2,y2;...'."""
    output(pcb_mod.edit1(get_session(), {"action": "add_track", "points": parse_points(points), "layer": layer,
                                         "width": width, "net": net}, "track"))


@pcb_group.command("via")
@click.option("--at", required=True)
@click.option("--net", default=None)
@click.option("--diameter", type=float, default=0.6)
@click.option("--drill", type=float, default=0.3)
@click.option("--type", "via_type", type=click.Choice(["through", "blind", "buried", "micro"]), default="through")
@click.option("--top", default="F.Cu")
@click.option("--bottom", default="B.Cu")
@handle_error
def pcb_via(at, net, diameter, drill, via_type, top, bottom):
    x, y = parse_xy(at)
    output(pcb_mod.edit1(get_session(), {"action": "add_via", "x": x, "y": y, "net": net, "diameter": diameter,
                                         "drill": drill, "via_type": via_type, "top": top, "bottom": bottom}, "via"))


@pcb_group.command("route")
@click.argument("from_pad")
@click.argument("to_pad")
@click.option("--layer", default=None, help="Default: F.Cu if both pads are on it, else B.Cu")
@click.option("--width", default=None, help="Track width in mm or auto (default: effective/default net class).")
@click.option("--mode", type=click.Choice(["45", "hv", "vh", "direct"]), default="45")
@click.option("--via-points", default=None, help="Intermediate points 'x,y;x,y'")
@handle_error
def pcb_route(from_pad, to_pad, layer, width, mode, via_points):
    """Route REF:PAD to REF:PAD with a 45°/orthogonal track (no obstacle avoidance; check DRC)."""
    width = None if width in (None, "auto") else float(width)
    a = {"action": "route", "from": from_pad, "to": to_pad, "layer": layer, "width": width, "mode": mode}
    if via_points:
        a["via_points"] = [list(parse_xy(p)) for p in via_points.split(";") if p]
    output(pcb_mod.edit1(get_session(), a, f"route {from_pad} {to_pad}"))


@pcb_group.command("autoroute")
@click.option("--engine", type=click.Choice(["builtin", "freerouting"]), default="builtin")
@click.option("--grid", default="0.25", help="builtin: positive grid mm, or auto (0.25 → 0.15 → 0.1 → 0.05).")
@click.option("--width", type=float, default=0.25)
@click.option("--clearance", type=float, default=0.2)
@click.option("--via-diameter", type=float, default=0.6)
@click.option("--via-drill", type=float, default=0.3)
@click.option("--layers", default="F.Cu,B.Cu")
@click.option("--net", default=None, help="Only this net")
@click.option("--skip-net", "skip_nets", multiple=True, help="Leave nets for zones (e.g. GND)")
@click.option("--passes", type=int, default=None, help="freerouting: max passes")
@click.option("--refs", default=None, help="builtin: only connections with a pad in these comma-separated refs.")
@click.option("--area", default=None, help="builtin: restrict all routes to x,y,w,h mm.")
@handle_error
def pcb_autoroute(engine, grid, width, clearance, via_diameter, via_drill, layers, net, skip_nets, passes, refs, area):
    """Autoroute unrouted connections (builtin grid router, or Freerouting via Specctra DSN/SES)."""
    if engine != "builtin" and (refs or area):
        raise ValueError("--refs and --area require --engine builtin")
    if grid != "auto":
        grid = float(grid)
    output(pcb_mod.autoroute(get_session(), engine, grid=grid, width=width, clearance=clearance,
                             via_diameter=via_diameter, via_drill=via_drill, layers=layers.split(","), net=net,
                             skip_nets=list(skip_nets) or None, passes=passes, refs=refs.split(",") if refs else None,
                             area=[float(v) for v in area.split(",")] if area else None))


@pcb_group.command("delete-tracks")
@click.option("--net", default=None)
@click.option("--layer", default=None)
@click.option("--uuid", "uuids", multiple=True)
@click.option("--vias-only", is_flag=True)
@click.option("--all", "all_", is_flag=True, help="Required when no filter is given")
@click.option("--include-locked", is_flag=True)
@handle_error
def pcb_delete_tracks(net, layer, uuids, vias_only, all_, include_locked):
    """Delete tracks/vias by net, layer or uuid (rip-up)."""
    if not (net or layer or uuids or vias_only or all_):
        raise ValueError("Refusing to delete every track without --all")
    output(pcb_mod.edit1(get_session(), {"action": "delete_tracks", "net": net, "layer": layer, "uuids": list(uuids),
                                         "vias_only": vias_only, "include_locked": include_locked}, "delete tracks"))


@pcb_group.command("delete-item")
@click.argument("uuid")
@handle_error
def pcb_delete_item(uuid):
    output(pcb_mod.edit1(get_session(), {"action": "delete_item", "uuid": uuid}, f"delete {uuid}"))


@pcb_group.group("zone")
def zone_group():
    """Copper zones (pours), keepouts and filling."""


@zone_group.command("add")
@click.option("--net", required=True)
@click.option("--layer", "layers", multiple=True, default=("F.Cu",))
@click.option("--rect", default=None, help="x,y,w,h (default: board outline)")
@click.option("--points", default=None, help="'x,y;x,y;...'")
@click.option("--margin", type=float, default=0.0, help="Grow(+)/shrink(-) the outline-derived rectangle")
@click.option("--clearance", type=float, default=None)
@click.option("--min-width", type=float, default=0.25)
@click.option("--priority", type=int, default=0)
@click.option("--pad-connection", type=click.Choice(["auto", "thermal", "solid", "none", "tht_thermal"]), default="auto",
              help="auto: solid SMD connections and thermal THT connections (default).")
@click.option("--thermal-gap", type=float, default=0.5)
@click.option("--thermal-width", type=float, default=0.5)
@click.option("--name", default=None)
@click.option("--no-fill", is_flag=True)
@handle_error
def zone_add(net, layers, rect, points, margin, clearance, min_width, priority, pad_connection, thermal_gap,
             thermal_width, name, no_fill):
    """Add a copper pour (filled by KiCad's ZONE_FILLER unless --no-fill)."""
    a = {"action": "add_zone", "net": net, "layers": list(layers), "margin": margin, "clearance": clearance,
         "min_width": min_width, "priority": priority, "pad_connection": pad_connection,
         "thermal_gap": thermal_gap, "thermal_width": thermal_width, "name": name, "fill": not no_fill}
    if rect:
        a["rect"] = [float(v) for v in rect.split(",")]
    if points:
        a["points"] = parse_points(points)
    output(pcb_mod.edit1(get_session(), a, f"zone {net}"))


@zone_group.command("keepout")
@click.option("--layer", "layers", multiple=True, default=("F.Cu", "B.Cu"))
@click.option("--rect", default=None)
@click.option("--points", default=None)
@click.option("--allow-tracks", is_flag=True)
@click.option("--allow-vias", is_flag=True)
@click.option("--no-pads", is_flag=True)
@click.option("--no-footprints", is_flag=True)
@click.option("--allow-fills", is_flag=True)
@click.option("--name", default=None)
@handle_error
def zone_keepout(layers, rect, points, allow_tracks, allow_vias, no_pads, no_footprints, allow_fills, name):
    """Add a rule area / keepout."""
    a = {"action": "add_zone", "keepout": True, "layers": list(layers), "no_tracks": not allow_tracks,
         "no_vias": not allow_vias, "no_pads": no_pads, "no_footprints": no_footprints, "no_fills": not allow_fills,
         "name": name}
    if rect:
        a["rect"] = [float(v) for v in rect.split(",")]
    if points:
        a["points"] = parse_points(points)
    output(pcb_mod.edit1(get_session(), a, "keepout"))


@zone_group.command("fill")
@handle_error
def zone_fill():
    """Fill all zones (KiCad ZONE_FILLER)."""
    output(pcb_mod.edit1(get_session(), {"action": "fill_zones"}, "fill zones"))


@zone_group.command("unfill")
@handle_error
def zone_unfill():
    output(pcb_mod.edit1(get_session(), {"action": "unfill_zones"}, "unfill zones"))


@zone_group.command("delete")
@click.option("--net", default=None)
@click.option("--uuid", default=None)
@handle_error
def zone_delete(net, uuid):
    output(pcb_mod.edit1(get_session(), {"action": "delete_zones", "net": net, "uuid": uuid}, "delete zones"))


@pcb_group.command("text")
@click.argument("text")
@click.option("--at", required=True)
@click.option("--layer", default="F.SilkS")
@click.option("--size", type=float, default=1.0)
@click.option("--thickness", type=float, default=0.15)
@click.option("--angle", type=float, default=0)
@handle_error
def pcb_text(text, at, layer, size, thickness, angle):
    x, y = parse_xy(at)
    output(pcb_mod.edit1(get_session(), {"action": "add_text", "text": text, "x": x, "y": y, "layer": layer,
                                         "size": size, "thickness": thickness, "angle": angle}, "text"))


@pcb_group.command("graphic")
@click.argument("shape", type=click.Choice(["line", "rect", "circle", "polygon"]))
@click.option("--start", default=None)
@click.option("--end", default=None)
@click.option("--center", default=None)
@click.option("--radius", type=float, default=None)
@click.option("--points", default=None)
@click.option("--layer", default="F.SilkS")
@click.option("--width", type=float, default=0.15)
@click.option("--filled", is_flag=True)
@handle_error
def pcb_graphic(shape, start, end, center, radius, points, layer, width, filled):
    """Add a graphic shape on any layer (silk, fab, user, Edge.Cuts...)."""
    a = {"action": "add_graphic", "shape": shape, "layer": layer, "width": width, "filled": filled}
    if start:
        a["start"] = list(parse_xy(start))
    if end:
        a["end"] = list(parse_xy(end))
    if center:
        a["center"] = list(parse_xy(center))
    if radius is not None:
        a["radius"] = radius
    if points:
        a["points"] = parse_points(points)
    output(pcb_mod.edit1(get_session(), a, f"graphic {shape}"))


@pcb_group.command("set-layers")
@click.argument("count", type=int)
@handle_error
def pcb_set_layers(count):
    """Set the copper layer count (2, 4, 6, ...)."""
    output(pcb_mod.edit1(get_session(), {"action": "set_copper_layers", "count": count}, f"{count} copper layers"))


@pcb_group.command("rename-layer")
@click.argument("layer")
@click.argument("name")
@handle_error
def pcb_rename_layer(layer, name):
    output(pcb_mod.edit1(get_session(), {"action": "rename_layer", "layer": layer, "name": name}, "rename layer"))


@pcb_group.command("set-pad-net")
@click.argument("pad")
@click.argument("net")
@handle_error
def pcb_set_pad_net(pad, net):
    """Assign a net to REF:PAD (board-only; schematic + `kce pcb update` is preferred)."""
    output(pcb_mod.edit1(get_session(), {"action": "set_pad_net", "pad": pad, "net": net}, f"{pad} -> {net}"))


@pcb_group.command("thickness")
@click.argument("mm", type=float)
@handle_error
def pcb_thickness(mm):
    output(pcb_mod.edit1(get_session(), {"action": "set_board_thickness", "thickness": mm}, "thickness"))


@pcb_group.command("origin")
@click.argument("kind", type=click.Choice(["aux", "grid"]))
@click.option("--at", required=True)
@handle_error
def pcb_origin(kind, at):
    """Set the drill/place (aux) or grid origin."""
    x, y = parse_xy(at)
    output(pcb_mod.edit1(get_session(), {"action": "set_origin", "kind": kind, "x": x, "y": y}, f"{kind} origin"))


@pcb_group.command("dsn")
@click.option("--output", "-o", "out", default=None)
@handle_error
def pcb_dsn(out):
    """Export Specctra DSN (for external autorouters like Freerouting)."""
    proj = _proj()
    out = out or os.path.join(proj.output_dir, proj.name + ".dsn")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    output(pcb_mod.export_dsn(get_session(), out))


@pcb_group.command("ses")
@click.argument("ses_file", type=click.Path(exists=True))
@handle_error
def pcb_ses(ses_file):
    """Import a routed Specctra session (.ses) back into the board."""
    output(pcb_mod.import_ses(get_session(), ses_file))


@pcb_group.command("batch")
@click.argument("actions_file", type=click.Path(exists=True))
@handle_error
def pcb_batch(actions_file):
    """Apply a JSON list of worker actions in one pcbnew session (see SKILL.md 'pcb batch')."""
    acts = json.load(open(actions_file, encoding="utf-8"))
    if not isinstance(acts, list) or not all(isinstance(a, dict) for a in acts):
        raise ValueError("PCB batch actions must be an array of objects")
    output(pcb_mod.edit(get_session(), acts, f"batch {os.path.basename(actions_file)}"))


# ═════════════════════════════════════════════════════════════════════════════
# lib
# ═════════════════════════════════════════════════════════════════════════════

@cli.group("lib")
def lib_group():
    """Symbol & footprint libraries: search, inspect, create, export, upgrade."""


@lib_group.command("index")
@click.option("--rebuild", is_flag=True)
@handle_error
def lib_index(rebuild):
    """Build (cached) search indexes of all symbol and footprint libraries."""
    sess = get_session()
    t = time.time()
    s = lib_mod.build_index(sess.project, "sym", rebuild)
    f = lib_mod.build_index(sess.project, "fp", rebuild)
    output(dict(lib_mod.index_status(sess.project), symbols=len(s), footprints=len(f),
                seconds=round(time.time() - t, 2)))


@lib_group.command("init-global-tables")
@click.option("--force", is_flag=True)
@handle_error
def lib_init_tables(force):
    """Install KiCad's default global sym/fp lib tables (first-run setup KiCad's GUI normally does)."""
    output(proj_mod.init_global_tables(force))


@lib_group.command("suggest-footprints")
@click.argument("lib_id")
@click.option("--query", "-q", default="", help="Extra filter terms (e.g. 0603, SOIC)")
@click.option("--limit", type=int, default=30)
@click.option("--size", help="Imperial package code, e.g. 0603 means 0603/1608 metric (not 0201/0603)")
@handle_error
def lib_suggest(lib_id, query, limit, size):
    """Footprints matching a symbol's ki_fp_filters and pin count."""
    output(lib_mod.suggest_footprints(get_session().project, lib_id, limit, query, size))


@lib_group.command("link-check")
@click.argument("lib_id")
@click.argument("fpid", required=False)
@handle_error
def lib_link_check(lib_id, fpid):
    """Compare a symbol's pins with its footprint's pads (catches a missing pad before DRC).

    FPID defaults to the symbol's Footprint property. Exposed and mechanical pads are
    listed separately; `ok:false` means a pin has no pad or a pad has no pin.
    """
    sess = get_session()
    if sess.project is None:
        raise RuntimeError("lib link-check needs a project (--project)")
    output(lib_mod.link_check(sess.project, lib_id, fpid))


@lib_group.group("sym")
def sym_group():
    """Symbol libraries."""


@sym_group.command("libs")
@handle_error
def sym_libs():
    output(lib_mod.list_libraries(get_session().project, "sym"))


def _library_search_output(rows, query, explain):
    terms = lib_mod.search_terms(" ".join(query))
    if not rows:
        click.echo(json.dumps({"warning": "no_results",
                               "hint": "Try fewer words or omit --pins/--pads; use --pins 8+ep for an exposed pad.",
                               "relaxations_tried": ["hyphen/underscore/space normalization", "package substring matching"],
                               "did_you_mean": []}), err=True)
    output({"results": rows, "score_parts": {"terms": terms, "match": "all terms",
            "weights": {"exact_name": 20, "name_prefix": 10, "name_substring": 6,
                        "keywords": 4, "library": 3, "description": 2},
            "name_length_penalty": 0.01, "package_aliases": {"dfn": ["son", "vson"]}}} if explain else rows)


@sym_group.command("search")
@click.argument("query", nargs=-1)
@click.option("--lib", default=None, help="Library wildcard")
@click.option("--pins", default=None, help="Pin count: 8, 3-5, 10+, or 8+ep (8 or 9 pins)")
@click.option("--power/--no-power", "power", default=None)
@click.option("--footprint", default=None, help="Substring of default footprint / filters")
@click.option("--limit", type=int, default=25)
@click.option("--explain", is_flag=True, help="Return results and scoring details instead of an array")
@handle_error
def sym_search(query, lib, pins, power, footprint, limit, explain):
    """Find symbols by all query words; hyphens, underscores and spaces are equivalent.

    Try `usb-c receptacle` or `nfc eeprom --pins 8+ep`. Empty results include a stderr hint."""
    res = lib_mod.search_symbols(get_session().project, " ".join(query), lib, pins, power, limit, footprint)
    rows = [{k: r[k] for k in ("lib_id", "description", "pin_count", "units", "footprint", "score")} for r in res]
    for row, hit in zip(rows, res):
        row["fp_filters"] = hit["fp_filters"].split()
    _library_search_output(rows, query, explain)


@sym_group.command("show")
@click.argument("lib_id")
@handle_error
def sym_show(lib_id):
    """Symbol detail: properties, units, pins. Accepts LIB:NAME or FILE.kicad_sym[:NAME]."""
    output(lib_mod.symbol_info(get_session().project, lib_id))


@sym_group.command("list")
@click.argument("library")
@handle_error
def sym_list(library):
    """List symbols in a library nickname or an unregistered .kicad_sym file."""
    output(lib_mod.list_library(get_session().project, "sym", library))


@sym_group.command("new")
@click.argument("library_file", type=click.Path())
@click.argument("name")
@click.option("--pin", "pins", multiple=True, required=True,
              help="NUMBER:NAME[:TYPE[:SIDE]] (TYPE: input/output/passive/power_in/...; SIDE left/right/top/bottom). "
                   "Comma-separate several.")
@click.option("--reference", default="U")
@click.option("--value", default=None)
@click.option("--footprint", default="")
@click.option("--datasheet", default="")
@click.option("--description", default="")
@click.option("--keywords", default="")
@click.option("--fp-filters", default="")
@click.option("--overwrite", is_flag=True)
@click.option("--add-to-table", default=None, help="Also register the library in the project sym-lib-table under this nickname")
@handle_error
def sym_new(library_file, name, pins, reference, value, footprint, datasheet, description, keywords, fp_filters,
            overwrite, add_to_table):
    """Create a box symbol with pins in a .kicad_sym (created if missing). Validated by kicad-cli."""
    sess = get_session()
    path = os.path.abspath(library_file if library_file.endswith(".kicad_sym") else library_file + ".kicad_sym")
    pl = lib_mod.parse_pin_specs(list(pins))
    sym = lib_mod.make_box_symbol(name, pl, reference, value, footprint, datasheet, description, keywords, fp_filters)
    if sess.project is None:
        raise RuntimeError("lib sym new needs a project (--project) for undo/validation")
    sess.snapshot(f"new symbol {name}", [path])
    d = sess.doc(path)
    tree = d.tree() if (os.path.exists(path) or d._tree is not None) else lib_mod.new_symbol_library()
    lib_mod.add_symbol_to_lib(tree, sym, overwrite)
    d.set_tree(tree)
    res = {"library": path, "symbol": name, "pins": len(pl)}
    if add_to_table:
        uri = kicad_uri(path, sess.project.dir)
        res["table"] = proj_mod.libtable_add(sess, "sym", add_to_table, uri)
    output(res)


@sym_group.command("delete")
@click.argument("library_file", type=click.Path(exists=True))
@click.argument("name")
@handle_error
def sym_delete(library_file, name):
    sess = get_session()
    path = os.path.abspath(library_file)
    sess.snapshot(f"delete symbol {name}", [path])
    lib_mod.delete_symbol_from_lib(sess.tree(path), name)
    sess.doc(path).mark_dirty()
    output({"library": path, "deleted": name})


@lib_group.group("fp")
def fp_group():
    """Footprint libraries."""


@fp_group.command("libs")
@handle_error
def fp_libs():
    output(lib_mod.list_libraries(get_session().project, "fp"))


@fp_group.command("search")
@click.argument("query", nargs=-1)
@click.option("--lib", default=None)
@click.option("--pads", default=None, help="Pad count: 2, 8-16, 40+")
@click.option("--type", "kind", type=click.Choice(["smd", "tht"]), default=None)
@click.option("--limit", type=int, default=25)
@click.option("--explain", is_flag=True, help="Return results and scoring details instead of an array")
@handle_error
def fp_search(query, lib, pads, kind, limit, explain):
    """Find footprints by all query words, pad count and SMD/THT.

    Package substrings match variants: `DFN 2x2 --pads 9 --type smd` also finds TDFN."""
    res = lib_mod.search_footprints(get_session().project, " ".join(query), lib, pads, kind, limit)
    rows = [{k: r[k] for k in ("fpid", "description", "pad_count", "attr", "score")} for r in res]
    _library_search_output(rows, query, explain)


@fp_group.command("show")
@click.argument("fpid")
@handle_error
def fp_show(fpid):
    """Footprint detail: pads, courtyard, models. Accepts LIB:NAME or FILE.kicad_mod."""
    output(lib_mod.footprint_info(get_session().project, fpid))


@fp_group.command("list")
@click.argument("library")
@handle_error
def fp_list(library):
    """List footprints in a library nickname or an unregistered .pretty directory."""
    output(lib_mod.list_library(get_session().project, "fp", library))


@fp_group.command("check")
@click.argument("fpid")
@click.option("--rules", type=click.Choice(["project", "default"]), default="project",
              help="Clearance source: the project's Default net class or KiCad's 0.2 mm default")
@click.option("--ipc-class", default=None, help="Requested IPC class; reported as unsupported until package compliance can be evaluated")
@handle_error
def fp_check(fpid, rules, ipc_class):
    """Check a library footprint on its own, without a board or DRC run.

    Reports pad numbers (signal vs exposed/mechanical), duplicate numbers, paste apertures
    that carry a number, courtyard presence/size/margin, pad-to-pad clearance violations
    and missing Fab/Courtyard layers. `ok:false` means fix the library before placing it.
    Read coverage and unsupported: this uses conservative rotated pad boxes, checks model
    paths only, and cannot establish cross-format equivalence or IPC compliance.
    """
    output(lib_mod.footprint_check(get_session(), fpid, rules, ipc_class))


@fp_group.group("model")
def fp_model_group():
    """3D model of a library footprint (persists across placements and pcb update)."""


def _require_project(what: str):
    sess = get_session()
    if sess.project is None:
        raise RuntimeError(f"{what} needs a project (--project)")
    return sess, sess.project


@fp_model_group.command("list")
@click.argument("fpid")
@handle_error
def fp_model_list(fpid):
    """List the model(s) of FPID with resolved paths and existence."""
    _, proj = _require_project("lib fp model list")
    output(lib_mod.model_report(proj, fpid))


@fp_model_group.command("set")
@click.argument("fpid")
@click.argument("file", type=click.Path())
@click.option("--offset", default=None, help="x,y,z in mm")
@click.option("--rotation", default=None, help="x,y,z in degrees")
@click.option("--scale", default=None, help="x,y,z factors")
@click.option("--add", is_flag=True, help="Add another model instead of replacing the first")
@click.option("--index", type=int, default=None, help="Model to replace (default 0)")
@click.option("--copy-into", default=None, help="Copy FILE into this directory first (relative to the project)")
@click.option("--no-check", is_flag=True, help="Write the path even if the file does not exist")
@handle_error
def fp_model_set(fpid, file, offset, rotation, scale, add, index, copy_into, no_check):
    """Set the 3D model inside a library footprint, so every placement gets a body.

    A FILE inside the project is written as ${KIPRJMOD}/... and travels with the project.
    Use --copy-into 3d/ to copy a vendor STEP in first.
    """
    sess, proj = _require_project("lib fp model set")
    _, fp_path = lib_mod.resolve_footprint(proj, fpid)
    if not no_check and not copy_into and not file.startswith("${"):
        env = kb.kicad_env_vars(proj.dir)
        if not os.path.exists(kb.expand_vars(os.path.abspath(os.path.expanduser(file)), env)):
            raise FileNotFoundError(f"3D model not found: {file} (use --no-check to write the path anyway)")
    ref, dest = lib_mod.model_file_ref(proj, file, copy_into)
    tree = copy.deepcopy(sess.tree(fp_path))
    info = lib_mod.set_footprint_model(tree, ref, offset=offset, rotation=rotation,
                                       scale=scale, index=index, add=add)
    source_bytes = None
    if dest:
        with open(os.path.abspath(os.path.expanduser(file)), "rb") as source:
            source_bytes = source.read()
    sess.snapshot(f"set 3D model on {fpid}", [fp_path] + ([dest] if dest else []))
    if dest:
        sess.doc(dest).set_bytes(source_bytes)
    sess.doc(fp_path).set_tree(tree)
    models = lib_mod.model_entries(proj, tree)
    for model in models:
        if dest and os.path.normcase(model["resolved_path"]) == os.path.normcase(dest):
            model["exists"] = True  # staged bytes are part of the candidate project
    output({"fpid": fpid, "index": info["index"], "created": info["created"], "dry_run": _dry_run,
            "models": models})


@fp_model_group.command("remove")
@click.argument("fpid")
@click.option("--index", type=int, default=0)
@handle_error
def fp_model_remove(fpid, index):
    """Remove a 3D model reference from a library footprint."""
    sess, proj = _require_project("lib fp model remove")
    _, fp_path = lib_mod.resolve_footprint(proj, fpid)
    tree = copy.deepcopy(sess.tree(fp_path))
    info = lib_mod.remove_footprint_model(tree, index)
    sess.snapshot(f"remove 3D model from {fpid}", [fp_path])
    sess.doc(fp_path).set_tree(tree)
    output({"fpid": fpid, "removed": info["removed"], "dry_run": _dry_run,
            "models": lib_mod.model_entries(proj, tree)})


@fp_group.command("new")
@click.argument("pretty_dir", type=click.Path())
@click.argument("name")
@click.option("--kind", type=click.Choice(["header", "dip", "soic", "smd-row", "chip", "custom"]), required=True)
@click.option("--pads", "count", type=int, default=2)
@click.option("--pitch", type=float, default=2.54)
@click.option("--rows", type=int, default=1, help="header: rows")
@click.option("--row-spacing", type=float, default=None)
@click.option("--pad-size", default="1.7,1.7", help="w,h")
@click.option("--drill", type=float, default=1.0)
@click.option("--pad", "custom_pads", multiple=True, help="custom: NUM:x,y:w,h[:smd|thru_hole[:drill]]")
@click.option("--description", default="")
@click.option("--tags", default="")
@click.option("--overwrite", is_flag=True)
@click.option("--add-to-table", default=None, help="Register the .pretty in the project fp-lib-table")
@click.option("--model", "model_file", default=None, help="3D model file to reference (relative to the project becomes ${KIPRJMOD})")
@handle_error
def fp_new(pretty_dir, name, kind, count, pitch, rows, row_spacing, pad_size, drill, custom_pads, description, tags,
           overwrite, add_to_table, model_file):
    """Create a footprint (header/DIP/SOIC/chip generators or explicit pads). Validated by kicad-cli."""
    sess = get_session()
    if sess.project is None:
        raise RuntimeError("lib fp new needs a project (--project)")
    pretty = os.path.abspath(pretty_dir if pretty_dir.endswith(".pretty") else pretty_dir + ".pretty")
    w, h = parse_xy(pad_size)
    if kind == "custom":
        pads = []
        for spec in custom_pads:
            parts = spec.split(":")
            x, y = parse_xy(parts[1])
            pw, ph = parse_xy(parts[2])
            ptype = parts[3] if len(parts) > 3 else "smd"
            pads.append({"number": parts[0], "x": x, "y": y, "w": pw, "h": ph, "type": ptype,
                         "drill": float(parts[4]) if len(parts) > 4 else drill})
    else:
        pads = lib_mod.generate_pads(kind, count, pitch, rows, w, h, drill, row_spacing)
    fp = lib_mod.make_footprint(name, pads, description, tags)
    if model_file:
        ref, _ = lib_mod.model_file_ref(sess.project, model_file)
        lib_mod.set_footprint_model(fp, ref)
    path = os.path.join(pretty, name + ".kicad_mod")
    overwrote = os.path.exists(path)
    if overwrote and not overwrite:
        raise FileExistsError(f"{path} exists (use --overwrite)")
    os.makedirs(pretty, exist_ok=True)
    sess.snapshot(f"new footprint {name}", [path])
    sess.doc(path).set_tree(fp)
    res = {"footprint": path, "pads": len(pads), "overwrote": overwrote, "dry_run": _dry_run,
           "models": lib_mod.model_entries(sess.project, fp)}
    if add_to_table:
        uri = kicad_uri(pretty, sess.project.dir)
        res["table"] = proj_mod.libtable_add(sess, "fp", add_to_table, uri)
    output(res)


WIZARDS = {
    "qfn": ("qfn_wizard", "QFNWizard"), "qfp": ("qfp_wizard", "QFPWizard"), "bga": ("bga_wizard", "BGAWizard"),
    "sdip": ("sdip_wizard", "SDIPWizard"), "zip": ("zip_wizard", "ZIPWizard"), "fpc": ("FPC_wizard", "FPC_FootprintWizard"),
    "circular_pad_array": ("circular_pad_array_wizard", "circular_pad_array_wizard"),
    "touch_slider": ("touch_slider_wizard", "TouchSliderWizard"),
    "mutualcap_button": ("mutualcap_button_wizard", "MutualcapButtonWizard"),
    "qrcode": ("qrcode_footprint_wizard", "QRCodeWizard"), "uss39_barcode": ("uss39_barcode", "Uss39Wizard"),
    "micromatch": ("microMatch_connectors", "MicroMatchWizard"), "scrollwheel": ("scrollwheel_wizard", "Scrollwheel"),
}


def _plugins_dir() -> str:
    share = kb.kicad_share_dir()
    cands = [os.path.join(share, "scripting", "plugins")] if share else []
    for c in cands:
        if os.path.isdir(c):
            return c
    raise FileNotFoundError("KiCad scripting/plugins directory (footprint wizards) not found; set KICAD_SHARE_DIR")


def _wizard_class(name: str):
    if name not in WIZARDS:
        raise KeyError(f"Unknown wizard {name!r}. Available: {sorted(WIZARDS)}")
    mod, cls = WIZARDS[name]
    src = open(os.path.join(_plugins_dir(), mod + ".py"), encoding="utf-8").read()
    if f"class {cls}" not in src:
        import re as _re
        m = _re.findall(r"class (\w+)\((?:FootprintWizardBase\.)?FootprintWizard", src)
        if m:
            cls = m[0]
    return mod, cls


@fp_group.command("wizards")
@handle_error
def fp_wizards():
    """KiCad's bundled footprint wizards (scripting/plugins).

    A dual-row package (DFN/SON) is the qfn wizard with `--param Pads.nx=0 --param Pads.ny=N`;
    the exposed pad is `EPad.epad`.
    """
    output([{"name": k, "module": v[0]} for k, v in WIZARDS.items()])


@fp_group.command("wizard-params")
@click.argument("wizard")
@handle_error
def fp_wizard_params(wizard):
    mod, cls = _wizard_class(wizard)
    output(kb.run_pcbnew("wizard_params", {"plugins_dir": _plugins_dir(), "module": mod, "class": cls}))


@fp_group.command("wizard")
@click.argument("wizard")
@click.argument("pretty_dir", type=click.Path())
@click.option("--param", "params", multiple=True, help="Page.Name=value (see wizard-params)")
@click.option("--name", default=None, help="Footprint name")
@click.option("--description", default=None, help="Footprint description (fills descr and the Description property)")
@click.option("--tags", default=None, help="Keywords for search")
@click.option("--datasheet", default=None)
@click.option("--model", "model_file", default=None, help="3D model file to reference (project-relative becomes ${KIPRJMOD})")
@click.option("--overwrite", is_flag=True, help="Replace an existing <name>.kicad_mod (default: refuse)")
@click.option("--add-to-table", default=None, help="Register the .pretty in the project fp-lib-table")
@handle_error
def fp_wizard(wizard, pretty_dir, params, name, description, tags, datasheet, model_file, overwrite, add_to_table):
    """Generate a footprint with a KiCad wizard (QFN/QFP/BGA/...) into a .pretty library.

    The wizard writes into a staging directory first, so an existing footprint is never
    overwritten without --overwrite and the write is recorded in `session history`.
    Exposed-pad paste apertures are renumbered to the exposed pad (never "1").
    """
    sess = get_session()
    proj = sess.project
    mod, cls = _wizard_class(wizard)
    pretty = os.path.abspath(pretty_dir if pretty_dir.endswith(".pretty") else pretty_dir + ".pretty")
    pv = parse_kv(params)
    td = kb.mkdtemp("wizard")
    try:
        res = kb.run_pcbnew("footprint_wizard", {"plugins_dir": _plugins_dir(), "module": mod, "class": cls,
                                                "params": pv, "library": td, "name": name})
        fp = sexpr.load(res["path"])
        renumbered = lib_mod.renumber_paste_only(fp)
        lib_mod.set_footprint_metadata(fp, description, tags, datasheet)
        if model_file:
            if proj is None:
                raise RuntimeError("--model needs a project (--project)")
            ref, _ = lib_mod.model_file_ref(proj, model_file)
            lib_mod.set_footprint_model(fp, ref)
        dest = os.path.join(pretty, res["name"] + ".kicad_mod")
        overwrote = os.path.exists(dest)
        if overwrote and not overwrite:
            raise FileExistsError(f"{dest} exists (use --overwrite)")
        os.makedirs(pretty, exist_ok=True)
        if proj is not None:
            sess.snapshot(f"new footprint {res['name']}", [dest])
            sess.doc(dest).set_tree(fp)
        elif not _dry_run:
            sexpr.dump(fp, dest)
        out = dict(res)
        out.update(path=dest, library=pretty, overwrote=overwrote, dry_run=_dry_run,
                   paste_only_renumbered=renumbered, description=description, tags=tags,
                   datasheet=datasheet, models=lib_mod.model_entries(proj, fp))
        if add_to_table:
            if proj is None:
                raise RuntimeError("--add-to-table needs a project (--project)")
            out["table"] = proj_mod.libtable_add(sess, "fp", add_to_table, kicad_uri(pretty, proj.dir))
        output(out)
    finally:
        shutil.rmtree(td, ignore_errors=True)


# ═════════════════════════════════════════════════════════════════════════════
# check
# ═════════════════════════════════════════════════════════════════════════════

@cli.group("check")
def check_group():
    """ERC / DRC with summaries, fix hints and a diagnosis plan."""


def check_options(function):
    for decorator in (
        click.option("--fail-on", type=click.Choice(["error", "warning", "none"]), default=None,
                     help="Exit 3 (design) for new findings at this threshold. Default: report only."),
        click.option("--baseline", type=click.Path(exists=True, dir_okay=False), help="Previous raw or summarized JSON report; gate only new findings."),
        click.option("--summary", is_flag=True, help="Omit violation rows, retaining counts and fix hints."),
        click.option("--max-violations", type=click.IntRange(min=0), help="Limit displayed rows; gate still checks every finding."),
    ):
        function = decorator(function)
    return function


def _check_policy(result, baseline, fail_on, summary, max_violations):
    previous = None
    if baseline:
        with open(baseline, encoding="utf-8") as f:
            previous = json.load(f)
        if isinstance(previous, dict) and result['kind'] in previous:
            previous = previous[result['kind']]
    return checks_mod.report_policy(result, baseline=previous, fail_on=fail_on or 'none',
                                    summary=summary, max_violations=max_violations)


@check_group.command("erc")
@click.option("--output", "-o", "out", default=None, help="Keep the raw JSON report here")
@click.option("--severity", type=click.Choice(["all", "error", "warning", "error+warning", "exclusions"]), default="all")
@click.option("--fail-on-error", is_flag=True, help="Exit 3 (design) if new ERC errors exist.")
@check_options
@handle_error
def check_erc(out, severity, fail_on_error, fail_on, baseline, summary, max_violations):
    """Check schematic electrical rules; use --fail-on error for CI and plan acceptance."""
    r, failed = _check_policy(checks_mod.run_erc(get_session(), out, severity), baseline,
                             fail_on or ('error' if fail_on_error else 'none'), summary, max_violations)
    output(r, f"ERC: {r['errors']} error(s), {r['warnings']} warning(s)")
    if failed:
        raise RuntimeError(f"ERC failed with {len(failed)} new finding(s); inspect `kce check diagnose --only erc`.")


@check_group.command("drc")
@click.option("--output", "-o", "out", default=None)
@click.option("--severity", type=click.Choice(["all", "error", "warning", "error+warning", "exclusions"]), default="all")
@click.option("--no-parity", is_flag=True, help="Skip schematic parity check")
@click.option("--no-refill", is_flag=True, help="Do not refill zones before DRC")
@click.option("--save-board", is_flag=True, help="Keep the refilled zones in the board")
@click.option("--fail-on-error", is_flag=True, help="Exit 3 (design) if new DRC errors exist.")
@check_options
@handle_error
def check_drc(out, severity, no_parity, no_refill, save_board, fail_on_error, fail_on, baseline, summary, max_violations):
    """Check board rules; truncation never weakens --fail-on or --baseline gates."""
    r = checks_mod.run_drc(get_session(), out, severity, parity=not no_parity, refill=not no_refill, save_board=save_board)
    r, failed = _check_policy(r, baseline, fail_on or ('error' if fail_on_error else 'none'), summary, max_violations)
    output(r, f"DRC: {r['errors']} error(s), {r['warnings']} warning(s), {r['unconnected']} unconnected")
    if failed:
        raise RuntimeError(f"DRC failed with {len(failed)} new finding(s); inspect `kce check diagnose --only drc`.")


@check_group.command("diagnose")
@click.option("--only", type=click.Choice(["erc", "drc", "both"]), default="both")
@click.option("--fail-on-error", is_flag=True, help="Exit 3 (design) on new errors.")
@check_options
@handle_error
def check_diagnose(only, fail_on_error, fail_on, baseline, summary, max_violations):
    """Run ERC+DRC and return a prioritised fix plan; --fail-on warning is a CI gate."""
    result = checks_mod.diagnose(get_session(), only, include_violations=True)
    failures = []
    for kind in ('erc', 'drc'):
        if kind in result:
            result[kind], failed = _check_policy(result[kind], baseline,
                fail_on or ('error' if fail_on_error else 'none'), summary, max_violations)
            failures.extend(failed)
    output(result)
    if failures:
        raise RuntimeError(f"DRC failed with {len(failures)} new finding(s); inspect `kce check diagnose --help`.")


# ═════════════════════════════════════════════════════════════════════════════
# fab
# ═════════════════════════════════════════════════════════════════════════════

@cli.group("fab")
def fab_group():
    """Manufacturing packages (gerbers, drill, pos, BOM, netlist, STEP, zip)."""


@fab_group.command("presets")
@handle_error
def fab_presets():
    output(fab_mod.list_presets())


@fab_group.command("package")
@click.option("--preset", type=click.Choice(sorted(fab_mod.PRESETS)), default="generic")
@click.option("--output-dir", "-o", default=None)
@click.option("--no-drc", is_flag=True)
@click.option("--fail-on-drc", is_flag=True, help="Abort if DRC reports errors")
@click.option("--step/--no-step", "step", default=None)
@click.option("--atomic-output", is_flag=True, help="Stage and atomically publish only complete packages (always enabled).")
@click.option("--fail-on-any-export", is_flag=True, help="Exit 3 on any export failure (always enabled).")
@click.option("--require-lcsc", is_flag=True, help="Exit 3 before export if any assembled part lacks LCSC.")
@click.option("--bom-fields", default=None, help="Comma-separated BOM fields; defaults include MPN/LCSC when present.")
@click.option("--rotation-fix", type=click.Path(exists=True, dir_okay=False), help="JSON reference/footprint glob to angle offset; changes only the JLC CPL.")
@click.option("--manifest", is_flag=True, help="Write tool/source/DRC metadata and SHA-256 payload hashes to manifest.json.")
@click.option("--name-suffix", default=None, help="Archive suffix using {rev}, {preset}, {tag}, {commit}.")
@click.option("--require-rev", default=None, help="Exit 3 before export unless the title-block revision matches (v prefix optional).")
@handle_error
def fab_package(preset, output_dir, no_drc, fail_on_drc, step, atomic_output, fail_on_any_export, require_lcsc, bom_fields, rotation_fix, manifest, name_suffix, require_rev):
    """Stage and verify every export before publishing. Failures preserve the accepted directory.

    Inspect complete/failed_steps before hand-off. Use a fresh --output-dir for portable releases.
    """
    r = fab_mod.make_package(get_session(), preset, output_dir, run_drc=not no_drc, fail_on_drc=fail_on_drc, step=step,
                              require_lcsc=require_lcsc, bom_fields=bom_fields, rotation_fix=rotation_fix, manifest=manifest,
                              name_suffix=name_suffix, require_rev=require_rev)
    if not r["complete"]:
        output(r)
        reason = "Fab package incomplete: " + ", ".join(r["failed_steps"])
        if "drc" in r["failed_steps"]:
            reason += "; fix the reported DRC errors with `kce check diagnose`, then rerun fab package --fail-on-drc"
        raise RuntimeError(reason)
    output(r, f"Fab package ({preset}): {r['summary']['total_files']} files, zip {r['zip']['path']}")


@fab_group.command("bom-check")
@click.option("--require", "required", default="MPN,LCSC,Footprint", help="Comma-separated required assembly fields.")
@click.option("--variant", default=None)
@click.option("--prices", type=click.Path(exists=True, dir_okay=False), help="CSV with LCSC (or --price-key) and unit_price_usd columns; unpriced parts fail.")
@click.option("--price-key", default="LCSC")
@click.option("--against", default=None, help="Baseline project, archive or git:REV.")
@click.option("--max-delta-pct", type=float, default=None)
@handle_error
def fab_bom_check(required, variant, prices, price_key, against, max_delta_pct):
    """Gate assembly fields and file-based costs. Emits the full report before design exit 3."""
    result = bom_mod.check(get_session(), tuple(f.strip() for f in required.split(",") if f.strip()),
                           prices, price_key, against, max_delta_pct, variant)
    output(result)
    if not result["ok"]:
        raise RuntimeError("BOM check failed; inspect missing, unpriced and over_budget")


@fab_group.group("parts")
def fab_parts():
    """Inspect assembly fields before choosing or ordering supplier parts."""


@fab_parts.command("check")
@click.option("--offline", is_flag=True, help="Field inspection only; stock lookup is always external.")
@handle_error
def fab_parts_check(offline):
    """List refs, LCSC IDs, footprints and lookup links; verify stock and package at the supplier."""
    output(bom_mod.parts_check(get_session()))


# ═════════════════════════════════════════════════════════════════════════════
# jobset
# ═════════════════════════════════════════════════════════════════════════════

@cli.group("jobset")
def jobset_group():
    """KiCad jobsets (.kicad_jobset): create, edit and run."""


@jobset_group.command("types")
@handle_error
def jobset_types():
    """Job types KiCad 10 supports and their setting keys."""
    output(jobset_mod.job_types())


@jobset_group.command("type-info")
@click.argument("job_type")
@handle_error
def jobset_type_info(job_type):
    output(jobset_mod.job_type_info(job_type))


@jobset_group.command("new")
@click.argument("name", required=False)
@click.option("--overwrite", is_flag=True)
@click.option("--fab-template", is_flag=True, help="Native check/export jobs; the archive contains Gerbers/drill. Use fab package for complete BOM/CPL hand-off.")
@handle_error
def jobset_new(name, overwrite, fab_template):
    sess = get_session()
    r = jobset_mod.new(sess, name, overwrite)
    if fab_template:
        r = jobset_mod.fab_template(sess, r["jobset"])
    output(r)


@jobset_group.command("show")
@click.argument("name", required=False)
@handle_error
def jobset_show(name):
    sess = get_session()
    output(jobset_mod.show(sess, jobset_mod.jobset_path(sess, name)))


@jobset_group.command("list")
@handle_error
def jobset_list():
    proj = _proj()
    output(sorted(f for f in os.listdir(proj.dir) if f.endswith(".kicad_jobset")))


@jobset_group.command("add-job")
@click.argument("job_type")
@click.option("--jobset", "name", default=None)
@click.option("--set", "settings", multiple=True, help="key=value (value JSON-parsed), see `kce jobset type-info`")
@click.option("--description", default="")
@click.option("--id", "job_id", default=None)
@handle_error
def jobset_add_job(job_type, name, settings, description, job_id):
    sess = get_session()
    output(jobset_mod.add_job(sess, jobset_mod.jobset_path(sess, name), job_type, parse_kv(settings), description, job_id))


@jobset_group.command("add-output")
@click.argument("kind", type=click.Choice(["folder", "archive"]))
@click.argument("output_path")
@click.option("--jobset", "name", default=None)
@click.option("--only", multiple=True, help="Job ids to include (default all)")
@click.option("--description", default="")
@handle_error
def jobset_add_output(kind, output_path, name, only, description):
    sess = get_session()
    output(jobset_mod.add_output(sess, jobset_mod.jobset_path(sess, name), kind, output_path, list(only), description))


@jobset_group.command("remove")
@click.argument("item_id")
@click.option("--jobset", "name", default=None)
@handle_error
def jobset_remove(item_id, name):
    sess = get_session()
    output(jobset_mod.remove(sess, jobset_mod.jobset_path(sess, name), item_id))


@jobset_group.command("execute")
@click.argument("name", required=False)
@click.option("--output", "output_id", default=None, help="Only this output id")
@click.option("--stop-on-error", is_flag=True)
@click.option("--fail-on-job-error/--no-fail-on-job-error", default=True,
              help="Exit 3 (design) if any job fails; stdout result is preserved.")
@handle_error
def jobset_execute(name, output_id, stop_on_error, fail_on_job_error):
    """Run a jobset with result listing (wraps `kicad-cli jobset run`; see also `kce jobset run`)."""
    sess = get_session()
    result = jobset_mod.run(sess, jobset_mod.jobset_path(sess, name), output_id, stop_on_error)
    output(result)
    if fail_on_job_error and result["failed_jobs"]:
        raise jobset_mod.JobsetFailed(result)
    if fail_on_job_error and result['returncode']:
        raise kb.KicadError(f"kicad-cli jobset run failed (exit {result['returncode']}); inspect stdout and `kce jobset execute --help`.",
                            dict(result, command=result.get('command', ['kicad-cli', 'jobset', 'run'])))


# ═════════════════════════════════════════════════════════════════════════════
# preview
# ═════════════════════════════════════════════════════════════════════════════

@cli.group("preview")
def preview_group():
    """Preview bundles (SVG/PNG rendered by kicad-cli + manifest.json): capture, latest, diff, live."""


@preview_group.command("recipes")
@handle_error
def preview_recipes():
    output(preview_mod.recipes())


@preview_group.command("capture")
@click.option("--recipe", type=click.Choice(sorted(preview_mod.RECIPES)), default="quick")
@click.option("--force", is_flag=True, help="Bypass the cache")
@click.option("--root-dir", default=None)
@click.option("--width", type=int, default=1280)
@click.option("--height", type=int, default=720)
@click.option("--label", default=None)
@handle_error
def preview_capture(recipe, force, root_dir, width, height, label):
    output(preview_mod.capture(get_session(), recipe, force, root_dir, width, height, label,
                               command=_invocation()))


@preview_group.command("latest")
@click.option("--recipe", default=None)
@click.option("--root-dir", default=None)
@handle_error
def preview_latest(recipe, root_dir):
    """Newest existing bundle (read-only, never renders)."""
    output(preview_mod.latest(get_session(), recipe, root_dir))


@preview_group.command("diff")
@click.option("--against", required=True, help="Project path, .zip archive, checkpoint:NAME or git:REV")
@click.option("--recipe", type=click.Choice(["board", "schematic", "3d", "quick"]), default="board")
@click.option("--root-dir", default=None)
@handle_error
def preview_diff(against, recipe, root_dir):
    """Publish a diff bundle (before/after renders + structural diff.json)."""
    output(preview_mod.diff(get_session(), against, recipe, root_dir, command=_invocation()))


@preview_group.group("live")
def live_group():
    """Live preview session (session.json head + history.json; manual push)."""


@live_group.command("start")
@click.option("--recipe", type=click.Choice(sorted(preview_mod.RECIPES)), default="quick")
@click.option("--root-dir", default=None)
@handle_error
def live_start(recipe, root_dir):
    output(preview_mod.live_start(get_session(), recipe, root_dir, _invocation()))


@live_group.command("push")
@click.option("--recipe", type=click.Choice(sorted(preview_mod.RECIPES)), default="quick")
@click.option("--root-dir", default=None)
@click.option("--stage", default=None, help="Stage label")
@click.option("--note", default=None)
@handle_error
def live_push(recipe, root_dir, stage, note):
    output(preview_mod.live_push(get_session(), recipe, root_dir, _invocation(),
                                 stage, note))


@live_group.command("status")
@click.option("--recipe", type=click.Choice(sorted(preview_mod.RECIPES)), default="quick")
@click.option("--root-dir", default=None)
@handle_error
def live_status(recipe, root_dir):
    """Cheap read-only probe of the live session (current bundle + history summary)."""
    output(preview_mod.status(get_session(), recipe, root_dir))


@live_group.command("stop")
@click.option("--recipe", type=click.Choice(sorted(preview_mod.RECIPES)), default="quick")
@click.option("--root-dir", default=None)
@handle_error
def live_stop(recipe, root_dir):
    output(preview_mod.live_stop(get_session(), recipe, root_dir))


# ═════════════════════════════════════════════════════════════════════════════
# run (transactional plans)
# ═════════════════════════════════════════════════════════════════════════════

@cli.command("run")
@click.argument("plan", type=click.Path(dir_okay=False))
@handle_error
def run_plan(plan):
    """Execute JSON plan steps in one session, then validate and save once.

    PLAN has version: 1 and steps: [{"args": ["sch", "add", ...]}, ...].
    See schemas/plan.schema.json. A step or final validation failure rolls back
    all project files and history. --dry-run executes on isolated copies, validates,
    and returns would_write/would_save without changing files, history or outputs.
    Each step may include expect: [{"path":"clean","op":"true"}]. Comparisons
    eq/ne/lt/le/gt/ge require value; empty/nonempty/true/false do not. Failed
    acceptance rolls back before commit (design/3); unknown operators are input/2.
    Commands must stay within one project; project switching, nested sessions,
    global configuration, direct upgrades and arbitrary Python are rejected.
    VCS/session metadata paths are reserved. Global options belong on run,
    including when they would otherwise appear after a step command.
    """
    global _plan_mode
    doc = plan_mod.load_plan(plan)
    sess = get_session()
    target = doc.get("project") or sess.project_path
    proj = proj_mod.resolve_project(target)
    if proj is None:
        sess.require_project()
    if sess.project is None or not same_path(sess.project_path, proj.pro_path):
        sess.set_project(proj)
    for step in doc["steps"]:
        plan_mod.validate_step(cli, step["args"], proj.dir)
    results: List[Dict[str, Any]] = []
    phase, failed_step, args = "step", 1, []
    previous = _plan_mode
    _plan_mode = True
    try:
        with sess.transaction(validate_files, dry_run=_dry_run) as saved:
            for i, step in enumerate(doc["steps"], 1):
                failed_step, args = i, step["args"]
                started = time.monotonic()
                progress_event("step_started", step=i, args=args)
                result = run_in_process(args, project=proj.pro_path, session=sess)
                plan_mod.accept(result, step.get("expect", []))
                progress_event("step_finished", step=i, elapsed_ms=round((time.monotonic() - started) * 1000, 3))
                results.append({"step": i, "args": args, "ok": True, "result": result,
                                "elapsed_ms": round((time.monotonic() - started) * 1000, 3)})
            phase, failed_step, args = "validation", None, []
    except Exception as e:
        raise plan_mod.PlanError(error_payload(e), step=failed_step, args=args,
                                 steps=len(doc["steps"]), results=results, phase=phase,
                                 rolled_back=not getattr(e, "rollback_failed", False)) from e
    finally:
        _plan_mode = previous
    output({"version": doc["version"], "project": proj.pro_path, "steps": results,
            "dry_run": _dry_run, **saved}, f"Ran {len(results)} step(s) in one transaction")



@cli.command("serve")
@click.option("--request-timeout-ms", type=click.IntRange(min=1), default=30000, show_default=True,
              help="Deadline for each request, including all plan steps; timeout kills and reaps native workers.")
@handle_error
def serve(request_timeout_ms):
    """Serve JSON-RPC 2.0 on stdin/stdout, one request and response per line.

    Method run takes {"args": ["pcb", "info"], "project": "..."} and returns
    the command's CLI JSON. Errors carry the K1 object, category and exit code.
    The pcbnew worker stays alive across board requests. shutdown or EOF exits.
    Diagnostics go to stderr; stdout contains only JSON-RPC responses.
    """
    from .core.serve import serve_stdio
    serve_stdio(get_session().project_path, request_timeout_ms, progress=_progress,
                default_session_dir=get_session().session_dir)


# ═════════════════════════════════════════════════════════════════════════════
# session
# ═════════════════════════════════════════════════════════════════════════════

@cli.group("session")
def session_group():
    """Session state, undo/redo/history (persists across one-shot commands)."""


@session_group.command("status")
@handle_error
def session_status():
    output(get_session().status())


@session_group.command("undo")
@click.option("--steps", type=int, default=1)
@handle_error
def session_undo(steps):
    sess = get_session()
    done = []
    for _ in range(steps):
        e = sess.undo()
        if e is None:
            break
        done.append({"id": e["id"], "description": e["description"], "files": sorted(e["files"])})
    if not done:
        raise RuntimeError("Nothing to undo")
    output({"undone": done, "undo_depth": len(sess._undo), "redo_depth": len(sess._redo)},
           f"Undid {len(done)} step(s)")


@session_group.command("redo")
@click.option("--steps", type=int, default=1)
@handle_error
def session_redo(steps):
    sess = get_session()
    done = []
    for _ in range(steps):
        e = sess.redo()
        if e is None:
            break
        done.append({"id": e["id"], "description": e["description"]})
    if not done:
        raise RuntimeError("Nothing to redo")
    output({"redone": done, "undo_depth": len(sess._undo), "redo_depth": len(sess._redo)})


@session_group.command("history")
@click.option("--limit", type=int, default=50)
@handle_error
def session_history(limit):
    sess = get_session()
    output({"undo": sess.list_history()[:limit], "redo": sess.redo_list()[:limit]})


@session_group.command("checkpoint")
@click.argument("name")
@click.option("--immutable", is_flag=True, help="Refuse replacement of an existing name.")
@handle_error
def session_checkpoint(name, immutable):
    """Publish an immutable generation under NAME; replacement atomically switches its head.

    Excludes session state, outputs/, .git, __pycache__ and locks. Restore deletes files
    created after the checkpoint: keep journals, plans and external libraries outside.
    Use session checkpoints to list names, project compare checkpoint:NAME to review.
    """
    sess = get_session()
    root = _proj().dir
    plan_mod.checkpoint_dir(root, name)
    if _dry_run:
        files = plan_mod.snapshot_files(root)
        output({"checkpoint": name, "files": sorted(files), "dry_run": True})
        return
    if sess.dirty_docs():
        raise ValueError("Save pending edits with `kce project save` before making a checkpoint.")
    sess._acquire_lock()
    try:
        result = plan_mod.save_checkpoint(root, name, state_dir=sess.state_dir, immutable=immutable)
    finally:
        sess.release_lock()
    output(result, f"Checkpoint {name} saved")


@session_group.command("checkpoints")
@handle_error
def session_checkpoints():
    """List accepted checkpoint heads with generation, content hash, files and size."""
    output(plan_mod.list_checkpoints(_proj().dir, get_session().state_dir))


@session_group.command("drop-checkpoint")
@click.argument("name")
@handle_error
def session_drop_checkpoint(name):
    """Drop NAME and all its stored generations."""
    sess = get_session()
    sess._acquire_lock()
    try:
        output(plan_mod.drop_checkpoint(_proj().dir, name, sess.state_dir))
    finally:
        sess.release_lock()


@session_group.command("restore")
@click.argument("name")
@handle_error
def session_restore(name):
    """Restore NAME, validate with KiCad and record undo history. Missing names are input errors."""
    output(plan_mod.restore_checkpoint(get_session(), name, validate_files, dry_run=_dry_run),
           f"Restored checkpoint {name}")


@session_group.command("save")
@handle_error
def session_save():
    output(save_now())


@session_group.command("discard")
@handle_error
def session_discard():
    """Drop unsaved in-memory edits (REPL)."""
    sess = get_session()
    n = len(sess.dirty_docs())
    sess.discard()
    output({"discarded_files": n})


# ═════════════════════════════════════════════════════════════════════════════
# raw (escape hatch)
# ═════════════════════════════════════════════════════════════════════════════

@cli.group("raw")
def raw_group():
    """Escape hatch: S-expression query/edit on any KiCad file, arbitrary pcbnew Python."""


def _raw_path(path: str) -> str:
    sess = get_session()
    if os.path.isabs(path) or os.path.exists(path):
        return os.path.abspath(path)
    if sess.project:
        return os.path.join(sess.project.dir, path)
    return os.path.abspath(path)


@raw_group.command("query")
@click.argument("file")
@click.argument("query")
@click.option("--limit", type=int, default=50)
@click.option("--sexpr", "as_sexpr", is_flag=True, help="Return S-expression text instead of nested lists")
@handle_error
def raw_query(file, query, limit, as_sexpr):
    """Query nodes: 'symbol[2]/property[Reference]', '//footprint/at', 'lib_symbols/symbol'."""
    sess = get_session()
    p = _raw_path(file)
    t = sess.tree(p) if sess.project else sexpr.load(p)
    res = sexpr.query(t, query)
    output({"count": len(res), "matches": [sexpr.compact(n) if as_sexpr else n.to_py() for n in res[:limit]]})


@raw_group.command("set")
@click.argument("file")
@click.argument("query")
@click.argument("values", nargs=-1, required=True)
@click.option("--all", "all_", is_flag=True, help="Apply to all matches (default: exactly one match required)")
@click.option("--quoted", is_flag=True, help="Store values as quoted strings")
@handle_error
def raw_set(file, query, values, all_, quoted):
    """Replace the atoms of matched node(s): `kce raw set board.kicad_pcb 'general/thickness' 1.2`."""
    sess = get_session()
    p = _raw_path(file)
    sess.snapshot(f"raw set {query}", [p])
    t = sess.tree(p)
    res = sexpr.query(t, query)
    if not res:
        raise KeyError(f"No match for {query!r}")
    if len(res) > 1 and not all_:
        raise ValueError(f"{len(res)} matches for {query!r}; refine the query or pass --all")
    for n in res:
        kids = [c for c in n[1:] if isinstance(c, sexpr.SList)]
        n[1:] = [sexpr.Q(v) if quoted else sexpr._atomize(v) for v in values] + kids
    sess.doc(p).mark_dirty()
    output({"file": p, "updated": len(res), "nodes": [sexpr.compact(n)[:300] for n in res[:10]]})


@raw_group.command("delete")
@click.argument("file")
@click.argument("query")
@click.option("--all", "all_", is_flag=True)
@handle_error
def raw_delete(file, query, all_):
    sess = get_session()
    p = _raw_path(file)
    sess.snapshot(f"raw delete {query}", [p])
    t = sess.tree(p)
    res = sexpr.query(t, query)
    if not res:
        raise KeyError(f"No match for {query!r}")
    if len(res) > 1 and not all_:
        raise ValueError(f"{len(res)} matches; pass --all")
    n = 0
    targets = {id(x) for x in res}
    for parent in list(sexpr.walk(t)):
        before = len(parent)
        parent[:] = [c for c in parent if id(c) not in targets]
        n += before - len(parent)
    sess.doc(p).mark_dirty()
    output({"file": p, "deleted": n})


@raw_group.command("append")
@click.argument("file")
@click.argument("query")
@click.argument("sexpr_text")
@handle_error
def raw_append(file, query, sexpr_text):
    """Append a child S-expression to the matched node ('' or '/' = root)."""
    sess = get_session()
    p = _raw_path(file)
    node = sexpr.loads(sexpr_text)
    sess.snapshot(f"raw append {query}", [p])
    t = sess.tree(p)
    res = [t] if query in ("", "/") else sexpr.query(t, query)
    if len(res) != 1:
        raise ValueError(f"Query must match exactly one node, matched {len(res)}")
    res[0].append(node)
    sess.doc(p).mark_dirty()
    output({"file": p, "appended": sexpr.compact(node)[:300]})


@raw_group.command("check")
@click.argument("file")
@handle_error
def raw_check(file):
    """Parse a KiCad file and report whether our writer round-trips it byte-for-byte."""
    p = _raw_path(file)
    txt = open(p, encoding="utf-8").read()
    t = sexpr.loads(txt)
    out = sexpr.dumps(t, sexpr.mode_for_path(p))
    output({"file": p, "root": t.tag, "version": t.value("version"), "generator_version": t.value("generator_version"),
            "nodes": sum(1 for _ in sexpr.walk(t)), "lossless_roundtrip": out == txt})


@raw_group.command("pcbnew")
@click.option("--code", "-c", default=None, help="Python code; `board` is the loaded BOARD; set `result`.")
@click.option("--file", "script", type=click.Path(exists=True), default=None)
@click.option("--readonly", is_flag=True, help="Do not save the board afterwards")
@click.option("--param", "params", multiple=True, help="KEY=VALUE available as params[KEY]")
@handle_error
def raw_pcbnew(code, script, readonly, params):
    """Run arbitrary pcbnew Python against the board (in the pcbnew interpreter)."""
    if not code and not script:
        raise ValueError("Give --code or --file")
    src = code or open(script, encoding="utf-8").read()
    sess = get_session()
    act = {"action": "script", "code": src, "params": parse_kv(params), "filename": script or "<code>"}
    if readonly:
        proj = _proj()
        with materialize(sess) as ws:
            output(kb.run_pcbnew("query_script", dict(act, path=remap(pcb_mod.board_path(sess), proj.dir, ws))))
    else:
        output(pcb_mod.edit1(sess, act, "pcbnew script"))


# advanced commands (teardrops, stitching, diff pairs, 3D models, simulation, GUI, panels, ...)
from . import cli_advanced  # noqa: E402,F401  (registers commands on the groups above)

# ═════════════════════════════════════════════════════════════════════════════
# kicad-cli passthrough (every subcommand & option), attached under the groups
# ═════════════════════════════════════════════════════════════════════════════

KICAD_CLI_COMMANDS = kicad_cli_cmds.attach_all(cli, {"session": get_session, "output": lambda d, m="": output(d, m),
                                                     "handle_error": handle_error})


# ═════════════════════════════════════════════════════════════════════════════
# REPL
# ═════════════════════════════════════════════════════════════════════════════

@cli.command("repl")
@click.argument("project_path", required=False, type=click.Path())
@handle_error
def repl(project_path):
    """Interactive shell, or commands from piped stdin.

    With piped stdin and --json, print one compact JSON line per command,
    including errors and help. quit/exit/q prints {"quit": true}. Blank lines
    and comments produce no result. Use project save to persist pending edits.
    """
    global _repl_mode, _had_error
    from .repl import Shell
    sess = get_session()
    if project_path and sess.project is None:
        sess.set_project(proj_mod.resolve_project(project_path))
    groups = {name: (cmd.short_help or (cmd.help or "").split("\n")[0]).strip()
              for name, cmd in sorted(cli.commands.items()) if isinstance(cmd, click.Group)}
    json_lines = _json_output and not sys.stdin.isatty()

    def emit(data):
        click.echo(json.dumps(data, default=str, ensure_ascii=not _utf8_stream(sys.stdout), separators=(",", ":")))

    def report_error(exc):
        emit(error_payload(exc))

    def execute(argv: List[str]) -> None:
        global _had_error
        _had_error = False
        if json_lines:
            try:
                emit(run_in_process(argv, session=sess))
            except Exception as e:
                report_error(e)
            return
        try:
            cli.main(argv, standalone_mode=False, prog_name=COMMAND)
        except SystemExit:
            pass
        except click.exceptions.UsageError as e:
            click.echo(f"Error: {e.format_message()}", err=True)
        except click.exceptions.Abort:
            pass

    def state():
        return (sess.project.name if sess.project else "", len(sess.dirty_docs()))

    extra = f"Project: {sess.project.pro_path}" if sess.project else "No project loaded (project new|open)."
    extra += f"\nAgent skill: {skill_mod.PACKAGED_DIR / 'SKILL.md'}"
    previous = _repl_mode
    _repl_mode = True
    try:
        Shell(execute, state, groups, history_file=os.path.join(kb.data_dir(), "history"), banner_extra=extra,
              json_mode=json_lines, on_error=report_error if json_lines else None).run()
    finally:
        sess.discard()
        _repl_mode = previous


# ═════════════════════════════════════════════════════════════════════════════
# skill
# ═════════════════════════════════════════════════════════════════════════════

@cli.group("skill")
def skill_group():
    """Install the bundled AI-agent skill (SKILL.md) into agent skill directories."""


TARGET_HELP = ("Target agent: " + ", ".join(sorted(skill_mod.TARGETS)) +
               ", auto (agents present on this machine + ~/.agents/skills) or all. Repeatable. "
               "Default: auto, unless only --dir is given; combine with --target auto --dir DIR.")


@skill_group.command("install")
@click.option("--target", "-t", "targets", multiple=True, help=TARGET_HELP)
@click.option("--dir", "dirs", multiple=True, type=click.Path(), help="Extra skills directory (repeatable).")
@click.option("--dry-run", "skill_dry_run", is_flag=True, help="Show what would be written.")
@click.option("--force", is_flag=True, help="Overwrite a different skill with the same directory name.")
@handle_error
def skill_install(targets, dirs, skill_dry_run, force):
    """Copy SKILL.md + COMMANDS.md into <skills dir>/kicad-cli-extended/."""
    r = skill_mod.install(targets, dirs, dry_run=skill_dry_run or _dry_run, force=force)
    output(r, ("Would install" if r["dry_run"] else "Installed") + f" skill {r['skill']}:")


@skill_group.command("uninstall")
@click.option("--target", "-t", "targets", multiple=True, help="Default: all known targets.")
@click.option("--dir", "dirs", multiple=True, type=click.Path())
@click.option("--dry-run", "skill_dry_run", is_flag=True)
@handle_error
def skill_uninstall(targets, dirs, skill_dry_run):
    """Remove the installed skill directories (only ones that hold this skill)."""
    output(skill_mod.uninstall(targets, dirs, dry_run=skill_dry_run or _dry_run))


@skill_group.command("status")
@handle_error
def skill_status():
    """Where the skill is installed and whether each copy is current."""
    output(skill_mod.status())


@skill_group.command("path")
@handle_error
def skill_path():
    """Directory of the packaged skill files."""
    output({"dir": str(skill_mod.PACKAGED_DIR), "files": [str(p) for p in skill_mod.packaged_files()]})


@skill_group.command("show")
@click.option("--file", "name", type=click.Choice(list(skill_mod.SKILL_FILES)), default="SKILL.md")
@handle_error
def skill_show(name):
    """Print the packaged SKILL.md (or COMMANDS.md)."""
    text = (skill_mod.PACKAGED_DIR / name).read_text(encoding="utf-8")
    if _json_output:
        output({"file": name, "text": text})
    else:
        click.echo(text)


def _usage_failure(e: click.ClickException) -> None:
    """Report a click parse failure (bad option, missing argument, unknown command) and exit 2."""
    ctx = getattr(e, "ctx", None)
    if "--json" in sys.argv[1:]:
        hint = f"{ctx.command_path} --help" if ctx is not None else f"{COMMAND} --help"
        click.echo(json.dumps({"error": e.format_message(), "type": "UsageError", "category": "input",
                               "hint": hint}, indent=2), err=True)
    else:
        e.show()
    sys.exit(EXIT_CODES["input"])


def main():
    configure_stdio()
    try:
        # the root group's result callback returns None, so a returned int is only ever the
        # exit code of --help / --version
        rc = cli(prog_name=COMMAND, standalone_mode=False)
    except click.ClickException as e:
        _usage_failure(e)
    except click.Abort:
        click.echo("Aborted!", err=True)
        sys.exit(1)
    sys.exit(rc if isinstance(rc, int) else 0)


if __name__ == "__main__":
    main()
