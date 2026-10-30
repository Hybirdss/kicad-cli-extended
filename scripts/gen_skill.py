#!/usr/bin/env python3
"""Generate the agent skill: SKILL.md (from scripts/skill_template.md) and COMMANDS.md
(command reference extracted from the Click tree, so it can never drift from the CLI).

Writes both copies: skills/kicad-cli-extended/ (repo root, for skill installers that read
GitHub) and src/kicad_cli_extended/skill_data/ (packaged, used by `kce skill install`).
"""
import sys
from pathlib import Path

import click

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kicad_cli_extended import APP_NAME, COMMAND  # noqa: E402
from kicad_cli_extended import cli as cli_mod  # noqa: E402

PASSTHROUGH = {id(c) for c in cli_mod.KICAD_CLI_COMMANDS.values()}


def walk(cmd, path, out):
    if isinstance(cmd, click.Group):
        for name, sub in sorted(cmd.commands.items()):
            walk(sub, path + [name], out)
        return
    args = " ".join(p.name.upper() for p in cmd.params if isinstance(p, click.Argument))
    help_ = (cmd.short_help or (cmd.help or "").split("\n")[0]).strip().replace("|", "\\|")
    if id(cmd) in PASSTHROUGH:
        kpath = next(k for k, c in cli_mod.KICAD_CLI_COMMANDS.items() if c is cmd)
        opts = f"all `kicad-cli {kpath}` options"
    else:
        opts = " ".join(f"`{p.opts[0]}`" for p in cmd.params if isinstance(p, click.Option))
    out.append((" ".join(path), args, help_, opts))


def commands_md() -> str:
    rows = []
    walk(cli_mod.cli, [], rows)
    lines = [f"# {COMMAND} command reference", "",
             f"Generated from the CLI ({len(rows)} commands). Global options: `--json`, `--project PATH`, "
             "`--dry-run`, `--read-only`, `--progress`, `--session-dir DIR`, `--no-validate`, `--version`. kicad-cli passthrough commands also accept "
             "`--show-command` and `--allow-fail`. Details: `kce <group> <command> --help`.", "",
             "Public result schemas: " + ", ".join(f"[{name}](../../schemas/{name}.schema.json)" for name in
                ("run-result", "sch-apply", "sch-verify", "pcb-score", "sch-nets", "rpc", "error")) + ".", "",
             "Use `kce --json info commands --full` or `kce info describe COMMAND` for effects and examples. Installed schemas: `kce info schema NAME`.", "",
             "## Choose commands by intent", "",
             "Start with the triggered recipes in [SKILL.md](SKILL.md). Use this map before composing a chain.", "",
             "| Intent | Commands to reach for | Acceptance |", "|---|---|---|",
             "| Nets as data | `sch apply`, `sch verify --expect FILE --strict` | match:true; ERC |",
             "| Atomic stage and rollback | `run PLAN`, `session checkpoint`, `session restore` | plan expect predicates; compare checkpoint:NAME |",
             "| Placement | `pcb score`, `pcb place near`, `pcb legalize --strict` | closed outline; zero overlaps/outside |",
             "| Preserve an ECO | dry-run `pcb update --stable-uuids --lock-existing`, scoped autoroute, `project compare` | pin nets, UUIDs, pad positions; DRC |",
             "| Explain failed checks | `check diagnose`, `errors` | explicit --fail-on policy |",
             "| Many PCB calls | `serve` | worker_reused; matching request ids; restore on lost outcome |",
             "| Missing part | `lib sym new`, `lib fp new`, `lib fp wizard`, `lib link-check`, `lib fp check` | datasheet pins/pads; coverage; ERC/DRC |",
             "| High-speed signals | `pcb diffpair`, `pcb length`, `pcb fence`, `pcb stitch`, `pcb teardrops` | skew/length budget; fill; DRC |",
             "| Breakaway panels | `pcb panelize` | source hash; effective mode/spacing; panel DRC; fab review |",
             "| Filters | `sim model`, `sim directive add`, `sim netlist`, `sim run` | node 0; AC stimulus; analytical/probe check |",
             "| Foreign design | `project import` into a new directory | source preservation and independent loss/equivalence review |",
             "| Release | `fab bom-check`, `fab package --fail-on-drc --manifest` | complete:true; files/hashes; CPL review |", "",
             "`serve` reuses pcbnew; separate kicad-cli invocations such as ERC do not share that worker. Connector edge placement uses `pcb move` and `pcb lock`.", "",
             "## Complete command reference", "",
             "| Command | Args | Description | Options |", "|---|---|---|---|"]
    lines += [f"| `{COMMAND} {p}` | {a} | {h} | {o} |" for p, a, h, o in rows]
    return "\n".join(lines) + "\n"


SCHEMA_DIR = ROOT / "src" / "kicad_cli_extended" / "schema_data"

SKILL_DIRS = (ROOT / "skills" / APP_NAME, ROOT / "src" / "kicad_cli_extended" / "skill_data")


def render() -> dict:
    skill = (ROOT / "scripts" / "skill_template.md").read_text(encoding="utf-8")
    return {"SKILL.md": skill, "COMMANDS.md": commands_md()}


def stale_files(files: dict) -> list:
    stale = []
    for dest in SKILL_DIRS:
        for name, text in files.items():
            path = dest / name
            if not path.is_file() or path.read_bytes() != text.encode("utf-8"):
                stale.append(str(path))
    for source in sorted((ROOT / "schemas").glob("*.schema.json")):
        dest = SCHEMA_DIR / source.name
        if not dest.is_file() or dest.read_bytes() != source.read_bytes():
            stale.append(str(dest))
    return stale


def main() -> int:
    files = render()
    if "--check" in sys.argv[1:]:
        stale = stale_files(files)
        if stale:
            print("skill files are out of date (run scripts/gen_skill.py):\n  " + "\n  ".join(stale),
                  file=sys.stderr)
            return 1
        print("skill files are up to date")
        return 0
    for dest in SKILL_DIRS:
        dest.mkdir(parents=True, exist_ok=True)
        for name, text in files.items():
            with open(dest / name, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
        print(f"wrote {dest}")
    SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    for source in sorted((ROOT / "schemas").glob("*.schema.json")):
        (SCHEMA_DIR / source.name).write_bytes(source.read_bytes())
    print(f"wrote {SCHEMA_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
