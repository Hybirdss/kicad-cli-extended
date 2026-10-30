"""Transactions: ``kce run`` plans, file snapshots and named checkpoints.

A plan is one JSON document that runs several kce commands as a single unit:
the steps share one session and one project, KiCad validates the result once
at the end, and any failure restores every project file to the bytes it had
before the first step.  ``session checkpoint`` / ``session restore`` reuse the
same file capture, stored by name under ``<project>/<state>/checkpoints``.
"""

from __future__ import annotations

import json
import hashlib
import uuid
import os
import re
import time
from typing import Any, Dict, List

from .. import APP_NAME, STATE_DIR

PLAN_VERSION = 1

# VCS metadata and bytecode are outside the project snapshot.
SKIP_DIRS = {"__pycache__", ".git"}

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


class PlanError(RuntimeError):
    """A plan step (or its final validation) failed; every file was rolled back.

    Carries the failing step's own kce error object, so the caller keeps the
    original ``type``/``category`` and exit code.
    """

    def __init__(self, payload: Dict[str, Any], *, step: int | None, args: List[str], steps: int, results: List[Dict[str, Any]],
                 phase: str = "step", rolled_back: bool = True):
        super().__init__(payload["error"])
        self.payload = dict(payload)
        self.type = payload.get("type", "InternalError")
        self.category = payload.get("category", "internal")
        self.step = step
        self.step_args = list(args)
        self.steps = steps
        self.results = results
        self.phase = phase
        self.rolled_back = rolled_back

    def details(self) -> Dict[str, Any]:
        out = dict(self.payload.get("details") or {})
        out.update({"step": self.step, "args": self.step_args, "steps": self.steps, "rolled_back": self.rolled_back,
                    "phase": self.phase, "results": self.results, "commit_state": "rolled_back" if self.rolled_back else "unknown"})
        return out


# ---------------------------------------------------------------------------
# Plan document
# ---------------------------------------------------------------------------


def validate_plan(data: Any) -> Dict[str, Any]:
    """Reject a malformed plan with an ``input`` error naming what to fix."""
    if not isinstance(data, dict):
        raise ValueError("A plan is a JSON object: {\"version\": 1, \"steps\": [{\"args\": [...]}]}")
    version = data.get("version")
    if type(version) is not int or version != PLAN_VERSION:
        raise ValueError(f"Unsupported plan version {version!r}; this {APP_NAME} runs version {PLAN_VERSION} "
                         "(see schemas/plan.schema.json)")
    extra = set(data) - {"version", "steps", "project"}
    if extra:
        raise ValueError(f"Unknown plan key(s) {sorted(extra)}; allowed: version, steps, project")
    project = data.get("project")
    if "project" in data and (not isinstance(project, str) or not project):
        raise ValueError("Plan \"project\" must be a non-empty path string when given")
    steps = data.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError("Plan \"steps\" must be a non-empty array of {\"args\": [...]}")
    for i, step in enumerate(steps, 1):
        if not isinstance(step, dict) or "args" not in step or set(step) - {"args", "expect"}:
            raise ValueError(f"Step {i} must be an object with args and optional expect")
        predicates = step.get("expect", [])
        if not isinstance(predicates, list):
            raise ValueError(f"Step {i}: expect must be an array; see `kce run --help`.")
        for predicate in predicates:
            validate_predicate(predicate, i)
        args = step["args"]
        if not isinstance(args, list) or not args or not all(isinstance(a, str) for a in args):
            raise ValueError(f"Step {i}: \"args\" must be a non-empty array of strings (e.g. [\"sch\", \"add\", ...])")
        if "--help" in args or "--version" in args:
            raise ValueError(f"Step {i}: help and version flags do not execute a step; see `kce run --help`.")
        option_args = args[:args.index('--')] if '--' in args else args
        global_options = {'--project', '-p', '--session-dir', '--dry-run', '--read-only', '--no-validate', '--progress'}
        if any(a in global_options or any(a.startswith(option + '=') for option in global_options)
               or a.startswith('-p') and not a.startswith('--') for a in option_args):
            raise ValueError(f"Step {i}: global transaction options belong on kce run, not inside a step; see `kce run --help`.")
        command = args[1:] if args[0] == "--json" else args
        if len(command) < 2 or command[0].startswith("-"):
            raise ValueError(f"Step {i}: give a command; put --project, --dry-run and --no-validate on `kce run`.")
        if command[0] not in {"sch", "pcb", "project", "session", "raw", "check", "info", "lib", "fab", "sim"}:
            raise ValueError(f"Step {i}: {command[0]!r} is not a project transaction command; see `kce run --help`.")
        if (command[:2] in (["project", "new"], ["project", "open"], ["project", "unarchive"],
                           ["sch", "normalize"], ["raw", "pcbnew"])
                or (command[0] == "session" and command[1] not in {"status", "history", "save"})):
            raise ValueError(f"Step {i}: {' '.join(command[:2])} cannot run inside a plan; see `kce run --help`.")
    return {"version": PLAN_VERSION, "steps": steps, **({"project": project} if project else {})}


def validate_step(cli, args: List[str], root: str) -> None:
    """Parse once at the boundary, before any step can write outside the project."""
    import click

    try:
        ctx = cli.make_context("kce", args.copy())
        while isinstance(ctx.command, click.Group):
            protected = getattr(ctx, "_protected_args", None)
            rest = [*(protected if protected is not None else ctx.protected_args), *ctx.args]
            if not rest:
                raise ValueError("Plan steps must name a command; see `kce run --help`.")
            name, command, remaining = ctx.command.resolve_command(ctx, rest)
            ctx = command.make_context(name, remaining, parent=ctx)
        from .contract import effect
        path = " ".join(ctx.command_path.split()[1:])
        if effect(path, ctx.params) == "control" or path in {"lib init-global-tables", "lib index", "sim run"}:
            raise ValueError(f"{path} cannot run inside a transaction; see `kce run --help`.")
        if ctx.params.get("scope") == "global":
            raise ValueError("Plan steps cannot change global libraries; use `kce run --help`.")
        if ctx.command.name in {"upgrade", "normalize"} or ctx.params.get("save_board"):
            raise ValueError("Direct upgrades cannot run in a plan; use `kce run --help`.")
        for param in ctx.command.params:
            names = set(param.opts)
            is_path = isinstance(param.type, click.Path) or param.name in {"file", "filename", "out", "output", "output_dir", "root_dir", "pretty_dir", "library_file"}
            is_path = is_path or bool(names & {"--output", "--file", "--output-dir", "--root-dir", "--report-file"})
            value = ctx.params.get(param.name)
            if not is_path or not value:
                continue
            for path in value if isinstance(value, (list, tuple)) else [value]:
                if param.name == "file" and not os.path.isabs(path) and not os.path.exists(path):
                    path = os.path.join(root, path)
                elif param.name == "filename":
                    path = os.path.join(root, path)
                if inside_project(path, root) and not transaction_path(path, root):
                    raise ValueError(f"Plan paths cannot access VCS or session metadata: {path}; use `kce run --help`.")
                if not inside_project(path, root):
                    raise ValueError(f"Plan path must stay inside the project: {path}; use `kce run --help`.")
    except click.ClickException as e:
        raise ValueError(f"{e.format_message()}; see `kce run --help`.") from e


def inside_project(path: str, root: str) -> bool:
    path, root = os.path.normcase(os.path.realpath(path)), os.path.normcase(os.path.realpath(root))
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        return False


def transaction_path(path: str, root: str) -> bool:
    if not inside_project(path, root):
        return False
    relative = os.path.relpath(os.path.realpath(path), os.path.realpath(root))
    return relative.split(os.sep)[0] not in {'.git', STATE_DIR}


def load_plan(path: str) -> Dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        raise FileNotFoundError(f"Plan not found: {path}")
    except json.JSONDecodeError as e:
        raise ValueError(f"Plan {path} is not valid JSON: {e}")
    return validate_plan(data)


# ---------------------------------------------------------------------------
# File capture (rollback and checkpoints)
# ---------------------------------------------------------------------------


def _project_files(root: str, include_state: bool = False) -> List[str]:
    """Absolute paths of every regular file a transaction may touch, sorted."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and (include_state or d not in {STATE_DIR, "outputs"}))
        if any(os.path.islink(os.path.join(dirpath, d)) for d in dirnames):
            raise ValueError("Project snapshots do not support symlink directories; use `kce run --help`.")
        for name in sorted(filenames):
            if name.endswith(".lck") or name == ".git" or (dirpath == os.path.join(root, STATE_DIR) and name == "lock"):
                continue
            path = os.path.join(dirpath, name)
            if os.path.islink(path):
                raise ValueError(f"Project snapshots do not support symlinks: {path}; use `kce run --help`.")
            if os.path.isfile(path):
                out.append(path)
    return out


def snapshot_files(root: str, include_state: bool = False) -> Dict[str, bytes]:
    """Capture the bytes of every project file, keyed by path relative to ``root``."""
    root = os.path.abspath(root)
    snap: Dict[str, bytes] = {}
    for path in _project_files(root, include_state):
        rel = os.path.relpath(path, root).replace(os.sep, "/")
        with open(path, "rb") as f:
            snap[rel] = f.read()
    return snap


def restore_files(root: str, snap: Dict[str, bytes], include_state: bool = False) -> Dict[str, List[str]]:
    """Put the captured bytes back; delete files that appeared after the capture."""
    root = os.path.abspath(root)
    from .session import atomic_write_bytes

    restored, deleted = [], []
    for path in _project_files(root, include_state):
        rel = os.path.relpath(path, root).replace(os.sep, "/")
        if rel not in snap:
            os.unlink(path)
            deleted.append(rel)
    for rel, data in sorted(snap.items()):
        path = os.path.join(root, rel.replace("/", os.sep))
        current = None
        if os.path.exists(path):
            with open(path, "rb") as f:
                current = f.read()
        if current != data:
            atomic_write_bytes(path, data)
            restored.append(rel)
    return {"restored": restored, "deleted": deleted}


# ---------------------------------------------------------------------------
# Named checkpoints
# ---------------------------------------------------------------------------


def _check_name(name: str) -> str:
    if not isinstance(name, str) or not _NAME.fullmatch(name) or name in (".", ".."):
        raise ValueError(f"Invalid checkpoint name {name!r}: use letters, digits, '.', '_' or '-'")
    return name


def checkpoints_dir(project_dir: str, state_dir=None) -> str:
    from .session import state_directory
    return os.path.join(state_dir or state_directory(project_dir), "checkpoints")


def checkpoint_dir(project_dir: str, name: str, state_dir=None) -> str:
    return os.path.join(checkpoints_dir(project_dir, state_dir), _check_name(name))


def content_sha256(files):
    digest = hashlib.sha256()
    for rel, data in sorted(files.items()):
        key = rel.encode("utf-8")
        digest.update(len(key).to_bytes(8, 'big') + key + len(data).to_bytes(8, 'big') + data)
    return digest.hexdigest()


def save_checkpoint(project_dir: str, name: str, *, state_dir=None, immutable=False) -> Dict[str, Any]:
    """Publish an immutable generation, then atomically switch the named head.

    Failed or interrupted writes leave the previous head and its generation intact.
    Old generations remain available on disk until the checkpoint is dropped.
    """
    from .session import atomic_write_bytes
    name = _check_name(name)
    dest = checkpoint_dir(project_dir, name, state_dir)
    if immutable and (os.path.isfile(os.path.join(dest, 'head.json')) or os.path.isfile(os.path.join(dest, 'manifest.json'))):
        raise FileExistsError(f"Checkpoint {name!r} already exists; choose a new immutable name")
    files = snapshot_files(project_dir)
    generation = uuid.uuid4().hex
    staging = os.path.join(dest, 'generations', generation)
    os.makedirs(os.path.join(staging, 'files'), exist_ok=True)
    for rel, data in files.items():
        path = os.path.join(staging, 'files', rel.replace('/', os.sep))
        atomic_write_bytes(path, data)
    manifest = {'version': 1, 'name': name, 'created': time.time(), 'project': os.path.abspath(project_dir),
                'generation': generation, 'content_sha256': content_sha256(files),
                'files': sorted(files), 'bytes': sum(len(d) for d in files.values())}
    atomic_write_bytes(os.path.join(staging, 'manifest.json'), json.dumps(manifest, indent=1).encode('utf-8'))
    # This is the only mutation of the accepted head; no remove-before-rename window.
    atomic_write_bytes(os.path.join(dest, 'head.json'), json.dumps({'generation': generation}).encode('utf-8'))
    return {'checkpoint': name, 'dir': dest, **{k: manifest[k] for k in
            ('created', 'files', 'bytes', 'generation', 'content_sha256')}}


def _rmtree(path: str) -> None:
    import shutil
    shutil.rmtree(path, ignore_errors=True)


def read_checkpoint(project_dir: str, name: str, *, state_dir=None) -> Dict[str, Any]:
    """Load a checkpoint's manifest and file bytes; a missing name is an input error."""
    name = _check_name(name)
    dest = checkpoint_dir(project_dir, name, state_dir)
    head = os.path.join(dest, 'head.json')
    if os.path.isfile(head):
        with open(head, encoding='utf-8') as f:
            generation = json.load(f).get('generation')
        if not isinstance(generation, str) or not re.fullmatch('[a-f0-9]{32}', generation):
            raise ValueError(f"Invalid checkpoint head {name!r}")
        dest = os.path.join(dest, 'generations', generation)
    manifest_path = os.path.join(dest, "manifest.json")
    if not os.path.exists(manifest_path):
        known = sorted(os.listdir(checkpoints_dir(project_dir, state_dir))) if os.path.isdir(checkpoints_dir(project_dir, state_dir)) else []
        raise ValueError(f"No checkpoint {name!r} in {project_dir} (have: {known or 'none'}); "
                         f"save one with `kce session checkpoint NAME`")
    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)
    files = {}
    if not isinstance(manifest, dict) or manifest.get("version") != 1 or not isinstance(manifest.get("files"), list):
        raise ValueError(f"Invalid checkpoint {name!r}; create it again with `kce session checkpoint NAME`.")
    for rel in manifest["files"]:
        if (not isinstance(rel, str) or not rel or "\\" in rel or ":" in rel
                or any(part in ("", ".", "..", STATE_DIR, ".git") for part in rel.split("/"))):
            raise ValueError(f"Invalid checkpoint file path {rel!r}; use `kce session checkpoint NAME`.")
        with open(os.path.join(dest, "files", rel.replace("/", os.sep)), "rb") as f:
            files[rel] = f.read()
    digest = content_sha256(files)
    if manifest.get('content_sha256', digest) != digest:
        from .session import ValidationError
        raise ValidationError(f"Checkpoint {name!r} content hash mismatch; restore an intact generation instead")
    manifest.setdefault('generation', 'legacy')
    manifest.setdefault('content_sha256', digest)
    manifest.setdefault('bytes', sum(len(d) for d in files.values()))
    return {"manifest": manifest, "files": files}


def restore_checkpoint(sess, name: str, validator, *, dry_run: bool = False) -> Dict[str, Any]:
    """Restore file bytes, additions and deletions through the session's undo history."""
    root = sess.require_project().dir
    with sess.transaction(validator, dry_run=dry_run) as saved:
        cp = read_checkpoint(root, name, state_dir=sess.state_dir)
        before = snapshot_files(root)
        for doc in sess.dirty_docs():
            if not inside_project(doc.path, root):
                raise ValueError("Checkpoint restore needs project-local edits; use `kce session discard` first.")
            rel = os.path.relpath(doc.path, root).replace(os.sep, "/")
            data = sess.current_bytes(doc.path)
            if data is None:
                before.pop(rel, None)
            else:
                before[rel] = data
        wanted = cp["files"]
        changed = sorted(rel for rel in before.keys() | wanted.keys() if before.get(rel) != wanted.get(rel))
        paths = [os.path.join(root, rel.replace("/", os.sep)) for rel in changed]
        if paths:
            sess.snapshot(f"restore checkpoint {name}", paths)
        for rel, path in zip(changed, paths):
            doc = sess.doc(path)
            doc.set_bytes(wanted.get(rel, b""))
            doc._deleted = rel not in wanted
    return {"checkpoint": name, "generation": cp["manifest"]["generation"],
            "content_sha256": cp["manifest"]["content_sha256"], "files": sorted(wanted), "dry_run": dry_run,
            "restored": [rel for rel in changed if rel in wanted],
            "deleted": [rel for rel in changed if rel not in wanted], **saved}


PREDICATE_OPS = {"eq", "ne", "lt", "le", "gt", "ge", "empty", "nonempty", "true", "false"}


def validate_predicate(predicate, step):
    if (not isinstance(predicate, dict) or set(predicate) - {"path", "op", "value"}
            or not isinstance(predicate.get("path"), str) or not predicate["path"]
            or not isinstance(predicate.get("op"), str)
            or predicate["op"] not in PREDICATE_OPS):
        raise ValueError(f"Step {step}: expect needs path and a supported op {sorted(PREDICATE_OPS)}; see `kce run --help`.")
    if predicate["op"] in {"eq", "ne", "lt", "le", "gt", "ge"} and "value" not in predicate:
        raise ValueError(f"Step {step}: expect {predicate['op']} requires value; see `kce run --help`.")


class AcceptanceFailed(RuntimeError):
    def __init__(self, predicate, actual, missing=False):
        super().__init__(f"Acceptance failed: {predicate['path']} {predicate['op']} {predicate.get('value', '')}; "
                         "transaction rolled back. Inspect `kce run --help`.")
        self.details = {"predicate": predicate, "actual": actual, "missing": missing}


def accept(result, predicates):
    """Evaluate explicit result predicates before the transaction can commit."""
    import operator
    for predicate in predicates:
        actual, missing = result, False
        for part in predicate['path'].split('.'):
            if isinstance(actual, dict) and part in actual:
                actual = actual[part]
            elif isinstance(actual, list) and part.isdecimal() and int(part) < len(actual):
                actual = actual[int(part)]
            else:
                actual, missing = None, True
                break
        op, value = predicate['op'], predicate.get('value')
        passed = False
        if not missing:
            if op in {'eq', 'ne'}:
                equal = actual == value and (not isinstance(actual, bool) and not isinstance(value, bool)
                                             or type(actual) is type(value))
                passed = equal if op == 'eq' else not equal
            elif op in {'lt', 'le', 'gt', 'ge'}:
                if type(actual) in (int, float) and type(value) in (int, float):
                    passed = getattr(operator, op)(actual, value)
            elif op in {'true', 'false'}:
                passed = actual is (op == 'true')
            elif isinstance(actual, (str, list, dict)):
                passed = (len(actual) == 0) if op == 'empty' else (len(actual) > 0)
        if not passed:
            raise AcceptanceFailed(predicate, actual, missing)


def list_checkpoints(project_dir, state_dir=None):
    root = checkpoints_dir(project_dir, state_dir)
    if not os.path.isdir(root):
        return []
    return [{k: data['manifest'][k] for k in ('name', 'created', 'files', 'bytes', 'generation', 'content_sha256')}
            for name in sorted(os.listdir(root)) if not name.startswith('.')
            if any(os.path.isfile(os.path.join(root, name, f)) for f in ('head.json', 'manifest.json'))
            for data in [read_checkpoint(project_dir, name, state_dir=state_dir)]]


def drop_checkpoint(project_dir, name, state_dir=None):
    # Validate/read the head before detaching it; a missing name remains input/2.
    read_checkpoint(project_dir, name, state_dir=state_dir)
    source = checkpoint_dir(project_dir, name, state_dir)
    trash = os.path.join(checkpoints_dir(project_dir, state_dir), '.' + uuid.uuid4().hex)
    os.replace(source, trash)
    _rmtree(trash)
    return {'dropped': name}
