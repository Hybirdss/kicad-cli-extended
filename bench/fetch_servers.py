#!/usr/bin/env python3
"""Fetch and build the pinned third-party KiCad MCP servers listed in servers.json.

Nothing is vendored: each server is cloned at its pinned commit into a work
directory outside this repository (default: <system temp>/kce-bench), then its
own documented setup is run (npm ci + npm run build, a Python venv with the
server's requirements, or uv sync).

    python bench/fetch_servers.py [--workdir DIR] [--kicad-python /usr/bin/python3]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


def sh(cmd, cwd=None, env=None):
    print("+", " ".join(map(str, cmd)), flush=True)
    subprocess.run([str(c) for c in cmd], cwd=cwd, env=env, check=True)


def venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default=os.path.join(tempfile.gettempdir(), "kce-bench"))
    ap.add_argument("--kicad-python", default=os.environ.get("KCE_PYTHON"),
                    help="interpreter that can `import pcbnew` (for servers with a pcbnew backend)")
    a = ap.parse_args()
    if a.kicad_python is None:
        exe = shutil.which("kce")
        cmd = [exe] if exe else [sys.executable, "-m", "kicad_cli_extended"]
        doctor = subprocess.run([*cmd, "--json", "info", "doctor"], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL,
                                timeout=180, check=True)
        a.kicad_python = json.loads(doctor.stdout)["pcbnew_python"]
    work = Path(a.workdir)
    work.mkdir(parents=True, exist_ok=True)
    spec = json.loads((HERE / "servers.json").read_text(encoding="utf-8"))
    for s in spec["servers"]:
        d = work / s["id"]
        if not (d / ".git").is_dir():
            sh(["git", "clone", "--filter=blob:none", s["repo"], d])
        sh(["git", "-c", "advice.detachedHead=false", "checkout", s["commit"]], cwd=d)
        if s["kind"] == "node+python":
            npm = shutil.which("npm") or "npm"
            sh([npm, "ci", "--no-audit", "--no-fund"], cwd=d)
            sh([npm, "run", "build"], cwd=d)
            venv = d / ".bench-venv"
            if not venv.exists():
                sh([a.kicad_python, "-m", "venv", "--system-site-packages", venv])
            sh([venv_python(venv), "-m", "pip", "install", "-q", "-r", d / "requirements.txt"])
        elif s["kind"] == "python-uv":
            sh([shutil.which("uv") or "uv", "sync", "--frozen"], cwd=d)
        print(f"ready: {s['id']} @ {s['commit'][:7]} -> {d}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
