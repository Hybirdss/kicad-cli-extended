"""KiCad jobsets (.kicad_jobset, JSON): create, inspect, edit and run via kicad-cli.

Job types and their setting keys come from ``utils/jobset_catalog.json``,
extracted from KiCad 10.0.7 ``common/jobs`` (scripts/gen_jobset_catalog.py).
Missing settings take KiCad's defaults (JOB_PARAM::FromJson uses j.value(key, default)).
"""

from __future__ import annotations

import json
import re
import os
import time
import uuid
from typing import Any, Dict, List, Optional

from .workspace import materialize, remap
from ..utils import kicad_backend as kb
from ..utils.paths import rel_inside
from ..utils.verify import describe_outputs

CATALOG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "utils", "jobset_catalog.json")
SCHEMA_VERSION = 1


def catalog() -> Dict[str, Any]:
    with open(CATALOG_PATH, encoding="utf-8") as f:
        return json.load(f)


def job_types() -> List[Dict[str, Any]]:
    cat = catalog()["jobs"]
    return [{"type": k, "title": v["title"], "deprecated": v["deprecated"],
             "settings": [p["key"] for p in v["settings"]]} for k, v in sorted(cat.items())]


def job_type_info(jtype: str) -> Dict[str, Any]:
    cat = catalog()["jobs"]
    if jtype not in cat:
        raise KeyError(f"Unknown job type {jtype!r}. Known: {sorted(cat)}")
    return dict(cat[jtype], type=jtype)


def jobset_path(sess, name: Optional[str]) -> str:
    proj = sess.require_project()
    if name and (os.path.isabs(name) or os.sep in name or name.endswith(".kicad_jobset")):
        p = name if name.endswith(".kicad_jobset") else name + ".kicad_jobset"
        return os.path.abspath(p if os.path.isabs(p) else os.path.join(proj.dir, p))
    return os.path.join(proj.dir, (name or proj.name) + ".kicad_jobset")


def load(sess, path: str) -> Dict[str, Any]:
    if not os.path.exists(path) and not sess.has_doc(path):
        raise FileNotFoundError(f"Jobset not found: {path} (create with `kce jobset new`)")
    return sess.doc(path, "json").json()


def new(sess, name: Optional[str] = None, overwrite: bool = False) -> Dict[str, Any]:
    path = jobset_path(sess, name)
    if os.path.exists(path) and not overwrite:
        raise FileExistsError(f"{path} exists (use --overwrite)")
    sess.snapshot(f"new jobset {os.path.basename(path)}", [path])
    d = sess.doc(path, "json")
    d._json = {"jobs": [], "outputs": [], "meta": {"version": SCHEMA_VERSION}}
    d.mark_dirty()
    return {"jobset": path}


def _coerce(v: str) -> Any:
    try:
        return json.loads(v)
    except (json.JSONDecodeError, TypeError):
        return v


def add_job(sess, path: str, jtype: str, settings: Dict[str, str], description: str = "",
            job_id: Optional[str] = None) -> Dict[str, Any]:
    info = job_type_info(jtype)
    keys = {p["key"]: p for p in info["settings"]}
    clean = {}
    for k, v in settings.items():
        if k not in keys:
            raise KeyError(f"{jtype} has no setting {k!r}. Valid: {sorted(keys)}")
        val = _coerce(v)
        ch = keys[k].get("choices")
        if ch and val not in ch:
            raise ValueError(f"{jtype}.{k} must be one of {ch}")
        clean[k] = val
    if jtype == "pcb_render":
        # KiCad 10 quirk: JOB_PCB_RENDER defaults to 0x0 px and then fails; use kicad-cli's defaults.
        clean.setdefault("width", 1600)
        clean.setdefault("height", 900)
    data = load(sess, path)
    sess.snapshot(f"jobset add {jtype}", [path])
    jid = job_id or str(uuid.uuid4())
    if any(j["id"] == jid for j in data["jobs"]):
        raise ValueError(f"Job id {jid} already exists")
    data["jobs"].append({"id": jid, "type": jtype, "description": description, "settings": clean})
    sess.doc(path).mark_dirty()
    return {"id": jid, "type": jtype, "settings": clean}


def add_output(sess, path: str, kind: str, output_path: str, only: Optional[List[str]] = None,
               description: str = "") -> Dict[str, Any]:
    if kind not in ("folder", "archive"):
        raise ValueError("output kind must be folder|archive")
    data = load(sess, path)
    ids = {j["id"] for j in data["jobs"]}
    for j in only or []:
        if j not in ids:
            raise KeyError(f"Job id {j} not in jobset; ids: {sorted(ids)}")
    sess.snapshot(f"jobset output {kind}", [path])
    oid = str(uuid.uuid4())
    settings = {"output_path": output_path}
    if kind == "archive":
        settings["format"] = "zip"
    data["outputs"].append({"id": oid, "type": kind, "only": only or [], "description": description,
                            "settings": settings})
    sess.doc(path).mark_dirty()
    return {"id": oid, "type": kind, "settings": settings, "only": only or []}


def remove(sess, path: str, item_id: str) -> Dict[str, Any]:
    data = load(sess, path)
    sess.snapshot(f"jobset remove {item_id}", [path])
    nj = [j for j in data["jobs"] if j["id"] != item_id]
    no = [o for o in data["outputs"] if o["id"] != item_id]
    if len(nj) == len(data["jobs"]) and len(no) == len(data["outputs"]):
        raise KeyError(f"No job/output with id {item_id}")
    for o in no:
        o["only"] = [x for x in o.get("only", []) if x != item_id]
    data["jobs"], data["outputs"] = nj, no
    sess.doc(path).mark_dirty()
    return {"removed": item_id}


def show(sess, path: str) -> Dict[str, Any]:
    data = load(sess, path)
    cat = catalog()["jobs"]
    return {"path": path, "jobs": [dict(j, title=cat.get(j["type"], {}).get("title")) for j in data["jobs"]],
            "outputs": data["outputs"]}


def run(sess, path: str, output_id: Optional[str] = None, stop_on_error: bool = False) -> Dict[str, Any]:
    proj = sess.require_project()
    data = load(sess, path)
    started = time.time()
    monotonic_started = time.monotonic()
    with materialize(sess) as ws:
        args = ["jobset", "run", "--file", remap(path, proj.dir, ws)]
        if stop_on_error:
            args.append("--stop-on-error")
        if output_id:
            args += ["--output", output_id]
        # relative destination paths are relative to the project directory (as in the GUI)
        r = kb.run_kicad_cli(args + [remap(proj.pro_path, proj.dir, ws)], check=False, cwd=ws)
        produced = []
        for o in data["outputs"]:
            op = o["settings"].get("output_path", "")
            op = op if os.path.isabs(op) else os.path.join(ws, op)
            # copy results out of a temp workspace into the real project
            rel = rel_inside(op, ws)
            if ws != proj.dir and rel is not None and os.path.exists(op):
                real = os.path.join(proj.dir, rel)
                import shutil
                if os.path.isdir(op):
                    shutil.copytree(op, real, dirs_exist_ok=True)
                else:
                    os.makedirs(os.path.dirname(real), exist_ok=True)
                    shutil.copy2(op, real)
                op = real
            produced.append({"output": o["id"], "type": o["type"], "path": op,
                             "files": describe_outputs(op, since=started - 1) if os.path.exists(op) else []})
    ok_line = [l for l in r["stdout"].splitlines() if "succeeded" in l]
    return {"jobset": path, "returncode": r["returncode"], "summary": [l.strip() for l in ok_line],
            "failed_jobs": max(r["stdout"].count("Job failed"), int(r["returncode"] == 6)),
            "failed": failures_from_log(data, r["stdout"], r["stderr"]),
            "command": r["command"], "elapsed_ms": round((time.monotonic() - monotonic_started) * 1000, 3),
            "outputs": produced, "stdout": r["stdout"][-3000:], "stderr": r["stderr"][-2000:]}


def fab_template(sess, path: str, output_dir: str = "jobset-out") -> Dict[str, Any]:
    """A ready-made fabrication jobset: ERC, DRC, gerbers, drill, pos, BOM, PDF, STEP, render + zip."""
    jobs = [("sch_erc", {"format": "json"}), ("pcb_drc", {"format": "json"}), ("pcb_export_gerbers", {}),
            ("pcb_export_drill", {}), ("pcb_export_pos", {"format": "csv", "units": "mm"}),
            ("sch_export_bom", {}), ("sch_export_plot_pdf", {}), ("pcb_export_3d", {"format": "step"}),
            ("pcb_render", {})]
    ids = []
    for jt, st in jobs:
        ids.append(add_job(sess, path, jt, {k: json.dumps(v) for k, v in st.items()})["id"])
    add_output(sess, path, "folder", output_dir)
    add_output(sess, path, "archive", os.path.join(output_dir, "fab.zip"), only=ids[2:4])
    return show(sess, path)


class JobsetFailed(RuntimeError):
    """A completed jobset contains failed jobs; stdout retains its complete result."""
    def __init__(self, result):
        super().__init__(f"Jobset failed: {result['failed_jobs']} failed job(s); inspect stdout outputs and retry `kce jobset execute --help`")
        self.details = {"failed": result.get("failed", []), "returncode": result.get("returncode")}


def failures_from_log(data, stdout, stderr):
    """KiCad identifies running jobs by destination and ordinal, not by UUID."""
    selected = data['jobs']
    current, title, lines, failed = None, None, [], []
    outputs = {o['id']: o for o in data.get('outputs', [])}
    for line in stdout.splitlines():
        destination = re.search(r'Running jobs for destination (\S+)', line)
        if destination:
            only = outputs.get(destination.group(1), {}).get('only', [])
            selected = [j for j in data['jobs'] if not only or j['id'] in only]
            current, title, lines = None, None, []
        running = re.search(r'Running job (\d+):\s*(.*)', line)
        if running:
            index = int(running.group(1)) - 1
            current = selected[index] if 0 <= index < len(selected) else None
            title, lines = running.group(2).strip(), []
        lines.append(line)
        if 'Job failed' in line:
            failed.append({'job_id': current['id'] if current else None,
                           'type': current['type'] if current else None,
                           'title': title, 'message': ('\n'.join(lines[-10:]) + '\n' + stderr[-2000:]).strip()})
    return failed
