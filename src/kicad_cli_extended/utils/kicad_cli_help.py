"""Parse the ``kicad-cli --help`` tree (argparse-cpp style output).

Used to generate the static command spec (``scripts/gen_kicad_cli_spec.py``)
and, at test time, to verify that every kicad-cli subcommand and option is
reachable through ``kce``.
"""
from __future__ import annotations

import re
import subprocess
from typing import Dict, List

from . import kicad_backend as kb


def kicad_cli_version() -> str:
    return kb.kicad_version()


def help_text(path: List[str]) -> str:
    r = subprocess.run([*kb.kicad_cli_command(), *path, "--help"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, timeout=120)
    return r.stdout


_OPT_LINE = re.compile(r"^  (\s?)(-\S.*?)(?:\s{2,}(.*))?$")


def parse_help(text: str) -> Dict:
    """Return {'usage','description','positionals','options','subcommands'}."""
    section = None
    res = {"usage": "", "description": "", "positionals": [], "options": [], "subcommands": []}
    lines = text.splitlines()
    desc: List[str] = []
    cur = None
    for i, line in enumerate(lines):
        if line.startswith("Usage:"):
            res["usage"] = line[len("Usage:"):].strip()
            section = "desc"
            continue
        m = re.match(r"^(Subcommands|Optional arguments|Positional arguments):\s*$", line)
        if m:
            section = m.group(1)
            cur = None
            continue
        if section == "desc":
            if line.strip():
                desc.append(line.strip())
            continue
        if not line.strip():
            cur = None
            continue
        if section == "Subcommands":
            mm = re.match(r"^  (\S+)\s*(.*)$", line)
            if mm:
                res["subcommands"].append({"name": mm.group(1), "help": mm.group(2).strip()})
        elif section == "Positional arguments":
            mm = re.match(r"^  (\S+)\s*(.*)$", line)
            if mm:
                res["positionals"].append({"name": mm.group(1), "help": mm.group(2).strip()})
        elif section == "Optional arguments":
            mm = _OPT_LINE.match(line)
            if mm and mm.group(2).startswith("-"):
                head = mm.group(2)
                flags = re.findall(r"(--?[A-Za-z0-9][\w-]*)", head)
                cur = {"flags": flags, "help": (mm.group(3) or "").strip()}
                res["options"].append(cur)
            elif cur is not None:
                cur["help"] += " " + line.strip()
    res["description"] = " ".join(desc)
    for o in res["options"]:
        h = o["help"]
        o["takes_value"] = "[nargs=0..1]" in h or "[nargs=1]" in h
        o["repeatable"] = "[may be repeated]" in h
        d = re.search(r"\[default: (.*?)\]", h)
        o["default"] = d.group(1).strip('"') if d else None
        o["help"] = re.sub(r"\s*\[(nargs=[^\]]*|default: [^\]]*|may be repeated)\]", "", h).strip()
        longs = [f for f in o["flags"] if f.startswith("--")]
        o["long"] = max(longs, key=len) if longs else o["flags"][0]
        o["aliases"] = [f for f in o["flags"] if f != o["long"]]
    res["options"] = [o for o in res["options"] if o["long"] not in ("--help",)]
    return res


def walk_kicad_cli(path: List[str] | None = None, tree: Dict | None = None) -> Dict[str, Dict]:
    """Recursively walk kicad-cli help. Keys are space-joined command paths."""
    path = path or []
    tree = {} if tree is None else tree
    info = parse_help(help_text(path))
    tree[" ".join(path)] = info
    for sub in info["subcommands"]:
        walk_kicad_cli(path + [sub["name"]], tree)
    return tree
