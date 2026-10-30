"""Check captured cold-agent command chains; never call a paid model from this gate.

Capture one fresh agent per recipe, with only an installed SKILL.md/COMMANDS.md
and CLI help. Save its first proposed chain as R1.json ... R12.json. These
records are evidence of discovery, not evidence that a board is manufacturable.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


REQUIRED = {
    1: ["info doctor", "info commands", "skill status"],
    2: ["project new", "session checkpoint", "sch apply", "sch verify", "pcb update", "pcb score"],
    3: ["lib sym search", "lib sym show"],
    4: ["session checkpoint", "run", "project compare", "preview diff", "session restore"],
    5: ["session checkpoint", "sch nets", "pcb footprints", "sch verify", "pcb update", "check drc"],
    6: ["session checkpoint", "pcb place near", "pcb score", "pcb legalize", "pcb connectivity", "check drc"],
    7: ["check erc", "check drc", "fab package"],
    8: ["check erc", "check drc", "fab package"],
    9: ["sch verify", "session checkpoint", "serve", "run", "pcb score", "session restore"],
    10: ["project import", "session checkpoint", "check drc"],
    11: ["sim model", "sim directive", "sim netlist", "sim run"],
    12: ["session checkpoint", "pcb diffpair", "pcb length", "pcb fence", "pcb stitch", "pcb teardrops", "pcb panelize", "fab package"],
}


def command(args):
    """Remove global options while preserving command-specific flags and arguments."""
    args = list(args)
    result = []
    while args:
        token = args.pop(0)
        if token in {"--json", "--dry-run", "--read-only", "--no-validate", "--progress"}:
            continue
        if token in {"-p", "--project", "--session-dir"}:
            if args:
                args.pop(0)
            continue
        if token.startswith(("--project=", "--session-dir=")):
            continue
        result.append(token)
    return result


def starts(args, path):
    words = path.split()
    return command(args)[:len(words)] == words


def design_gate(row, check):
    args = command(row)
    if "--fail-on" not in args or args[args.index("--fail-on") + 1:][:1] not in (["error"], ["warning"]):
        return False
    if starts(row, f"check {check}"):
        return True
    return starts(row, "check diagnose") and ("--only" not in args or
            args[args.index("--only") + 1:][:1] == [check])


def expanded_chain(record):
    """Include the agent's own plan and conditional recovery/RPC steps in execution order."""
    plans = record.get("plans", {})
    rows = []
    original_notes = json.dumps(record.get("notes", {}), ensure_ascii=False)
    continuation = []
    for row in record.get("continuation", []):
        quote = row.get("source_quote", "")
        # Formatting a prose continuation is allowed only while preserving the
        # original quoted decision; it is not a new discovery attempt.
        if not quote or quote not in original_notes:
            raise ValueError("continuation must quote the agent's original notes")
        continuation.append(row["args"])
    for row in [*record.get("chain", []), *continuation]:
        rows.append(row)
        args = command(row)
        if args and args[0] == "run" and len(args) > 1:
            plan = plans.get(args[1], {})
            if isinstance(plan, dict):
                for step in plan.get("steps", []):
                    rows.append((["--dry-run"] if "--dry-run" in row else []) + step["args"])
    for request in record.get("requests", []):
        if request.get("method") == "run":
            args = request.get("params", {}).get("args", [])
            rows.append(args)
            if starts(args, "run"):
                plan = plans.get(command(args)[1], {})
                rows.extend(step["args"] for step in plan.get("steps", []))
    return rows


def check_record(recipe, record):
    errors = []
    chain = record.get("chain", [])
    if not record.get("prompt") or not record.get("reads"):
        errors.append("missing prompt or actual documentation reads")
    if not chain or any(not isinstance(row, list) or not row or
                        any(not isinstance(token, str) for token in row) for row in chain):
        return errors + ["chain must contain argv arrays"]
    chain = expanded_chain(record)
    notes = record.get("notes", {})
    recovery = record.get("failure_chain", [])
    if isinstance(notes, dict):
        recovery += notes.get("failure_chain", [])
    for path in REQUIRED[recipe]:
        candidates = chain + (recovery if path == "session restore" else [])
        if path in {"check erc", "check drc"} and any(design_gate(row, path.split()[1]) for row in chain):
            continue
        if not any(starts(row, path) for row in candidates):
            errors.append(f"missing {path}")
    for i, row in enumerate(chain):
        if starts(row, "run") and "--dry-run" not in row and recipe in {2, 4, 5}:
            args = command(row)
            if not any(command(previous) == args and "--dry-run" in previous for previous in chain[:i]):
                errors.append("run stage precedes its preview")
        if starts(row, "pcb update") and "--dry-run" not in row:
            if not any(starts(previous, "pcb update") and "--dry-run" in previous for previous in chain[:i]):
                errors.append("pcb update precedes its preview")
            if not any(starts(previous, "sch verify") and "--strict" in previous for previous in chain[:i]):
                errors.append("pcb update precedes strict intent verification")
            if recipe in {2, 4, 5} and not any(design_gate(previous, "erc") for previous in chain[:i]):
                errors.append("pcb update precedes erc gate")
            if not any(starts(later, "pcb score") or design_gate(later, "drc") for later in chain[i + 1:]):
                errors.append("pcb update has no subsequent score or drc gate")
        if starts(row, "fab package") and "--fail-on-drc" not in row:
            errors.append("fab package omits --fail-on-drc")
        if starts(row, "fab package"):
            for check in ("erc", "drc"):
                if not any(design_gate(previous, check) for previous in chain[:i]):
                    errors.append(f"fab package precedes {check} gate")
    # New project/import creates the scope that can then be checkpointed. Scratch
    # simulation is likewise separate from an existing user's design.
    mutations = {"sch apply", "sch set-value", "sch add", "pcb move", "pcb update",
                 "pcb place", "pcb legalize", "pcb route", "pcb diffpair rule",
                 "pcb length rule", "pcb panelize", "lib sym new", "lib fp wizard"}
    if recipe != 11:
        for i, row in enumerate(chain):
            if "--dry-run" in row or "--help" in row:
                continue
            if any(starts(row, path) for path in mutations):
                if not any(starts(previous, "session checkpoint") for previous in chain[:i]):
                    errors.append("mutation precedes checkpoint")
                if recipe in {2, 4, 5} and not any(starts(previous, "run") and "--dry-run" not in previous
                                                   for previous in chain[:i]):
                    errors.append("schematic mutation bypasses previewed run stage")
                break
    return errors


def check_directory(root):
    result = {}
    for recipe in range(1, 13):
        path = root / f"R{recipe}.json"
        if not path.is_file():
            result[f"R{recipe}"] = ["missing transcript"]
        else:
            result[f"R{recipe}"] = check_record(recipe, json.loads(path.read_text(encoding="utf-8")))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    result = check_directory(args.directory)
    print(json.dumps({"recipes": result, "passed": sum(not errors for errors in result.values()),
                      "scope": "installed-skill discovery; Linux execution is checked separately"}, ensure_ascii=False))
    return int(any(result.values()))


if __name__ == "__main__":
    raise SystemExit(main())
