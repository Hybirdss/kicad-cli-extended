#!/usr/bin/env python3
"""Token/time benchmark: `kce` (CLI + skill) vs. public KiCad MCP servers.

1. Context overhead: what an agent loads before doing any work.
   MCP: tools/list (+ resources, resource templates, prompts) as returned to the client.
   kce: the skill frontmatter (always in the system prompt) and the SKILL.md body (read once
   when the skill triggers).
2. Tasks: identical jobs on fresh copies of the same KiCad demo project. For every call we
   record the request (the tool-call JSON an agent emits) and the response (the text an agent
   receives back), counted with tiktoken o200k_base, plus wall-clock time.

Usage (third-party servers fetched with fetch_servers.py):
    uv run --no-project --with tiktoken==0.14.0 python bench/run_bench.py \
        --workdir <dir used by fetch_servers.py> --demos .fixtures/kicad-src/demos [--reps 5]

Before each repetition the harness waits (up to --max-wait seconds) for the 1-minute load
average to fall below --max-load, and records the load it started from. bench/test_bench.py
checks the context figures.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import re
import shlex
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import tiktoken

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from mcp_client import McpError, StdioClient  # noqa: E402

ENC = tiktoken.get_encoding("o200k_base")
DEMO = "pic_programmer"


def tok(text: str) -> int:
    return len(ENC.encode(text, disallowed_special=()))


def tool_def(t: dict) -> str:
    """A tool as model APIs receive it: name, description, input schema."""
    return json.dumps({"name": t["name"], "description": t.get("description", ""),
                       "input_schema": t.get("inputSchema", {})}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# sides
# ---------------------------------------------------------------------------


def kce_context(skill: str, commands: str) -> Dict[str, Any]:
    """What an agent loads for `kce`, counted in tokens.

    ``skill`` is the whole SKILL.md file, which already contains its frontmatter: loading the
    skill therefore costs ``tok(skill)``, and the frontmatter must not be added a second time.
    ``bench/test_bench.py`` checks that the two figures agree.
    """
    front = skill.split("---", 2)[1]
    return {"always_loaded": {"skill_frontmatter": tok(front)},
            "on_trigger": {"skill_body": tok(skill)},
            "on_demand": {"commands_reference": tok(commands)},
            "always_tokens": tok(front), "with_skill_tokens": tok(skill)}


class KceSide:
    name = "kce"

    def __init__(self):
        exe = shutil.which("kce")
        venv_py = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if exe:
            self.cmd = [exe]
        elif venv_py.exists():
            # `uv run --no-project` gives an interpreter without kce installed; use the repo's.
            self.cmd = [str(venv_py), "-m", "kicad_cli_extended"]
        else:
            self.cmd = [sys.executable, "-m", "kicad_cli_extended"]
        self.version = subprocess.run(self.cmd + ["--version"], capture_output=True, text=True,
                                      encoding="utf-8").stdout.strip()

    def context(self) -> Dict[str, Any]:
        d = ROOT / "skills" / "kicad-cli-extended"
        return kce_context((d / "SKILL.md").read_text(encoding="utf-8"),
                           (d / "COMMANDS.md").read_text(encoding="utf-8"))

    def call(self, argv: List[str]) -> Dict[str, Any]:
        line = shlex.join(["kce", *argv])
        t0 = time.perf_counter()
        r = subprocess.run(self.cmd + argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           stdin=subprocess.DEVNULL, timeout=1800)
        dt_ = time.perf_counter() - t0
        out = r.stdout + (("\n" + r.stderr) if r.stderr.strip() else "")
        return {"request": json.dumps({"command": line}), "response": out, "seconds": dt_, "ok": r.returncode == 0,
                "label": line}


class McpSide:
    def __init__(self, name: str, command: List[str], cwd: Path, env: Dict[str, str]):
        self.name = name
        self.client = StdioClient(command, cwd=str(cwd), env=env)
        self.client.start()
        self.tools = self.client.list_tools()
        self.resources = self.client.list_resources()
        self.templates = self.client.list_resource_templates()
        self.prompts = self.client.list_prompts()

    def context(self) -> Dict[str, Any]:
        tools_tokens = sum(tok(tool_def(t)) for t in self.tools)
        res = {"tools": len(self.tools), "tools_tokens": tools_tokens,
               "tools_list_raw_tokens": tok(json.dumps({"tools": self.tools}, ensure_ascii=False)),
               "resources": len(self.resources), "resources_tokens": tok(json.dumps(self.resources, ensure_ascii=False)),
               "resource_templates": len(self.templates),
               "resource_templates_tokens": tok(json.dumps(self.templates, ensure_ascii=False)),
               "prompts": len(self.prompts), "prompts_tokens": tok(json.dumps(self.prompts, ensure_ascii=False)),
               "startup_seconds": round(self.client.startup_seconds, 2),
               "server_info": self.client.server_info}
        res["always_tokens"] = tools_tokens
        return res

    def call(self, tool: str, args: dict) -> Dict[str, Any]:
        req = json.dumps({"name": tool, "arguments": args}, ensure_ascii=False)
        t0 = time.perf_counter()
        try:
            r = self.client.call_tool(tool, args)
            ok = not r.get("isError", False)
        except McpError as e:
            r, ok = {"content": [{"type": "text", "text": f"MCP error: {e}"}]}, False
        dt_ = time.perf_counter() - t0
        text = "\n".join(c.get("text", "") for c in r.get("content", []) if c.get("type") == "text")
        if not text and r.get("structuredContent") is not None:
            text = json.dumps(r["structuredContent"], ensure_ascii=False)
        return {"request": req, "response": text, "seconds": dt_, "ok": ok, "label": f"{tool}({args})"}

    def close(self):
        self.client.close()


# ---------------------------------------------------------------------------
# tasks: same job, each side's own way of doing it
# ---------------------------------------------------------------------------


def task_plan(side: str, p: Dict[str, str]) -> Optional[List[Any]]:
    """Calls for each task. None = the side has no tool for it.

    lamaalrajih's schemas mark a leaked FastMCP ``ctx`` parameter as required; calls without it
    fail input validation, so ``ctx: null`` is passed as the schema demands.
    """
    kce = lambda *a: ("kce", list(a))  # noqa: E731
    mcp = lambda tool, **args: ("mcp", tool, args)  # noqa: E731
    P = ["--json", "--project", p["dir"]]
    plans = {
        "erc": {
            "kce": [kce(*P, "check", "erc")],
            "mixelpixx": [mcp("run_erc", schematicPath=p["sch"])],
            "lamaalrajih": None,
        },
        "drc": {
            "kce": [kce(*P, "check", "drc")],
            "mixelpixx": [mcp("open_project", filename=p["pro"]), mcp("run_drc")],
            "lamaalrajih": [mcp("run_drc_check", project_path=p["pro"], ctx=None)],
        },
        "bom": {
            "kce": [kce(*P, "sch", "export", "bom", "--output", p["out"] + "/bom.csv")],
            "mixelpixx": [mcp("export_sch_bom", schematicPath=p["sch"], outputPath=p["out"] + "/bom.csv")],
            "lamaalrajih": [mcp("export_bom_csv", project_path=p["pro"], ctx=None)],
        },
        "gerber_drill": {
            "kce": [kce(*P, "pcb", "export", "gerbers", "--output", p["out"] + "/gerbers/"),
                    kce(*P, "pcb", "export", "drill", "--output", p["out"] + "/gerbers/")],
            "mixelpixx": [mcp("export_gerbers", outputDir=p["out"] + "/gerbers", boardPath=p["pcb"]),
                          mcp("export_drill", outputDir=p["out"] + "/gerbers", boardPath=p["pcb"])],
            "lamaalrajih": None,
        },
    }
    return plans


def verify(task: str, p: Dict[str, str], responses: List[str], ok: bool) -> Dict[str, Any]:
    """Did the task produce what it should? Files are checked on disk, not from the tool's claims;
    ERC/DRC must report an actual violation count. A failing run never counts as produced."""
    out = Path(p["out"])
    if task == "gerber_drill":
        g = out / "gerbers"
        gbr = [f for f in g.glob("*") if f.suffix.lower() in (".gtl", ".gbl", ".gbr", ".gm1", ".gts", ".gbs", ".gto",
                                                              ".gbo", ".gtp", ".gbp")] if g.is_dir() else []
        drl = list(g.glob("*.drl")) if g.is_dir() else []
        return {"gerber_files": len(gbr), "drill_files": len(drl), "produced": ok and bool(gbr and drl)}
    if task == "bom":
        csvs = list(out.glob("*.csv")) + list(Path(p["dir"]).glob("*bom*.csv")) + list(Path(p["dir"]).glob("*/*bom*.csv"))
        return {"bom_files": [f.name for f in csvs], "produced": ok and bool(csvs)}
    text = "\n".join(responses).lower()
    # ERC/DRC: the response must carry an actual violation count, not just any error text.
    return {"produced": ok and bool(re.search(r"\d+\s+violation|\"total\"\s*:|\"total_violations\"|\"errors\"\s*:", text))}


def fresh_project(demos: Path, tmp: Path, tag: str) -> Dict[str, str]:
    d = tmp / tag / DEMO
    shutil.copytree(demos / DEMO, d)
    (tmp / tag / "out").mkdir()
    return {"dir": str(d), "pro": str(d / f"{DEMO}.kicad_pro"), "sch": str(d / f"{DEMO}.kicad_sch"),
            "pcb": str(d / f"{DEMO}.kicad_pcb"), "out": str(tmp / tag / "out")}


def run_task(side_obj, side: str, steps, proj) -> Dict[str, Any]:
    calls = []
    for st in steps:
        if st[0] == "kce":
            calls.append(side_obj.call(st[1]))
        else:
            calls.append(side_obj.call(st[1], st[2]))
    return {"calls": len(calls), "ok": all(c["ok"] for c in calls),
            "request_tokens": sum(tok(c["request"]) for c in calls),
            "response_tokens": sum(tok(c["response"]) for c in calls),
            "seconds": round(sum(c["seconds"] for c in calls), 3),
            "detail": [{"call": c["label"][:300], "ok": c["ok"], "request_tokens": tok(c["request"]),
                        "response_tokens": tok(c["response"]), "seconds": round(c["seconds"], 3),
                        "response_head": c["response"][:400]} for c in calls]}


# ---------------------------------------------------------------------------


def scrub(value: Any) -> Any:
    """Remove local path prefixes after counting the original request/response tokens."""
    home = os.path.expanduser("~")
    if isinstance(value, str):
        return value.replace(str(Path(tempfile.gettempdir()) / "kce-bench-run"), "<benchmark>").replace(home, "~")
    if isinstance(value, list):
        return [scrub(v) for v in value]
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    return value


def wait_for_load(max_load: float, max_wait: float) -> Dict[str, Any]:
    """Wait until the 1-minute load average is below ``max_load``, at most ``max_wait`` seconds.

    Load is the one thing that distorts the time column, so a repetition starts from a quiet
    machine when it can; when it cannot, the results file says it started above the limit.
    ``max_load <= 0`` disables the wait.
    """
    load = os.getloadavg()[0] if hasattr(os, "getloadavg") else None
    t0 = time.monotonic()
    while load is not None and max_load > 0 and load >= max_load:
        left = max_wait - (time.monotonic() - t0)
        if left <= 0:
            break
        time.sleep(min(15.0, left))
        load = os.getloadavg()[0]
    return {"loadavg": None if load is None else round(load, 2),
            "waited_seconds": round(time.monotonic() - t0, 1),
            "started_above_limit": bool(load is not None and max_load > 0 and load >= max_load)}


def main() -> int:
    loadavg_at_start = list(os.getloadavg()) if hasattr(os, "getloadavg") else None
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", required=True, help="directory used by fetch_servers.py")
    ap.add_argument("--demos", default=str(ROOT / ".fixtures" / "kicad-src" / "demos"))
    ap.add_argument("--reps", type=int, default=5,
                    help="interleaved repetitions per task and side (default 5; see METHOD.md)")
    ap.add_argument("--max-load", type=float, default=None,
                    help="before each repetition, wait until the 1-minute load average is below this "
                         "(default: CPUs * 0.25; 0 disables the wait)")
    ap.add_argument("--max-wait", type=float, default=1800.0,
                    help="longest a repetition waits for the load to drop, in seconds (default 1800=30 min)")
    ap.add_argument("--out", default=str(HERE / "results" / f"results-{dt.date.today().isoformat()}.json"))
    a = ap.parse_args()
    max_load = (os.cpu_count() or 1) * 0.25 if a.max_load is None else a.max_load
    work = Path(a.workdir)
    spec = json.loads((HERE / "servers.json").read_text(encoding="utf-8"))

    sides: Dict[str, Any] = {"kce": KceSide()}
    context: Dict[str, Any] = {"kce": sides["kce"].context()}
    servers_meta = []
    for s in spec["servers"]:
        d = work / s["id"]
        for cfg, c in s["configs"].items():
            if s["kind"] == "node+python":
                venv_py = d / ".bench-venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
                cmd, env = ["node", "dist/index.js"], dict(c["env"], KICAD_PYTHON=str(venv_py))
            else:
                cmd = [str(d / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")), "main.py"]
                env = dict(c["env"])
            key = s["id"] if cfg == "default" else f"{s['id']}:{cfg}"
            side = McpSide(key, cmd, d, env)
            context[key] = side.context()
            if cfg == "default":
                sides[s["id"]] = side
            else:
                side.close()
        servers_meta.append({k: s[k] for k in ("id", "repo", "ref", "commit", "stars_at_selection")})

    demos = Path(a.demos)
    tasks_out: Dict[str, Dict[str, Any]] = {}
    # A fixed directory name keeps the project paths in every call identical across repetitions;
    # a random one shifts each request's token count by a token or two.
    tmp = Path(tempfile.gettempdir()) / "kce-bench-run"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    try:
        reps_load: List[Dict[str, Any]] = []
        for rep in range(a.reps):
            gate = wait_for_load(max_load, a.max_wait)
            reps_load.append({"rep": rep, **gate})
            print(f"rep {rep} start loadavg={gate['loadavg']} waited={gate['waited_seconds']}s"
                  + ("  ABOVE_MAX_LOAD" if gate["started_above_limit"] else ""), flush=True)
            for task in ("erc", "drc", "bom", "gerber_drill"):
                for side_name, side_obj in sides.items():
                    proj = fresh_project(demos, tmp, f"{rep}-{task}-{side_name.replace(':', '_')}")
                    steps = task_plan(side_name, proj)[task][side_name]
                    slot = tasks_out.setdefault(task, {}).setdefault(side_name, {"runs": []})
                    if steps is None:
                        slot["supported"] = False
                        continue
                    r = run_task(side_obj, side_name, steps, proj)
                    r["loadavg"] = gate["loadavg"]
                    r["verify"] = verify(task, proj, [d["response_head"] for d in r["detail"]], r["ok"])
                    slot["supported"] = True
                    slot["runs"].append(r)
                    print(f"rep {rep} {task:13s} {side_name:12s} calls={r['calls']} ok={r['ok']} "
                          f"tokens={r['request_tokens']}+{r['response_tokens']} {r['seconds']:.2f}s", flush=True)
    finally:
        for s in sides.values():
            if hasattr(s, "close"):
                s.close()
        shutil.rmtree(tmp, ignore_errors=True)

    summary: Dict[str, Any] = {}
    for task, by_side in tasks_out.items():
        for side_name, slot in by_side.items():
            runs = slot.get("runs", [])
            if not runs:
                summary.setdefault(task, {})[side_name] = {"supported": slot.get("supported", False)}
                continue
            summary.setdefault(task, {})[side_name] = {
                "supported": True, "calls": runs[-1]["calls"], "ok_runs": sum(r["ok"] for r in runs),
                "produced_runs": sum(bool(r["verify"].get("produced")) for r in runs), "runs": len(runs),
                "request_tokens": int(statistics.median(r["request_tokens"] for r in runs)),
                "response_tokens": int(statistics.median(r["response_tokens"] for r in runs)),
                "median_seconds": round(statistics.median(r["seconds"] for r in runs), 2)}
    result = {
        "date": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "tokenizer": "tiktoken o200k_base", "tiktoken": tiktoken.__version__ if hasattr(tiktoken, "__version__") else "?",
        "machine": {"os": platform.platform(), "python": platform.python_version(), "cpus": os.cpu_count(),
                    "loadavg_at_start": loadavg_at_start,
                    "loadavg_at_end": os.getloadavg() if hasattr(os, "getloadavg") else None},
        "load_gate": {"max_load": round(max_load, 2), "max_wait_seconds": a.max_wait, "reps": reps_load},
        "kicad": json.loads(subprocess.run([*sides["kce"].cmd, "--json", "info", "doctor"],
                                            capture_output=True, text=True, encoding="utf-8", errors="replace",
                                            stdin=subprocess.DEVNULL, timeout=180, check=True).stdout)["kicad_version"],
        "kce": sides["kce"].version, "servers": servers_meta, "demo_project": DEMO, "reps": a.reps,
        "context": context, "summary": summary, "tasks": tasks_out,
    }
    result = scrub(result)

    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(result, f, indent=1, ensure_ascii=False, default=str)
        f.write("\n")
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
